"""SCRFD 人脸检测, 供海报裁剪定位主体.

解码对齐 insightface 的 ``model_zoo/scrfd.py``: 每个 stride 的输出是相对 anchor 中心的
**距离**, 要先按格子坐标生成 anchor 才能解出 x1y1x2y2. 把预测值直接当坐标会把所有框挤到
左上角.

模型为 insightface buffalo_l 的 ``det_10g.onnx`` (16.9 MB), 随包分发. 会话惰性创建 ——
不裁海报时不加载模型, 也不占内存.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import structlog
from PIL import Image

logger = structlog.get_logger()

_MODEL_PATH = Path(__file__).with_name("face_detect_scrfd.onnx")
_GENDER_MODEL_PATH = Path(__file__).with_name("face_detect_genderage.onnx")
#: 输出按 stride 分组, 每组依次是 score / bbox / kps.
_STRIDES_3 = (8, 16, 32)
_STRIDES_5 = (8, 16, 32, 64, 128)
#: SCRFD 输入要求宽高是 32 的倍数.
_SIZE_MULTIPLE = 32
#: 经验阈值: 30 张真实封面扫过一遍, 0.45 能全部检出且无「整图上身」这类大框误检.
DEFAULT_CONF = 0.45
_NMS_IOU = 0.4


def model_available() -> bool:
    return _MODEL_PATH.is_file()


def _anchor_centers(height: int, width: int, stride: int, num_anchors: int) -> np.ndarray:
    """每个格子的中心坐标; 一格多 anchor 时按组重复, 与特征排布一致."""
    centers = np.stack(np.mgrid[:height, :width][::-1], axis=-1).astype(np.float32)
    centers = (centers * stride).reshape(-1, 2)
    if num_anchors > 1:
        centers = np.stack([centers] * num_anchors, axis=1).reshape(-1, 2)
    return centers


def _distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    return np.stack(
        [
            points[:, 0] - distance[:, 0],
            points[:, 1] - distance[:, 1],
            points[:, 0] + distance[:, 2],
            points[:, 1] + distance[:, 3],
        ],
        axis=-1,
    )


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        index = int(order[0])
        keep.append(index)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[index, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[index, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[index, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[index, 3], boxes[rest, 3])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        area_i = (boxes[index, 2] - boxes[index, 0]) * (boxes[index, 3] - boxes[index, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        union = area_i + area_r - inter
        overlap = np.where(union > 0, inter / union, 0.0)
        order = rest[overlap <= iou]
    return keep


class _Detector:
    """模型会话与其 stride 配置; 由 ``_detector()`` 缓存复用."""

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        outputs = len(self._session.get_outputs())
        # 输出数决定分组: 6/9 → 3 个 stride, 10/15 → 5 个; 后两种多出的那路是关键点.
        if outputs in (6, 9):
            self._strides, self._num_anchors = _STRIDES_3, 2
        elif outputs in (10, 15):
            self._strides, self._num_anchors = _STRIDES_5, 1
        else:
            raise ValueError(f"unexpected SCRFD output count: {outputs}")
        self._use_kps = outputs in (9, 15)
        self._center_cache: dict[tuple[int, int, int], np.ndarray] = {}

    def detect(
        self, image: Image.Image, *, conf: float, max_side: int
    ) -> list[tuple[float, float, float, float, float]]:
        """返回 ``(x1, y1, x2, y2, score)`` 列表, 坐标为原图像素."""
        rgb = image.convert("RGB")
        src_w, src_h = rgb.size
        if src_w <= 0 or src_h <= 0:
            return []

        scale = min(1.0, max_side / max(src_w, src_h))
        new_w, new_h = round(src_w * scale), round(src_h * scale)
        input_w = max(_SIZE_MULTIPLE, -(-new_w // _SIZE_MULTIPLE) * _SIZE_MULTIPLE)
        input_h = max(_SIZE_MULTIPLE, -(-new_h // _SIZE_MULTIPLE) * _SIZE_MULTIPLE)

        canvas = np.zeros((input_h, input_w, 3), dtype=np.float32)
        canvas[:new_h, :new_w] = np.asarray(rgb.resize((new_w, new_h), Image.Resampling.BILINEAR), dtype=np.float32)
        # SCRFD 期望 (x - 127.5) / 128, BGR 顺序.
        blob = (canvas[:, :, ::-1] - 127.5) / 128.0
        blob = np.ascontiguousarray(blob.transpose(2, 0, 1)[None])
        outputs = self._session.run(None, {self._input_name: blob})

        scores_list: list[np.ndarray] = []
        boxes_list: list[np.ndarray] = []
        fmc = len(self._strides)
        for idx, stride in enumerate(self._strides):
            scores = outputs[idx].reshape(-1)
            if not scores.size:
                continue
            bbox_preds = outputs[idx + fmc].reshape(-1, 4) * stride
            grid_h, grid_w = input_h // stride, input_w // stride
            cache_key = (grid_h, grid_w, stride)
            centers = self._center_cache.get(cache_key)
            if centers is None:
                centers = _anchor_centers(grid_h, grid_w, stride, self._num_anchors)
                if len(self._center_cache) < 100:
                    self._center_cache[cache_key] = centers
            if centers.shape[0] != bbox_preds.shape[0]:
                continue
            selected = np.where(scores >= conf)[0]
            if not selected.size:
                continue
            scores_list.append(scores[selected])
            boxes_list.append(_distance2bbox(centers, bbox_preds)[selected])

        if not scores_list:
            return []

        scores = np.concatenate(scores_list)
        boxes = np.concatenate(boxes_list) / scale
        boxes[:, 0::2] = boxes[:, 0::2].clip(0, src_w)
        boxes[:, 1::2] = boxes[:, 1::2].clip(0, src_h)

        return [
            (float(boxes[i, 0]), float(boxes[i, 1]), float(boxes[i, 2]), float(boxes[i, 3]), float(scores[i]))
            for i in _nms(boxes, scores, _NMS_IOU)
        ]


@lru_cache(maxsize=1)
def _detector() -> _Detector | None:
    """会话只建一次; 模型缺失或 onnxruntime 不可用时返回 None, 调用方退回固定裁剪."""
    if not model_available():
        return None
    try:
        return _Detector(_MODEL_PATH)
    except Exception as e:  # 依赖缺失与模型损坏都只是「不给检测」
        logger.warning("face detector unavailable", error=str(e))
        return None


def largest_face(
    image: Image.Image, *, conf: float = DEFAULT_CONF, max_side: int = 640
) -> tuple[float, float, float, float] | None:
    """最大的一张脸; 无检测结果返回 None.

    选面积而非分数: 海报主体的人脸通常最大, 而背景小图的分数未必低.
    """
    detector = _detector()
    if detector is None:
        return None
    faces = detector.detect(image, conf=conf, max_side=max_side)
    if not faces:
        return None
    x1, y1, x2, y2, _ = max(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))
    return (x1, y1, x2, y2)


class _GenderModel:
    """性别年龄模型; 输入按 bbox 中心缩放裁剪, 与 insightface ``Attribute`` 一致."""

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        shape = self._session.get_inputs()[0].shape
        self._size = int(shape[2]) if isinstance(shape[2], int) else 96

    def is_female(self, rgb: np.ndarray, bbox: tuple[float, float, float, float]) -> bool | None:
        """True=女, False=男, None=无法判断."""
        x1, y1, x2, y2 = bbox
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            return None
        center = ((x1 + x2) / 2, (y1 + y2) / 2)
        # 留 1.5 倍余量, 与官方一致: 裁剪范围比人脸框宽.
        scale = self._size / (max(w, h) * 1.5)
        aligned = _warp_affine_center(rgb, center, scale, self._size)
        # 与 cv2.dnn.blobFromImage(1/128, (127.5,127.5,127.5), swapRB=True) 等价:
        # 先 BGR, 再 (x-127.5)/128, 最后转成 NCHW.
        blob = (aligned[:, :, ::-1].astype(np.float32) - 127.5) / 128.0
        blob = np.ascontiguousarray(blob.transpose(2, 0, 1)[None])
        pred = self._session.run(None, {self._input_name: blob})[0][0]
        if len(pred) < 2:
            return None
        # 前两位是性别对数, 取大者: 0=女, 1=男.
        return int(np.argmax(pred[:2])) == 0


def _warp_affine_center(rgb: np.ndarray, center: tuple[float, float], scale: float, size: int) -> np.ndarray:
    """以 ``center`` 为中心按 ``scale`` 缩放到 ``size × size``.

    等价于 ``cv2.warpAffine`` 配 ``[[s,0,-cx*s+size/2],[0,s,-cy*s+size/2]]``:
    目标像素中心取最近邻原像素, 越界补 0. 只为一个 96×96 的裁剪引 opencv 不值得.
    """
    target = (np.arange(size, dtype=np.float32) + 0.5 - size / 2) / scale
    ys = target + center[1]
    xs = target + center[0]
    xi = np.rint(xs).astype(np.intp)
    yi = np.rint(ys).astype(np.intp)
    valid_x = (xi >= 0) & (xi < rgb.shape[1])
    valid_y = (yi >= 0) & (yi < rgb.shape[0])
    out = np.zeros((size, size, rgb.shape[2]), dtype=rgb.dtype)
    if valid_x.any() and valid_y.any():
        out[np.ix_(valid_y, valid_x)] = rgb[np.ix_(yi[valid_y], xi[valid_x])]
    return out


@lru_cache(maxsize=1)
def _gender_model() -> _GenderModel | None:
    if not _GENDER_MODEL_PATH.is_file():
        return None
    try:
        return _GenderModel(_GENDER_MODEL_PATH)
    except Exception as e:  # 模型缺失或损坏只是「不做性别过滤」
        logger.warning("gender model unavailable", error=str(e))
        return None


def main_subject_face(
    image: Image.Image, *, conf: float = DEFAULT_CONF, max_side: int = 640
) -> tuple[float, float, float, float] | None:
    """海报主体人脸: 优先最大的女性脸, 没有女性则退回最大的脸.

    封面上男性脸常常更清晰更大, 单看面积会选错人; 性别模型可过滤掉.
    """
    detector = _detector()
    if detector is None:
        return None
    faces = detector.detect(image, conf=conf, max_side=max_side)
    if not faces:
        return None

    def area(face: tuple[float, float, float, float, float]) -> float:
        return (face[2] - face[0]) * (face[3] - face[1])

    faces.sort(key=lambda f: area(f), reverse=True)
    gender = _gender_model()
    if gender is not None:
        rgb = np.asarray(image.convert("RGB"))
        for face in faces:
            try:
                is_female = gender.is_female(rgb, (face[0], face[1], face[2], face[3]))
            except Exception as e:
                # 性别判定只是锦上添花: 失败就退回最大脸, 别让整个裁剪跟着失败.
                logger.debug("gender inference failed", error=str(e))
                break
            if is_female:
                return (face[0], face[1], face[2], face[3])
    # 没有性别模型, 或没有一张判为女性: 退回最大脸.
    x1, y1, x2, y2, _ = faces[0]
    return (x1, y1, x2, y2)
