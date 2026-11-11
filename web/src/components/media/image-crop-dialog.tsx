import {
  ActionIcon,
  Button,
  Group,
  Loader,
  Modal,
  NumberInput,
  Stack,
  Switch,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowBackUp, IconRotate } from "@tabler/icons-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactCrop, { type Crop, type PixelCrop } from "react-image-crop";
import "react-image-crop/dist/ReactCrop.css";
import { CropMagnifier } from "./crop-magnifier";
import { apiFetch } from "@/lib/api-token";
import { proxyImageUrl } from "@/lib/utils";
import { Z_INDEX } from "@/lib/z-index";

const MIN_ASPECT = 0.1;
const MAX_ASPECT = 5;

/** 自然像素框 (left/top/right/bottom, right/bottom 不含). */
export interface CropBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** 裁切前顺时针旋转的角度; 与后端 `CropBoxRequest.rotation` 一致. */
export type CropRotation = 0 | 90 | 180 | 270;

export interface CropDialogLabels {
  title: string;
  confirm: string;
  needSelection: string;
  operationFailed: string;
  aspect: string;
  lockAspect: string;
  left: string;
  top: string;
  width: string;
  height: string;
  cancel: string;
  rotateLeft: string;
  rotateRight: string;
  resetRotation: string;
}

export interface ImageCropDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 源图 URL (外部或内部); 对话框显示与后端同一份字节. */
  imageUrl: string;
  /** 初始与锁定宽高比 (w/h); 变化时重置选区并重新装载图片. */
  defaultAspect: number;
  /** 高度受限时初始框的水平位置: center 居中, right 靠右. */
  initialAnchor: "center" | "right";
  labels: CropDialogLabels;
  /** 提交自然像素框与旋转角度; 提交后保持打开, 由调用方在成功后关闭. */
  onSubmit: (box: CropBox, rotation: CropRotation) => void;
  submitting: boolean;
  /** 标题栏右侧的附加控件, 例如切换裁切基准图. */
  headerExtra?: ReactNode;
  /** 打开时是否默认锁定宽高比; 标定窗口这类要自由拖的场景传 false. */
  defaultLockAspect?: boolean;
  /** 选区 (自然像素) 变化时回调; 供调用方取出「当前裁切框」做别的用途. */
  onSelectionChange?: (box: CropBox) => void;
  /** 基准图的自然像素尺寸就绪时回调; 换算比例要用到它. */
  onNaturalSizeChange?: (size: { width: number; height: number }) => void;
  /** 层叠高度; 嵌在别的弹框里时要给得比它高. */
  zIndex?: number;
}

interface DecodedImage {
  bitmap: ImageBitmap;
  width: number;
  height: number;
}

interface Size {
  width: number;
  height: number;
}

function rotatedSize(size: Size, rotation: CropRotation): Size {
  return rotation % 180 === 0 ? size : { width: size.height, height: size.width };
}

function nextRotation(current: CropRotation, delta: 90 | 270): CropRotation {
  const next = (current + delta) % 360;
  if (next === 90) return 90;
  if (next === 180) return 180;
  if (next === 270) return 270;
  return 0;
}

/**
 * 顺时针 90° 整数倍旋转: 纯像素重排, 不重采样.
 *
 * 不走 CSS `transform`: 布局盒与 react-image-crop 的坐标基准都不会随之改变, 选框与指针会错位.
 */
function drawRotated(canvas: HTMLCanvasElement, image: DecodedImage, rotation: CropRotation): void {
  const { width, height } = rotatedSize(image, rotation);
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.save();
  if (rotation === 90) {
    ctx.translate(width, 0);
    ctx.rotate(Math.PI / 2);
  } else if (rotation === 180) {
    ctx.translate(width, height);
    ctx.rotate(Math.PI);
  } else if (rotation === 270) {
    ctx.translate(0, height);
    ctx.rotate(-Math.PI / 2);
  }
  ctx.drawImage(image.bitmap, 0, 0);
  ctx.restore();
}

/** 显示像素 (CSS px) → 自然像素的缩放; canvas 后备存储是全分辨率, 显示尺寸受 CSS 约束. */
function displayScale(canvas: HTMLCanvasElement): { x: number; y: number } {
  return {
    x: canvas.width / (canvas.clientWidth || canvas.width),
    y: canvas.height / (canvas.clientHeight || canvas.height),
  };
}

