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

3. **PandaScore**
   - Aktuell **nicht aktiv** in dieser Integration.
   - Hintergrund: Ohne stabile API-Auth/Rate-Limit-Strategie fuehrte PandaScore in Tests zu unzuverlaessigen Daten.
   - Fokus ist daher auf Liquipedia + HLTV mit starkem Fallback gelegt.

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

Es gibt jetzt zwei Wege, je nachdem wo du in HA YAML bearbeitest:

1. **Komplettes Dashboard (Raw Dashboard YAML):**

- `dashboard/iem_cologne_dashboard.yaml`

2. **Einzelne Ansicht (genau dein Weg: Ansicht -> 3 Punkte -> in YAML bearbeiten):**

- `dashboard/iem_cologne_view_match_center.yaml`
- `dashboard/iem_cologne_view_sources.yaml`
- `dashboard/iem_cologne_view_mobile.yaml`

### Wichtiger Unterschied

- Wenn du in HA auf einer **einzelnen Ansicht** auf "in YAML bearbeiten" gehst, darf dort **kein** `title:` + `views:` Root vom ganzen Dashboard stehen.
- Genau dafuer sind die drei `iem_cologne_view_*.yaml` Dateien gebaut.

### Schritt-fuer-Schritt fuer deinen konkreten HA-Flow

1. In HA eine neue Ansicht anlegen.
2. In dieser Ansicht oben rechts auf **3 Punkte -> in YAML bearbeiten**.
3. Kompletten Inhalt ersetzen durch eine der Dateien:
   - `iem_cologne_view_match_center.yaml` (Hauptansicht)
   - `iem_cologne_view_sources.yaml` (Quellen/Diagnose)
   - `iem_cologne_view_mobile.yaml` (Mobile)
4. Speichern.
5. Fuer jede weitere Ansicht wiederholen.
6. Browser mit **Strg+F5** hart neu laden.
7. Falls noch leer: Integration unter **Geraete & Dienste** neu laden.

### Was du danach sofort ablesen kannst

- **Datenmodus**: Vollmodus oder Fallback.
- **Teams gesamt / Stage Teams**: erkannte Teilnehmer je Stage.
- **Team Roster (Auszug)**: nur sichtbar, wenn Roster extrahiert wurde.
- **Bracket / Playoffs Signale**: erkannte K.-o.-Linien.
- **Source State**: Liquipedia/HLTV Status inkl. Fehlertext.

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

## Fehlerbild: "Quellen sind durcheinander" (z. B. Atlanta statt Cologne)

Wenn du vermischte Event-Daten siehst, geh exakt so vor:

1. **Integration-Optionen pruefen**
   - Einstellungen -> Geraete & Dienste -> IEM Cologne Major -> Konfigurieren
   - `liquipedia_page` muss sein: `Intel_Extreme_Masters/2026/Cologne`
   - Speichern.

2. **Integration neu laden**
   - In derselben Integrationskachel auf "Neu laden".

3. **Source State ansehen**
   - In der Quellen-Ansicht auf folgende Werte achten:
   - `HLTV Strict Filter: True`
   - `HLTV verwendete URLs` zeigt nur Cologne-Event-Links
   - `HLTV verworfene URLs` zeigt ggf. ausgefilterte falsche Seiten

4. **Browser hart neu laden**
   - `Strg+F5`, damit keine alte Lovelace-Ansicht gecacht bleibt.
