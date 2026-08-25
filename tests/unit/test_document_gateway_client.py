from __future__ import annotations

import httpx

from app.integrations.document_gateway.client import DocumentGatewayClient


def test_core_gateway_client_uses_local_host_boundary_and_operator_key(tmp_path) -> None:
    key_file = tmp_path / "operator.key"
    key_file.write_text("synthetic-secret", encoding="utf-8")
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"status": "ready"}, request=request)

    client = DocumentGatewayClient(
        base_url="http://document-gateway:8010",
        operator_key_path=str(key_file),
        transport=httpx.MockTransport(handler),
    )

    assert client.ready()
    assert observed[0].headers["host"] == "localhost"
    assert observed[0].headers["x-jarvis-operator-key"] == "synthetic-secret"
    client.close()
