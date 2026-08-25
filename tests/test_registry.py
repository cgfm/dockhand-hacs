"""Integration-style tests for Dockhand's HA registry migration."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dockhand.const import DOMAIN, MISSING_RESOURCE_GRACE_SECONDS
from custom_components.dockhand.identity import ContainerIdentityRegistry
from custom_components.dockhand.registry import async_migrate_registries

RUNTIME_A = "a" * 64
RUNTIME_B = "b" * 64
STABLE_KEY = "container:entry-a:stable"


def _entry(hass: HomeAssistant, entry_id: str = "entry-a") -> MockConfigEntry:
    """Create an in-memory Dockhand config entry."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id=entry_id, data={})
    entry.add_to_hass(hass)
    return entry


def _coordinator(data: dict[str, Any]) -> SimpleNamespace:
    """Build the coordinator surface consumed by registry migration."""
    return SimpleNamespace(
        data=data,
        client=SimpleNamespace(base_url="https://dockhand.example"),
        identity_registry=ContainerIdentityRegistry(),
    )


def _device(
    registry: dr.DeviceRegistry,
    entry: MockConfigEntry,
    identifier: str,
    *,
    name: str = "web",
    model: str = "Docker Container",
) -> dr.DeviceEntry:
    """Create a device in the real Home Assistant registry implementation."""
    return registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, identifier)},
        name=name,
        manufacturer="Dockhand",
        model=model,
    )


def _entity(
    registry: er.EntityRegistry,
    entry: MockConfigEntry,
    device: dr.DeviceEntry,
    unique_id: str,
    entity_id: str,
) -> er.RegistryEntry:
    """Create an entity with an exact generated ID for conflict tests."""
    result = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        config_entry=entry,
        device_id=device.id,
        suggested_object_id=entity_id.removeprefix("sensor."),
    )
    if result.entity_id != entity_id:
        result = registry.async_update_entity(result.entity_id, new_entity_id=entity_id)
    return result


def _container_data(runtime_id: str = RUNTIME_B) -> dict[str, Any]:
    """Return one current container using the stable 1.2 key."""
    return {
        "environments": {1: {"id": 1, "name": "Local"}},
        "containers": {
            STABLE_KEY: {
                "id": runtime_id,
                "name": "/web",
                "image": "example/web:1",
                "environment_id": 1,
            }
        },
        "stacks": {},
        "stats": {},
    }


async def test_migration_preserves_original_entity_id(hass: HomeAssistant) -> None:
    """The oldest unsuffixed entity survives duplicate consolidation."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    original = _device(devices, entry, f"1_{RUNTIME_A}")
    duplicate = _device(devices, entry, f"1_{RUNTIME_B}")
    _entity(
        entities,
        entry,
        original,
        f"{DOMAIN}_1_{RUNTIME_A}_state",
        "sensor.web_state",
    )
    _entity(
        entities,
        entry,
        duplicate,
        f"{DOMAIN}_1_{RUNTIME_B}_state",
        "sensor.web_state_2",
    )
    coordinator = _coordinator(_container_data())

    async_migrate_registries(hass, entry, coordinator, now=100)

    kept = entities.async_get("sensor.web_state")
    assert kept is not None
    assert kept.unique_id == f"{STABLE_KEY}:state"
    assert entities.async_get("sensor.web_state_2") is None
    stable_device = devices.async_get_device_by_identifier(
        (DOMAIN, STABLE_KEY), entry.entry_id
    )
    assert stable_device is not None
    assert kept.device_id == stable_device.id


async def test_partial_migration_prefers_lower_suffix(hass: HomeAssistant) -> None:
    """A real-world _2 winner reclaims the free original unsuffixed ID."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    legacy = _device(devices, entry, f"1_{RUNTIME_B}")
    stable = _device(devices, entry, STABLE_KEY)
    _entity(
        entities,
        entry,
        legacy,
        f"{DOMAIN}_1_{RUNTIME_B}_state",
        "sensor.web_state_2",
    )
    _entity(
        entities,
        entry,
        stable,
        f"{STABLE_KEY}:state",
        "sensor.web_state_4",
    )

    async_migrate_registries(hass, entry, _coordinator(_container_data()), now=100)

    kept = entities.async_get("sensor.web_state")
    assert kept is not None
    assert kept.unique_id == f"{STABLE_KEY}:state"
    assert entities.async_get("sensor.web_state_2") is None
    assert entities.async_get("sensor.web_state_4") is None


