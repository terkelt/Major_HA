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
            "team_rosters": {},
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
