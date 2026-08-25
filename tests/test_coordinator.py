"""Tests for Dockhand coordinator refresh and identity behavior."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dockhand.api import (
    DockhandApiError,
    DockhandAuthError,
    DockhandRateLimitError,
)
from custom_components.dockhand.const import CONF_ENVIRONMENTS, DOMAIN
from custom_components.dockhand.coordinator import DockhandDataUpdateCoordinator

RUNTIME_A = "a" * 64
RUNTIME_B = "b" * 64
COMPOSE_LABELS = {
    "com.docker.compose.project": "website",
    "com.docker.compose.service": "web",
    "com.docker.compose.container-number": "1",
}


class FakeClient:
    """Deterministic Dockhand API used by coordinator tests."""

    base_url = "https://dockhand.example"

    def __init__(self) -> None:
        """Set initial API responses."""
        self.environments: list[Any] = [
            {"id": 1, "name": "Local"},
            {"id": 2, "name": "Remote"},
        ]
        self.containers: dict[int, list[Any]] = {
            1: [
                {
                    "id": RUNTIME_A,
                    "name": "/web",
                    "image": "example/web:1",
                    "state": "running",
                }
            ],
            2: [],
        }
        self.stacks: dict[int, list[Any]] = {1: [], 2: []}
        self.inspect_calls: list[tuple[str, int]] = []
        self.stats_error: DockhandApiError | None = None

    async def get_environments(self) -> list[Any]:
        """Return environments or raise a configured exception."""
        if len(self.environments) == 1 and isinstance(self.environments[0], Exception):
            raise self.environments[0]
        return self.environments

    async def get_containers(self, env_id: int) -> list[Any]:
        """Return containers for an environment."""
        return self.containers[env_id]

    async def get_stacks(self, env_id: int) -> list[Any]:
        """Return stacks for an environment."""
        return self.stacks[env_id]

    async def get_container_inspect(
        self, container_id: str, env_id: int
    ) -> dict[str, Any]:
        """Return only test Compose labels plus a secret that must be discarded."""
        self.inspect_calls.append((container_id, env_id))
        return {
            "Config": {
                "Labels": COMPOSE_LABELS,
                "Env": ["PASSWORD=must-not-enter-coordinator-data"],
            }
        }

    async def get_container_stats(
        self, _container_id: str, _env_id: int
    ) -> dict[str, Any]:
        """Return stats or a configured per-container failure."""
        if self.stats_error:
            raise self.stats_error
        return {"cpuPercent": 4.2, "memoryUsage": 1024}


def _entry(
    hass: HomeAssistant, *, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Create a coordinator config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-a",
        data={},
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


async def _coordinator(
    hass: HomeAssistant,
    client: FakeClient,
    *,
    options: dict[str, Any] | None = None,
) -> DockhandDataUpdateCoordinator:
    """Create a coordinator and run its HA setup hook."""
    coordinator = DockhandDataUpdateCoordinator(
        hass, client, _entry(hass, options=options)
    )
    await coordinator._async_setup()
    return coordinator


async def test_refresh_collects_resources_and_discards_inspect_secrets(
    hass: HomeAssistant,
) -> None:
    """Refresh builds one coherent snapshot without retaining inspect secrets."""
    client = FakeClient()
    coordinator = await _coordinator(hass, client)

    data = await coordinator._async_update_data()

    assert len(data["environments"]) == 2
    assert len(data["containers"]) == 1
    assert len(data["stats"]) == 1
    container = next(iter(data["containers"].values()))
    assert container["labels"] == COMPOSE_LABELS
    assert "Config" not in container
    assert "PASSWORD" not in repr(data)


async def test_compose_container_recreate_keeps_resource_key(
    hass: HomeAssistant,
) -> None:
    """A new Docker runtime ID with identical Compose labels keeps HA identity."""
    client = FakeClient()
    coordinator = await _coordinator(hass, client)
    first = await coordinator._async_update_data()
    first_key = next(iter(first["containers"]))

    client.containers[1][0] = {
        **client.containers[1][0],
        "id": RUNTIME_B,
        "name": "/web-renamed",
    }
    second = await coordinator._async_update_data()

    assert next(iter(second["containers"])) == first_key
    assert client.inspect_calls == [(RUNTIME_A, 1), (RUNTIME_B, 1)]


async def test_existing_runtime_is_not_reinspected(hass: HomeAssistant) -> None:
    """Stable runtime IDs avoid an inspect request on every polling interval."""
    client = FakeClient()
    coordinator = await _coordinator(hass, client)
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert client.inspect_calls == [(RUNTIME_A, 1)]


async def test_stats_failure_keeps_main_snapshot(hass: HomeAssistant) -> None:
    """A transient stats error does not make container state unavailable."""
    client = FakeClient()
    client.stats_error = DockhandApiError("stats unavailable")
    coordinator = await _coordinator(hass, client)

    data = await coordinator._async_update_data()

    assert len(data["containers"]) == 1
    assert data["stats"] == {}


async def test_environment_filter_is_applied_before_child_requests(
    hass: HomeAssistant,
) -> None:
    """Options restrict resources without mixing environments."""
    client = FakeClient()
    coordinator = await _coordinator(hass, client, options={CONF_ENVIRONMENTS: [2]})

    data = await coordinator._async_update_data()

    assert set(data["environments"]) == {2}
    assert data["containers"] == {}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (DockhandAuthError("expired"), ConfigEntryAuthFailed),
        (DockhandApiError("invalid"), UpdateFailed),
        (DockhandRateLimitError(12), UpdateFailed),
    ],
)
async def test_refresh_maps_api_failures(
    hass: HomeAssistant,
    error: DockhandApiError,
    expected: type[Exception],
) -> None:
    """Coordinator failures have the Home Assistant lifecycle semantics."""
    client = FakeClient()
    client.environments = [error]
    coordinator = await _coordinator(hass, client)

    with pytest.raises(expected) as raised:
        await coordinator._async_update_data()

    if isinstance(error, DockhandRateLimitError):
        assert raised.value.retry_after == 12


async def test_malformed_container_aborts_snapshot(hass: HomeAssistant) -> None:
    """An incomplete list response cannot delete resources as an empty result."""
    client = FakeClient()
    client.containers[1] = [{"name": "missing-id"}]
    coordinator = await _coordinator(hass, client)

    with pytest.raises(UpdateFailed, match="invalid container"):
        await coordinator._async_update_data()
