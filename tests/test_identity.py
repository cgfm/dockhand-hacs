"""Regression tests for stable Dockhand container identities."""

from __future__ import annotations

from custom_components.dockhand.identity import (
    ContainerIdentityRegistry,
    compose_identity,
    container_key,
    container_labels,
    entity_unique_id,
    environment_key,
    reconcile_resource_keys,
    stack_key,
)


def test_recreated_container_keeps_identity_by_name() -> None:
    """A new Docker ID with the same logical name reuses the identity."""
    registry = ContainerIdentityRegistry()
    stable_id = registry.resolve(1, {"id": "old-id", "name": "/web"}, set())

    assert stable_id == registry.resolve(1, {"id": "new-id", "name": "/web"}, set())


def test_runtime_id_match_tracks_rename_and_later_recreate() -> None:
    """A rename is persisted and a later recreation uses the renamed identity."""
    registry = ContainerIdentityRegistry()
    stable_id = registry.resolve(1, {"id": "runtime", "name": "/old"}, set())

    assert stable_id == registry.resolve(1, {"id": "runtime", "name": "/new"}, set())
    assert stable_id == registry.resolve(
        1, {"id": "replacement", "name": "/new"}, set()
    )


def test_compose_identity_survives_recreate_and_rename() -> None:
    """Compose project, service and replica form the strongest identity."""
    labels = {
        "com.docker.compose.project": "monitoring",
        "com.docker.compose.service": "grafana",
        "com.docker.compose.container-number": "2",
    }
    registry = ContainerIdentityRegistry()
    stable_id = registry.resolve(
        1, {"id": "old", "name": "/grafana-2", "labels": labels}, set()
    )

    assert stable_id == registry.resolve(
        1, {"id": "new", "name": "/renamed", "labels": labels}, set()
    )


def test_incomplete_refresh_does_not_erase_compose_identity() -> None:
    """Missing transient metadata cannot break a later recreate-and-rename match."""
    labels = {
        "com.docker.compose.project": "monitoring",
        "com.docker.compose.service": "grafana",
        "com.docker.compose.container-number": "1",
    }
    registry = ContainerIdentityRegistry()
    stable_id = registry.resolve(
        1, {"id": "old", "name": "/grafana", "labels": labels}, set()
    )

    assert stable_id == registry.resolve(1, {"id": "old"}, set())
    assert stable_id == registry.resolve(
        1, {"id": "new", "name": "/renamed", "labels": labels}, set()
    )


def test_nested_inspect_labels_are_supported() -> None:
    """Docker inspect's Config.Labels response shape yields a Compose identity."""
    assert (
        compose_identity(
            {
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "project",
                        "com.docker.compose.service": "worker",
                        "com.docker.compose.container-number": "3",
                    }
                }
            }
        )
        == "project\x1fworker\x1f3"
    )


def test_only_labels_are_selected_from_sensitive_inspect_data() -> None:
    """Identity extraction never needs Docker environment variables."""
    inspect = {
        "Config": {
            "Env": ["PASSWORD=do-not-retain"],
            "Labels": {"com.docker.compose.project": "project"},
        },
        "Mounts": [{"Source": "/private/path"}],
    }

    assert container_labels(inspect) == {"com.docker.compose.project": "project"}


def test_equal_names_are_scoped_to_environment() -> None:
    """Equal names in different environments do not collide."""
    registry = ContainerIdentityRegistry()
    first = registry.resolve(1, {"id": "one", "name": "/web"}, set())
    second = registry.resolve(2, {"id": "two", "name": "/web"}, set())

    assert first != second


def test_concurrent_equal_names_are_distinct() -> None:
    """One identity cannot be assigned twice in a coordinator snapshot."""
    registry = ContainerIdentityRegistry()
    used: set[str] = set()
    first = registry.resolve(1, {"id": "one", "name": "/same"}, used)
    second = registry.resolve(1, {"id": "two", "name": "/same"}, used)

    assert first != second


def test_missing_names_use_runtime_id_instead_of_colliding() -> None:
    """Incomplete API records do not all collapse into one identity."""
    registry = ContainerIdentityRegistry()

    assert registry.resolve(1, {"id": "one"}, set()) != registry.resolve(
        1, {"id": "two"}, set()
    )


def test_serialization_survives_restart() -> None:
    """Identity and cleanup state persist across a Home Assistant restart."""
    registry = ContainerIdentityRegistry()
    stable_id = registry.resolve(1, {"id": "old", "name": "/web"}, set())
    registry.reconcile_resources({"container:entry:stable"}, 100)
    registry.reconcile_resources(set(), 110)

    restored = ContainerIdentityRegistry(registry.as_dict())

    assert stable_id == restored.resolve(1, {"id": "new", "name": "/web"}, set())
    assert restored.is_stale("container:entry:stable", 120, 10)


def test_legacy_store_is_inspected_once_then_persists_marker() -> None:
    """Pre-1.2 inspection state is enriched once without polling inspect forever."""
    registry = ContainerIdentityRegistry()
    stable_id = registry.resolve(1, {"id": "runtime", "name": "/web"}, set())
    legacy_data = registry.as_dict()
    legacy_data["identities"][stable_id].pop("inspected_container_id")
    restored = ContainerIdentityRegistry(legacy_data)
    container = {"id": "runtime", "name": "/web"}

    assert restored.needs_inspect(1, container)
    restored.mark_inspected(stable_id, "runtime")
    assert not restored.needs_inspect(1, container)
    assert not ContainerIdentityRegistry(restored.as_dict()).needs_inspect(1, container)


def test_missing_grace_is_cancelled_when_resource_returns() -> None:
    """A temporary API gap never marks a returned resource stale."""
    registry = ContainerIdentityRegistry()
    registry.reconcile_resources({"resource"}, 100)
    registry.reconcile_resources(set(), 110)
    assert not registry.is_stale("resource", 119, 10)

    registry.reconcile_resources({"resource"}, 120)

    assert not registry.is_stale("resource", 999, 10)


def test_dynamic_entity_keys_are_never_readded() -> None:
    """Temporary or long absences cannot create a second runtime entity object."""
    known = {"container"}
    assert reconcile_resource_keys(set(), known) == set()
    assert reconcile_resource_keys({"container"}, known) == set()
    assert reconcile_resource_keys({"container", "new"}, known) == {"new"}
    assert known == {"container", "new"}


def test_resource_keys_are_config_entry_scoped() -> None:
    """Two Dockhand entries cannot share devices or entities."""
    assert environment_key("a", 1) != environment_key("b", 1)
    assert container_key("a", "stable") != container_key("b", "stable")
    assert stack_key("a", 1, "stack") != stack_key("b", 1, "stack")
    assert (
        entity_unique_id(container_key("entry", "logical"), "state")
        == "container:entry:logical:state"
    )
