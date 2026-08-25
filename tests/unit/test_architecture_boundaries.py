from __future__ import annotations

import ast
from pathlib import Path


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
