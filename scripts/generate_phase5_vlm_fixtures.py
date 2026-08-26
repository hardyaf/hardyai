from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


TEXT = "UPLIFT SUPPLY\nINVOICE 10482\nDATE 08/25/2026\nACCOUNT 987654321\nTOTAL $123.45"


def _font(size: int, *, italic: bool = False):
    filenames = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"
        if italic
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for filename in filenames:
        if Path(filename).is_file():
            return ImageFont.truetype(filename, size=size)
    return ImageFont.load_default(size=size)


def _base(*, font_size: int = 64, italic: bool = False) -> Image.Image:
    image = Image.new("RGB", (1400, 900), (235, 232, 218))
    draw = ImageDraw.Draw(image)
    draw.multiline_text(
        (110, 100),
        TEXT,
        fill=(38, 42, 48),
        font=_font(font_size, italic=italic),
        spacing=34,
    )
    return image


def _round_trip_jpeg(image: Image.Image, *, quality: int) -> Image.Image:
    stream = BytesIO()
    image.save(stream, format="JPEG", quality=quality, optimize=False)
    stream.seek(0)
    with Image.open(stream) as reopened:
        return reopened.convert("RGB")


def _low_resolution(image: Image.Image, width: int) -> Image.Image:
    height = max(1, round(image.height * width / image.width))
    reduced = image.resize((width, height), Image.Resampling.BILINEAR)
    return reduced.resize(image.size, Image.Resampling.BILINEAR)


def _fold_occlusion(image: Image.Image, height: int) -> Image.Image:
    value = image.copy()
    draw = ImageDraw.Draw(value)
    for top in (100, 198, 296, 394, 492):
        middle = top + 34
        draw.rectangle((95, middle, 900, middle + height), fill=(235, 232, 218))
    return _round_trip_jpeg(value, quality=24)


def _character_occlusion(image: Image.Image) -> Image.Image:
    value = image.copy()
    draw = ImageDraw.Draw(value)
    for left, top in ((265, 100), (385, 198), (335, 296), (455, 394), (325, 492)):
        draw.rectangle((left, top + 8, left + 19, top + 61), fill=(235, 232, 218))
    return _round_trip_jpeg(value, quality=24)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic difficult OCR/VLM holdouts.")
    parser.add_argument("--output", default="/tmp/hardyai-phase5-vlm-fixtures")
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)

    base = _base()
    variants = {
        "receipt-lowres-700": _low_resolution(base, 700),
        "receipt-lowres-500": _low_resolution(base, 500).filter(ImageFilter.GaussianBlur(0.7)),
        "receipt-lowres-350": _low_resolution(base, 350).filter(ImageFilter.GaussianBlur(1.0)),
        "receipt-fold-8": _fold_occlusion(base, 8),
        "receipt-fold-14": _fold_occlusion(base, 14),
        "receipt-character-occlusion": _character_occlusion(base),
        "receipt-faint-compressed": _round_trip_jpeg(
            ImageEnhance.Contrast(ImageEnhance.Brightness(base).enhance(0.70)).enhance(0.38),
            quality=18,
        ),
        "receipt-skew-compressed": _round_trip_jpeg(
            _base(font_size=52, italic=True).rotate(
                8.0,
                resample=Image.Resampling.BICUBIC,
                expand=False,
                fillcolor=(235, 232, 218),
            ),
            quality=22,
        ),
        **{
            f"receipt-skew-{angle}": _round_trip_jpeg(
                _base(font_size=52, italic=True).rotate(
                    float(angle),
                    resample=Image.Resampling.BICUBIC,
                    expand=False,
                    fillcolor=(235, 232, 218),
                ),
                quality=20,
            )
            for angle in (10, 12, 15)
        },
        **{
            f"receipt-skew-small-{angle}": _round_trip_jpeg(
                _base(font_size=42, italic=True).rotate(
                    float(angle),
                    resample=Image.Resampling.BICUBIC,
                    expand=False,
                    fillcolor=(235, 232, 218),
                ),
                quality=14,
            )
            for angle in (8, 10, 12)
        },
        "receipt-perspective": base.transform(
            base.size,
            Image.Transform.QUAD,
            (90, 40, 1320, 125, 1375, 845, 20, 770),
            resample=Image.Resampling.BICUBIC,
            fillcolor=(235, 232, 218),
        ),
    }
    for name, image in variants.items():
        image.save(root / f"{name}.png")
        (root / f"{name}.txt").write_text(TEXT + "\n", encoding="utf-8")
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
