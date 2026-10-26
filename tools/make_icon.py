#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 .app 图标（iconset 目录，交给 iconutil 打包成 .icns）。"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = 1024
FONT_CANDIDATES = [
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", (0, 1, 2)),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", (0, 1)),
    ("/System/Library/Fonts/STHeiti Medium.ttc", (0,)),
]
TOP = (216, 27, 52)      # #d81b34
BOTTOM = (150, 10, 32)   # #960a20


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)
    return img


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path, indexes in FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        for idx in indexes:
            try:
                return ImageFont.truetype(path, size, index=idx)
            except Exception:
                continue
    return ImageFont.load_default()


def make_master() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    base = gradient(SIZE).convert("RGBA")
    canvas.paste(base, (0, 0), rounded_mask(SIZE, 210))

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((34, 34, SIZE - 34, SIZE - 34), radius=180, outline=(255, 255, 255, 46), width=5)

    font = load_font(560)
    text = "抢"
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x = (SIZE - w) / 2 - box[0]
    y = (SIZE - h) / 2 - box[1]

    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x + 10, y + 14), text, font=font, fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    canvas = Image.alpha_composite(canvas, shadow)
    ImageDraw.Draw(canvas).text((x, y), text, font=font, fill=(255, 255, 255, 255))
    return canvas


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "AppIcon.iconset")
    out.mkdir(parents=True, exist_ok=True)
    master = make_master()

    plan = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for px, name in plan:
        master.resize((px, px), Image.LANCZOS).save(out / name)
    print(f"iconset 已生成：{out}（{len(plan)} 个尺寸）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
