from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    text: str = Field(min_length=1)
    request_id: str | None = None
    session_id: str | None = None
    user_id: str = "local_user"
    source: str = "web"
    context: dict[str, Any] = Field(default_factory=dict)


class AskResponse(BaseModel):
    request_id: str | None = None
    ticket: dict[str, Any] | None = None
    session_id: str
    agent_id: str | None = None
    source: str
    owner: str
    state: str
    power_state: str
    session_runtime: dict[str, Any] = Field(default_factory=dict)
    intent: str
    classification: dict[str, Any] = Field(default_factory=dict)
    route: str
    result: dict[str, Any]
    dialog: dict[str, Any] = Field(default_factory=dict)
    assistant: dict[str, Any] = Field(default_factory=dict)
    delivery: dict[str, Any] = Field(default_factory=dict)
