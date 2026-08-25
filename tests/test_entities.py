"""Tests for Dockhand entity values, availability, and action guards."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.dockhand.binary_sensor import (
    DockhandContainerRunningSensor,
    DockhandStackHealthySensor,
)
from custom_components.dockhand.button import (
    BUTTON_DESCRIPTIONS,
    DockhandContainerButton,
)
from custom_components.dockhand.sensor import (
    CONTAINER_SENSOR_DESCRIPTIONS,
    STACK_SENSOR_DESCRIPTIONS,
    DockhandContainerSensor,
    DockhandStackSensor,
    _parse_health,
    _parse_image_tag,
)

CONTAINER_KEY = "container:entry-a:stable"
STACK_KEY = "stack:entry-a:1:website"


def _coordinator() -> SimpleNamespace:
    """Return the coordinator surface used by entity properties."""
    return SimpleNamespace(
        config_entry=SimpleNamespace(entry_id="entry-a"),
        client=SimpleNamespace(
            base_url="https://dockhand.example",
            container_action=AsyncMock(),
            update_container_image=AsyncMock(),
        ),
        data={
            "containers": {
                CONTAINER_KEY: {
                    "id": "a" * 64,
                    "name": "/web",
                    "image": "registry.example:5000/team/web:1.2",
                    "state": "running",
                    "status": "Up 2 hours (healthy)",
                    "environment_id": 1,
                    "via_device_id": "environment-device",
                }
            },
            "stats": {CONTAINER_KEY: {"cpuPercent": 3.5, "memoryUsage": 1048576}},
            "stacks": {
                STACK_KEY: {
                    "id": "website",
                    "name": "Website",
                    "status": 1,
                    "environment_id": 1,
                    "via_device_id": "environment-device",
                    "containerDetails": [
                        {"name": "web", "state": "running", "health": "healthy"}
                    ],
                }
            },
        },
        last_update_success=True,
        async_request_refresh=AsyncMock(),
    )


def _container_sensor(key: str) -> DockhandContainerSensor:
    """Create one container sensor by description key."""
    coordinator = _coordinator()
    description = next(
        item for item in CONTAINER_SENSOR_DESCRIPTIONS if item.key == key
    )
    return DockhandContainerSensor(
        coordinator,
        CONTAINER_KEY,
        coordinator.data["containers"][CONTAINER_KEY],
        description,
    )


def _button(action: str) -> DockhandContainerButton:
    """Create one container button by action."""
    coordinator = _coordinator()
    description = next(item for item in BUTTON_DESCRIPTIONS if item.action == action)
    return DockhandContainerButton(
        coordinator,
        CONTAINER_KEY,
        coordinator.data["containers"][CONTAINER_KEY],
        description,
    )


def test_image_parser_handles_ports_tags_and_digests() -> None:
    """Registry ports are not confused with image tags."""
    assert _parse_image_tag("registry.example:5000/team/web") == "latest"
    assert _parse_image_tag("registry.example:5000/team/web:1.2") == "1.2"
    assert _parse_image_tag("web@sha256:abc") == "sha256:abc"
    assert _parse_image_tag("") == ""


def test_health_parser_distinguishes_no_healthcheck() -> None:
    """Containers without healthchecks expose unknown health, not unhealthy."""
    assert _parse_health("Up 2 hours") is None
    assert _parse_health("Up 2 hours (healthy)") == "healthy"
    assert _parse_health("Up 2 hours (health: starting)") == "starting"
    assert _parse_health("Up 2 hours (unhealthy)") == "unhealthy"


def test_unknown_upstream_states_are_not_published_verbatim() -> None:
    """Malformed states remain unknown instead of becoming arbitrary HA states."""
    state = _container_sensor("state")
    state.coordinator.data["containers"][CONTAINER_KEY]["state"] = "secret-value"
    assert state.native_value == "unknown"

    health = _container_sensor("health")
    health.coordinator.data["containers"][CONTAINER_KEY]["health"] = "secret-value"
    assert health.native_value is None

    coordinator = _coordinator()
    coordinator.data["stacks"][STACK_KEY]["status"] = "secret-value"
    description = next(
        item for item in STACK_SENSOR_DESCRIPTIONS if item.key == "status"
    )
    stack = DockhandStackSensor(
        coordinator, STACK_KEY, coordinator.data["stacks"][STACK_KEY], description
    )
    assert stack.native_value == "unknown"


def test_stats_sensor_requires_current_stats() -> None:
    """A running container without stats does not publish a stale measurement."""
    sensor = _container_sensor("cpu_percent")
    assert sensor.available
    assert sensor.native_value == 3.5

    sensor.coordinator.data["stats"].clear()

    assert not sensor.available
    assert sensor.native_value is None


def test_metric_values_reject_invalid_and_nonfinite_numbers() -> None:
    """Malformed partial stats do not publish invalid numeric HA states."""
    sensor = _container_sensor("cpu_percent")
    stats = sensor.coordinator.data["stats"][CONTAINER_KEY]
    stats["cpuPercent"] = "4.25"
    assert sensor.native_value == 4.25

    stats["cpuPercent"] = "nan"
    assert sensor.native_value is None

    memory = _container_sensor("memory_usage")
    memory.coordinator.data["stats"][CONTAINER_KEY]["memoryUsage"] = "invalid"
    assert memory.native_value is None


def test_container_device_uses_resolved_parent_device_id() -> None:
    """Entity device info uses the non-deprecated HA 2026.8 parent link."""
    sensor = _container_sensor("state")

    assert sensor.device_info["via_device_id"] == "environment-device"
    assert "via_device" not in sensor.device_info


def test_button_availability_follows_container_state() -> None:
    """Impossible lifecycle actions are disabled before a user can invoke them."""
    assert not _button("start").available
    assert _button("stop").available
    assert _button("pause").available
    assert not _button("unpause").available
    assert _button("restart").available
    assert _button("update").available

    paused = _button("unpause")
    paused.coordinator.data["containers"][CONTAINER_KEY]["state"] = "paused"
    assert paused.available

    unknown = _button("start")
    unknown.coordinator.data["containers"][CONTAINER_KEY]["state"] = "unknown"
    assert not unknown.available
    update = _button("update")
    update.coordinator.data["containers"][CONTAINER_KEY]["state"] = "restarting"
    assert not update.available


async def test_button_uses_latest_runtime_id_after_recreate() -> None:
    """An existing button operates on the latest coordinator runtime ID."""
    button = _button("restart")
    current = button.coordinator.data["containers"][CONTAINER_KEY]
    current["id"] = "b" * 64

    await button.async_press()

    button.coordinator.client.container_action.assert_awaited_once_with(
        "b" * 64, "restart", 1
    )
    button.coordinator.async_request_refresh.assert_awaited_once()


def test_stack_with_malformed_details_is_safe_and_unknown() -> None:
    """Partial stack responses cannot crash state property evaluation."""
    coordinator = _coordinator()
    coordinator.data["stacks"][STACK_KEY]["containerDetails"] = None
    description = next(
        item for item in STACK_SENSOR_DESCRIPTIONS if item.key == "container_count"
    )
    sensor = DockhandStackSensor(
        coordinator, STACK_KEY, coordinator.data["stacks"][STACK_KEY], description
    )
    problem = DockhandStackHealthySensor(
        coordinator, STACK_KEY, coordinator.data["stacks"][STACK_KEY]
    )

    assert sensor.native_value == 0
    assert problem.is_on is None
    assert problem.extra_state_attributes == {"problem_containers": []}


def test_missing_resource_marks_entities_unavailable() -> None:
    """Entity objects stay registered but unavailable during temporary API gaps."""
    coordinator = _coordinator()
    running = DockhandContainerRunningSensor(
        coordinator, CONTAINER_KEY, coordinator.data["containers"][CONTAINER_KEY]
    )
    coordinator.data["containers"].clear()

    assert not running.available
    assert running.is_on is None


def test_unknown_container_state_is_not_reported_as_stopped() -> None:
    """A partial API state yields unknown instead of a false running sensor."""
    coordinator = _coordinator()
    running = DockhandContainerRunningSensor(
        coordinator, CONTAINER_KEY, coordinator.data["containers"][CONTAINER_KEY]
    )
    coordinator.data["containers"][CONTAINER_KEY]["state"] = "unexpected"

    assert running.is_on is None
