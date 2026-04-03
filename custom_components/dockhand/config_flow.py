"""Config flow for Dockhand integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
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

from .api import (
    DockhandApiClient,
    DockhandAuthError,
    DockhandConnectionError,
    DockhandApiError,
)
from .const import (
    DOMAIN,
    CONF_URL,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_VERIFY_SSL,
    CONF_ENVIRONMENTS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
)

_LOGGER = logging.getLogger(__name__)

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
            url = user_input[CONF_URL].rstrip("/")
            username = user_input.get(CONF_USERNAME, "")
            password = user_input.get(CONF_PASSWORD, "")
            verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

            # Check for duplicate entries
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()

            client = DockhandApiClient(
                url=url,
                username=username if username else None,
                password=password if password else None,
                verify_ssl=verify_ssl,
            )

            try:
                info = await client.test_connection()
                self._environments = await client.get_environments()

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


class DockhandOptionsFlow(OptionsFlow):
    """Handle Dockhand options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

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
                }
            ),
        )
