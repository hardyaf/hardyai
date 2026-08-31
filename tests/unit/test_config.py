from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest


SETTING_NAMES = {
    "MAIN_TOOL_EXECUTION_MODE",
    "MAIN_TOOL_ENABLED_DOMAINS",
    "MAIN_TOOL_ENABLED_OPERATIONS",
    "MAIN_TOOL_MAX_SELECTED_SKILLS",
    "MAIN_TOOL_MAX_STEPS",
    "MAIN_TOOL_MAX_FAILURES",
    "MAIN_TOOL_MAX_IDENTICAL_READ_CALLS",
    "MAIN_TOOL_MAX_OBSERVATION_CHARS",
    "MAIN_TOOL_MAX_TOTAL_OBSERVATION_CHARS",
    "MAIN_TOOL_TIMEOUT_SECONDS",
    "LEGACY_MICRO_ROUTING_ENABLED",
    "MICRO_MODEL_NUM_PREDICT",
    "MAIN_REPAIR_MODEL_NUM_PREDICT",
    "MAIN_CONVERSATION_MODEL_NUM_PREDICT",
    "MODEL_ADAPTIVE_TOKEN_MAX_ATTEMPTS",
    "MODEL_ADAPTIVE_TOKEN_MAX_MULTIPLIER",
    "MAIN_AGENT_LOOP_MAX_STEPS",
    "MAIN_AGENT_LOOP_MAX_FAILURES",
    "WEB_RESEARCH_DECISION_MODEL_NUM_PREDICT",
    "EMAIL_AGENT_SUMMARY_NUM_PREDICT",
    "EMAIL_AGENT_CLASSIFIER_NUM_PREDICT",
    "ACTION_TICKET_REVIEW_MODEL_NUM_PREDICT",
    "TURN_TIMEOUT_SECONDS",
}


def _load_config(overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if key not in SETTING_NAMES}
    environment["JARVIS_SKIP_DOTENV"] = "1"
    environment.update(overrides or {})
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from app.config import settings; "
                "print(json.dumps({"
                "'mode': settings.main_tool_execution_mode, "
                "'domains': settings.main_tool_enabled_domains, "
                "'operations': settings.main_tool_enabled_operations, "
                "'selected': settings.main_tool_max_selected_skills, "
                "'steps': settings.main_tool_max_steps, "
                "'failures': settings.main_tool_max_failures, "
                "'repeats': settings.main_tool_max_identical_read_calls, "
                "'observation': settings.main_tool_max_observation_chars, "
                "'total_observation': settings.main_tool_max_total_observation_chars, "
                "'timeout': settings.main_tool_timeout_seconds, "
                "'legacy_micro': settings.legacy_micro_routing_enabled, "
                "'micro_num_predict': settings.micro_model_num_predict, "
                "'repair_num_predict': settings.main_repair_model_num_predict, "
                "'conversation_num_predict': settings.main_conversation_model_num_predict, "
                "'adaptive_attempts': settings.model_adaptive_token_max_attempts, "
                "'adaptive_multiplier': settings.model_adaptive_token_max_multiplier, "
                "'agent_steps': settings.main_agent_loop_max_steps, "
                "'agent_failures': settings.main_agent_loop_max_failures, "
                "'web_decision_num_predict': settings.web_research_decision_model_num_predict, "
                "'email_summary_num_predict': settings.email_agent_summary_num_predict, "
                "'email_classifier_num_predict': settings.email_agent_classifier_num_predict, "
                "'ticket_review_num_predict': settings.action_ticket_review_model_num_predict, "
                "'turn_timeout': settings.turn_timeout_seconds}))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_main_tool_settings_have_inert_locked_defaults() -> None:
    completed = _load_config()

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "mode": "off",
        "domains": [],
        "operations": [],
        "selected": 3,
        "steps": 8,
        "failures": 2,
        "repeats": 2,
        "observation": 8000,
        "total_observation": 24000,
        "timeout": 120,
        "legacy_micro": True,
        "micro_num_predict": 256,
        "repair_num_predict": 1024,
        "conversation_num_predict": 1024,
        "adaptive_attempts": 4,
        "adaptive_multiplier": 8,
        "agent_steps": 8,
        "agent_failures": 2,
        "web_decision_num_predict": 256,
        "email_summary_num_predict": 1024,
        "email_classifier_num_predict": 256,
        "ticket_review_num_predict": 1024,
        "turn_timeout": 240.0,
    }