async def test_completed_partial_migration_restores_free_original_entity_id(
    hass: HomeAssistant,
) -> None:
    """A later reload repairs suffixes left by an earlier partial migration."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    stable = _device(devices, entry, STABLE_KEY)
    _entity(
        entities,
        entry,
        stable,
        f"{STABLE_KEY}:state",
        "sensor.web_state_3",
    )

    async_migrate_registries(hass, entry, _coordinator(_container_data()), now=100)

    restored = entities.async_get("sensor.web_state")
    assert restored is not None
    assert restored.unique_id == f"{STABLE_KEY}:state"
    assert entities.async_get("sensor.web_state_3") is None


async def test_suffix_is_kept_when_original_entity_id_is_occupied(
    hass: HomeAssistant,
) -> None:
    """Canonicalization cannot replace an unrelated entity using the base ID."""
    entry = _entry(hass)
    foreign_entry = MockConfigEntry(domain="other", entry_id="foreign", data={})
    foreign_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    stable = _device(devices, entry, STABLE_KEY)
    _entity(
        entities,
        entry,
        stable,
        f"{STABLE_KEY}:state",
        "sensor.web_state_2",
    )
    foreign = entities.async_get_or_create(
        "sensor",
        "other",
        "foreign-state",
        config_entry=foreign_entry,
        suggested_object_id="web_state",
    )
    assert foreign.entity_id == "sensor.web_state"

    async_migrate_registries(hass, entry, _coordinator(_container_data()), now=100)

    kept = entities.async_get("sensor.web_state_2")
    assert kept is not None and kept.unique_id == f"{STABLE_KEY}:state"
    assert entities.async_get("sensor.web_state") == foreign


async def test_partial_migration_moves_winning_conflict_to_target_device(
    hass: HomeAssistant,
) -> None:
    """A winning stable unique ID cannot remain attached to an obsolete device."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    legacy = _device(devices, entry, f"1_{RUNTIME_B}")
    target = _device(devices, entry, STABLE_KEY)
    wrong = _device(devices, entry, "unrelated", name="Other", model="Other")
    _entity(
        entities,
        entry,
        legacy,
        f"{DOMAIN}_1_{RUNTIME_B}_state",
        "sensor.web_state_2",
    )
    _entity(
        entities,
        entry,
        wrong,
        f"{STABLE_KEY}:state",
        "sensor.web_state",
    )

    async_migrate_registries(hass, entry, _coordinator(_container_data()), now=100)

    kept = entities.async_get("sensor.web_state")
    assert kept is not None and kept.device_id == target.id
    assert entities.async_get("sensor.web_state_2") is None


async def test_older_scoped_partial_identity_is_resumed(hass: HomeAssistant) -> None:
    """A prior 1.2 identity is migrated when its parent environment proves scope."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    parent = _device(
        devices,
        entry,
        "environment:entry-a:1",
        name="Dockhand Local",
        model="Docker Environment",
    )
    prior_key = "container:entry-a:prior-algorithm"
    partial = _device(devices, entry, prior_key)
    partial = devices.async_update_device(partial.id, via_device_id=parent.id)
    assert partial is not None
    _entity(
        entities,
        entry,
        partial,
        f"{prior_key}:state",
        "sensor.web_state",
    )

    async_migrate_registries(hass, entry, _coordinator(_container_data()), now=100)

    migrated_device = devices.async_get_device_by_identifier(
        (DOMAIN, STABLE_KEY), entry.entry_id
    )
    migrated_entity = entities.async_get("sensor.web_state")
    assert migrated_device is not None and migrated_device.id == partial.id
    assert migrated_entity is not None
    assert migrated_entity.unique_id == f"{STABLE_KEY}:state"


async def test_migration_is_idempotent(hass: HomeAssistant, caplog: Any) -> None:
    """Repeated setup leaves device IDs, entity IDs and unique IDs unchanged."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    legacy = _device(devices, entry, f"1_{RUNTIME_A}")
    _entity(
        entities,
        entry,
        legacy,
        f"{DOMAIN}_1_{RUNTIME_A}_state",
        "sensor.web_state",
    )
    coordinator = _coordinator(_container_data(RUNTIME_A))

    async_migrate_registries(hass, entry, coordinator, now=100)
    first_device = devices.async_get_device_by_identifier(
        (DOMAIN, STABLE_KEY), entry.entry_id
    )
    first_entity = entities.async_get("sensor.web_state")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="custom_components.dockhand.registry"):
        async_migrate_registries(hass, entry, coordinator, now=200)

    assert first_device is not None
    assert first_entity is not None
    assert (
        devices.async_get_device_by_identifier((DOMAIN, STABLE_KEY), entry.entry_id)
        == first_device
    )
    assert entities.async_get("sensor.web_state") == first_entity
    assert "does not match a known Dockhand schema" not in caplog.text


async def test_other_config_entry_is_never_touched(hass: HomeAssistant) -> None:
    """Name matches in another Dockhand instance remain entirely isolated."""
    own_entry = _entry(hass, "entry-a")
    foreign_entry = _entry(hass, "entry-b")
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    own = _device(devices, own_entry, f"1_{RUNTIME_A}")
    foreign = _device(devices, foreign_entry, f"1_{RUNTIME_B}")
    _entity(
        entities,
        own_entry,
        own,
        f"{DOMAIN}_1_{RUNTIME_A}_state",
        "sensor.web_state",
    )
    foreign_entity = _entity(
        entities,
        foreign_entry,
        foreign,
        f"{DOMAIN}_1_{RUNTIME_B}_state",
        "sensor.web_state_2",
    )

    async_migrate_registries(
        hass, own_entry, _coordinator(_container_data(RUNTIME_A)), now=100
    )

    assert devices.async_get(foreign.id) == foreign
    assert entities.async_get(foreign_entity.entity_id) == foreign_entity
    assert (DOMAIN, f"1_{RUNTIME_B}") in foreign.identifiers


