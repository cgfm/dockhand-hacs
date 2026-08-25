"""Tests for Dockhand config, reauth, reconfigure, and options flows."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dockhand.config_flow import _normalize_url
from custom_components.dockhand.const import (
    CONF_ENVIRONMENTS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
)

ENTRY_DATA = {
    CONF_URL: "https://dockhand.example",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "secret",
    CONF_VERIFY_SSL: True,
}


def _entry(hass: HomeAssistant, entry_id: str = "entry-a") -> MockConfigEntry:
    """Add a Dockhand config entry."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id=entry_id, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    return entry


def test_url_normalization_handles_defaults_paths_and_ipv6() -> None:
    """Duplicate detection canonicalizes safe URL variants."""
    assert _normalize_url(" HTTPS://DOCKHAND.EXAMPLE:443/ ") == (
        "https://dockhand.example"
    )
    assert _normalize_url("http://[2001:db8::1]:3000/proxy/") == (
        "http://[2001:db8::1]:3000/proxy"
    )


async def test_user_flow_selects_environments(hass: HomeAssistant) -> None:
    """A successful flow validates credentials before persisting selection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM

    with (
        patch(
            "custom_components.dockhand.config_flow.DockhandApiClient.authenticate",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.dockhand.config_flow.DockhandApiClient.get_environments",
            new=AsyncMock(
                return_value=[{"id": 1, "name": "Local"}, {"id": 2, "name": "Remote"}]
            ),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_URL: "https://DOCKHAND.example:443/",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "secret",
                CONF_VERIFY_SSL: True,
            },
        )
        assert result["step_id"] == "environments"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ENVIRONMENTS: ["2"]}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == "https://dockhand.example"
    assert result["data"][CONF_ENVIRONMENTS] == [2]


async def test_invalid_or_credentialed_url_is_rejected(hass: HomeAssistant) -> None:
    """Unsafe URL components never enter config entry data or logs."""
    for url in (
        "ftp://dockhand.example",
        "https://user:secret@dockhand.example",
        "https://dockhand.example?token=secret",
        "not a URL",
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: url}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_url"}


async def test_normalized_duplicate_is_aborted(hass: HomeAssistant) -> None:
    """Equivalent URLs cannot create a second config entry."""
    _entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "https://DOCKHAND.example:443/"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_malformed_environment_response_is_not_persisted(
    hass: HomeAssistant,
) -> None:
    """A partial environment response cannot become a destructive selection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with (
        patch(
            "custom_components.dockhand.config_flow.DockhandApiClient.authenticate",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.dockhand.config_flow.DockhandApiClient.get_environments",
            new=AsyncMock(return_value=[{"id": "not-an-integer"}]),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: "https://dockhand.example"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_invalid_historical_url_does_not_break_new_flow(
    hass: HomeAssistant,
) -> None:
    """Bad 1.1 entry data is ignored only for duplicate comparison."""
    broken = MockConfigEntry(domain=DOMAIN, data={CONF_URL: "invalid"})
    broken.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with (
        patch(
            "custom_components.dockhand.config_flow.DockhandApiClient.authenticate",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.dockhand.config_flow.DockhandApiClient.get_environments",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "https://dockhand.example"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth_updates_credentials_and_reloads(hass: HomeAssistant) -> None:
    """A reauth flow validates and replaces only authentication settings."""
    entry = _entry(hass)
    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(return_value=True)
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=dict(entry.data),
        )
        assert result["step_id"] == "reauth_confirm"
        with patch(
            "custom_components.dockhand.config_flow.DockhandApiClient.test_connection",
            new=AsyncMock(return_value={"environments": 1}),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_USERNAME: "new-user",
                    CONF_PASSWORD: "new-secret",
                    CONF_VERIFY_SSL: True,
                },
            )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_USERNAME] == "new-user"
    assert entry.data[CONF_PASSWORD] == "new-secret"


async def test_reconfigure_keeps_password_when_left_blank(
    hass: HomeAssistant,
) -> None:
    """Changing the URL does not accidentally erase stored credentials."""
    entry = _entry(hass)
    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(return_value=True)
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
            data=dict(entry.data),
        )
        with patch(
            "custom_components.dockhand.config_flow.DockhandApiClient.test_connection",
            new=AsyncMock(return_value={"environments": 1}),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_URL: "https://new-dockhand.example/",
                    CONF_USERNAME: "user",
                    CONF_PASSWORD: "",
                    CONF_VERIFY_SSL: True,
                },
            )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_URL] == "https://new-dockhand.example"
    assert entry.data[CONF_PASSWORD] == "secret"


async def test_options_flow_persists_reloadable_options(hass: HomeAssistant) -> None:
    """Options use HA's reload lifecycle and integer environment IDs."""
    entry = _entry(hass)
    entry.runtime_data = SimpleNamespace(
        client=SimpleNamespace(
            get_environments=AsyncMock(return_value=[{"id": 1, "name": "Local"}])
        ),
        data={"environments": {1: {"id": 1, "name": "Local"}}},
    )
    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(return_value=True)
    ) as reload_entry:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL: 45, CONF_ENVIRONMENTS: ["1"]},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 45, CONF_ENVIRONMENTS: [1]}
    reload_entry.assert_awaited_once_with(entry.entry_id)
