from __future__ import annotations

import argparse
import base64
import difflib
import json
import statistics
import time
import urllib.request
from pathlib import Path


def _normalized(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _post(*, base_url: str, key: str, path: Path) -> tuple[dict, float]:
    payload = json.dumps(
        {
            "filename": path.name,
            "media_type": "image/png",
            "file_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/ocr",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json", "X-Api-Key": key},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=360) as response:
        value = json.load(response)
    return value, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the isolated conventional OCR service.")
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8030")
    parser.add_argument("--api-key-path", default="/run/secrets/paddleocr_api_key")
    parser.add_argument("--output")
    parser.add_argument("--min-similarity", type=float, default=0.90)
    args = parser.parse_args()
    root = Path(args.fixtures).resolve()
    key = Path(args.api_key_path).read_text(encoding="utf-8").strip()
    rows = []
    for image_path in sorted(root.glob("*.png")):
        expected = (image_path.with_suffix(".txt")).read_text(encoding="utf-8")
        response, seconds = _post(base_url=args.base_url, key=key, path=image_path)
        pages = response.get("pages") if isinstance(response.get("pages"), list) else []
        observed = "\n".join(
            str(text)
            for page in pages if isinstance(page, dict)
            for text in (page.get("rec_texts") or [])
        )
        similarity = difflib.SequenceMatcher(None, _normalized(expected), _normalized(observed)).ratio()
        canaries = {
            "date": "08/25/2026" in observed,
            "account": "987654321" in observed,
            "total": "$123.45" in observed or "123.45" in observed,
        }
        rows.append(
            {
                "fixture": image_path.name,
                "seconds": round(seconds, 4),
                "similarity": round(similarity, 4),
                "canaries": canaries,
                "passed": similarity >= args.min_similarity and all(canaries.values()),
            }
        )
    report = {
        "schema_version": 1,
        "fixture_count": len(rows),
        "mean_seconds": round(statistics.mean(row["seconds"] for row in rows), 4) if rows else None,
        "all_passed": bool(rows) and all(row["passed"] for row in rows),
        "results": rows,
    }
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