/** 把选区坐标吸附到整像素: ReactCrop 给的是浮点, 直接存会一路带出 0.5px 的零头. */
function snapCrop(crop: Crop, canvas: HTMLCanvasElement | null): Crop {
  if (canvas === null) return crop;
  const shownW = canvas.clientWidth || 1;
  const shownH = canvas.clientHeight || 1;
  // displayScale 是「显示 → 自然」的倍数; 选区贴着整像素的自然是显示坐标 × scale.
  const scaleX = canvas.width / shownW;
  const scaleY = canvas.height / shownH;

  // 先还原成显示像素, 再换算成自然像素并取整 —— 取整必须在**自然像素**上做,
  // 只吸附显示坐标的话, 乘回 scale (通常不是整数) 后又冒出小数.
  const shown =
    crop.unit === "px"
      ? { x: crop.x, y: crop.y, width: crop.width, height: crop.height }
      : {
          x: (crop.x / 100) * shownW,
          y: (crop.y / 100) * shownH,
          width: (crop.width / 100) * shownW,
          height: (crop.height / 100) * shownH,
        };

  const natX = Math.round(shown.x * scaleX);
  const natY = Math.round(shown.y * scaleY);
  const natW = Math.max(1, Math.round(shown.width * scaleX));
  const natH = Math.max(1, Math.round(shown.height * scaleY));

  // 再折回显示坐标交给 ReactCrop; 这样由它换算出的自然坐标天然是整数.
  const backX = natX / scaleX;
  const backY = natY / scaleY;
  const backW = Math.max(1, natW / scaleX);
  const backH = Math.max(1, natH / scaleY);

  return { unit: "px", x: backX, y: backY, width: backW, height: backH };
}

/** 显示像素选区 → 原图像素框 (left/top/right/bottom, right/bottom 不含). */
function toNaturalBox(crop: Crop, canvas: HTMLCanvasElement): CropBox {
  const scale = displayScale(canvas);
  // ReactCrop 在百分比模式下给出 0~100 的数值; 不先归一就乘 scale, 会得到几万的假坐标.
  const shown =
    crop.unit === "px"
      ? { x: crop.x, y: crop.y, width: crop.width, height: crop.height }
      : {
          x: (crop.x / 100) * canvas.clientWidth,
          y: (crop.y / 100) * canvas.clientHeight,
          width: (crop.width / 100) * canvas.clientWidth,
          height: (crop.height / 100) * canvas.clientHeight,
        };
  const left = Math.max(0, Math.round(shown.x * scale.x));
  const top = Math.max(0, Math.round(shown.y * scale.y));
  const right = Math.min(
    canvas.width,
    Math.max(left + 1, Math.round((shown.x + shown.width) * scale.x)),
  );
  const bottom = Math.min(
    canvas.height,
    Math.max(top + 1, Math.round((shown.y + shown.height) * scale.y)),
  );
  return {
    left: Math.max(0, left),
    top: Math.max(0, top),
    right: Math.min(canvas.width, right),
    bottom: Math.min(canvas.height, bottom),
  };
}

/** 原图像素框 → 显示像素选区. */
function fromNaturalBox(box: CropBox, canvas: HTMLCanvasElement): PixelCrop {
  const scale = displayScale(canvas);
  // displayScale 是「显示 → 自然」的倍数, 这里是反方向, 必须**除**而不是乘:
  // 两个方向都用乘法的话, 一次「显示→自然→显示」往返会把坐标放大 scale² 倍
  // (图片比显示区大 8 倍时就是 64 倍), 反复操作输入框的数字会直接冲到几万.
  return {
    unit: "px",
    x: box.left / scale.x,
    y: box.top / scale.y,
    width: Math.max(1, (box.right - box.left) / scale.x),
    height: Math.max(1, (box.bottom - box.top) / scale.y),
  };
}

/** 默认选区: 符合 aspect (w/h) 的最大内接框; 高度受限时打满高度, 宽度受限时打满宽度靠上. */
function initialAspectCrop(
  naturalWidth: number,
  naturalHeight: number,
  aspect: number,
  anchor: "center" | "right",
): Crop {
  const imageRatio = naturalWidth / naturalHeight;
  if (imageRatio >= aspect) {
    const widthPct = (aspect / imageRatio) * 100;
    const x = anchor === "right" ? 100 - widthPct : (100 - widthPct) / 2;
    return { unit: "%", x, y: 0, width: widthPct, height: 100 };
  }
  const heightPct = (imageRatio / aspect) * 100;
  return { unit: "%", x: 0, y: 0, width: 100, height: heightPct };
}

