"""One-off script: fetch all 32 team rosters from Liquipedia and print as
Python dict for hardcoding into const.py.

Run:  python tools/fetch_rosters_once.py
Respects Liquipedia 1 req / 30 s rate limit -> ~16 minutes total.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request

LIQUIPEDIA_API_URL = "https://liquipedia.net/counterstrike/api.php"
USER_AGENT = (
    "IEMCologneMajorHA/0.2 "
    "(https://github.com/terkelt/Major_HA/issues; community integration)"
)

SLUGS: dict[str, str] = {
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

ID_RE = re.compile(r"\|\s*id\s*=\s*([^\|\}\n]+)")


def fetch_wikitext(page: str) -> str:
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": page,
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
    }
    url = f"{LIQUIPEDIA_API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return ""
    page_data = pages[0]
    if page_data.get("missing"):
        raise ValueError(f"page missing: {page}")
    revs = page_data.get("revisions", [])
    if not revs:
        return ""
    return revs[0].get("slots", {}).get("main", {}).get("content", "") or ""


def parse_active_players(wikitext: str) -> list[str]:
    """Extract player IDs from the ACTIVE roster section only.

    Liquipedia team pages list multiple rosters (active, inactive, former, female,
    academy, etc). We want only the active main squad – stop at the first
    'Former' / 'Inactive' / 'Academy' / 'Female' / 'Coaching Staff' header.
    """
    # Cut at first non-active section heading
    cut_re = re.compile(
        r"==\s*(Former|Inactive|Academy|Female|Coaching\s*Staff|Substitute"
        r"|Trial|Streamers?|Content\s*Creators?|Notable)[^=]*==",
        re.IGNORECASE,
    )
    m = cut_re.search(wikitext)
    active_section = wikitext[: m.start()] if m else wikitext

    seen: list[str] = []
    for match in ID_RE.finditer(active_section):
        name = match.group(1).strip()
        # Clean wiki markup
        name = re.sub(r"\[\[([^\]\|]+)(\|[^\]]+)?\]\]", r"\1", name)
        name = name.strip()
        if not name or name in seen:
            continue
        seen.append(name)
        if len(seen) >= 5:  # CS uses 5-man rosters
            break
    return seen


def main() -> int:
    results: dict[str, list[str]] = {}
    failures: dict[str, str] = {}
    teams = list(SLUGS.items())
    total = len(teams)

    for i, (team_name, slug) in enumerate(teams, start=1):
        print(f"[{i:2}/{total}] {team_name:<20} ({slug}) ... ", end="", flush=True)
        try:
            wikitext = fetch_wikitext(slug)
            players = parse_active_players(wikitext)
            if players:
                results[team_name] = players
                print(f"OK ({len(players)}) -> {', '.join(players)}")
            else:
                failures[team_name] = "no players parsed"
                print("EMPTY")
        except Exception as exc:
            failures[team_name] = str(exc)
            print(f"FAIL: {exc}")

        if i < total:
            time.sleep(31)  # respect 1 req / 30s

    print("\n\n" + "=" * 70)
    print("RESULT – paste into const.py")
    print("=" * 70)
    print("HARDCODED_ROSTERS: dict[str, list[str]] = {")
    for team in SLUGS:
        if team in results:
            players_repr = ", ".join(f'"{p}"' for p in results[team])
            print(f'    "{team}": [{players_repr}],')
        else:
            print(f'    # "{team}": [],  # FAILED: {failures.get(team, "?")}')
    print("}")
    print("=" * 70)
    print(f"\nSuccess: {len(results)}/{total}    Failed: {len(failures)}")
    if failures:
        print("Failures:")
        for t, e in failures.items():
            print(f"  - {t}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
