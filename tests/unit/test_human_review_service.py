from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService


HASH = "a" * 64


def _review(service: HumanReviewService, **overrides):
    values = {
        "review_kind": "field_correction",
        "subject_type": "synthetic_domain_record",
        "subject_id": "subject-1",
        "subject_version": "version-1",
        "item_hash": HASH,
        "sensitivity": "private",
        "validator_summary": [{"code": "low_confidence", "passed": False}],
        "evidence_refs": ["opaque:evidence:1"],
    }
    values.update(overrides)
    return service.create_review(**values)


def test_review_is_generic_hash_bound_and_decision_is_idempotent(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    review = _review(service)
    assert review["subject_type"] == "synthetic_domain_record"
    assert review["state"] == "pending"

    with pytest.raises(ValueError, match="review_version_changed"):
        service.decide(
            review_id=review["review_id"],
            bound_item_hash="b" * 64,
            decision="approve",
            actor_principal="operator:1",
            reason="Verified against the source.",
            idempotency_key="decision-wrong",
        )
    decision = service.decide(
        review_id=review["review_id"],
        bound_item_hash=HASH,
        decision="approve",
        actor_principal="operator:1",
        reason="Verified against the source.",
        idempotency_key="decision-1",
    )
    repeated = service.decide(
        review_id=review["review_id"],
        bound_item_hash=HASH,
        decision="approve",
        actor_principal="operator:1",
        reason="Repeated request.",
        idempotency_key="decision-1",
    )
    assert decision["decision_id"] == repeated["decision_id"]
    assert repository.get(review["review_id"])["state"] == "approved"
    assert repository.mark_applied(decision_id=decision["decision_id"], action_receipt_ref="receipt:1")
    assert repository.get(review["review_id"])["state"] == "executed"
    repository.close()


def test_review_expiry_and_supersession_are_explicit(tmp_path) -> None:
    repository = HumanReviewRepository(str(tmp_path / "core.db"))
    service = HumanReviewService(repository)
    expired = _review(
        service,
        subject_id="expired",
        expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    assert repository.get(expired["review_id"])["state"] == "expired"

    original = _review(service, subject_id="replace", subject_version="v1")
    replacement = _review(service, subject_id="replace", subject_version="v2", item_hash="c" * 64)
    assert repository.supersede(
        review_id=original["review_id"],
        replacement_review_id=replacement["review_id"],
    )
    persisted = repository.get(original["review_id"])
    assert persisted["state"] == "superseded"
    assert persisted["superseded_by_review_id"] == replacement["review_id"]
    repository.close()
