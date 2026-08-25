"""Entity and device registry migration helpers for Dockhand."""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, MISSING_RESOURCE_GRACE_SECONDS
from .identity import entity_unique_id, environment_key, normalize_container_name

if TYPE_CHECKING:
    from .coordinator import DockhandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

_LEGACY_CONTAINER_IDENTIFIER = re.compile(r"^[^_]+_[0-9a-fA-F]{12,64}$")
_SCOPED_RESOURCE_PREFIXES = ("container:", "environment:", "stack:")
_ENTITY_ID_SUFFIX = re.compile(r"_(\d+)$")


def _belongs_to_entry(device: dr.DeviceEntry, entry_id: str) -> bool:
    """Return whether a device is owned by this config entry."""
    return device.config_entry_id == entry_id


def _dockhand_identifiers(device: dr.DeviceEntry) -> set[str]:
    """Return integration-owned identifiers for a device."""
    return {identifier for domain, identifier in device.identifiers if domain == DOMAIN}


def _entry_devices(
    device_registry: dr.DeviceRegistry, entry_id: str
) -> list[dr.DeviceEntry]:
    """Return all devices owned by a config entry."""
    return list(dr.async_entries_for_config_entry(device_registry, entry_id))


def _get_device(
    device_registry: dr.DeviceRegistry,
    config_entry: ConfigEntry,
    identifier: str,
) -> dr.DeviceEntry | None:
    """Look up one Dockhand device without crossing config-entry boundaries."""
    return device_registry.async_get_device_by_identifier(
        (DOMAIN, identifier), config_entry.entry_id
    )


def _entity_id_rank(entity_id: str) -> tuple[int, int]:
    """Rank generated entity IDs, preferring unsuffixed and lower suffixes."""
    if match := _ENTITY_ID_SUFFIX.search(entity_id):
        return (1, int(match.group(1)))
    return (0, 0)


def _merge_entity_customizations(
    entity_registry: er.EntityRegistry,
    source: er.RegistryEntry,
    target: er.RegistryEntry,
) -> None:
    """Copy non-default user customizations when consolidating duplicates."""
    changes: dict[str, Any] = {}
    for field in (
        "aliases",
        "area_id",
        "categories",
        "device_class",
        "disabled_by",
        "hidden_by",
        "icon",
        "labels",
        "name",
    ):
        source_value = getattr(source, field, None)
        target_value = getattr(target, field, None)
        if source_value and not target_value:
            changes[field] = source_value
    if changes:
        entity_registry.async_update_entity(target.entity_id, **changes)


def _remove_device(
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    config_entry: ConfigEntry,
    device: dr.DeviceEntry,
) -> bool:
    """Remove an integration-owned device after its Dockhand entities are gone."""
    if not _belongs_to_entry(device, config_entry.entry_id):
        _LOGGER.error(
            "Refusing to remove Dockhand device %s owned by another config entry",
            device.id,
        )
        return False

    foreign_identifiers = {
        identifier for identifier in device.identifiers if identifier[0] != DOMAIN
    }
    if foreign_identifiers or device.connections:
        _LOGGER.warning(
            "Keeping obsolete Dockhand device %s because another integration "
            "identifier or connection is attached",
            device.id,
        )
        return False

    entries = list(er.async_entries_for_device(entity_registry, device.id))
    foreign_entries = [
        entity
        for entity in entries
        if entity.config_entry_id != config_entry.entry_id or entity.platform != DOMAIN
    ]
    if foreign_entries:
        _LOGGER.warning(
            "Keeping obsolete Dockhand device %s because %d entities from other "
            "integrations or config entries still reference it",
            device.id,
            len(foreign_entries),
        )
        return False

    for entity in entries:
        if (
            entity.config_entry_id == config_entry.entry_id
            and entity.platform == DOMAIN
        ):
            entity_registry.async_remove(entity.entity_id)

    device_registry.async_remove_device(device.id)
    return True


