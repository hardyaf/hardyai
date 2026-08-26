from __future__ import annotations

import argparse
import base64
import difflib
import json
import statistics
import time
import urllib.request
import urllib.error
from pathlib import Path


def _normalized(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _request(
    *,
    url: str,
    key_header: str,
    key: str,
    path: Path,
    extra_headers: dict[str, str] | None = None,
) -> tuple[dict, float]:
    media_type = "image/jpeg" if path.suffix.casefold() in {".jpg", ".jpeg"} else "image/png"
    payload = json.dumps(
        {
            "filename": path.name,
            "media_type": media_type,
            "file_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("ascii")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        key_header: key,
    }
    headers.update(extra_headers or {})
    request = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=360) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("benchmark_response_invalid")
    return value, time.perf_counter() - started


def _conventional_text(value: dict) -> str:
    pages = value.get("pages") if isinstance(value.get("pages"), list) else []
    return "\n".join(
        str(text)
        for page in pages
        if isinstance(page, dict)
        for text in (page.get("rec_texts") or [])
    )


def _vlm_text(value: dict) -> str:
    pages = value.get("pages") if isinstance(value.get("pages"), list) else []
    return "\n".join(
        str(block.get("text") or "")
        for page in pages
        if isinstance(page, dict)
        for block in (page.get("blocks") or [])
        if isinstance(block, dict)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare conventional OCR with full PaddleOCR-VL.")
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--conventional-url", default="http://paddleocr-serve:8030/ocr")
    parser.add_argument("--conventional-key-path", required=True)
    parser.add_argument("--vlm-url", default="http://paddleocr-vl-serve:8050/parse-image")
    parser.add_argument("--vlm-key-path", required=True)
    parser.add_argument(
        "--vlm-lane",
        default="",
        help="Set document_vlm when benchmarking through the accelerator admission proxy.",
    )
    parser.add_argument("--output")
    parser.add_argument("--include-observed-text", action="store_true")
    parser.add_argument("--minimum-improvement", type=float, default=0.02)
    args = parser.parse_args()
    root = Path(args.fixtures).resolve()
    conventional_key = Path(args.conventional_key_path).read_text(encoding="utf-8").strip()
    vlm_key = Path(args.vlm_key_path).read_text(encoding="utf-8").strip()
    rows = []
    images = sorted((*root.glob("*.png"), *root.glob("*.jpg"), *root.glob("*.jpeg")))
    for image_path in images:
        expected = image_path.with_suffix(".txt").read_text(encoding="utf-8")
        conventional, conventional_seconds = _request(
            url=args.conventional_url,
            key_header="X-Api-Key",
            key=conventional_key,
            path=image_path,
        )
        conventional_text = _conventional_text(conventional)
        conventional_similarity = difflib.SequenceMatcher(
            None,
            _normalized(expected),
            _normalized(conventional_text),
        ).ratio()
        try:
            vlm, vlm_seconds = _request(
                url=args.vlm_url,
                key_header="X-HardyAI-Accelerator-Key",
                key=vlm_key,
                path=image_path,
                extra_headers=(
                    {"X-HardyAI-Accelerator-Lane": args.vlm_lane}
                    if args.vlm_lane
                    else None
                ),
            )
            vlm_text = _vlm_text(vlm)
            vlm_similarity = difflib.SequenceMatcher(
                None,
                _normalized(expected),
                _normalized(vlm_text),
            ).ratio()
        except urllib.error.HTTPError as exc:
            error_row = {
                    "fixture": image_path.name,
                    "conventional_seconds": round(conventional_seconds, 4),
                    "conventional_similarity": round(conventional_similarity, 4),
                    "vlm_error": f"vlm_http_{int(exc.code)}",
            }
            if args.include_observed_text:
                error_row["conventional_text"] = conventional_text
            rows.append(error_row)
            continue
        row = {
                "fixture": image_path.name,
                "conventional_seconds": round(conventional_seconds, 4),
                "conventional_similarity": round(conventional_similarity, 4),
                "vlm_seconds": round(vlm_seconds, 4),
                "vlm_similarity": round(vlm_similarity, 4),
                "improvement": round(vlm_similarity - conventional_similarity, 4),
        }
        if args.include_observed_text:
            row["conventional_text"] = conventional_text
            row["vlm_text"] = vlm_text
        rows.append(row)
    successful = [row for row in rows if "vlm_similarity" in row]
    best_improvement = max((row["improvement"] for row in successful), default=-1.0)
    report = {
        "schema_version": 1,
        "fixture_count": len(rows),
        "best_improvement": best_improvement,
        "mean_conventional_similarity": (
            round(statistics.mean(row["conventional_similarity"] for row in rows), 4)
            if rows
            else None
        ),
        "mean_vlm_similarity": (
            round(statistics.mean(row["vlm_similarity"] for row in successful), 4)
            if successful
            else None
        ),
        "accepted": bool(rows) and best_improvement >= args.minimum_improvement,
        "results": rows,
    }
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
