from __future__ import annotations

import hashlib

import pytest

from benchmarks.documents.run_benchmark import evaluate


def _manifest(tmp_path):
    source = tmp_path / "native.pdf"
    source.write_bytes(b"%PDF-1.4\nsynthetic\n%%EOF\n")
    return {
        "schema_version": "1",
        "corpus_id": "synthetic-native-v1",
        "split": "synthetic",
        "cases": [
            {
                "fixture_id": "native-1",
                "relative_path": "native.pdf",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "document_class": "born_digital_pdf",
                "format": "pdf",
                "expected_route": "native_docling",
                "expected_pages": 1,
                "minimum_blocks": 2,
                "requires_table": True,
            }
        ],
    }


def test_benchmark_evaluates_only_structural_content_free_metrics(tmp_path) -> None:
    metrics = evaluate(
        _manifest(tmp_path),
        {
            "cases": [
                {
                    "fixture_id": "native-1",
                    "status": "complete",
                    "route": "native_docling",
                    "page_count": 1,
                    "block_count": 4,
                    "table_count": 1,
                    "reading_order_complete": True,
                    "evidence_coverage": 1.0,
                }
            ]
        },
        tmp_path.resolve(),
    )
    assert metrics["passed"] == 1 and metrics["failed"] == 0


def test_benchmark_rejects_content_and_hash_mismatch(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    with pytest.raises(ValueError, match="content-bearing"):
        evaluate(manifest, {"cases": [{"fixture_id": "native-1", "text": "leak"}]}, tmp_path.resolve())
    manifest["cases"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        evaluate(manifest, {"cases": []}, tmp_path.resolve())
