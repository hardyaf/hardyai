from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4


source_path = Path(os.environ["CANARY_SOURCE_DATABASE"])
canary_path = Path(os.environ["DATABASE_PATH"])
if not source_path.is_file() or canary_path.exists():
    raise SystemExit("canary_database_precondition_failed")
canary_path.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
    with sqlite3.connect(canary_path) as destination:
        source.backup(destination)

from app import runtime  # noqa: E402
from app.schemas.api import AskRequest  # noqa: E402
token = uuid4().hex[:10]
list_name = f"p5a adaptive canary {token}"
expected_items = [f"titanium sprocket {token}", f"amber gasket {token}"]
request_text = f"Add {expected_items[0]} and {expected_items[1]} to the {list_name} list."
session_id = f"p5a-canary-{token}"
user_id = str(os.environ.get("CANARY_USER_ID") or "p5a-canary-user").strip()
request_context = {
    "agent_id": "jarvis",
    "agent_display_name": "Jarvis",
    "timezone": "America/New_York",
}
session = runtime.session_store.get_or_create(
    session_id=session_id,
    user_id=user_id,
    source="web",
)
discovery_cards = runtime.router._authorized_skill_executor.discovery_cards(
    user_id=user_id,
    agent_id="jarvis",
    source_interface="web",
    request_context=request_context,
)
model_records: list[dict[str, object]] = []


class RecordingModel:
    def __init__(self, delegate: object) -> None:
        self._delegate = delegate

    def select_skills(self, *args: object, **kwargs: object) -> object:
        output = self._delegate.select_skills(*args, **kwargs)
        model_records.append(
            {
                "stage": "select_skills",
                "mode": output.get("mode") if isinstance(output, dict) else None,
                "selected_skill_ids": (
                    output.get("selected_skill_ids") if isinstance(output, dict) else None
                ),
            }
        )
        return output

    def decide_turn(self, *args: object, **kwargs: object) -> object:
        output = self._delegate.decide_turn(*args, **kwargs)
        model_records.append(
            {
                "stage": "decide_turn",
                "mode": output.get("mode") if isinstance(output, dict) else None,
            }
        )
        return output

    def next_tool_step(self, *args: object, **kwargs: object) -> object:
        output = self._delegate.next_tool_step(*args, **kwargs)
        arguments = output.get("arguments") if isinstance(output, dict) else None
        claims = output.get("provenance_claims") if isinstance(output, dict) else None
        observations = args[2] if len(args) > 2 else kwargs.get("observations")
        model_records.append(
            {
                "stage": "next_tool_step",
                "mode": output.get("mode") if isinstance(output, dict) else None,
                "tool_id": output.get("tool_id") if isinstance(output, dict) else None,
                "call_id_present": bool(output.get("call_id")) if isinstance(output, dict) else False,
                "argument_keys": sorted(arguments) if isinstance(arguments, dict) else [],
                "observation_refs": [
                    str(observation.get("observation_ref") or "")
                    for observation in observations or []
                    if isinstance(observation, dict)
                ],
                "claim_destinations": [
                    str(claim.get("destination_pointer") or "")
                    for claim in claims or []
                    if isinstance(claim, dict)
                ],
                "claim_sources": [
                    str(claim.get("source_pointer") or "")
                    for claim in claims or []
                    if isinstance(claim, dict)
                ],
                "claim_source_refs": [
                    str(claim.get("source_observation_ref") or "")
                    for claim in claims or []
                    if isinstance(claim, dict)
                ],
            }
        )
        return output


recording_model = RecordingModel(runtime.main_conversation_backend)
runtime.router._main_tool_loop._model = recording_model
runtime.router._main_turn_commitment._main_tool_model = recording_model
full_route = os.environ.get("CANARY_FULL_ROUTE") == "1"
if full_route:
    routed = runtime.router.route(
        AskRequest(
            text=request_text,
            request_id=f"p5a-canary-request-{token}",
            session_id=session_id,
            user_id=user_id,
            source="web",
            context={**request_context, "wake_on_message": True},
        )
    )
    result = routed.get("result") if isinstance(routed, dict) else None
else:
    routed = {"route": "main_tool_loop_direct_canary"}
    result = runtime.router._main_tool_loop.run(
        text=request_text,
        request_id=f"p5a-canary-request-{token}",
        session=session,
        user_id=user_id,
        agent_id="jarvis",
        source_interface="web",
        request_context=request_context,
    )
if not isinstance(result, dict):
    raise SystemExit("canary_result_missing")

with sqlite3.connect(canary_path) as connection:
    collection = connection.execute(
        """
        SELECT list_id, list_name_normalized
        FROM lists
        WHERE owner_user_id = ? AND list_name_normalized = ?
        """,
        (user_id, list_name),
    ).fetchone()
    if collection is None:
        print(
            json.dumps(
                {
                    "passed": False,
                    "route": routed.get("route"),
                    "status": result.get("status"),
                    "stop_reason": result.get("stop_reason"),
                    "steps": result.get("steps"),
                    "failures": result.get("failures"),
                    "observation_count": result.get("observation_count"),
                    "committed_effect_count": result.get("committed_effect_count"),
                    "selected_skill_count": len(result.get("selected_skill_ids") or []),
                    "tool_count": len(result.get("tool_ids") or []),
                    "discovery_card_count": len(discovery_cards),
                    "discovery_skill_ids": [
                        str(card.get("skill_id") or "") for card in discovery_cards
                    ],
                    "configured_domains": list(runtime.settings.main_tool_enabled_domains),
                    "configured_operation_count": len(
                        runtime.settings.main_tool_enabled_operations
                    ),
                    "model_backend_status": runtime.main_conversation_backend.status(),
                    "model_records": model_records,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit("canary_collection_missing")
    item_rows = connection.execute(
        """
        SELECT item_name
        FROM list_items
        WHERE list_id = ?
        ORDER BY position, item_id
        """,
        (str(collection[0]),),
    ).fetchall()
    actions = connection.execute(
        """
        SELECT action
        FROM list_operations
        WHERE owner_user_id = ? AND target_ref = ?
        ORDER BY created_at, operation_id
        """,
        (user_id, str(collection[0])),
    ).fetchall()

actual_items = [str(row[0]) for row in item_rows]
actual_actions = [str(row[0]) for row in actions]
passed = (
    result.get("status") == "responded"
    and actual_items == expected_items
    and actual_actions.count("lists.create_collection") == 1
    and actual_actions.count("lists.add_items") == 1
)
summary = {
    "passed": passed,
    "route": routed.get("route"),
    "status": result.get("status"),
    "stop_reason": result.get("stop_reason"),
    "steps": result.get("steps"),
    "observation_count": result.get("observation_count"),
    "committed_effect_count": result.get("committed_effect_count"),
    "item_count": len(actual_items),
    "items_match": actual_items == expected_items,
    "actions": actual_actions,
    "discovery_card_count": len(discovery_cards),
    "model_backend_status": runtime.main_conversation_backend.status(),
    "model_records": model_records,
}
print(json.dumps(summary, indent=2, sort_keys=True))
raise SystemExit(0 if passed else 1)
