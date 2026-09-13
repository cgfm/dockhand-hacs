"""Sensor platform for Dockhand."""

from __future__ import annotations

import math
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfInformation,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DockhandDataUpdateCoordinator
from .identity import (
    entity_unique_id,
    environment_key,
    reconcile_resource_keys,
)

KNOWN_CONTAINER_STATES = frozenset(
    {"created", "dead", "exited", "paused", "removing", "restarting", "running"}
)
KNOWN_HEALTH_STATES = frozenset({"healthy", "starting", "unhealthy"})
KNOWN_STACK_STATES = frozenset(
    {"active", "down", "inactive", "running", "stopped", "up"}
)


def _numeric(value: Any) -> float | None:
    """Return a finite number from a Dockhand metric."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _format_bytes(value: Any) -> float | None:
    """Convert bytes to mebibytes."""
    if (number := _numeric(value)) is None:
        return None
    return round(number / (1024 * 1024), 2)


def _with_parent(device_info: DeviceInfo, via_device_id: Any) -> DeviceInfo:
    """Attach a parent only when Dockhand supplied a valid HA device ID."""
    if isinstance(via_device_id, str):
        device_info["via_device_id"] = via_device_id
    return device_info


def _parse_image_tag(image: str) -> str:
    """Extract the tag/version portion from a Docker image reference.

    Examples:
      nginx            -> latest
      nginx:1.25.3     -> 1.25.3
      ghcr.io/org/app:v2.0  -> v2.0
      nginx@sha256:abc123   -> sha256:abc123
    """
    if not image:
        return ""
    if "@" in image:
        return image.split("@", 1)[1]
    if ":" in image:
        _name, tag = image.rsplit(":", 1)
        # A tag cannot contain '/' — if it does we hit a registry:port case
        if "/" not in tag:
            return tag
    return "latest"


def _parse_health(status: str) -> str | None:
    """Parse health status from a Docker container status string.

    Docker embeds health in the status string, e.g.:
      "Up 2 hours (healthy)"
      "Up 30 seconds (health: starting)"
      "Up 2 hours (unhealthy)"
      "Up 2 hours"  -> no health check configured
    """
    if not status:
        return None
    match = re.search(r"\(([^)]+)\)", status)
    if not match:
        return None
    inner = match.group(1).lower()
    if "unhealthy" in inner:
        return "unhealthy"
    if "starting" in inner:
        return "starting"
    if "healthy" in inner:
        return "healthy"
    return None


CONTAINER_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="state",
        name="State",
        icon="mdi:docker",
        translation_key="container_state",
    ),
    SensorEntityDescription(
        key="image",
        name="Image",
        icon="mdi:package-variant",
        translation_key="container_image",
    ),
    SensorEntityDescription(
        key="image_tag",
        name="Image version",
        icon="mdi:tag",
        translation_key="container_image_tag",
    ),
    SensorEntityDescription(
        key="health",
        name="Health",
        icon="mdi:heart-pulse",
        translation_key="container_health",
    ),
    SensorEntityDescription(
        key="cpu_percent",
        name="CPU",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cpu-64-bit",
        suggested_display_precision=1,
        translation_key="cpu_percent",
    ),
    SensorEntityDescription(
        key="memory_usage",
        name="Memory usage",
        native_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:memory",
        suggested_display_precision=1,
        translation_key="memory_usage",
    ),
    SensorEntityDescription(
        key="memory_percent",
        name="Memory",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:memory",
        suggested_display_precision=1,
        translation_key="memory_percent",
    ),
    SensorEntityDescription(
        key="network_rx",
        name="Network RX",
        native_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:download-network",
        suggested_display_precision=1,
        translation_key="network_rx",
    ),
    SensorEntityDescription(
        key="network_tx",
        name="Network TX",
        native_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:upload-network",
        suggested_display_precision=1,
        translation_key="network_tx",
    ),
    SensorEntityDescription(
        key="block_read",
        name="Disk read",
        native_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:harddisk",
        suggested_display_precision=1,
        translation_key="block_read",
    ),
    SensorEntityDescription(
        key="block_write",
        name="Disk write",
        native_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:harddisk",
        suggested_display_precision=1,
        translation_key="block_write",
    ),
)

ENVIRONMENT_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="container_count",
        name="Containers",
        icon="mdi:server-network",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="container_count",
    ),
    SensorEntityDescription(
        key="running_count",
        name="Running containers",
        icon="mdi:server-network",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="running_count",
    ),
    SensorEntityDescription(
        key="stopped_count",
        name="Stopped containers",
        icon="mdi:server-network-off",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="stopped_count",
    ),
)

STACK_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="status",
        name="Status",
        icon="mdi:layers-triple",
        translation_key="stack_status",
    ),
    SensorEntityDescription(
        key="container_count",
        name="Containers",
        icon="mdi:layers-triple",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="stack_container_count",
    ),
    SensorEntityDescription(
        key="running_count",
        name="Running containers",
        icon="mdi:layers-triple",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="stack_running_count",
    ),
    SensorEntityDescription(
        key="stopped_count",
        name="Stopped containers",
        icon="mdi:layers-triple-outline",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="stack_stopped_count",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dockhand sensors."""
    coordinator: DockhandDataUpdateCoordinator = entry.runtime_data

    entities: list[SensorEntity] = []

    # Create environment sensors
    for env_id, env_info in coordinator.data.get("environments", {}).items():
        for desc in ENVIRONMENT_SENSOR_DESCRIPTIONS:
            entities.append(
                DockhandEnvironmentSensor(coordinator, env_id, env_info, desc)
            )

    # Create container sensors
    for unique_key, container_info in coordinator.data.get("containers", {}).items():
        for desc in CONTAINER_SENSOR_DESCRIPTIONS:
            entities.append(
                DockhandContainerSensor(coordinator, unique_key, container_info, desc)
            )

    # Create stack sensors
    for stack_key, stack_info in coordinator.data.get("stacks", {}).items():
        for desc in STACK_SENSOR_DESCRIPTIONS:
            entities.append(
                DockhandStackSensor(coordinator, stack_key, stack_info, desc)
            )

    async_add_entities(entities)

    known_environment_keys: set[int] = set(
        coordinator.data.get("environments", {}).keys()
    )
    known_container_keys: set[str] = set(coordinator.data.get("containers", {}).keys())
    known_stack_keys: set[str] = set(coordinator.data.get("stacks", {}).keys())

    @callback
    def _async_check_new_entities() -> None:
        """Check for newly discovered resources and add their entities."""
        nonlocal known_environment_keys, known_container_keys, known_stack_keys
        new_entities: list[SensorEntity] = []

        current_environment_keys = set(coordinator.data.get("environments", {}).keys())
        new_environment_keys = reconcile_resource_keys(
            current_environment_keys,
            known_environment_keys,
        )
        for env_id in new_environment_keys:
            env_info = coordinator.data["environments"][env_id]
            for desc in ENVIRONMENT_SENSOR_DESCRIPTIONS:
                new_entities.append(
                    DockhandEnvironmentSensor(coordinator, env_id, env_info, desc)
                )

        current_container_keys = set(coordinator.data.get("containers", {}).keys())
        new_container_keys = reconcile_resource_keys(
            current_container_keys,
            known_container_keys,
        )
        for unique_key in new_container_keys:
            container_info = coordinator.data["containers"][unique_key]
            for desc in CONTAINER_SENSOR_DESCRIPTIONS:
                new_entities.append(
                    DockhandContainerSensor(
                        coordinator, unique_key, container_info, desc
                    )
                )

        current_stack_keys = set(coordinator.data.get("stacks", {}).keys())
        new_stack_keys = reconcile_resource_keys(
            current_stack_keys,
            known_stack_keys,
        )
        for stack_key in new_stack_keys:
            stack_info = coordinator.data["stacks"][stack_key]
            for desc in STACK_SENSOR_DESCRIPTIONS:
                new_entities.append(
                    DockhandStackSensor(coordinator, stack_key, stack_info, desc)
                )

        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_check_new_entities))


