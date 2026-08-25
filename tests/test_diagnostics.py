"""Tests for privacy-preserving Dockhand diagnostics."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dockhand.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.dockhand.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redact_connection_and_resource_metadata(
    hass: HomeAssistant,
) -> None:
    """Diagnostics contain useful counts without operationally sensitive names."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_URL: "https://private-dockhand.example",
            CONF_USERNAME: "private-user",
            CONF_PASSWORD: "private-password",
        },
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        data={
            "environments": {1: {"name": "private-environment"}},
            "containers": {
                "stable": {
                    "name": "private-container",
                    "image": "private.registry/image:secret",
                    "state": "running",
                    "health": "healthy",
                }
            },
            "stacks": {"stack": {"name": "private-stack"}},
            "stats": {"stable": {"cpuPercent": 1}},
        }
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = repr(diagnostics)

    assert diagnostics["coordinator_data"] == {
        "environment_count": 1,
        "container_count": 1,
        "stack_count": 1,
        "stats_count": 1,
        "container_states": {"running": 1},
        "container_health": {"healthy": 1},
    }
    for secret in (
        "private-dockhand",
        "private-user",
        "private-password",
        "private-environment",
        "private-container",
        "private.registry",
        "private-stack",
    ):
        assert secret not in serialized

    container = entry.runtime_data.data["containers"]["stable"]
    container["state"] = "token=do-not-report"
    container["health"] = "password=do-not-report"
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["coordinator_data"]["container_states"] == {"unknown": 1}
    assert diagnostics["coordinator_data"]["container_health"] == {"unknown": 1}
    assert "do-not-report" not in repr(diagnostics)
