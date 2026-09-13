"""WebSocket API for on-demand Dockhand container log streams."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Coroutine
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.typing import VolDictType

from .api import (
    DockhandApiError,
    DockhandAuthError,
    DockhandConnectionError,
    _validated_container_id,
)
from .const import DOMAIN
from .coordinator import DockhandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

DATA_LOG_STREAM_MANAGER = f"{DOMAIN}_log_stream_manager"
WS_TYPE_SUBSCRIBE_LOGS = f"{DOMAIN}/subscribe_logs"
DEFAULT_LOG_TAIL = 200
MAX_LOG_TAIL = 5000


def _strict_positive_int(value: Any) -> int:
    """Validate a JSON integer without accepting booleans or strings."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise vol.Invalid("value must be a positive integer")
    return value


def _valid_tail(value: Any) -> int:
    """Validate the requested initial number of log lines."""
    value = _strict_positive_int(value)
    if value > MAX_LOG_TAIL:
        raise vol.Invalid(f"value must be at most {MAX_LOG_TAIL}")
    return value


def _valid_container_id(value: Any) -> str:
    """Validate a container ID before it can reach a URL path."""
    if not isinstance(value, str):
        raise vol.Invalid("container_id must be a string")
    try:
        return _validated_container_id(value)
    except DockhandApiError:
        raise vol.Invalid("invalid container_id") from None


SUBSCRIBE_LOGS_SCHEMA: VolDictType = {
    vol.Required("type"): WS_TYPE_SUBSCRIBE_LOGS,
    vol.Required("entry_id"): vol.All(str, vol.Length(min=1)),
    vol.Required("container_id"): _valid_container_id,
    vol.Required("env_id"): _strict_positive_int,
    vol.Optional("tail", default=DEFAULT_LOG_TAIL): _valid_tail,
}


