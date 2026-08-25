"""Diagnostics support for Dockhand."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from .coordinator import DockhandDataUpdateCoordinator

TO_REDACT = {CONF_PASSWORD, CONF_URL, CONF_USERNAME}
KNOWN_STATES = {
    "created",
    "dead",
    "exited",
    "paused",
    "removing",
    "restarting",
    "running",
}
KNOWN_HEALTH = {"healthy", "not_reported", "starting", "unhealthy"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: DockhandDataUpdateCoordinator = entry.runtime_data

    data = coordinator.data or {}
    containers = data.get("containers", {})
    stacks = data.get("stacks", {})
    state_counts: dict[str, int] = {}
    health_counts: dict[str, int] = {}
    for container in containers.values():
        state = str(container.get("state") or "unknown").lower()
        if state not in KNOWN_STATES:
            state = "unknown"
        state_counts[state] = state_counts.get(state, 0) + 1
        health = str(
            container.get("health") or container.get("healthStatus") or "not_reported"
        ).lower()
        if health not in KNOWN_HEALTH:
            health = "unknown"
        health_counts[health] = health_counts.get(health, 0) + 1

    return {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "coordinator_data": {
            "environment_count": len(data.get("environments", {})),
            "container_count": len(containers),
            "stack_count": len(stacks),
            "stats_count": len(data.get("stats", {})),
            "container_states": state_counts,
            "container_health": health_counts,
        },
    }
