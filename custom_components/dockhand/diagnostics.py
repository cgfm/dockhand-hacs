"""Diagnostics support for Dockhand."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_USERNAME
from .coordinator import DockhandDataUpdateCoordinator

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: DockhandDataUpdateCoordinator = entry.runtime_data

    # Redact sensitive data
    redacted_data = {
        k: "**REDACTED**" if k in TO_REDACT else v
        for k, v in entry.data.items()
    }

    data = coordinator.data or {}

    # Summarize containers (don't leak full IDs)
    containers_summary = []
    for key, container in data.get("containers", {}).items():
        containers_summary.append(
            {
                "name": container.get("name", "unknown"),
                "state": container.get("state", "unknown"),
                "image": container.get("image", ""),
                "environment": container.get("environment_name", ""),
                "has_stats": key in data.get("stats", {}),
            }
        )

    environments_summary = []
    for env_id, env in data.get("environments", {}).items():
        environments_summary.append(
            {
                "id": env_id,
                "name": env.get("name", ""),
                "connection_type": env.get("connectionType", ""),
            }
        )

    return {
        "config": redacted_data,
        "coordinator_data": {
            "environments": environments_summary,
            "containers": containers_summary,
            "container_count": len(containers_summary),
            "stats_count": len(data.get("stats", {})),
        },
    }
