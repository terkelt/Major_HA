"""DataUpdateCoordinator for HLTV Event integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import HLTVEventClient
from .const import CONF_EVENT_URL, CONF_UPDATE_INTERVAL, DATA_COORDINATOR, DEFAULT_UPDATE_INTERVAL_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)


class HLTVEventCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to fetch and cache HLTV event data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.config_entry = entry
        event_url: str = entry.data[CONF_EVENT_URL]
        update_interval_minutes = int(
            entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)
        )
        self.client = HLTVEventClient(
            session=async_get_clientsession(hass),
            event_url=event_url,
        )
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{self.client.event_id}",
            update_interval=timedelta(minutes=update_interval_minutes),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.async_fetch_data()
        except Exception as err:
            if self.data:
                _LOGGER.warning("Using cached HLTV data after update failure: %s", err)
                return self.data
            raise UpdateFailed(f"Failed to fetch HLTV event data: {err}") from err
