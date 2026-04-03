"""The Dockhand integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DockhandApiClient
from .const import (
    DOMAIN,
    CONF_URL,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
)
from .coordinator import DockhandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

type DockhandConfigEntry = ConfigEntry[DockhandDataUpdateCoordinator]


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

    # Authenticate on setup
    await client.authenticate()

    coordinator = DockhandDataUpdateCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DockhandConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: DockhandConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