def _migrate_entities(
    entity_registry: er.EntityRegistry,
    config_entry: ConfigEntry,
    source_device: dr.DeviceEntry,
    target_device: dr.DeviceEntry,
    old_unique_prefixes: tuple[str, ...],
    new_resource_key: str,
) -> bool:
    """Migrate unique IDs while preserving the oldest generated entity IDs."""
    migration_complete = True
    for registry_entry in list(
        er.async_entries_for_device(entity_registry, source_device.id)
    ):
        if (
            registry_entry.config_entry_id != config_entry.entry_id
            or registry_entry.platform != DOMAIN
            or not isinstance(registry_entry.unique_id, str)
        ):
            continue

        suffix = next(
            (
                registry_entry.unique_id.removeprefix(prefix)
                for prefix in old_unique_prefixes
                if registry_entry.unique_id.startswith(prefix)
            ),
            None,
        )
        if not suffix:
            migration_complete = False
            _LOGGER.warning(
                "Keeping entity %s on its legacy device because its unique ID "
                "does not match a known Dockhand schema",
                registry_entry.entity_id,
            )
            continue

        new_unique_id = entity_unique_id(new_resource_key, suffix)
        conflict_id = entity_registry.async_get_entity_id(
            registry_entry.domain,
            registry_entry.platform,
            new_unique_id,
        )
        if conflict_id and conflict_id != registry_entry.entity_id:
            conflict = entity_registry.async_get(conflict_id)
            if conflict is None:
                _LOGGER.error(
                    "Registry reported missing conflict %s while migrating %s",
                    conflict_id,
                    registry_entry.entity_id,
                )
                migration_complete = False
                continue
            if (
                conflict.config_entry_id != config_entry.entry_id
                or conflict.platform != DOMAIN
            ):
                _LOGGER.error(
                    "Refusing to replace entity %s owned by another config entry",
                    conflict_id,
                )
                migration_complete = False
                continue

            if _entity_id_rank(conflict_id) < _entity_id_rank(registry_entry.entity_id):
                _merge_entity_customizations(entity_registry, registry_entry, conflict)
                if conflict.device_id != target_device.id:
                    entity_registry.async_update_entity(
                        conflict.entity_id,
                        device_id=target_device.id,
                    )
                _LOGGER.info(
                    "Removing duplicate entity %s in favor of older ID %s",
                    registry_entry.entity_id,
                    conflict_id,
                )
                entity_registry.async_remove(registry_entry.entity_id)
                continue

            _merge_entity_customizations(entity_registry, conflict, registry_entry)
            _LOGGER.info(
                "Removing duplicate entity %s in favor of older ID %s",
                conflict_id,
                registry_entry.entity_id,
            )
            entity_registry.async_remove(conflict_id)

        changes: dict[str, Any] = {}
        if registry_entry.unique_id != new_unique_id:
            changes["new_unique_id"] = new_unique_id
        if registry_entry.device_id != target_device.id:
            changes["device_id"] = target_device.id
        if changes:
            entity_registry.async_update_entity(
                registry_entry.entity_id,
                **changes,
            )

    return migration_complete


def _merge_device_customizations(
    device_registry: dr.DeviceRegistry,
    source: dr.DeviceEntry,
    target: dr.DeviceEntry,
) -> dr.DeviceEntry:
    """Keep user-assigned metadata when a duplicate device is consolidated."""
    changes: dict[str, Any] = {}
    if source.area_id and not target.area_id:
        changes["area_id"] = source.area_id
    if source.name_by_user and not target.name_by_user:
        changes["name_by_user"] = source.name_by_user
    merged_labels = source.labels | target.labels
    if merged_labels != target.labels:
        changes["labels"] = merged_labels
    if not changes:
        return target
    updated = device_registry.async_update_device(target.id, **changes)
    return updated or target


def _restore_unsuffixed_entity_ids(
    entity_registry: er.EntityRegistry,
    config_entry: ConfigEntry,
    target_device: dr.DeviceEntry,
    resource_key: str,
) -> None:
    """Restore a generated base entity ID after its duplicate was removed."""
    entries = sorted(
        er.async_entries_for_device(entity_registry, target_device.id),
        key=lambda entry: (_entity_id_rank(entry.entity_id), entry.entity_id),
    )
    for registry_entry in entries:
        if (
            registry_entry.config_entry_id != config_entry.entry_id
            or registry_entry.platform != DOMAIN
            or not isinstance(registry_entry.unique_id, str)
            or not registry_entry.unique_id.startswith(f"{resource_key}:")
            or (suffix_match := _ENTITY_ID_SUFFIX.search(registry_entry.entity_id))
            is None
        ):
            continue

        base_entity_id = registry_entry.entity_id[: suffix_match.start()]
        if entity_registry.async_get(base_entity_id) is not None:
            continue

        _LOGGER.info(
            "Restoring Dockhand entity ID %s to available original ID %s",
            registry_entry.entity_id,
            base_entity_id,
        )
        entity_registry.async_update_entity(
            registry_entry.entity_id,
            new_entity_id=base_entity_id,
        )


