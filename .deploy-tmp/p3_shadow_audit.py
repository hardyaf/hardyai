from __future__ import annotations

import json
import sqlite3
import sys


start = sys.argv[1]
connection = sqlite3.connect("file:/opt/jarvis/data/jarvis_v2.db?mode=ro", uri=True)
connection.row_factory = sqlite3.Row
events = connection.execute(
    """
    SELECT event_type, timestamp, payload_json
    FROM events
    WHERE timestamp >= ? AND event_type LIKE 'main.action.%'
    ORDER BY id
    """,
    (start,),
).fetchall()
all_main_event_count, latest_main_event_at = connection.execute(
    "SELECT COUNT(*), MAX(timestamp) FROM events WHERE event_type LIKE 'main.action.%'"
).fetchone()

allowed_keys = {
    "main.action.commitment.shadow.evaluated": {"status", "reason_code"},
    "main.action.loop.shadow.evaluated": {
        "status",
        "stop_reason",
        "selected_skill_ids",
        "tool_ids",
        "operation_ids",
        "receipt_refs",
        "observation_count",
        "committed_effect_count",
        "would_call_count",
        "steps",
        "failures",
        "elapsed_ms",
    },
}
event_counts: dict[str, int] = {}
unexpected_keys: set[str] = set()
committed_effect_count = 0
operation_ref_count = 0
receipt_ref_count = 0
for event in events:
    event_type = str(event["event_type"])
    event_counts[event_type] = event_counts.get(event_type, 0) + 1
    payload = json.loads(str(event["payload_json"]))
    unexpected_keys.update(set(payload) - allowed_keys.get(event_type, set()))
    committed_effect_count += int(payload.get("committed_effect_count") or 0)
    operation_ref_count += len(payload.get("operation_ids") or [])
    receipt_ref_count += len(payload.get("receipt_refs") or [])

created_counts = {}
for table, column in (
    ("work_tickets", "created_at"),
    ("ticket_entries", "created_at"),
    ("ticket_expectations", "created_at"),
    ("ticket_review_runs", "started_at"),
    ("durable_jobs", "created_at"),
    ("skill_runs", "created_at"),
    ("scheduled_jobs", "created_at"),
):
    created_counts[table] = int(
        connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} >= ?",  # noqa: S608 - closed tuple above
            (start,),
        ).fetchone()[0]
    )
created_counts["operation_receipts"] = int(
    connection.execute(
        "SELECT COUNT(*) FROM operation_receipts WHERE committed_at >= ?",
        (start,),
    ).fetchone()[0]
)
durable_job_types = {
    str(row[0]): int(row[1])
    for row in connection.execute(
        "SELECT job_type, COUNT(*) FROM durable_jobs WHERE created_at >= ? GROUP BY job_type",
        (start,),
    ).fetchall()
}
recent_event_counts = {
    str(row[0]): int(row[1])
    for row in connection.execute(
        "SELECT event_type, COUNT(*) FROM events WHERE timestamp >= ? GROUP BY event_type",
        (start,),
    ).fetchall()
}
recent_sessions = [
    dict(row)
    for row in connection.execute(
        """
        SELECT session_id, source, state, owner, last_activity_timestamp
        FROM sessions
        WHERE last_activity_timestamp >= ?
        ORDER BY last_activity_timestamp DESC
        LIMIT 20
        """,
        (start,),
    ).fetchall()
]
recent_memory = [
    {
        "timestamp": str(row["timestamp"]),
        "session_id": str(row["session_id"]),
        "source": str(row["source"]),
        "intent": str(row["intent"]),
        "route": str(row["route"]),
        "request_text": str(row["request_text"]),
        "response_summary": str(row["response_summary"]),
        "metadata_keys": sorted(json.loads(str(row["metadata_json"])).keys()),
    }
    for row in connection.execute(
        """
        SELECT timestamp, session_id, source, intent, route, request_text,
               response_summary, metadata_json
        FROM memory_entries
        WHERE timestamp >= ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (start,),
    ).fetchall()
]
connection.close()

print(
    json.dumps(
        {
            "start": start,
            "event_counts": event_counts,
            "all_main_event_count": int(all_main_event_count),
            "latest_main_event_at": latest_main_event_at,
            "unexpected_payload_keys": sorted(unexpected_keys),
            "committed_effect_count": committed_effect_count,
            "operation_ref_count": operation_ref_count,
            "receipt_ref_count": receipt_ref_count,
            "created_counts": created_counts,
            "durable_job_types": durable_job_types,
            "recent_event_counts": recent_event_counts,
            "recent_sessions": recent_sessions,
            "recent_memory": recent_memory,
        },
        sort_keys=True,
    )
)