/** 显示像素选区 → 百分比选区; ReactCrop 的 aspect 在百分比下不会累积取整误差. */
function toCropPercent(
  crop: { x: number; y: number; width: number; height: number },
  canvas: HTMLCanvasElement,
): Crop {
  const w = canvas.clientWidth || 1;
  const h = canvas.clientHeight || 1;
  return {
    unit: "%",
    x: (crop.x / w) * 100,
    y: (crop.y / h) * 100,
    width: (crop.width / w) * 100,
    height: (crop.height / h) * 100,
  };
}

/** NumberInput 的 onChange 可能给字符串 (清空时是空串), 统一解析成数字或 undefined. */
function parseNumberInput(value: number | string): number | undefined {
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** Percent/pixel Crop → 显示像素 PixelCrop. */
function toPixelCrop(crop: Crop, canvas: HTMLCanvasElement): PixelCrop {
  if (crop.unit === "px") {
    return {
      unit: "px",
      x: crop.x,
      y: crop.y,
      width: crop.width,
      height: crop.height,
    };
  }
  return {
    unit: "px",
    x: (crop.x / 100) * canvas.clientWidth,
    y: (crop.y / 100) * canvas.clientHeight,
    width: (crop.width / 100) * canvas.clientWidth,
    height: (crop.height / 100) * canvas.clientHeight,
  };
}

function clampAspect(value: number, fallback: number): number {
  if (!Number.isFinite(value) || value <= 0) return fallback;
  return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, value));
}

type BoxState = { left: number; top: number; width: number; height: number };

const EMPTY_BOX: BoxState = { left: 0, top: 0, width: 0, height: 0 };

function fitLockedBox(
  next: BoxState,
  imageSize: Size,
  aspect: number,
  anchor: "width" | "height",
): BoxState {
  let left = Math.max(0, Math.min(next.left, imageSize.width - 1));
  let top = Math.max(0, Math.min(next.top, imageSize.height - 1));
  let width = Math.max(1, Math.round(next.width));
  let height = Math.max(1, Math.round(next.height));

  if (anchor === "width") {
    width = Math.min(width, imageSize.width - left);
    height = Math.max(1, Math.round(width / aspect));
    if (top + height > imageSize.height) {
      height = imageSize.height - top;
      width = Math.max(1, Math.round(height * aspect));
      if (left + width > imageSize.width) {
        width = imageSize.width - left;
        height = Math.max(1, Math.round(width / aspect));
      }
    }
  } else {
    height = Math.min(height, imageSize.height - top);
    width = Math.max(1, Math.round(height * aspect));
    if (left + width > imageSize.width) {
      // 优先右对齐腾出宽度
      left = Math.max(0, imageSize.width - width);
      if (left + width > imageSize.width) {
        width = imageSize.width - left;
        height = Math.max(1, Math.round(width / aspect));
        if (top + height > imageSize.height) {
          height = imageSize.height - top;
          width = Math.max(1, Math.round(height * aspect));
          left = Math.max(0, imageSize.width - width);
        }
      }
    }
  }

  return { left, top, width, height };
}

/**
 * 通用像素框裁切对话框: 显示 `imageUrl` 当前字节, 只提交自然像素框与旋转角度.
 *
 * 角度变化时重绘 canvas (解码只做一次), 坐标始终是旋转后的像素系;
 * 调用方负责 mutation 与成功后的关闭; 本组件在提交后保持打开.
 */