def _migrate_device(
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    config_entry: ConfigEntry,
    old_device: dr.DeviceEntry | None,
    new_resource_key: str,
    old_unique_prefixes: tuple[str, ...],
    *,
    name: str,
    model: str,
    sw_version: str | None = None,
    configuration_url: str | None = None,
    via_device_id: str | None = None,
) -> dr.DeviceEntry:
    """Migrate or create one device with a config-entry-scoped identifier."""
    target_device = _get_device(device_registry, config_entry, new_resource_key)

    if target_device is None and old_device is not None:
        identifiers = {
            identifier
            for identifier in old_device.identifiers
            if identifier[0] != DOMAIN
        }
        identifiers.add((DOMAIN, new_resource_key))
        target_device = device_registry.async_update_device(
            old_device.id,
            new_identifiers=identifiers,
            name=name,
            manufacturer="Dockhand",
            model=model,
            sw_version=sw_version,
            configuration_url=configuration_url,
            via_device_id=via_device_id,
        )

    if target_device is None:
        target_device = device_registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, new_resource_key)},
            name=name,
            manufacturer="Dockhand",
            model=model,
            sw_version=sw_version,
            configuration_url=configuration_url,
            via_device_id=via_device_id,
        )
    else:
        target_device = (
            device_registry.async_update_device(
                target_device.id,
                name=name,
                manufacturer="Dockhand",
                model=model,
                sw_version=sw_version,
                configuration_url=configuration_url,
                via_device_id=via_device_id,
            )
            or target_device
        )

    migration_complete = True
    if old_device is not None:
        migration_complete = _migrate_entities(
            entity_registry,
            config_entry,
            old_device,
            target_device,
            old_unique_prefixes,
            new_resource_key,
        )

    if (
        migration_complete
        and old_device is not None
        and old_device.id != target_device.id
    ):
        target_device = _merge_device_customizations(
            device_registry, old_device, target_device
        )
        _remove_device(
            entity_registry,
            device_registry,
            config_entry,
            old_device,
        )
    elif old_device is not None and old_device.id != target_device.id:
        _LOGGER.warning(
            "Keeping legacy Dockhand device %s because not every entity could be "
            "migrated safely",
            old_device.id,
        )

    _restore_unsuffixed_entity_ids(
        entity_registry,
        config_entry,
        target_device,
        new_resource_key,
    )

    return target_device


def _legacy_container_devices(
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    config_entry: ConfigEntry,
    environment_id: Any,
    container_name: str,
    runtime_id: str,
    claimed_device_ids: set[str],
) -> list[dr.DeviceEntry]:
    """Find legacy or safely scoped partial-migration container devices."""
    normalized_name = normalize_container_name(container_name)
    parent = _get_device(
        device_registry,
        config_entry,
        environment_key(config_entry.entry_id, environment_id),
    )
    candidates = []
    for device in _entry_devices(device_registry, config_entry.entry_id):
        identifiers = _dockhand_identifiers(device)
        legacy_identifiers = {
            identifier
            for identifier in identifiers
            if identifier.startswith(f"{environment_id}_")
            and _LEGACY_CONTAINER_IDENTIFIER.fullmatch(identifier)
        }
        scoped_identifiers = {
            identifier
            for identifier in identifiers
            if identifier.startswith(f"container:{config_entry.entry_id}:")
        }
        scoped_environment_match = bool(
            scoped_identifiers
            and parent is not None
            and device.via_device_id == parent.id
        )
        if (
            device.id in claimed_device_ids
            or device.model != "Docker Container"
            or not (legacy_identifiers or scoped_environment_match)
        ):
            continue
        exact_runtime_match = f"{environment_id}_{runtime_id}" in legacy_identifiers
        name_match = bool(normalized_name) and (
            normalize_container_name(device.name) == normalized_name
        )
        if exact_runtime_match or name_match:
            candidates.append(device)

    def _rank(device: dr.DeviceEntry) -> tuple[int, int, str, str]:
        ranks = [
            _entity_id_rank(registry_entry.entity_id)
            for registry_entry in er.async_entries_for_device(
                entity_registry, device.id
            )
            if registry_entry.config_entry_id == config_entry.entry_id
            and registry_entry.platform == DOMAIN
        ]
        return (
            sum(rank[0] for rank in ranks),
            sum(rank[1] for rank in ranks),
            str(getattr(device, "created_at", "")),
            device.id,
        )

    return sorted(candidates, key=_rank)


def _canonical_legacy_container_device(
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    config_entry: ConfigEntry,
    environment_id: Any,
    container_name: str,
    claimed_device_ids: set[str],
) -> dr.DeviceEntry | None:
    """Compatibility helper returning the best name-matched legacy device."""
    candidates = _legacy_container_devices(
        entity_registry,
        device_registry,
        config_entry,
        environment_id,
        container_name,
        "",
        claimed_device_ids,
    )
    return candidates[0] if candidates else None


