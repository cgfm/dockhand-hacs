"""Data update coordinator for Dockhand."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    DockhandApiClient,
    DockhandApiError,
    DockhandAuthError,
    DockhandConnectionError,
    DockhandRateLimitError,
)
from .const import (
    CONF_ENVIRONMENTS,
    CONF_SCAN_INTERVAL,
    DATA_IMAGE_UPDATE_STATUS,
    DATA_IMAGE_UPDATES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_PARALLEL_REQUESTS,
)
from .identity import (
    ContainerIdentityRegistry,
    container_key,
    container_labels,
    environment_key,
    stack_key,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


def identity_storage_key(config_entry_id: str) -> str:
    """Return the per-entry storage key for logical container identities."""
    return f"{DOMAIN}.container_identities.{config_entry_id}"


async def async_remove_identity_store(
    hass: HomeAssistant, config_entry_id: str
) -> None:
    """Remove persisted container identities for a deleted config entry."""
    await Store[dict[str, Any]](
        hass,
        STORAGE_VERSION,
        identity_storage_key(config_entry_id),
    ).async_remove()


class DockhandDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to fetch data from Dockhand API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: DockhandApiClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        if CONF_ENVIRONMENTS in config_entry.options:
            self._selected_env_ids: list[int] | None = config_entry.options[
                CONF_ENVIRONMENTS
            ]
        else:
            self._selected_env_ids = config_entry.data.get(CONF_ENVIRONMENTS)
        self._identity_store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            identity_storage_key(config_entry.entry_id),
        )
        self._identity_registry = ContainerIdentityRegistry()
        self._request_semaphore = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)
        self._pending_update_failures: set[int] = set()

        scan_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL,
            config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            config_entry=config_entry,
        )

    async def _async_setup(self) -> None:
        """Load persistent logical container identities."""
        stored = await self._identity_store.async_load()
        self._identity_registry = ContainerIdentityRegistry(stored)

    @property
    def identity_registry(self) -> ContainerIdentityRegistry:
        """Return the persistent identity and cleanup state."""
        return self._identity_registry

    async def async_save_identity_state(self) -> None:
        """Save identity state when registry migration changed it."""
        if not self._identity_registry.dirty:
            return
        await self._identity_store.async_save(self._identity_registry.as_dict())
        self._identity_registry.dirty = False

    async def _limited[ResultT](
        self,
        request: Callable[..., Awaitable[ResultT]],
        *args: Any,
        **kwargs: Any,
    ) -> ResultT:
        """Limit concurrent Dockhand requests across environments and stats."""
        async with self._request_semaphore:
            return await request(*args, **kwargs)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Dockhand."""
        try:
            data: dict[str, Any] = {
                "environments": {},
                "containers": {},
                DATA_IMAGE_UPDATES: {},
                DATA_IMAGE_UPDATE_STATUS: {},
                "stats": {},
                "stacks": {},
            }

            # Fetch environments
            environments = await self._limited(self.client.get_environments)
            env_map: dict[int, dict] = {}

            for env in environments:
                if not isinstance(env, dict):
                    raise DockhandApiError(
                        "Dockhand returned a non-object environment record"
                    )
                env_id = env.get("id")
                if not isinstance(env_id, int) or isinstance(env_id, bool):
                    raise DockhandApiError(
                        "Dockhand returned an environment without a numeric ID"
                    )
                if (
                    self._selected_env_ids is not None
                    and env_id not in self._selected_env_ids
                ):
                    continue
                env_map[env_id] = env
                data["environments"][env_id] = env

            # Fetch containers, stacks, and cached updates for all environments.
            used_identity_ids: set[str] = set()
            await asyncio.gather(
                *[
                    self._fetch_env_data(data, env_id, env_info, used_identity_ids)
                    for env_id, env_info in env_map.items()
                ]
            )

            # Fetch stats for all running containers in parallel
            running = [
                (unique_key, container)
                for unique_key, container in data["containers"].items()
                if container.get("state") == "running"
            ]
            await asyncio.gather(
                *[
                    self._fetch_container_stats(data, unique_key, container)
                    for unique_key, container in running
                ]
            )

            current_resources = set(data["containers"]) | set(data["stacks"])
            current_resources.update(
                environment_key(self.config_entry.entry_id, environment_id)
                for environment_id in data["environments"]
            )
            self._identity_registry.reconcile_resources(current_resources, time.time())
            await self.async_save_identity_state()

            return data

        except DockhandAuthError as err:
            raise ConfigEntryAuthFailed(
                f"Authentication with Dockhand failed: {err}"
            ) from err
        except DockhandConnectionError as err:
            raise UpdateFailed(f"Cannot connect to Dockhand: {err}") from err
        except DockhandRateLimitError as err:
            raise UpdateFailed(
                "Dockhand API rate limit exceeded", retry_after=err.retry_after
            ) from err
        except DockhandApiError as err:
            raise UpdateFailed(f"Dockhand API error: {err}") from err

    async def _fetch_env_data(
        self,
        data: dict[str, Any],
        env_id: int,
        env_info: dict,
        used_identity_ids: set[str],
    ) -> None:
        """Fetch containers and stacks for one environment (run concurrently)."""
        env_name = env_info.get("name", f"env-{env_id}")

        containers_result, stacks_result, pending_updates = await asyncio.gather(
            self._limited(self.client.get_containers, env_id),
            self._limited(self.client.get_stacks, env_id),
            self._fetch_pending_updates(env_id),
        )
        data[DATA_IMAGE_UPDATE_STATUS][env_id] = pending_updates is not None

        if not stacks_result:
            _LOGGER.debug("No stacks returned for environment ID %s", env_id)
        else:
            _LOGGER.debug(
                "Received %d stacks for environment ID %s",
                len(stacks_result),
                env_id,
            )

        containers: list[dict[str, Any]] = []
        for container in containers_result:
            if not isinstance(container, dict) or not container.get("id"):
                raise DockhandApiError(
                    f"Dockhand returned an invalid container in environment {env_id}"
                )
            containers.append(container)

        async def _with_inspect(
            container: dict[str, Any],
        ) -> tuple[dict[str, Any], bool]:
            """Merge Docker labels for a newly observed runtime ID."""
            if not self._identity_registry.needs_inspect(env_id, container):
                return container, False
            inspect = await self._limited(
                self.client.get_container_inspect,
                str(container["id"]),
                env_id,
            )
            # Inspect may contain environment variables and other secrets. Keep
            # only Docker labels needed for the logical Compose identity.
            inspect_labels = container_labels(inspect)
            list_labels = container_labels(container)
            if not inspect_labels and not list_labels:
                return container, True
            return {**container, "labels": {**inspect_labels, **list_labels}}, True

        enriched_containers = await asyncio.gather(
            *(_with_inspect(container) for container in containers)
        )

        pending_by_runtime_id = {
            str(update["container_id"]).lower(): update
            for update in pending_updates or []
        }

        for container, inspected in enriched_containers:
            stable_id = self._identity_registry.resolve(
                env_id, container, used_identity_ids
            )
            if inspected:
                self._identity_registry.mark_inspected(stable_id, str(container["id"]))
            unique_key = container_key(self.config_entry.entry_id, stable_id)
            data["containers"][unique_key] = {
                **container,
                "stable_id": stable_id,
                "environment_id": env_id,
                "environment_name": env_name,
            }
            runtime_id = str(container["id"]).lower()
            pending = pending_by_runtime_id.get(runtime_id)
            if pending is None:
                prefix_matches = [
                    update
                    for pending_id, update in pending_by_runtime_id.items()
                    if runtime_id.startswith(pending_id)
                    or pending_id.startswith(runtime_id)
                ]
                if len(prefix_matches) == 1:
                    pending = prefix_matches[0]
            if pending is not None:
                image_update: dict[str, Any] = {"available": True}
                if current_image := pending.get("current_image"):
                    image_update["current_image"] = current_image
                if checked_at := pending.get("checked_at"):
                    image_update["checked_at"] = checked_at
                data[DATA_IMAGE_UPDATES][unique_key] = image_update

        for stack in stacks_result:
            if not isinstance(stack, dict):
                raise DockhandApiError(
                    f"Dockhand returned a non-object stack in environment {env_id}"
                )
            stack_id = stack.get("id") or stack.get("name")
            if stack_id is None:
                raise DockhandApiError(
                    f"Dockhand returned a stack without an ID or name in "
                    f"environment {env_id}"
                )
            unique_stack_key = stack_key(self.config_entry.entry_id, env_id, stack_id)
            data["stacks"][unique_stack_key] = {
                **stack,
                "environment_id": env_id,
                "environment_name": env_name,
            }

    async def _fetch_pending_updates(self, env_id: int) -> list[dict[str, Any]] | None:
        """Fetch optional cached update metadata without failing the main refresh."""
        try:
            updates = await self._limited(
                self.client.get_pending_container_updates, env_id
            )
        except DockhandApiError:
            if env_id not in self._pending_update_failures:
                _LOGGER.warning(
                    "Unable to read cached image update status for Dockhand "
                    "environment %s; other integration data remains available",
                    env_id,
                )
                self._pending_update_failures.add(env_id)
            else:
                _LOGGER.debug(
                    "Cached image update status remains unavailable for Dockhand "
                    "environment %s",
                    env_id,
                )
            return None

        self._pending_update_failures.discard(env_id)
        return updates

    async def _fetch_container_stats(
        self, data: dict[str, Any], unique_key: str, container: dict
    ) -> None:
        """Fetch stats for one running container (run concurrently)."""
        container_id = container.get("id", "")
        env_id = container.get("environment_id")
        try:
            if not isinstance(env_id, int):
                raise DockhandApiError("Container is missing a numeric environment ID")
            stats = await self._limited(
                self.client.get_container_stats, container_id, env_id
            )
            data["stats"][unique_key] = stats
        except DockhandAuthError:
            raise
        except DockhandRateLimitError:
            raise
        except DockhandApiError as err:
            _LOGGER.debug(
                "Failed to fetch stats for a container: %s",
                err,
            )
