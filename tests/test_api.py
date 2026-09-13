"""Tests for the Dockhand API client over a real local aiohttp server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from aiohttp import ClientSession, ClientTimeout, web

from custom_components.dockhand.api import (
    DockhandApiClient,
    DockhandApiError,
    DockhandAuthError,
    DockhandConnectionError,
    DockhandRateLimitError,
    _iter_sse_events,
    _normalize_sse_event,
)

CONTAINER_ID = "a" * 64
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


async def _chunks(*chunks: bytes) -> AsyncIterator[bytes]:
    """Yield byte chunks with async-stream semantics."""
    for chunk in chunks:
        yield chunk


async def _parsed(*chunks: bytes) -> list[tuple[str, str]]:
    """Collect parsed SSE events from arbitrary transport chunks."""
    return [event async for event in _iter_sse_events(_chunks(*chunks))]


async def test_sse_parser_reads_one_complete_event() -> None:
    """One complete SSE event is emitted once."""
    assert await _parsed(b"event: log\ndata: hello\n\n") == [("log", "hello")]


async def test_sse_parser_reads_multiple_events_in_one_chunk() -> None:
    """One aiohttp chunk can contain multiple SSE events."""
    assert await _parsed(
        b"event: connected\ndata: {}\n\nevent: end\ndata: done\n\n"
    ) == [("connected", "{}"), ("end", "done")]


async def test_sse_parser_handles_lines_and_utf8_split_across_chunks() -> None:
    """TCP boundaries can split both SSE lines and UTF-8 code points."""
    encoded = "event: log\ndata: Gr\u00fc\u00dfe\n\n".encode()
    split = encoded.index("\u00fc".encode()) + 1
    assert await _parsed(encoded[:8], encoded[8:split], encoded[split:]) == [
        ("log", "Gr\u00fc\u00dfe")
    ]


async def test_sse_parser_joins_json_data_lines() -> None:
    """Multiple data fields use the SSE newline joining rule."""
    assert await _parsed(b'event: log\ndata: {"text":\ndata: "hello"}\n\n') == [
        ("log", '{"text":\n"hello"}')
    ]


@pytest.mark.parametrize(
    ("event_name", "data", "expected"),
    [
        (
            "connected",
            '{"containerName":"demo"}',
            {
                "event": "connected",
                "container_id": CONTAINER_ID,
                "container_name": "demo",
            },
        ),
        (
            "log",
            '{"text":"ready\\n","containerName":"demo","stream":"stderr"}',
            {
                "event": "log",
                "text": "ready\n",
                "container_name": "demo",
                "stream": "stderr",
            },
        ),
        ("error", '{"error":"failed"}', {"event": "error", "error": "failed"}),
        ("end", '{"reason":"stopped"}', {"event": "end", "reason": "stopped"}),
    ],
)
def test_sse_events_are_normalized(
    event_name: str, data: str, expected: dict[str, Any]
) -> None:
    """Dockhand's relevant JSON event payloads use one stable shape."""
    assert _normalize_sse_event(event_name, data, CONTAINER_ID) == expected


async def test_sse_parser_ignores_comments_and_supports_crlf_and_lf() -> None:
    """Heartbeat comments never become frontend log events."""
    assert await _parsed(
        b": keepalive\r\n\r\nevent: connected\r\ndata: {}\r\n\r\n",
        b": next heartbeat\n\nevent: end\ndata: eof\n\n",
    ) == [("connected", "{}"), ("end", "eof")]


async def test_sse_parser_flushes_an_event_at_stream_end() -> None:
    """A final event is not lost when EOF arrives without a blank line."""
    assert await _parsed(b"event: end\ndata: shutdown") == [("end", "shutdown")]


async def test_sse_unknown_event_is_ignored_by_normalizer() -> None:
    """Future Dockhand event types cannot crash or leak into the card."""
    assert _normalize_sse_event("future-event", "{}", CONTAINER_ID) is None