def test_main_reasoning_settings_accept_development_headroom_profile() -> None:
    completed = _load_config(
        {
            "MAIN_TOOL_MAX_STEPS": "12",
            "MAIN_TOOL_MAX_FAILURES": "4",
            "MAIN_TOOL_TIMEOUT_SECONDS": "240",
            "MICRO_MODEL_NUM_PREDICT": "512",
            "MAIN_REPAIR_MODEL_NUM_PREDICT": "2048",
            "MAIN_CONVERSATION_MODEL_NUM_PREDICT": "2048",
            "MODEL_ADAPTIVE_TOKEN_MAX_ATTEMPTS": "5",
            "MODEL_ADAPTIVE_TOKEN_MAX_MULTIPLIER": "16",
            "MAIN_AGENT_LOOP_MAX_STEPS": "12",
            "MAIN_AGENT_LOOP_MAX_FAILURES": "4",
            "WEB_RESEARCH_DECISION_MODEL_NUM_PREDICT": "512",
            "EMAIL_AGENT_SUMMARY_NUM_PREDICT": "2048",
            "EMAIL_AGENT_CLASSIFIER_NUM_PREDICT": "512",
            "ACTION_TICKET_REVIEW_MODEL_NUM_PREDICT": "2048",
            "TURN_TIMEOUT_SECONDS": "360",
        }
    )

    assert completed.returncode == 0, completed.stderr
    settings = json.loads(completed.stdout)
    assert settings["steps"] == 12
    assert settings["failures"] == 4
    assert settings["timeout"] == 240
    assert settings["micro_num_predict"] == 512
    assert settings["repair_num_predict"] == 2048
    assert settings["conversation_num_predict"] == 2048
    assert settings["adaptive_attempts"] == 5
    assert settings["adaptive_multiplier"] == 16
    assert settings["agent_steps"] == 12
    assert settings["agent_failures"] == 4
    assert settings["web_decision_num_predict"] == 512
    assert settings["email_summary_num_predict"] == 2048
    assert settings["email_classifier_num_predict"] == 512
    assert settings["ticket_review_num_predict"] == 2048
    assert settings["turn_timeout"] == 360.0


def test_main_tool_operation_allowlist_preserves_exact_identifiers() -> None:
    completed = _load_config(
        {
            "MAIN_TOOL_ENABLED_DOMAINS": "email,calendar",
            "MAIN_TOOL_ENABLED_OPERATIONS": "email.query_messages,calendar.query_events",
        }
    )

    assert completed.returncode == 0, completed.stderr
    settings = json.loads(completed.stdout)
    assert settings["domains"] == ["email", "calendar"]
    assert settings["operations"] == ["email.query_messages", "calendar.query_events"]
    assert "email.query" not in settings["operations"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAIN_TOOL_EXECUTION_MODE", "observe"),
        ("MAIN_TOOL_ENABLED_DOMAINS", "email,email"),
        ("MAIN_TOOL_ENABLED_DOMAINS", "Email"),
        ("MAIN_TOOL_ENABLED_OPERATIONS", "email.query_messages,email.query_messages"),
        ("MAIN_TOOL_ENABLED_OPERATIONS", "email"),
        ("MAIN_TOOL_MAX_SELECTED_SKILLS", "4"),
        ("MAIN_TOOL_MAX_STEPS", "0"),
        ("MAIN_TOOL_MAX_FAILURES", "many"),
        ("MAIN_TOOL_MAX_IDENTICAL_READ_CALLS", "-1"),
        ("MAIN_TOOL_MAX_OBSERVATION_CHARS", "0"),
        ("MAIN_TOOL_MAX_TOTAL_OBSERVATION_CHARS", "0"),
        ("MAIN_TOOL_TIMEOUT_SECONDS", "0"),
        ("LEGACY_MICRO_ROUTING_ENABLED", "sometimes"),
    ],
)
def test_main_tool_settings_reject_invalid_values(name: str, value: str) -> None:
    completed = _load_config({name: value})

    assert completed.returncode != 0
    assert name in completed.stderr
