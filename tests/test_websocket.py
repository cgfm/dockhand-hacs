"""Tests for the on-demand Dockhand live-log WebSocket API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import (
    MockHAClientWebSocket,
    WebSocketGenerator,
)

from custom_components.dockhand.api import DockhandApiError
from custom_components.dockhand.const import CONF_URL, DOMAIN
from custom_components.dockhand.websocket import async_active_log_stream_count

CONTAINER_ID = "a" * 64
OTHER_CONTAINER_ID = "b" * 64


@pytest.fixture
async def loaded_entry(hass: HomeAssistant) -> AsyncIterator[MockConfigEntry]:
    """Set up one loaded Dockhand entry without external network access."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-a",
        version=1,
        minor_version=2,
        data={CONF_URL: "https://dockhand.example"},
    )
    entry.add_to_hass(hass)

    with ExitStack() as mocks:
        mocks.enter_context(
            patch(
                "custom_components.dockhand.api.DockhandApiClient.authenticate",
                new=AsyncMock(),
            )
        )
        mocks.enter_context(
            patch(
                "custom_components.dockhand.api.DockhandApiClient.get_environments",
                new=AsyncMock(return_value=[{"id": 1, "name": "Local"}]),
            )
        )
        mocks.enter_context(
            patch(
                "custom_components.dockhand.api.DockhandApiClient.get_containers",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": CONTAINER_ID,
                            "name": "/demo",
                            "image": "example/demo:latest",
                            "state": "running",
                            "status": "Up",
                        }
                    ]
                ),
            )
        )
        mocks.enter_context(
            patch(
                "custom_components.dockhand.api.DockhandApiClient.get_stacks",
                new=AsyncMock(return_value=[]),
            )
        )
        mocks.enter_context(
            patch(
                "custom_components.dockhand.api.DockhandApiClient.get_pending_container_updates",
                new=AsyncMock(return_value=[]),
            )
        )
        mocks.enter_context(
            patch(
                "custom_components.dockhand.api.DockhandApiClient.get_container_inspect",
                new=AsyncMock(return_value={}),
            )
        )
        mocks.enter_context(
            patch(
                "custom_components.dockhand.api.DockhandApiClient.get_container_stats",
                new=AsyncMock(return_value={}),
            )
        )

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry

        if entry.state is ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)


async def _subscribe(
    client: MockHAClientWebSocket,
    entry: MockConfigEntry,
    **changes: Any,
) -> int:
    """Send a Dockhand log subscription and return its message ID."""
    message = {
        "type": "dockhand/subscribe_logs",
        "entry_id": entry.entry_id,
        "container_id": CONTAINER_ID[:12],
        "env_id": 1,
        "tail": 200,
        **changes,
    }
    await client.send_json_auto_id(message)
    return message["id"]


async def _unsubscribe(client: MockHAClientWebSocket, subscription_id: int) -> None:
    """Unsubscribe from a WebSocket subscription and assert success."""
    await client.send_json_auto_id(
        {"type": "unsubscribe_events", "subscription": subscription_id}
    )
    response = await client.receive_json()
    assert response["success"]


