"""Data update coordinator for Dockhand."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DockhandApiClient, DockhandApiError, DockhandConnectionError
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL, CONF_SCAN_INTERVAL, CONF_ENVIRONMENTS

_LOGGER = logging.getLogger(__name__)


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
        self._selected_env_ids: list[int] | None = config_entry.options.get(
            CONF_ENVIRONMENTS
        ) or config_entry.data.get(CONF_ENVIRONMENTS)

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Dockhand."""
        try:
            data: dict[str, Any] = {
                "environments": {},
                "containers": {},
                "stats": {},
                "stacks": {},
            }

            # Fetch environments
            environments = await self.client.get_environments()
            env_map: dict[int, dict] = {}

            for env in environments:
                env_id = env.get("id")
                if env_id is None:
                    continue
                if self._selected_env_ids and env_id not in self._selected_env_ids:
                    continue
                env_map[env_id] = env
                data["environments"][env_id] = env

            # Fetch containers + stacks for all environments in parallel
            await asyncio.gather(
                *[
                    self._fetch_env_data(data, env_id, env_info)
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

            return data

        except DockhandConnectionError as err:
            raise UpdateFailed(f"Cannot connect to Dockhand: {err}") from err
        except DockhandApiError as err:
            raise UpdateFailed(f"Dockhand API error: {err}") from err

    async def _fetch_env_data(
        self, data: dict[str, Any], env_id: int, env_info: dict
    ) -> None:
        """Fetch containers and stacks for one environment (run concurrently)."""
        env_name = env_info.get("name", f"env-{env_id}")

        containers_result, stacks_result = await asyncio.gather(
            self.client.get_containers(env_id),
            self.client.get_stacks(env_id),
            return_exceptions=True,
        )

        if isinstance(containers_result, Exception):
            _LOGGER.warning(
                "Failed to fetch containers for environment %s: %s",
                env_name,
                containers_result,
            )
            containers_result = []

        if isinstance(stacks_result, Exception):
            _LOGGER.warning(
                "Failed to fetch stacks for environment %s: %s",
                env_name,
                stacks_result,
            )
            stacks_result = []

        if not stacks_result:
            _LOGGER.warning(
                "No stacks returned for environment %s (env_id=%s) — "
                "check if the /api/stacks endpoint supports the 'env' parameter",
                env_name,
                env_id,
            )
        else:
            _LOGGER.debug(
                "Stacks raw response for environment %s (%d stacks): %s",
                env_name,
                len(stacks_result),
                stacks_result,
            )

        for container in containers_result:
            container_id = container.get("id", "")
            unique_key = f"{env_id}_{container_id}"
            data["containers"][unique_key] = {
                **container,
                "environment_id": env_id,
                "environment_name": env_name,
            }

        for stack in stacks_result:
            stack_id = stack.get("id") or stack.get("name")
            if stack_id is None:
                _LOGGER.warning(
                    "Stack in environment %s has neither 'id' nor 'name' field: %s",
                    env_name,
                    stack,
                )
                continue
            stack_key = f"{env_id}_{stack_id}"
            data["stacks"][stack_key] = {
                **stack,
                "environment_id": env_id,
                "environment_name": env_name,
            }

    async def _fetch_container_stats(
        self, data: dict[str, Any], unique_key: str, container: dict
    ) -> None:
        """Fetch stats for one running container (run concurrently)."""
        container_id = container.get("id", "")
        env_id = container.get("environment_id")
        container_name = container.get("name", "unknown")
        try:
            stats = await self.client.get_container_stats(container_id, env_id)
            data["stats"][unique_key] = stats
        except DockhandApiError as err:
            _LOGGER.debug(
                "Failed to fetch stats for container %s: %s",
                container_name,
                err,
            )
