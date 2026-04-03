"""Dockhand REST API client."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class DockhandApiError(Exception):
    """Base exception for Dockhand API errors."""


class DockhandAuthError(DockhandApiError):
    """Authentication error."""


class DockhandConnectionError(DockhandApiError):
    """Connection error."""


class DockhandApiClient:
    """Client for the Dockhand REST API."""

    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = False,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client."""
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._session = session
        self._cookies: dict[str, str] = {}
        self._auth_enabled: bool | None = None
        self._owns_session = session is None

    @property
    def base_url(self) -> str:
        """Return the base URL."""
        return self._url

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._verify_ssl)
            self._session = aiohttp.ClientSession(connector=connector)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the session."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
        params: dict | None = None,
        retry_auth: bool = True,
    ) -> Any:
        """Make an API request."""
        session = await self._get_session()
        url = f"{self._url}/api{path}"

        try:
            async with session.request(
                method,
                url,
                json=json_data,
                params=params,
                cookies=self._cookies,
                ssl=self._verify_ssl if self._verify_ssl else False,
            ) as resp:
                # Store session cookies
                for cookie_name, cookie in resp.cookies.items():
                    self._cookies[cookie_name] = cookie.value

                if resp.status == 401 and retry_auth:
                    # Try to re-authenticate
                    await self.authenticate()
                    return await self._request(
                        method, path, json_data, params, retry_auth=False
                    )

                if resp.status == 403:
                    raise DockhandAuthError("Permission denied")

                if resp.status >= 400:
                    try:
                        error_data = await resp.json()
                        error_msg = error_data.get("error", f"HTTP {resp.status}")
                    except Exception:
                        error_msg = f"HTTP {resp.status}"
                    raise DockhandApiError(error_msg)

                if resp.content_type == "application/json":
                    return await resp.json()
                return await resp.text()

        except aiohttp.ClientError as err:
            raise DockhandConnectionError(
                f"Cannot connect to Dockhand at {self._url}: {err}"
            ) from err

    async def check_auth_state(self) -> dict[str, Any]:
        """Check authentication state."""
        return await self._request("GET", "/auth/session", retry_auth=False)

    async def authenticate(self) -> bool:
        """Authenticate with the Dockhand API."""
        # First check if auth is even enabled
        auth_state = await self.check_auth_state()
        self._auth_enabled = auth_state.get("authEnabled", False)

        if not self._auth_enabled:
            _LOGGER.debug("Dockhand authentication is disabled, no login needed")
            return True

        if auth_state.get("authenticated", False):
            _LOGGER.debug("Already authenticated")
            return True

        if not self._username or not self._password:
            raise DockhandAuthError(
                "Authentication is enabled but no credentials provided"
            )

        result = await self._request(
            "POST",
            "/auth/login",
            json_data={
                "username": self._username,
                "password": self._password,
                "provider": "local",
            },
            retry_auth=False,
        )

        if not result.get("success"):
            raise DockhandAuthError("Authentication failed")

        _LOGGER.debug("Successfully authenticated with Dockhand")
        return True

    async def test_connection(self) -> dict[str, Any]:
        """Test the connection and return server info."""
        await self.authenticate()
        envs = await self.get_environments()
        return {
            "environments": len(envs) if isinstance(envs, list) else 0,
            "auth_enabled": self._auth_enabled,
        }

    async def get_environments(self) -> list[dict[str, Any]]:
        """Get all configured environments."""
        result = await self._request("GET", "/environments")
        if isinstance(result, list):
            return result
        return []

    async def get_containers(
        self, env_id: int, show_all: bool = True
    ) -> list[dict[str, Any]]:
        """Get containers for an environment."""
        result = await self._request(
            "GET",
            "/containers",
            params={"env": str(env_id), "all": str(show_all).lower()},
        )
        if isinstance(result, list):
            return result
        return []

    async def get_container_stats(
        self, container_id: str, env_id: int
    ) -> dict[str, Any]:
        """Get statistics for a container."""
        return await self._request(
            "GET",
            f"/containers/{container_id}/stats",
            params={"env": str(env_id)},
        )

    async def get_container_inspect(
        self, container_id: str, env_id: int
    ) -> dict[str, Any]:
        """Get detailed container information."""
        return await self._request(
            "GET",
            f"/containers/{container_id}",
            params={"env": str(env_id)},
        )

    async def container_action(
        self, container_id: str, action: str, env_id: int
    ) -> dict[str, Any]:
        """Perform a container lifecycle action (start, stop, restart, pause, unpause)."""
        return await self._request(
            "POST",
            f"/containers/{container_id}/{action}",
            params={"env": str(env_id)},
        )

    async def update_container_image(
        self, container_id: str, env_id: int, image: str, name: str
    ) -> dict[str, Any]:
        """Pull the latest image and recreate the container."""
        return await self._request(
            "POST",
            f"/containers/{container_id}/update",
            params={"env": str(env_id)},
            json_data={
                "repullImage": True,
                "startAfterUpdate": True,
                "image": image,
                "name": name,
            },
        )

    async def get_stacks(self, env_id: int) -> list[dict[str, Any]]:
        """Get stacks for an environment."""
        result = await self._request(
            "GET",
            "/stacks",
            params={"env": str(env_id)},
        )
        _LOGGER.debug("get_stacks raw result for env %s: type=%s value=%s", env_id, type(result).__name__, result)
        if isinstance(result, list):
            return result
        _LOGGER.warning(
            "get_stacks for env %s returned unexpected type %s: %s",
            env_id,
            type(result).__name__,
            result,
        )
        return []

