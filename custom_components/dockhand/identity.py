"""Stable identity handling for Dockhand resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_CONTAINER_NUMBER_LABEL = "com.docker.compose.container-number"


def normalize_container_name(name: Any) -> str:
    """Return a normalized Docker container name used only for matching."""
    return str(name or "").lstrip("/").casefold()


def environment_key(config_entry_id: str, environment_id: Any) -> str:
    """Return the stable Home Assistant key for an environment."""
    return f"environment:{config_entry_id}:{environment_id}"


def container_key(config_entry_id: str, stable_id: str) -> str:
    """Return the stable Home Assistant key for a container."""
    return f"container:{config_entry_id}:{stable_id}"


def stack_key(config_entry_id: str, environment_id: Any, stack_id: Any) -> str:
    """Return the stable Home Assistant key for a stack."""
    return f"stack:{config_entry_id}:{environment_id}:{stack_id}"


def entity_unique_id(resource_key: str, entity_key: str) -> str:
    """Return a unique ID scoped to one stable resource."""
    return f"{resource_key}:{entity_key}"


def reconcile_resource_keys[ResourceKeyT: (str, int)](
    current_keys: set[ResourceKeyT],
    known_keys: set[ResourceKeyT],
) -> set[ResourceKeyT]:
    """Return resources not yet represented by an entity in this runtime.

    Entity objects remain registered while a resource is temporarily absent. Keeping
    the key known prevents a later refresh from adding a second object with the same
    unique ID. Registry cleanup is deliberately handled only during entry setup after
    the persisted grace period has elapsed.
    """
    new_keys = current_keys - known_keys
    known_keys.update(current_keys)
    return new_keys


def container_labels(container: dict[str, Any]) -> dict[str, Any]:
    """Extract Docker labels from known Dockhand response shapes."""
    for key in ("labels", "Labels"):
        value = container.get(key)
        if isinstance(value, dict):
            return value

    config = container.get("Config")
    if isinstance(config, dict):
        value = config.get("Labels")
        if isinstance(value, dict):
            return value

    return {}


def compose_identity(container: dict[str, Any]) -> str | None:
    """Return a stable Compose identity when the required labels are present."""
    labels = container_labels(container)
    project = (
        labels.get(COMPOSE_PROJECT_LABEL)
        or container.get("composeProject")
        or container.get("stack")
    )
    service = (
        labels.get(COMPOSE_SERVICE_LABEL)
        or container.get("composeService")
        or container.get("service")
    )
    number = labels.get(COMPOSE_CONTAINER_NUMBER_LABEL) or container.get(
        "composeContainerNumber"
    )

    # Some Dockhand list responses expose the stack but omit Docker labels. The
    # container name is still a useful service fallback; inspect data is preferred
    # and merged by the coordinator for every previously unseen runtime ID.
    if project and not service:
        service = normalize_container_name(container.get("name"))

    if not project or not service:
        return None

    return "\x1f".join((str(project), str(service), str(number or "1")))


@dataclass(slots=True)
class ContainerIdentity:
    """Persisted logical identity for a Docker container."""

    stable_id: str
    environment_id: str
    container_id: str
    name: str
    compose_id: str | None = None
    inspected_container_id: str = ""

    @classmethod
    def from_dict(cls, stable_id: str, data: dict[str, Any]) -> ContainerIdentity:
        """Create an identity from persisted JSON data."""
        return cls(
            stable_id=stable_id,
            environment_id=str(data.get("environment_id", "")),
            container_id=str(data.get("container_id", "")),
            name=normalize_container_name(data.get("name")),
            compose_id=(
                str(data["compose_id"]) if data.get("compose_id") is not None else None
            ),
            inspected_container_id=str(data.get("inspected_container_id", "")),
        )

    def as_dict(self) -> dict[str, str | None]:
        """Return a JSON-serializable representation."""
        return {
            "environment_id": self.environment_id,
            "container_id": self.container_id,
            "name": self.name,
            "compose_id": self.compose_id,
            "inspected_container_id": self.inspected_container_id,
        }


class ContainerIdentityRegistry:
    """Resolve changing Docker IDs to persistent logical IDs."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """Initialize the registry from persisted data."""
        raw_identities = (data or {}).get("identities", {})
        self._identities: dict[str, ContainerIdentity] = {}
        if isinstance(raw_identities, dict):
            for stable_id, raw in raw_identities.items():
                if isinstance(stable_id, str) and isinstance(raw, dict):
                    self._identities[stable_id] = ContainerIdentity.from_dict(
                        stable_id, raw
                    )
        raw_known = (data or {}).get("known_resources", [])
        self._known_resources = (
            {value for value in raw_known if isinstance(value, str)}
            if isinstance(raw_known, list)
            else set()
        )
        raw_missing = (data or {}).get("missing_resources", {})
        self._missing_resources: dict[str, float] = {}
        if isinstance(raw_missing, dict):
            for identifier, missing_since in raw_missing.items():
                if isinstance(identifier, str) and isinstance(
                    missing_since, int | float
                ):
                    self._missing_resources[identifier] = float(missing_since)
        self.dirty = False

    def needs_inspect(self, environment_id: Any, container: dict[str, Any]) -> bool:
        """Return whether inspect data is needed for a new runtime identity."""
        env_id = str(environment_id)
        runtime_id = str(container.get("id", ""))
        if not runtime_id:
            return False
        return not any(
            identity.environment_id == env_id
            and identity.inspected_container_id == runtime_id
            for identity in self._identities.values()
        )

    def mark_inspected(self, stable_id: str, container_id: str) -> None:
        """Remember that inspect data was sanitized for this Docker runtime."""
        identity = self._identities[stable_id]
        if identity.inspected_container_id == container_id:
            return
        identity.inspected_container_id = container_id
        self.dirty = True

    def resolve(
        self,
        environment_id: Any,
        container: dict[str, Any],
        used_ids: set[str],
    ) -> str:
        """Resolve a container, preferring runtime ID, Compose identity, then name."""
        env_id = str(environment_id)
        runtime_id = str(container.get("id", ""))
        name = normalize_container_name(container.get("name"))
        compose_id = compose_identity(container)

        matchers = (
            lambda identity: bool(runtime_id) and identity.container_id == runtime_id,
            lambda identity: bool(compose_id) and identity.compose_id == compose_id,
            lambda identity: bool(name) and identity.name == name,
        )

        identity: ContainerIdentity | None = None
        candidates = sorted(self._identities.values(), key=lambda item: item.stable_id)
        for matcher in matchers:
            identity = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.stable_id not in used_ids
                    and candidate.environment_id == env_id
                    and matcher(candidate)
                ),
                None,
            )
            if identity is not None:
                break

        if identity is None:
            identity_seed = compose_id or name or runtime_id
            stable_id = uuid5(
                NAMESPACE_URL,
                f"dockhand\x1f{env_id}\x1f{identity_seed}",
            ).hex
            if stable_id in self._identities or stable_id in used_ids:
                stable_id = uuid4().hex
            identity = ContainerIdentity(
                stable_id=stable_id,
                environment_id=env_id,
                container_id=runtime_id,
                name=name,
                compose_id=compose_id,
            )
            self._identities[stable_id] = identity
            self.dirty = True
        else:
            # A temporarily incomplete list/inspect response must not erase the
            # stronger matching information learned on an earlier refresh.
            updated_name = name or identity.name
            updated_compose_id = compose_id or identity.compose_id
            updated = (
                identity.container_id != runtime_id
                or identity.name != updated_name
                or identity.compose_id != updated_compose_id
            )
            if updated:
                identity.container_id = runtime_id
                identity.name = updated_name
                identity.compose_id = updated_compose_id
                self.dirty = True

        used_ids.add(identity.stable_id)
        return identity.stable_id

    def reconcile_resources(self, current: set[str], now: float) -> None:
        """Persist first-missing timestamps for known resource identifiers."""
        changed = False
        for identifier in current:
            if identifier not in self._known_resources:
                self._known_resources.add(identifier)
                changed = True
            if identifier in self._missing_resources:
                self._missing_resources.pop(identifier)
                changed = True

        for identifier in self._known_resources - current:
            if identifier not in self._missing_resources:
                self._missing_resources[identifier] = now
                changed = True

        self.dirty |= changed

    def note_missing_resources(self, identifiers: set[str], now: float) -> None:
        """Start the grace period for legacy resources absent during migration."""
        changed = False
        for identifier in identifiers:
            if identifier not in self._known_resources:
                self._known_resources.add(identifier)
                changed = True
            if identifier not in self._missing_resources:
                self._missing_resources[identifier] = now
                changed = True
        self.dirty |= changed

    def is_stale(self, identifier: str, now: float, grace_seconds: int) -> bool:
        """Return whether a resource has been missing for the full grace period."""
        missing_since = self._missing_resources.get(identifier)
        return (
            missing_since is not None
            and now >= missing_since
            and now - missing_since >= grace_seconds
        )

    def forget_resource(self, identifier: str) -> None:
        """Forget cleanup state after its registry device was removed."""
        changed = identifier in self._known_resources
        self._known_resources.discard(identifier)
        changed |= self._missing_resources.pop(identifier, None) is not None
        self.dirty |= changed

    def as_dict(self) -> dict[str, Any]:
        """Return all identities as JSON-serializable data."""
        return {
            "identities": {
                stable_id: identity.as_dict()
                for stable_id, identity in self._identities.items()
            },
            "known_resources": sorted(self._known_resources),
            "missing_resources": dict(sorted(self._missing_resources.items())),
        }