def current_resource_identifiers(
    config_entry: ConfigEntry, data: dict[str, Any]
) -> set[str]:
    """Return all resource identifiers currently supplied by Dockhand."""
    identifiers = set(data.get("containers", {})) | set(data.get("stacks", {}))
    identifiers.update(
        environment_key(config_entry.entry_id, environment_id)
        for environment_id in data.get("environments", {})
    )
    return identifiers


def _legacy_identifier(device: dr.DeviceEntry) -> str | None:
    """Return one legacy Dockhand resource identifier, if present."""
    return next(
        (
            identifier
            for identifier in _dockhand_identifiers(device)
            if identifier.startswith(("env_", "stack_"))
            or _LEGACY_CONTAINER_IDENTIFIER.fullmatch(identifier)
        ),
        None,
    )


def async_migrate_registries(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: DockhandDataUpdateCoordinator,
    *,
    now: float | None = None,
) -> None:
    """Migrate legacy registry entries and resume interrupted migrations."""
    data = coordinator.data
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    claimed_device_ids: set[str] = set()
    migration_time = time.time() if now is None else now

    try:
        for environment_id, environment in data.get("environments", {}).items():
            old_identifier = f"env_{environment_id}"
            new_identifier = environment_key(config_entry.entry_id, environment_id)
            old_device = _get_device(device_registry, config_entry, old_identifier)
            environment_target = _migrate_device(
                entity_registry,
                device_registry,
                config_entry,
                old_device,
                new_identifier,
                (f"{DOMAIN}_{old_identifier}_",),
                name=f"Dockhand {environment.get('name', environment_id)}",
                model="Docker Environment",
                configuration_url=coordinator.client.base_url,
            )
            environment["device_id"] = environment_target.id
            claimed_device_ids.add(environment_target.id)

        for new_identifier, stack in data.get("stacks", {}).items():
            environment_id = stack.get("environment_id")
            stack_id = stack.get("id") or stack.get("name")
            old_resource_key = f"{environment_id}_{stack_id}"
            old_identifier = f"stack_{old_resource_key}"
            parent = _get_device(
                device_registry,
                config_entry,
                environment_key(config_entry.entry_id, environment_id),
            )
            old_device = _get_device(device_registry, config_entry, old_identifier)
            stack_target = _migrate_device(
                entity_registry,
                device_registry,
                config_entry,
                old_device,
                new_identifier,
                (f"{DOMAIN}_stack_{old_resource_key}_",),
                name=str(stack.get("name", f"Stack {stack_id}")),
                model="Docker Stack",
                via_device_id=parent.id if parent else None,
            )
            stack["device_id"] = stack_target.id
            stack["via_device_id"] = parent.id if parent else None
            claimed_device_ids.add(stack_target.id)

        for new_identifier, container in data.get("containers", {}).items():
            environment_id = container.get("environment_id")
            runtime_id = str(container.get("id", ""))
            parent = _get_device(
                device_registry,
                config_entry,
                environment_key(config_entry.entry_id, environment_id),
            )
            candidates = _legacy_container_devices(
                entity_registry,
                device_registry,
                config_entry,
                environment_id,
                str(container.get("name", "")),
                runtime_id,
                claimed_device_ids,
            )
            if not candidates:
                candidates = [None]

            container_target: dr.DeviceEntry | None = None
            for old_device in candidates:
                old_identifier = f"{environment_id}_{runtime_id}"
                old_unique_prefixes = (f"{DOMAIN}_{old_identifier}_",)
                if old_device is not None:
                    device_identifiers = _dockhand_identifiers(old_device)
                    source_identifier = next(
                        (
                            identifier
                            for identifier in device_identifiers
                            if identifier.startswith(f"{environment_id}_")
                            and _LEGACY_CONTAINER_IDENTIFIER.fullmatch(identifier)
                        ),
                        None,
                    )
                    if source_identifier is None:
                        source_identifier = next(
                            (
                                identifier
                                for identifier in device_identifiers
                                if identifier.startswith(
                                    f"container:{config_entry.entry_id}:"
                                )
                            ),
                            old_identifier,
                        )
                        old_unique_prefixes = (f"{source_identifier}:",)
                    else:
                        old_unique_prefixes = (f"{DOMAIN}_{source_identifier}_",)
                container_target = _migrate_device(
                    entity_registry,
                    device_registry,
                    config_entry,
                    old_device,
                    new_identifier,
                    (*old_unique_prefixes, f"{new_identifier}:"),
                    name=str(container.get("name", "unknown")).lstrip("/"),
                    model="Docker Container",
                    sw_version=str(container.get("image") or "") or None,
                    via_device_id=parent.id if parent else None,
                )
                if old_device is not None:
                    claimed_device_ids.add(old_device.id)

            if container_target is not None:
                container["device_id"] = container_target.id
                container["via_device_id"] = parent.id if parent else None
                claimed_device_ids.add(container_target.id)

        current_identifiers = current_resource_identifiers(config_entry, data)
        missing_identifiers: set[str] = set()
        for device in _entry_devices(device_registry, config_entry.entry_id):
            identifiers = _dockhand_identifiers(device)
            if identifiers & current_identifiers:
                continue
            for identifier in identifiers:
                if identifier.startswith(_SCOPED_RESOURCE_PREFIXES) or (
                    _legacy_identifier(device) == identifier
                ):
                    missing_identifiers.add(identifier)
        coordinator.identity_registry.note_missing_resources(
            missing_identifiers, migration_time
        )

        async_sync_registry(
            hass,
            config_entry,
            coordinator,
            cleanup_stale=True,
            now=migration_time,
        )
    except Exception:
        _LOGGER.exception(
            "Dockhand registry migration failed for config entry %s; the next "
            "setup will safely resume the partial migration",
            config_entry.entry_id,
        )
        raise


