from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.accelerator.client import accelerator_request_headers
from app.config import settings
from app.core.main_backend import OllamaMainConversationBackend, OllamaMainRepairBackend
from app.core.micro_backend import OllamaMicroInferenceBackend
from app.core.ollama_observability import AdaptiveTokenBudgetPolicy
from app.core.main_jarvis import MainJarvis
from app.core.micro_jarvis import MicroJarvis
from app.core.router import JarvisRouter
from app.core.session_store import SessionStore
from app.core.state_machine import RuntimePowerController
from app.db.sqlite_store import SQLiteStore
from app.db.repositories import RuntimeStateRepository, ScheduledJobsRepository, SkillCatalogRepository
from app.memory.composite_memory_store import CompositeMemoryStore
from app.memory.markdown_memory_store import MarkdownMemoryStore
from app.memory.sqlite_memory_store import SQLiteMemoryStore
from app.services.event_log import EventLogService
from app.services.google.calendar_live import GoogleCalendarLiveService
from app.services.google.calendar_inbox import GoogleCalendarInboxProvider
from app.services.google.gmail_gateway import GoogleGmailReadOnlyGateway
from app.services.google.gmail_mime import GmailMimeParser
from app.services.memory_service import MemoryService
from app.services.durable_write_service import DurableWriteConfig, DurableWriteService
from app.services.turn_service import TurnService
from app.services.identity_service import ExternalIdentityService
from app.services.scheduled_jobs_service import ScheduledJobsService
from app.skills.domains.conversation.history_service import ConversationHistoryService
from app.skills.domains.conversation.storage import ConversationSQLiteStorage
from app.skills.domains.private_notes.service import (
    PrivateNotesDigestCompiler,
    PrivateNotesDigestService,
)
from app.skills.domains.private_notes.storage import PrivateNotesSQLiteStorage
from app.skills.domains.calendar_inbox.service import CalendarInboxConfig, CalendarInboxService
from app.skills.domains.calendar_inbox.storage import CalendarInboxSQLiteStorage
from app.skills.domains.email_agent.classification import (
    EmailClassifier,
    OllamaEmailModelClassifier,
)
from app.skills.domains.email_agent.config import EmailAgentPermissions
from app.skills.domains.email_agent.service import EmailAgentRuntimeConfig, EmailAgentService
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage
from app.skills.domains.email_agent.summarization import OllamaEmailSummaryCompiler
from app.skills.registry_service import SkillRegistryService
from app.tools.calendar_service import CalendarService
from app.tools.home_service import HomeService
from app.tools.lists_service import ListsService
from app.tickets.repository import TicketRepository
from app.tickets.service import ActionTicketService
from app.tickets.context_builder import ReviewContextBuilder
from app.tickets.remediation_policy import RemediationPolicy
from app.tickets.remediation_service import RemediationService
from app.tickets.review_backend import EvidenceOnlyReviewBackend, OllamaTicketReviewBackend
from app.tickets.review_service import TicketReviewService
from app.tickets.verifier_registry import VerifierRegistry
from app.tickets.verifiers.lists import ListsSourceVerifier
from app.tickets.verifiers.home import SimulatedHomeSourceVerifier
from app.tickets.verifiers.calendar import GoogleCalendarSourceVerifier
from app.integrations.plane.client import PlaneClient
from app.integrations.plane.sync_service import PlaneSyncService
from app.integrations.document_gateway.client import DocumentGatewayClient
from app.integrations.local_service import validate_local_http_service_url
from app.reviews.repository import HumanReviewRepository
from app.reviews.service import HumanReviewService
from app.skills.domains.documents.query_service import DocumentQueryService
from app.skills.domains.documents.review_corrections import DocumentFieldReviewCoordinator
from app.provenance.repository import ProvenanceRepository
from app.services.document_proposal_execution_service import DocumentProposalExecutionService
from app.services.model_compute_budget_service import ModelComputeBudgetNotificationService
from app.research.decision_backend import OllamaResearchDecisionBackend
from app.research.searxng import SearxngSearchProvider
from app.research.service import WebResearchService


def _is_local_model_url(value: str) -> bool:
    try:
        validate_local_http_service_url(value, label="Local model URL")
        return True
    except ValueError:
        return False

