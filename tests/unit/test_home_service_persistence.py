from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from app.db.sqlite_store import SQLiteStore
from app.tools.home_service import HomeService


def test_home_service_persists_switch_state_across_instances():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_service.db"
        store = SQLiteStore(database_path=str(db_path))

        service_one = HomeService(sqlite_store=store, default_switch_names=["office test light"])
        first = service_one.set_switch(
            switch_name="office test light",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert first["status"] == "ok"

        # Simulate restart by creating a new service instance over the same DB.
        service_two = HomeService(sqlite_store=store, default_switch_names=["office test light"])
        switches = service_two.list_switches()
        office = next(item for item in switches if item["name"] == "office test light")
        assert office["state"] == "on"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_home_service_resolves_alias_to_existing_named_switch():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-alias-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_alias.db"
        store = SQLiteStore(database_path=str(db_path))
        service = HomeService(sqlite_store=store, default_switch_names=["office test light"])

        result = service.set_switch(
            switch_name="office light",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert result["status"] == "ok"
        assert result["switch_name"] == "office test light"
        assert result["matched_existing"] is True

        switches = service.list_switches()
        office = next(item for item in switches if item["name"] == "office test light")
        assert office["state"] == "on"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_home_service_resolves_single_token_alias_to_existing_named_switch():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-short-alias-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_short_alias.db"
        store = SQLiteStore(database_path=str(db_path))
        service = HomeService(sqlite_store=store, default_switch_names=["office test light"])

        result = service.set_switch(
            switch_name="office",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert result["status"] == "ok"
        assert result["switch_name"] == "office test light"
        assert result["matched_existing"] is True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_home_service_does_not_create_unknown_switches():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-unknown-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_unknown.db"
        store = SQLiteStore(database_path=str(db_path))
        service = HomeService(sqlite_store=store, default_switch_names=["office test light"])

        result = service.set_switch(
            switch_name="garage floodlight",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert result["status"] == "unknown_switch"

        switches = service.list_switches()
        names = [item["name"] for item in switches]
        assert names == ["office test light"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_home_service_can_toggle_all_lights_without_creating_new_switches():
    data_root = (Path.cwd() / "data").resolve()
    if not data_root.exists():
        data_root = (Path.cwd() / "jarvis_poc" / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    scratch = data_root / f"jarvis-home-all-test-{uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        db_path = scratch / "home_all.db"
        store = SQLiteStore(database_path=str(db_path))
        service = HomeService(
            sqlite_store=store,
            default_switch_names=["office test light", "kitchen light", "living room lamp"],
        )

        result = service.set_switch(
            switch_name="all lights",
            action="on",
            source_interface="dashboard",
            requested_by_user_id="jordan",
        )
        assert result["status"] == "ok"
        assert result["scope"] == "all"
        assert result["affected_count"] == 3

        switches = service.list_switches()
        assert len(switches) == 3
        assert all(item["state"] == "on" for item in switches)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
