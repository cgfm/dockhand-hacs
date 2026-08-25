"""Config flow for Dockhand integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from yarl import URL

from .api import (
    DockhandApiClient,
    DockhandApiError,
    DockhandAuthError,
    DockhandConnectionError,
)
from .const import (
    CONF_ENVIRONMENTS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .coordinator import DockhandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _normalize_url(value: str) -> str:
    """Normalize a Dockhand URL for storage and duplicate detection."""
    url = URL(value.strip())
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.user is not None
        or url.password is not None
        or url.query_string
        or url.fragment
    ):
        raise ValueError("A plain HTTP(S) URL with a host is required")
    if url.host:
        url = url.with_host(url.host.lower())
    if (url.scheme == "http" and url.port == 80) or (
        url.scheme == "https" and url.port == 443
    ):
        url = url.with_port(None)
    return str(url).rstrip("/")


def _validated_environments(
    environments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate environment records before presenting or storing their IDs."""
    validated: list[dict[str, Any]] = []
    seen: set[int] = set()
    for environment in environments:
        environment_id = (
            environment.get("id") if isinstance(environment, dict) else None
        )
        if (
            not isinstance(environment_id, int)
            or isinstance(environment_id, bool)
            or environment_id in seen
        ):
            raise DockhandApiError("Dockhand returned invalid environment data")
        seen.add(environment_id)
        validated.append(environment)
    return validated


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Optional(CONF_USERNAME, default=""): TextSelector(
            TextSelectorConfig(autocomplete="username")
        ),
        vol.Optional(CONF_PASSWORD, default=""): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): BooleanSelector(),
    }
)


class DockhandConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dockhand."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize."""
        self._data: dict[str, Any] = {}
        self._environments: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                url = _normalize_url(user_input[CONF_URL])
            except TypeError, ValueError:
                errors["base"] = "invalid_url"
                return self.async_show_form(
                    step_id="user",
                    data_schema=STEP_USER_DATA_SCHEMA,
                    errors=errors,
                )
            username = user_input.get(CONF_USERNAME, "")
            password = user_input.get(CONF_PASSWORD, "")
            verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

            for entry in self._async_current_entries():
                try:
                    existing_url = _normalize_url(entry.data[CONF_URL])
                except KeyError, TypeError, ValueError:
                    continue
                if existing_url == url:
                    return self.async_abort(reason="already_configured")

            client = DockhandApiClient(
                url=url,
                username=username if username else None,
                password=password if password else None,
                verify_ssl=verify_ssl,
            )

            try:
                await client.authenticate()
                self._environments = _validated_environments(
                    await client.get_environments()
                )

                self._data = {
                    CONF_URL: url,
                    CONF_USERNAME: username,
                    CONF_PASSWORD: password,
                    CONF_VERIFY_SSL: verify_ssl,
                }

                if self._environments:
                    return await self.async_step_environments()

                return self.async_create_entry(
                    title=f"Dockhand ({url})",
                    data=self._data,
                )

            except DockhandConnectionError:
                errors["base"] = "cannot_connect"
            except DockhandAuthError:
                errors["base"] = "invalid_auth"
            except DockhandApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            finally:
                await client.close()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start a reauthentication flow."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement Dockhand credentials."""
        reauth_entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            updated_data = {
                **reauth_entry.data,
                CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
                CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                CONF_VERIFY_SSL: user_input.get(
                    CONF_VERIFY_SSL,
                    reauth_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ),
            }
            client = DockhandApiClient(
                url=updated_data[CONF_URL],
                username=updated_data[CONF_USERNAME] or None,
                password=updated_data[CONF_PASSWORD] or None,
                verify_ssl=updated_data[CONF_VERIFY_SSL],
            )
            try:
                await client.test_connection()
            except DockhandConnectionError:
                errors["base"] = "cannot_connect"
            except DockhandAuthError:
                errors["base"] = "invalid_auth"
            except DockhandApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception(
                    "Unexpected exception during Dockhand reauthentication"
                )
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates=updated_data,
                )
            finally:
                await client.close()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_USERNAME,
                        default=reauth_entry.data.get(CONF_USERNAME, ""),
                    ): TextSelector(TextSelectorConfig(autocomplete="username")),
                    vol.Optional(CONF_PASSWORD, default=""): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    ),
                    vol.Optional(
                        CONF_VERIFY_SSL,
                        default=reauth_entry.data.get(
                            CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL
                        ),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update connection details and reload the existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                url = _normalize_url(user_input[CONF_URL])
            except TypeError, ValueError:
                errors["base"] = "invalid_url"
            else:
                for other_entry in self._async_current_entries():
                    if other_entry.entry_id == entry.entry_id:
                        continue
                    try:
                        other_url = _normalize_url(other_entry.data[CONF_URL])
                    except KeyError, TypeError, ValueError:
                        continue
                    if other_url == url:
                        return self.async_abort(reason="already_configured")

                username = user_input.get(CONF_USERNAME, "")
                password = user_input.get(CONF_PASSWORD, "")
                if not password and username == entry.data.get(CONF_USERNAME, ""):
                    password = entry.data.get(CONF_PASSWORD, "")
                verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                updated_data = {
                    **entry.data,
                    CONF_URL: url,
                    CONF_USERNAME: username,
                    CONF_PASSWORD: password,
                    CONF_VERIFY_SSL: verify_ssl,
                }
                client = DockhandApiClient(
                    url=url,
                    username=username or None,
                    password=password or None,
                    verify_ssl=verify_ssl,
                )
                try:
                    await client.test_connection()
                except DockhandConnectionError:
                    errors["base"] = "cannot_connect"
                except DockhandAuthError:
                    errors["base"] = "invalid_auth"
                except DockhandApiError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception(
                        "Unexpected exception while reconfiguring Dockhand"
                    )
                    errors["base"] = "unknown"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates=updated_data,
                    )
                finally:
                    await client.close()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=entry.data[CONF_URL]): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    ),
                    vol.Optional(
                        CONF_USERNAME,
                        default=entry.data.get(CONF_USERNAME, ""),
                    ): TextSelector(TextSelectorConfig(autocomplete="username")),
                    vol.Optional(CONF_PASSWORD, default=""): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    ),
                    vol.Optional(
                        CONF_VERIFY_SSL,
                        default=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_environments(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select environments to monitor."""
        if user_input is not None:
            selected = user_input.get(CONF_ENVIRONMENTS, [])
            self._data[CONF_ENVIRONMENTS] = [int(e) for e in selected]
            url = self._data[CONF_URL]
            return self.async_create_entry(
                title=f"Dockhand ({url})",
                data=self._data,
            )

        env_options = {
            str(env["id"]): env.get("name", f"Environment {env['id']}")
            for env in self._environments
            if "id" in env
        }

        return self.async_show_form(
            step_id="environments",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENVIRONMENTS,
                        default=list(env_options.keys()),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=k, label=v)
                                for k, v in env_options.items()
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            description_placeholders={"env_count": str(len(env_options))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DockhandOptionsFlow:
        """Get the options flow handler."""
        return DockhandOptionsFlow()


class DockhandOptionsFlow(OptionsFlowWithReload):
    """Handle Dockhand options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            options = {
                CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                CONF_ENVIRONMENTS: [
                    int(environment_id)
                    for environment_id in user_input.get(CONF_ENVIRONMENTS, [])
                ],
            }
            return self.async_create_entry(title="", data=options)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        current_environments: list[int] | None = self.config_entry.options.get(
            CONF_ENVIRONMENTS,
            self.config_entry.data.get(CONF_ENVIRONMENTS),
        )
        coordinator: DockhandDataUpdateCoordinator = self.config_entry.runtime_data
        errors: dict[str, str] = {}
        try:
            environments = _validated_environments(
                await coordinator.client.get_environments()
            )
        except DockhandApiError:
            errors["base"] = "cannot_connect"
            environments = list(coordinator.data.get("environments", {}).values())
        env_options = {
            str(environment["id"]): environment.get(
                "name", f"Environment {environment['id']}"
            )
            for environment in environments
            if "id" in environment
        }
        if current_environments is None:
            current_environments = [
                int(environment_id) for environment_id in env_options
            ]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=current_interval,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10,
                            max=300,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_ENVIRONMENTS,
                        default=[
                            str(environment_id)
                            for environment_id in current_environments
                        ],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=value, label=label)
                                for value, label in env_options.items()
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )
