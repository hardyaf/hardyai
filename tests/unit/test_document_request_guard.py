from __future__ import annotations

import asyncio

from app.api.document_limits import DocumentRequestGuard


def _scope() -> dict[str, object]:
    return {
        "type": "http",
        "path": "/documents",
        "method": "POST",
        "scheme": "http",
        "client": ("127.0.0.1", 1234),
        "headers": [(b"host", b"testserver"), (b"x-jarvis-operator-key", b"secret")],
    }


def test_chunked_body_is_counted_without_content_length() -> None:
    async def exercise() -> list[dict[str, object]]:
        async def downstream(scope, receive, send) -> None:
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        messages = iter(
            [
                {"type": "http.request", "body": b"a" * 80, "more_body": True},
                {"type": "http.request", "body": b"b" * 80, "more_body": False},
            ]
        )

        async def receive():
            return next(messages)

        sent: list[dict[str, object]] = []

        async def send(message):
            sent.append(message)

        guard = DocumentRequestGuard(
            downstream,
            max_request_bytes=100,
            body_timeout_seconds=5,
            global_concurrency=1,
            per_principal_concurrency=1,
            app_env="test",
        )
        await guard(_scope(), receive, send)
        return sent

    sent = asyncio.run(exercise())
    assert sent[0]["status"] == 413


def test_slow_body_deadline_and_principal_concurrency_fail_before_downstream_response() -> None:
    async def exercise() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocking_downstream(scope, receive, send) -> None:
            entered.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive_empty():
            return {"type": "http.request", "body": b"", "more_body": False}

        guard = DocumentRequestGuard(
            blocking_downstream,
            max_request_bytes=100,
            body_timeout_seconds=5,
            global_concurrency=2,
            per_principal_concurrency=1,
            app_env="test",
        )
        first_sent: list[dict[str, object]] = []
        second_sent: list[dict[str, object]] = []

        async def first_send(message):
            first_sent.append(message)

        async def second_send(message):
            second_sent.append(message)

        first = asyncio.create_task(guard(_scope(), receive_empty, first_send))
        await entered.wait()
        await guard(_scope(), receive_empty, second_send)
        release.set()
        await first

        async def slow_downstream(scope, receive, send) -> None:
            await receive()

        async def slow_receive():
            await asyncio.sleep(0.1)
            return {"type": "http.request", "body": b"", "more_body": False}

        deadline_guard = DocumentRequestGuard(
            slow_downstream,
            max_request_bytes=100,
            body_timeout_seconds=1,
            global_concurrency=1,
            per_principal_concurrency=1,
            app_env="test",
        )
        deadline_guard.body_timeout_seconds = 0.01
        timeout_sent: list[dict[str, object]] = []

        async def timeout_send(message):
            timeout_sent.append(message)

        await deadline_guard(_scope(), slow_receive, timeout_send)
        return second_sent, timeout_sent

    concurrency_sent, timeout_sent = asyncio.run(exercise())
    assert concurrency_sent[0]["status"] == 429
    assert timeout_sent[0]["status"] == 408


def test_ambiguous_or_invalid_body_framing_is_rejected_before_downstream() -> None:
    async def exercise(headers) -> int:
        called = False

        async def downstream(scope, receive, send) -> None:
            nonlocal called
            called = True

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        sent: list[dict[str, object]] = []

        async def send(message):
            sent.append(message)

        scope = _scope()
        scope["headers"] = list(scope["headers"]) + headers
        guard = DocumentRequestGuard(
            downstream,
            max_request_bytes=100,
            body_timeout_seconds=5,
            global_concurrency=1,
            per_principal_concurrency=1,
            app_env="test",
        )
        await guard(scope, receive, send)
        assert called is False
        return int(sent[0]["status"])

    assert asyncio.run(exercise([(b"content-length", b"1"), (b"content-length", b"1")])) == 400
    assert asyncio.run(exercise([(b"content-length", b"1"), (b"transfer-encoding", b"chunked")])) == 400
    assert asyncio.run(exercise([(b"content-length", b"not-a-number")])) == 400
    assert asyncio.run(exercise([(b"content-length", b"+1")])) == 400


def test_duplicate_host_is_rejected_before_downstream() -> None:
    async def exercise() -> tuple[bool, list[dict[str, object]]]:
        called = False

        async def downstream(scope, receive, send) -> None:
            nonlocal called
            called = True

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        sent: list[dict[str, object]] = []

        async def send(message):
            sent.append(message)

        scope = _scope()
        scope["headers"] = list(scope["headers"]) + [(b"host", b"testserver")]
        guard = DocumentRequestGuard(
            downstream,
            max_request_bytes=100,
            body_timeout_seconds=5,
            global_concurrency=1,
            per_principal_concurrency=1,
            app_env="test",
        )
        await guard(scope, receive, send)
        return called, sent

    called, sent = asyncio.run(exercise())
    assert called is False
    assert sent[0]["status"] == 400
