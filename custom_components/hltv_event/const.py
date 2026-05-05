"""Constants for the HLTV Event integration."""

from __future__ import annotations

DOMAIN = "hltv_event"
NAME = "HLTV Event"

CONF_EVENT_URL = "event_url"
CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_UPDATE_INTERVAL_MINUTES = 5

DATA_COORDINATOR = "coordinator"

# Cache durations (seconds)
CACHE_EVENT_SECONDS = 300      # 5 min – event overview
CACHE_RESULTS_SECONDS = 180    # 3 min – completed matches
CACHE_MATCHES_SECONDS = 120    # 2 min – upcoming/live
CACHE_ROSTER_SECONDS = 86400   # 24 h  – team rosters

HLTV_BASE = "https://www.hltv.org"

HLTV_USER_AGENT = (
    "HLTVEventHA/1.0 (https://github.com/terkelt/Major_HA/issues; community HA integration)"
)
