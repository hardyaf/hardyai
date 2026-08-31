from __future__ import annotations

import ast
import importlib
from pathlib import Path

import yaml

from app.skills.registry_service import SkillRegistryService


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports_module(path: Path, module_name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == module_name or alias.name.startswith(f"{module_name}.") for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom):
            imported = str(node.module or "")
            if imported == module_name or imported.startswith(f"{module_name}."):
                return True
    return False


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _skill_frontmatter(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    assert source.startswith("---\n")
    _, raw, _ = source.split("---", 2)
    value = yaml.safe_load(raw)
    assert isinstance(value, dict)
    return value


def test_http_adapters_do_not_import_runtime_composition_root() -> None:
    adapter_files = _python_files(APP_ROOT / "api") + [APP_ROOT / "dependencies.py"]
    offenders = [path.relative_to(REPO_ROOT).as_posix() for path in adapter_files if _imports_module(path, "app.runtime")]
    assert offenders == []


def test_schema_and_connection_authority_stays_under_app_db() -> None:
    ddl_tokens = ("CREATE TABLE", "ALTER TABLE")
    ddl_offenders: list[str] = []
    connection_offenders: list[str] = []
    for path in _python_files(APP_ROOT):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(APP_ROOT).as_posix()
        if any(token in source for token in ddl_tokens) and not relative.startswith("db/"):
            ddl_offenders.append(relative)
        if "sqlite3.connect(" in source and relative != "db/connection.py":
            connection_offenders.append(relative)
    assert ddl_offenders == []
    assert connection_offenders == []


def test_router_and_house_adapters_obey_size_ratchets() -> None:
    router_path = APP_ROOT / "core" / "router.py"
    request_flow_path = APP_ROOT / "core" / "request_flow.py"
    house_path = APP_ROOT / "api" / "routes" / "house.py"
    assert len(router_path.read_text(encoding="utf-8").splitlines()) <= 1400
    assert len(house_path.read_text(encoding="utf-8").splitlines()) <= 150

    tree = ast.parse(router_path.read_text(encoding="utf-8"))
    router_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "JarvisRouter")
    route_method = next(
        node for node in router_class.body if isinstance(node, ast.FunctionDef) and node.name == "route"
    )
    assert route_method.end_lineno - route_method.lineno + 1 <= 3

    flow_tree = ast.parse(request_flow_path.read_text(encoding="utf-8"))
    flow_class = next(
        node
        for node in flow_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RequestFlowCoordinator"
    )
    flow_methods = [node for node in flow_class.body if isinstance(node, ast.FunctionDef)]
    assert max(node.end_lineno - node.lineno + 1 for node in flow_methods) <= 200
    flow_route = next(node for node in flow_methods if node.name == "route")
    assert flow_route.end_lineno - flow_route.lineno + 1 <= 25


def test_create_app_uses_explicit_application_container() -> None:
    main_path = APP_ROOT / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    create_app = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create_app"
    )
    assert create_app.args.args[0].arg == "container"
    assert not _imports_module(main_path, "app.runtime")


def test_document_domain_is_provider_neutral_and_core_does_not_mount_document_routes() -> None:
    document_domain = _python_files(APP_ROOT / "skills" / "domains" / "documents")
    forbidden_modules = ("app.integrations.paperless", "httpx", "fastapi")
    offenders = {
        module: [
            path.relative_to(REPO_ROOT).as_posix()
            for path in document_domain
            if _imports_module(path, module)
        ]
        for module in forbidden_modules
    }
    assert offenders == {module: [] for module in forbidden_modules}

    main_source = (APP_ROOT / "main.py").read_text(encoding="utf-8")
    turn_source = (APP_ROOT / "services" / "turn_service.py").read_text(encoding="utf-8")
    assert "routes.documents" not in main_source
    assert "document.archive.v1" not in turn_source