async def test_admin_can_subscribe_and_events_are_forwarded(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """An administrator receives normalized events from exactly one task."""
    stop = asyncio.Event()
    cancelled = asyncio.Event()

    async def stream(
        container_id: str, env_id: int, tail: int
    ) -> AsyncIterator[dict[str, Any]]:
        assert (container_id, env_id, tail) == (CONTAINER_ID, 1, 200)
        try:
            yield {
                "event": "connected",
                "container_id": container_id,
                "container_name": "demo",
            }
            yield {
                "event": "log",
                "text": "application ready\n",
                "stream": "stdout",
                "container_name": "demo",
            }
            await stop.wait()
        finally:
            cancelled.set()

    client_api = loaded_entry.runtime_data.client
    with patch.object(client_api, "stream_container_logs", new=stream):
        client = await hass_ws_client(hass)
        subscription_id = await _subscribe(client, loaded_entry)

        result = await client.receive_json()
        assert result["id"] == subscription_id
        assert result["type"] == "result"
        assert result["success"]
        connected = await client.receive_json()
        log = await client.receive_json()
        assert connected["event"] == {
            "event": "connected",
            "container_id": CONTAINER_ID,
            "container_name": "demo",
        }
        assert log["event"] == {
            "event": "log",
            "text": "application ready\n",
            "stream": "stdout",
            "container_name": "demo",
        }
        assert async_active_log_stream_count(hass, loaded_entry.entry_id) == 1

        await _unsubscribe(client, subscription_id)
        async with asyncio.timeout(1):
            await cancelled.wait()
        assert async_active_log_stream_count(hass, loaded_entry.entry_id) == 0


async def test_non_admin_cannot_subscribe(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    """The WebSocket command rejects authenticated non-admin users."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _subscribe(client, loaded_entry)

    result = await client.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "unauthorized"
    assert async_active_log_stream_count(hass) == 0


async def test_invalid_config_entry_is_rejected(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Only a loaded Dockhand config entry can supply the API client."""
    client = await hass_ws_client(hass)
    await _subscribe(client, loaded_entry, entry_id="missing-entry")

    result = await client.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "invalid_entry"
    assert async_active_log_stream_count(hass) == 0


async def test_invalid_container_id_is_rejected_by_schema(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A container ID cannot alter the fixed Dockhand URL path."""
    client = await hass_ws_client(hass)
    await _subscribe(client, loaded_entry, container_id="../secrets")

    result = await client.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "invalid_format"
    assert async_active_log_stream_count(hass) == 0


async def test_container_must_belong_to_the_selected_entry_and_environment(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A syntactically valid arbitrary container cannot bypass the coordinator."""
    client = await hass_ws_client(hass)
    await _subscribe(client, loaded_entry, container_id=OTHER_CONTAINER_ID[:12])

    result = await client.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "invalid_container"
    assert async_active_log_stream_count(hass) == 0


@pytest.mark.parametrize("env_id", [0, True, "1"])
async def test_invalid_environment_id_is_rejected_by_schema(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    env_id: Any,
) -> None:
    """Environment IDs must be positive JSON integers."""
    client = await hass_ws_client(hass)
    await _subscribe(client, loaded_entry, env_id=env_id)

    result = await client.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "invalid_format"
    assert async_active_log_stream_count(hass) == 0


async def test_unselected_environment_is_rejected(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A valid ID cannot access an environment outside the coordinator snapshot."""
    client = await hass_ws_client(hass)
    await _subscribe(client, loaded_entry, env_id=2)

    result = await client.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "invalid_environment"
    assert async_active_log_stream_count(hass) == 0


@pytest.mark.parametrize("tail", [0, 5001, True, "200"])
async def test_invalid_tail_is_rejected(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    tail: Any,
) -> None:
    """Log history requests are strictly bounded to safe integers."""
    client = await hass_ws_client(hass)
    await _subscribe(client, loaded_entry, tail=tail)

    result = await client.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "invalid_format"
    assert async_active_log_stream_count(hass) == 0


async def test_api_error_is_sanitized_and_task_is_reaped(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Upstream error details are not leaked and the failed task is removed."""

    async def stream(
        _container_id: str, _env_id: int, _tail: int
    ) -> AsyncIterator[dict[str, Any]]:
        if False:
            yield {}
        raise DockhandApiError("password=do-not-expose")

    with patch.object(
        loaded_entry.runtime_data.client, "stream_container_logs", new=stream
    ):
        client = await hass_ws_client(hass)
        subscription_id = await _subscribe(client, loaded_entry)
        assert (await client.receive_json())["success"]
        event = await client.receive_json()
        assert event["id"] == subscription_id
        assert event["event"] == {
            "event": "error",
            "error": "Unable to stream Dockhand logs",
        }
        await asyncio.sleep(0)
        assert async_active_log_stream_count(hass, loaded_entry.entry_id) == 0
        assert "do-not-expose" not in repr(event)


async def test_unsubscribe_cancels_the_stream_task(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Unsubscribe cancellation reaches the active async generator."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def stream(
        _container_id: str, _env_id: int, _tail: int
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            started.set()
            await asyncio.Event().wait()
            if False:
                yield {}
        finally:
            cancelled.set()

    with patch.object(
        loaded_entry.runtime_data.client, "stream_container_logs", new=stream
    ):
        client = await hass_ws_client(hass)
        subscription_id = await _subscribe(client, loaded_entry)
        assert (await client.receive_json())["success"]
        async with asyncio.timeout(1):
            await started.wait()

        await _unsubscribe(client, subscription_id)
        async with asyncio.timeout(1):
            await cancelled.wait()
        assert async_active_log_stream_count(hass, loaded_entry.entry_id) == 0


async def test_config_entry_unload_cancels_active_streams(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Reload/unload cannot leave an upstream SSE connection behind."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def stream(
        _container_id: str, _env_id: int, _tail: int
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            started.set()
            await asyncio.Event().wait()
            if False:
                yield {}
        finally:
            cancelled.set()

    with patch.object(
        loaded_entry.runtime_data.client, "stream_container_logs", new=stream
    ):
        client = await hass_ws_client(hass)
        await _subscribe(client, loaded_entry)
        assert (await client.receive_json())["success"]
        async with asyncio.timeout(1):
            await started.wait()

        assert await hass.config_entries.async_unload(loaded_entry.entry_id)
        async with asyncio.timeout(1):
            await cancelled.wait()
        event = await client.receive_json()
        assert event["event"] == {
            "event": "end",
            "reason": "Dockhand integration entry was unloaded",
        }
        assert async_active_log_stream_count(hass, loaded_entry.entry_id) == 0


async def test_multiple_viewers_have_independent_tasks(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Stopping one viewer does not affect another viewer's stream."""
    started = 0
    cancelled = 0
    both_started = asyncio.Event()

    async def stream(
        _container_id: str, _env_id: int, _tail: int
    ) -> AsyncIterator[dict[str, Any]]:
        nonlocal started, cancelled
        try:
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.Event().wait()
            if False:
                yield {}
        finally:
            cancelled += 1

    with patch.object(
        loaded_entry.runtime_data.client, "stream_container_logs", new=stream
    ):
        first = await hass_ws_client(hass)
        second = await hass_ws_client(hass)
        first_id = await _subscribe(first, loaded_entry)
        second_id = await _subscribe(second, loaded_entry)
        assert (await first.receive_json())["success"]
        assert (await second.receive_json())["success"]
        async with asyncio.timeout(1):
            await both_started.wait()
        assert async_active_log_stream_count(hass, loaded_entry.entry_id) == 2

        await _unsubscribe(first, first_id)
        await asyncio.sleep(0)
        assert cancelled == 1
        assert async_active_log_stream_count(hass, loaded_entry.entry_id) == 1

        await _unsubscribe(second, second_id)
        await asyncio.sleep(0)
        assert cancelled == 2
        assert async_active_log_stream_count(hass, loaded_entry.entry_id) == 0
