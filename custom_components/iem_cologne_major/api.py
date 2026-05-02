"""API client for IEM Cologne Major data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import logging
import re
from typing import Any

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

    async def async_fetch_data(self) -> dict[str, Any]:
        """Fetch and merge data from Liquipedia and optional HLTV signal pages."""
        liquipedia_data = await self._async_fetch_liquipedia_bundle()
        hltv_signal: dict[str, Any] = {
            "enabled": self._include_hltv_signal,
            "ok": True,
            "error": None,
            "signal_lines": [],
        }
        if self._include_hltv_signal:
            hltv_signal = await self._async_fetch_hltv_signal()

        now = datetime.now(UTC)
        active_stage = self._detect_active_stage(now.date(), liquipedia_data.get("stage_windows", []))

        upcoming_matches = liquipedia_data.get("upcoming_matches", [])
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

        return {
            "updated_at": now.isoformat(),
            "active_stage": active_stage,
            "overview": liquipedia_data.get("overview", {}),
            "stage_windows": liquipedia_data.get("stage_windows", []),
            "participants": liquipedia_data.get("participants", {}),
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
                },
                "hltv": {
                    "enabled": hltv_signal.get("enabled", False),
                    "ok": hltv_signal.get("ok", False),
                    "error": hltv_signal.get("error"),
                },
            },
        }

    async def _async_fetch_liquipedia_bundle(self) -> dict[str, Any]:
        html = await self._async_fetch_liquipedia_page_html(self._liquipedia_page)
        soup = BeautifulSoup(html, "html.parser")

        overview = self._parse_infobox(soup)
        stage_windows = self._parse_stage_windows(soup)
        participants = self._parse_participants(soup)
        upcoming_matches = self._parse_upcoming_matches(soup)
        score_signal_lines = await self._async_fetch_liquipedia_score_lines()

        return {
            "overview": overview,
            "stage_windows": stage_windows,
            "participants": participants,
            "upcoming_matches": upcoming_matches,
            "score_signal_lines": score_signal_lines,
        }

    async def _async_fetch_liquipedia_page_html(self, page: str) -> str:
        params = {
            "action": "parse",
            "page": page,
            "prop": "text",
            "format": "json",
            "formatversion": 2,
        }
        headers = {
            "User-Agent": "HomeAssistant-IEMCologneMajor/0.1 (+community project)",
        }

        async with self._session.get(LIQUIPEDIA_API_URL, params=params, headers=headers, timeout=20) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if "parse" not in data or "text" not in data["parse"]:
            raise ValueError("Liquipedia response did not contain parse text")

        return data["parse"]["text"]

    async def _async_fetch_liquipedia_score_lines(self) -> list[str]:
        pages = [
            f"{self._liquipedia_page}/Stage_1",
            f"{self._liquipedia_page}/Stage_2",
            f"{self._liquipedia_page}/Stage_3",
            f"{self._liquipedia_page}/Playoffs",
        ]
        lines: list[str] = []
        for page in pages:
            try:
                html = await self._async_fetch_liquipedia_page_html(page)
            except Exception:
                continue

            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text("\n", strip=True)
            lines.extend(self._extract_score_lines(text))

        return self._uniq(lines)[:60]

    async def _async_fetch_hltv_signal(self) -> dict[str, Any]:
        bundle: dict[str, Any] = {
            "enabled": True,
            "ok": True,
            "error": None,
            "signal_lines": [],
        }
        try:
            lines: list[str] = []
            headers = {
                "User-Agent": "HomeAssistant-IEMCologneMajor/0.1 (+community project)",
            }
            for url in HLTV_EVENT_URLS:
                async with self._session.get(url, headers=headers, timeout=20) as resp:
                    resp.raise_for_status()
                    html = await resp.text()

                soup = BeautifulSoup(html, "html.parser")
                text = soup.get_text("\n", strip=True)
                lines.extend(self._extract_score_lines(text))

            bundle["signal_lines"] = self._uniq(lines)[:40]
            return bundle
        except Exception as err:
            _LOGGER.warning("HLTV signal fetch failed: %s", err)
            bundle["ok"] = False
            bundle["error"] = str(err)
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
            lines.append(line)
        return lines

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
