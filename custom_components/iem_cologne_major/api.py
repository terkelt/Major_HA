"""API client for IEM Cologne Major data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import hashlib
import logging
import re
from typing import Any

from aiohttp import ClientResponseError
from aiohttp import ClientSession
from bs4 import BeautifulSoup

from .const import HLTV_EVENT_URLS, LIQUIPEDIA_API_URL

_LOGGER = logging.getLogger(__name__)

_ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)")
_STAGE_LINE_RE = re.compile(
    r"^(Stage\s+[123]|Playoffs):\s+(.+?)\s+-\s+(.+?),\s*(\d{4})$",
    flags=re.IGNORECASE,
)
_SCORE_TOKEN_RE = re.compile(r"\b\d{1,2}\s*[:\-]\s*\d{1,2}\b")
_HLTV_TEAM_HREF_RE = re.compile(r"^/team/\d+/")
_ROSTER_LINE_RE = re.compile(r"^([1-5SC])\s+.+\s+([^\s]+)$")
_COLOGNE_EVENT_KEYWORDS = ("iem cologne major 2026", "cologne, germany")
_BLOCKED_EVENT_KEYWORDS = ("iem atlanta", "atlanta")

_MAIN_PAGE_CACHE_MINUTES = 3
_HLTV_CACHE_MINUTES = 10
_LIQUIPEDIA_BACKOFF_MINUTES = 15
_LIQUIPEDIA_USER_AGENT = (
    "IEMCologneMajorHA/0.1 "
    "(https://github.com/terkelt/Major_HA/issues; community integration)"
)

_DEFAULT_STAGE_WINDOWS = [
    {"name": "Stage 1", "start": "2026-06-02", "end": "2026-06-05"},
    {"name": "Stage 2", "start": "2026-06-06", "end": "2026-06-09"},
    {"name": "Stage 3", "start": "2026-06-11", "end": "2026-06-15"},
    {"name": "Playoffs", "start": "2026-06-18", "end": "2026-06-21"},
]


@dataclass(slots=True)
class ParsedStageWindow:
    """Date range for a tournament stage."""

    name: str
    start: date
    end: date


class IEMCologneApiClient:
    """Fetches tournament data from source pages with fast polling."""

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

    async def async_fetch_data(self) -> dict[str, Any]:
        """Fetch and merge data from Liquipedia and optional HLTV signal pages."""
        now = datetime.now(UTC)
        try:
            liquipedia_data = await self._async_fetch_liquipedia_bundle(now.date())
            active_stage = self._detect_active_stage(now.date(), liquipedia_data.get("stage_windows", []))

            hltv_signal: dict[str, Any] = {
                "enabled": self._include_hltv_signal,
                "ok": True,
                "error": None,
                "signal_lines": [],
                "bracket_lines": [],
                "teams": [],
            }
            if self._include_hltv_signal:
                hltv_signal = await self._async_fetch_hltv_signal(active_stage)

            upcoming_matches = liquipedia_data.get("upcoming_matches", [])
            participants = liquipedia_data.get("participants", {})
            if self._participants_count(participants) == 0 and hltv_signal.get("teams"):
                participants = self._participants_from_fallback_teams(hltv_signal.get("teams", []))

            bracket_lines = list(liquipedia_data.get("bracket_lines", []))
            bracket_lines.extend(hltv_signal.get("bracket_lines", []))
            bracket_lines = self._uniq(bracket_lines)[:32]

            score_lines = list(liquipedia_data.get("score_signal_lines", []))
            score_lines.extend(hltv_signal.get("signal_lines", []))
            score_lines = self._uniq(score_lines)[:80]

            completed_matches = [
                {
                    "name": line,
                    "status": "finished",
                }
                for line in score_lines
            ]

            score_change_detected = self._update_score_fingerprint(score_lines)
            if score_change_detected:
                self._last_score_change = now.isoformat()

            upcoming_matches.sort(key=lambda m: m.get("begin_at") or "")
            next_match = upcoming_matches[0] if upcoming_matches else None

            matches_today = 0
            for match in upcoming_matches:
                begin_at = self._safe_parse_datetime(match.get("begin_at"))
                if begin_at and begin_at.date() == now.date():
                    matches_today += 1

            if not self._include_finished_matches:
                completed_matches = []

            payload = {
                "updated_at": now.isoformat(),
                "active_stage": active_stage,
                "overview": {
                    **liquipedia_data.get("overview", {}),
                    "source_mode": "full" if liquipedia_data.get("participants") else "degraded",
                },
                "stage_windows": liquipedia_data.get("stage_windows", []),
                "participants": participants,
                "team_rosters": liquipedia_data.get("team_rosters", {}),
                "bracket_lines": bracket_lines,
                "upcoming_matches": upcoming_matches,
                "live_matches": [],
                "completed_matches": completed_matches,
                "next_match": next_match,
                "matches_today": matches_today,
                "score_change_detected": score_change_detected,
                "last_score_change": self._last_score_change,
                "score_signal_lines": score_lines,
                "sources": {
                    "liquipedia": {
                        "page": self._liquipedia_page,
                        "ok": True,
                        "policy_mode": "mediawiki_api_only",
                    },
                    "hltv": {
                        "enabled": hltv_signal.get("enabled", False),
                        "ok": hltv_signal.get("ok", False),
                        "error": hltv_signal.get("error"),
                        "strict_filter": hltv_signal.get("strict_filter", True),
                        "used_urls": hltv_signal.get("used_urls", []),
                        "dropped_urls": hltv_signal.get("dropped_urls", []),
                        "teams_detected": len(hltv_signal.get("teams", [])),
                        "bracket_lines_detected": len(hltv_signal.get("bracket_lines", [])),
                    },
                },
            }
            self._last_payload = payload
            return payload
        except Exception as err:
            if self._last_payload is not None:
                return self._build_fallback_payload(now)
            return await self._build_emergency_payload(now, str(err))

    async def _async_fetch_liquipedia_bundle(self, today: date) -> dict[str, Any]:
        html = await self._async_fetch_liquipedia_page_html(
            self._liquipedia_page,
            cache_minutes=_MAIN_PAGE_CACHE_MINUTES,
        )
        soup = BeautifulSoup(html, "html.parser")

        overview = self._parse_infobox(soup)
        stage_windows = self._parse_stage_windows(soup)
        participants = self._parse_participants(soup)
        team_rosters = self._parse_team_rosters(soup, participants)
        upcoming_matches = self._parse_upcoming_matches(soup)
        score_signal_lines = self._extract_score_lines(soup.get_text("\n", strip=True))
        bracket_lines = self._collect_bracket_lines(soup.get_text("\n", strip=True))

        return {
            "overview": overview,
            "stage_windows": stage_windows,
            "participants": participants,
            "team_rosters": team_rosters,
            "bracket_lines": bracket_lines,
            "upcoming_matches": upcoming_matches,
            "score_signal_lines": self._uniq(score_signal_lines)[:60],
        }

    async def _async_fetch_liquipedia_page_html(self, page: str, cache_minutes: int) -> str:
        cached = self._page_cache.get(page)
        now = datetime.now(UTC)
        if cached and cached[0] > now:
            return cached[1]

        if self._liquipedia_backoff_until and now < self._liquipedia_backoff_until:
            if cached:
                return cached[1]
            raise RuntimeError("Liquipedia temporarily rate limited")

        params = {
            "action": "parse",
            "page": page,
            "prop": "text",
            "format": "json",
            "formatversion": 2,
        }
        headers = {
            "User-Agent": _LIQUIPEDIA_USER_AGENT,
        }

        try:
            async with self._session.get(LIQUIPEDIA_API_URL, params=params, headers=headers, timeout=20) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as err:
            if err.status == 429:
                self._liquipedia_backoff_until = now + timedelta(minutes=_LIQUIPEDIA_BACKOFF_MINUTES)
                if cached:
                    _LOGGER.warning("Liquipedia rate limited for page %s, using cached response", page)
                    return cached[1]
            raise

        if "parse" not in data or "text" not in data["parse"]:
            raise ValueError("Liquipedia response did not contain parse text")

        html = data["parse"]["text"]
        self._page_cache[page] = (now + timedelta(minutes=cache_minutes), html)
        self._liquipedia_backoff_until = None
        return html

    async def _async_fetch_hltv_signal(self, active_stage: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        if self._hltv_cache and self._hltv_cache[0] > now:
            return self._hltv_cache[1]

        bundle: dict[str, Any] = {
            "enabled": True,
            "ok": True,
            "error": None,
            "strict_filter": True,
            "used_urls": [],
            "dropped_urls": [],
            "signal_lines": [],
            "bracket_lines": [],
            "teams": [],
        }
        try:
            lines: list[str] = []
            bracket_lines: list[str] = []
            teams: list[str] = []
            headers = {
                "User-Agent": "HomeAssistant-IEMCologneMajor/0.1 (+community project)",
            }
            for url in self._hltv_urls_for_stage(active_stage):
                async with self._session.get(url, headers=headers, timeout=20) as resp:
                    resp.raise_for_status()
                    html = await resp.text()

                soup = BeautifulSoup(html, "html.parser")
                text = soup.get_text("\n", strip=True)
                if not self._is_expected_cologne_page(text):
                    bundle["dropped_urls"].append(url)
                    continue
                bundle["used_urls"].append(url)
                lines.extend(self._extract_score_lines(text))
                bracket_lines.extend(self._collect_bracket_lines(text))
                teams.extend(self._extract_hltv_teams(soup))

            if not bundle["used_urls"]:
                raise RuntimeError("No HLTV Cologne pages passed strict filter")

            bundle["signal_lines"] = self._uniq(lines)[:40]
            bundle["bracket_lines"] = self._uniq(bracket_lines)[:32]
            bundle["teams"] = self._uniq(teams)[:32]
            self._hltv_cache = (now + timedelta(minutes=_HLTV_CACHE_MINUTES), bundle)
            return bundle
        except Exception as err:
            _LOGGER.warning("HLTV signal fetch failed: %s", err)
            bundle["ok"] = False
            bundle["error"] = str(err)
            if self._hltv_cache:
                return self._hltv_cache[1]
            return bundle

    def _parse_infobox(self, soup: BeautifulSoup) -> dict[str, Any]:
        info: dict[str, Any] = {}
        table = soup.find("table", class_=re.compile("infobox"))
        if not table:
            return info

        for row in table.find_all("tr"):
            key_el = row.find("th")
            val_el = row.find("td")
            if not key_el or not val_el:
                continue

            key = self._clean_text(key_el.get_text(" ", strip=True)).lower().replace(" ", "_")
            value = self._clean_text(val_el.get_text(" ", strip=True))
            if key and value:
                info[key] = value

        return info

    def _parse_stage_windows(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        heading = self._find_heading_by_text(soup, "Format")
        if not heading:
            return []

        windows: list[dict[str, Any]] = []
        for line in self._collect_section_lines(heading):
            match = _STAGE_LINE_RE.match(line)
            if not match:
                continue

            stage_name = match.group(1)
            left = self._normalize_ordinals(match.group(2))
            right = self._normalize_ordinals(match.group(3))
            year = match.group(4)

            parsed = self._parse_stage_window(stage_name, left, right, year)
            if parsed:
                windows.append(
                    {
                        "name": parsed.name,
                        "start": parsed.start.isoformat(),
                        "end": parsed.end.isoformat(),
                    }
                )

        return windows

    def _parse_stage_window(
        self, stage_name: str, left_date_text: str, right_date_text: str, year: str
    ) -> ParsedStageWindow | None:
        try:
            if " " not in right_date_text:
                left_month, left_day = left_date_text.split(" ", 1)
                right_date_text = f"{left_month} {right_date_text}"

            start = datetime.strptime(f"{left_date_text} {year}", "%B %d %Y").date()
            end = datetime.strptime(f"{right_date_text} {year}", "%B %d %Y").date()

            return ParsedStageWindow(name=stage_name.title(), start=start, end=end)
        except Exception:
            return None

    def _parse_participants(self, soup: BeautifulSoup) -> dict[str, list[str]]:
        participants: dict[str, list[str]] = {
            "stage_1": [],
            "stage_2": [],
            "stage_3": [],
        }

        mapping = {
            "Stage 1 Invites": "stage_1",
            "Stage 2 Invites": "stage_2",
            "Stage 3 Invites": "stage_3",
        }

        for section_name, out_key in mapping.items():
            heading = self._find_heading_by_text(soup, section_name)
            if not heading:
                continue

            names: list[str] = []
            for link in self._collect_section_links(heading):
                href = link.get("href", "")
                text = self._clean_text(link.get_text(" ", strip=True))
                if not text or not href.startswith("/counterstrike/"):
                    continue
                if text in {"VRS Europe (Apr. 2026)", "VRS Americas (Apr. 2026)", "VRS Asia (Apr. 2026)"}:
                    continue
                if text in names:
                    continue
                names.append(text)

            participants[out_key] = names

        return participants

    def _parse_upcoming_matches(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        heading = self._find_heading_by_text(soup, "Upcoming Matches")
        if not heading:
            return []

        matches: list[dict[str, Any]] = []
        for line in self._collect_section_lines(heading):
            if "TBD" not in line and "vs" not in line.lower() and "Show countdown" not in line:
                continue

            matches.append(
                {
                    "name": line,
                    "status": "scheduled",
                    "begin_at": None,
                    "opponents": [],
                }
            )

        return matches[:25]

    def _parse_team_rosters(
        self, soup: BeautifulSoup, participants: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        rosters: dict[str, list[str]] = {}
        mapping = {
            "Stage 1 Invites": "stage_1",
            "Stage 2 Invites": "stage_2",
            "Stage 3 Invites": "stage_3",
        }

        for section_name, stage_key in mapping.items():
            heading = self._find_heading_by_text(soup, section_name)
            if not heading:
                continue

            stage_teams = participants.get(stage_key, [])
            if not stage_teams:
                continue

            current_team: str | None = None
            for line in self._collect_section_lines(heading):
                team_match = self._match_team_line(line, stage_teams)
                if team_match:
                    current_team = team_match
                    rosters.setdefault(current_team, [])
                    continue

                if not current_team:
                    continue

                player = self._extract_player_from_roster_line(line)
                if player and player not in rosters[current_team]:
                    rosters[current_team].append(player)

            # keep only realistic roster size
            for team in list(rosters):
                rosters[team] = rosters[team][:7]

        return rosters

    def _find_heading_by_text(self, soup: BeautifulSoup, needle: str) -> Any | None:
        for heading in soup.find_all(["h2", "h3", "h4"]):
            text = self._clean_text(heading.get_text(" ", strip=True))
            if needle.lower() in text.lower():
                return heading
        return None

    def _collect_section_lines(self, heading: Any) -> list[str]:
        lines: list[str] = []
        for node in heading.next_siblings:
            if getattr(node, "name", None) in {"h2", "h3", "h4"}:
                break
            if getattr(node, "get_text", None):
                raw = node.get_text("\n", strip=True)
                for line in raw.splitlines():
                    cleaned = self._clean_text(line)
                    if cleaned:
                        lines.append(cleaned)
        return lines

    def _collect_section_links(self, heading: Any) -> list[Any]:
        links: list[Any] = []
        for node in heading.next_siblings:
            if getattr(node, "name", None) in {"h2", "h3", "h4"}:
                break
            if getattr(node, "find_all", None):
                links.extend(node.find_all("a"))
        return links

    def _detect_active_stage(self, today: date, stage_windows: list[dict[str, Any]]) -> str:
        if not stage_windows:
            return "Unknown"

        parsed: list[ParsedStageWindow] = []
        for item in stage_windows:
            try:
                parsed.append(
                    ParsedStageWindow(
                        name=item["name"],
                        start=datetime.fromisoformat(item["start"]).date(),
                        end=datetime.fromisoformat(item["end"]).date(),
                    )
                )
            except Exception:
                continue

        if not parsed:
            return "Unknown"

        parsed.sort(key=lambda x: x.start)

        if today < parsed[0].start:
            return "Upcoming"
        if today > parsed[-1].end:
            return "Finished"

        for stage in parsed:
            if stage.start <= today <= stage.end:
                return stage.name

        return "Live"

    def _normalize_ordinals(self, text: str) -> str:
        return _ORDINAL_RE.sub(r"\1", text)

    def _clean_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _safe_parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None

    def _participants_count(self, participants: dict[str, Any]) -> int:
        return sum(len(v) for v in participants.values() if isinstance(v, list))

    def _participants_from_fallback_teams(self, teams: list[str]) -> dict[str, list[str]]:
        # Prefer filling Stage 1 first in fallback mode because this stage has the broadest invite pool.
        stage_1 = teams[:16]
        remaining = teams[16:24]
        stage_2 = remaining[:8]
        stage_3 = teams[24:32]
        return {
            "stage_1": stage_1,
            "stage_2": stage_2,
            "stage_3": stage_3,
        }

    def _extract_score_lines(self, text: str) -> list[str]:
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = self._clean_text(raw_line)
            if not line or len(line) > 120:
                continue
            if not _SCORE_TOKEN_RE.search(line):
                continue
            if any(skip in line for skip in ("Cookie", "viewers", "Privacy", "VRS", "http")):
                continue
            if any(block in line.lower() for block in _BLOCKED_EVENT_KEYWORDS):
                continue
            lines.append(line)
        return lines

    def _collect_bracket_lines(self, text: str) -> list[str]:
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = self._clean_text(raw_line)
            if not line or len(line) > 120:
                continue
            lower = line.lower()
            if any(block in lower for block in _BLOCKED_EVENT_KEYWORDS):
                continue
            if "Final" in line or "Semifinal" in line or "Quarterfinal" in line:
                lines.append(line)
                continue
            if "TBD" in line and ("vs" in line.lower() or "Final" in line):
                lines.append(line)
                continue
            if _SCORE_TOKEN_RE.search(line) and "vs" in line.lower():
                lines.append(line)
        return lines

    def _extract_hltv_teams(self, soup: BeautifulSoup) -> list[str]:
        section_heading = self._find_heading_by_text(soup, "Teams attending")
        if section_heading:
            scoped_teams: list[str] = []
            for link in self._collect_section_links(section_heading):
                href = link.get("href", "")
                if not _HLTV_TEAM_HREF_RE.match(href):
                    continue
                name = self._clean_text(link.get_text(" ", strip=True))
                if not name or len(name) > 40:
                    continue
                if any(block in name.lower() for block in _BLOCKED_EVENT_KEYWORDS):
                    continue
                scoped_teams.append(name)
            if scoped_teams:
                return scoped_teams

        teams: list[str] = []
        for link in soup.find_all("a"):
            href = link.get("href", "")
            if not _HLTV_TEAM_HREF_RE.match(href):
                continue
            name = self._clean_text(link.get_text(" ", strip=True))
            if not name or len(name) > 40:
                continue
            if any(block in name.lower() for block in _BLOCKED_EVENT_KEYWORDS):
                continue
            teams.append(name)
        return teams

    def _is_expected_cologne_page(self, text: str) -> bool:
        lower = text.lower()
        has_expected = any(keyword in lower for keyword in _COLOGNE_EVENT_KEYWORDS)
        has_blocked = any(keyword in lower for keyword in _BLOCKED_EVENT_KEYWORDS)
        return has_expected and not has_blocked

    def _match_team_line(self, line: str, teams: list[str]) -> str | None:
        normalized = self._clean_text(line)
        for team in teams:
            if normalized == team:
                return team
            if normalized.endswith(team):
                return team
        return None

    def _extract_player_from_roster_line(self, line: str) -> str | None:
        normalized = self._clean_text(line)
        if normalized.startswith("VRS "):
            return None
        match = _ROSTER_LINE_RE.match(normalized)
        if not match:
            return None
        player = match.group(2)
        if player.lower() in {"dnp", "sub"}:
            return None
        return player

    def _update_score_fingerprint(self, lines: list[str]) -> bool:
        serialized = "\n".join(lines)
        new_fingerprint = hashlib.sha1(serialized.encode("utf-8")).hexdigest()
        old_fingerprint = self._last_scores_fingerprint
        self._last_scores_fingerprint = new_fingerprint
        return old_fingerprint is not None and old_fingerprint != new_fingerprint

    def _uniq(self, lines: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for line in lines:
            if line in seen:
                continue
            seen.add(line)
            output.append(line)
        return output

    def _stage_to_detail_page(self, active_stage: str) -> str | None:
        mapping = {
            "Stage 1": f"{self._liquipedia_page}/Stage_1",
            "Stage 2": f"{self._liquipedia_page}/Stage_2",
            "Stage 3": f"{self._liquipedia_page}/Stage_3",
            "Playoffs": f"{self._liquipedia_page}/Playoffs",
        }
        return mapping.get(active_stage)

    def _hltv_urls_for_stage(self, active_stage: str) -> list[str]:
        stage_map = {
            "Stage 1": [HLTV_EVENT_URLS[0]],
            "Stage 2": [HLTV_EVENT_URLS[1]],
            "Stage 3": [HLTV_EVENT_URLS[2]],
            "Playoffs": [HLTV_EVENT_URLS[2]],
        }
        return stage_map.get(active_stage, [HLTV_EVENT_URLS[2]])

    def _build_fallback_payload(self, now: datetime) -> dict[str, Any]:
        payload = dict(self._last_payload or {})
        sources = dict(payload.get("sources", {}))
        liquipedia = dict(sources.get("liquipedia", {}))
        liquipedia["ok"] = False
        liquipedia["error"] = "Using cached data due to rate limiting"
        sources["liquipedia"] = liquipedia
        payload["sources"] = sources
        payload["updated_at"] = now.isoformat()
        return payload

    async def _build_emergency_payload(self, now: datetime, liquipedia_error: str) -> dict[str, Any]:
        hltv_signal: dict[str, Any] = {
            "enabled": self._include_hltv_signal,
            "ok": False,
            "error": "disabled",
            "signal_lines": [],
            "bracket_lines": [],
            "teams": [],
        }
        if self._include_hltv_signal:
            try:
                hltv_signal = await self._async_fetch_hltv_signal("Unknown")
            except Exception:
                pass

        score_lines = self._uniq(list(hltv_signal.get("signal_lines", [])))[:80]
        score_change_detected = self._update_score_fingerprint(score_lines)
        if score_change_detected:
            self._last_score_change = now.isoformat()

        payload = {
            "updated_at": now.isoformat(),
            "active_stage": self._detect_active_stage(now.date(), _DEFAULT_STAGE_WINDOWS),
            "overview": {
                "source_mode": "emergency",
            },
            "stage_windows": _DEFAULT_STAGE_WINDOWS,
            "participants": self._participants_from_fallback_teams(hltv_signal.get("teams", [])),
            "team_rosters": {},
            "bracket_lines": hltv_signal.get("bracket_lines", []),
            "upcoming_matches": [],
            "live_matches": [],
            "completed_matches": [{"name": line, "status": "finished"} for line in score_lines],
            "next_match": None,
            "matches_today": 0,
            "score_change_detected": score_change_detected,
            "last_score_change": self._last_score_change,
            "score_signal_lines": score_lines,
            "sources": {
                "liquipedia": {
                    "page": self._liquipedia_page,
                    "ok": False,
                    "error": liquipedia_error,
                    "policy_mode": "mediawiki_api_only",
                },
                "hltv": {
                    "enabled": hltv_signal.get("enabled", False),
                    "ok": hltv_signal.get("ok", False),
                    "error": hltv_signal.get("error"),
                    "strict_filter": hltv_signal.get("strict_filter", True),
                    "used_urls": hltv_signal.get("used_urls", []),
                    "dropped_urls": hltv_signal.get("dropped_urls", []),
                    "teams_detected": len(hltv_signal.get("teams", [])),
                    "bracket_lines_detected": len(hltv_signal.get("bracket_lines", [])),
                },
            },
        }
        self._last_payload = payload
        return payload
