"""One-shot: fetch all 32 rosters from the IEM Cologne 2026 main tournament
page wikitext (one HTTP request, no rate-limit pain).

Liquipedia tournament pages use {{TeamCard|...}} templates that contain
|p1=..|p2=..|p3=..|p4=..|p5=.. for the 5-player active roster, plus a
team name parameter.

Run:  python tools/fetch_rosters_from_main_page.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request

LIQUIPEDIA_API_URL = "https://liquipedia.net/counterstrike/api.php"
USER_AGENT = (
    "IEMCologneMajorHA/0.2 "
    "(https://github.com/terkelt/Major_HA/issues; community integration)"
)
MAIN_PAGE = "Intel_Extreme_Masters/2026/Cologne"


def fetch_wikitext(page: str) -> str:
    import gzip
    import io

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
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        data = json.loads(raw.decode("utf-8"))
    page_data = data["query"]["pages"][0]
    if page_data.get("missing"):
        raise ValueError(f"page missing: {page}")
    return page_data["revisions"][0]["slots"]["main"]["content"]


# Match {{TeamCard|...}} blocks (handle nested braces by counting)
def split_teamcards(wikitext: str) -> list[str]:
    cards: list[str] = []
    i = 0
    n = len(wikitext)
    while i < n:
        idx = wikitext.find("{{TeamCard", i)
        if idx < 0:
            break
        # find matching }}
        depth = 0
        j = idx
        while j < n:
            if wikitext[j:j + 2] == "{{":
                depth += 1
                j += 2
            elif wikitext[j:j + 2] == "}}":
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        cards.append(wikitext[idx:j])
        i = j
    return cards


def parse_card(card: str) -> tuple[str | None, list[str]]:
    # Team name: |team=... or |teamname=... or |1=team
    team = None
    for key in ("team", "teamname", "name"):
        m = re.search(rf"\|\s*{key}\s*=\s*([^\|\n\}}]+)", card, re.IGNORECASE)
        if m:
            team = m.group(1).strip()
            break

    players: list[str] = []
    # p1..p5 or player1..player5
    for n in range(1, 8):
        m = re.search(rf"\|\s*p{n}\s*=\s*([^\|\n\}}]+)", card)
        if not m:
            m = re.search(rf"\|\s*player{n}\s*=\s*([^\|\n\}}]+)", card)
        if m:
            name = m.group(1).strip()
            # strip wiki links
            name = re.sub(r"\[\[([^\]\|]+)(\|[^\]]+)?\]\]", r"\1", name).strip()
            if name and name not in players:
                players.append(name)
    return team, players[:5]


def main() -> int:
    print(f"Fetching {MAIN_PAGE} ...")
    wt = fetch_wikitext(MAIN_PAGE)
    print(f"Got {len(wt)} chars of wikitext\n")

    cards = split_teamcards(wt)
    print(f"Found {len(cards)} TeamCard blocks\n")

    results: dict[str, list[str]] = {}
    skipped: list[str] = []
    for i, card in enumerate(cards, start=1):
        team, players = parse_card(card)
        if team and players:
            results[team] = players
            print(f"[{i:2}] {team:<25} {', '.join(players)}")
        else:
            skipped.append(f"card #{i}: team={team!r} players={players}")
            print(f"[{i:2}] SKIPPED team={team!r} players={players}")

    print("\n" + "=" * 70)
    print(f"Parsed {len(results)} teams with rosters")
    print("=" * 70)

    print("\n# Paste into const.py:")
    print("HARDCODED_ROSTERS: dict[str, list[str]] = {")
    for team in sorted(results):
        players_repr = ", ".join(f'"{p}"' for p in results[team])
        print(f'    "{team}": [{players_repr}],')
    print("}")

    if skipped:
        print(f"\nSkipped {len(skipped)} cards:")
        for s in skipped:
            print(f"  - {s}")

    # Also dump raw cards for inspection if low yield
    if len(results) < 16:
        print("\n--- DUMP first 2 cards for debugging ---")
        for c in cards[:2]:
            print(c[:500])
            print("---")

    return 0


if __name__ == "__main__":
    sys.exit(main())
