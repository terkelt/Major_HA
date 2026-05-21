"""The IEM Cologne Major integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import IEMCologneDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Roster fetch interval (one team per call, Liquipedia 1 req/30s)
_ROSTER_INTERVAL = timedelta(minutes=5)
# Delay before first roster fetch after startup
_ROSTER_STARTUP_DELAY_S = 60


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up IEM Cologne Major from a config entry."""
    coordinator = IEMCologneDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # --- Independent roster fetch timer ---------------------------------
    # Runs every 5 minutes regardless of main coordinator success/failure.
    # Fetches one team roster per call (respects Liquipedia 1 req/30s).
    async def _do_roster_fetch(_now=None) -> None:
        _LOGGER.debug("[IEM Cologne] Scheduled roster fetch triggered")
        await coordinator.api.async_fetch_next_roster()

    entry.async_on_unload(
        async_track_time_interval(hass, _do_roster_fetch, _ROSTER_INTERVAL)
    )

    # Trigger first roster fetch shortly after startup so we don't wait 5 min
    entry.async_on_unload(
        async_call_later(hass, _ROSTER_STARTUP_DELAY_S, _do_roster_fetch)
    )

    _LOGGER.info(
        "[IEM Cologne] Roster fetch scheduled: first in %ds, then every %s",
        _ROSTER_STARTUP_DELAY_S,
        _ROSTER_INTERVAL,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