sqlite_store = SQLiteStore(database_path=settings.database_path)
runtime_state_repository = RuntimeStateRepository(sqlite_store)
skill_catalog_repository = SkillCatalogRepository(sqlite_store)
scheduled_jobs_repository = ScheduledJobsRepository(sqlite_store)
event_log = EventLogService(persistence=runtime_state_repository)
model_compute_budget_notifications: ModelComputeBudgetNotificationService | None = None
adaptive_token_budget_policy = AdaptiveTokenBudgetPolicy(
    enabled=settings.model_adaptive_token_budget_enabled,
    max_attempts=settings.model_adaptive_token_max_attempts,
    growth_factor=settings.model_adaptive_token_growth_factor,
    max_predict_multiplier=settings.model_adaptive_token_max_multiplier,
)


def _record_ollama_call(metrics: dict[str, Any]) -> None:
    event_log.record(
        event_type="model.ollama_call",
        session_id="system:model-runtime",
        payload=metrics,
    )
    if metrics.get("escalated_to_num_predict") is not None:
        event_log.record(
            event_type="model.compute_budget.escalated",
            session_id="system:model-runtime",
            payload={
                "lane": metrics.get("lane"),
                "model": metrics.get("model"),
                "reason": metrics.get("escalation_reason"),
                "attempt": metrics.get("attempt"),
                "from_num_predict": metrics.get("requested_num_predict"),
                "to_num_predict": metrics.get("escalated_to_num_predict"),
                "prompt_chars": metrics.get("prompt_chars"),
                "estimated_prompt_tokens": metrics.get("estimated_prompt_tokens"),
                "prompt_eval_count": metrics.get("prompt_eval_count"),
                "eval_count": metrics.get("eval_count"),
                "done_reason": metrics.get("done_reason"),
                "total_duration_ms": metrics.get("total_duration_ms"),
                "call_id": metrics.get("call_id"),
            },
        )
        if (
            model_compute_budget_notifications is not None
            and int(metrics.get("attempt") or 1) == 1
        ):
            try:
                model_compute_budget_notifications.enqueue_escalation(metrics)
            except Exception as exc:
                event_log.record(
                    event_type="model.compute_budget.notice_enqueue_failed",
                    session_id="system:model-runtime",
                    payload={
                        "lane": metrics.get("lane"),
                        "model": metrics.get("model"),
                        "error_type": type(exc).__name__,
                    },
                )
    if metrics.get("failed_loop") is True:
        event_log.record(
            event_type="model.compute_budget.failed_loop",
            session_id="system:model-runtime",
            payload={
                "lane": metrics.get("lane"),
                "model": metrics.get("model"),
                "reason": metrics.get("done_reason") or metrics.get("escalation_reason"),
                "attempt": metrics.get("attempt"),
                "final_num_predict": metrics.get("requested_num_predict"),
                "call_id": metrics.get("call_id"),
            },
        )
        if model_compute_budget_notifications is not None:
            try:
                model_compute_budget_notifications.enqueue_failed_loop(metrics)
            except Exception as exc:
                event_log.record(
                    event_type="model.compute_budget.notice_enqueue_failed",
                    session_id="system:model-runtime",
                    payload={
                        "lane": metrics.get("lane"),
                        "model": metrics.get("model"),
                        "notice_kind": "failed_loop",
                        "error_type": type(exc).__name__,
                    },
                )


ticket_repository = TicketRepository(database_path=sqlite_store.database_path)
job_repository = ticket_repository.job_repository
model_compute_budget_notifications = ModelComputeBudgetNotificationService(
    repository=job_repository,
    worker_id="model-compute-budget-enqueuer",
)
human_review_repository = HumanReviewRepository(database_path=settings.database_path)
human_review_service = HumanReviewService(human_review_repository)
action_ticket_service = ActionTicketService(
    repository=ticket_repository,
    enabled=settings.action_tickets_enabled,
    review_delay_seconds=settings.action_ticket_review_delay_seconds,
    review_max_attempts=settings.action_ticket_review_max_attempts,
    plane_enabled=settings.plane_enabled,
    execution_watchdog_seconds=settings.action_ticket_execution_watchdog_seconds,
)
skill_registry = SkillRegistryService(sqlite_store=skill_catalog_repository)
skill_registry.seed_defaults()
skill_registry.sync_skills_from_markdown()
external_identity_service = ExternalIdentityService(
    repository=ticket_repository,
    skill_registry=skill_registry,
)
micro_backend = None
if settings.micro_model_enabled and settings.micro_model_provider.strip().lower() == "ollama":
    micro_backend = OllamaMicroInferenceBackend(
        base_url=settings.local_model_url,
        model=settings.micro_model_name,
        timeout_seconds=settings.micro_model_timeout_seconds,
        skill_registry=skill_registry,
        num_ctx=settings.micro_model_num_ctx,
        num_predict=settings.micro_model_num_predict,
        metrics_callback=_record_ollama_call,
        adaptive_policy=adaptive_token_budget_policy,
    )
