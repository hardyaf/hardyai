from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from pathlib import Path
from uuid import uuid4


env_path_value = os.environ.get("JARVIS_ENV_PATH", "").strip()
env_root = Path.cwd()
if env_path_value:
    env_path = Path(env_path_value).resolve()
    env_root = env_path.parent
    allowed_env_keys = {
        "DATABASE_PATH",
        "JARVIS_OPERATOR_API_KEY",
        "JARVIS_OPERATOR_API_KEY_FILE",
        "MAIN_TOOL_EXECUTION_MODE",
        "MAIN_TOOL_ENABLED_DOMAINS",
        "MAIN_TOOL_ENABLED_OPERATIONS",
    }
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in allowed_env_keys:
            os.environ[key] = value.strip().strip('"').strip("'")

token = uuid4().hex[:10]
user_id = "operator"
list_name = f"p5a live canary {token}"
expected_items = [f"cobalt washer {token}", f"linen cord {token}"]
payload = {
    "text": f"Add {expected_items[0]} and {expected_items[1]} to the {list_name} list.",
    "request_id": f"p5a-live-canary-{token}",
    "session_id": f"p5a-live-canary-{token}",
    "user_id": user_id,
    "source": "web",
    "context": {
        "agent_id": "jarvis",
        "agent_display_name": "Jarvis",
        "timezone": "America/New_York",
        "wake_on_message": True,
    },
}
operator_key = os.environ.get("JARVIS_OPERATOR_API_KEY", "").strip()
operator_key_file_value = os.environ.get("JARVIS_OPERATOR_API_KEY_FILE", "").strip()
if not operator_key and operator_key_file_value:
    operator_key_file = Path(operator_key_file_value)
    if operator_key_file.is_symlink():
        raise SystemExit("operator_key_file_must_not_be_symlink")
    operator_key = operator_key_file.read_text(encoding="utf-8").strip()
if not operator_key:
    raise SystemExit("operator_key_missing")

request = urllib.request.Request(
    os.environ.get("JARVIS_BASE_URL", "http://127.0.0.1:8000").rstrip("/") + "/ask",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-Jarvis-Operator-Key": operator_key,
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=180) as response:
    routed = json.load(response)
result = routed.get("result") if isinstance(routed, dict) else None
if not isinstance(result, dict):
    raise SystemExit("live_canary_result_missing")

database_path = os.environ.get("DATABASE_PATH", "./data/jarvis_v2.db")
resolved_database_path = Path(database_path)
if not resolved_database_path.is_absolute():
    resolved_database_path = (env_root / resolved_database_path).resolve()
with sqlite3.connect(resolved_database_path) as connection:
    schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    collection = connection.execute(
        """
        SELECT list_id
        FROM lists
        WHERE owner_user_id = ? AND list_name_normalized = ?
        """,
        (user_id, list_name),
    ).fetchone()
    if collection is None:
        item_rows: list[tuple[str]] = []
        action_rows: list[tuple[str]] = []
    else:
        item_rows = connection.execute(
            """
            SELECT item_name
            FROM list_items
            WHERE list_id = ?
            ORDER BY position, item_id
            """,
            (str(collection[0]),),
        ).fetchall()
        action_rows = connection.execute(
            """
            SELECT action
            FROM list_operations
            WHERE owner_user_id = ? AND target_ref = ?
            ORDER BY created_at, operation_id
            """,
            (user_id, str(collection[0])),
        ).fetchall()

actual_items = [str(row[0]) for row in item_rows]
actions = [str(row[0]) for row in action_rows]
passed = (
    schema_version == 9
    and quick_check == "ok"
    and os.environ.get("MAIN_TOOL_EXECUTION_MODE") == "active"
    and os.environ.get("MAIN_TOOL_ENABLED_DOMAINS") == "lists"
    and routed.get("route") == "main_tool_loop"
    and result.get("status") == "responded"
    and actual_items == expected_items
    and actions.count("lists.create_collection") == 1
    and actions.count("lists.add_items") == 1
)
print(
    json.dumps(
        {
            "passed": passed,
            "route": routed.get("route"),
            "status": result.get("status"),
            "stop_reason": result.get("stop_reason"),
            "steps": result.get("steps"),
            "observation_count": result.get("observation_count"),
            "committed_effect_count": result.get("committed_effect_count"),
            "item_count": len(actual_items),
            "items_match": actual_items == expected_items,
            "actions": actions,
            "schema_version": schema_version,
            "quick_check": quick_check,
            "execution_mode": os.environ.get("MAIN_TOOL_EXECUTION_MODE"),
            "enabled_domains": os.environ.get("MAIN_TOOL_ENABLED_DOMAINS"),
            "enabled_operation_count": len(
                [
                    item
                    for item in os.environ.get("MAIN_TOOL_ENABLED_OPERATIONS", "").split(",")
                    if item
                ]
            ),
        },
        indent=2,
        sort_keys=True,
    )
)
raise SystemExit(0 if passed else 1)
