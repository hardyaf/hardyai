from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


TEXT = "UPLIFT SUPPLY\nINVOICE 10482\nDATE 08/25/2026\nACCOUNT 987654321\nTOTAL $123.45"


def _font(size: int):
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default(size=size)


def _base() -> Image.Image:
    image = Image.new("RGB", (1400, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.multiline_text((100, 90), TEXT, fill="black", font=_font(72), spacing=36)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate non-sensitive deterministic OCR canaries.")
    parser.add_argument("--output", default="/tmp/hardyai-ocr-fixtures")
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    clean = _base()
    clean.save(root / "receipt-clean.png")
    clean.rotate(4.0, expand=False, fillcolor="white").save(root / "receipt-skew.png")
    dim = ImageEnhance.Contrast(ImageEnhance.Brightness(clean).enhance(0.55)).enhance(0.75)
    dim.save(root / "receipt-low-light.png")
    for name in ("receipt-clean", "receipt-skew", "receipt-low-light"):
        (root / f"{name}.txt").write_text(TEXT + "\n", encoding="utf-8")
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
