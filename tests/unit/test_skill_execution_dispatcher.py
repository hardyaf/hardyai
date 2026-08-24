from __future__ import annotations

from typing import Any

from app.skills.execution_dispatcher import SkillExecutionDispatcher


class _FakeHomeService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def set_switch(
        self,
        *,
        switch_name: str,
        action: str,
        source_interface: str | None,
        requested_by_user_id: str | None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "switch_name": switch_name,
                "action": action,
                "source_interface": source_interface,
                "requested_by_user_id": requested_by_user_id,
            }
        )
        return {
            "status": "ok",
            "switch_name": switch_name,
            "state": action,
        }


def test_execution_dispatcher_runs_safe_execution_ref():
    fake_home = _FakeHomeService()
    dispatcher = SkillExecutionDispatcher(
        lists_service=object(),
        calendar_service=object(),
        home_service=fake_home,
    )
    skill = {"execution_ref": "app.skills.domains.lights.handler:run"}

    result = dispatcher.execute(
        skill=skill,
        intent="home.set_switch",
        entities={"switch_name": "desk", "action": "on"},
        context={"source_interface": "web", "requested_by_user_id": "jordan"},
    )

    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert fake_home.calls
    assert fake_home.calls[0]["switch_name"] == "desk"
    assert fake_home.calls[0]["action"] == "on"
    assert fake_home.calls[0]["source_interface"] == "web"
    assert fake_home.calls[0]["requested_by_user_id"] == "jordan"


def test_execution_dispatcher_rejects_unsafe_execution_ref():
    dispatcher = SkillExecutionDispatcher(
        lists_service=object(),
        calendar_service=object(),
        home_service=object(),
    )
    skill = {"execution_ref": "os.system:run"}

    result = dispatcher.execute(
        skill=skill,
        intent="home.set_switch",
        entities={"switch_name": "desk", "action": "on"},
        context={},
    )

    assert result is None


def test_execution_dispatcher_runs_email_only_through_domain_handler():
    class FakeEmailAgent:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            return {"status": "ok", "message": "E1 - fixture"}

    email = FakeEmailAgent()
    dispatcher = SkillExecutionDispatcher(
        lists_service=object(),
        calendar_service=object(),
        home_service=object(),
        email_agent_service=email,
    )

    result = dispatcher.execute(
        skill={"execution_ref": "app.skills.domains.email_agent.handler:run"},
        intent="email.list_recent",
        entities={"query": "recent email"},
        context={"source_interface": "discord", "identity_bound": True},
    )

    assert result == {"status": "ok", "message": "E1 - fixture"}
    assert email.calls[0]["intent"] == "email.list_recent"
    assert email.calls[0]["context"]["identity_bound"] is True
