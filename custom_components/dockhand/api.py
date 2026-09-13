"""Dockhand REST API client."""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import re
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

import aiohttp

from .const import API_ACTION_TIMEOUT_SECONDS, API_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)


class DockhandApiError(Exception):
    """Base exception for Dockhand API errors."""


class DockhandAuthError(DockhandApiError):
    """Authentication error."""


class DockhandConnectionError(DockhandApiError):
    """Connection error."""


class DockhandRateLimitError(DockhandApiError):
    """The Dockhand API asked the client to retry later."""

    def __init__(self, retry_after: float) -> None:
        """Initialize a rate-limit error with a bounded retry delay."""
        super().__init__("Dockhand API rate limit exceeded")
        self.retry_after = retry_after


_CONTAINER_ID = re.compile(r"^[0-9a-fA-F]{12,64}$")
_CONTAINER_ACTIONS = frozenset({"start", "stop", "pause", "unpause", "restart"})
_MAX_SSE_EVENT_CHARS = 1024 * 1024


def _validated_container_id(container_id: str) -> str:
    """Return a validated Docker container ID safe for use in a URL path."""
    if not _CONTAINER_ID.fullmatch(container_id):
        raise DockhandApiError("Dockhand returned an invalid container ID")
    return container_id


def _validated_environment_id(env_id: int) -> int:
    """Return a validated Dockhand environment ID."""
    if not isinstance(env_id, int) or isinstance(env_id, bool) or env_id < 1:
        raise DockhandApiError("Invalid Dockhand environment ID")
    return env_id