class DockhandContainerSensor(
    CoordinatorEntity[DockhandDataUpdateCoordinator], SensorEntity
):
    """Sensor for a Dockhand container metric."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DockhandDataUpdateCoordinator,
        unique_key: str,
        container_info: dict[str, Any],
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._unique_key = unique_key
        self._container_name = container_info.get("name", "unknown").lstrip("/")
        self._env_id = container_info.get("environment_id")
        self._env_name = container_info.get("environment_name", "")

        self._attr_unique_id = entity_unique_id(unique_key, description.key)
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
        available = (
            self.coordinator.last_update_success
            and self._unique_key in self.coordinator.data.get("containers", {})
        )
        if not available:
            return False
        if self.entity_description.key in {
            "cpu_percent",
            "memory_usage",
            "memory_percent",
            "network_rx",
            "network_tx",
            "block_read",
            "block_write",
        }:
            return self.native_value is not None
        return True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        container = self.coordinator.data.get("containers", {}).get(self._unique_key)
        if not container:
            return None

        key = self.entity_description.key
        stats = self.coordinator.data.get("stats", {}).get(self._unique_key, {})

        if key == "state":
            state = str(container.get("state") or "").lower()
            return state if state in KNOWN_CONTAINER_STATES else "unknown"
        if key == "image":
            return container.get("image", "")
        if key == "image_tag":
            return _parse_image_tag(container.get("image", ""))
        if key == "health":
            # Dockhand may expose health directly or embed it in the status.
            direct = container.get("health") or container.get("healthStatus")
            if direct:
                health = str(direct).lower()
                return health if health in KNOWN_HEALTH_STATES else None
            return _parse_health(container.get("status", ""))
        if key == "cpu_percent":
            return _numeric(stats.get("cpuPercent"))
        if key == "memory_usage":
            return _format_bytes(stats.get("memoryUsage"))
        if key == "memory_percent":
            return _numeric(stats.get("memoryPercent"))
        if key == "network_rx":
            return _format_bytes(stats.get("networkRx"))
        if key == "network_tx":
            return _format_bytes(stats.get("networkTx"))
        if key == "block_read":
            value = stats.get("blockRead")
            return _format_bytes(value if value is not None else stats.get("blkioRead"))
        if key == "block_write":
            value = stats.get("blockWrite")
            return _format_bytes(
                value if value is not None else stats.get("blkioWrite")
            )

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        container = self.coordinator.data.get("containers", {}).get(self._unique_key)
        if not container:
            return {}

        attrs: dict[str, Any] = {
            "container_id": str(container.get("id", ""))[:12],
            "entry_id": self.coordinator.config_entry.entry_id,
            "image": container.get("image", ""),
            "environment": self._env_name,
            "environment_id": self._env_id,
        }

        if self.entity_description.key == "state":
            attrs["status"] = container.get("status", "")

        if self.entity_description.key == "memory_usage":
            stats = self.coordinator.data.get("stats", {}).get(self._unique_key, {})
            mem_limit = stats.get("memoryLimit")
            if mem_limit:
                memory_limit = _format_bytes(mem_limit)
                attrs["memory_limit_mib"] = memory_limit
                # Keep the legacy attribute to avoid breaking existing templates.
                attrs["memory_limit_mb"] = memory_limit

        return attrs


class DockhandEnvironmentSensor(
    CoordinatorEntity[DockhandDataUpdateCoordinator], SensorEntity
):
    """Sensor for Dockhand environment summary."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DockhandDataUpdateCoordinator,
        env_id: int,
        env_info: dict[str, Any],
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._env_id = env_id
        self._env_name = env_info.get("name", f"Environment {env_id}")

        resource_key = environment_key(coordinator.config_entry.entry_id, env_id)
        self._attr_unique_id = entity_unique_id(resource_key, description.key)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, resource_key)},
            name=f"Dockhand {self._env_name}",
            manufacturer="Dockhand",
            model="Docker Environment",
            configuration_url=coordinator.client.base_url,
        )

    @property
    def available(self) -> bool:
        """Return whether the environment is in the latest successful update."""
        return (
            self.coordinator.last_update_success
            and self._env_id in self.coordinator.data.get("environments", {})
        )

    @property
    def native_value(self) -> int | None:
        """Return the sensor value."""
        containers = self.coordinator.data.get("containers", {})
        env_containers = [
            c for c in containers.values() if c.get("environment_id") == self._env_id
        ]

        key = self.entity_description.key
        if key == "container_count":
            return len(env_containers)
        if key == "running_count":
            return sum(1 for c in env_containers if c.get("state") == "running")
        if key == "stopped_count":
            return sum(1 for c in env_containers if c.get("state") != "running")

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        env = self.coordinator.data.get("environments", {}).get(self._env_id, {})
        return {
            "environment_id": self._env_id,
            "connection_type": env.get("connectionType", ""),
        }


