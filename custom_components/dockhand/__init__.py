"""The Dockhand integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .api import (
    DockhandApiClient,
    DockhandApiError,
    DockhandAuthError,
    DockhandConnectionError,
)
from .const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .coordinator import (
    DockhandDataUpdateCoordinator,
    async_remove_identity_store,
)
from .registry import (
    async_migrate_registries,
    async_sync_registry,
    current_resource_identifiers,
)
from .websocket import async_cancel_log_streams, async_setup_websocket

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

type DockhandConfigEntry = ConfigEntry[DockhandDataUpdateCoordinator]

FRONTEND_DIRECTORY = Path(__file__).parent / "frontend"
FRONTEND_PATH = FRONTEND_DIRECTORY / "dockhand-logs-card.js"
FRONTEND_URL = "/dockhand/frontend/dockhand-logs-card.js"
FRONTEND_MODULE_URL = f"{FRONTEND_URL}?v=3"
CARDS_FRONTEND_PATH = FRONTEND_DIRECTORY / "dockhand-cards.js"
CARDS_FRONTEND_URL = "/dockhand/frontend/dockhand-cards.js"
CARDS_FRONTEND_MODULE_URL = f"{CARDS_FRONTEND_URL}?v=1"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide WebSocket and frontend resources."""
    async_setup_websocket(hass)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(FRONTEND_URL, str(FRONTEND_PATH), cache_headers=False),
            StaticPathConfig(
                CARDS_FRONTEND_URL,
                str(CARDS_FRONTEND_PATH),
                cache_headers=False,
            ),
        ]
    )
    add_extra_js_url(hass, FRONTEND_MODULE_URL)
    add_extra_js_url(hass, CARDS_FRONTEND_MODULE_URL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DockhandConfigEntry) -> bool:
    """Set up Dockhand from a config entry."""
    url = entry.data[CONF_URL]
    username = entry.data.get(CONF_USERNAME, "")
    password = entry.data.get(CONF_PASSWORD, "")
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

    session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    client = DockhandApiClient(
        url=url,
        username=username if username else None,
        password=password if password else None,
        verify_ssl=verify_ssl,
        session=session,
    )

    try:
        await client.authenticate()
    except DockhandAuthError as err:
        raise ConfigEntryAuthFailed(
            f"Authentication with Dockhand failed: {err}"
        ) from err
    except (DockhandConnectionError, DockhandApiError) as err:
        raise ConfigEntryNotReady(f"Cannot connect to Dockhand: {err}") from err

    coordinator = DockhandDataUpdateCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    async_migrate_registries(hass, entry, coordinator)
    await coordinator.async_save_identity_state()
    entry.runtime_data = coordinator

    @callback
    def _async_sync_registry() -> None:
        # This listener is registered before platform listeners so parent devices
        # exist before newly discovered child entities provide via_device_id.
        async_sync_registry(hass, entry, coordinator)

    entry.async_on_unload(coordinator.async_add_listener(_async_sync_registry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DockhandConfigEntry) -> bool:
    """Unload a config entry."""
    await async_cancel_log_streams(hass, entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persistent data belonging to a deleted config entry."""
    await async_remove_identity_store(hass, entry.entry_id)


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Migrate config entry metadata away from a mutable URL unique ID."""
    if entry.version == 1 and entry.minor_version < 2:
        hass.config_entries.async_update_entry(
            entry,
            version=1,
            minor_version=2,
            unique_id=None,
        )
        return True
    return entry.version == 1


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: DockhandConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Allow removal only when Dockhand no longer supplies the device."""
    current_identifiers = current_resource_identifiers(
        config_entry, config_entry.runtime_data.data
    )
    return not any(
        domain == DOMAIN and identifier in current_identifiers
        for domain, identifier in device_entry.identifiers
    )
