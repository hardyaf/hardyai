from __future__ import annotations

import pytest

from app.tickets.async_receipts import (
    AsyncChildOutcomeSink,
    TicketEffectManifestReservation,
    build_execution_manifest,
    reduce_ticket_effects,
)
from app.tickets.repository import TicketRepository, content_hash


def _ticket(repository: TicketRepository, request_id: str = "request-1") -> dict:
    return repository.create_ticket(
        origin_request_id=request_id,
        session_id="session-1",
        user_id="user-1",
        agent_id="jarvis",
        source="test",
        intent="lists.add_item",
        skill_id="skill.lists.core",
        route="main_tool_loop",
        title="bounded test ticket",
    )


def _manifest(*, parent: str = "parent-1", states: int = 2, recovery: str | None = None):
    children = [
        {
            "child_operation_id": f"child-{index}",
            "child_index": index,
            "target_hash": content_hash({"target": index}),
            "arguments_hash": content_hash({"arguments": index}),
        }
        for index in range(states)
    ]
    return build_execution_manifest(
        parent_operation_id=parent,
        authorization_binding_hash=content_hash("authorization"),
        effect_cardinality="independent_batch" if states > 1 else "single",
        skill_id="skill.lists.core",
        tool_id="lists.add_item",
        contract_version=1,
        descriptor_hash=content_hash("descriptor"),
        resource_version=1,
        parent_arguments_hash=content_hash("arguments"),
        children=children,
        recovery_manifest_hash=recovery,
        sensitivity="normal",
        persistence="standard",
    )


def _receipt(child_id: str, *, expected: str = "effect") -> dict:
    return {
        "operation_id": child_id,
        "idempotency_key": f"ticket-effect-receipt:v1:{child_id}",
        "capability": "lists.add_item",
        "action": "add_item",
        "resource_key": f"list:user-1:{child_id}",
        "status": "committed",
        "committed_at": "2026-09-06T00:00:00+00:00",
        "expected_effect": {"effect_hash": content_hash(expected)},
        "validator_name": "lists.sqlite",
        "validator_version": "1",
        "resource_locator": {"resource_hash": content_hash(child_id)},
        "execution_observation": {},
        "result": {},
    }


def _reserved(repository: TicketRepository, *, count: int = 2):
    ticket = _ticket(repository)
    manifest = _manifest(states=count)
    reservation = TicketEffectManifestReservation(repository).reserve(
        ticket_id=str(ticket["ticket_id"]),
        request_id=str(ticket["origin_request_id"]),
        manifest=manifest,
    )
    return ticket, manifest, str(reservation["manifest_hash"])


def test_manifest_is_immutable_and_exact_idempotent(tmp_path):
    repository = TicketRepository(str(tmp_path / "manifest.db"))
    try:
        ticket = _ticket(repository)
        manifest = _manifest()
        reservation = TicketEffectManifestReservation(repository)
        first = reservation.reserve(
            ticket_id=str(ticket["ticket_id"]),
            request_id="request-1",
            manifest=manifest,
        )
        replay = reservation.reserve(
            ticket_id=str(ticket["ticket_id"]),
            request_id="request-1",
            manifest=manifest,
        )
        assert first["entry"]["entry_id"] == replay["entry"]["entry_id"]
        changed = dict(manifest)
        changed["descriptor_hash"] = content_hash("changed")
        with pytest.raises(ValueError, match="tool_execution_manifest_conflict"):
            reservation.reserve(
                ticket_id=str(ticket["ticket_id"]),
                request_id="request-1",
                manifest=changed,
            )
        assert len(repository.list_execution_manifests(str(ticket["ticket_id"]))) == 1
    finally:
        repository.close()


