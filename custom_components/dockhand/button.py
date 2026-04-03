"""Button platform for Dockhand - container lifecycle actions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DockhandApiError
from .const import DOMAIN
from .coordinator import DockhandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DockhandButtonEntityDescription(ButtonEntityDescription):
    """Describes a Dockhand button entity."""

    action: str = ""


BUTTON_DESCRIPTIONS: tuple[DockhandButtonEntityDescription, ...] = (
    DockhandButtonEntityDescription(
        key="start",
        translation_key="start",
        icon="mdi:play",
        action="start",
    ),
    DockhandButtonEntityDescription(
        key="stop",
        translation_key="stop",
        icon="mdi:stop",
        action="stop",
    ),
    DockhandButtonEntityDescription(
        key="pause",
        translation_key="pause",
        icon="mdi:pause",
        action="pause",
    ),
    DockhandButtonEntityDescription(
        key="unpause",
        translation_key="unpause",
        icon="mdi:play-pause",
        action="unpause",
    ),
    DockhandButtonEntityDescription(
        key="restart",
        translation_key="restart",
        icon="mdi:restart",
        action="restart",
    ),
    DockhandButtonEntityDescription(
        key="update",
        translation_key="update",
        icon="mdi:update",
        action="update",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dockhand buttons."""
    coordinator: DockhandDataUpdateCoordinator = entry.runtime_data

    entities: list[ButtonEntity] = []

    for unique_key, container_info in coordinator.data.get("containers", {}).items():
        for description in BUTTON_DESCRIPTIONS:
            entities.append(
                DockhandContainerButton(coordinator, unique_key, container_info, description)
            )

    async_add_entities(entities)

    known_keys: set[str] = set(coordinator.data.get("containers", {}).keys())

    @callback
    def _async_check_new_entities() -> None:
        nonlocal known_keys
        new_entities: list[ButtonEntity] = []
        current_keys = set(coordinator.data.get("containers", {}).keys())
        new_keys = current_keys - known_keys

        for unique_key in new_keys:
            container_info = coordinator.data["containers"][unique_key]
            for description in BUTTON_DESCRIPTIONS:
                new_entities.append(
                    DockhandContainerButton(coordinator, unique_key, container_info, description)
                )

        if new_entities:
            async_add_entities(new_entities)

        known_keys = current_keys

    coordinator.async_add_listener(_async_check_new_entities)


class DockhandContainerButton(
    CoordinatorEntity[DockhandDataUpdateCoordinator], ButtonEntity
):
    """Button to trigger a Docker container action."""

    _attr_has_entity_name = True
    entity_description: DockhandButtonEntityDescription

    def __init__(
        self,
        coordinator: DockhandDataUpdateCoordinator,
        unique_key: str,
        container_info: dict[str, Any],
        description: DockhandButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._unique_key = unique_key
        self._container_id = container_info.get("id", "")
        self._container_name = container_info.get("name", "unknown").lstrip("/")
        self._env_id = container_info.get("environment_id")
        self._image = container_info.get("image", "")

        self._attr_unique_id = f"{DOMAIN}_{unique_key}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_key)},
            name=self._container_name,
            manufacturer="Dockhand",
            model="Docker Container",
            sw_version=container_info.get("image", ""),
            via_device=(DOMAIN, f"env_{self._env_id}"),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.last_update_success
            and self._unique_key in self.coordinator.data.get("containers", {})
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            if self.entity_description.action == "update":
                # Fetch current image from coordinator data in case it changed
                container = self.coordinator.data.get("containers", {}).get(self._unique_key, {})
                image = container.get("image", self._image)
                await self.coordinator.client.update_container_image(
                    self._container_id, self._env_id, image, self._container_name
                )
            else:
                await self.coordinator.client.container_action(
                    self._container_id, self.entity_description.action, self._env_id
                )
            await self.coordinator.async_request_refresh()
        except DockhandApiError as err:
            _LOGGER.error(
                "Failed to %s container %s: %s",
                self.entity_description.action,
                self._container_name,
                err,
            )