def async_sync_registry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: DockhandDataUpdateCoordinator,
    *,
    cleanup_stale: bool = False,
    now: float | None = None,
) -> None:
    """Upsert registry metadata and optionally remove grace-expired resources."""
    data = coordinator.data
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    sync_time = time.time() if now is None else now

    for environment_id, environment in data.get("environments", {}).items():
        identifier = environment_key(config_entry.entry_id, environment_id)
        device = _migrate_device(
            entity_registry,
            device_registry,
            config_entry,
            None,
            identifier,
            (f"{DOMAIN}_{identifier}_",),
            name=f"Dockhand {environment.get('name', environment_id)}",
            model="Docker Environment",
            configuration_url=coordinator.client.base_url,
        )
        environment["device_id"] = device.id

    for identifier, stack in data.get("stacks", {}).items():
        parent = _get_device(
            device_registry,
            config_entry,
            environment_key(config_entry.entry_id, stack.get("environment_id")),
        )
        device = _migrate_device(
            entity_registry,
            device_registry,
            config_entry,
            None,
            identifier,
            (f"{DOMAIN}_{identifier}_",),
            name=str(stack.get("name", "Docker Stack")),
            model="Docker Stack",
            via_device_id=parent.id if parent else None,
        )
        stack["device_id"] = device.id
        stack["via_device_id"] = parent.id if parent else None

    for identifier, container in data.get("containers", {}).items():
        parent = _get_device(
            device_registry,
            config_entry,
            environment_key(config_entry.entry_id, container.get("environment_id")),
        )
        device = _migrate_device(
            entity_registry,
            device_registry,
            config_entry,
            None,
            identifier,
            (f"{DOMAIN}_{identifier}_",),
            name=str(container.get("name", "unknown")).lstrip("/"),
            model="Docker Container",
            sw_version=str(container.get("image") or "") or None,
            via_device_id=parent.id if parent else None,
        )
        container["device_id"] = device.id
        container["via_device_id"] = parent.id if parent else None

    if not cleanup_stale:
        return

    current_identifiers = current_resource_identifiers(config_entry, data)
    for device in list(_entry_devices(device_registry, config_entry.entry_id)):
        identifiers = _dockhand_identifiers(device)
        if identifiers & current_identifiers:
            continue
        managed_identifiers = {
            identifier
            for identifier in identifiers
            if identifier.startswith(_SCOPED_RESOURCE_PREFIXES)
            or _LEGACY_CONTAINER_IDENTIFIER.fullmatch(identifier)
            or identifier.startswith(("env_", "stack_"))
        }
        if not managed_identifiers or not all(
            coordinator.identity_registry.is_stale(
                identifier, sync_time, MISSING_RESOURCE_GRACE_SECONDS
            )
            for identifier in managed_identifiers
        ):
            continue
        if _remove_device(entity_registry, device_registry, config_entry, device):
            for identifier in managed_identifiers:
                coordinator.identity_registry.forget_resource(identifier)
            _LOGGER.info(
                "Removed Dockhand device %s after the seven-day grace period",
                device.id,
            )
