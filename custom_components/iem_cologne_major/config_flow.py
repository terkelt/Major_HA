"""Config flow for IEM Cologne Major."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_INCLUDE_HLTV_SIGNAL,
    CONF_INCLUDE_FINISHED_MATCHES,
    CONF_LIQUIPEDIA_PAGE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_INCLUDE_HLTV_SIGNAL,
    DEFAULT_INCLUDE_FINISHED_MATCHES,
    DEFAULT_LIQUIPEDIA_PAGE,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LIQUIPEDIA_PAGE, default=DEFAULT_LIQUIPEDIA_PAGE): str,
        vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL_MINUTES): vol.All(
            int, vol.Range(min=1, max=30)
        ),
        vol.Required(
            CONF_INCLUDE_FINISHED_MATCHES,
            default=DEFAULT_INCLUDE_FINISHED_MATCHES,
        ): bool,
        vol.Required(
            CONF_INCLUDE_HLTV_SIGNAL,
            default=DEFAULT_INCLUDE_HLTV_SIGNAL,
        ): bool,
    }
)


class IEMCologneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for IEM Cologne Major."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

        unique_id = user_input[CONF_LIQUIPEDIA_PAGE].lower().replace(" ", "_")
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(title="IEM Cologne Major", data=user_input)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return IEMCologneOptionsFlow(config_entry)


class IEMCologneOptionsFlow(config_entries.OptionsFlow):
    """Options flow for IEM Cologne Major."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        merged = {**self.config_entry.data, **self.config_entry.options}

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LIQUIPEDIA_PAGE,
                    default=merged.get(CONF_LIQUIPEDIA_PAGE, DEFAULT_LIQUIPEDIA_PAGE),
                ): str,
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=merged.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
                ): vol.All(int, vol.Range(min=1, max=30)),
                vol.Required(
                    CONF_INCLUDE_FINISHED_MATCHES,
                    default=merged.get(
                        CONF_INCLUDE_FINISHED_MATCHES,
                        DEFAULT_INCLUDE_FINISHED_MATCHES,
                    ),
                ): bool,
                vol.Required(
                    CONF_INCLUDE_HLTV_SIGNAL,
                    default=merged.get(
                        CONF_INCLUDE_HLTV_SIGNAL,
                        DEFAULT_INCLUDE_HLTV_SIGNAL,
                    ),
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