export function ImageCropDialog({
  opened,
  onClose,
  imageUrl,
  defaultAspect,
  initialAnchor,
  labels,
  onSubmit,
  submitting,
  headerExtra,
  defaultLockAspect = false,
  onSelectionChange,
  onNaturalSizeChange,
  zIndex = Z_INDEX.cropDialog,
}: ImageCropDialogProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [source, setSource] = useState<DecodedImage | null>(null);
  const [rotation, setRotation] = useState<CropRotation>(0);
  const [crop, setCrop] = useState<Crop>();
  const [completedCrop, setCompletedCrop] = useState<PixelCrop>();
  const [lockAspect, setLockAspect] = useState(defaultLockAspect);
  const [aspectRatio, setAspectRatio] = useState(defaultAspect);
  const [box, setBox] = useState<BoxState>(EMPTY_BOX);
  const [loadError, setLoadError] = useState(false);
  // 放大镜要从画布取像素; 渲染期不能读 ref, 因此另存一份到 state.
  const [canvasEl, setCanvasEl] = useState<HTMLCanvasElement | null>(null);
  const aspectRef = useRef(aspectRatio);
  const naturalSize = useMemo(
    () => (source ? rotatedSize(source, rotation) : null),
    [source, rotation],
  );

  // 基准图尺寸就绪后抛给调用方: 换算裁切框比例需要它.
  const onNaturalSizeRef = useRef(onNaturalSizeChange);
  useEffect(() => {
    onNaturalSizeRef.current = onNaturalSizeChange;
  }, [onNaturalSizeChange]);
  useEffect(() => {
    if (naturalSize !== null) onNaturalSizeRef.current?.(naturalSize);
  }, [naturalSize]);

  // 打开/关闭用 opened 单独记, 换图看 imageUrl 本身.
  // 早先把 opened 掺进 imageKey (关闭时置空), 于是每次重开都被当成「换了图」, 走清空分支;
  // 而那个分支同时把 source 也清掉了, 后面重建选区时又依赖 source, 结果选框再也不出现.
  const sessionKey = `${String(opened)}:${imageUrl}:${defaultAspect}`;
  const [prevSession, setPrevSession] = useState(sessionKey);
  const [prevImage, setPrevImage] = useState(imageUrl);
  if (sessionKey !== prevSession) {
    setPrevSession(sessionKey);
    setLockAspect(defaultLockAspect);
    setAspectRatio(defaultAspect);
    if (!opened) {
      // 关闭时只记下状态, 位图与选区留着: 重新打开同一张图要能立刻用上.
      setCompletedCrop(undefined);
      setBox(EMPTY_BOX);
    } else if (imageUrl !== prevImage) {
      // 换了图源: 位图、旋转、选区都要重来.
      setPrevImage(imageUrl);
      setCompletedCrop(undefined);
      setBox(EMPTY_BOX);
      setSource(null);
      setRotation(0);
      setLoadError(false);
      setCrop(undefined);
    } else if (source === null) {
      // 首次打开同一张图: 位图还没解码, 等 drawRotated 的 effect 建好选区.
      setCompletedCrop(undefined);
      setBox(EMPTY_BOX);
    } else {
      // 重开同一张图且位图还在: 按当前比例重建选区.
      setCompletedCrop(undefined);
      setBox(EMPTY_BOX);
      const size = rotatedSize(source, rotation);
      setCrop(initialAspectCrop(size.width, size.height, aspectRatio, initialAnchor));
    }
  }

  useEffect(() => {
    aspectRef.current = aspectRatio;
  }, [aspectRatio]);

  // 裁切必须与后端 acquire 的同一份字节对齐: 绕过 HTTP 磁盘缓存强制拉取, 并且只解码一次.
  useEffect(() => {
    if (!opened) return;

    const proxied = proxyImageUrl(imageUrl) ?? imageUrl;
    const ac = new AbortController();

    void (async () => {
      try {
        const res = await apiFetch(proxied, { cache: "no-store", signal: ac.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        // 显式要求按 EXIF 方向解码: 与后端 `exif_transpose` 后的像素系一致.
        const bitmap = await createImageBitmap(blob, { imageOrientation: "from-image" });
        if (ac.signal.aborted) {
          bitmap.close();
          return;
        }
        setSource({ bitmap, width: bitmap.width, height: bitmap.height });
      } catch {
        if (!ac.signal.aborted) setLoadError(true);
      }
    })();

    return () => ac.abort();
  }, [opened, imageUrl]);

  // 位图被替换或对话框关闭时释放.
  useEffect(() => {
    const bitmap = source?.bitmap;
    return () => bitmap?.close();
  }, [source]);

  // 选区变化都汇聚到这里: 更新输入框的值, 顺带把自然像素框抛给调用方.
  const onSelectionRef = useRef(onSelectionChange);
  useEffect(() => {
    onSelectionRef.current = onSelectionChange;
  }, [onSelectionChange]);

  const syncBoxFromCrop = useCallback((pixelCrop: Crop, canvas: HTMLCanvasElement) => {
    const natural = toNaturalBox(pixelCrop, canvas);
    const next = {
      left: natural.left,
      top: natural.top,
      width: natural.right - natural.left,
      height: natural.bottom - natural.top,
    };
    setBox(next);
    onSelectionRef.current?.(natural);
  }, []);

  // 换角度后按新坐标系重绘, 并把选区重置为该比例下的默认框 (原框已不在画布内).
  // 只在「source 或 rotation 真的变了」时重建选区: 依赖里含 initialAnchor / syncBoxFromCrop,
  // 而选区随每次交互变化, 无条件重建会让框在多次开关后越铺越大.
  const cropSessionRef = useRef<string>("");
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!source || !canvas) return;
    drawRotated(canvas, source, rotation);
    // 重开对话框时也要重建: 把 opened 计入 key, 关闭再打开就会重新算一次选区.
    const key = `${String(opened)}:${String(rotation)}:${imageUrl}`;
    if (cropSessionRef.current === key) return;
    cropSessionRef.current = key;
    const next = initialAspectCrop(canvas.width, canvas.height, aspectRef.current, initialAnchor);
    setCrop(next);
    setCompletedCrop(undefined);
    if (canvas.clientWidth > 0) {
      const pixel = toPixelCrop(next, canvas);
      setCompletedCrop(pixel);
      syncBoxFromCrop(pixel, canvas);
    } else {
      setBox(EMPTY_BOX);
    }
  }, [source, rotation, initialAnchor, syncBoxFromCrop, imageUrl, opened]);

  // 初始 percent crop / 拖拽中同步像素框到 NumberInput
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!crop || !canvas || canvas.width === 0 || canvas.clientWidth === 0 || !naturalSize) return;
    const pixel = toPixelCrop(crop, canvas);
    if (pixel.width < 1 || pixel.height < 1) return;
    setCompletedCrop(pixel);
    syncBoxFromCrop(pixel, canvas);
  }, [crop, naturalSize, syncBoxFromCrop]);

  const commitBox = (next: BoxState) => {
    const canvas = canvasRef.current;
    if (!canvas || canvas.clientWidth === 0) return;
    const pixel = fromNaturalBox(
      {
        left: next.left,
        top: next.top,
        right: next.left + next.width,
        bottom: next.top + next.height,
      },
      canvas,
    );
    setCrop(pixel);
    setCompletedCrop(pixel);
    setBox(next);
  };

  const applyNaturalBox = (
    next: BoxState,
    options?: { locked?: boolean; aspect?: number; anchor?: "width" | "height" },
  ) => {
    if (!naturalSize) return;
    const locked = options?.locked ?? lockAspect;
    const aspect = clampAspect(options?.aspect ?? aspectRatio, defaultAspect);
    const anchor = options?.anchor ?? "height";

    if (!locked) {
      const left = Math.max(0, Math.min(next.left, naturalSize.width - 1));
      const top = Math.max(0, Math.min(next.top, naturalSize.height - 1));
      const width = Math.max(1, Math.min(Math.round(next.width), naturalSize.width - left));
      const height = Math.max(1, Math.min(Math.round(next.height), naturalSize.height - top));
      commitBox({ left, top, width, height });
      return;
    }

    commitBox(fitLockedBox(next, naturalSize, aspect, anchor));
  };

  /** 把当前选区收成 aspect 比例: 高度不动, 宽度 = 高度 × 比例, 水平居中到原中心. */
  /**
   * 把当前选区收成 aspect 比例: 高度不动, 宽度 = 高度 × 比例, 水平居中.
   *
   * 用**百分比**回写给 ReactCrop, 而不是自己算像素: 像素取整后比例永远差一点点
   * (1765/2507 = 0.70004 而非 0.7), 而 ``aspect`` 属性会据此反复「修正」选区 ——
   * onChange 又把修正值送回来重算, 于是选框来回抖. 百分比没有取整误差, 一次到位.
   */
  const lockToAspect = () => {
    const canvas = canvasRef.current;
    if (!canvas || !naturalSize || !crop) return;
    const pct = crop.unit === "px" ? toCropPercent(crop, canvas) : crop;
    if (pct.width <= 0 || pct.height <= 0) return;

    // 高度不动, 宽度 = 高度 × (比例 / 图片宽高比); 百分比里的纵横都归一到 0~100.
    const imageRatio = naturalSize.width / naturalSize.height;
    const widthPct = Math.min(100, (pct.height * aspectRatio) / imageRatio);
    const heightPct = (widthPct * imageRatio) / aspectRatio;
    const centerXPct = pct.x + pct.width / 2;
    const xPct = Math.max(0, Math.min(centerXPct - widthPct / 2, 100 - widthPct));
    const yPct = Math.max(0, Math.min(pct.y, 100 - heightPct));

    setCrop({ unit: "%", x: xPct, y: yPct, width: widthPct, height: heightPct });
    setCompletedCrop(undefined);
  };

  const reshapeWithAspect = (nextAspect: number) => {
    const aspect = clampAspect(nextAspect, defaultAspect);
    setAspectRatio(aspect);
    if (!naturalSize) return;
    // 未锁定时只更新比例值, 不强制修改选区; 锁定时按新比例重塑
    if (!lockAspect) return;
    const height = box.height > 0 ? box.height : naturalSize.height;
    const top = box.height > 0 ? box.top : 0;
    const preferRight = box.width === 0 || box.left + box.width >= naturalSize.width - 1;
    let width = Math.max(1, Math.round(height * aspect));
    let left = box.left;
    if (preferRight || left + width > naturalSize.width) {
      left = Math.max(0, naturalSize.width - width);
    }
    applyNaturalBox({ left, top, width, height }, { locked: true, aspect, anchor: "height" });
  };

  const rotateBy = (delta: 90 | 270) => {
    setRotation((current) => nextRotation(current, delta));
  };

  const handleConfirm = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const pixel =
      completedCrop && completedCrop.width >= 1 && completedCrop.height >= 1
        ? completedCrop
        : crop
          ? toPixelCrop(crop, canvas)
          : undefined;
    if (!pixel || pixel.width < 1 || pixel.height < 1) {
      notifications.show({ message: labels.needSelection, color: "red" });
      return;
    }
    const natural = toNaturalBox(pixel, canvas);
    if (natural.right <= natural.left || natural.bottom <= natural.top) {
      notifications.show({ message: labels.needSelection, color: "red" });
      return;
    }
    onSubmit(natural, rotation);
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={
        <Group gap="sm" wrap="nowrap">
          <span>{labels.title}</span>
          {headerExtra}
        </Group>
      }
      size="min(92vw, 64rem)"
      centered
      // 可能叠在别的弹框里 (标定裁切窗口), 需要压在上层才能点到画布.
      zIndex={zIndex}
    >
      <Stack gap="md">
        {/* 舞台自身不出滚动条: 出条会改变可用宽度, 画布随之重算尺寸, 拖动时选框就会跳.
            画布按 contain 等比缩到这块区域里, 尺寸只由容器决定一次. */}
        <div
          style={{
            height: "64vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            overflow: "hidden",
          }}
        >
          {loadError ? (
            <Text c="red" size="sm">
              {labels.operationFailed}
            </Text>
          ) : !source ? (
            <Group justify="center" p="xl">
              <Loader size="sm" />
            </Group>
          ) : (
            <div
              style={{
                position: "relative",
                maxWidth: "100%",
                maxHeight: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <ReactCrop
                crop={crop}
                onChange={(c) => {
                  const snapped = snapCrop(c, canvasRef.current);
                  setCrop(snapped);
                  // 拖动过程中就跟着更新比例, 不必等松手.
                  if (!lockAspect && canvasRef.current && snapped.width > 0 && snapped.height > 0) {
                    setAspectRatio(
                      Number(
                        clampAspect(snapped.width / snapped.height, aspectRef.current).toFixed(3),
                      ),
                    );
                  }
                }}
                onComplete={(c) => {
                  // 松手时也走一次吸附: 拖动过程中的浮点残留不该落进输入框.
                  // completedCrop 只用于「有选区」的判断, 保持 ReactCrop 给的像素值;
                  // 吸附后的整数框由 syncBoxFromCrop 写进 box.
                  setCompletedCrop(c);
                  if (canvasRef.current && c.width > 0 && c.height > 0) {
                    const snapped = snapCrop(c, canvasRef.current);
                    syncBoxFromCrop(snapped, canvasRef.current);
                    // 自由拖动时把宽高比跟着选区走: 左上角那个数值要能读到刚拖出来的实际比例.
                    if (!lockAspect && snapped.height > 0) {
                      setAspectRatio(
                        Number(
                          clampAspect(snapped.width / snapped.height, aspectRef.current).toFixed(3),
                        ),
                      );
                    }
                  }
                }}
                aspect={lockAspect ? aspectRatio : undefined}
                keepSelection
              >
                <canvas
                  ref={(node) => {
                    canvasRef.current = node;
                    setCanvasEl(node);
                  }}
                  role="img"
                  aria-label="crop source"
                  style={{
                    maxWidth: "100%",
                    maxHeight: "64vh",
                    width: "auto",
                    height: "auto",
                    objectFit: "contain",
                    display: "block",
                  }}
                />
              </ReactCrop>
              <CropMagnifier source={canvasEl} crop={crop} enabled={opened} />
              {naturalSize && (
                <Text
                  size="xs"
                  c="dimmed"
                  style={{
                    position: "absolute",
                    right: 8,
                    bottom: 8,
                    pointerEvents: "none",
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: "color-mix(in srgb, var(--mantine-color-body) 75%, transparent)",
                  }}
                >
                  {naturalSize.width}×{naturalSize.height}
                </Text>
              )}
            </div>
          )}
        </div>

        <Group gap="xs">
          <ActionIcon
            variant="default"
            size="md"
            onClick={() => rotateBy(270)}
            disabled={!source}
            aria-label={labels.rotateLeft}
          >
            <IconRotate size={18} style={{ transform: "scaleX(-1)" }} />
          </ActionIcon>
          <ActionIcon
            variant="default"
            size="md"
            onClick={() => rotateBy(90)}
            disabled={!source}
            aria-label={labels.rotateRight}
          >
            <IconRotate size={18} />
          </ActionIcon>
          <ActionIcon
            variant="default"
            size="md"
            onClick={() => setRotation(0)}
            disabled={rotation === 0}
            aria-label={labels.resetRotation}
          >
            <IconArrowBackUp size={18} />
          </ActionIcon>
        </Group>

        <Group align="flex-start" wrap="wrap" gap="sm">
          <Stack gap={6} style={{ width: 120 }}>
            <NumberInput
              label={labels.aspect}
              value={aspectRatio}
              min={MIN_ASPECT}
              max={MAX_ASPECT}
              step={0.01}
              decimalScale={3}
              onChange={(v) => {
                if (typeof v !== "number") return;
                reshapeWithAspect(v);
              }}
            />
            <Switch
              label={labels.lockAspect}
              checked={lockAspect}
              onChange={(e) => {
                const checked = e.currentTarget.checked;
                setLockAspect(checked);
                // 开锁定: 以 crop (ReactCrop 的真值) 为准换算, 不经 box.
                // box 是 effect 从 crop 异步写出的派生值, 拿它回推会引入滞后误差 —— 反复
                // 开关时每轮都按上一轮的近似值重算, 选框于是越走越歪.
                if (checked) lockToAspect();
              }}
            />
          </Stack>
          <NumberInput
            label={labels.left}
            value={box.left}
            min={0}
            max={naturalSize ? naturalSize.width - 1 : undefined}
            allowDecimal={false}
            step={1}
            clampBehavior="strict"
            onChange={(v) =>
              applyNaturalBox({
                ...box,
                left: parseNumberInput(v) ?? box.left,
              })
            }
            style={{ width: 110 }}
          />
          <NumberInput
            label={labels.top}
            value={box.top}
            min={0}
            max={naturalSize ? naturalSize.height - 1 : undefined}
            allowDecimal={false}
            step={1}
            clampBehavior="strict"
            onChange={(v) =>
              applyNaturalBox({
                ...box,
                top: parseNumberInput(v) ?? box.top,
              })
            }
            style={{ width: 110 }}
          />
          <NumberInput
            label={labels.width}
            value={box.width}
            min={1}
            max={naturalSize ? naturalSize.width : undefined}
            allowDecimal={false}
            step={1}
            clampBehavior="strict"
            onChange={(v) =>
              applyNaturalBox(
                { ...box, width: parseNumberInput(v) ?? box.width },
                { anchor: "width" },
              )
            }
            style={{ width: 110 }}
          />
          <NumberInput
            label={labels.height}
            value={box.height}
            min={1}
            max={naturalSize ? naturalSize.height : undefined}
            allowDecimal={false}
            step={1}
            clampBehavior="strict"
            disabled={lockAspect}
            onChange={(v) =>
              applyNaturalBox({
                ...box,
                height: parseNumberInput(v) ?? box.height,
              })
            }
            style={{ width: 110 }}
          />
        </Group>

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {labels.cancel}
          </Button>
          <Button
            onClick={handleConfirm}
            loading={submitting}
            disabled={!source || loadError || !naturalSize}
          >
            {labels.confirm}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