main_repair_backend = None
if settings.main_repair_model_enabled and settings.main_repair_model_provider.strip().lower() == "ollama":
    main_repair_backend = OllamaMainRepairBackend(
        base_url=settings.local_model_url,
        model=settings.main_repair_model_name,
        timeout_seconds=settings.main_repair_model_timeout_seconds,
        keep_alive_seconds=settings.larger_model_micro_only_window_seconds,
        skill_registry=skill_registry,
        num_ctx=settings.main_repair_model_num_ctx,
        num_predict=settings.main_repair_model_num_predict,
        think=settings.main_repair_model_think,
        metrics_callback=_record_ollama_call,
        adaptive_policy=adaptive_token_budget_policy,
    )
main_conversation_backend = None
conversation_model_name = None
if settings.main_repair_model_enabled and settings.main_repair_model_provider.strip().lower() == "ollama":
    # Keep main conversation and main repair on the same model family for consistent reasoning quality.
    conversation_model_name = settings.main_repair_model_name
    conversation_timeout = max(settings.main_conversation_model_timeout_seconds, 8.0)
elif settings.micro_model_enabled and settings.micro_model_provider.strip().lower() == "ollama":
    conversation_model_name = settings.micro_model_name
    conversation_timeout = max(settings.micro_model_timeout_seconds, 8.0)
if conversation_model_name:
    conversation_keep_alive = None
    if conversation_model_name == settings.main_repair_model_name:
        conversation_keep_alive = settings.larger_model_micro_only_window_seconds
    main_conversation_backend = OllamaMainConversationBackend(
        base_url=settings.local_model_url,
        model=conversation_model_name,
        timeout_seconds=conversation_timeout,
        keep_alive_seconds=conversation_keep_alive,
        skill_registry=skill_registry,
        num_ctx=settings.main_conversation_model_num_ctx,
        num_predict=settings.main_conversation_model_num_predict,
        think=settings.main_conversation_model_think,
        turn_decision_think=settings.main_turn_decision_model_think,
        metrics_callback=_record_ollama_call,
        adaptive_policy=adaptive_token_budget_policy,
    )

web_research_service = None
research_decision_backend = None
if settings.web_research_enabled and settings.web_research_provider == "searxng":
    if conversation_model_name:
        research_decision_backend = OllamaResearchDecisionBackend(
            base_url=settings.local_model_url,
            model=conversation_model_name,
            timeout_seconds=settings.web_research_decision_timeout_seconds,
            keep_alive_seconds=(
                settings.larger_model_micro_only_window_seconds
                if conversation_model_name == settings.main_repair_model_name
                else None
            ),
            num_ctx=settings.web_research_decision_model_num_ctx,
            num_predict=settings.web_research_decision_model_num_predict,
            think=settings.web_research_decision_model_think,
            metrics_callback=_record_ollama_call,
            adaptive_policy=adaptive_token_budget_policy,
        )
    web_research_service = WebResearchService(
        provider=SearxngSearchProvider(
            base_url=settings.web_research_base_url,
            timeout_seconds=settings.web_research_timeout_seconds,
        ),
        decision_backend=research_decision_backend,
        enabled=True,
        max_results=settings.web_research_max_results,
        safe_search=settings.web_research_safe_search,
        children_enabled=settings.web_research_children_enabled,
        cache_ttl_seconds=settings.web_research_cache_ttl_seconds,
    )

