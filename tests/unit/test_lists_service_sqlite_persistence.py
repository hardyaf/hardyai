from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from app.db.sqlite_store import SQLiteStore
from app.tools.lists_service import ListsService


def test_lists_service_persists_items_with_sqlite_store():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-lists-sqlite-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "lists.db"
        store = SQLiteStore(database_path=str(db_path))
        service_one = ListsService(default_list_names=["groceries"], sqlite_store=store)

        add_result = service_one.add_item("groceries", "milk")
        assert add_result["status"] == "ok"
        assert add_result["count"] == 1

        service_two = ListsService(default_list_names=["groceries"], sqlite_store=store)
        get_result = service_two.get_items("grocery list")
        assert get_result["status"] == "ok"
        assert get_result["list_name"] == "groceries"
        assert get_result["items"] == ["milk"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_lists_service_persists_and_resolves_compact_vs_spaced_names_with_sqlite():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-lists-sqlite-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "lists.db"
        store = SQLiteStore(database_path=str(db_path))
        service_one = ListsService(sqlite_store=store)
        service_one.create_list("EasterPrep")
        add_result = service_one.add_item("easter prep", "buy candy")
        assert add_result["status"] == "ok"

        service_two = ListsService(sqlite_store=store)
        get_result = service_two.get_items("easterprep list")
        assert get_result["status"] == "ok"
        assert get_result["items"] == ["buy candy"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_lists_service_sqlite_persists_remove_done_and_delete_operations():
    data_root = (Path.cwd() / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-lists-sqlite-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "lists.db"
        store = SQLiteStore(database_path=str(db_path))
        service_one = ListsService(sqlite_store=store)
        service_one.create_list("costco")
        service_one.add_item("costco", "apples")
        service_one.add_item("costco", "tofu")

        done_result = service_one.mark_item_done(
            list_name="costco",
            item_text="apples",
            completion_mode="done",
        )
        assert done_result["status"] == "ok"

        remove_result = service_one.remove_item("costco", "tofu")
        assert remove_result["status"] == "ok"

        service_two = ListsService(sqlite_store=store)
        check_result = service_two.get_items("costco")
        assert check_result["status"] == "ok"
        assert check_result["items"] == ["apples"]

        delete_result = service_two.delete_list("costco")
        assert delete_result["status"] == "ok"

        service_three = ListsService(sqlite_store=store)
        missing_result = service_three.get_items("costco")
        assert missing_result["status"] == "unknown_list"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