def test_document_gateway_never_opens_or_mounts_the_core_database() -> None:
    gateway_files = [
        APP_ROOT / "api" / "document_app.py",
        APP_ROOT / "api" / "routes" / "documents.py",
        APP_ROOT / "composition" / "documents.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in gateway_files)
    assert "DurableJobRepository" not in source
    assert "settings.database_path" not in source
    assert "app.jobs.enqueue_ipc" in source


def test_discord_core_adapter_passes_metadata_only_to_isolated_attachment_ingress() -> None:
    bot_source = (APP_ROOT / "services" / "discord" / "bot.py").read_text(encoding="utf-8")
    transfer_path = APP_ROOT / "integrations" / "discord_attachment" / "service.py"
    transfer_source = transfer_path.read_text(encoding="utf-8")

    assert "attachment.read(" not in bot_source
    assert "attachment.save(" not in bot_source
    assert "source_url=str(attachment.url)" in bot_source
    assert "app.runtime" not in transfer_source
    assert "sqlite" not in transfer_source.casefold()
    assert "cdn.discordapp.com" in transfer_source


def test_active_skill_declarations_have_unique_ownership_and_importable_handlers() -> None:
    owners: dict[str, list[str]] = {}
    unimportable: list[str] = []
    for path in sorted((APP_ROOT / "prompts" / "skills").glob("*_skill.md")):
        frontmatter = _skill_frontmatter(path)
        if not bool(frontmatter.get("active", True)):
            continue
        skill_id = str(frontmatter.get("skill_id") or "")
        operation_ids = {
            str(item).strip()
            for item in frontmatter.get("intents") or []
            if str(item).strip()
        }
        operation_ids.update(
            str(item.get("tool_id") or "").strip()
            for item in frontmatter.get("main_tools") or []
            if isinstance(item, dict) and str(item.get("tool_id") or "").strip()
        )
        for operation_id in operation_ids:
            owners.setdefault(operation_id, []).append(skill_id)

        execution_ref = str(frontmatter.get("execution_ref") or "").strip()
        module_name, separator, attribute = execution_ref.partition(":")
        try:
            module = importlib.import_module(module_name) if separator and attribute else None
        except Exception:
            module = None
        if module is None or not callable(getattr(module, attribute, None)):
            unimportable.append(skill_id)

    duplicates = {
        operation_id: sorted(skill_ids)
        for operation_id, skill_ids in owners.items()
        if len(skill_ids) > 1
    }
    assert duplicates == {}
    assert unimportable == []


def test_runtime_capability_projection_excludes_implementation_and_storage_references() -> None:
    class _Catalog:
        @staticmethod
        def list_skills(*, active_only: bool = True) -> list[dict]:
            assert active_only is True
            return [
                {
                    "skill_id": "skill.lists.core",
                    "skill_name": "Lists",
                    "skill_user": "all",
                    "skill_agents": ["all"],
                    "intents": ["lists.get_items"],
                    "execution_ref": "app.skills.domains.lists.handler:run",
                    "storage_ref": "must-not-project",
                    "micro_enabled": False,
                    "micro_functions": [],
                }
            ]

    projection = SkillRegistryService(_Catalog()).runtime_capability_catalog(
        user_id="fixture-user",
        agent_id="jarvis",
    )
    assert len(projection) == 1
    assert "execution_ref" not in projection[0]
    assert "storage_ref" not in projection[0]


def test_app_runtime_imports_are_limited_to_existing_compatibility_composition_paths() -> None:
    approved = {
        "app/container.py",
        "app/workers/plane_sync_worker.py",
        "app/workers/ticket_review_worker.py",
    }
    importers = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in _python_files(APP_ROOT)
        if _imports_module(path, "app.runtime")
    }
    assert importers <= approved


def test_skill_domains_do_not_import_other_domains_handlers_or_stores() -> None:
    domains_root = APP_ROOT / "skills" / "domains"
    offenders: list[str] = []
    for path in _python_files(domains_root):
        source_domain = path.relative_to(domains_root).parts[0]
        for module in _imported_modules(path):
            parts = module.split(".")
            if len(parts) < 5 or parts[:3] != ["app", "skills", "domains"]:
                continue
            target_domain = parts[3]
            target_boundary = parts[4]
            if target_domain != source_domain and target_boundary in {"handler", "storage"}:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()} -> {module}")
    assert offenders == []


def test_p2_typed_tool_seam_is_not_reachable_from_main_or_router() -> None:
    guarded_files = [
        APP_ROOT / "core" / "router.py",
        APP_ROOT / "core" / "request_flow.py",
        APP_ROOT / "core" / "main_backend.py",
        APP_ROOT / "core" / "main_turn_commitment.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in guarded_files)
    assert ".execute_tool(" not in source
    assert ".effective_tools(" not in source
    assert ".discovery_cards(" not in source
    assert "ToolCallEnvelope" not in source