micro_jarvis = MicroJarvis(
    backend=micro_backend,
    fast_confidence_threshold=settings.micro_fast_confidence_threshold,
    heuristic_fallback_enabled=settings.micro_model_heuristic_fallback_enabled,
)
main_jarvis = MainJarvis(
    repair_backend=main_repair_backend,
    conversation_backend=main_conversation_backend,
    research_service=web_research_service,
)
session_store = SessionStore(
    persistence=runtime_state_repository,
    channel_idle_timeout_seconds=settings.channel_session_idle_timeout_seconds,
)
runtime_power = RuntimePowerController(
    larger_model_micro_only_window_seconds=settings.larger_model_micro_only_window_seconds
)
private_notes_storage = PrivateNotesSQLiteStorage(database_path=settings.database_path)
private_notes_service = PrivateNotesDigestService(
    storage=private_notes_storage,
    compiler=PrivateNotesDigestCompiler(conversation_backend=main_conversation_backend),
    event_log=event_log,
)
scheduled_jobs = ScheduledJobsService(
    sqlite_store=scheduled_jobs_repository,
    skill_registry=skill_registry,
    event_log=event_log,
)
scheduled_jobs.seed_defaults(
    ensure_compiled_artifacts=settings.skill_artifact_auto_compile_enabled,
)
if settings.skill_artifact_auto_compile_enabled:
    scheduled_jobs.run_trigger(
        cron_expr=ScheduledJobsService.MAIN_IDLE_TRIGGER,
        reason="startup_compile_if_stale",
    )


def _on_model_runtime_transition(previous_active: bool, current_active: bool) -> None:
    if settings.skill_artifact_auto_compile_enabled:
        scheduled_jobs.handle_runtime_transition(
            previous_active=previous_active,
            current_active=current_active,
        )
    if previous_active and not current_active:
        event_log.record(
            event_type="runtime.main_idle_handoff",
            session_id="system:runtime",
            payload={
                "from": "main",
                "to": "micro",
                "reason": "main_idle_timeout",
                "timeout_seconds": settings.larger_model_micro_only_window_seconds,
            },
        )
    elif (not previous_active) and current_active:
        event_log.record(
            event_type="runtime.main_runtime_warm",
            session_id="system:runtime",
            payload={
                "from": "micro",
                "to": "main",
                "reason": "main_labeled_task_detected",
                "timeout_seconds": settings.larger_model_micro_only_window_seconds,
            },
        )


runtime_power.set_model_runtime_transition_hook(_on_model_runtime_transition)
memory_store_chain = [SQLiteMemoryStore(sqlite_store)]
if settings.memory_mode.strip().lower() in {"hybrid", "sqlite+markdown"}:
    memory_store_chain.append(MarkdownMemoryStore(base_dir=settings.memory_markdown_path))
memory_service = MemoryService(store=CompositeMemoryStore(memory_store_chain))
durable_write_service = DurableWriteService(
    repository=ticket_repository,
    memory_service=memory_service,
    config=DurableWriteConfig(),
)
conversation_history_base_dir = (
    Path(settings.database_path).expanduser().resolve().parent / "skill_history" / "conversation"
)
conversation_storage = ConversationSQLiteStorage(sqlite_store=sqlite_store)
conversation_history_service = ConversationHistoryService(
    persistence=conversation_storage,
    base_dir=str(conversation_history_base_dir),
)
lists_service = ListsService(
    sqlite_store=sqlite_store,
)
google_calendar_live = (
    GoogleCalendarLiveService(settings.google_permissions_path)
    if settings.calendar_google_enabled
    else None
)
calendar_service = CalendarService(google_live=google_calendar_live)
calendar_inbox_storage = None
calendar_inbox_service = None
if settings.calendar_inbox_enabled:
    if google_calendar_live is None:
        raise RuntimeError("CALENDAR_INBOX_ENABLED=true requires CALENDAR_GOOGLE_ENABLED=true.")
    calendar_inbox_storage = CalendarInboxSQLiteStorage(database_path=settings.database_path)
    calendar_inbox_service = CalendarInboxService(
        storage=calendar_inbox_storage,
        provider=GoogleCalendarInboxProvider(google_calendar_live),
        config=CalendarInboxConfig(
            timezone_name=settings.calendar_inbox_timezone,
            start_hour=settings.calendar_inbox_start_hour,
            end_hour=settings.calendar_inbox_end_hour,
            max_messages_per_run=settings.calendar_inbox_max_messages_per_run,
            lookback_days=settings.calendar_inbox_lookback_days,
            allowed_sender_emails=tuple(settings.calendar_inbox_allowed_sender_emails),
        ),
        event_log=event_log,
    )
