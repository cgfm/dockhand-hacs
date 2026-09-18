"""End-to-end Home Assistant lifecycle tests for Dockhand."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dockhand import (
    CARDS_FRONTEND_MODULE_URL,
    CARDS_FRONTEND_URL,
    FRONTEND_MODULE_URL,
    FRONTEND_URL,
    async_migrate_entry,
    async_remove_entry,
)
from custom_components.dockhand.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from custom_components.dockhand.coordinator import STORAGE_VERSION, identity_storage_key

RUNTIME_A = "a" * 64
RUNTIME_B = "b" * 64
LABELS_A = {
    "com.docker.compose.project": "website",
    "com.docker.compose.service": "web",
    "com.docker.compose.container-number": "1",
}
LABELS_B = {
    "com.docker.compose.project": "website",
    "com.docker.compose.service": "worker",
    "com.docker.compose.container-number": "1",
}


def _container(runtime_id: str, name: str, image: str) -> dict[str, str]:
    """Return a valid Dockhand container list record."""
    return {
        "id": runtime_id,
        "name": name,
        "image": image,
        "state": "running",
        "status": "Up 1 minute (healthy)",
    }


def _api_mocks() -> tuple[ExitStack, AsyncMock]:
    """Patch all network methods used by setup and refresh."""
    stack = ExitStack()
    stack.enter_context(
        patch(
            "custom_components.dockhand.api.DockhandApiClient.authenticate",
            new=AsyncMock(),
        )
    )
    stack.enter_context(
        patch(
            "custom_components.dockhand.api.DockhandApiClient.get_environments",
            new=AsyncMock(return_value=[{"id": 1, "name": "Local"}]),
        )
    )
    containers = stack.enter_context(
        patch(
            "custom_components.dockhand.api.DockhandApiClient.get_containers",
            new=AsyncMock(
                return_value=[_container(RUNTIME_A, "/web", "example/web:1")]
            ),
        )
    )
    stack.enter_context(
        patch(
            "custom_components.dockhand.api.DockhandApiClient.get_stacks",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "website",
                        "name": "Website",
                        "status": 1,
                        "containerDetails": [
                            {"name": "web", "state": "running", "health": "healthy"}
                        ],
                    }
                ]
            ),
        )
    )
    stack.enter_context(
        patch(
            "custom_components.dockhand.api.DockhandApiClient.get_pending_container_updates",
            new=AsyncMock(return_value=[]),
        )
    )

    async def inspect(container_id: str, _env_id: int) -> dict[str, object]:
        labels = LABELS_A if container_id == RUNTIME_A else LABELS_B
        return {"Config": {"Labels": labels}}

    stack.enter_context(
        patch(
            "custom_components.dockhand.api.DockhandApiClient.get_container_inspect",
            new=AsyncMock(side_effect=inspect),
        )
    )
    stack.enter_context(
        patch(
            "custom_components.dockhand.api.DockhandApiClient.get_container_stats",
            new=AsyncMock(return_value={"cpuPercent": 1.0, "memoryUsage": 1024}),
        )
    )
    return stack, containers


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a normal 1.2 Dockhand entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-a",
        version=1,
        minor_version=2,
        data={
            CONF_URL: "https://dockhand.example",
            CONF_USERNAME: "user",
            CONF_PASSWORD: "secret",
            CONF_VERIFY_SSL: True,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_reload_and_unload(hass: HomeAssistant) -> None:
    """The complete integration loads, reloads, and unloads all platforms."""
    entry = _entry(hass)
    mocks, _containers = _api_mocks()
    with mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert FRONTEND_MODULE_URL in hass.data[DATA_EXTRA_MODULE_URL].urls
        assert CARDS_FRONTEND_MODULE_URL in hass.data[DATA_EXTRA_MODULE_URL].urls
        frontend_resources = {
            resource.canonical for resource in hass.http.app.router.resources()
        }
        assert FRONTEND_URL in frontend_resources
        assert CARDS_FRONTEND_URL in frontend_resources
        assert (
            len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id))
            == 33
        )

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert (
            len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id))
            == 33
        )

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_refresh_adds_each_entity_once_across_temporary_gaps(
    hass: HomeAssistant,
) -> None:
    """Dynamic discovery adds new resources once and never duplicates them."""
    entry = _entry(hass)
    mocks, containers = _api_mocks()
    with mocks:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 33

        containers.return_value = [
            _container(RUNTIME_A, "/web", "example/web:1"),
            _container(RUNTIME_B, "/worker", "example/worker:1"),
        ]
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 52

        containers.return_value = [_container(RUNTIME_B, "/worker", "example/worker:1")]
        await entry.runtime_data.async_refresh()
        containers.return_value = [
            _container(RUNTIME_A, "/web", "example/web:1"),
            _container(RUNTIME_B, "/worker", "example/worker:1"),
        ]
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

        assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 52
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_config_entry_metadata_and_identity_store_migration(
    hass: HomeAssistant,
) -> None:
    """Legacy URL unique IDs are removed and identity data is deleted with entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="legacy-entry",
        unique_id="https://mutable.example",
        version=1,
        minor_version=1,
        data={CONF_URL: "https://mutable.example"},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 1
    assert entry.minor_version == 2
    assert entry.unique_id is None

    store = Store[dict[str, object]](
        hass, STORAGE_VERSION, identity_storage_key(entry.entry_id)
    )
    await store.async_save({"identities": {"test": {}}})
    assert await store.async_load() is not None

    await async_remove_entry(hass, entry)

    fresh_store = Store[dict[str, object]](
        hass, STORAGE_VERSION, identity_storage_key(entry.entry_id)
    )
    assert await fresh_store.async_load() is None
