"""生成 App 图标（1024 母版 + iOS/Android 各尺寸）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "app" / "static"
ICONS = OUT / "icons"
ICONS.mkdir(parents=True, exist_ok=True)

SIZE = 1024
BG_TOP = (17, 22, 34)
BG_BOTTOM = (10, 30, 48)
ACCENT = (91, 157, 255)
ACCENT2 = (57, 217, 138)


def radial_base() -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), BG_TOP)
    px = img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            t = (x + y) / (2 * SIZE)
            r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
            g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
            b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
            px[x, y] = (r, g, b)
    return img


def draw_glyph(img: Image.Image) -> Image.Image:
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy = SIZE // 2, SIZE // 2

    # 外圈光环
    for i, alpha in ((300, 26), (250, 34), (200, 46)):
        d.ellipse([cx - i, cy - i, cx + i, cy + i], outline=(91, 157, 255, alpha), width=14)

    # 声波柱（左右各三根，模拟听力）
    bars = [(-210, 90), (-140, 170), (-70, 260), (0, 330), (70, 260), (140, 170), (210, 90)]
    for dx, h in bars:
        color = ACCENT if abs(dx) < 100 else ACCENT2
        d.rounded_rectangle([cx + dx - 22, cy - h // 2, cx + dx + 22, cy + h // 2],
                            radius=22, fill=color + (235,))

    # 中心圆点
    d.ellipse([cx - 34, cy - 34, cx + 34, cy + 34], fill=(255, 255, 255, 245))
    return img


def rounded(img: Image.Image, radius: int = 190) -> Image.Image:
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE, SIZE], radius=radius, fill=255)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), (0, 0), mask)
    return out


def main() -> None:
    master = rounded(draw_glyph(radial_base()))
    master.save(ICONS / "icon-1024.png")
    for size in (512, 384, 192, 180, 167, 152, 120):
        master.resize((size, size), Image.LANCZOS).save(ICONS / f"icon-{size}.png")
    master.resize((180, 180), Image.LANCZOS).save(OUT / "apple-touch-icon.png")
    # maskable（安卓/部分浏览器）
    maskable = Image.new("RGBA", (SIZE, SIZE), BG_TOP + (255,))
    glyph = draw_glyph(radial_base()).convert("RGBA").resize((int(SIZE * 0.78), int(SIZE * 0.78)))
    maskable.paste(glyph, ((SIZE - glyph.width) // 2, (SIZE - glyph.height) // 2), glyph)
    maskable.resize((512, 512), Image.LANCZOS).save(ICONS / "icon-maskable-512.png")
    print("icons ->", ICONS)


if __name__ == "__main__":
    main()
