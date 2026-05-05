"""API client for IEM Cologne Major 2026 – hardcoded base + live signals."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib
import logging
import re
from typing import Any

from aiohttp import ClientResponseError, ClientSession
from bs4 import BeautifulSoup

from .const import HLTV_EVENT_URLS, LIQUIPEDIA_API_URL, TOURNAMENT_BASE

_LOGGER = logging.getLogger(__name__)

# Regex patterns
_SCORE_TOKEN_RE = re.compile(r"\b\d{1,2}\s*[:\-]\s*\d{1,2}\b")
_DATE_LIKE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+\w{3,9}\s+\d{4}\b")
_BARE_SCORE_RE = re.compile(r"^\s*\d{1,2}\s*[:\-]\s*\d{1,2}\s*$")

# Caching / backoff durations
_MAIN_PAGE_CACHE_MINUTES = 5
_HLTV_CACHE_MINUTES = 10
_LIQUIPEDIA_BACKOFF_MINUTES = 20

# Liquipedia API compliant User-Agent (see liquipedia.net/api-terms-of-use)
_LIQUIPEDIA_USER_AGENT = (
    "IEMCologneMajorHA/0.2 "
    "(https://github.com/terkelt/Major_HA/issues; community integration)"
)

# Stage windows derived from TOURNAMENT_BASE (ordered: stage_1 -> playoffs)
_STAGE_WINDOWS: list[dict[str, str]] = [
    {
        "name": v["short"],
        "start": v["start"],
        "end": v["end"],
    }
    for v in TOURNAMENT_BASE["stages"].values()
]

# All known participant team names (for live-signal validation)
_ALL_TEAMS: frozenset[str] = frozenset(
    TOURNAMENT_BASE["stages"]["stage_1"]["participants"]
    + TOURNAMENT_BASE["stages"]["stage_2"]["direct_invites"]
    + TOURNAMENT_BASE["stages"]["stage_3"]["direct_invites"]
)

# Keywords that indicate bracket / playoff context
_BRACKET_KEYWORDS = (
    "grand final", "upper final", "lower final",
    "semifinal", "semi-final", "quarterfinal", "quarter-final",
    "upper bracket", "lower bracket",
)

# Roster cache duration
_ROSTER_CACHE_HOURS = 24

# Liquipedia page slugs for each known team
# Used for lazy roster fetching (one team per coordinator cycle)
_LQ_TEAM_SLUGS: dict[str, str] = {
    # Stage 1
    "GamerLegion": "GamerLegion",
    "B8": "B8_(esports_club)",
    "HEROIC": "HEROIC",
    "BetBoom": "BetBoom_Team",
    "BIG": "BIG",
    "M80": "M80",
    "TYLOO": "TYLOO",
    "MIBR": "MIBR",
    "SINNERS": "SINNERS_Esports",
    "NRG": "NRG_Esports",
    "Gaimin Gladiators": "Gaimin_Gladiators",
    "Liquid": "Team_Liquid",
    "Lynn Vision": "Lynn_Vision_Gaming",
    "THUNDER dOWNUNDER": "THUNDER_dOWNUNDER",
    "FlyQuest": "FlyQuest",
    "Sharks": "Sharks_Esports",
    # Stage 2 direct invites
    "FUT": "FUT_Esports",
    "Spirit": "Team_Spirit",
    "Astralis": "Astralis",
    "G2": "G2_Esports",
    "Legacy": "Legacy_(Brazilian_organisation)",
    "Monte": "Monte_(esports)",
    "9z": "9z_Team",
    "paiN": "paiN_Gaming",
    # Stage 3 direct invites
    "Vitality": "Team_Vitality",
    "Natus Vincere": "Natus_Vincere",
    "Falcons": "Team_Falcons",
    "The MongolZ": "The_MongolZ",
    "PARIVISION": "PARIVISION",
    "Aurora": "Aurora_Gaming",
    "FURIA": "FURIA_Esports",
    "MOUZ": "MOUZ",
}

# Swiss standings: W-L regex (handles both hyphen and en-dash)
_WL_RE = re.compile(r"^(\d+)[\u2013\-](\d+)$")

# Liquipedia subpage slug per active stage (populated once page exists)
_STAGE_LQ_SUBPAGES: dict[str, str] = {
    "Stage 1": "Intel_Extreme_Masters/2026/Cologne/Stage_1",
    "Stage 2": "Intel_Extreme_Masters/2026/Cologne/Stage_2",
    "Stage 3": "Intel_Extreme_Masters/2026/Cologne/Stage_3",
}

# How many teams advance from each Swiss stage
_STAGE_ADVANCE_COUNT: dict[str, int] = {
    "Stage 1": 8,
    "Stage 2": 8,
    "Stage 3": 8,
}

# Noise keywords that indicate a line is NOT a score signal
_NOISE_KEYWORDS = (
    "cookie", "privacy", "http", "edit", "viewer", "follow",
    "subscribe", "highlight", "vod", "replay", "stream", "twitch",
    "youtube", "twitter", "reddit",
)


class IEMCologneApiClient:
    """Fetches live signals for IEM Cologne Major 2026.

    Tournament structure (teams, stages, dates) is always sourced from
    TOURNAMENT_BASE in const.py.  Live network calls are used only for
    match result signals.
    """

    def __init__(
        self,
        session: ClientSession,
        liquipedia_page: str,
        include_finished_matches: bool,
        include_hltv_signal: bool,
    ) -> None:
        self._session = session
        self._liquipedia_page = liquipedia_page
        self._include_finished_matches = include_finished_matches
        self._include_hltv_signal = include_hltv_signal

        self._last_scores_fingerprint: str | None = None
        self._last_score_change: str | None = None
        self._last_payload: dict[str, Any] | None = None
        self._page_cache: dict[str, tuple[datetime, str]] = {}
        self._hltv_cache: tuple[datetime, dict[str, Any]] | None = None
        self._liquipedia_backoff_until: datetime | None = None
        # Roster fetching state
        self._roster_cache: dict[str, tuple[datetime, list[str]]] = {}
        self._roster_team_list: list[str] = list(_LQ_TEAM_SLUGS.keys())
        self._roster_idx: int = 0
        # Swiss standings cache: stage_name -> (expires_at, standings_list)
        self._standings_cache: dict[str, tuple[datetime, list[dict]]] = {}

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    async def async_fetch_data(self) -> dict[str, Any]:
        """Build payload: hardcoded tournament data + live score signals."""
        now = datetime.now(UTC)
        today = now.date()
        active_stage = self._detect_active_stage(today)

        # --- Hardcoded authoritative teams (never wrong) ----------------
        participants: dict[str, list[str]] = {
            "stage_1": list(TOURNAMENT_BASE["stages"]["stage_1"]["participants"]),
            "stage_2": list(TOURNAMENT_BASE["stages"]["stage_2"]["direct_invites"]),
            "stage_3": list(TOURNAMENT_BASE["stages"]["stage_3"]["direct_invites"]),
        }

        # --- Live score signals -----------------------------------------
        score_lines: list[str] = []
        bracket_lines: list[str] = []

        liq_ok = True
        liq_error: str | None = None
        try:
            liq = await self._async_fetch_liquipedia_signals()
            score_lines.extend(liq.get("score_lines", []))
            bracket_lines.extend(liq.get("bracket_lines", []))
        except Exception as exc:
            liq_ok = False
            liq_error = str(exc)
            _LOGGER.warning("Liquipedia fetch failed: %s", exc)

        hltv_ok = True
        hltv_error: str | None = None
        hltv_used: list[str] = []
        hltv_dropped: list[str] = []
        if self._include_hltv_signal:
            hltv = await self._async_fetch_hltv_signals(active_stage)
            hltv_ok = hltv.get("ok", False)
            hltv_error = hltv.get("error")
            hltv_used = hltv.get("used_urls", [])
            hltv_dropped = hltv.get("dropped_urls", [])
            score_lines.extend(hltv.get("score_lines", []))
            bracket_lines.extend(hltv.get("bracket_lines", []))

        score_lines = self._uniq(score_lines)[:60]
        bracket_lines = self._uniq(bracket_lines)[:24]

        # --- Change detection -------------------------------------------
        score_changed = self._update_score_fingerprint(score_lines)
        if score_changed:
            self._last_score_change = now.isoformat()

        # --- Build payload ----------------------------------------------
        payload: dict[str, Any] = {
            "updated_at": now.isoformat(),
            "active_stage": active_stage,
            # Authoritative tournament info (hardcoded)
            "tournament": {
                "name": TOURNAMENT_BASE["name"],
                "organizer": TOURNAMENT_BASE["organizer"],
                "location": TOURNAMENT_BASE["location"],
                "venue": TOURNAMENT_BASE["venue"],
                "prize_pool": TOURNAMENT_BASE["prize_pool"],
                "map_pool": TOURNAMENT_BASE["map_pool"],
            },
            "stage_windows": _STAGE_WINDOWS,
            "stage_info": {
                k: {
                    "name": v["name"],
                    "short": v["short"],
                    "start": v["start"],
                    "end": v["end"],
                    "format": v["format"],
                    "prize": v.get("prize", ""),
                }
                for k, v in TOURNAMENT_BASE["stages"].items()
            },
            "participants": participants,
            "team_rosters": self._get_cached_rosters(),
            "swiss_standings": self._get_cached_standings(active_stage),
            # Live signals
            "score_signal_lines": score_lines,
            "bracket_lines": bracket_lines,
            # Match state
            "upcoming_matches": [],
            "live_matches": [],
            "completed_matches": [],
            "next_match": None,
            "matches_today": 0,
            # Change detection
            "score_change_detected": score_changed,
            "last_score_change": self._last_score_change,
            # Source diagnostics
            "sources": {
                "liquipedia": {
                    "page": self._liquipedia_page,
                    "ok": liq_ok,
                    "error": liq_error,
                    "policy_mode": "mediawiki_api_only",
                },
                "hltv": {
                    "enabled": self._include_hltv_signal,
                    "ok": hltv_ok,
                    "error": hltv_error,
                    "used_urls": hltv_used,
                    "dropped_urls": hltv_dropped,
                    "score_lines_detected": len(score_lines),
                    "bracket_lines_detected": len(bracket_lines),
                },
            },
        }
        self._last_payload = payload
        return payload

    # ------------------------------------------------------------------ #
    #  Liquipedia                                                          #
    # ------------------------------------------------------------------ #

    async def _async_fetch_liquipedia_signals(self) -> dict[str, Any]:
        """Single Liquipedia API call -> extract team-validated signal lines."""
        html = await self._async_fetch_liquipedia_page_html(
            self._liquipedia_page,
            cache_minutes=_MAIN_PAGE_CACHE_MINUTES,
        )
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        return {
            "score_lines": self._uniq(self._extract_score_lines(text))[:40],
            "bracket_lines": self._uniq(self._extract_bracket_lines(text))[:20],
        }

    async def _async_fetch_liquipedia_page_html(
        self, page: str, cache_minutes: int
    ) -> str:
        """Fetch a Liquipedia MediaWiki API parse result with caching."""
        cached = self._page_cache.get(page)
        now = datetime.now(UTC)

        if cached and cached[0] > now:
            return cached[1]

        if (
            self._liquipedia_backoff_until
            and now < self._liquipedia_backoff_until
        ):
            if cached:
                _LOGGER.debug("Liquipedia backoff active, returning cache for %s", page)
                return cached[1]
            raise RuntimeError(
                "Liquipedia temporarily rate-limited, no cache available"
            )

        params = {
            "action": "parse",
            "page": page,
            "prop": "text",
            "format": "json",
            "formatversion": 2,
        }
        headers = {"User-Agent": _LIQUIPEDIA_USER_AGENT}

        try:
            async with self._session.get(
                LIQUIPEDIA_API_URL,
                params=params,
                headers=headers,
                timeout=20,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as err:
            if err.status == 429:
                self._liquipedia_backoff_until = now + timedelta(
                    minutes=_LIQUIPEDIA_BACKOFF_MINUTES
                )
                if cached:
                    _LOGGER.warning(
                        "Liquipedia 429 rate-limited, using cache for %s", page
                    )
                    return cached[1]
            raise

        if "parse" not in data or "text" not in data.get("parse", {}):
            raise ValueError(
                f"Liquipedia API response missing parse.text for page '{page}'"
            )

        html: str = data["parse"]["text"]
        self._page_cache[page] = (now + timedelta(minutes=cache_minutes), html)
        self._liquipedia_backoff_until = None
        return html

    # ------------------------------------------------------------------ #
    #  HLTV                                                                #
    # ------------------------------------------------------------------ #

    async def _async_fetch_hltv_signals(self, active_stage: str) -> dict[str, Any]:
        """Fetch score signals from trusted HLTV Cologne event URLs."""
        now = datetime.now(UTC)
        if self._hltv_cache and self._hltv_cache[0] > now:
            return self._hltv_cache[1]

        bundle: dict[str, Any] = {
            "ok": True,
            "error": None,
            "used_urls": [],
            "dropped_urls": [],
            "score_lines": [],
            "bracket_lines": [],
        }
        score_lines: list[str] = []
        bracket_lines: list[str] = []

        for url in self._hltv_urls_for_stage(active_stage):
            try:
                async with self._session.get(
                    url,
                    headers={"User-Agent": _LIQUIPEDIA_USER_AGENT},
                    timeout=20,
                ) as resp:
                    resp.raise_for_status()
                    html = await resp.text()

                soup = BeautifulSoup(html, "html.parser")
                text = soup.get_text("\n", strip=True)

                bundle["used_urls"].append(url)
                score_lines.extend(self._extract_score_lines(text))
                bracket_lines.extend(self._extract_bracket_lines(text))

            except Exception as url_err:
                bundle["dropped_urls"].append(url)
                _LOGGER.debug("HLTV URL %s failed: %s", url, url_err)

        if bundle["used_urls"]:
            bundle["score_lines"] = self._uniq(score_lines)[:40]
            bundle["bracket_lines"] = self._uniq(bracket_lines)[:20]
        else:
            bundle["ok"] = False
            bundle["error"] = "All HLTV URLs failed or were dropped"

        self._hltv_cache = (
            now + timedelta(minutes=_HLTV_CACHE_MINUTES),
            bundle,
        )
        return bundle

    # ------------------------------------------------------------------ #
    #  Stage detection                                                     #
    # ------------------------------------------------------------------ #

    def _detect_active_stage(self, today: date) -> str:
        """Return current stage name based on hardcoded tournament calendar."""
        stages = [
            ("Stage 1",  date(2026, 6,  2), date(2026, 6,  5)),
            ("Stage 2",  date(2026, 6,  6), date(2026, 6,  9)),
            ("Stage 3",  date(2026, 6, 11), date(2026, 6, 15)),
            ("Playoffs", date(2026, 6, 18), date(2026, 6, 21)),
        ]
        first_day = stages[0][1]
        last_day = stages[-1][2]

        if today < first_day:
            return "Upcoming"
        if today > last_day:
            return "Finished"

        for name, start, end in stages:
            if start <= today <= end:
                return name

        for i, (_name, _start, end) in enumerate(stages[:-1]):
            next_start = stages[i + 1][1]
            if end < today < next_start:
                return f"Pause (vor {stages[i + 1][0]})"

        return "Unknown"

    # ------------------------------------------------------------------ #
    #  Signal extraction helpers                                           #
    # ------------------------------------------------------------------ #

    def _extract_score_lines(self, text: str) -> list[str]:
        """Return lines containing a score token AND at least one known team."""
        lines: list[str] = []
        for raw in text.splitlines():
            line = self._clean_text(raw)
            if not line or len(line) > 120 or len(line) < 6:
                continue
            if _DATE_LIKE_RE.search(line):
                continue
            if _BARE_SCORE_RE.fullmatch(line):
                continue
            if not _SCORE_TOKEN_RE.search(line):
                continue
            lower = line.lower()
            if any(kw in lower for kw in _NOISE_KEYWORDS):
                continue
            if any(team.lower() in lower for team in _ALL_TEAMS):
                lines.append(line)
        return lines

    def _extract_bracket_lines(self, text: str) -> list[str]:
        """Return bracket/playoff lines mentioning known teams."""
        lines: list[str] = []
        for raw in text.splitlines():
            line = self._clean_text(raw)
            if not line or len(line) > 120:
                continue
            lower = line.lower()
            has_bracket = any(kw in lower for kw in _BRACKET_KEYWORDS)
            has_team = any(team.lower() in lower for team in _ALL_TEAMS)
            has_score = bool(_SCORE_TOKEN_RE.search(line))
            if any(kw in lower for kw in _NOISE_KEYWORDS):
                continue
            if has_bracket and (has_team or "tbd" in lower):
                lines.append(line)
            elif has_score and has_team and " vs " in lower:
                lines.append(line)
        return lines

    # ------------------------------------------------------------------ #
    #  Roster fetching (Liquipedia, lazy – one team per background call)  #
    # ------------------------------------------------------------------ #

    def _get_cached_rosters(self) -> dict[str, list[str]]:
        """Return all currently valid cached rosters."""
        now = datetime.now(UTC)
        return {
            team: players
            for team, (expires, players) in self._roster_cache.items()
            if expires > now
        }

    async def async_fetch_next_roster(self) -> None:
        """Background fetch: Swiss standings (if stale) first, then one team roster.

        Called ~32 seconds after each main coordinator update to respect
        Liquipedia's 1-req/30s rate limit.  Only ONE request is made per call.
        """
        now = datetime.now(UTC)
        today = now.date()
        active_stage = self._detect_active_stage(today)

        # 1) Fetch Swiss standings for the active stage if stale --------
        stage_subpage = _STAGE_LQ_SUBPAGES.get(active_stage)
        if stage_subpage:
            cached_st = self._standings_cache.get(active_stage)
            if not cached_st or cached_st[0] <= now:
                n_advance = _STAGE_ADVANCE_COUNT.get(active_stage, 8)
                try:
                    html = await self._async_fetch_liquipedia_page_html(
                        stage_subpage, cache_minutes=_MAIN_PAGE_CACHE_MINUTES
                    )
                    standings = self._parse_swiss_standings(html, n_advance)
                    self._standings_cache[active_stage] = (
                        now + timedelta(minutes=_MAIN_PAGE_CACHE_MINUTES),
                        standings,
                    )
                    _LOGGER.debug(
                        "Swiss standings for %s: %d entries", active_stage, len(standings)
                    )
                except Exception as exc:  # noqa: BLE001
                    # Page may not exist yet (pre-tournament); keep last known data
                    self._standings_cache.setdefault(
                        active_stage,
                        (now + timedelta(minutes=_MAIN_PAGE_CACHE_MINUTES), []),
                    )
                    _LOGGER.debug("Standings fetch skipped for %s: %s", active_stage, exc)
                return  # one request per background call

        # 2) Fetch next team's roster -----------------------------------
        teams = self._roster_team_list
        if not teams:
            return

        for i in range(len(teams)):
            idx = (self._roster_idx + i) % len(teams)
            team_name = teams[idx]
            cached = self._roster_cache.get(team_name)
            if cached and cached[0] > now:
                continue  # still fresh

            # Fetch this team
            self._roster_idx = (idx + 1) % len(teams)
            slug = _LQ_TEAM_SLUGS.get(team_name, team_name.replace(" ", "_"))
            try:
                html = await self._async_fetch_liquipedia_page_html(
                    slug, cache_minutes=_ROSTER_CACHE_HOURS * 60
                )
                players = self._parse_roster_from_html(html, team_name)
                self._roster_cache[team_name] = (
                    now + timedelta(hours=_ROSTER_CACHE_HOURS),
                    players,
                )
                _LOGGER.debug("Fetched roster for %s: %s", team_name, players)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Could not fetch roster for %s: %s", team_name, exc)
            return  # only fetch one team per call

    def _get_cached_standings(self, active_stage: str) -> list[dict]:
        """Return last known Swiss standings for the active stage (stale-while-revalidate)."""
        cached = self._standings_cache.get(active_stage)
        return cached[1] if cached else []

    @staticmethod
    def _parse_swiss_standings(html: str, n_advance: int = 8) -> list[dict[str, Any]]:
        """Parse the Swiss overview table from a rendered Liquipedia stage page.

        Expected table columns (Liquipedia CS Major format):
          #  |  Team  |  Matches  |  Rounds  |  RD  |  BU  |  Round 1  | ...
        """
        soup = BeautifulSoup(html, "html.parser")
        standings: list[dict] = []

        for table in soup.select("table.wikitable"):
            # Identify header row
            header_row = table.find("tr")
            if not header_row:
                continue
            headers = [
                th.get_text(strip=True).lower()
                for th in header_row.find_all(["th", "td"])
            ]
            # Must have both "team" and "matches" columns
            if "team" not in headers or "matches" not in headers:
                continue

            matches_idx = headers.index("matches")
            team_idx = headers.index("team")
            rd_idx = headers.index("rd") if "rd" in headers else -1
            rounds_idx = headers.index("rounds") if "rounds" in headers else -1
            round_col_indices = [i for i, h in enumerate(headers) if re.match(r"round\s*\d", h)]

            for row in table.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= matches_idx:
                    continue

                # Rank
                rank_txt = cells[0].get_text(strip=True).rstrip(".")
                try:
                    rank = int(rank_txt)
                except ValueError:
                    continue

                # Team name: prefer .team-template-text, then <a>, then raw text
                team_cell = cells[team_idx]
                name_el = (
                    team_cell.select_one(".team-template-text")
                    or team_cell.select_one("a")
                    or team_cell
                )
                team_name = name_el.get_text(strip=True)
                if not team_name:
                    continue

                # W-L record
                wl_txt = cells[matches_idx].get_text(strip=True).replace("\u2013", "-")
                wl_m = _WL_RE.match(wl_txt)
                if not wl_m:
                    continue
                wins, losses = int(wl_m.group(1)), int(wl_m.group(2))

                # Status
                if wins >= 3:
                    status = "advancing"
                elif losses >= 3:
                    status = "eliminated"
                else:
                    status = "playing"

                # Round diff
                rd_txt = cells[rd_idx].get_text(strip=True) if rd_idx >= 0 and rd_idx < len(cells) else ""
                rounds_txt = cells[rounds_idx].get_text(strip=True) if rounds_idx >= 0 and rounds_idx < len(cells) else ""

                # Per-round results
                round_results = [
                    cells[ri].get_text(" ", strip=True)
                    for ri in round_col_indices
                    if ri < len(cells) and cells[ri].get_text(strip=True)
                ]

                standings.append({
                    "rank": rank,
                    "team": team_name,
                    "record": f"{wins}-{losses}",
                    "wins": wins,
                    "losses": losses,
                    "status": status,
                    "rd": rd_txt,
                    "rounds": rounds_txt,
                    "round_results": round_results,
                })

            if standings:
                break  # found the right table

        return standings

    def _parse_roster_from_html(self, html: str, team_name: str) -> list[str]:
        """Extract player names from a Liquipedia team page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        players: list[str] = []

        # Liquipedia CS2 team pages: players in .roster-card table
        # Player link href="/counterstrike/PlayerName" with in-game tag as text
        for a in soup.select('table.roster-card a[href*="/counterstrike/"]'):
            name = a.get_text(strip=True)
            if name and len(name) >= 2 and name not in players:
                players.append(name)
            if len(players) >= 5:
                break

        # Fallback: any player-looking links in the page
        if not players:
            for a in soup.select('a[href*="/counterstrike/"]'):
                href = a.get("href", "")
                name = a.get_text(strip=True)
                # Heuristic: player pages are single words or short tags
                if name and 2 <= len(name) <= 20 and "_" not in name and name not in players:
                    players.append(name)
                if len(players) >= 5:
                    break

        return players

    # ------------------------------------------------------------------ #
    #  Utility helpers                                                     #
    # ------------------------------------------------------------------ #

    def _update_score_fingerprint(self, lines: list[str]) -> bool:
        """Return True if score lines changed since last call."""
        new = hashlib.sha1("\n".join(lines).encode()).hexdigest()
        old = self._last_scores_fingerprint
        self._last_scores_fingerprint = new
        return old is not None and old != new

    @staticmethod
    def _uniq(lines: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            if line not in seen:
                seen.add(line)
                out.append(line)
        return out

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _hltv_urls_for_stage(self, active_stage: str) -> list[str]:
        """Return relevant HLTV URL(s) for the currently active stage."""
        mapping: dict[str, list[str]] = {
            "Stage 1": [HLTV_EVENT_URLS[0]],
            "Stage 2": [HLTV_EVENT_URLS[1]],
            "Stage 3": [HLTV_EVENT_URLS[2]],
            "Playoffs": [HLTV_EVENT_URLS[2]],
        }
        if active_stage.startswith("Pause"):
            return HLTV_EVENT_URLS[:2] if "Stage 2" in active_stage else HLTV_EVENT_URLS[1:]
        return mapping.get(active_stage, [HLTV_EVENT_URLS[2]])
