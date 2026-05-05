"""DataUpdateCoordinator for IEM Cologne Major."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IEMCologneApiClient
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

_LOGGER = logging.getLogger(__name__)


class IEMCologneDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to fetch and cache IEM Cologne data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.config_entry = entry
        options = entry.options
        data = entry.data

        self._liquipedia_page = options.get(
            CONF_LIQUIPEDIA_PAGE,
            data.get(CONF_LIQUIPEDIA_PAGE, DEFAULT_LIQUIPEDIA_PAGE),
        )
        self._include_finished_matches = options.get(
            CONF_INCLUDE_FINISHED_MATCHES,
            data.get(CONF_INCLUDE_FINISHED_MATCHES, DEFAULT_INCLUDE_FINISHED_MATCHES),
        )
        self._include_hltv_signal = options.get(
            CONF_INCLUDE_HLTV_SIGNAL,
            data.get(CONF_INCLUDE_HLTV_SIGNAL, DEFAULT_INCLUDE_HLTV_SIGNAL),
        )
        update_interval_minutes = int(
            options.get(
                CONF_UPDATE_INTERVAL,
                data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
            )
        )

        self.api = IEMCologneApiClient(
            session=async_get_clientsession(hass),
            liquipedia_page=self._liquipedia_page,
            include_finished_matches=self._include_finished_matches,
            include_hltv_signal=self._include_hltv_signal,
        )

        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=update_interval_minutes),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.api.async_fetch_data()
        except Exception as err:
            if self.data:
                _LOGGER.warning("Using last known IEM Cologne data after update failure: %s", err)
                return self.data
            raise UpdateFailed(f"Failed to update IEM Cologne data: {err}") from err

        # Schedule one background roster fetch 32 s later (respects Liquipedia
        # 1-req/30s limit; the main page fetch just ran, so we wait a cycle).
        self.hass.loop.call_later(32, lambda: self.hass.async_create_task(
            self.api.async_fetch_next_roster()
        ))
        return data
