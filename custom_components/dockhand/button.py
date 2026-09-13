"""Button platform for Dockhand - container lifecycle actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DockhandApiError
from .const import DATA_IMAGE_UPDATES, DOMAIN
from .coordinator import DockhandDataUpdateCoordinator
from .identity import (
    entity_unique_id,
    environment_key,
    reconcile_resource_keys,
)


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

ENVIRONMENT_CHECK_DESCRIPTION = DockhandButtonEntityDescription(
    key="check_image_updates",
    translation_key="check_image_updates",
    icon="mdi:refresh",
    action="check_image_updates",
)


def _with_parent(device_info: DeviceInfo, via_device_id: Any) -> DeviceInfo:
    """Attach a parent only when Dockhand supplied a valid HA device ID."""
    if isinstance(via_device_id, str):
        device_info["via_device_id"] = via_device_id
    return device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dockhand buttons."""
    coordinator: DockhandDataUpdateCoordinator = entry.runtime_data

    entities: list[ButtonEntity] = []

    for env_id, env_info in coordinator.data.get("environments", {}).items():
        entities.append(
            DockhandEnvironmentCheckUpdatesButton(coordinator, env_id, env_info)
        )

    for unique_key, container_info in coordinator.data.get("containers", {}).items():
        for description in BUTTON_DESCRIPTIONS:
            entities.append(
                DockhandContainerButton(
                    coordinator, unique_key, container_info, description
                )
            )

    async_add_entities(entities)

    known_environment_keys: set[int] = set(
        coordinator.data.get("environments", {}).keys()
    )
    known_container_keys: set[str] = set(coordinator.data.get("containers", {}).keys())

    @callback
    def _async_check_new_entities() -> None:
        nonlocal known_environment_keys, known_container_keys
        new_entities: list[ButtonEntity] = []

        current_environment_keys = set(coordinator.data.get("environments", {}).keys())
        new_environment_keys = reconcile_resource_keys(
            current_environment_keys,
            known_environment_keys,
        )
        for env_id in new_environment_keys:
            new_entities.append(
                DockhandEnvironmentCheckUpdatesButton(
                    coordinator,
                    env_id,
                    coordinator.data["environments"][env_id],
                )
            )

        current_container_keys = set(coordinator.data.get("containers", {}).keys())
        new_container_keys = reconcile_resource_keys(
            current_container_keys,
            known_container_keys,
        )

        for unique_key in new_container_keys:
            container_info = coordinator.data["containers"][unique_key]
            for description in BUTTON_DESCRIPTIONS:
                new_entities.append(
                    DockhandContainerButton(
                        coordinator, unique_key, container_info, description
                    )
                )

        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_check_new_entities))


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
        self._container_name = container_info.get("name", "unknown").lstrip("/")
        self._env_id = container_info.get("environment_id")
        self._image = container_info.get("image", "")

        self._attr_unique_id = entity_unique_id(unique_key, description.key)
        self._attr_device_info = _with_parent(
            DeviceInfo(
                identifiers={(DOMAIN, unique_key)},
                name=self._container_name,
                manufacturer="Dockhand",
                model="Docker Container",
                sw_version=container_info.get("image", ""),
            ),
            container_info.get("via_device_id"),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not (
            self.coordinator.last_update_success
            and self._unique_key in self.coordinator.data.get("containers", {})
        ):
            return False

        container = self.coordinator.data["containers"][self._unique_key]
        state = str(container.get("state", "")).lower()
        action = self.entity_description.action
        if action == "start":
            return state in {"created", "exited", "stopped"}
        if action == "stop":
            return state in {"running", "paused", "restarting"}
        if action == "pause":
            return state == "running"
        if action == "unpause":
            return state == "paused"
        if action == "restart":
            return state in {"running", "paused"}
        if action == "update":
            return bool(
                state in {"created", "exited", "paused", "running", "stopped"}
                and container.get("id")
                and container.get("image")
                and container.get("name")
                and self.coordinator.data.get(DATA_IMAGE_UPDATES, {})
                .get(self._unique_key, {})
                .get("available")
                is True
            )
        return False

    async def async_press(self) -> None:
        """Handle the button press."""
        container = self.coordinator.data.get("containers", {}).get(self._unique_key)
        if not container or not (container_id := container.get("id")):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="container_not_available",
                translation_placeholders={"container": self._container_name},
            )

        container_name = str(container.get("name", self._container_name)).lstrip("/")
        environment_id = container.get("environment_id", self._env_id)
        try:
            if self.entity_description.action == "update":
                if (
                    self.coordinator.data.get(DATA_IMAGE_UPDATES, {})
                    .get(self._unique_key, {})
                    .get("available")
                    is not True
                ):
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="image_update_not_available",
                        translation_placeholders={"container": container_name},
                    )
                image = str(container.get("image", self._image))
                await self.coordinator.client.update_container_image(
                    str(container_id), environment_id, image, container_name
                )
            else:
                await self.coordinator.client.container_action(
                    str(container_id),
                    self.entity_description.action,
                    environment_id,
                )
            await self.coordinator.async_request_refresh()
        except DockhandApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="container_action_failed",
                translation_placeholders={
                    "action": self.entity_description.action,
                    "container": container_name,
                    "error": str(err),
                },
            ) from err


class DockhandEnvironmentCheckUpdatesButton(
    CoordinatorEntity[DockhandDataUpdateCoordinator], ButtonEntity
):
    """Button to ask Dockhand for a fresh registry update check."""

    _attr_has_entity_name = True
    entity_description = ENVIRONMENT_CHECK_DESCRIPTION

    def __init__(
        self,
        coordinator: DockhandDataUpdateCoordinator,
        env_id: int,
        env_info: dict[str, Any],
    ) -> None:
        """Initialize the environment update-check button."""
        super().__init__(coordinator)
        self._env_id = env_id
        self._env_name = str(env_info.get("name", f"Environment {env_id}"))
        resource_key = environment_key(coordinator.config_entry.entry_id, env_id)
        self._attr_unique_id = entity_unique_id(
            resource_key, ENVIRONMENT_CHECK_DESCRIPTION.key
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, resource_key)},
            name=f"Dockhand {self._env_name}",
            manufacturer="Dockhand",
            model="Docker Environment",
            configuration_url=coordinator.client.base_url,
        )

    @property
    def available(self) -> bool:
        """Return whether the environment is part of the current snapshot."""
        return (
            self.coordinator.last_update_success
            and self._env_id in self.coordinator.data.get("environments", {})
        )

    async def async_press(self) -> None:
        """Ask Dockhand to check registries, then read its persisted result."""
        if self._env_id not in self.coordinator.data.get("environments", {}):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="environment_not_available",
                translation_placeholders={"environment": self._env_name},
            )
        try:
            await self.coordinator.client.check_container_updates(self._env_id)
            await self.coordinator.async_request_refresh()
        except DockhandApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="image_update_check_failed",
                translation_placeholders={
                    "environment": self._env_name,
                    "error": str(err),
                },
            ) from err