def test_shared_sqlite_manifest_and_private_domain_row_commit_or_rollback_together(tmp_path):
    repository = TicketRepository(str(tmp_path / "atomic.db"))
    try:
        repository._conn.execute(
            "CREATE TABLE synthetic_domain_operations (operation_id TEXT PRIMARY KEY, recovery_hash TEXT NOT NULL)"
        )
        repository._conn.commit()
        ticket = _ticket(repository)
        recovery_hash = content_hash("private-domain-values")
        manifest = _manifest(states=1, recovery=recovery_hash)

        def reserve_domain(cursor, projection, manifest_hash, expected_recovery_hash):
            assert set(projection) == {
                "ticket_id",
                "origin_request_id",
                "user_id",
                "agent_id",
                "source",
                "intent",
                "skill_id",
                "route",
            }
            assert manifest_hash == content_hash(manifest)
            row = cursor.execute(
                "SELECT recovery_hash FROM synthetic_domain_operations WHERE operation_id = ?",
                ("parent-1",),
            ).fetchone()
            if row is None:
                cursor.execute(
                    "INSERT INTO synthetic_domain_operations VALUES (?, ?)",
                    ("parent-1", expected_recovery_hash),
                )
                status = "created"
            else:
                assert row["recovery_hash"] == expected_recovery_hash
                status = "existing"
            return {"status": status, "recovery_manifest_hash": expected_recovery_hash}

        reservation = TicketEffectManifestReservation(repository)
        created = reservation.reserve(
            ticket_id=str(ticket["ticket_id"]),
            request_id="request-1",
            manifest=manifest,
            domain_reservation=reserve_domain,
        )
        assert created["domain_reservation"]["status"] == "created"
        replay = reservation.reserve(
            ticket_id=str(ticket["ticket_id"]),
            request_id="request-1",
            manifest=manifest,
            domain_reservation=reserve_domain,
        )
        assert replay["domain_reservation"]["status"] == "existing"

        second_ticket = _ticket(repository, "request-2")
        second_manifest = _manifest(parent="parent-2", states=1, recovery=recovery_hash)

        def fail_after_private_insert(cursor, projection, manifest_hash, expected_recovery_hash):
            del projection, manifest_hash
            cursor.execute(
                "INSERT INTO synthetic_domain_operations VALUES (?, ?)",
                ("parent-2", expected_recovery_hash),
            )
            raise RuntimeError("synthetic_crash")

        with pytest.raises(RuntimeError, match="synthetic_crash"):
            reservation.reserve(
                ticket_id=str(second_ticket["ticket_id"]),
                request_id="request-2",
                manifest=second_manifest,
                domain_reservation=fail_after_private_insert,
            )
        assert repository.get_entry_by_dedupe("tool-execution-manifest:v1:parent-2") is None
        assert repository._conn.execute(
            "SELECT 1 FROM synthetic_domain_operations WHERE operation_id = 'parent-2'"
        ).fetchone() is None
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("states", "expected_status", "expected_reason"),
    [
        (["verified", "verified"], "completed", "all_children_verified"),
        (["cancelled", "cancelled"], "failed", "all_children_cancelled"),
        (["denied", "denied"], "failed", "no_effect_terminal"),
        (["verified", "denied"], "partial", "partial_effect_terminal"),
        (["verified", "dead_letter"], "partial", "partial_effect_terminal"),
        (["cancelled", "denied"], "failed", "no_effect_terminal"),
    ],
)
def test_closed_child_outcome_reducer(states, expected_status, expected_reason, tmp_path):
    repository = TicketRepository(str(tmp_path / f"reducer-{expected_reason}-{states[0]}.db"))
    try:
        ticket, _, manifest_hash = _reserved(repository)
        sink = AsyncChildOutcomeSink(repository=repository)
        aggregate = None
        for index, state in enumerate(states):
            outcome = sink.record_terminal_child(
                parent_operation_id="parent-1",
                parent_manifest_hash=manifest_hash,
                child_operation_id=f"child-{index}",
                effect_state=state,
                receipt=_receipt(f"child-{index}") if state == "verified" else None,
                reason_code=None if state == "verified" else f"test_{state}",
            )
            aggregate = outcome["aggregate"]
        assert aggregate["status"] == expected_status
        assert aggregate["reason"] == expected_reason
        assert aggregate["expected_count"] == 2
        assert aggregate["terminal_count"] == 2
        assert len(repository.list_receipts(str(ticket["ticket_id"]))) == states.count("verified")
    finally:
        repository.close()