class DockhandStackSensor(
    CoordinatorEntity[DockhandDataUpdateCoordinator], SensorEntity
):
    """Sensor for a Dockhand stack."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DockhandDataUpdateCoordinator,
        stack_key: str,
        stack_info: dict[str, Any],
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._stack_key = stack_key
        self._stack_id = stack_info.get("id") or stack_info.get("name")
        self._stack_name = stack_info.get("name", f"Stack {self._stack_id}")
        self._env_id = stack_info.get("environment_id")
        self._env_name = stack_info.get("environment_name", "")

        self._attr_unique_id = entity_unique_id(stack_key, description.key)
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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        stack = self.coordinator.data.get("stacks", {}).get(self._stack_key)
        if not stack:
            return None

        key = self.entity_description.key
        if key == "status":
            # Dockhand may return status as a string or integer (1=active, 2=inactive)
            raw = stack.get("status")
            if isinstance(raw, int):
                if raw == 1:
                    return "active"
                if raw == 2:
                    return "inactive"
                return "unknown"
            normalized = str(raw).lower() if raw is not None else ""
            return normalized if normalized in KNOWN_STACK_STATES else "unknown"

        raw_details = stack.get("containerDetails", [])
        details = (
            [item for item in raw_details if isinstance(item, dict)]
            if isinstance(raw_details, list)
            else []
        )
        if key == "container_count":
            return len(details)
        if key == "running_count":
            return sum(1 for c in details if c.get("state") == "running")
        if key == "stopped_count":
            return sum(1 for c in details if c.get("state") != "running")

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        stack = self.coordinator.data.get("stacks", {}).get(self._stack_key)
        if not stack:
            return {}
        return {
            "stack_id": self._stack_id,
            "environment": self._env_name,
            "environment_id": self._env_id,
            "type": stack.get("type", ""),
        }
