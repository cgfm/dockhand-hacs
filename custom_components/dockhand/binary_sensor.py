"""Binary sensor platform for Dockhand."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DockhandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dockhand binary sensors."""
    coordinator: DockhandDataUpdateCoordinator = entry.runtime_data

    entities: list[BinarySensorEntity] = []

    for unique_key, container_info in coordinator.data.get("containers", {}).items():
        entities.append(
            DockhandContainerRunningSensor(coordinator, unique_key, container_info)
        )

    for stack_key, stack_info in coordinator.data.get("stacks", {}).items():
        entities.append(DockhandStackActiveSensor(coordinator, stack_key, stack_info))
        entities.append(DockhandStackHealthySensor(coordinator, stack_key, stack_info))

    async_add_entities(entities)

    known_container_keys: set[str] = set(coordinator.data.get("containers", {}).keys())
    known_stack_keys: set[str] = set(coordinator.data.get("stacks", {}).keys())

    @callback
    def _async_check_new_entities() -> None:
        nonlocal known_container_keys, known_stack_keys
        new_entities: list[BinarySensorEntity] = []

        current_container_keys = set(coordinator.data.get("containers", {}).keys())
        for unique_key in current_container_keys - known_container_keys:
            container_info = coordinator.data["containers"][unique_key]
            new_entities.append(
                DockhandContainerRunningSensor(coordinator, unique_key, container_info)
            )
        known_container_keys = current_container_keys

        current_stack_keys = set(coordinator.data.get("stacks", {}).keys())
        for stack_key in current_stack_keys - known_stack_keys:
            stack_info = coordinator.data["stacks"][stack_key]
            new_entities.append(DockhandStackActiveSensor(coordinator, stack_key, stack_info))
            new_entities.append(DockhandStackHealthySensor(coordinator, stack_key, stack_info))
        known_stack_keys = current_stack_keys

        if new_entities:
            async_add_entities(new_entities)

    coordinator.async_add_listener(_async_check_new_entities)


class DockhandContainerRunningSensor(
    CoordinatorEntity[DockhandDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether a container is running."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = "Running"

    def __init__(
        self,
        coordinator: DockhandDataUpdateCoordinator,
        unique_key: str,
        container_info: dict[str, Any],
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._unique_key = unique_key
        self._container_name = container_info.get("name", "unknown").lstrip("/")
        self._env_id = container_info.get("environment_id")

        self._attr_unique_id = f"{DOMAIN}_{unique_key}_running"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_key)},
            name=f"{self._container_name}",
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

    @property
    def is_on(self) -> bool | None:
        """Return True if the container is running."""
        container = self.coordinator.data.get("containers", {}).get(self._unique_key)
        if not container:
            return None
        return container.get("state") == "running"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        container = self.coordinator.data.get("containers", {}).get(self._unique_key)
        if not container:
            return {}
        return {
            "container_id": container.get("id", "")[:12],
            "status": container.get("status", ""),
            "image": container.get("image", ""),
        }


class DockhandStackActiveSensor(
    CoordinatorEntity[DockhandDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether a Docker stack is active."""

    _attr_has_entity_name = True
    _attr_name = "Active"
    _attr_icon = "mdi:layers-triple"

    def __init__(
        self,
        coordinator: DockhandDataUpdateCoordinator,
        stack_key: str,
        stack_info: dict[str, Any],
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._stack_key = stack_key
        self._stack_id = stack_info.get("id") or stack_info.get("name")
        self._stack_name = stack_info.get("name", f"Stack {self._stack_id}")
        self._env_id = stack_info.get("environment_id")

        self._attr_unique_id = f"{DOMAIN}_stack_{stack_key}_active"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"stack_{stack_key}")},
            name=self._stack_name,
            manufacturer="Dockhand",
            model="Docker Stack",
            via_device=(DOMAIN, f"env_{self._env_id}"),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.last_update_success
            and self._stack_key in self.coordinator.data.get("stacks", {})
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the stack is active."""
        stack = self.coordinator.data.get("stacks", {}).get(self._stack_key)
        if not stack:
            return None
        raw = stack.get("status")
        if isinstance(raw, int):
            return raw == 1
        if isinstance(raw, str):
            return raw.lower() in ("active", "running", "up")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        stack = self.coordinator.data.get("stacks", {}).get(self._stack_key)
        if not stack:
            return {}
        return {
            "stack_id": self._stack_id,
            "environment_id": self._env_id,
            "type": stack.get("type", ""),
        }


class DockhandStackHealthySensor(
    CoordinatorEntity[DockhandDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether all containers in a stack are healthy.

    ON (problem) = at least one container is not running or is unhealthy.
    OFF (no problem) = all containers are running and none are unhealthy.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Problem"

    def __init__(
        self,
        coordinator: DockhandDataUpdateCoordinator,
        stack_key: str,
        stack_info: dict[str, Any],
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._stack_key = stack_key
        self._stack_id = stack_info.get("id") or stack_info.get("name")
        self._stack_name = stack_info.get("name", f"Stack {self._stack_id}")
        self._env_id = stack_info.get("environment_id")

        self._attr_unique_id = f"{DOMAIN}_stack_{stack_key}_problem"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"stack_{stack_key}")},
            name=self._stack_name,
            manufacturer="Dockhand",
            model="Docker Stack",
            via_device=(DOMAIN, f"env_{self._env_id}"),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.last_update_success
            and self._stack_key in self.coordinator.data.get("stacks", {})
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if any container in the stack has a problem."""
        stack = self.coordinator.data.get("stacks", {}).get(self._stack_key)
        if not stack:
            return None
        for container in stack.get("containerDetails", []):
            if container.get("state") != "running":
                return True
            if container.get("health") == "unhealthy":
                return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return which containers have problems."""
        stack = self.coordinator.data.get("stacks", {}).get(self._stack_key)
        if not stack:
            return {}
        problems = [
            container.get("name", "")
            for container in stack.get("containerDetails", [])
            if container.get("state") != "running"
            or container.get("health") == "unhealthy"
        ]
        return {"problem_containers": problems}
