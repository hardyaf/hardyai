from __future__ import annotations

from scripts.check_public_tree import check_tree


def test_public_tree_does_not_mistake_numeric_digest_run_for_private_identifier(tmp_path):
    (tmp_path / "README.md").write_text(
        '{"source_hash":"715c9b37394e3d26d53be01122710187717092ca435235601bfc7b5290c6f379"}',
        encoding="utf-8",
    )

    assert check_tree(tmp_path) == []


def test_public_tree_still_rejects_non_placeholder_long_numeric_identifier(tmp_path):
    synthetic_identifier = "1234567890" + "12345678"
    (tmp_path / "README.md").write_text(
        f"channel identifier: {synthetic_identifier}",
        encoding="utf-8",
    )

    assert check_tree(tmp_path) == [
        "non-placeholder long numeric identifier: README.md"
    ]
