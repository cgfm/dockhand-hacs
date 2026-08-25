"""Tests for the Dockhand API client over a real local aiohttp server."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from aiohttp import ClientSession, ClientTimeout, web

from custom_components.dockhand.api import (
    DockhandApiClient,
    DockhandApiError,
    DockhandAuthError,
    DockhandConnectionError,
    DockhandRateLimitError,
)

CONTAINER_ID = "a" * 64
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


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
