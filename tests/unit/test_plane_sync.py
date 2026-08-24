from __future__ import annotations

from typing import Any

from app.db.sqlite_store import SQLiteStore
from app.integrations.plane.sync_service import PlaneSyncService
from app.tickets.repository import TicketRepository


class FakePlane:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.creates = 0

    def list_states(self):
        return [{"id": "done-id", "name": "Done"}, {"id": "backlog-id", "name": "Backlog"}]

    def find_work_item_by_external_id(self, external_id):
        return next((item for item in self.items.values() if item.get("external_id") == external_id), None)

    def create_work_item(self, payload):
        self.creates += 1
        item = {**payload, "id": f"plane-{self.creates}"}
        self.items[item["id"]] = item
        return item

    def update_work_item(self, work_item_id, payload):
        self.items[work_item_id].update(payload)
        return dict(self.items[work_item_id])

    def create_comment(self, work_item_id, payload):
        return {"id": "comment", **payload}


def test_plane_projection_reconciles_by_external_id_and_escapes_transcript(tmp_path):
    path = tmp_path / "plane.db"
    SQLiteStore(database_path=str(path)).close()
    repo = TicketRepository(database_path=str(path))
    try:
        ticket = repo.create_ticket(
            origin_request_id="plane-request",
            session_id="session",
            user_id="user",
            agent_id="jarvis",
            source="discord",
            intent="lists.add_item",
            skill_id="skill.lists.core",
            route="micro_tool",
            title="Add <milk> & eggs",
        )
        repo.append_entry(
            ticket_id=ticket["ticket_id"],
            request_id="plane-request",
            entry_type="user_request",
            actor_type="user",
            verbatim_text="private <text>",
        )
        plane = FakePlane()
        sync = PlaneSyncService(repository=repo, client=plane, sync_raw_transcript=False)
        first = sync.sync_ticket(ticket["ticket_id"])
        # Simulate a lost local mapping after Plane committed the first create.
        repo._conn.execute(
            "UPDATE work_tickets SET plane_work_item_id = NULL WHERE ticket_id = ?",
            (ticket["ticket_id"],),
        )
        repo._conn.commit()
        second = sync.sync_ticket(ticket["ticket_id"])
        assert first["status"] == second["status"] == "synced"
        assert plane.creates == 1
        description = next(iter(plane.items.values()))["description_html"]
        assert "private" not in description
    finally:
        repo.close()