class DockhandLogStreamManager:
    """Track live log tasks so entry unload can deterministically stop them."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the stream task registry."""
        self._hass = hass
        self._tasks: defaultdict[str, set[asyncio.Task[None]]] = defaultdict(set)

    @callback
    def async_create_task(
        self,
        entry: ConfigEntry,
        target: Coroutine[Any, Any, None],
    ) -> asyncio.Task[None]:
        """Create one background task tied to a Dockhand config entry."""
        task = entry.async_create_background_task(
            self._hass,
            target,
            "Dockhand live container logs",
            eager_start=False,
        )
        tasks = self._tasks[entry.entry_id]
        tasks.add(task)

        @callback
        def task_done(completed: asyncio.Task[None]) -> None:
            tasks.discard(completed)
            if not tasks and self._tasks.get(entry.entry_id) is tasks:
                self._tasks.pop(entry.entry_id, None)

        task.add_done_callback(task_done)
        return task

    async def async_cancel_entry(self, entry_id: str) -> None:
        """Cancel and await every active stream for one config entry."""
        tasks = tuple(self._tasks.pop(entry_id, ()))
        for task in tasks:
            task.cancel("config_entry_unload")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @callback
    def async_active_count(self, entry_id: str | None = None) -> int:
        """Return the number of active live-log stream tasks."""
        if entry_id is not None:
            return len(self._tasks.get(entry_id, ()))
        return sum(len(tasks) for tasks in self._tasks.values())


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register the Dockhand WebSocket command exactly once."""
    if DATA_LOG_STREAM_MANAGER in hass.data:
        return
    hass.data[DATA_LOG_STREAM_MANAGER] = DockhandLogStreamManager(hass)
    websocket_api.async_register_command(hass, websocket_subscribe_logs)


async def async_cancel_log_streams(hass: HomeAssistant, entry_id: str) -> None:
    """Stop all active streams for an unloading config entry."""
    if manager := hass.data.get(DATA_LOG_STREAM_MANAGER):
        await manager.async_cancel_entry(entry_id)


@callback
def async_active_log_stream_count(
    hass: HomeAssistant, entry_id: str | None = None
) -> int:
    """Return the active stream count for diagnostics and tests."""
    if manager := hass.data.get(DATA_LOG_STREAM_MANAGER):
        return manager.async_active_count(entry_id)
    return 0


def _resolve_container_id(
    coordinator: DockhandDataUpdateCoordinator,
    requested_id: str,
    env_id: int,
) -> str | None:
    """Resolve a validated full or short ID within the selected environment."""
    matches: list[str] = []
    for container in coordinator.data.get("containers", {}).values():
        runtime_id = container.get("id")
        if (
            container.get("environment_id") == env_id
            and isinstance(runtime_id, str)
            and runtime_id.startswith(requested_id)
        ):
            matches.append(runtime_id)
    return matches[0] if len(matches) == 1 else None


async def _async_forward_log_stream(
    connection: websocket_api.ActiveConnection,
    message_id: int,
    coordinator: DockhandDataUpdateCoordinator,
    container_id: str,
    env_id: int,
    tail: int,
) -> None:
    """Forward one Dockhand SSE stream to one Home Assistant client."""
    sent_end = False
    try:
        async for event in coordinator.client.stream_container_logs(
            container_id, env_id, tail
        ):
            if event.get("event") == "error":
                event = {
                    "event": "error",
                    "error": "Dockhand reported a log stream error",
                }
            elif event.get("event") == "end":
                sent_end = True
            connection.send_event(message_id, event)
        if not sent_end:
            connection.send_event(
                message_id,
                {"event": "end", "reason": "Dockhand closed the log stream"},
            )
    except asyncio.CancelledError as err:
        if err.args == ("config_entry_unload",):
            connection.send_event(
                message_id,
                {
                    "event": "end",
                    "reason": "Dockhand integration entry was unloaded",
                },
            )
        raise
    except DockhandAuthError:
        connection.send_event(
            message_id,
            {
                "event": "error",
                "error": "Dockhand denied access to the log stream",
            },
        )
    except DockhandConnectionError:
        connection.send_event(
            message_id,
            {
                "event": "error",
                "error": "The Dockhand log stream is unavailable",
            },
        )
    except DockhandApiError:
        connection.send_event(
            message_id,
            {"event": "error", "error": "Unable to stream Dockhand logs"},
        )
    except Exception:
        # Do not log exception text here: third-party exceptions can contain
        # upstream payloads, internal addresses, or other sensitive details.
        _LOGGER.error("Unexpected failure in a Dockhand live-log stream")
        connection.send_event(
            message_id,
            {"event": "error", "error": "Unexpected Dockhand log stream error"},
        )


@websocket_api.websocket_command(SUBSCRIBE_LOGS_SCHEMA)
@websocket_api.require_admin
@callback
def websocket_subscribe_logs(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Subscribe an administrator to one on-demand container log stream."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
    ):
        connection.send_error(
            msg["id"], "invalid_entry", "Dockhand config entry is not loaded"
        )
        return

    coordinator: DockhandDataUpdateCoordinator = entry.runtime_data
    env_id = msg["env_id"]
    if env_id not in coordinator.data.get("environments", {}):
        connection.send_error(
            msg["id"], "invalid_environment", "Dockhand environment is not available"
        )
        return

    container_id = _resolve_container_id(coordinator, msg["container_id"], env_id)
    if container_id is None:
        connection.send_error(
            msg["id"], "invalid_container", "Dockhand container is not available"
        )
        return

    manager: DockhandLogStreamManager = hass.data[DATA_LOG_STREAM_MANAGER]
    task = manager.async_create_task(
        entry,
        _async_forward_log_stream(
            connection,
            msg["id"],
            coordinator,
            container_id,
            env_id,
            msg["tail"],
        ),
    )

    @callback
    def unsubscribe() -> None:
        """Stop this viewer's upstream Dockhand SSE connection."""
        if not task.done():
            task.cancel()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
