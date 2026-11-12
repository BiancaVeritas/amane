"""刮削期按 ``download_resources`` 下载 / 裁剪 / 就地超分, 返回应写入 metadata 的 URL.

与整理到媒体库路径无关. 任一步失败不抛, 仅降级.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..config import DownloadableResource
from ..parsing import ContentType
from ..sr import get_preset_meta, run_SR
from .crop_rules import rule_window
from .images import (
    CropRotation,
    allows_poster_crop,
    crop_box,
    crop_poster,
    crop_poster_by_rule,
    format_crop_box_args,
    needs_upscale,
    probe_oriented_size,
    probe_size,
    rotated_size,
    validate_crop_box,
)
from .resource_store import RESOURCE_URL_PREFIX

if TYPE_CHECKING:
    from ..config import HotSettings, PosterCropRule, SrConfig
    from ..db.models import Resource
    from ..net.http import WebClient
    from .resource_store import ResourceStore

logger = structlog.get_logger()


@dataclass
class MaterializedImages:
    # fanart 不是 metadata 字段 (整理时按 JAV 约定取 thumb), 不在此返回.

    poster_urls: list[str]
    thumb_urls: list[str]
    trailer_urls: list[str]


def _internal_url(store: ResourceStore, locator_url: str) -> str:
    return f"{RESOURCE_URL_PREFIX}/{store.url_hash(locator_url)}"


def _winner_first(urls: list[str], winner: str | None) -> list[str]:
    """成功者前置, 其余候选 (下载失败与未尝试的) 保序留在尾部, 供来源恢复后重试."""
    return ([winner] if winner else []) + [u for u in urls if u != winner]


async def _acquire_first(urls: list[str], store: ResourceStore, client: WebClient) -> tuple[str | None, Path | None]:
    """按序尝试候选, 命中即停. 候选是同一张图的回退链, 逐个下载只会把它在 Resource 里存成多份."""
    for url in urls:
        local = await store.acquire(url, client)
        if local:
            return url, local
    return None, None


async def _fallback_posters(
    store: ResourceStore,
    poster_urls: list[str],
    client: WebClient,
    config: HotSettings,
    data_dir: Path,
) -> list[str]:
    """从封面裁不出海报时的退路: 下载站点原图并用它当海报.

    只保留真正下载成功的地址; 全部失败时返回原列表, 交给下游按死链处理.
    """
    succeeded: set[str] = set()
    first_local: Resource | None = None
    for url in poster_urls:
        local = await store.acquire(url, client)
        if not local:
            continue
        succeeded.add(url)
        if first_local is None:
            first_local = await store.get_by_url(url)
    if first_local is not None:
        await _maybe_upscale(store, first_local, config, data_dir)
    # 成功的放到最前, 其余保序留在尾部: 下游按头部取图, 尾部留给来源恢复后重试.
    ordered = _winner_first(poster_urls, next(iter(succeeded)) if succeeded else None)
    return ordered if succeeded else list(poster_urls)


def sr_args_dict(cfg: SrConfig) -> dict:
    pm = get_preset_meta(cfg.preset)
    return {"preset": cfg.preset, "tool": pm.tool, "model": pm.model, "scale": pm.scale}


async def _maybe_upscale(
    store: ResourceStore,
    resource: Resource,
    config: HotSettings,
    data_dir: Path,
) -> None:
    if not config.sr.enabled:
        return
    full = store.full_path(resource)
    size = probe_size(full)
    file_bytes = full.stat().st_size if full.exists() else 0
    if not needs_upscale(
        size,
        file_bytes,
        max_dim_threshold=config.sr.max_dim_threshold,
        max_bytes_threshold=config.sr.max_bytes_threshold,
    ):
        return

    async def producer(src: Path, out: Path) -> bool:
        result = await run_SR(src, out, config.sr, data_dir)
        return result.success

    await store.upscale_in_place(resource, sr_args_dict(config.sr), producer)


async def materialize_images(
    poster_urls: list[str],
    thumb_urls: list[str],
    trailer_urls: list[str],
    store: ResourceStore,
    client: WebClient,
    config: HotSettings,
    data_dir: Path,
    *,
    content_type: ContentType | None = None,
    extrafanart_urls: dict[str, list[str]] | None = None,
    recrop: bool = False,
    crop_rule: PosterCropRule | None = None,
) -> MaterializedImages:
    """仅下载集合内的类型; 未选中的图片类型保留聚合 URL.

    预告片是例外: 未启用预告片下载时, 同时清空其 URL, 避免下游仍显示播放入口.
    候选按序尝试, 命中即停; 未命中的候选保序留在尾部, 供来源恢复后重试.
    extrafanart 仅下载到 Resource, 站点分组结构不重排.

    ``crop_rule`` 命中时按规则给的固定窗口裁海报, 优先于人脸检测.
    """
    kinds = set(config.scraping.download_resources)

    # 下载 thumb.
    thumb_winner: str | None = None
    thumb_src: str | None = None
    thumb_local: Path | None = None
    if DownloadableResource.thumb in kinds:
        thumb_src, thumb_local = await _acquire_first(thumb_urls, store, client)
        thumb_winner = thumb_src  # 下面的兜底裁剪会改写 thumb_src, 排序只认本步下载到的那个
        if thumb_src:
            res = await store.get_by_url(thumb_src)
            if res:
                await _maybe_upscale(store, res, config, data_dir)

    # 海报一律从封面裁: 站点给的海报横向居多, 尺寸与构图都不统一, 也不能按人脸定位.
    # 裁剪失败时才退回站点原图, 免得一张海报也拿不到.
    result_poster_urls = list(poster_urls)
    if DownloadableResource.poster in kinds:
        # 裁剪需要 thumb 本地文件; 若未选 thumb 下载, 为裁剪临时 acquire 首个 thumb.
        # 用 _acquire_first 而不是手写循环: 候选按序尝试, 命中即停,
        # 同一张图的回退链不会在 Resource 里存成多份.
        if thumb_local is None and thumb_urls:
            thumb_src, thumb_local = await _acquire_first(thumb_urls, store, client)

        if config.scraping.crop_poster and thumb_local and allows_poster_crop(content_type):
            ratio = (
                config.scraping.uncensored_poster_ratio
                if content_type == ContentType.UNCENSORED
                else config.scraping.poster_ratio
            )
            local_thumb = thumb_local
            # 规则的派生键带上窗口参数, 否则改规则后仍命中旧产物.
            # use_aspect 时窗口依赖封面尺寸, 键里必须含「宽高比 + 对齐 + 上边距」,
            # 否则换一张同系列但分辨率不同的封面会命中上一次的裁切结果.
            rule = crop_rule
            if rule is None:
                args = f"{ratio:.4f}"
            elif rule.use_aspect:
                args = (
                    f"rule:aspect={rule.aspect:.4f},align={rule.align}"
                    f",top={rule.top:.4f},height={rule.height:.4f},left={rule.left:.4f}"
                )
            else:
                args = f"rule:{rule.left:.4f},{rule.top:.4f},{rule.width:.4f},{rule.height:.4f}"

            async def crop_producer(dest: Path) -> bool:
                if rule is not None:
                    # 用 exif 转正后的尺寸: 裁切端也是按转正后的图算框的.
                    size = probe_oriented_size(local_thumb)
                    width_px, height_px = size if size is not None else (0, 0)
                    left, top, width, height = rule_window(
                        rule, image_width=width_px, image_height=height_px
                    )
                    return crop_poster_by_rule(
                        local_thumb,
                        dest,
                        left=left,
                        top=top,
                        width=width,
                        height=height,
                        jpeg_quality=config.scraping.jpeg_quality,
                    )
                return crop_poster(
                    local_thumb,
                    dest,
                    poster_ratio=ratio,
                    jpeg_quality=config.scraping.jpeg_quality,
                    detect_face=config.scraping.poster_crop_detect_face,
                )

            crop_res = await store.acquire_derived(thumb_src or "", "crop", args, crop_producer, force=recrop)
            if crop_res:
                await _maybe_upscale(store, crop_res, config, data_dir)
                result_poster_urls = [_internal_url(store, crop_res.url)]
            else:
                # 裁不出来才退到站点原图, 至少别让海报空着.
                result_poster_urls = await _fallback_posters(store, poster_urls, client, config, data_dir)
        else:
            result_poster_urls = await _fallback_posters(store, poster_urls, client, config, data_dir)

    # 下载预告片.
    trailer_winner: str | None = None
    if DownloadableResource.trailer in kinds:
        trailer_winner, _ = await _acquire_first(trailer_urls, store, client)

    # 下载剧照: 按站点优先级, 有结果即停.
    if DownloadableResource.extrafanart in kinds and extrafanart_urls:
        priority = list(extrafanart_urls.keys())
        await store.acquire_extrafanart(extrafanart_urls, priority, client)

    out = MaterializedImages(
        poster_urls=result_poster_urls,
        thumb_urls=_winner_first(thumb_urls, thumb_winner),
        # 未启用预告片下载时清空 URL, 避免下游仍显示播放入口.
        trailer_urls=(_winner_first(trailer_urls, trailer_winner) if DownloadableResource.trailer in kinds else []),
    )
    logger.debug(
        "images materialized",
        kinds=sorted(kinds),
        poster=out.poster_urls[:1],
        thumb=out.thumb_urls[:1],
        trailer=out.trailer_urls[:1],
    )
    return out


async def manual_crop_image(
    source_url: str,
    box: tuple[int, int, int, int],
    store: ResourceStore,
    client: WebClient,
    config: HotSettings,
    data_dir: Path,
    *,
    subject: str,
    rotation: CropRotation = 0,
) -> str:
    """手动裁切源图, 返回内部 URL. 源可为外部 http(s) 或 ``/api/resources/{hash}``.

    先按 EXIF 转正、再顺时针旋转 ``rotation``, 之后按 ``box`` 裁切; 失败抛 ``ValueError``
    (消息可直接作 API detail); ``subject`` 组装提示文案 (如「封面图」「头像」).
    """
    source = await store.resolve_source(source_url, client)
    if source is None:
        raise ValueError(f"无法获取{subject}")

    size = probe_oriented_size(source.path)
    if size is None:
        raise ValueError(f"{subject}无法读取")
    if not validate_crop_box(box, rotated_size(size, rotation)):
        raise ValueError("裁切区域无效")

    args = format_crop_box_args(*box, rotation=rotation)
    jpeg_quality = config.scraping.jpeg_quality

    async def producer(dest: Path) -> bool:
        return crop_box(source.path, dest, box, rotation=rotation, jpeg_quality=jpeg_quality)

    crop_res = await store.acquire_derived(source.locator, "crop", args, producer)
    if crop_res is None:
        raise ValueError("裁切失败")

    await _maybe_upscale(store, crop_res, config, data_dir)
    return _internal_url(store, crop_res.url)
