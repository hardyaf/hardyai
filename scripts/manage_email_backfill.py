from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings  # noqa: E402
from app.jobs.repository import DurableJobRepository  # noqa: E402


JOB_TYPE = "email.projection_backfill.v1"
AGGREGATE_PREFIX = "email-central-projection"
MAX_PAGE_SIZE = 100
MAX_PAGES = 500
MAX_MESSAGES = 50_000
RESULT_FIELDS = (
    "candidate_count",
    "accepted_count",
    "ignored_count",
    "failed_count",
    "summary_count",
    "classification_count",
)


def _backfill_id() -> str:
    return "backfill_v1_" + uuid4().hex


def _page_key(*, backfill_id: str, page_index: int, token: str | None) -> str:
    material = f"{backfill_id}\n{int(page_index)}\n{str(token or 'first')}"
    return f"{JOB_TYPE}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _bounded_root(*, page_size: int, max_pages: int, max_messages: int) -> tuple[int, int, int]:
    size = int(page_size)
    pages = int(max_pages)
    messages = int(max_messages)
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    if not 1 <= pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")
    if not 1 <= messages <= MAX_MESSAGES:
        raise ValueError(f"max_messages must be between 1 and {MAX_MESSAGES}")
    return size, pages, messages


def _enqueue(
    repository: DurableJobRepository,
    *,
    backfill_id: str,
    page_index: int,
    page_token: str | None,
    page_size: int,
    max_pages: int,
    max_messages: int,
    processed_messages: int,
) -> dict[str, Any]:
    size, pages, messages = _bounded_root(
        page_size=page_size,
        max_pages=max_pages,
        max_messages=max_messages,
    )
    index = int(page_index)
    processed = int(processed_messages)
    if index < 0 or index >= pages or processed < 0 or processed >= messages:
        raise ValueError("backfill continuation exceeds its root budget")
    return repository.enqueue_job(
        job_type=JOB_TYPE,
        aggregate_id=f"{AGGREGATE_PREFIX}:{backfill_id}",
        idempotency_key=_page_key(
            backfill_id=backfill_id,
            page_index=index,
            token=page_token,
        ),
        payload={
            "version": 1,
            "backfill_id": backfill_id,
            "page_index": index,
            "page_token": page_token,
            "page_size": size,
            "max_pages": pages,
            "max_messages": messages,
            "processed_messages": processed,
        },
        max_attempts=4,
        priority=50,
        resource_class="cpu_small",
    )


def enqueue_root(
    repository: DurableJobRepository,
    *,
    page_size: int,
    max_pages: int,
    max_messages: int,
) -> dict[str, Any]:
    root_id = _backfill_id()
    job = _enqueue(
        repository,
        backfill_id=root_id,
        page_index=0,
        page_token=None,
        page_size=page_size,
        max_pages=max_pages,
        max_messages=max_messages,
        processed_messages=0,
    )
    return {"status": "enqueued", "backfill_id": root_id, "job_id": job.get("job_id")}


