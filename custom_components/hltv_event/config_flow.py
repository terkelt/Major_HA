"""Config flow for HLTV Event integration."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_EVENT_URL, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES, DOMAIN

_HLTV_EVENT_RE = re.compile(r"https://www\.hltv\.org/events/(\d+)/[\w-]+")


class HLTVEventConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HLTV Event."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_EVENT_URL].strip()
            if not _HLTV_EVENT_RE.match(url):
                errors[CONF_EVENT_URL] = "invalid_url"
            else:
                m = _HLTV_EVENT_RE.match(url)
                event_id = m.group(1)
                await self.async_set_unique_id(f"hltv_event_{event_id}")
                self._abort_if_unique_id_configured()
                title = url.split("/")[-1].replace("-", " ").title()
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EVENT_URL,
                        description={"suggested_value": "https://www.hltv.org/events/9161/nodwin-clutch-series-8-closed-qualifier"},
                    ): str,
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=DEFAULT_UPDATE_INTERVAL_MINUTES,
                    ): vol.All(vol.Coerce(int), vol.Range(min=2, max=60)),
                }
            ),
            errors=errors,
        )