email_agent_storage = None
email_agent_service = None
email_summary_compiler = None
email_model_classifier = None
if settings.email_agent_enabled:
    if not settings.email_agent_label_shadow_enabled:
        raise RuntimeError(
            "EMAIL_AGENT_LABEL_SHADOW_ENABLED must remain true in the read-only build."
        )
    if settings.email_agent_attachment_extraction_enabled:
        raise RuntimeError(
            "EMAIL_AGENT_ATTACHMENT_EXTRACTION_ENABLED is not available in the metadata-only build."
        )
    email_permissions = EmailAgentPermissions.load(settings.email_agent_permissions_path)
    email_google_live = google_calendar_live or GoogleCalendarLiveService(settings.google_permissions_path)
    email_gateway = GoogleGmailReadOnlyGateway.from_calendar_live(
        calendar_live=email_google_live,
        account_key=email_permissions.google_account_key,
        expected_profile_email=email_permissions.gmail_profile,
    )
    email_agent_storage = EmailAgentSQLiteStorage(database_path=settings.database_path)
    if (
        settings.email_agent_summary_model_provider == "ollama"
        and conversation_model_name
    ):
        if not settings.email_agent_allow_remote_model and not _is_local_model_url(settings.local_model_url):
            raise RuntimeError(
                "Email summaries require a local Ollama URL unless EMAIL_AGENT_ALLOW_REMOTE_MODEL=true."
            )
        email_summary_compiler = OllamaEmailSummaryCompiler(
            base_url=settings.local_model_url,
            model=conversation_model_name,
            timeout_seconds=settings.main_conversation_model_timeout_seconds,
            num_ctx=settings.email_agent_model_num_ctx,
            num_predict=settings.email_agent_summary_num_predict,
            think=settings.email_agent_summary_model_think,
            metrics_callback=_record_ollama_call,
            adaptive_policy=adaptive_token_budget_policy,
        )
        email_model_classifier = OllamaEmailModelClassifier(
            base_url=settings.local_model_url,
            model=conversation_model_name,
            timeout_seconds=settings.main_conversation_model_timeout_seconds,
            num_ctx=settings.email_agent_model_num_ctx,
            num_predict=settings.email_agent_classifier_num_predict,
            think=settings.email_agent_classifier_model_think,
            metrics_callback=_record_ollama_call,
            adaptive_policy=adaptive_token_budget_policy,
        )
    email_agent_service = EmailAgentService(
        storage=email_agent_storage,
        gateway=email_gateway,
        permissions=email_permissions,
        mime_parser=GmailMimeParser(max_body_bytes=settings.email_agent_max_body_bytes),
        classifier=EmailClassifier(
            permissions=email_permissions,
            model_classifier=email_model_classifier,
        ),
        summary_compiler=email_summary_compiler,
        config=EmailAgentRuntimeConfig(
            timezone_name=settings.email_agent_timezone,
            sync_enabled=settings.email_agent_sync_enabled,
            sync_interval_seconds=settings.email_agent_sync_interval_seconds,
            on_demand_stale_seconds=settings.email_agent_on_demand_stale_seconds,
            max_history_pages=settings.email_agent_max_history_pages,
            max_messages_per_run=settings.email_agent_max_messages_per_run,
            max_interactive_messages=settings.email_agent_max_interactive_messages,
            reference_retention_hours=settings.email_agent_reference_retention_hours,
            allow_historical_backfill=settings.email_agent_allow_historical_backfill,
            label_writes_enabled=settings.email_agent_label_writes_enabled,
            spam_writes_enabled=settings.email_agent_spam_writes_enabled,
            spam_max_operations_per_command=min(
                5,
                settings.email_agent_spam_worker_batch_size,
            ),
            max_provider_attempts=settings.email_agent_spam_max_attempts,
        ),
        event_log=event_log,
    )
home_service = HomeService(
    sqlite_store=sqlite_store,
    default_switch_names=settings.house_switch_names,
)

document_gateway_client = None
documents_service = None
if settings.documents_enabled:
    document_gateway_client = DocumentGatewayClient(
        base_url=settings.document_gateway_base_url,
        operator_key_path=settings.document_gateway_operator_key_path,
        timeout_seconds=min(settings.paperless_timeout_seconds, 30.0),
    )
    documents_service = DocumentQueryService(
        gateway=document_gateway_client,
        reviews=human_review_service,
        field_reviews=DocumentFieldReviewCoordinator(
            gateway=document_gateway_client,
            reviews=human_review_service,
        ),
    )

