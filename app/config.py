from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.ollama_observability import OllamaThinkMode, normalize_ollama_think_mode


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


def _as_ollama_think(name: str, default: OllamaThinkMode) -> OllamaThinkMode:
    raw = os.getenv(name)
    return normalize_ollama_think_mode(raw, default=default)


def _secret_value(value_name: str, file_name: str) -> str:
    direct = str(os.getenv(value_name, "") or "").strip()
    if direct:
        return direct
    secret_path = str(os.getenv(file_name, "") or "").strip()
    if not secret_path:
        return ""
    try:
        return Path(secret_path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


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
    main_repair_model_think: OllamaThinkMode
    main_conversation_model_timeout_seconds: float
    main_conversation_model_num_ctx: int
    main_conversation_model_num_predict: int
    main_conversation_model_think: OllamaThinkMode
    main_turn_decision_model_think: OllamaThinkMode
    model_adaptive_token_budget_enabled: bool
    model_adaptive_token_max_attempts: int
    model_adaptive_token_growth_factor: float
    model_adaptive_token_max_multiplier: int
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
    web_research_decision_model_think: OllamaThinkMode
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
    discord_attachment_ingress_enabled: bool
    discord_attachment_ingress_base_url: str
    discord_attachment_ingress_timeout_seconds: float
    discord_attachment_max_per_message: int
    discord_document_notifications_enabled: bool
    discord_document_notification_poll_seconds: float
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
    email_agent_summary_model_think: OllamaThinkMode
    email_agent_classifier_model_think: OllamaThinkMode
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
    action_ticket_review_model_think: OllamaThinkMode
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
    offline_mode: bool
    documents_enabled: bool
    documents_local_only: bool
    documents_database_path: str
    documents_spool_path: str
    documents_max_upload_bytes: int
    documents_max_request_overhead_bytes: int
    documents_spool_quota_bytes: int
    documents_min_free_bytes: int
    documents_max_image_pixels: int
    documents_body_timeout_seconds: float
    documents_global_concurrency: int
    documents_per_principal_concurrency: int
    document_job_socket_path: str
    document_archive_poll_seconds: float
    document_archive_max_attempts: int
    documents_processing_enabled: bool
    documents_safe_extraction_enabled: bool
    documents_note_proposals_enabled: bool
    documents_contact_proposals_enabled: bool
    documents_intelligence_enabled: bool
    documents_restricted_workflow_enabled: bool
    documents_restricted_security_review_id: str
    documents_restricted_recovery_attestation_path: str
    documents_artifacts_path: str
    document_process_max_attempts: int
    document_process_lease_seconds: float
    documents_watch_enabled: bool
    documents_watch_path: str
    documents_watch_owner_id: str
    documents_watch_stable_seconds: float
    documents_origin_reconciliation_enabled: bool
    documents_origin_owner_id: str
    documents_origin_reconciliation_limit: int
    documents_docling_enabled: bool
    docling_base_url: str
    docling_api_key_path: str
    docling_server_version: str
    docling_image_digest: str
    docling_timeout_seconds: float
    docling_max_response_bytes: int
    documents_paddleocr_enabled: bool
    paddleocr_base_url: str
    paddleocr_api_key_path: str
    paddleocr_server_version: str
    paddleocr_image_digest: str
    paddleocr_model_tier: str
    paddleocr_timeout_seconds: float
    paddleocr_max_response_bytes: int
    documents_paddleocr_vl_enabled: bool
    paddleocr_vl_base_url: str
    paddleocr_vl_framework_version: str
    paddleocr_vl_pipeline_version: str
    paddleocr_vl_image_digest: str
    paddleocr_vl_timeout_seconds: float
    paddleocr_vl_max_new_tokens: int
    paddleocr_vl_max_response_bytes: int
    document_gateway_base_url: str
    document_gateway_operator_key_path: str
    paperless_base_url: str
    paperless_read_token_path: str
    paperless_read_user_id_path: str
    paperless_archive_token_path: str
    paperless_api_version: int
    paperless_server_version: str
    paperless_timeout_seconds: float


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
        "qwen3.8:27b",
    ),
    main_repair_model_timeout_seconds=_as_float(
        "MAIN_REPAIR_MODEL_TIMEOUT_SECONDS",
        _as_float("MICRO_MODEL_TIMEOUT_SECONDS", 6.0),
    ),
    main_repair_model_num_ctx=max(512, _as_int("MAIN_REPAIR_MODEL_NUM_CTX", 32768)),
    main_repair_model_num_predict=max(1, _as_int("MAIN_REPAIR_MODEL_NUM_PREDICT", 512)),
    main_repair_model_think=_as_ollama_think("MAIN_REPAIR_MODEL_THINK", False),
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
    main_conversation_model_num_ctx=max(512, _as_int("MAIN_CONVERSATION_MODEL_NUM_CTX", 32768)),
    main_conversation_model_num_predict=max(1, _as_int("MAIN_CONVERSATION_MODEL_NUM_PREDICT", 1024)),
    main_conversation_model_think=_as_ollama_think("MAIN_CONVERSATION_MODEL_THINK", "low"),
    main_turn_decision_model_think=_as_ollama_think("MAIN_TURN_DECISION_MODEL_THINK", False),
    model_adaptive_token_budget_enabled=_as_bool("MODEL_ADAPTIVE_TOKEN_BUDGET_ENABLED", True),
    model_adaptive_token_max_attempts=max(
        1,
        min(_as_int("MODEL_ADAPTIVE_TOKEN_MAX_ATTEMPTS", 4), 8),
    ),
    model_adaptive_token_growth_factor=max(
        1.25,
        min(_as_float("MODEL_ADAPTIVE_TOKEN_GROWTH_FACTOR", 2.0), 4.0),
    ),
    model_adaptive_token_max_multiplier=max(
        1,
        min(_as_int("MODEL_ADAPTIVE_TOKEN_MAX_MULTIPLIER", 8), 32),
    ),
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
    web_research_decision_model_think=_as_ollama_think(
        "WEB_RESEARCH_DECISION_MODEL_THINK",
        False,
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
    discord_attachment_ingress_enabled=_as_bool(
        "DISCORD_ATTACHMENT_INGRESS_ENABLED",
        False,
    ),
    discord_attachment_ingress_base_url=os.getenv(
        "DISCORD_ATTACHMENT_INGRESS_BASE_URL",
        "http://discord-attachment-ingress:8020",
    ).rstrip("/"),
    discord_attachment_ingress_timeout_seconds=max(
        5.0,
        min(_as_float("DISCORD_ATTACHMENT_INGRESS_TIMEOUT_SECONDS", 180.0), 600.0),
    ),
    discord_attachment_max_per_message=max(
        1,
        min(_as_int("DISCORD_ATTACHMENT_MAX_PER_MESSAGE", 4), 10),
    ),
    discord_document_notifications_enabled=_as_bool(
        "DISCORD_DOCUMENT_NOTIFICATIONS_ENABLED",
        False,
    ),
    discord_document_notification_poll_seconds=max(
        1.0,
        min(_as_float("DISCORD_DOCUMENT_NOTIFICATION_POLL_SECONDS", 2.0), 60.0),
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
    email_agent_model_num_ctx=max(512, _as_int("EMAIL_AGENT_MODEL_NUM_CTX", 32768)),
    email_agent_summary_num_predict=max(1, _as_int("EMAIL_AGENT_SUMMARY_NUM_PREDICT", 1024)),
    email_agent_classifier_num_predict=max(1, _as_int("EMAIL_AGENT_CLASSIFIER_NUM_PREDICT", 256)),
    email_agent_summary_model_think=_as_ollama_think("EMAIL_AGENT_SUMMARY_MODEL_THINK", "low"),
    email_agent_classifier_model_think=_as_ollama_think(
        "EMAIL_AGENT_CLASSIFIER_MODEL_THINK",
        False,
    ),
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
        or os.getenv("MAIN_REPAIR_MODEL_NAME", "qwen3.8:27b").strip()
        or "qwen3.8:27b"
    ),
    action_ticket_review_model_timeout_seconds=max(
        1.0,
        _as_float("ACTION_TICKET_REVIEW_MODEL_TIMEOUT_SECONDS", 180.0),
    ),
    action_ticket_review_model_num_ctx=max(
        512,
        _as_int("ACTION_TICKET_REVIEW_MODEL_NUM_CTX", 32768),
    ),
    action_ticket_review_model_num_predict=max(
        1,
        _as_int("ACTION_TICKET_REVIEW_MODEL_NUM_PREDICT", 1024),
    ),
    action_ticket_review_model_think=_as_ollama_think(
        "ACTION_TICKET_REVIEW_MODEL_THINK",
        False,
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
    operator_api_key=_secret_value("JARVIS_OPERATOR_API_KEY", "JARVIS_OPERATOR_API_KEY_FILE"),
    operator_session_ttl_seconds=max(
        300,
        min(_as_int("JARVIS_OPERATOR_SESSION_TTL_SECONDS", 3600), 86400),
    ),
    turn_max_concurrency=max(1, min(_as_int("TURN_MAX_CONCURRENCY", 1), 8)),
    turn_queue_capacity=max(0, min(_as_int("TURN_QUEUE_CAPACITY", 8), 100)),
    turn_timeout_seconds=max(5.0, min(_as_float("TURN_TIMEOUT_SECONDS", 240.0), 900.0)),
    offline_mode=_as_bool("OFFLINE_MODE", False),
    documents_enabled=_as_bool("DOCUMENTS_ENABLED", False),
    documents_local_only=_as_bool("DOCUMENTS_LOCAL_ONLY", True),
    documents_database_path=os.getenv("DOCUMENTS_DATABASE_PATH", "data/documents/documents.db"),
    documents_spool_path=os.getenv("DOCUMENTS_SPOOL_PATH", "data/documents/spool"),
    documents_max_upload_bytes=max(
        1024,
        min(_as_int("DOCUMENTS_MAX_UPLOAD_BYTES", 52428800), 104857600),
    ),
    documents_max_request_overhead_bytes=max(
        65536,
        min(_as_int("DOCUMENTS_MAX_REQUEST_OVERHEAD_BYTES", 1048576), 8388608),
    ),
    documents_spool_quota_bytes=max(
        52428800,
        _as_int("DOCUMENTS_SPOOL_QUOTA_BYTES", 2147483648),
    ),
    documents_min_free_bytes=max(
        0,
        _as_int("DOCUMENTS_MIN_FREE_BYTES", 1073741824),
    ),
    documents_max_image_pixels=max(
        1000000,
        min(_as_int("DOCUMENTS_MAX_IMAGE_PIXELS", 64000000), 100000000),
    ),
    documents_body_timeout_seconds=max(
        5.0,
        min(_as_float("DOCUMENTS_BODY_TIMEOUT_SECONDS", 120.0), 600.0),
    ),
    documents_global_concurrency=max(
        1,
        min(_as_int("DOCUMENTS_GLOBAL_CONCURRENCY", 2), 16),
    ),
    documents_per_principal_concurrency=max(
        1,
        min(_as_int("DOCUMENTS_PER_PRINCIPAL_CONCURRENCY", 1), 4),
    ),
    document_job_socket_path=os.getenv("DOCUMENT_JOB_SOCKET_PATH", "/run/jarvis-documents/enqueue.sock"),
    document_archive_poll_seconds=max(
        1.0,
        min(_as_float("DOCUMENT_ARCHIVE_POLL_SECONDS", 5.0), 300.0),
    ),
    document_archive_max_attempts=max(
        3,
        min(_as_int("DOCUMENT_ARCHIVE_MAX_ATTEMPTS", 30), 100),
    ),
    documents_processing_enabled=_as_bool("DOCUMENTS_PROCESSING_ENABLED", False),
    documents_safe_extraction_enabled=_as_bool("DOCUMENTS_SAFE_EXTRACTION_ENABLED", False),
    documents_note_proposals_enabled=_as_bool("DOCUMENTS_NOTE_PROPOSALS_ENABLED", False),
    documents_contact_proposals_enabled=_as_bool("DOCUMENTS_CONTACT_PROPOSALS_ENABLED", False),
    documents_intelligence_enabled=_as_bool("DOCUMENTS_INTELLIGENCE_ENABLED", False),
    documents_restricted_workflow_enabled=_as_bool("DOCUMENTS_RESTRICTED_WORKFLOW_ENABLED", False),
    documents_restricted_security_review_id=os.getenv("DOCUMENTS_RESTRICTED_SECURITY_REVIEW_ID", ""),
    documents_restricted_recovery_attestation_path=os.getenv(
        "DOCUMENTS_RESTRICTED_RECOVERY_ATTESTATION_PATH", ""
    ),
    documents_artifacts_path=os.getenv(
        "DOCUMENTS_ARTIFACTS_PATH",
        "data/documents/artifacts",
    ),
    document_process_max_attempts=max(
        3,
        min(_as_int("DOCUMENT_PROCESS_MAX_ATTEMPTS", 60), 200),
    ),
    document_process_lease_seconds=max(
        30.0,
        min(_as_float("DOCUMENT_PROCESS_LEASE_SECONDS", 300.0), 1800.0),
    ),
    documents_watch_enabled=_as_bool("DOCUMENTS_WATCH_ENABLED", False),
    documents_watch_path=os.getenv("DOCUMENTS_WATCH_PATH", "data/documents/import"),
    documents_watch_owner_id=os.getenv("DOCUMENTS_WATCH_OWNER_ID", "").strip(),
    documents_watch_stable_seconds=max(
        0.5,
        min(_as_float("DOCUMENTS_WATCH_STABLE_SECONDS", 5.0), 300.0),
    ),
    documents_origin_reconciliation_enabled=_as_bool(
        "DOCUMENTS_ORIGIN_RECONCILIATION_ENABLED",
        False,
    ),
    documents_origin_owner_id=os.getenv("DOCUMENTS_ORIGIN_OWNER_ID", "").strip(),
    documents_origin_reconciliation_limit=max(
        1,
        min(_as_int("DOCUMENTS_ORIGIN_RECONCILIATION_LIMIT", 50), 100),
    ),
    documents_docling_enabled=_as_bool("DOCUMENTS_DOCLING_ENABLED", False),
    docling_base_url=os.getenv("DOCLING_BASE_URL", "http://docling-serve:5001").rstrip("/"),
    docling_api_key_path=os.getenv("DOCLING_API_KEY_PATH", "/run/secrets/docling_api_key"),
    docling_server_version=os.getenv("DOCLING_SERVER_VERSION", "1.30.0").strip(),
    docling_image_digest=os.getenv(
        "DOCLING_IMAGE_DIGEST",
        "sha256:0244089785d5ccb7570dfaa593cdc81ec64a1aadc63ffa9dce065064b0a6a807",
    ).strip(),
    docling_timeout_seconds=max(
        1.0,
        min(_as_float("DOCLING_TIMEOUT_SECONDS", 300.0), 900.0),
    ),
    docling_max_response_bytes=max(
        1024,
        min(_as_int("DOCLING_MAX_RESPONSE_BYTES", 67108864), 268435456),
    ),
    documents_paddleocr_enabled=_as_bool("DOCUMENTS_PADDLEOCR_ENABLED", False),
    paddleocr_base_url=os.getenv(
        "PADDLEOCR_BASE_URL",
        "http://paddleocr-serve:8030",
    ).rstrip("/"),
    paddleocr_api_key_path=os.getenv(
        "PADDLEOCR_API_KEY_PATH",
        "/run/secrets/paddleocr_api_key",
    ),
    paddleocr_server_version=os.getenv("PADDLEOCR_SERVER_VERSION", "3.7.0").strip(),
    paddleocr_image_digest=os.getenv("PADDLEOCR_IMAGE_DIGEST", "local-build-required").strip(),
    paddleocr_model_tier=os.getenv("PADDLEOCR_MODEL_TIER", "small").strip().casefold(),
    paddleocr_timeout_seconds=max(
        1.0,
        min(_as_float("PADDLEOCR_TIMEOUT_SECONDS", 300.0), 900.0),
    ),
    paddleocr_max_response_bytes=max(
        1024,
        min(_as_int("PADDLEOCR_MAX_RESPONSE_BYTES", 67108864), 268435456),
    ),
    documents_paddleocr_vl_enabled=_as_bool("DOCUMENTS_PADDLEOCR_VL_ENABLED", False),
    paddleocr_vl_base_url=os.getenv(
        "PADDLEOCR_VL_BASE_URL",
        "http://accelerator-admission:8040",
    ).rstrip("/"),
    paddleocr_vl_framework_version=os.getenv(
        "PADDLEOCR_VL_FRAMEWORK_VERSION",
        "3.6.0",
    ).strip(),
    paddleocr_vl_pipeline_version=os.getenv(
        "PADDLEOCR_VL_PIPELINE_VERSION",
        "1.6",
    ).strip(),
    paddleocr_vl_image_digest=os.getenv(
        "PADDLEOCR_VL_IMAGE_DIGEST",
        "sha256:6c735bdf9e758ffdd58ccc067db0c2d84e37e5e6a2cbd47156069d4d7ea5d709",
    ).strip(),
    paddleocr_vl_timeout_seconds=max(
        5.0,
        min(_as_float("PADDLEOCR_VL_TIMEOUT_SECONDS", 120.0), 900.0),
    ),
    paddleocr_vl_max_new_tokens=max(
        64,
        min(_as_int("PADDLEOCR_VL_MAX_NEW_TOKENS", 4096), 4096),
    ),
    paddleocr_vl_max_response_bytes=max(
        1024,
        min(_as_int("PADDLEOCR_VL_MAX_RESPONSE_BYTES", 16777216), 67108864),
    ),
    document_gateway_base_url=os.getenv(
        "DOCUMENT_GATEWAY_BASE_URL",
        "http://document-gateway:8010",
    ).rstrip("/"),
    document_gateway_operator_key_path=os.getenv(
        "DOCUMENT_GATEWAY_OPERATOR_KEY_PATH",
        "/run/secrets/jarvis_operator_api_key",
    ),
    paperless_base_url=os.getenv("PAPERLESS_BASE_URL", "http://paperless-webserver:8000").rstrip("/"),
    paperless_read_token_path=os.getenv(
        "PAPERLESS_READ_TOKEN_PATH",
        "/run/secrets/paperless_read_token",
    ),
    paperless_read_user_id_path=os.getenv(
        "PAPERLESS_READ_USER_ID_PATH",
        "/run/secrets/paperless_read_user_id",
    ),
    paperless_archive_token_path=os.getenv(
        "PAPERLESS_ARCHIVE_TOKEN_PATH",
        "/run/secrets/paperless_archive_token",
    ),
    paperless_api_version=max(1, min(_as_int("PAPERLESS_API_VERSION", 10), 99)),
    paperless_server_version=os.getenv("PAPERLESS_SERVER_VERSION", "3.0.5").strip(),
    paperless_timeout_seconds=max(
        1.0,
        min(_as_float("PAPERLESS_TIMEOUT_SECONDS", 60.0), 300.0),
    ),
)
