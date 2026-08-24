from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sqlite3
import stat
import sys
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DISCORD_EXAMPLE_IDS = {111111111111111111, 222222222222222222}
OLLAMA_PROBE_NUM_PREDICT = 512
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True)
class CheckResult:
    level: str
    name: str
    detail: str


class InstallChecks:
    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def pass_(self, name: str, detail: str) -> None:
        self.results.append(CheckResult("PASS", name, detail))

    def warn(self, name: str, detail: str) -> None:
        self.results.append(CheckResult("WARN", name, detail))

    def fail(self, name: str, detail: str) -> None:
        self.results.append(CheckResult("FAIL", name, detail))

    @property
    def failed(self) -> bool:
        return any(result.level == "FAIL" for result in self.results)

    @property
    def warned(self) -> bool:
        return any(result.level == "WARN" for result in self.results)


def canonical_model_name(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if normalized and ":" not in normalized:
        return f"{normalized}:latest"
    return normalized


def ollama_model_is_present(configured_name: str, available_names: Iterable[str]) -> bool:
    target = canonical_model_name(configured_name)
    return any(canonical_model_name(name) == target for name in available_names)


def configured_model_names(settings: Any) -> list[str]:
    names: list[str] = []
    if settings.micro_model_enabled:
        names.append(str(settings.micro_model_name).strip())
    if settings.main_repair_model_enabled:
        names.append(str(settings.main_repair_model_name).strip())
    if bool(getattr(settings, "action_ticket_review_enabled", False)):
        names.append(str(getattr(settings, "action_ticket_review_model_name", "")).strip())
    return list(dict.fromkeys(name for name in names if name))


def _positive_discord_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    return parsed if parsed > 0 else None


def discord_policy_has_allow_scope(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    defaults = payload.get("defaults")
    if isinstance(defaults, dict):
        for key in (
            "allowed_guild_ids",
            "allowed_channel_ids",
            "allowed_role_ids",
            "allowed_user_ids",
        ):
            value = defaults.get(key)
            if isinstance(value, list) and any(_positive_discord_id(item) for item in value):
                return True
    guilds = payload.get("guilds")
    if isinstance(guilds, list):
        for row in guilds:
            if isinstance(row, dict) and _positive_discord_id(row.get("guild_id")):
                return True
    return False


def discord_policy_uses_example_ids(payload: Any) -> bool:
    if isinstance(payload, dict):
        return any(discord_policy_uses_example_ids(value) for value in payload.values())
    if isinstance(payload, list):
        return any(discord_policy_uses_example_ids(value) for value in payload)
    try:
        return int(str(payload).strip()) in DISCORD_EXAMPLE_IDS
    except (TypeError, ValueError):
        return False


def _resolve_from_repo(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _request_json(
    url: str,
    *,
    timeout_seconds: float,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    request_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = None
    headers: dict[str, str] = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    headers.update(request_headers or {})
    request = Request(url=url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8")
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("response was not a JSON object")
    return loaded


def _request_text(url: str, *, timeout_seconds: float) -> str:
    request = Request(url=url, headers={"Accept": "text/html"}, method="GET")
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="replace")


def _network_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, URLError):
        return str(exc.reason)
    return str(exc) or exc.__class__.__name__


def _check_platform(checks: InstallChecks) -> None:
    if sys.version_info >= (3, 11):
        checks.pass_("python", platform.python_version())
    else:
        checks.fail("python", f"{platform.python_version()} found; Python 3.11+ is required")

    if platform.system() != "Linux":
        checks.warn("operating_system", f"{platform.system()} host; Ubuntu checks were skipped")
        return

    os_release_path = Path("/etc/os-release")
    try:
        values: dict[str, str] = {}
        for raw_line in os_release_path.read_text(encoding="utf-8").splitlines():
            if "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            values[key] = value.strip().strip('"')
        distro = values.get("ID", "unknown")
        version = values.get("VERSION_ID", "unknown")
    except OSError as exc:
        checks.warn("operating_system", f"could not read /etc/os-release: {exc}")
        return

    if distro == "ubuntu" and version == "24.04":
        checks.pass_("operating_system", "Ubuntu 24.04")
    else:
        checks.warn("operating_system", f"{distro} {version}; deployment assets target Ubuntu 24.04")


def _check_dependencies(checks: InstallChecks) -> None:
    modules = ("fastapi", "uvicorn", "pydantic", "httpx", "discord", "yaml")
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    if missing:
        checks.fail("python_dependencies", f"missing: {', '.join(missing)}")
    else:
        checks.pass_("python_dependencies", "runtime imports are installed")


def _check_skill_artifacts(checks: InstallChecks) -> None:
    artifacts = (
        REPO_ROOT / "app" / "prompts" / "skills" / "critical_skills.md",
        REPO_ROOT / "app" / "prompts" / "micro_jarvis_skills.md",
    )
    critical_text = ""
    for artifact_path in artifacts:
        metadata_path = artifact_path.with_name(f"{artifact_path.name}.meta.json")
        if not artifact_path.is_file() or artifact_path.stat().st_size == 0:
            checks.fail("skill_artifacts", f"missing or empty: {artifact_path}")
            continue
        if not metadata_path.is_file():
            checks.fail("skill_artifacts", f"missing metadata: {metadata_path}")
            continue
        try:
            artifact_text = artifact_path.read_text(encoding="utf-8")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            checks.fail("skill_artifacts", f"could not read {artifact_path}: {exc}")
            continue
        if not isinstance(metadata, dict):
            checks.fail("skill_artifacts", f"metadata is not an object: {metadata_path}")
            continue
        expected_hash = str(metadata.get("content_hash") or "").strip().lower()
        actual_hash = hashlib.sha256(artifact_text.encode("utf-8")).hexdigest()
        if not expected_hash or actual_hash != expected_hash:
            checks.fail("skill_artifacts", f"content hash mismatch: {artifact_path}")
            continue
        checks.pass_("skill_artifacts", f"content hash verified: {artifact_path.name}")
        if artifact_path.name == "critical_skills.md":
            critical_text = artifact_text

    if not critical_text:
        return
    referenced_paths = set(
        re.findall(r"^- markdown_path: `([^`]+)`\s*$", critical_text, flags=re.MULTILINE)
    )
    if not referenced_paths:
        checks.fail("skill_artifacts", "critical skill artifact contains no source references")
        return
    for source_path_text in sorted(referenced_paths):
        source_path = _resolve_from_repo(source_path_text)
        try:
            source_path.relative_to(REPO_ROOT)
        except ValueError:
            checks.fail("skill_artifacts", f"source path leaves repository: {source_path_text}")
            continue
        try:
            source_text = source_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            checks.fail("skill_artifacts", f"could not read source {source_path_text}: {exc}")
            continue
        if not source_text or source_text not in critical_text:
            checks.fail(
                "skill_artifacts",
                f"compiled critical artifact is stale for {source_path_text}",
            )
            continue
        checks.pass_("skill_artifacts", f"source embedded: {source_path_text}")


def _check_filesystem(checks: InstallChecks, settings: Any) -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        checks.fail("environment_file", f"missing {env_path}")
    else:
        if os.name == "posix":
            permissions = stat.S_IMODE(env_path.stat().st_mode)
            if permissions & 0o077:
                checks.fail(
                    "environment_file",
                    f"{env_path} mode is {permissions:04o}; run chmod 600 .env",
                )
            else:
                checks.pass_("environment_file", f"{env_path} permissions are restricted")
        else:
            checks.pass_("environment_file", str(env_path))

    database_path = _resolve_from_repo(settings.database_path)
    data_dir = database_path.parent
    if not data_dir.is_dir():
        checks.fail("data_directory", f"missing {data_dir}")
    elif not os.access(data_dir, os.W_OK):
        checks.fail("data_directory", f"not writable: {data_dir}")
    else:
        checks.pass_("data_directory", f"writable: {data_dir}")
        if os.name == "posix":
            data_permissions = stat.S_IMODE(data_dir.stat().st_mode)
            if data_permissions & 0o077:
                checks.fail(
                    "data_permissions",
                    f"{data_dir} mode is {data_permissions:04o}; run chmod 700 {data_dir}",
                )
            else:
                checks.pass_("data_permissions", f"{data_dir} permissions are restricted")

    if database_path.exists():
        if not os.access(database_path, os.W_OK):
            checks.fail("sqlite_writable", f"database is not writable: {database_path}")
        else:
            checks.pass_("sqlite_writable", f"writable: {database_path}")
        if os.name == "posix":
            database_permissions = stat.S_IMODE(database_path.stat().st_mode)
            if database_permissions & 0o077:
                checks.fail(
                    "sqlite_permissions",
                    f"{database_path} mode is {database_permissions:04o}; run chmod 600 {database_path}",
                )
            else:
                checks.pass_("sqlite_permissions", f"{database_path} permissions are restricted")
        try:
            connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
            try:
                row = connection.execute("PRAGMA quick_check").fetchone()
            finally:
                connection.close()
            if row and row[0] == "ok":
                checks.pass_("sqlite", f"quick_check ok: {database_path}")
            else:
                checks.fail("sqlite", f"quick_check failed: {row}")
        except sqlite3.Error as exc:
            checks.fail("sqlite", f"could not read {database_path}: {exc}")
    else:
        checks.warn("sqlite", f"{database_path} will be created on first API start")

    if str(settings.memory_mode).strip().lower() in {"hybrid", "sqlite+markdown"}:
        markdown_path = _resolve_from_repo(settings.memory_markdown_path)
        if markdown_path.is_dir() and os.access(markdown_path, os.W_OK):
            checks.pass_("markdown_memory", f"writable: {markdown_path}")
        else:
            checks.fail("markdown_memory", f"missing or not writable: {markdown_path}")

    if settings.skill_artifact_auto_compile_enabled:
        checks.warn(
            "skill_artifacts",
            "runtime auto-compile is enabled and can modify tracked prompt artifacts; "
            "set SKILL_ARTIFACT_AUTO_COMPILE_ENABLED=false for the first server install",
        )
    else:
        checks.pass_("skill_artifacts", "runtime auto-compile is disabled")


def _check_local_models(
    checks: InstallChecks,
    settings: Any,
    *,
    require_models: bool,
    probe_models: bool,
    timeout_seconds: float,
) -> None:
    model_names = configured_model_names(settings)
    if not model_names:
        message = "both local-model lanes are disabled"
        if require_models:
            checks.fail("local_models", message)
        else:
            checks.warn("local_models", message)
        return

    providers: list[str] = []
    if settings.micro_model_enabled:
        providers.append(str(settings.micro_model_provider).strip().lower())
    if settings.main_repair_model_enabled:
        providers.append(str(settings.main_repair_model_provider).strip().lower())
    if bool(getattr(settings, "action_ticket_review_enabled", False)):
        providers.append(
            str(getattr(settings, "action_ticket_review_model_provider", "")).strip().lower()
        )
    unsupported = sorted({provider for provider in providers if provider != "ollama"})
    if unsupported:
        checks.fail("model_provider", f"unsupported configured provider(s): {', '.join(unsupported)}")
        return
    checks.pass_("model_provider", "ollama")

    ollama_url = str(settings.local_model_url).strip().rstrip("/")
    parsed = urlparse(ollama_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        checks.fail("ollama_url", f"invalid LOCAL_MODEL_URL: {ollama_url}")
        return
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        checks.pass_("ollama_url", ollama_url)
    else:
        checks.warn("ollama_url", f"non-loopback Ollama endpoint: {ollama_url}")

    try:
        tags = _request_json(f"{ollama_url}/api/tags", timeout_seconds=min(timeout_seconds, 10.0))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        checks.fail("ollama_api", f"unreachable at {ollama_url}: {_network_error(exc)}")
        return

    available_names: list[str] = []
    raw_models = tags.get("models")
    if isinstance(raw_models, list):
        for row in raw_models:
            if not isinstance(row, dict):
                continue
            for key in ("name", "model"):
                value = str(row.get(key) or "").strip()
                if value:
                    available_names.append(value)
    checks.pass_("ollama_api", f"reachable; {len(set(available_names))} installed model tag(s)")

    present_models: list[str] = []
    for model_name in model_names:
        if ollama_model_is_present(model_name, available_names):
            checks.pass_("ollama_model", f"installed: {model_name}")
            present_models.append(model_name)
        else:
            checks.fail("ollama_model", f"missing: {model_name}; run ollama pull {model_name}")

    if not probe_models:
        return
    for model_name in present_models:
        try:
            generated = _request_json(
                f"{ollama_url}/api/generate",
                timeout_seconds=timeout_seconds,
                method="POST",
                payload={
                    "model": model_name,
                    "prompt": "Reply with only the word OK.",
                    "stream": False,
                    "keep_alive": "30s",
                    # Reasoning models can consume a small initial budget before
                    # placing their visible answer in the response field.
                    "options": {"num_predict": OLLAMA_PROBE_NUM_PREDICT, "temperature": 0},
                },
            )
            response_text = str(generated.get("response") or "").strip()
            if generated.get("done") is True and response_text:
                checks.pass_("ollama_inference", f"{model_name} generated a response")
            else:
                thinking_text = str(generated.get("thinking") or "").strip()
                checks.fail(
                    "ollama_inference",
                    f"{model_name} returned an incomplete response "
                    f"(done={generated.get('done')!r}, "
                    f"done_reason={generated.get('done_reason')!r}, "
                    f"response_chars={len(response_text)}, "
                    f"thinking_chars={len(thinking_text)}, "
                    f"eval_count={generated.get('eval_count')!r})",
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            checks.fail("ollama_inference", f"{model_name}: {_network_error(exc)}")


def _check_web_research(checks: InstallChecks, settings: Any) -> None:
    if not bool(getattr(settings, "web_research_enabled", False)):
        checks.pass_("web_research", "disabled")
        return

    provider = str(getattr(settings, "web_research_provider", "") or "").strip().lower()
    if provider != "searxng":
        checks.fail("web_research", f"unsupported provider: {provider or '<empty>'}")
        return

    if not (
        bool(getattr(settings, "main_repair_model_enabled", False))
        or bool(getattr(settings, "micro_model_enabled", False))
    ):
        checks.fail("web_research", "an enabled local model lane is required to synthesize research")
        return

    base_url = str(getattr(settings, "web_research_base_url", "") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        checks.fail("web_research", f"invalid WEB_RESEARCH_BASE_URL: {base_url}")
        return

    safe_search = int(getattr(settings, "web_research_safe_search", 1))
    if bool(getattr(settings, "web_research_children_enabled", False)):
        checks.warn("web_research_children", "enabled; child requests will force strict safe search")
    else:
        checks.pass_("web_research_children", "disabled by default")

    query = urlencode(
        {
            "q": "Jarvis install verification",
            "format": "json",
            "language": "en",
            "safesearch": max(0, min(safe_search, 2)),
        }
    )
    timeout = min(float(getattr(settings, "web_research_timeout_seconds", 15.0)), 15.0)
    try:
        payload = _request_json(f"{base_url}/search?{query}", timeout_seconds=timeout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        checks.fail("web_research", f"SearXNG unreachable at {base_url}: {_network_error(exc)}")
        return
    results = payload.get("results")
    if not isinstance(results, list):
        checks.fail("web_research", "SearXNG JSON response did not contain a results list")
        return
    checks.pass_("web_research", f"SearXNG reachable; probe returned {len(results)} result(s)")


def _check_discord(checks: InstallChecks, settings: Any, *, require_discord: bool) -> None:
    if not settings.discord_enabled:
        if require_discord:
            checks.fail("discord", "DISCORD_ENABLED is false")
        else:
            checks.warn("discord", "disabled")
        return
    if not str(settings.discord_bot_token).strip():
        checks.fail("discord_token", "DISCORD_ENABLED is true but DISCORD_BOT_TOKEN is empty")
    else:
        checks.pass_("discord_token", "configured (value hidden)")

    raw_guild_id = str(settings.discord_command_guild_id).strip()
    raw_channel_id = str(settings.discord_command_channel_id).strip()

    guild_id = _positive_discord_id(raw_guild_id)
    channel_id = _positive_discord_id(raw_channel_id)
    if raw_guild_id and guild_id is None:
        checks.fail("discord_scope", "DISCORD_COMMAND_GUILD_ID must be a positive numeric ID")
    if raw_channel_id and channel_id is None:
        checks.fail("discord_scope", "DISCORD_COMMAND_CHANNEL_ID must be a positive numeric ID")
    if guild_id in DISCORD_EXAMPLE_IDS or channel_id in DISCORD_EXAMPLE_IDS:
        checks.fail("discord_scope", "replace the example Discord guild/channel ID")
    explicit_scope = guild_id is not None or channel_id is not None
    policy_path = _resolve_from_repo(settings.discord_permissions_path)
    policy_scope = False
    if policy_path.is_file():
        if os.name == "posix":
            policy_permissions = stat.S_IMODE(policy_path.stat().st_mode)
            if policy_permissions & 0o077:
                checks.fail(
                    "discord_policy_permissions",
                    f"{policy_path} mode is {policy_permissions:04o}; run chmod 600 {policy_path}",
                )
            else:
                checks.pass_(
                    "discord_policy_permissions",
                    f"{policy_path} permissions are restricted",
                )
        try:
            import yaml

            policy_payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
            if discord_policy_uses_example_ids(policy_payload):
                checks.fail("discord_policy", f"replace the example IDs in {policy_path}")
                return
            policy_scope = discord_policy_has_allow_scope(policy_payload)
        except Exception as exc:
            checks.fail("discord_policy", f"could not load {policy_path}: {exc}")
            return
        if policy_scope:
            checks.pass_("discord_policy", f"allow scope configured in {policy_path}")
        elif explicit_scope:
            checks.warn("discord_policy", f"{policy_path} has no allow scope; explicit env scope will be used")
        else:
            checks.fail("discord_policy", f"{policy_path} has no guild/channel/user allow scope")
    elif explicit_scope:
        checks.warn("discord_policy", f"missing {policy_path}; explicit env scope will be used")
    else:
        checks.fail(
            "discord_policy",
            "no restrictive policy or DISCORD_COMMAND_GUILD_ID/DISCORD_COMMAND_CHANNEL_ID is configured",
        )


def _check_google_calendar(checks: InstallChecks, settings: Any) -> None:
    if not settings.calendar_google_enabled:
        checks.pass_("google_calendar", "disabled for the first-install profile")
        return
    permissions_path = _resolve_from_repo(settings.google_permissions_path)
    if permissions_path.is_file():
        if os.name == "posix":
            permissions = stat.S_IMODE(permissions_path.stat().st_mode)
            if permissions & 0o077:
                checks.fail(
                    "google_calendar_permissions",
                    f"{permissions_path} mode is {permissions:04o}; run chmod 600 {permissions_path}",
                )
                return
        checks.warn(
            "google_calendar",
            f"configured via {permissions_path}; complete OAuth interactively before headless service use",
        )
    else:
        checks.fail("google_calendar", f"missing permissions file: {permissions_path}")


def _check_email_agent(checks: InstallChecks, settings: Any) -> None:
    if not bool(getattr(settings, "email_agent_enabled", False)):
        checks.pass_("email_agent", "disabled")
        return

    from app.skills.domains.email_agent.config import EmailAgentPermissions

    path = _resolve_from_repo(str(getattr(settings, "email_agent_permissions_path", "")))
    if not path.is_file():
        checks.fail("email_agent_permissions", f"missing permissions file: {path}")
        return
    if os.name == "posix":
        permissions = stat.S_IMODE(path.stat().st_mode)
        if permissions & 0o077:
            checks.fail(
                "email_agent_permissions",
                f"{path} mode is {permissions:04o}; run chmod 600 {path}",
            )
        else:
            checks.pass_("email_agent_permissions", f"{path} permissions are restricted")
    try:
        policy = EmailAgentPermissions.load(str(path))
    except Exception as exc:
        checks.fail("email_agent_permissions", f"invalid protected configuration: {exc}")
        return
    checks.pass_(
        "email_agent_permissions",
        f"{len(policy.source_routes)} source route(s), "
        f"{sum(1 for item in policy.access_grants if item.enabled)} enabled user/channel grant(s)",
    )

    unsafe_flags = {
        "EMAIL_AGENT_LABEL_SHADOW_ENABLED": bool(
            getattr(settings, "email_agent_label_shadow_enabled", False)
        ),
        "EMAIL_AGENT_ATTACHMENT_EXTRACTION_ENABLED": not bool(
            getattr(settings, "email_agent_attachment_extraction_enabled", False)
        ),
        "EMAIL_AGENT_ALLOW_HISTORICAL_BACKFILL": not bool(
            getattr(settings, "email_agent_allow_historical_backfill", False)
        ),
    }
    failed_flags = [name for name, safe in unsafe_flags.items() if not safe]
    if failed_flags:
        checks.fail("email_agent_safety", f"unsafe first-rollout flags: {', '.join(failed_flags)}")
    else:
        checks.pass_(
            "email_agent_safety",
            "classification evidence retained; attachment extraction and historical backfill off; "
            f"managed label writes {'on' if getattr(settings, 'email_agent_label_writes_enabled', False) else 'off'}",
        )
    if bool(getattr(settings, "email_agent_allow_remote_model", False)):
        checks.warn("email_agent_model", "remote email-model processing is explicitly enabled")
    else:
        checks.pass_("email_agent_model", "remote email-model processing is disabled")
    if bool(getattr(settings, "email_agent_sync_enabled", False)):
        checks.pass_("email_agent_sync", "bounded scheduler sync enabled")
    else:
        checks.warn("email_agent_sync", "skill enabled but scheduler sync disabled")
    spam_writes_enabled = bool(getattr(settings, "email_agent_spam_writes_enabled", False))
    label_writes_enabled = bool(getattr(settings, "email_agent_label_writes_enabled", False))
    if spam_writes_enabled or label_writes_enabled:
        token_value = (
            getattr(settings, "email_agent_label_token_path", "")
            if label_writes_enabled
            else getattr(settings, "email_agent_spam_token_path", "")
        )
        worker_token = _resolve_from_repo(str(token_value))
        if not worker_token.is_file():
            if bool(getattr(settings, "email_agent_worker_token_isolated", False)):
                checks.pass_(
                    "email_spam_worker_isolation",
                    "mailbox write token is intentionally absent from this API process; "
                    "verify the isolated worker separately",
                )
            else:
                checks.fail("email_spam_worker", f"enabled but token file is missing: {worker_token}")
        elif spam_writes_enabled and "spam" not in policy.category_keys:
            checks.fail("email_spam_worker", "enabled but the shared spam category is missing")
        elif label_writes_enabled and not policy.managed_gmail_labels:
            checks.fail("email_spam_worker", "managed label writes enabled without an allowlist")
        elif os.name == "posix" and stat.S_IMODE(worker_token.stat().st_mode) & 0o077:
            checks.fail("email_spam_worker", f"{worker_token} permissions must be 0600")
        else:
            checks.pass_(
                "email_spam_worker",
                "isolated mailbox worker token configured; "
                f"manual caps {getattr(settings, 'email_agent_spam_max_writes_per_hour', 0)}/hour and "
                f"{getattr(settings, 'email_agent_spam_max_writes_per_day', 0)}/day; "
                f"managed-label caps {getattr(settings, 'email_agent_label_max_writes_per_hour', 0)}/hour and "
                f"{getattr(settings, 'email_agent_label_max_writes_per_day', 0)}/day",
            )
    else:
        checks.pass_("email_spam_worker", "manual Gmail mailbox writes disabled")


def _check_action_tickets(checks: InstallChecks, settings: Any) -> None:
    if not settings.action_tickets_enabled:
        if settings.action_ticket_review_enabled or settings.action_ticket_auto_remediation_enabled:
            checks.fail("action_tickets", "review/remediation requires ACTION_TICKETS_ENABLED=true")
        else:
            checks.pass_("action_tickets", "disabled")
        return

    if not str(settings.operator_api_key or "").strip():
        checks.fail("action_ticket_operator_auth", "set JARVIS_OPERATOR_API_KEY before enabling tickets")
    else:
        checks.pass_("action_ticket_operator_auth", "configured (value hidden)")

    if settings.action_ticket_review_enabled:
        if str(settings.action_ticket_review_model_provider).strip().lower() != "ollama":
            checks.fail("action_ticket_review", "only the Ollama review provider is implemented")
        elif not str(settings.action_ticket_review_model_name or "").strip():
            checks.fail("action_ticket_review", "review model name resolved to an empty value")
        else:
            checks.pass_(
                "action_ticket_review",
                f"enabled with model {settings.action_ticket_review_model_name}",
            )
    else:
        checks.warn("action_ticket_review", "capture is enabled but delayed review is disabled")

    if settings.action_ticket_auto_remediation_enabled and not settings.action_ticket_review_enabled:
        checks.fail("action_ticket_remediation", "auto-remediation requires review to be enabled")
    elif settings.action_ticket_auto_remediation_enabled:
        checks.warn("action_ticket_remediation", "enabled for the narrow Lists allowlist")
    else:
        checks.pass_("action_ticket_remediation", "disabled")

    if settings.plane_enabled:
        required = {
            "PLANE_API_BASE_URL": settings.plane_api_base_url,
            "PLANE_API_KEY": settings.plane_api_key,
            "PLANE_WORKSPACE_SLUG": settings.plane_workspace_slug,
            "PLANE_PROJECT_ID": settings.plane_project_id,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            checks.fail("plane", f"missing: {', '.join(missing)}")
        else:
            checks.pass_("plane", "configured (API key hidden)")
        if settings.plane_sync_raw_transcript:
            checks.warn("plane_privacy", "raw family transcript synchronization is enabled")
        else:
            checks.pass_("plane_privacy", "raw transcript synchronization is disabled")


def _check_live_api(
    checks: InstallChecks,
    settings: Any,
    *,
    api_url: str,
    smoke_turn: bool,
    timeout_seconds: float,
) -> None:
    base_url = api_url.strip().rstrip("/")
    try:
        health = _request_json(f"{base_url}/health", timeout_seconds=min(timeout_seconds, 10.0))
        if health.get("status") == "ok":
            checks.pass_("jarvis_health", f"healthy at {base_url}")
        else:
            checks.fail("jarvis_health", f"unexpected payload: {health}")
            return
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        checks.fail("jarvis_health", f"unreachable at {base_url}: {_network_error(exc)}")
        return

    try:
        dashboard = _request_text(f"{base_url}/dashboard", timeout_seconds=min(timeout_seconds, 10.0))
        if "Jarvis House Dashboard" in dashboard:
            checks.pass_("control_panel", "dashboard HTML loaded")
        else:
            checks.fail("control_panel", "dashboard response did not contain the expected title")
    except OSError as exc:
        checks.fail("control_panel", _network_error(exc))

    if not smoke_turn:
        return
    operator_key = str(getattr(settings, "operator_api_key", "") or "").strip()
    request_headers: dict[str, str] = {}
    if operator_key:
        parsed_api_url = urlparse(base_url)
        hostname = str(parsed_api_url.hostname or "").strip().casefold()
        if parsed_api_url.scheme.casefold() == "https" or hostname in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            request_headers["X-Jarvis-Operator-Key"] = operator_key
        else:
            checks.fail(
                "jarvis_smoke_turn",
                "refusing to send the operator key over non-loopback HTTP; use localhost or HTTPS",
            )
            return
    try:
        response = _request_json(
            f"{base_url}/ask",
            timeout_seconds=timeout_seconds,
            method="POST",
            request_headers=request_headers,
            payload={
                "text": "Tell me who you are in one short sentence.",
                "user_id": "install-smoke",
                "source": "install_smoke",
                "context": {"wake_on_message": True, "force_main_owner": True},
            },
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        checks.fail("jarvis_smoke_turn", _network_error(exc))
        return

    assistant = response.get("assistant")
    assistant_text = assistant.get("text") if isinstance(assistant, dict) else None
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        checks.fail("jarvis_smoke_turn", "response did not contain assistant.text")
        return
    checks.pass_("jarvis_smoke_turn", "received an assistant response")

    if configured_model_names(settings):
        result = response.get("result")
        source = result.get("conversation_source") if isinstance(result, dict) else None
        classification = response.get("classification")
        repair_source = (
            classification.get("repair_source") if isinstance(classification, dict) else None
        )
        route = response.get("route")
        if source == "model":
            checks.pass_("jarvis_model_path", "conversation_source=model")
        elif route == "main_jarvis_repair" and repair_source == "backend":
            checks.pass_("jarvis_model_path", "main repair_source=backend")
        else:
            checks.fail(
                "jarvis_model_path",
                "expected a model-backed conversation or repair path, got "
                f"route={route!r}, conversation_source={source!r}, repair_source={repair_source!r}",
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a Jarvis Ubuntu first-install configuration and optional live service.",
    )
    parser.add_argument("--api-url", help="also check a running Jarvis API, for example http://127.0.0.1:8000")
    parser.add_argument("--smoke-turn", action="store_true", help="send one non-destructive /ask turn (writes trace/memory rows)")
    parser.add_argument("--probe-models", action="store_true", help="run a short direct inference against each configured Ollama model")
    parser.add_argument("--require-models", action="store_true", help="fail when local model lanes are disabled")
    parser.add_argument("--require-discord", action="store_true", help="fail when the Discord adapter is disabled")
    parser.add_argument("--timeout-seconds", type=float, default=120.0, help="bounded inference/API timeout (default: 120)")
    parser.add_argument("--strict", action="store_true", help="return a failure exit code for warnings as well as failures")
    parser.add_argument("--json", action="store_true", dest="json_output", help="emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.smoke_turn and not args.api_url:
        parser.error("--smoke-turn requires --api-url")
    if args.timeout_seconds <= 0 or args.timeout_seconds > 600:
        parser.error("--timeout-seconds must be greater than 0 and no more than 600")

    from app.config import settings

    checks = InstallChecks()
    _check_platform(checks)
    _check_dependencies(checks)
    _check_skill_artifacts(checks)
    _check_filesystem(checks, settings)
    _check_local_models(
        checks,
        settings,
        require_models=args.require_models,
        probe_models=args.probe_models,
        timeout_seconds=args.timeout_seconds,
    )
    _check_web_research(checks, settings)
    _check_discord(checks, settings, require_discord=args.require_discord)
    _check_google_calendar(checks, settings)
    _check_email_agent(checks, settings)
    _check_action_tickets(checks, settings)
    if args.api_url:
        _check_live_api(
            checks,
            settings,
            api_url=args.api_url,
            smoke_turn=args.smoke_turn,
            timeout_seconds=args.timeout_seconds,
        )

    counts = {
        level: sum(result.level == level for result in checks.results)
        for level in ("PASS", "WARN", "FAIL")
    }
    if args.json_output:
        print(json.dumps({"checks": [asdict(result) for result in checks.results], "summary": counts}, indent=2))
    else:
        for result in checks.results:
            print(f"[{result.level}] {result.name}: {result.detail}")
        print(f"Summary: {counts['PASS']} passed, {counts['WARN']} warning(s), {counts['FAIL']} failed")

    if checks.failed or (args.strict and checks.warned):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
