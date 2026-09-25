from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class TaskRunnerLauncherClient:
    """Narrow client for the trusted fixed-policy runner launcher."""

    def __init__(self, *, base_url: str, key_path: str, timeout_seconds: float) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._key_path = Path(key_path)
        self._timeout_seconds = max(5.0, float(timeout_seconds))

    def _key(self) -> str:
        try:
            value = self._key_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("task_runner_key_unavailable") from exc
        if not value:
            raise RuntimeError("task_runner_key_unavailable")
        return value

    def run(
        self,
        *,
        task_id: str,
        run_id: str,
        workspace_ref: str,
        broker_token: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self._timeout_seconds, trust_env=False) as client:
            response = client.post(
                f"{self._base_url}/runs",
                headers={"X-Task-Runner-Key": self._key()},
                json={
                    "task_id": task_id,
                    "run_id": run_id,
                    "workspace_ref": workspace_ref,
                    "broker_token": broker_token,
                    "timeout_seconds": timeout_seconds,
                },
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("task_runner_response_invalid")
        return payload