ticket_verifier_registry = VerifierRegistry()
ticket_verifier_registry.register(ListsSourceVerifier(lists_service=lists_service))
ticket_verifier_registry.register(SimulatedHomeSourceVerifier(home_service=home_service))
ticket_verifier_registry.register(GoogleCalendarSourceVerifier(calendar_service=calendar_service))
ticket_review_context_builder = ReviewContextBuilder(
    repository=ticket_repository,
    max_chars=settings.action_ticket_review_context_max_chars,
)
if settings.action_ticket_review_model_provider.strip().lower() == "ollama":
    ticket_review_backend = OllamaTicketReviewBackend(
        base_url=settings.local_model_url,
        model=settings.action_ticket_review_model_name,
        timeout_seconds=settings.action_ticket_review_model_timeout_seconds,
        num_ctx=settings.action_ticket_review_model_num_ctx,
        num_predict=settings.action_ticket_review_model_num_predict,
        think=settings.action_ticket_review_model_think,
        metrics_callback=_record_ollama_call,
        adaptive_policy=adaptive_token_budget_policy,
    )
else:
    ticket_review_backend = EvidenceOnlyReviewBackend()
ticket_remediation_policy = RemediationPolicy(
    max_generation=settings.action_ticket_remediation_max_generation,
)
ticket_remediation_service = RemediationService(
    repository=ticket_repository,
    lists_service=lists_service,
    review_delay_seconds=settings.action_ticket_review_delay_seconds,
    review_max_attempts=settings.action_ticket_review_max_attempts,
    plane_enabled=settings.plane_enabled,
)
ticket_review_service = TicketReviewService(
    repository=ticket_repository,
    verifier_registry=ticket_verifier_registry,
    context_builder=ticket_review_context_builder,
    review_backend=ticket_review_backend,
    remediation_policy=ticket_remediation_policy,
    remediation_service=ticket_remediation_service,
    auto_remediation_enabled=settings.action_ticket_auto_remediation_enabled,
    plane_enabled=settings.plane_enabled,
)

plane_client = None
plane_sync_service = None
if settings.plane_enabled:
    plane_client = PlaneClient(
        base_url=settings.plane_api_base_url,
        api_key=settings.plane_api_key,
        workspace_slug=settings.plane_workspace_slug,
        project_id=settings.plane_project_id,
        timeout_seconds=settings.plane_api_timeout_seconds,
    )
    plane_sync_service = PlaneSyncService(
        repository=ticket_repository,
        client=plane_client,
        sync_raw_transcript=settings.plane_sync_raw_transcript,
    )

router = JarvisRouter(
    micro_jarvis=micro_jarvis,
    main_jarvis=main_jarvis,
    session_store=session_store,
    runtime_power=runtime_power,
    event_log=event_log,
    memory_service=memory_service,
    conversation_history_service=conversation_history_service,
    lists_service=lists_service,
    calendar_service=calendar_service,
    home_service=home_service,
    skill_registry=skill_registry,
    agent_loop_max_steps=settings.main_agent_loop_max_steps,
    agent_loop_max_failures=settings.main_agent_loop_max_failures,
    agent_loop_context_max_chars=settings.main_agent_loop_context_max_chars,
    agent_loop_auto_approve_actions=settings.main_agent_loop_auto_approve_actions,
    main_agent_content_policy_enabled=settings.main_agent_content_policy_enabled,
    main_agent_content_policy_children_only=settings.main_agent_content_policy_children_only,
    main_agent_content_policy_blocked_patterns=settings.main_agent_content_policy_blocked_patterns,
    main_agent_token_session_enabled=settings.main_agent_token_session_enabled,
    main_agent_token_session_max_turns=settings.main_agent_token_session_max_turns,
    main_conversational_confidence_threshold=settings.main_conversational_confidence_threshold,
    main_low_confidence_floor=settings.main_low_confidence_floor,
    main_high_risk_confidence_threshold=settings.main_high_risk_confidence_threshold,
    main_sticky_followup_turns=settings.main_sticky_followup_turns,
    main_pending_clarification_heuristic_fallback_enabled=settings.main_pending_clarification_heuristic_fallback_enabled,
    action_ticket_service=action_ticket_service,
    identity_service=external_identity_service,
    email_agent_service=email_agent_service,
    documents_service=documents_service,
    durable_write_service=durable_write_service,
)
action_execution_service = router.action_execution_service
provenance_repository = ProvenanceRepository(settings.database_path)
document_proposal_execution_service = (
    DocumentProposalExecutionService(
        gateway=document_gateway_client,
        reviews=human_review_repository,
        actions=action_execution_service,
        provenance=provenance_repository,
    )
    if document_gateway_client is not None
    else None
)
turn_finalizer = router.turn_finalizer
turn_service = TurnService(
    router=router,
    max_concurrency=settings.turn_max_concurrency,
    queue_capacity=settings.turn_queue_capacity,
    timeout_seconds=settings.turn_timeout_seconds,
)


