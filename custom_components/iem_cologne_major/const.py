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

# Authoritative tournament data for IEM Cologne Major 2026
# Verified from HLTV/Liquipedia on 2026-05-05
TOURNAMENT_BASE: dict = {
    "name": "IEM Cologne Major 2026",
    "organizer": "ESL",
    "location": "Cologne, Germany",
    "venue": "LANXESS Arena",
    "prize_pool": "1,250,000 USD",
    "map_pool": ["Dust2", "Mirage", "Inferno", "Nuke", "Overpass", "Ancient", "Anubis"],
    "stages": {
        "stage_1": {
            "name": "Challengers Stage",
            "short": "Stage 1",
            "start": "2026-06-02",
            "end": "2026-06-05",
            "format": "Swiss Bo1 / Elim. Bo3",
            "prize": "8 Stage-2-Spots",
            "participants": [
                "GamerLegion", "B8", "HEROIC", "BetBoom", "BIG", "M80",
                "TYLOO", "MIBR", "SINNERS", "NRG", "Gaimin Gladiators",
                "Liquid", "Lynn Vision", "THUNDER dOWNUNDER", "FlyQuest", "Sharks",
            ],
        },
        "stage_2": {
            "name": "Legends Stage",
            "short": "Stage 2",
            "start": "2026-06-06",
            "end": "2026-06-09",
            "format": "Swiss Bo1 / Elim. Bo3",
            "prize": "8 Stage-3-Spots",
            "direct_invites": [
                "FUT", "Spirit", "Astralis", "G2", "Legacy", "Monte", "9z", "paiN",
            ],
            "qualifier_slots": 8,
        },
        "stage_3": {
            "name": "Champions Stage",
            "short": "Stage 3",
            "start": "2026-06-11",
            "end": "2026-06-15",
            "format": "Swiss Bo3 (keine Bo1-Matches)",
            "prize": "8 Playoff-Spots",
            "direct_invites": [
                "Vitality", "Natus Vincere", "Falcons", "The MongolZ",
                "PARIVISION", "Aurora", "FURIA", "MOUZ",
            ],
            "qualifier_slots": 8,
        },
        "playoffs": {
            "name": "Playoffs",
            "short": "Playoffs",
            "start": "2026-06-18",
            "end": "2026-06-21",
            "format": "Single Elim. Bo3 / Grand Final Bo5",
            "prize": "1. Platz: 500.000 USD",
            "prize_distribution": {
                "1st": "500.000 USD",
                "2nd": "170.000 USD",
                "3-4th": "80.000 USD",
                "5-8th": "45.000 USD",
                "9-11th": "20.000 USD",
                "12-16th": "20.000 USD",
            },
            "qualifier_slots": 8,
        },
    },
}
