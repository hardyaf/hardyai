from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv_file() -> None:
    if os.getenv("JARVIS_SKIP_DOTENV", "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        # Preserve explicitly provided process env vars over .env defaults.
        os.environ.setdefault(key, value)


_load_dotenv_file()


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _as_csv_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    items = [item.strip() for item in raw.split(",")]
    return [item for item in items if item]


@dataclass(frozen=True)
class Settings:
    app_env: str
    micro_fast_confidence_threshold: float
    default_session_source: str
    micro_model_enabled: bool
    micro_model_provider: str
    micro_model_name: str
    local_model_url: str
    micro_model_timeout_seconds: float
    micro_model_num_ctx: int
    micro_model_num_predict: int
    micro_model_heuristic_fallback_enabled: bool
    main_repair_model_enabled: bool
    main_repair_model_provider: str
    main_repair_model_name: str
    main_repair_model_timeout_seconds: float
    main_repair_model_num_ctx: int
    main_repair_model_num_predict: int
    main_conversation_model_timeout_seconds: float
    main_conversation_model_num_ctx: int
    main_conversation_model_num_predict: int
    larger_model_micro_only_window_seconds: float
    skill_artifact_auto_compile_enabled: bool
    main_agent_loop_max_steps: int
    main_agent_loop_max_failures: int
    main_agent_loop_context_max_chars: int
    main_agent_loop_auto_approve_actions: bool
    main_agent_content_policy_enabled: bool
    main_agent_content_policy_children_only: bool
    main_agent_content_policy_blocked_patterns: list[str]
    main_agent_token_session_enabled: bool
    main_agent_token_session_max_turns: int
    main_conversational_confidence_threshold: float
    main_low_confidence_floor: float
    main_high_risk_confidence_threshold: float
    main_sticky_followup_turns: int
    main_pending_clarification_heuristic_fallback_enabled: bool
    web_research_enabled: bool
    web_research_provider: str
    web_research_base_url: str
    web_research_timeout_seconds: float
    web_research_decision_timeout_seconds: float
    web_research_decision_model_num_ctx: int
    web_research_decision_model_num_predict: int
    web_research_max_results: int
    web_research_safe_search: int
    web_research_children_enabled: bool
    web_research_cache_ttl_seconds: float
    channel_session_idle_timeout_seconds: float
    database_path: str
    discord_enabled: bool
    discord_bot_token: str
    discord_command_prefix: str
    discord_command_channel_id: str
    discord_command_guild_id: str
    discord_permissions_path: str
    memory_mode: str
    memory_markdown_path: str
    house_switch_names: list[str]
    calendar_google_enabled: bool
    google_permissions_path: str
    calendar_inbox_enabled: bool
    calendar_inbox_timezone: str
    calendar_inbox_start_hour: int
    calendar_inbox_end_hour: int
    calendar_inbox_poll_seconds: float
    calendar_inbox_max_messages_per_run: int
    calendar_inbox_lookback_days: int
    calendar_inbox_allowed_sender_emails: list[str]
    email_agent_enabled: bool
    email_agent_sync_enabled: bool
    email_agent_permissions_path: str
    email_agent_timezone: str
    email_agent_scheduler_poll_seconds: float
    email_agent_sync_interval_seconds: int
    email_agent_on_demand_stale_seconds: int
    email_agent_max_history_pages: int
    email_agent_max_messages_per_run: int
    email_agent_max_interactive_messages: int
    email_agent_max_body_bytes: int
    email_agent_reference_retention_hours: int
    email_agent_allow_historical_backfill: bool
    email_agent_summary_model_provider: str
    email_agent_model_num_ctx: int
    email_agent_summary_num_predict: int
    email_agent_classifier_num_predict: int
    email_agent_allow_remote_model: bool
    email_agent_attachment_extraction_enabled: bool
    email_agent_label_shadow_enabled: bool
    email_agent_label_writes_enabled: bool
    email_agent_label_max_writes_per_hour: int
    email_agent_label_max_writes_per_day: int
    email_agent_label_token_path: str
    email_agent_spam_writes_enabled: bool
    email_agent_spam_token_path: str
    email_agent_worker_token_isolated: bool
    email_agent_spam_worker_poll_seconds: float
    email_agent_spam_worker_batch_size: int
    email_agent_spam_worker_lease_seconds: int
    email_agent_spam_max_attempts: int
    email_agent_spam_max_writes_per_hour: int
    email_agent_spam_max_writes_per_day: int
    action_tickets_enabled: bool
    action_ticket_capture_mode: str
    action_ticket_review_enabled: bool
    action_ticket_review_delay_seconds: float
    action_ticket_execution_watchdog_seconds: float
    action_ticket_review_poll_seconds: float
    action_ticket_review_live_idle_seconds: float
    action_ticket_review_batch_size: int
    action_ticket_review_max_attempts: int
    action_ticket_remediation_max_generation: int
    action_ticket_review_model_provider: str
    action_ticket_review_model_name: str
    action_ticket_review_model_timeout_seconds: float
    action_ticket_review_model_num_ctx: int
    action_ticket_review_model_num_predict: int
    action_ticket_review_context_max_chars: int
    action_ticket_auto_remediation_enabled: bool
    plane_enabled: bool
    plane_api_base_url: str
    plane_api_key: str
    plane_workspace_slug: str
    plane_project_id: str
    plane_sync_raw_transcript: bool
    plane_api_timeout_seconds: float
    operator_api_key: str
    operator_session_ttl_seconds: int
    turn_max_concurrency: int
    turn_queue_capacity: int
    turn_timeout_seconds: float


settings = Settings(
    app_env=os.getenv("APP_ENV", "development"),
    micro_fast_confidence_threshold=_as_float("MICRO_FAST_CONFIDENCE_THRESHOLD", 0.72),
    default_session_source=os.getenv("DEFAULT_SESSION_SOURCE", "web"),
    micro_model_enabled=_as_bool("MICRO_MODEL_ENABLED", False),
    micro_model_provider=os.getenv("MICRO_MODEL_PROVIDER", "ollama"),
    micro_model_name=os.getenv("MICRO_MODEL_NAME", "qwen2.5:7b"),
    local_model_url=os.getenv("LOCAL_MODEL_URL", "http://127.0.0.1:11434"),
    micro_model_timeout_seconds=_as_float("MICRO_MODEL_TIMEOUT_SECONDS", 6.0),
    micro_model_num_ctx=max(512, _as_int("MICRO_MODEL_NUM_CTX", 4096)),
    micro_model_num_predict=max(1, _as_int("MICRO_MODEL_NUM_PREDICT", 256)),
    micro_model_heuristic_fallback_enabled=_as_bool(
        "MICRO_MODEL_HEURISTIC_FALLBACK_ENABLED",
        True,
    ),
    main_repair_model_enabled=_as_bool(
        "MAIN_REPAIR_MODEL_ENABLED",
        _as_bool("MICRO_MODEL_ENABLED", False),
    ),
    main_repair_model_provider=os.getenv(
        "MAIN_REPAIR_MODEL_PROVIDER",
        os.getenv("MICRO_MODEL_PROVIDER", "ollama"),
    ),
    main_repair_model_name=os.getenv(
        "MAIN_REPAIR_MODEL_NAME",
        "gpt-oss:20b",
    ),
    main_repair_model_timeout_seconds=_as_float(
        "MAIN_REPAIR_MODEL_TIMEOUT_SECONDS",
        _as_float("MICRO_MODEL_TIMEOUT_SECONDS", 6.0),
    ),
    main_repair_model_num_ctx=max(512, _as_int("MAIN_REPAIR_MODEL_NUM_CTX", 12288)),
    main_repair_model_num_predict=max(1, _as_int("MAIN_REPAIR_MODEL_NUM_PREDICT", 512)),
    main_conversation_model_timeout_seconds=_as_float(
        "MAIN_CONVERSATION_MODEL_TIMEOUT_SECONDS",
        max(
            _as_float(
                "MAIN_REPAIR_MODEL_TIMEOUT_SECONDS",
                _as_float("MICRO_MODEL_TIMEOUT_SECONDS", 6.0),
            ),
            20.0,
        ),
    ),
    main_conversation_model_num_ctx=max(512, _as_int("MAIN_CONVERSATION_MODEL_NUM_CTX", 12288)),
    main_conversation_model_num_predict=max(1, _as_int("MAIN_CONVERSATION_MODEL_NUM_PREDICT", 1024)),
    larger_model_micro_only_window_seconds=_as_float(
        "LARGER_MODEL_MICRO_ONLY_WINDOW_SECONDS",
        180.0,
    ),
    skill_artifact_auto_compile_enabled=_as_bool(
        "SKILL_ARTIFACT_AUTO_COMPILE_ENABLED",
        True,
    ),
    main_agent_loop_max_steps=max(1, _as_int("MAIN_AGENT_LOOP_MAX_STEPS", 8)),
    main_agent_loop_max_failures=max(1, _as_int("MAIN_AGENT_LOOP_MAX_FAILURES", 2)),
    main_agent_loop_context_max_chars=max(256, _as_int("MAIN_AGENT_LOOP_CONTEXT_MAX_CHARS", 6000)),
    main_agent_loop_auto_approve_actions=_as_bool("MAIN_AGENT_LOOP_AUTO_APPROVE_ACTIONS", True),
    main_agent_content_policy_enabled=_as_bool("MAIN_AGENT_CONTENT_POLICY_ENABLED", True),
    main_agent_content_policy_children_only=_as_bool("MAIN_AGENT_CONTENT_POLICY_CHILDREN_ONLY", True),
    main_agent_content_policy_blocked_patterns=_as_csv_list(
        "MAIN_AGENT_CONTENT_POLICY_BLOCKED_PATTERNS",
        ["kill", "weapon", "drug", "explicit", "porn"],
    ),
    main_agent_token_session_enabled=_as_bool("MAIN_AGENT_TOKEN_SESSION_ENABLED", True),
    main_agent_token_session_max_turns=max(1, _as_int("MAIN_AGENT_TOKEN_SESSION_MAX_TURNS", 12)),
    main_conversational_confidence_threshold=_as_float(
        "MAIN_CONVERSATIONAL_CONFIDENCE_THRESHOLD",
        0.70,
    ),
    main_low_confidence_floor=_as_float(
        "MAIN_LOW_CONFIDENCE_FLOOR",
        0.55,
    ),
    main_high_risk_confidence_threshold=_as_float(
        "MAIN_HIGH_RISK_CONFIDENCE_THRESHOLD",
        0.80,
    ),
    main_sticky_followup_turns=max(0, _as_int("MAIN_STICKY_FOLLOWUP_TURNS", 2)),
    main_pending_clarification_heuristic_fallback_enabled=_as_bool(
        "MAIN_PENDING_CLARIFICATION_HEURISTIC_FALLBACK_ENABLED",
        False,
    ),
    web_research_enabled=_as_bool("WEB_RESEARCH_ENABLED", False),
    web_research_provider=os.getenv("WEB_RESEARCH_PROVIDER", "searxng").strip().lower(),
    web_research_base_url=os.getenv("WEB_RESEARCH_BASE_URL", "http://127.0.0.1:8080"),
    web_research_timeout_seconds=max(1.0, _as_float("WEB_RESEARCH_TIMEOUT_SECONDS", 15.0)),
    web_research_decision_timeout_seconds=max(
        1.0,
        _as_float("WEB_RESEARCH_DECISION_TIMEOUT_SECONDS", 60.0),
    ),
    web_research_decision_model_num_ctx=max(
        512,
        _as_int("WEB_RESEARCH_DECISION_MODEL_NUM_CTX", 12288),
    ),
    web_research_decision_model_num_predict=max(
        1,
        _as_int("WEB_RESEARCH_DECISION_MODEL_NUM_PREDICT", 256),
    ),
    web_research_max_results=max(1, min(_as_int("WEB_RESEARCH_MAX_RESULTS", 5), 8)),
    web_research_safe_search=max(0, min(_as_int("WEB_RESEARCH_SAFE_SEARCH", 1), 2)),
    web_research_children_enabled=_as_bool("WEB_RESEARCH_CHILDREN_ENABLED", False),
    web_research_cache_ttl_seconds=max(
        0.0,
        _as_float("WEB_RESEARCH_CACHE_TTL_SECONDS", 900.0),
    ),
    channel_session_idle_timeout_seconds=_as_float("CHANNEL_SESSION_IDLE_TIMEOUT_SECONDS", 180.0),
    database_path=os.getenv("DATABASE_PATH", "./data/jarvis_v2.db"),
    discord_enabled=_as_bool("DISCORD_ENABLED", False),
    discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
    discord_command_prefix=os.getenv("DISCORD_COMMAND_PREFIX", "!jarvis"),
    discord_command_channel_id=os.getenv("DISCORD_COMMAND_CHANNEL_ID", ""),
    discord_command_guild_id=os.getenv("DISCORD_COMMAND_GUILD_ID", ""),
    discord_permissions_path=os.getenv(
        "DISCORD_PERMISSIONS_PATH",
        "secrets/live/discord_permissions.yaml",
    ),
    memory_mode=os.getenv("MEMORY_MODE", "sqlite"),
    memory_markdown_path=os.getenv("MEMORY_MARKDOWN_PATH", "./data/memory_markdown"),
    house_switch_names=_as_csv_list(
        "HOUSE_SWITCH_NAMES",
        [
            "office test light",
            "kitchen light",
            "living room lamp",
            "bedroom lamp",
        ],
    ),
    calendar_google_enabled=_as_bool("CALENDAR_GOOGLE_ENABLED", False),
    google_permissions_path=os.getenv(
        "GOOGLE_PERMISSIONS_PATH",
        "secrets/live/google_permissions.yaml",
    ),
    calendar_inbox_enabled=_as_bool("CALENDAR_INBOX_ENABLED", False),
    calendar_inbox_timezone=os.getenv("CALENDAR_INBOX_TIMEZONE", "America/New_York").strip()
    or "America/New_York",
    calendar_inbox_start_hour=max(0, min(_as_int("CALENDAR_INBOX_START_HOUR", 8), 23)),
    calendar_inbox_end_hour=max(0, min(_as_int("CALENDAR_INBOX_END_HOUR", 20), 23)),
    calendar_inbox_poll_seconds=max(30.0, _as_float("CALENDAR_INBOX_POLL_SECONDS", 60.0)),
    calendar_inbox_max_messages_per_run=max(
        1,
        min(_as_int("CALENDAR_INBOX_MAX_MESSAGES_PER_RUN", 100), 200),
    ),
    calendar_inbox_lookback_days=max(1, min(_as_int("CALENDAR_INBOX_LOOKBACK_DAYS", 30), 90)),
    calendar_inbox_allowed_sender_emails=_as_csv_list(
        "CALENDAR_INBOX_ALLOWED_SENDER_EMAILS",
        [],
    ),
    email_agent_enabled=_as_bool("EMAIL_AGENT_ENABLED", False),
    email_agent_sync_enabled=_as_bool("EMAIL_AGENT_SYNC_ENABLED", False),
    email_agent_permissions_path=os.getenv(
        "EMAIL_AGENT_PERMISSIONS_PATH",
        "secrets/live/email_agent_permissions.yaml",
    ),
    email_agent_timezone=os.getenv("EMAIL_AGENT_TIMEZONE", "America/New_York").strip()
    or "America/New_York",
    email_agent_scheduler_poll_seconds=max(
        30.0,
        min(_as_float("EMAIL_AGENT_SCHEDULER_POLL_SECONDS", 60.0), 3600.0),
    ),
    email_agent_sync_interval_seconds=max(
        60,
        min(_as_int("EMAIL_AGENT_SYNC_INTERVAL_SECONDS", 600), 3600),
    ),
    email_agent_on_demand_stale_seconds=max(
        30,
        min(_as_int("EMAIL_AGENT_ON_DEMAND_STALE_SECONDS", 120), 1800),
    ),
    email_agent_max_history_pages=max(
        1,
        min(_as_int("EMAIL_AGENT_MAX_HISTORY_PAGES", 5), 10),
    ),
    email_agent_max_messages_per_run=max(
        1,
        min(_as_int("EMAIL_AGENT_MAX_MESSAGES_PER_RUN", 100), 200),
    ),
    email_agent_max_interactive_messages=max(
        1,
        min(_as_int("EMAIL_AGENT_MAX_INTERACTIVE_MESSAGES", 10), 20),
    ),
    email_agent_max_body_bytes=max(
        1024,
        min(_as_int("EMAIL_AGENT_MAX_BODY_BYTES", 1048576), 2097152),
    ),
    email_agent_reference_retention_hours=max(
        1,
        min(_as_int("EMAIL_AGENT_REFERENCE_RETENTION_HOURS", 72), 720),
    ),
    email_agent_allow_historical_backfill=_as_bool(
        "EMAIL_AGENT_ALLOW_HISTORICAL_BACKFILL",
        False,
    ),
    email_agent_summary_model_provider=os.getenv(
        "EMAIL_AGENT_SUMMARY_MODEL_PROVIDER",
        "ollama",
    ).strip().casefold(),
    email_agent_model_num_ctx=max(512, _as_int("EMAIL_AGENT_MODEL_NUM_CTX", 12288)),
    email_agent_summary_num_predict=max(1, _as_int("EMAIL_AGENT_SUMMARY_NUM_PREDICT", 1024)),
    email_agent_classifier_num_predict=max(1, _as_int("EMAIL_AGENT_CLASSIFIER_NUM_PREDICT", 256)),
    email_agent_allow_remote_model=_as_bool("EMAIL_AGENT_ALLOW_REMOTE_MODEL", False),
    email_agent_attachment_extraction_enabled=_as_bool(
        "EMAIL_AGENT_ATTACHMENT_EXTRACTION_ENABLED",
        False,
    ),
    email_agent_label_shadow_enabled=_as_bool("EMAIL_AGENT_LABEL_SHADOW_ENABLED", True),
    email_agent_label_writes_enabled=_as_bool("EMAIL_AGENT_LABEL_WRITES_ENABLED", False),
    email_agent_label_max_writes_per_hour=max(
        1,
        min(_as_int("EMAIL_AGENT_LABEL_MAX_WRITES_PER_HOUR", 20), 100),
    ),
    email_agent_label_max_writes_per_day=max(
        1,
        min(_as_int("EMAIL_AGENT_LABEL_MAX_WRITES_PER_DAY", 50), 500),
    ),
    email_agent_label_token_path=os.getenv(
        "EMAIL_AGENT_LABEL_TOKEN_PATH",
        "secrets/email-spam-worker/token.json",
    ),
    email_agent_spam_writes_enabled=_as_bool("EMAIL_AGENT_SPAM_WRITES_ENABLED", False),
    email_agent_spam_token_path=os.getenv(
        "EMAIL_AGENT_SPAM_TOKEN_PATH",
        "secrets/email-spam-worker/token.json",
    ),
    email_agent_worker_token_isolated=_as_bool(
        "EMAIL_AGENT_WORKER_TOKEN_ISOLATED",
        False,
    ),
    email_agent_spam_worker_poll_seconds=max(
        1.0,
        min(_as_float("EMAIL_AGENT_SPAM_WORKER_POLL_SECONDS", 2.0), 60.0),
    ),
    email_agent_spam_worker_batch_size=max(
        1,
        min(_as_int("EMAIL_AGENT_SPAM_WORKER_BATCH_SIZE", 5), 10),
    ),
    email_agent_spam_worker_lease_seconds=max(
        15,
        min(_as_int("EMAIL_AGENT_SPAM_WORKER_LEASE_SECONDS", 60), 300),
    ),
    email_agent_spam_max_attempts=max(
        1,
        min(_as_int("EMAIL_AGENT_SPAM_MAX_ATTEMPTS", 3), 5),
    ),
    email_agent_spam_max_writes_per_hour=max(
        1,
        min(_as_int("EMAIL_AGENT_SPAM_MAX_WRITES_PER_HOUR", 5), 50),
    ),
    email_agent_spam_max_writes_per_day=max(
        1,
        min(_as_int("EMAIL_AGENT_SPAM_MAX_WRITES_PER_DAY", 10), 200),
    ),
    action_tickets_enabled=_as_bool("ACTION_TICKETS_ENABLED", False),
    action_ticket_capture_mode=os.getenv("ACTION_TICKET_CAPTURE_MODE", "shadow").strip().lower(),
    action_ticket_review_enabled=_as_bool("ACTION_TICKET_REVIEW_ENABLED", False),
    action_ticket_review_delay_seconds=max(
        0.0,
        _as_float("ACTION_TICKET_REVIEW_DELAY_SECONDS", 3600.0),
    ),
    action_ticket_execution_watchdog_seconds=max(
        30.0,
        _as_float("ACTION_TICKET_EXECUTION_WATCHDOG_SECONDS", 300.0),
    ),
    action_ticket_review_poll_seconds=max(
        1.0,
        _as_float("ACTION_TICKET_REVIEW_POLL_SECONDS", 10.0),
    ),
    action_ticket_review_live_idle_seconds=max(
        0.0,
        _as_float("ACTION_TICKET_REVIEW_LIVE_IDLE_SECONDS", 15.0),
    ),
    action_ticket_review_batch_size=max(
        1,
        _as_int("ACTION_TICKET_REVIEW_BATCH_SIZE", 5),
    ),
    action_ticket_review_max_attempts=max(
        1,
        _as_int("ACTION_TICKET_REVIEW_MAX_ATTEMPTS", 3),
    ),
    action_ticket_remediation_max_generation=max(
        0,
        _as_int("ACTION_TICKET_REMEDIATION_MAX_GENERATION", 3),
    ),
    action_ticket_review_model_provider=os.getenv(
        "ACTION_TICKET_REVIEW_MODEL_PROVIDER",
        "ollama",
    ),
    action_ticket_review_model_name=(
        os.getenv("ACTION_TICKET_REVIEW_MODEL_NAME", "").strip()
        or os.getenv("MAIN_REPAIR_MODEL_NAME", "gpt-oss:20b").strip()
        or "gpt-oss:20b"
    ),
    action_ticket_review_model_timeout_seconds=max(
        1.0,
        _as_float("ACTION_TICKET_REVIEW_MODEL_TIMEOUT_SECONDS", 180.0),
    ),
    action_ticket_review_model_num_ctx=max(
        512,
        _as_int("ACTION_TICKET_REVIEW_MODEL_NUM_CTX", 12288),
    ),
    action_ticket_review_model_num_predict=max(
        1,
        _as_int("ACTION_TICKET_REVIEW_MODEL_NUM_PREDICT", 1024),
    ),
    action_ticket_review_context_max_chars=max(
        4096,
        _as_int("ACTION_TICKET_REVIEW_CONTEXT_MAX_CHARS", 32000),
    ),
    action_ticket_auto_remediation_enabled=_as_bool(
        "ACTION_TICKET_AUTO_REMEDIATION_ENABLED",
        False,
    ),
    plane_enabled=_as_bool("PLANE_ENABLED", False),
    plane_api_base_url=os.getenv("PLANE_API_BASE_URL", ""),
    plane_api_key=os.getenv("PLANE_API_KEY", ""),
    plane_workspace_slug=os.getenv("PLANE_WORKSPACE_SLUG", ""),
    plane_project_id=os.getenv("PLANE_PROJECT_ID", ""),
    plane_sync_raw_transcript=_as_bool("PLANE_SYNC_RAW_TRANSCRIPT", False),
    plane_api_timeout_seconds=max(1.0, _as_float("PLANE_API_TIMEOUT_SECONDS", 30.0)),
    operator_api_key=os.getenv("JARVIS_OPERATOR_API_KEY", ""),
    operator_session_ttl_seconds=max(
        300,
        min(_as_int("JARVIS_OPERATOR_SESSION_TTL_SECONDS", 3600), 86400),
    ),
    turn_max_concurrency=max(1, min(_as_int("TURN_MAX_CONCURRENCY", 1), 8)),
    turn_queue_capacity=max(0, min(_as_int("TURN_QUEUE_CAPACITY", 8), 100)),
    turn_timeout_seconds=max(5.0, min(_as_float("TURN_TIMEOUT_SECONDS", 240.0), 900.0)),
)