async def test_stack_and_container_link_to_environment_device(
    hass: HomeAssistant,
) -> None:
    """Parent links use resolved device IDs and survive setup ordering."""
    entry = _entry(hass)
    data = _container_data()
    stack_identifier = "stack:entry-a:1:website"
    data["stacks"] = {
        stack_identifier: {
            "id": "website",
            "name": "Website",
            "environment_id": 1,
        }
    }

    async_migrate_registries(hass, entry, _coordinator(data), now=100)

    devices = dr.async_get(hass)
    environment = devices.async_get_device_by_identifier(
        (DOMAIN, "environment:entry-a:1"), entry.entry_id
    )
    container = devices.async_get_device_by_identifier(
        (DOMAIN, STABLE_KEY), entry.entry_id
    )
    stack = devices.async_get_device_by_identifier(
        (DOMAIN, stack_identifier), entry.entry_id
    )
    assert environment is not None
    assert container is not None and container.via_device_id == environment.id
    assert stack is not None and stack.via_device_id == environment.id


async def test_missing_device_observes_persisted_grace_period(
    hass: HomeAssistant,
) -> None:
    """Removed resources remain until a full seven-day persisted grace expires."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    obsolete = _device(devices, entry, f"1_{RUNTIME_A}")
    coordinator = _coordinator(
        {"environments": {}, "containers": {}, "stacks": {}, "stats": {}}
    )

    async_migrate_registries(hass, entry, coordinator, now=100)
    async_migrate_registries(
        hass,
        entry,
        coordinator,
        now=100 + MISSING_RESOURCE_GRACE_SECONDS - 1,
    )
    assert devices.async_get(obsolete.id) is not None

    async_migrate_registries(
        hass,
        entry,
        coordinator,
        now=100 + MISSING_RESOURCE_GRACE_SECONDS,
    )
    assert devices.async_get(obsolete.id) is None


async def test_unrecognized_unique_id_keeps_legacy_device(
    hass: HomeAssistant,
) -> None:
    """Unknown legacy schemas are logged and retained instead of deleted."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    _device(devices, entry, STABLE_KEY)
    legacy = _device(devices, entry, f"1_{RUNTIME_B}")
    unexpected = _entity(
        entities,
        entry,
        legacy,
        "dockhand_custom_unique_id",
        "sensor.web_custom",
    )

    async_migrate_registries(hass, entry, _coordinator(_container_data()), now=100)

    assert devices.async_get(legacy.id) is not None
    assert entities.async_get(unexpected.entity_id) is not None


async def test_cleanup_requires_grace_for_every_device_identifier(
    hass: HomeAssistant,
) -> None:
    """One old identifier cannot prematurely delete a newer partial migration."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    obsolete = _device(devices, entry, f"1_{RUNTIME_A}")
    partial_identifier = "container:entry-a:partial"
    obsolete = devices.async_update_device(
        obsolete.id,
        new_identifiers={
            (DOMAIN, f"1_{RUNTIME_A}"),
            (DOMAIN, partial_identifier),
        },
    )
    assert obsolete is not None
    coordinator = _coordinator(
        {"environments": {}, "containers": {}, "stacks": {}, "stats": {}}
    )
    coordinator.identity_registry.note_missing_resources({f"1_{RUNTIME_A}"}, 100)
    coordinator.identity_registry.note_missing_resources({partial_identifier}, 200)

    async_migrate_registries(
        hass,
        entry,
        coordinator,
        now=100 + MISSING_RESOURCE_GRACE_SECONDS,
    )
    assert devices.async_get(obsolete.id) is not None

    async_migrate_registries(
        hass,
        entry,
        coordinator,
        now=200 + MISSING_RESOURCE_GRACE_SECONDS,
    )
    assert devices.async_get(obsolete.id) is None


async def test_cleanup_keeps_device_with_foreign_identifier(
    hass: HomeAssistant,
) -> None:
    """Cleanup cannot delete a device another integration has identified."""
    entry = _entry(hass)
    devices = dr.async_get(hass)
    obsolete = _device(devices, entry, f"1_{RUNTIME_A}")
    obsolete = devices.async_update_device(
        obsolete.id,
        new_identifiers={
            (DOMAIN, f"1_{RUNTIME_A}"),
            ("other_integration", "shared-device"),
        },
    )
    assert obsolete is not None
    coordinator = _coordinator(
        {"environments": {}, "containers": {}, "stacks": {}, "stats": {}}
    )
    coordinator.identity_registry.note_missing_resources({f"1_{RUNTIME_A}"}, 100)

    async_migrate_registries(
        hass,
        entry,
        coordinator,
        now=100 + MISSING_RESOURCE_GRACE_SECONDS,
    )

    assert devices.async_get(obsolete.id) is not None
