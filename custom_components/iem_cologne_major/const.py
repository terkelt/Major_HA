"""Constants for the IEM Cologne Major integration."""

from __future__ import annotations

DOMAIN = "iem_cologne_major"
NAME = "IEM Cologne Major"

CONF_LIQUIPEDIA_PAGE = "liquipedia_page"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_INCLUDE_FINISHED_MATCHES = "include_finished_matches"
CONF_INCLUDE_HLTV_SIGNAL = "include_hltv_signal"

DEFAULT_LIQUIPEDIA_PAGE = "Intel_Extreme_Masters/2026/Cologne"
DEFAULT_UPDATE_INTERVAL_MINUTES = 5
DEFAULT_INCLUDE_FINISHED_MATCHES = True
DEFAULT_INCLUDE_HLTV_SIGNAL = True

DATA_COORDINATOR = "coordinator"

LIQUIPEDIA_API_URL = "https://liquipedia.net/counterstrike/api.php"
HLTV_EVENT_URLS = [
	"https://www.hltv.org/events/9028/iem-cologne-major-2026-stage-1",
	"https://www.hltv.org/events/9029/iem-cologne-major-2026-stage-2",
	"https://www.hltv.org/events/8301/iem-cologne-major-2026",
]
