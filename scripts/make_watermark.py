"""按现有角标风格生成自定义水印 PNG.

现有角标 (subtitle / uncensored / cracked / leaked / 分辨率) 是同一套规格:
1000x600 画布上, 一个 877x433 的全圆头胶囊, 纯色填充, 白色粗体居中, 外缘带柔和投影.
本脚本按同一套参数生成新角标, 使其与内置的视觉一致.

用法::

    python make_watermark.py LTX "#E11D48" ltx.png
    python make_watermark.py "4K HDR" "#0EA5E9" hdr.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CANVAS = (1000, 600)
PILL = (877, 433)
RADIUS = 216  # 半高, 即完全圆头; 实测内置角标顶边直线段 253..747, 与半圆一致
TEXT_MAX_H = 213
SHADOW_BLUR = 12
SHADOW_OFFSET = (0, 6)
SHADOW_ALPHA = 70

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",  # 微软雅黑 Bold
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/seguisb.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
)


def _hex(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    raise SystemExit("找不到可用粗体字体, 请把字体路径加进 _FONT_CANDIDATES")


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_h: int, max_w: int) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int, int]]:
    """按目标像素高度二分字号; 太宽时同时受宽度约束."""
    size = max_h
    while size > 8:
        font = _font(size)
        box = draw.textbbox((0, 0), text, font=font)
        width, height = box[2] - box[0], box[3] - box[1]
        if height <= max_h and width <= max_w:
            return font, box
        size -= 4
    font = _font(8)
    return font, draw.textbbox((0, 0), text, font=font)


def build(text: str, color: str, out: Path) -> None:
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    fill = _hex(color)

    pill = Image.new("L", (PILL[0], PILL[1]), 0)
    ImageDraw.Draw(pill).rounded_rectangle((0, 0, PILL[0] - 1, PILL[1] - 1), radius=RADIUS, fill=255)

    # 投影: 胶囊轮廓模糊后压到画布下方, 模拟内置角标的柔和外缘.
    shadow_mask = pill.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ox = (CANVAS[0] - PILL[0]) // 2 + SHADOW_OFFSET[0]
    oy = (CANVAS[1] - PILL[1]) // 2 + SHADOW_OFFSET[1]
    shadow_tile = Image.new("RGBA", PILL, (*fill, SHADOW_ALPHA))
    shadow_layer.paste(shadow_tile, (ox, oy), shadow_mask)
    canvas = Image.alpha_composite(canvas, shadow_layer)

    body_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    body_tile = Image.new("RGBA", PILL, (*fill, 255))
    body_layer.paste(body_tile, ((CANVAS[0] - PILL[0]) // 2, (CANVAS[1] - PILL[1]) // 2), pill)
    canvas = Image.alpha_composite(canvas, body_layer)

    draw = ImageDraw.Draw(canvas)
    inner_w = int(PILL[0] * 0.82)
    font, box = _fit_text(draw, text, TEXT_MAX_H, inner_w)
    tw, th = box[2] - box[0], box[3] - box[1]
    pos = ((CANVAS[0] - tw) / 2 - box[0], (CANVAS[1] - th) / 2 - box[1])
    draw.text(pos, text, font=font, fill=(255, 255, 255, 255))

    canvas.save(out, "PNG")
    print(f"wrote {out} ({CANVAS[0]}x{CANVAS[1]}, {out.stat().st_size} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit("用法: python make_watermark.py <文字> <#RRGGBB> <输出文件>")
    build(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