async def test_container_log_stream_uses_sse_route_headers_cookies_and_query(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """The live stream reuses authentication and has no read timeout."""

    async def logs(request: web.Request) -> web.StreamResponse:
        assert request.headers["Accept"] == "text/event-stream"
        assert request.cookies["session"] == "existing-session"
        assert request.query == {"env": "7", "tail": "321"}
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b': keepalive\n\nevent: connected\ndata: {"container')
        await response.write(b'Name":"demo"}\n\nevent: log\r\ndata: ')
        await response.write(
            b'{"text":"ready\\n","stream":"stdout","containerName":"demo"}\r\n\r\n'
        )
        await response.write_eof()
        return response

    client = await _client_for(
        aiohttp_server,
        [("GET", f"/api/containers/{CONTAINER_ID}/logs/stream", logs)],
    )
    client._cookies["session"] = "existing-session"
    try:
        events = [
            event async for event in client.stream_container_logs(CONTAINER_ID, 7, 321)
        ]
        assert events == [
            {
                "event": "connected",
                "container_id": CONTAINER_ID,
                "container_name": "demo",
            },
            {
                "event": "log",
                "text": "ready\n",
                "stream": "stdout",
                "container_name": "demo",
            },
        ]
        assert client._stream_timeout.total is None
        assert client._stream_timeout.sock_read is None
    finally:
        await client.close()


async def test_container_log_stream_reauthenticates_once(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """An expired stream cookie uses the client's serialized auth retry."""
    stream_calls = 0

    async def logs(_request: web.Request) -> web.Response:
        nonlocal stream_calls
        stream_calls += 1
        if stream_calls == 1:
            return web.Response(status=401)
        return web.Response(
            text="event: end\ndata: complete\n\n",
            content_type="text/event-stream",
        )

    async def auth(_request: web.Request) -> web.Response:
        return web.json_response({"authEnabled": False, "authenticated": False})

    client = await _client_for(
        aiohttp_server,
        [
            ("GET", f"/api/containers/{CONTAINER_ID}/logs/stream", logs),
            ("GET", "/api/auth/session", auth),
        ],
    )
    try:
        assert [
            event async for event in client.stream_container_logs(CONTAINER_ID, 1)
        ] == [{"event": "end", "reason": "complete"}]
        assert stream_calls == 2
    finally:
        await client.close()


async def test_container_log_stream_rejects_permission_error(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """Dockhand HTTP 403 is an authentication/permission error."""

    async def forbidden(_request: web.Request) -> web.Response:
        return web.Response(status=403, text="secret upstream details")

    client = await _client_for(
        aiohttp_server,
        [("GET", f"/api/containers/{CONTAINER_ID}/logs/stream", forbidden)],
    )
    try:
        with pytest.raises(DockhandAuthError, match="Permission denied"):
            async for _event in client.stream_container_logs(CONTAINER_ID, 1):
                pass
    finally:
        await client.close()


async def test_container_log_stream_cancellation_closes_http_connection(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """Cancelling iteration promptly disconnects the upstream SSE response."""
    disconnected = asyncio.Event()

    async def logs(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b"event: connected\ndata: {}\n\n")
        try:
            while True:
                await asyncio.sleep(0.01)
                await response.write(b": keepalive\n\n")
        except ConnectionResetError, RuntimeError:
            return response
        finally:
            disconnected.set()

    client = await _client_for(
        aiohttp_server,
        [("GET", f"/api/containers/{CONTAINER_ID}/logs/stream", logs)],
    )
    stream = client.stream_container_logs(CONTAINER_ID, 1)
    try:
        assert (await anext(stream))["event"] == "connected"
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        async with asyncio.timeout(1):
            await disconnected.wait()
    finally:
        await stream.aclose()
        await client.close()


async def _client_for(
    aiohttp_server: Any,
    routes: list[tuple[str, str, Handler]],
) -> DockhandApiClient:
    """Start a local HTTP server and return its Dockhand client."""
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    server = await aiohttp_server(app)
    return DockhandApiClient(str(server.make_url("")).rstrip("/"), "user", "secret")


async def test_get_pending_container_updates(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """The cached-update GET is validated and reduced to safe fields."""

    async def pending(request: web.Request) -> web.Response:
        assert request.method == "GET"
        assert request.query == {"env": "7"}
        return web.json_response(
            {
                "environmentId": 7,
                "pendingUpdates": [
                    {
                        "containerId": CONTAINER_ID,
                        "containerName": "demo",
                        "currentImage": "example/demo:latest",
                        "checkedAt": "2026-09-13T17:22:03Z",
                        "unexpected": "not-retained",
                    }
                ],
            }
        )

    client = await _client_for(
        aiohttp_server, [("GET", "/api/containers/check-updates", pending)]
    )
    try:
        assert await client.get_pending_container_updates(7) == [
            {
                "container_id": CONTAINER_ID,
                "container_name": "demo",
                "current_image": "example/demo:latest",
                "checked_at": "2026-09-13T17:22:03Z",
            }
        ]
    finally:
        await client.close()


async def test_get_pending_container_updates_accepts_empty_list(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """An environment without pending images is a valid response."""

    async def pending(_request: web.Request) -> web.Response:
        return web.json_response({"environmentId": 1, "pendingUpdates": []})

    client = await _client_for(
        aiohttp_server, [("GET", "/api/containers/check-updates", pending)]
    )
    try:
        assert await client.get_pending_container_updates(1) == []
    finally:
        await client.close()


async def test_check_container_updates_requests_json_with_action_timeout(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """Manual checks use POST, JSON negotiation, and the long action timeout."""

    async def check(request: web.Request) -> web.Response:
        assert request.method == "POST"
        assert request.query == {"env": "3"}
        assert request.headers["Accept"] == "application/json"
        return web.json_response(
            {
                "total": 2,
                "updatesFound": 1,
                "results": [
                    {"containerId": CONTAINER_ID, "hasUpdate": True},
                    {"imageName": "rate-limited-image", "error": "rate limited"},
                ],
            }
        )

    client = await _client_for(
        aiohttp_server, [("POST", "/api/containers/check-updates", check)]
    )
    try:
        result = await client.check_container_updates(3)
        assert result["updatesFound"] == 1
        assert len(result["results"]) == 2
        assert client._action_timeout.total == 5 * 60
    finally:
        await client.close()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"environmentId": 2, "pendingUpdates": []},
        {"environmentId": 1, "pendingUpdates": "invalid"},
        {"environmentId": 1, "pendingUpdates": [{"containerId": "invalid"}]},
    ],
)
async def test_pending_update_response_is_validated(
    aiohttp_server: Any, socket_enabled: None, payload: Any
) -> None:
    """Malformed cached data cannot be mistaken for an empty update set."""

    async def pending(_request: web.Request) -> web.Response:
        return web.json_response(payload)

    client = await _client_for(
        aiohttp_server, [("GET", "/api/containers/check-updates", pending)]
    )
    try:
        with pytest.raises(DockhandApiError, match="invalid pending update"):
            await client.get_pending_container_updates(1)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"total": True, "updatesFound": 0, "results": []},
        {"total": 1, "updatesFound": 2, "results": []},
        {"total": 1, "updatesFound": 0, "results": "invalid"},
        {"total": 1, "updatesFound": 0, "results": ["invalid"]},
    ],
)
async def test_update_check_response_is_validated(
    aiohttp_server: Any, socket_enabled: None, payload: Any
) -> None:
    """Malformed manual-check summaries raise a safe API error."""

    async def check(_request: web.Request) -> web.Response:
        return web.json_response(payload)

    client = await _client_for(
        aiohttp_server, [("POST", "/api/containers/check-updates", check)]
    )
    try:
        with pytest.raises(DockhandApiError, match="invalid image update check"):
            await client.check_container_updates(1)
    finally:
        await client.close()


