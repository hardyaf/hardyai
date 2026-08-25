from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


TIERS = ("tiny", "small", "medium")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seal_model_tree(model_dir: Path) -> None:
    paths = sorted(model_dir.rglob("*"), key=lambda path: len(path.parts), reverse=True)
    for path in paths:
        if path.is_symlink():
            continue
        if path.is_file():
            os.chmod(path, 0o444)
        elif path.is_dir():
            os.chmod(path, 0o555)
    os.chmod(model_dir, 0o555)


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision and hash fixed PP-OCRv6 model weights.")
    parser.add_argument("--model-root", default="/models")
    parser.add_argument("--tiers", nargs="+", choices=TIERS, default=list(TIERS))
    args = parser.parse_args()
    root = Path(args.model_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(root)

    from paddleocr import PaddleOCR
    import paddleocr

    for tier in args.tiers:
        PaddleOCR(
            text_detection_model_name=f"PP-OCRv6_{tier}_det",
            text_recognition_model_name=f"PP-OCRv6_{tier}_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
            cpu_threads=1,
        )

    official = root / "official_models"
    required = [
        official / f"PP-OCRv6_{tier}_{kind}"
        for tier in args.tiers
        for kind in ("det", "rec")
    ]
    missing = [str(path) for path in required if not path.is_dir()]
    if missing:
        raise RuntimeError("Provisioning did not produce expected model directories: " + ", ".join(missing))
    files = []
    for model_dir in required:
        for path in sorted(model_dir.rglob("*")):
            if path.is_file() and not path.is_symlink():
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "size_bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                )
        _seal_model_tree(model_dir)
    manifest = {
        "schema_version": 1,
        "provider": "PaddlePaddle/PaddleOCR",
        "paddleocr_version": str(getattr(paddleocr, "__version__", "3.7.0")),
        "model_family": "PP-OCRv6",
        "tiers": list(args.tiers),
        "file_count": len(files),
        "files": files,
    }
    target = root / "model-manifest.json"
    with NamedTemporaryFile("w", encoding="utf-8", dir=root, prefix=".manifest-", delete=False) as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, target)
    os.chmod(target, 0o444)
    print(json.dumps({"status": "ok", "manifest": str(target), "file_count": len(files)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