def run_once(
    repository: DurableJobRepository,
    *,
    email_agent_service: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    worker_id = f"email-backfill-{uuid4()}"
    jobs = repository.claim_jobs(
        job_type=JOB_TYPE,
        worker_id=worker_id,
        limit=1,
        lease_seconds=300,
        now=now or datetime.now(UTC),
    )
    if not jobs:
        return {"status": "idle", "completed_pages": 0, "failures": 0}
    job = jobs[0]
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    token = int(job.get("lease_fencing_token") or 0)
    try:
        if int(payload.get("version") or 0) != 1:
            raise ValueError("email_backfill_payload_version_invalid")
        backfill_id = str(payload.get("backfill_id") or "").strip()
        if not backfill_id.startswith("backfill_v1_"):
            raise ValueError("email_backfill_id_invalid")
        page_size, max_pages, max_messages = _bounded_root(
            page_size=int(payload.get("page_size") or 0),
            max_pages=int(payload.get("max_pages") or 0),
            max_messages=int(payload.get("max_messages") or 0),
        )
        page_index = int(payload.get("page_index") or 0)
        processed_before = int(payload.get("processed_messages") or 0)
        if page_index < 0 or page_index >= max_pages or not 0 <= processed_before < max_messages:
            raise ValueError("email_backfill_budget_state_invalid")
        effective_page_size = min(page_size, max_messages - processed_before)
        result = email_agent_service.process_historical_backfill_page(
            page_token=str(payload.get("page_token") or "").strip() or None,
            page_size=effective_page_size,
        )
        page_result = {field: max(0, int(result.get(field) or 0)) for field in RESULT_FIELDS}
        if page_result["candidate_count"] > effective_page_size:
            raise ValueError("email_backfill_page_exceeded_requested_size")
        processed_after = processed_before + page_result["candidate_count"]
        next_token = str(result.get("next_page_token") or "").strip() or None
        continuation_allowed = bool(
            next_token
            and page_index + 1 < max_pages
            and processed_after < max_messages
        )
        continuation_job_id: str | None = None
        if continuation_allowed:
            continuation = _enqueue(
                repository,
                backfill_id=backfill_id,
                page_index=page_index + 1,
                page_token=next_token,
                page_size=page_size,
                max_pages=max_pages,
                max_messages=max_messages,
                processed_messages=processed_after,
            )
            continuation_job_id = str(continuation.get("job_id") or "") or None
        page_state = "continued" if continuation_allowed else "partial" if next_token else "completed"
        checkpoint = {
            **payload,
            "processed_after": processed_after,
            "page_result": page_result,
            "page_state": page_state,
            "provider_pagination_complete": next_token is None,
            "continuation_job_id": continuation_job_id,
        }
        if not repository.checkpoint_payload(
            job_id=str(job["job_id"]),
            worker_id=worker_id,
            fencing_token=token,
            payload=checkpoint,
        ):
            raise RuntimeError("email_backfill_checkpoint_lease_lost")
        if not repository.complete_job(
            job_id=str(job["job_id"]),
            worker_id=worker_id,
            fencing_token=token,
        ):
            raise RuntimeError("email_backfill_completion_lease_lost")
        return {
            "status": page_state,
            "backfill_id": backfill_id,
            "completed_pages": 1,
            "failures": 0,
            "continuation_enqueued": continuation_allowed,
        }
    except Exception as exc:
        retried = repository.retry_job(
            job_id=str(job["job_id"]),
            worker_id=worker_id,
            fencing_token=token,
            error_code=type(exc).__name__,
            delay_seconds=60,
        )
        if not retried:
            raise RuntimeError("email_backfill_retry_lease_lost") from exc
        return {
            "status": "retry",
            "completed_pages": 0,
            "failures": 1,
            "error_code": type(exc).__name__,
        }


def _jobs_for_backfill(
    repository: DurableJobRepository,
    *,
    backfill_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    rows = repository.list_jobs(job_type=JOB_TYPE, limit=max(1, min(int(limit), 1000)))
    if not backfill_id:
        return rows
    return [
        row
        for row in rows
        if isinstance(row.get("payload"), dict)
        and str(row["payload"].get("backfill_id") or "") == backfill_id
    ]


def backfill_status(
    repository: DurableJobRepository,
    *,
    backfill_id: str | None,
    limit: int,
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = _jobs_for_backfill(repository, backfill_id=backfill_id, limit=limit)
    counts: dict[str, int] = {}
    outcomes = {field: 0 for field in RESULT_FIELDS}
    page_states: set[str] = set()
    for row in rows:
        state = str(row.get("status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if state == "completed" and isinstance(payload.get("page_result"), dict):
            for field in RESULT_FIELDS:
                outcomes[field] += max(0, int(payload["page_result"].get(field) or 0))
        page_state = str(payload.get("page_state") or "")
        if page_state:
            page_states.add(page_state)
    return {
        "status": "ok",
        "backfill_id": backfill_id,
        "job_counts": counts,
        "outcomes": outcomes,
        "dead_letter_count": counts.get("dead_letter", 0),
        "provider_pagination_complete": "completed" in page_states,
        "budget_exhausted_partial": "partial" in page_states,
        "coverage": coverage or {
            "earliest_indexed_at": None,
            "latest_indexed_at": None,
            "message_count": 0,
        },
        "returned_jobs": len(rows),
    }


def cancel_backfill(
    repository: DurableJobRepository,
    *,
    backfill_id: str,
) -> dict[str, Any]:
    rows = _jobs_for_backfill(repository, backfill_id=backfill_id, limit=1000)
    cancelled = 0
    for row in rows:
        if str(row.get("status") or "") not in {"pending", "retry"}:
            continue
        updated = repository.request_cancel(job_id=str(row["job_id"]))
        if updated is not None and str(updated.get("status") or "") == "cancelled":
            cancelled += 1
    return {
        "status": "cancelled",
        "backfill_id": backfill_id,
        "cancelled_unclaimed_jobs": cancelled,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enqueue, inspect, or process bounded durable Email historical backfill pages."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    enqueue = subparsers.add_parser("enqueue")
    enqueue.add_argument("--page-size", type=int, default=50)
    enqueue.add_argument("--max-pages", type=int, required=True)
    enqueue.add_argument("--max-messages", type=int, required=True)
    subparsers.add_parser("run-once")
    status = subparsers.add_parser("status")
    status.add_argument("--backfill-id")
    status.add_argument("--limit", type=int, default=1000)
    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("--backfill-id", required=True)
    args = parser.parse_args()

    repository = DurableJobRepository(settings.database_path)
    try:
        if args.command == "enqueue":
            result = enqueue_root(
                repository,
                page_size=args.page_size,
                max_pages=args.max_pages,
                max_messages=args.max_messages,
            )
        elif args.command == "run-once":
            from app.runtime import email_agent_service

            if email_agent_service is None:
                raise RuntimeError("Email agent is not configured.")
            result = run_once(repository, email_agent_service=email_agent_service)
        elif args.command == "cancel":
            result = cancel_backfill(repository, backfill_id=args.backfill_id)
        else:
            from app.skills.domains.email_agent.config import EmailAgentPermissions
            from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage

            permissions = EmailAgentPermissions.load(settings.email_agent_permissions_path)
            storage = EmailAgentSQLiteStorage(settings.database_path)
            try:
                coverage = storage.projection_coverage(
                    allowed_source_keys=tuple(
                        route.route_key for route in permissions.source_routes
                    )
                )
            finally:
                storage.close()
            result = backfill_status(
                repository,
                backfill_id=args.backfill_id,
                limit=args.limit,
                coverage=coverage,
            )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 1 if result.get("status") == "retry" else 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
