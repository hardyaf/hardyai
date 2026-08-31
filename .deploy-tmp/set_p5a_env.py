from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


path = Path(sys.argv[1]).resolve()
if path.is_symlink() or not path.is_file():
    raise SystemExit("env_precondition_failed")
updates = {
    "MICRO_MODEL_NUM_PREDICT": "512",
    "MAIN_REPAIR_MODEL_NAME": "gpt-oss:20b",
    "MAIN_REPAIR_MODEL_TIMEOUT_SECONDS": "90",
    "MAIN_REPAIR_MODEL_NUM_PREDICT": "2048",
    "MAIN_CONVERSATION_MODEL_TIMEOUT_SECONDS": "120",
    "MAIN_CONVERSATION_MODEL_NUM_PREDICT": "2048",
    "MODEL_ADAPTIVE_TOKEN_MAX_ATTEMPTS": "5",
    "MODEL_ADAPTIVE_TOKEN_MAX_MULTIPLIER": "16",
    "MAIN_TOOL_EXECUTION_MODE": "active",
    "MAIN_TOOL_ENABLED_DOMAINS": "lists",
    "MAIN_TOOL_ENABLED_OPERATIONS": (
        "lists.list_collections,lists.get_collection,"
        "lists.create_collection,lists.add_items"
    ),
    "MAIN_TOOL_MAX_STEPS": "12",
    "MAIN_TOOL_MAX_FAILURES": "4",
    "MAIN_TOOL_TIMEOUT_SECONDS": "240",
    "MAIN_AGENT_LOOP_MAX_STEPS": "12",
    "MAIN_AGENT_LOOP_MAX_FAILURES": "4",
    "WEB_RESEARCH_DECISION_MODEL_NUM_PREDICT": "512",
    "EMAIL_AGENT_SUMMARY_NUM_PREDICT": "2048",
    "EMAIL_AGENT_CLASSIFIER_NUM_PREDICT": "512",
    "ACTION_TICKET_REVIEW_MODEL_NUM_PREDICT": "2048",
    "TURN_TIMEOUT_SECONDS": "360",
}
seen: set[str] = set()
output: list[str] = []
for line in path.read_text(encoding="utf-8").splitlines():
    key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
    if key not in updates:
        output.append(line)
        continue
    if key in seen:
        raise SystemExit(f"duplicate_env_key:{key}")
    output.append(f"{key}={updates[key]}")
    seen.add(key)
for key, value in updates.items():
    if key not in seen:
        output.append(f"{key}={value}")
temporary = path.with_name(path.name + ".p5a.tmp")
temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
os.replace(temporary, path)
print("updated=" + ",".join(sorted(updates)))