async def _iter_sse_events(
    chunks: AsyncIterable[bytes],
) -> AsyncIterator[tuple[str, str]]:
    """Parse an SSE byte stream without assuming aiohttp chunk boundaries."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    text_buffer = ""
    event_name = ""
    data_lines: list[str] = []
    event_chars = 0

    def process_line(line: str) -> tuple[str, str] | None:
        """Process one complete SSE line and optionally finish an event."""
        nonlocal event_name, data_lines, event_chars

        if not line:
            if not event_name and not data_lines:
                return None
            event = (event_name or "message", "\n".join(data_lines))
            event_name = ""
            data_lines = []
            event_chars = 0
            return event

        if line.startswith(":"):
            return None

        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
            event_chars += len(value)
            if event_chars > _MAX_SSE_EVENT_CHARS:
                raise DockhandApiError("Dockhand SSE event exceeds the size limit")
        return None

    async for chunk in chunks:
        text_buffer += decoder.decode(chunk)
        while "\n" in text_buffer:
            line, text_buffer = text_buffer.split("\n", 1)
            if line.endswith("\r"):
                line = line[:-1]
            if event := process_line(line):
                yield event
        if len(text_buffer) > _MAX_SSE_EVENT_CHARS:
            raise DockhandApiError("Dockhand SSE line exceeds the size limit")

    text_buffer += decoder.decode(b"", final=True)
    if text_buffer:
        if text_buffer.endswith("\r"):
            text_buffer = text_buffer[:-1]
        if event := process_line(text_buffer):
            yield event
    if event_name or data_lines:
        yield event_name or "message", "\n".join(data_lines)


def _normalize_sse_event(
    event_name: str, data: str, container_id: str
) -> dict[str, Any] | None:
    """Normalize a relevant Dockhand SSE event for Home Assistant."""
    if event_name not in {"connected", "log", "end", "error"}:
        return None

    try:
        payload: Any = json.loads(data)
    except json.JSONDecodeError:
        payload = data

    def payload_text(*keys: str) -> str | None:
        if isinstance(payload, dict):
            for key in keys:
                value = payload.get(key)
                if isinstance(value, str):
                    return value
            return None
        return payload if isinstance(payload, str) else None

    if event_name == "connected":
        normalized: dict[str, Any] = {
            "event": "connected",
            "container_id": container_id,
        }
        if container_name := payload_text("containerName", "container_name", "name"):
            normalized["container_name"] = container_name
        return normalized

    if event_name == "log":
        text = payload_text("text")
        if text is None:
            return None
        normalized = {"event": "log", "text": text}
        if stream := payload_text("stream"):
            normalized["stream"] = stream
        if container_name := payload_text("containerName", "container_name"):
            normalized["container_name"] = container_name
        return normalized

    if event_name == "end":
        return {
            "event": "end",
            "reason": payload_text("reason", "message") or "Log stream ended",
        }

    return {
        "event": "error",
        "error": payload_text("error", "message") or "Dockhand log stream error",
    }


class DockhandApiClient:
    """Client for the Dockhand REST API."""

    def __init__(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
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
        self._auth_lock = asyncio.Lock()
        self._timeout = aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS)
        self._action_timeout = aiohttp.ClientTimeout(total=API_ACTION_TIMEOUT_SECONDS)
        self._stream_timeout = aiohttp.ClientTimeout(
            total=None,
            connect=API_TIMEOUT_SECONDS,
            sock_connect=API_TIMEOUT_SECONDS,
            sock_read=None,
        )

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
        request_timeout: aiohttp.ClientTimeout | None = None,
        headers: dict[str, str] | None = None,
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
                headers=headers,
                cookies=self._cookies,
                ssl=self._verify_ssl if self._verify_ssl else False,
                timeout=request_timeout or self._timeout,
            ) as resp:
                # Store session cookies
                for cookie_name, cookie in resp.cookies.items():
                    self._cookies[cookie_name] = cookie.value

                if resp.status == 401:
                    if retry_auth:
                        resp.release()
                        # Serialize reauthentication so concurrent stats requests do
                        # not all submit credentials after the same expired session.
                        async with self._auth_lock:
                            await self.authenticate()
                        return await self._request(
                            method=method,
                            path=path,
                            json_data=json_data,
                            params=params,
                            retry_auth=False,
                            request_timeout=request_timeout,
                            headers=headers,
                        )
                    raise DockhandAuthError("Authentication failed")

                if resp.status == 403:
                    raise DockhandAuthError("Permission denied")

                if resp.status == 429:
                    try:
                        retry_after = float(resp.headers.get("Retry-After", "60"))
                    except ValueError:
                        retry_after = 60
                    raise DockhandRateLimitError(min(max(retry_after, 1), 300))

                if resp.status >= 400:
                    # Do not echo an arbitrary server response into HA logs or UI;
                    # upstream error bodies can contain internal or secret data.
                    raise DockhandApiError(
                        f"Dockhand API request failed with HTTP {resp.status}"
                    )

                if (
                    resp.content_type == "application/json"
                    or resp.content_type.endswith("+json")
                ):
                    try:
                        return await resp.json(content_type=None)
                    except (ValueError, aiohttp.ClientPayloadError) as err:
                        raise DockhandApiError(
                            "Dockhand returned an invalid JSON response"
                        ) from err
                return await resp.text()

        except TimeoutError, aiohttp.ClientError:
            # aiohttp exception text can contain internal hosts or IP addresses.
            raise DockhandConnectionError("Cannot connect to Dockhand") from None

    async def check_auth_state(self) -> dict[str, Any]:
        """Check authentication state."""
        result = await self._request("GET", "/auth/session", retry_auth=False)
        if isinstance(result, dict):
            return result
        raise DockhandApiError(
            f"Unexpected authentication response type: {type(result).__name__}"
        )

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

        if not isinstance(result, dict):
            raise DockhandApiError(
                f"Unexpected login response type: {type(result).__name__}"
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
            "environments": len(envs),
            "auth_enabled": self._auth_enabled,
        }

    async def get_environments(self) -> list[dict[str, Any]]:
        """Get all configured environments."""
        result = await self._request("GET", "/environments")
        if isinstance(result, list):
            return result
        raise DockhandApiError(
            f"Unexpected environments response type: {type(result).__name__}"
        )

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
        raise DockhandApiError(
            f"Unexpected containers response type: {type(result).__name__}"
        )

    async def get_pending_container_updates(self, env_id: int) -> list[dict[str, Any]]:
        """Return Dockhand's persisted pending image updates for an environment."""
        env_id = _validated_environment_id(env_id)
        result = await self._request(
            "GET",
            "/containers/check-updates",
            params={"env": str(env_id)},
        )
        if not isinstance(result, dict):
            raise DockhandApiError("Dockhand returned invalid pending update data")

        response_env_id = result.get("environmentId")
        if (
            not isinstance(response_env_id, int)
            or isinstance(response_env_id, bool)
            or response_env_id != env_id
        ):
            raise DockhandApiError("Dockhand returned invalid pending update data")

        pending = result.get("pendingUpdates")
        if not isinstance(pending, list):
            raise DockhandApiError("Dockhand returned invalid pending update data")

        normalized: list[dict[str, Any]] = []
        for item in pending:
            if not isinstance(item, dict):
                raise DockhandApiError("Dockhand returned invalid pending update data")
            container_id = item.get("containerId")
            if not isinstance(container_id, str):
                raise DockhandApiError("Dockhand returned invalid pending update data")
            try:
                _validated_container_id(container_id)
            except DockhandApiError:
                raise DockhandApiError(
                    "Dockhand returned invalid pending update data"
                ) from None

            update: dict[str, Any] = {"container_id": container_id}
            for source, target in (
                ("containerName", "container_name"),
                ("currentImage", "current_image"),
                ("checkedAt", "checked_at"),
            ):
                value = item.get(source)
                if value is not None and not isinstance(value, str):
                    raise DockhandApiError(
                        "Dockhand returned invalid pending update data"
                    )
                if value:
                    update[target] = value
            normalized.append(update)
        return normalized

    async def check_container_updates(self, env_id: int) -> dict[str, Any]:
        """Ask Dockhand to perform a fresh registry update check."""
        env_id = _validated_environment_id(env_id)
        result = await self._request(
            "POST",
            "/containers/check-updates",
            params={"env": str(env_id)},
            request_timeout=self._action_timeout,
            headers={"Accept": "application/json"},
        )
        if not isinstance(result, dict):
            raise DockhandApiError("Dockhand returned invalid image update check data")

        total = result.get("total")
        updates_found = result.get("updatesFound")
        results = result.get("results")
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or not isinstance(updates_found, int)
            or isinstance(updates_found, bool)
            or updates_found < 0
            or updates_found > total
            or not isinstance(results, list)
            or any(not isinstance(item, dict) for item in results)
        ):
            raise DockhandApiError("Dockhand returned invalid image update check data")
        return result

    async def get_container_stats(
        self, container_id: str, env_id: int
    ) -> dict[str, Any]:
        """Get statistics for a container."""
        container_id = _validated_container_id(container_id)
        result = await self._request(
            "GET",
            f"/containers/{container_id}/stats",
            params={"env": str(env_id)},
        )
        if isinstance(result, dict):
            return result
        raise DockhandApiError(
            f"Unexpected container stats response type: {type(result).__name__}"
        )

    async def get_container_inspect(
        self, container_id: str, env_id: int
    ) -> dict[str, Any]:
        """Get detailed container information."""
        container_id = _validated_container_id(container_id)
        result = await self._request(
            "GET",
            f"/containers/{container_id}/inspect",
            params={"env": str(env_id)},
        )
        if isinstance(result, dict):
            return result
        raise DockhandApiError(
            f"Unexpected container inspect response type: {type(result).__name__}"
        )

    async def stream_container_logs(
        self,
        container_id: str,
        env_id: int,
        tail: int = 200,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream normalized container log events from Dockhand."""
        container_id = _validated_container_id(container_id)
        env_id = _validated_environment_id(env_id)
        if not isinstance(tail, int) or isinstance(tail, bool) or not 1 <= tail <= 5000:
            raise DockhandApiError("Invalid Dockhand log tail value")

        session = await self._get_session()
        url = f"{self._url}/api/containers/{container_id}/logs/stream"
        retry_auth = True

        while True:
            response: aiohttp.ClientResponse | None = None
            try:
                response = await session.get(
                    url,
                    params={"env": str(env_id), "tail": str(tail)},
                    headers={"Accept": "text/event-stream"},
                    cookies=self._cookies,
                    ssl=self._verify_ssl if self._verify_ssl else False,
                    timeout=self._stream_timeout,
                )

                for cookie_name, cookie in response.cookies.items():
                    self._cookies[cookie_name] = cookie.value

                if response.status == 401:
                    response.close()
                    response = None
                    if retry_auth:
                        retry_auth = False
                        async with self._auth_lock:
                            await self.authenticate()
                        continue
                    raise DockhandAuthError("Authentication failed")

                if response.status == 403:
                    raise DockhandAuthError("Permission denied")
                if response.status >= 400:
                    raise DockhandApiError(
                        f"Dockhand log stream failed with HTTP {response.status}"
                    )

                async for event_name, data in _iter_sse_events(
                    response.content.iter_any()
                ):
                    if normalized := _normalize_sse_event(
                        event_name, data, container_id
                    ):
                        yield normalized
                return
            except TimeoutError, aiohttp.ClientError:
                raise DockhandConnectionError("Cannot connect to Dockhand") from None
            finally:
                if response is not None:
                    response.close()

    async def container_action(
        self, container_id: str, action: str, env_id: int
    ) -> Any:
        """Perform a container lifecycle action."""
        container_id = _validated_container_id(container_id)
        if action not in _CONTAINER_ACTIONS:
            raise DockhandApiError(f"Unsupported container action: {action}")
        return await self._request(
            "POST",
            f"/containers/{container_id}/{action}",
            params={"env": str(env_id)},
        )

    async def update_container_image(
        self, container_id: str, env_id: int, image: str, name: str
    ) -> Any:
        """Pull the latest image and recreate the container."""
        container_id = _validated_container_id(container_id)
        if not image or not name:
            raise DockhandApiError(
                "Container image and name are required for an image update"
            )
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
            request_timeout=self._action_timeout,
        )

    async def get_stacks(self, env_id: int) -> list[dict[str, Any]]:
        """Get stacks for an environment."""
        result = await self._request(
            "GET",
            "/stacks",
            params={"env": str(env_id)},
        )
        _LOGGER.debug(
            "get_stacks returned %s for environment %s",
            type(result).__name__,
            env_id,
        )
        if isinstance(result, list):
            return result
        raise DockhandApiError(
            f"Unexpected stacks response type: {type(result).__name__}"
        )
