"""Binary sensor platform for Dockhand."""

from __future__ import annotations

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

from .const import DATA_IMAGE_UPDATE_STATUS, DATA_IMAGE_UPDATES, DOMAIN
from .coordinator import DockhandDataUpdateCoordinator
from .identity import (
    entity_unique_id,
    reconcile_resource_keys,
)

KNOWN_CONTAINER_STATES = frozenset(
    {"created", "dead", "exited", "paused", "removing", "restarting", "running"}
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
    """Set up Dockhand binary sensors."""
    coordinator: DockhandDataUpdateCoordinator = entry.runtime_data

    entities: list[BinarySensorEntity] = []

    for unique_key, container_info in coordinator.data.get("containers", {}).items():
        entities.append(
            DockhandContainerRunningSensor(coordinator, unique_key, container_info)
        )
        entities.append(
            DockhandContainerImageUpdateSensor(coordinator, unique_key, container_info)
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
        new_container_keys = reconcile_resource_keys(
            current_container_keys,
            known_container_keys,
        )
        for unique_key in new_container_keys:
            container_info = coordinator.data["containers"][unique_key]
            new_entities.append(
                DockhandContainerRunningSensor(coordinator, unique_key, container_info)
            )
            new_entities.append(
                DockhandContainerImageUpdateSensor(
                    coordinator, unique_key, container_info
                )
            )

        current_stack_keys = set(coordinator.data.get("stacks", {}).keys())
        new_stack_keys = reconcile_resource_keys(
            current_stack_keys,
            known_stack_keys,
        )
        for stack_key in new_stack_keys:
            stack_info = coordinator.data["stacks"][stack_key]
            new_entities.append(
                DockhandStackActiveSensor(coordinator, stack_key, stack_info)
            )
            new_entities.append(
                DockhandStackHealthySensor(coordinator, stack_key, stack_info)
            )

        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_check_new_entities))


class DockhandContainerRunningSensor(
    CoordinatorEntity[DockhandDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether a container is running."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "container_running"

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

        self._attr_unique_id = entity_unique_id(unique_key, "running")
        self._attr_device_info = _with_parent(
            DeviceInfo(
                identifiers={(DOMAIN, unique_key)},
                name=f"{self._container_name}",
                manufacturer="Dockhand",
                model="Docker Container",
                sw_version=container_info.get("image", ""),
            ),
            container_info.get("via_device_id"),
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
        state = str(container.get("state") or "").lower()
        if state not in KNOWN_CONTAINER_STATES:
            return None
        return state == "running"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        container = self.coordinator.data.get("containers", {}).get(self._unique_key)
        if not container:
            return {}
        return {
            "container_id": str(container.get("id", ""))[:12],
            "entry_id": self.coordinator.config_entry.entry_id,
            "environment_id": self._env_id,
            "status": container.get("status", ""),
            "image": container.get("image", ""),
        }


class DockhandContainerImageUpdateSensor(
    CoordinatorEntity[DockhandDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor indicating a Dockhand-persisted image update."""

    _attr_has_entity_name = True
    _attr_translation_key = "image_update_available"
    _attr_icon = "mdi:package-up"

    def __init__(
        self,
        coordinator: DockhandDataUpdateCoordinator,
        unique_key: str,
        container_info: dict[str, Any],
    ) -> None:
        """Initialize the image update binary sensor."""
        super().__init__(coordinator)
        self._unique_key = unique_key
        self._container_name = container_info.get("name", "unknown").lstrip("/")

        self._attr_unique_id = entity_unique_id(unique_key, "image_update_available")
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
        """Return whether cached update status loaded for this environment."""
        container = self.coordinator.data.get("containers", {}).get(self._unique_key)
        if not self.coordinator.last_update_success or not container:
            return False
        return (
            self.coordinator.data.get(DATA_IMAGE_UPDATE_STATUS, {}).get(
                container.get("environment_id")
            )
            is True
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether Dockhand reports a pending image update."""
        if not self.available:
            return None
        update = self.coordinator.data.get(DATA_IMAGE_UPDATES, {}).get(self._unique_key)
        return bool(update and update.get("available") is True)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return only compact, non-sensitive pending update metadata."""
        if not self.available:
            return {}
        update = self.coordinator.data.get(DATA_IMAGE_UPDATES, {}).get(self._unique_key)
        if not isinstance(update, dict):
            return {}
        attributes: dict[str, Any] = {}
        if image := update.get("current_image"):
            attributes["image"] = image
        if checked_at := update.get("checked_at"):
            attributes["checked_at"] = checked_at
        return attributes


class DockhandStackActiveSensor(
    CoordinatorEntity[DockhandDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether a Docker stack is active."""

    _attr_has_entity_name = True
    _attr_translation_key = "stack_active"
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

        self._attr_unique_id = entity_unique_id(stack_key, "active")
        self._attr_device_info = _with_parent(
            DeviceInfo(
                identifiers={(DOMAIN, stack_key)},
                name=self._stack_name,
                manufacturer="Dockhand",
                model="Docker Stack",
            ),
            stack_info.get("via_device_id"),
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
            if raw in (1, 2):
                return raw == 1
            return None
        if isinstance(raw, str):
            normalized = raw.lower()
            if normalized in ("active", "running", "up"):
                return True
            if normalized in ("inactive", "stopped", "down"):
                return False
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
    _attr_translation_key = "stack_problem"

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

        self._attr_unique_id = entity_unique_id(stack_key, "problem")
        self._attr_device_info = _with_parent(
            DeviceInfo(
                identifiers={(DOMAIN, stack_key)},
                name=self._stack_name,
                manufacturer="Dockhand",
                model="Docker Stack",
            ),
            stack_info.get("via_device_id"),
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
        details = stack.get("containerDetails")
        if not isinstance(details, list):
            return None
        for container in details:
            if not isinstance(container, dict):
                continue
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
        raw_details = stack.get("containerDetails", [])
        details = (
            [item for item in raw_details if isinstance(item, dict)]
            if isinstance(raw_details, list)
            else []
        )
        problems = [
            container.get("name", "")
            for container in details
            if container.get("state") != "running"
            or container.get("health") == "unhealthy"
        ]
        return {"problem_containers": problems}
