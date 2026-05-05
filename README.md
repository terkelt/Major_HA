# Home Assistant Integration: IEM Cologne Major 2026 (CS2)

## HACS Installation (direkt)

1. Repository nach GitHub pushen (oeffentlich oder privat).
2. In Home Assistant: **HACS -> Integrations -> 3 Punkte oben rechts -> Custom repositories**.
3. Repository-URL eintragen und **Category: Integration** waehlen.
4. Danach in HACS unter Integrations nach **IEM Cologne Major** suchen und installieren.
5. Home Assistant neu starten.
6. In HA: **Einstellungen -> Geraete & Dienste -> Integration hinzufuegen** und **IEM Cologne Major** auswaehlen.

Hinweis: Die Datei `hacs.json` ist bereits enthalten, damit HACS das Repo als Integration erkennt.

Diese Custom-Integration stellt dir im Home Assistant Dashboard alle zentralen Daten zum **IEM Cologne Major 2026** bereit:

- Turnierphase (Upcoming, Stage 1, Stage 2, Stage 3, Playoffs, Finished)
- Naechstes Match
- Matches heute
- Teilnehmer je Stage (Stage 1, Stage 2, Stage 3)
- Team-Roster (wenn aus Quellen extrahierbar)
- Bracket-/Playoff-Signale
- Score-Signale und Aenderungserkennung fuer neue Ergebnisse
- Letzte erkannte Score-Aenderung als Zeitstempel
- Vollstaendige Rohdaten als Attribut-Payload fuer eigene Dashboard-Karten

## Datenquellen

1. **Liquipedia Counter-Strike API (MediaWiki parse API)**
   - Seite: `Intel_Extreme_Masters/2026/Cologne`
   - Nutzt Turnierstruktur, Stages, Teilnehmer und Ergebnis-Signale
2. **HLTV Eventseiten (Signalquelle)**
   - Fuer zusaetzliche Score-Signaltexte und schnellere Erkennung von Aenderungen
   - Keine Live-API erforderlich

## Verifizierte Event-Infos (Stand: 02.05.2026)

Aus den recherchierten Quellen:

- Start: **02. Juni 2026**
- Stage 1: **02.-05. Juni 2026**
- Stage 2: **06.-09. Juni 2026**
- Stage 3: **11.-15. Juni 2026**
- Playoffs: **18.-21. Juni 2026**
- Event-Referenzen:
  - Liquipedia: Intel Extreme Masters/2026/Cologne
  - HLTV Events (IEM Cologne Major 2026 inkl. Stage 1/2)

## Installation

1. Lege den Ordner `custom_components/iem_cologne_major` in deiner Home-Assistant-Konfiguration ab.
2. Starte Home Assistant neu.
3. Gehe auf **Einstellungen -> Geraete & Dienste -> Integration hinzufuegen**.
4. Suche nach **IEM Cologne Major**.
5. Konfiguriere:
   - `liquipedia_page` (Standard: `Intel_Extreme_Masters/2026/Cologne`)
   - `update_interval` in Minuten
   - `include_finished_matches`
   - `include_hltv_signal`

## HACS Release-Hinweis

Fuer Updates in HACS empfiehlt sich ein Git-Tag pro Version (z. B. `v0.1.0`).

## Entitaeten

- `sensor.iem_cologne_phase`
- `sensor.iem_cologne_score_signals`
- `sensor.iem_cologne_last_score_change`
- `sensor.iem_cologne_next_match`
- `sensor.iem_cologne_matches_today`
- `sensor.iem_cologne_participants`
- `sensor.iem_cologne_all_data`

## Dashboard

Eine fertige Lovelace-View liegt unter:

- `dashboard/iem_cologne_dashboard.yaml`

Du kannst diese YAML als neue Raw-View in dein Dashboard uebernehmen.

Enthalten sind drei Views:

- Match Center (Desktop/Tablet)
- Quellen
- Mobile (kompakt mit grossen Kacheln und Score-Feed)

## Hinweise

- Diese Variante arbeitet bewusst **ohne Live-API**.
- Stattdessen wird mit kurzem Polling-Intervall gearbeitet. Wegen Rate-Limits ist **5 Minuten** der sinnvolle Default; 1-2 Minuten sollten nur vorsichtig getestet werden.
- HLTV wurde als Signalquelle beruecksichtigt, kann aber je nach Site-Layout/Consent-Banner weniger strukturierte Daten liefern als Liquipedia.
- Die Integration ist als Community-Custom-Component gebaut.

### Was das Dashboard konkret bedeutet

- **Datenmodus** zeigt, ob die Integration im Vollmodus (Liquipedia-Strukturdaten) oder im Fallback-/Emergency-Modus (Signal-/Cache-Daten) laeuft.
- **Teams gesamt** ist die Summe aus Stage 1/2/3 Teilnehmerlisten.
- **Team Roster (Auszug)** erscheint nur, wenn aus den Quellen Spielerzeilen robust erkannt werden.
- **Bracket / Playoffs Signale** sind erkannte K.-o.-Hinweise, keine vollstaendige grafische Bracket-Engine.
