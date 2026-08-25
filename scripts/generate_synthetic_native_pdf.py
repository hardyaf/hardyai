#!/usr/bin/env python3
"""Generate deterministic, non-sensitive born-digital PDF fixtures without third-party packages."""

from __future__ import annotations

import argparse
from pathlib import Path


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(
    lines: list[tuple[int, int, int, str]],
    rules: list[tuple[int, int, int, int]] | None = None,
) -> bytes:
    commands = ["BT", "/F1 12 Tf"]
    for x, y, size, value in lines:
        commands.extend([f"/F1 {size} Tf", f"1 0 0 1 {x} {y} Tm", f"({_escape(value)}) Tj"])
    commands.append("ET")
    for x1, y1, x2, y2 in rules or []:
        commands.append(f"{x1} {y1} m {x2} {y2} l S")
    stream = ("\n".join(commands) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
    ]
    payload = bytearray(b"%PDF-1.4\n%HardyAI synthetic native fixture\n")
    offsets = [0]
    for number, value in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii") + value + b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    args.output_directory.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "native-prose.pdf": (
            [(72, 730, 18, "HardyAI Native Prose"), (72, 690, 12, "Synthetic offline document parsing evidence."), (72, 670, 12, "Reference: NATIVE-PROSE-20260825")],
            [],
        ),
        "native-columns.pdf": (
            [(54, 730, 18, "HardyAI Two Column Fixture"), (54, 690, 12, "Left column first paragraph."), (54, 670, 12, "Left column second paragraph."), (330, 690, 12, "Right column first paragraph."), (330, 670, 12, "Right column second paragraph.")],
            [],
        ),
        "native-table.pdf": (
            [(72, 730, 18, "HardyAI Table Fixture"), (90, 680, 12, "Item"), (310, 680, 12, "Amount"), (90, 640, 12, "Synthetic service"), (310, 640, 12, "$42.17")],
            [(72, 700, 450, 700), (72, 660, 450, 660), (72, 620, 450, 620), (72, 620, 72, 700), (280, 620, 280, 700), (450, 620, 450, 700)],
        ),
        "native-near-empty.pdf": (
            [(72, 730, 12, "tiny")],
            [],
        ),
    }
    for name, (lines, rules) in fixtures.items():
        (args.output_directory / name).write_bytes(_pdf(lines, rules))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