def model_backends_status() -> dict[str, Any]:
    micro_ollama_configured = settings.micro_model_enabled and settings.micro_model_provider.strip().lower() == "ollama"
    main_ollama_configured = (
        settings.main_repair_model_enabled and settings.main_repair_model_provider.strip().lower() == "ollama"
    )
    ollama_reachable: bool | None = None
    if micro_ollama_configured or main_ollama_configured:
        try:
            response = httpx.get(
                f"{settings.local_model_url.rstrip('/')}/api/tags",
                headers=accelerator_request_headers("runtime_health"),
                timeout=1.0,
            )
            ollama_reachable = response.status_code < 500
        except Exception:
            ollama_reachable = False

    def _lane_status(backend: Any) -> dict[str, Any] | None:
        status_method = getattr(backend, "status", None)
        if not callable(status_method):
            return None
        value = status_method()
        return value if isinstance(value, dict) else None

    return {
        "micro_model_enabled": settings.micro_model_enabled,
        "main_repair_model_enabled": settings.main_repair_model_enabled,
        "main_conversation_model_enabled": conversation_model_name is not None,
        "micro_provider": settings.micro_model_provider,
        "main_repair_provider": settings.main_repair_model_provider,
        "micro_model_name": settings.micro_model_name,
        "main_repair_model_name": settings.main_repair_model_name,
        "main_conversation_model_name": conversation_model_name,
        "main_conversation_model_timeout_seconds": settings.main_conversation_model_timeout_seconds,
        "main_agent_loop_context_max_chars": settings.main_agent_loop_context_max_chars,
        "ollama_lanes": {
            "micro": _lane_status(micro_backend),
            "main_repair": _lane_status(main_repair_backend),
            "main_conversation": _lane_status(main_conversation_backend),
            "research_decision": _lane_status(research_decision_backend),
            "email_summary": _lane_status(email_summary_compiler),
            "email_classifier": _lane_status(email_model_classifier),
            "action_ticket_review": _lane_status(ticket_review_backend),
        },
        "larger_model_micro_only_window_seconds": settings.larger_model_micro_only_window_seconds,
        "skill_artifact_auto_compile_enabled": settings.skill_artifact_auto_compile_enabled,
        "larger_model_runtime": runtime_power.model_runtime_status(),
        "micro_heuristic_fallback_enabled": settings.micro_model_heuristic_fallback_enabled,
        "main_agent_content_policy_enabled": settings.main_agent_content_policy_enabled,
        "main_agent_content_policy_children_only": settings.main_agent_content_policy_children_only,
        "main_agent_token_session_enabled": settings.main_agent_token_session_enabled,
        "main_agent_token_session_max_turns": settings.main_agent_token_session_max_turns,
        "main_conversational_confidence_threshold": settings.main_conversational_confidence_threshold,
        "main_low_confidence_floor": settings.main_low_confidence_floor,
        "main_high_risk_confidence_threshold": settings.main_high_risk_confidence_threshold,
        "main_sticky_followup_turns": settings.main_sticky_followup_turns,
        "main_pending_clarification_heuristic_fallback_enabled": (
            settings.main_pending_clarification_heuristic_fallback_enabled
        ),
        "web_research": (
            web_research_service.status()
            if web_research_service is not None
            else {
                "enabled": settings.web_research_enabled,
                "provider": settings.web_research_provider,
                "configured": False,
            }
        ),
        "ollama_reachable": ollama_reachable,
        "action_tickets_enabled": settings.action_tickets_enabled,
        "action_ticket_review_enabled": settings.action_ticket_review_enabled,
        "plane_enabled": settings.plane_enabled,
    }


def reset_runtime(*, hard_clear: bool = False) -> None:
    session_store.reset()
    event_log.reset()
    if hard_clear:
        sqlite_store.clear_all()
        lists_service.reset()
        calendar_service.reset()
        home_service.reset()
        for switch_name in settings.house_switch_names:
            home_service.ensure_switch(name=switch_name, room_name=None, default_state="off")
    runtime_power.wake()
    runtime_power.reset_model_activity()
