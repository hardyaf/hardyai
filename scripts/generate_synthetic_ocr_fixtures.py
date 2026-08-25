#!/usr/bin/env python3
"""Generate non-sensitive PDF/JPEG/PNG fixtures for an installed OCR smoke test."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--marker", default="baseline")
    args = parser.parse_args()

    destination = args.output_directory
    destination.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1800, 1100), "white")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        title_font = ImageFont.truetype(font_path, 72)
        body_font = ImageFont.truetype(font_path, 52)
    except OSError:
        title_font = ImageFont.load_default()
        body_font = title_font

    draw.text((100, 100), "HARDYAI OCR CANARY 20260825", fill="black", font=title_font)
    draw.text((100, 300), "Synthetic Utility Statement", fill="black", font=body_font)
    draw.text((100, 430), "Account: TEST-000042", fill="black", font=body_font)
    draw.text((100, 560), "Statement Date: August 25, 2026", fill="black", font=body_font)
    draw.text((100, 690), "Total Due: $42.17", fill="black", font=body_font)
    draw.text((100, 820), "No real personal information", fill="black", font=body_font)
    draw.text((100, 950), f"Fixture marker: {args.marker[:40]}", fill="black", font=body_font)

    image.save(destination / "hardyai-canary.png", format="PNG")
    image.save(destination / "hardyai-canary.jpg", format="JPEG", quality=95)
    image.save(destination / "hardyai-canary.pdf", format="PDF", resolution=150.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