def test_missing_child_is_queued_for_reconciliation(tmp_path):
    repository = TicketRepository(str(tmp_path / "missing.db"))
    try:
        ticket, _, manifest_hash = _reserved(repository)
        AsyncChildOutcomeSink(repository=repository).record_terminal_child(
            parent_operation_id="parent-1",
            parent_manifest_hash=manifest_hash,
            child_operation_id="child-0",
            effect_state="verified",
            receipt=_receipt("child-0"),
        )
        aggregate = reduce_ticket_effects(repository, str(ticket["ticket_id"]))
        assert aggregate["status"] == "queued"
        assert aggregate["ticket_status"] == "reconciliation_required"
        assert aggregate["missing_child_operation_ids"] == ["child-1"]
    finally:
        repository.close()


def test_child_outcome_replay_is_exact_and_no_effect_forbids_receipt(tmp_path):
    repository = TicketRepository(str(tmp_path / "conflict.db"))
    try:
        _, _, manifest_hash = _reserved(repository)
        sink = AsyncChildOutcomeSink(repository=repository)
        first = sink.record_terminal_child(
            parent_operation_id="parent-1",
            parent_manifest_hash=manifest_hash,
            child_operation_id="child-0",
            effect_state="verified",
            receipt=_receipt("child-0"),
        )
        replay = sink.record_terminal_child(
            parent_operation_id="parent-1",
            parent_manifest_hash=manifest_hash,
            child_operation_id="child-0",
            effect_state="verified",
            receipt=_receipt("child-0"),
        )
        assert first["entry"]["entry_id"] == replay["entry"]["entry_id"]
        with pytest.raises(ValueError, match="operation_receipt_idempotency_conflict"):
            sink.record_terminal_child(
                parent_operation_id="parent-1",
                parent_manifest_hash=manifest_hash,
                child_operation_id="child-0",
                effect_state="verified",
                receipt=_receipt("child-0", expected="changed"),
            )
        with pytest.raises(ValueError, match="ticket_no_effect_child_receipt_forbidden"):
            sink.record_terminal_child(
                parent_operation_id="parent-1",
                parent_manifest_hash=manifest_hash,
                child_operation_id="child-1",
                effect_state="cancelled",
                receipt=_receipt("child-1"),
            )
        with pytest.raises(ValueError, match="ticket_verified_child_receipt_required"):
            sink.record_terminal_child(
                parent_operation_id="parent-1",
                parent_manifest_hash=manifest_hash,
                child_operation_id="child-1",
                effect_state="verified",
            )
    finally:
        repository.close()


def test_sensitive_control_fields_and_no_store_manifests_are_rejected(tmp_path):
    repository = TicketRepository(str(tmp_path / "sensitivity.db"))
    try:
        ticket = _ticket(repository)
        manifest = _manifest(states=1)
        forbidden = dict(manifest)
        forbidden["persistence"] = "no_store"
        with pytest.raises(ValueError, match="tool_execution_manifest_no_store_forbidden"):
            TicketEffectManifestReservation(repository).reserve(
                ticket_id=str(ticket["ticket_id"]),
                request_id="request-1",
                manifest=forbidden,
            )

        redacted = dict(manifest)
        redacted["persistence"] = "redacted"
        reservation = TicketEffectManifestReservation(repository).reserve(
            ticket_id=str(ticket["ticket_id"]),
            request_id="request-1",
            manifest=redacted,
        )
        with pytest.raises(ValueError, match="ticket_redacted_receipt_content_forbidden"):
            AsyncChildOutcomeSink(repository=repository).record_terminal_child(
                parent_operation_id="parent-1",
                parent_manifest_hash=str(reservation["manifest_hash"]),
                child_operation_id="child-0",
                effect_state="verified",
                receipt={**_receipt("child-0"), "result": {"subject": "private"}},
            )
    finally:
        repository.close()
