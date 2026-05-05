"""HLTV Event coordinator – fetches and parses HLTV event pages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any

from aiohttp import ClientSession
from bs4 import BeautifulSoup

from .const import (
    CACHE_EVENT_SECONDS,
    CACHE_MATCHES_SECONDS,
    CACHE_RESULTS_SECONDS,
    CACHE_ROSTER_SECONDS,
    HLTV_BASE,
    HLTV_USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

_SCORE_RE = re.compile(r"\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b")
_TEAM_HREF_RE = re.compile(r"^/team/(\d+)/(.+)$")
_PLAYER_HREF_RE = re.compile(r"^/player/(\d+)/(.+)$")
_EVENT_ID_RE = re.compile(r"/events/(\d+)/")


class HLTVEventClient:
    """Fetches and parses a single HLTV event."""

    def __init__(self, session: ClientSession, event_url: str) -> None:
        self._session = session
        self.event_url = event_url.rstrip("/")
        m = _EVENT_ID_RE.search(event_url)
        self.event_id = m.group(1) if m else "0"
        self._cache: dict[str, tuple[datetime, str]] = {}
        self._roster_cache: dict[str, tuple[datetime, list[str]]] = {}
        self._team_queue: list[dict] = []
        self._roster_idx = 0

    # ------------------------------------------------------------------ #
    #  Public                                                              #
    # ------------------------------------------------------------------ #

    async def async_fetch_data(self) -> dict[str, Any]:
        """Fetch all event data and return structured payload."""
        now = datetime.now(UTC)

        # --- Event overview (teams, bracket, format) ---
        event_html, event_ok, event_err = await self._safe_fetch(self.event_url, CACHE_EVENT_SECONDS)
        event_info = self._parse_event_overview(event_html) if event_html else {}

        # --- Results ---
        results_url = f"{HLTV_BASE}/results?event={self.event_id}"
        results_html, results_ok, results_err = await self._safe_fetch(results_url, CACHE_RESULTS_SECONDS)
        results = self._parse_results(results_html) if results_html else []

        # --- Upcoming / Live matches ---
        matches_url = f"{HLTV_BASE}/events/{self.event_id}/matches"
        matches_html, matches_ok, matches_err = await self._safe_fetch(matches_url, CACHE_MATCHES_SECONDS)
        matches_data = self._parse_matches(matches_html) if matches_html else {"live": [], "upcoming": []}

        # --- Rosters (cached, one team per call cycle) ---
        teams: list[dict] = event_info.get("teams", [])
        if teams and not self._team_queue:
            self._team_queue = list(teams)
        rosters = await self._fetch_next_roster()

        return {
            "updated_at": now.isoformat(),
            "event_name": event_info.get("event_name", self._slug_to_title()),
            "event_url": self.event_url,
            "event_id": self.event_id,
            "start_date": event_info.get("start_date", ""),
            "end_date": event_info.get("end_date", ""),
            "location": event_info.get("location", ""),
            "prize_pool": event_info.get("prize_pool", ""),
            "format": event_info.get("format", ""),
            "map_pool": event_info.get("map_pool", []),
            "teams": teams,
            "rosters": rosters,
            "results": results,
            "live_matches": matches_data["live"],
            "upcoming_matches": matches_data["upcoming"],
            "sources": {
                "event_page": {"ok": event_ok, "error": event_err},
                "results_page": {"ok": results_ok, "error": results_err},
                "matches_page": {"ok": matches_ok, "error": matches_err},
            },
        }

    async def async_fetch_next_roster_background(self) -> None:
        """Call from coordinator after a delay to lazily fill rosters."""
        await self._fetch_next_roster()

    # ------------------------------------------------------------------ #
    #  Parsing                                                             #
    # ------------------------------------------------------------------ #

    def _parse_event_overview(self, html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        result: dict[str, Any] = {}

        # Event name
        for sel in ("h1.event-hub-title", ".event-title", "h1"):
            el = soup.select_one(sel)
            if el:
                result["event_name"] = el.get_text(strip=True)
                break

        # Teams – collect unique team entries from /team/ID/slug links
        seen_ids: set[str] = set()
        teams: list[dict] = []
        for a in soup.select('a[href^="/team/"]'):
            m = _TEAM_HREF_RE.match(a.get("href", ""))
            if not m:
                continue
            tid, slug = m.group(1), m.group(2)
            if tid in seen_ids:
                continue
            name = a.get_text(strip=True)
            # Filter out navigation noise: team links outside team boxes usually
            # have short/empty text or link text that matches the slug
            if not name or len(name) < 2 or name.startswith("#"):
                name = slug.replace("-", " ").title()
            seen_ids.add(tid)
            teams.append({
                "id": tid,
                "name": name,
                "slug": slug,
                "url": f"{HLTV_BASE}/team/{tid}/{slug}",
            })
        result["teams"] = teams

        # Event meta table (start/end date, location, prize pool)
        for row in soup.select("table.stats-table tr, .event-info-container tr, table tr"):
            cells = row.select("td")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower()
                val = cells[1].get_text(strip=True)
                if "start" in key:
                    result["start_date"] = val
                elif "end" in key:
                    result["end_date"] = val
                elif "location" in key:
                    result["location"] = val
                elif "prize" in key:
                    result["prize_pool"] = val

        # Map pool
        maps: list[str] = []
        for el in soup.select(".map-pool-map-name, .mapbox .map-name"):
            m_name = el.get_text(strip=True)
            if m_name:
                maps.append(m_name)
        if not maps:
            # Fallback: text search
            text = soup.get_text(" ")
            for m_name in ("Dust2", "Mirage", "Inferno", "Nuke", "Overpass", "Ancient", "Anubis", "Vertigo", "Train"):
                if m_name.lower() in text.lower():
                    maps.append(m_name)
        result["map_pool"] = maps

        return result

    def _parse_results(self, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict] = []

        # Try structured result boxes
        for box in soup.select(".result-box, .results-container .result"):
            teams = box.select(".team-cell .team, .team span")
            scores = box.select(".result-score span, .score")
            if len(teams) >= 2:
                t1 = teams[0].get_text(strip=True)
                t2 = teams[1].get_text(strip=True)
                s1, s2 = "?", "?"
                score_texts = [s.get_text(strip=True) for s in scores if s.get_text(strip=True).isdigit()]
                if len(score_texts) >= 2:
                    s1, s2 = score_texts[0], score_texts[1]
                if t1 and t2:
                    results.append({"team1": t1, "score1": s1, "team2": t2, "score2": s2})

        # Fallback: scan page text for "Team1 X - Y Team2" patterns
        if not results:
            text = soup.get_text("\n", strip=True)
            for line in text.splitlines():
                m = re.search(r"(.+?)\s+(\d)\s*[-:]\s*(\d)\s+(.+)", line)
                if m:
                    t1, s1, s2, t2 = m.group(1).strip(), m.group(2), m.group(3), m.group(4).strip()
                    if 1 <= len(t1) <= 40 and 1 <= len(t2) <= 40:
                        results.append({"team1": t1, "score1": s1, "team2": t2, "score2": s2})

        return results[:50]

    def _parse_matches(self, html: str) -> dict[str, list[dict]]:
        soup = BeautifulSoup(html, "html.parser")
        live: list[dict] = []
        upcoming: list[dict] = []

        for match in soup.select(".upcoming-match, .live-match, .match-day .match"):
            teams = match.select(".team span, .team-cell .team")
            time_el = match.select_one(".match-time, .time")
            is_live = bool(match.select_one(".live-indicator, .live"))

            t_names = [t.get_text(strip=True) for t in teams if t.get_text(strip=True)]
            match_time = time_el.get_text(strip=True) if time_el else ""

            if len(t_names) >= 2:
                entry = {"team1": t_names[0], "team2": t_names[1], "time": match_time}
                if is_live:
                    live.append(entry)
                else:
                    upcoming.append(entry)

        return {"live": live[:20], "upcoming": upcoming[:30]}

    def _parse_roster(self, html: str) -> list[str]:
        """Extract player names from a HLTV team page."""
        soup = BeautifulSoup(html, "html.parser")
        players: list[str] = []

        # Primary: links to /player/ID/
        for a in soup.select('a[href^="/player/"]'):
            m = _PLAYER_HREF_RE.match(a.get("href", ""))
            if not m:
                continue
            name = a.get_text(strip=True)
            if name and len(name) > 1 and name not in players:
                players.append(name)

        # Fallback: .text-ellipsis cells in player tables
        if not players:
            for el in soup.select(".text-ellipsis, .playersTable .player-name"):
                name = el.get_text(strip=True)
                if name and len(name) > 1 and name not in players:
                    players.append(name)

        # Keep only top-5 (active roster, not coaches etc.)
        return players[:5]

    # ------------------------------------------------------------------ #
    #  HTTP helpers                                                        #
    # ------------------------------------------------------------------ #

    async def _safe_fetch(self, url: str, cache_seconds: int) -> tuple[str | None, bool, str | None]:
        """Fetch URL with cache, return (html, ok, error)."""
        now = datetime.now(UTC)
        cached = self._cache.get(url)
        if cached and cached[0] > now:
            return cached[1], True, None
        try:
            async with self._session.get(
                url,
                headers={
                    "User-Agent": HLTV_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=25,
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()
            self._cache[url] = (now + timedelta(seconds=cache_seconds), html)
            return html, True, None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("HLTV fetch failed for %s: %s", url, exc)
            if cached:
                return cached[1], True, f"stale cache ({exc})"
            return None, False, str(exc)

    async def _fetch_next_roster(self) -> dict[str, list[str]]:
        """Return cached rosters, and trigger one new roster fetch if needed."""
        now = datetime.now(UTC)
        # Build current roster dict from cache
        rosters = {
            team: players
            for team, (expires, players) in self._roster_cache.items()
            if expires > now
        }

        # Find next team that needs a roster
        if self._team_queue:
            for i in range(len(self._team_queue)):
                idx = (self._roster_idx + i) % len(self._team_queue)
                team = self._team_queue[idx]
                tname = team.get("name", "")
                cached = self._roster_cache.get(tname)
                if cached and cached[0] > now:
                    continue
                # Fetch this team's roster
                self._roster_idx = (idx + 1) % len(self._team_queue)
                team_html, ok, _ = await self._safe_fetch(team["url"], CACHE_ROSTER_SECONDS)
                if ok and team_html:
                    players = self._parse_roster(team_html)
                    self._roster_cache[tname] = (
                        now + timedelta(seconds=CACHE_ROSTER_SECONDS),
                        players,
                    )
                    rosters[tname] = players
                break

        return rosters

    def _slug_to_title(self) -> str:
        slug = self.event_url.split("/")[-1]
        return slug.replace("-", " ").title()