async def test_pending_updates_maps_permission_error(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """A forbidden cached-update endpoint uses the existing auth exception."""

    async def forbidden(_request: web.Request) -> web.Response:
        return web.Response(status=403, text="private-registry-details")

    client = await _client_for(
        aiohttp_server, [("GET", "/api/containers/check-updates", forbidden)]
    )
    try:
        with pytest.raises(DockhandAuthError, match="Permission denied"):
            await client.get_pending_container_updates(1)
    finally:
        await client.close()


async def test_pending_updates_maps_rejected_reauthentication(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """A repeated 401 from the new endpoint remains an authentication error."""

    async def unauthorized(_request: web.Request) -> web.Response:
        return web.Response(status=401)

    async def auth(_request: web.Request) -> web.Response:
        return web.json_response({"authEnabled": False, "authenticated": False})

    client = await _client_for(
        aiohttp_server,
        [
            ("GET", "/api/containers/check-updates", unauthorized),
            ("GET", "/api/auth/session", auth),
        ],
    )
    try:
        with pytest.raises(DockhandAuthError, match="Authentication failed"):
            await client.get_pending_container_updates(1)
    finally:
        await client.close()


async def test_update_check_maps_registry_rate_limit(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """HTTP registry rate limits remain a bounded Dockhand rate-limit error."""

    async def limited(_request: web.Request) -> web.Response:
        return web.Response(status=429, headers={"Retry-After": "90"})

    client = await _client_for(
        aiohttp_server, [("POST", "/api/containers/check-updates", limited)]
    )
    try:
        with pytest.raises(DockhandRateLimitError) as raised:
            await client.check_container_updates(1)
        assert raised.value.retry_after == 90
    finally:
        await client.close()


async def test_anonymous_authentication_and_environments(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """An installation with disabled authentication does not attempt login."""

    async def auth(_request: web.Request) -> web.Response:
        return web.json_response({"authEnabled": False, "authenticated": False})

    async def environments(_request: web.Request) -> web.Response:
        return web.json_response([{"id": 1}])

    client = await _client_for(
        aiohttp_server,
        [
            ("GET", "/api/auth/session", auth),
            ("GET", "/api/environments", environments),
        ],
    )
    try:
        assert await client.test_connection() == {
            "environments": 1,
            "auth_enabled": False,
        }
    finally:
        await client.close()


async def test_login_and_cookie_authentication(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """Enabled authentication submits credentials and reuses the session cookie."""
    received_login: dict[str, Any] = {}

    async def auth(_request: web.Request) -> web.Response:
        return web.json_response({"authEnabled": True, "authenticated": False})

    async def login(request: web.Request) -> web.Response:
        received_login.update(await request.json())
        response = web.json_response({"success": True})
        response.set_cookie("session", "test-session")
        return response

    async def environments(request: web.Request) -> web.Response:
        assert request.cookies["session"] == "test-session"
        return web.json_response([])

    client = await _client_for(
        aiohttp_server,
        [
            ("GET", "/api/auth/session", auth),
            ("POST", "/api/auth/login", login),
            ("GET", "/api/environments", environments),
        ],
    )
    try:
        await client.test_connection()
        assert received_login == {
            "username": "user",
            "password": "secret",
            "provider": "local",
        }
    finally:
        await client.close()


async def test_401_reauthenticates_once(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """An expired session is reauthenticated once before retrying the request."""
    calls = 0

    async def environments(_request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return web.json_response({}, status=401)
        return web.json_response([{"id": 2}])

    async def auth(_request: web.Request) -> web.Response:
        return web.json_response({"authEnabled": False, "authenticated": False})

    client = await _client_for(
        aiohttp_server,
        [
            ("GET", "/api/environments", environments),
            ("GET", "/api/auth/session", auth),
        ],
    )
    try:
        assert await client.get_environments() == [{"id": 2}]
        assert calls == 2
    finally:
        await client.close()


async def test_concurrent_401_requests_share_one_login(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """Parallel stats-like requests do not create a credential login storm."""
    login_calls = 0

    async def environments(request: web.Request) -> web.Response:
        if request.cookies.get("session") != "valid":
            return web.json_response({}, status=401)
        return web.json_response([{"id": 1}])

    async def auth(request: web.Request) -> web.Response:
        authenticated = request.cookies.get("session") == "valid"
        return web.json_response({"authEnabled": True, "authenticated": authenticated})

    async def login(_request: web.Request) -> web.Response:
        nonlocal login_calls
        login_calls += 1
        response = web.json_response({"success": True})
        response.set_cookie("session", "valid")
        return response

    client = await _client_for(
        aiohttp_server,
        [
            ("GET", "/api/environments", environments),
            ("GET", "/api/auth/session", auth),
            ("POST", "/api/auth/login", login),
        ],
    )
    try:
        results = await asyncio.gather(
            client.get_environments(), client.get_environments()
        )
        assert results == [[{"id": 1}], [{"id": 1}]]
        assert login_calls == 1
    finally:
        await client.close()


async def test_second_401_is_auth_error(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """A rejected retried request is surfaced as an authentication failure."""

    async def unauthorized(_request: web.Request) -> web.Response:
        return web.json_response({}, status=401)

    async def auth(_request: web.Request) -> web.Response:
        return web.json_response({"authEnabled": False, "authenticated": False})

    client = await _client_for(
        aiohttp_server,
        [
            ("GET", "/api/environments", unauthorized),
            ("GET", "/api/auth/session", auth),
        ],
    )
    try:
        with pytest.raises(DockhandAuthError):
            await client.get_environments()
    finally:
        await client.close()


async def test_timeout_is_connection_error(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """Transport timeouts use the coordinator's retryable connection error."""

    async def slow(_request: web.Request) -> web.Response:
        await asyncio.sleep(0.1)
        return web.json_response([])

    client = await _client_for(aiohttp_server, [("GET", "/api/environments", slow)])
    client._timeout = ClientTimeout(total=0.01)
    try:
        with pytest.raises(DockhandConnectionError):
            await client.get_environments()
    finally:
        await client.close()


async def test_invalid_json_is_api_error(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """Invalid JSON cannot silently become an empty payload."""

    async def invalid(_request: web.Request) -> web.Response:
        return web.Response(text="not-json", content_type="application/json")

    client = await _client_for(aiohttp_server, [("GET", "/api/environments", invalid)])
    try:
        with pytest.raises(DockhandApiError, match="invalid JSON"):
            await client.get_environments()
    finally:
        await client.close()


async def test_rate_limit_is_bounded(aiohttp_server: Any, socket_enabled: None) -> None:
    """Retry-After values cannot stall updates indefinitely."""

    async def limited(_request: web.Request) -> web.Response:
        return web.Response(status=429, headers={"Retry-After": "900"})

    client = await _client_for(aiohttp_server, [("GET", "/api/environments", limited)])
    try:
        with pytest.raises(DockhandRateLimitError) as raised:
            await client.get_environments()
        assert raised.value.retry_after == 300
    finally:
        await client.close()


async def test_error_body_is_not_exposed(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """Arbitrary upstream error bodies are not copied into HA logs or UI errors."""

    async def failed(_request: web.Request) -> web.Response:
        return web.Response(status=500, text="password=do-not-log")

    client = await _client_for(aiohttp_server, [("GET", "/api/environments", failed)])
    try:
        with pytest.raises(DockhandApiError) as raised:
            await client.get_environments()
        assert "do-not-log" not in str(raised.value)
    finally:
        await client.close()


@pytest.mark.parametrize("container_id", ["../secret", "short", "g" * 64])
async def test_container_id_is_validated(container_id: str) -> None:
    """Untrusted runtime IDs cannot alter API paths."""
    client = DockhandApiClient("http://dockhand.invalid")
    try:
        with pytest.raises(DockhandApiError, match="invalid container ID"):
            await client.get_container_inspect(container_id, 1)
    finally:
        await client.close()


async def test_inspect_and_actions_use_expected_routes(
    aiohttp_server: Any, socket_enabled: None
) -> None:
    """Inspect and lifecycle calls target only explicit Dockhand routes."""

    async def inspect(request: web.Request) -> web.Response:
        assert request.query == {"env": "7"}
        return web.json_response({"Id": CONTAINER_ID})

    async def restart(request: web.Request) -> web.Response:
        assert request.query == {"env": "7"}
        return web.json_response({"success": True})

    client = await _client_for(
        aiohttp_server,
        [
            ("GET", f"/api/containers/{CONTAINER_ID}/inspect", inspect),
            ("POST", f"/api/containers/{CONTAINER_ID}/restart", restart),
        ],
    )
    try:
        assert await client.get_container_inspect(CONTAINER_ID, 7) == {
            "Id": CONTAINER_ID
        }
        assert await client.container_action(CONTAINER_ID, "restart", 7) == {
            "success": True
        }
    finally:
        await client.close()


async def test_unsupported_action_is_rejected() -> None:
    """Only the explicitly supported container actions can reach the API."""
    client = DockhandApiClient("http://dockhand.invalid")
    try:
        with pytest.raises(DockhandApiError, match="Unsupported"):
            await client.container_action(CONTAINER_ID, "delete", 1)
    finally:
        await client.close()


async def test_injected_session_is_not_closed() -> None:
    """The HA-owned shared session remains open when the client is closed."""
    session = ClientSession()
    client = DockhandApiClient("http://dockhand.invalid", session=session)

    await client.close()

    assert not session.closed
    await session.close()
