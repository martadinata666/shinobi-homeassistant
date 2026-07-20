"""Config and options flow for Shinobi NVR."""

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
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ShinobiApiError, ShinobiAuthError, ShinobiClient
from .const import (
    CONF_API_KEY,
    CONF_GROUP_KEY,
    CONF_HOST,
    CONF_MOTION_TIMEOUT,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_GROUP_KEY): str,
        vol.Optional(CONF_SSL, default=DEFAULT_SSL): bool,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
    }
)


async def _validate(hass, data: dict[str, Any]) -> str:
    """Validate credentials, returning a friendly server label."""
    session = async_get_clientsession(hass)
    client = ShinobiClient(
        session=session,
        host=data[CONF_HOST],
        port=data[CONF_PORT],
        api_key=data[CONF_API_KEY],
        group_key=data[CONF_GROUP_KEY],
        use_ssl=data.get(CONF_SSL, False),
        verify_ssl=data.get(CONF_VERIFY_SSL, True),
    )
    monitors = await client.async_validate()
    return f"Shinobi ({data[CONF_HOST]}:{data[CONF_PORT]}, {len(monitors)} monitors)"


class ShinobiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id = (
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                f":{user_input[CONF_GROUP_KEY]}"
            )
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            try:
                title = await _validate(self.hass, user_input)
            except ShinobiAuthError:
                errors["base"] = "invalid_auth"
            except ShinobiApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating Shinobi")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ShinobiOptionsFlow:
        return ShinobiOptionsFlow(config_entry)


class ShinobiOptionsFlow(OptionsFlow):
    """Adjust poll interval and motion timeout after setup."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=5, max=300)),
                vol.Optional(
                    CONF_MOTION_TIMEOUT,
                    default=opts.get(CONF_MOTION_TIMEOUT, DEFAULT_MOTION_TIMEOUT),
                ): vol.All(int, vol.Range(min=5, max=600)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
