"""Holt Ergebnisse und Spielpläne für die internationalen Ligen und schreibt sie
kompakt nach data/fussball/. Ohne Schlüssel, ohne Zusatzpakete.

Zwei Quellen:
  * openfootball (GitHub) für die großen Ligen. Enthält den kompletten
    Spielplan der laufenden Saison, also auch kommende Partien. Schnell und
    ohne Drosselung, daher jeden Lauf in voller Tiefe.
  * football-data.co.uk für alles Weitere. Dort gibt es nur gespielte
    Partien plus eine wöchentliche Datei mit kommenden Spielen, und der
    Server drosselt viele Anfragen in kurzer Folge (HTTP 429).

Priorität: aktuelle Form und der heutige Spielplan zählen mehr als lange
Historie. Deshalb holt JEDER Lauf für JEDE football-data-Liga die laufende
Saison und alle Ansetzungen frisch (das ist, was für "heute" und für die
Form der letzten Spiele zählt). Die zusätzliche Vorsaison, die nur für sehr
frühe Saisonphasen als Anhaltspunkt dient, wird dagegen nicht bei jedem Lauf
neu geholt, sondern reihum: pro Lauf ein paar Ligen, bis irgendwann alle
einmal einen zwischengespeicherten historischen Datensatz haben (Ordner
data/fussball/.cache, wird mit eingecheckt). So füllt sich die Seite von
Anfang an über die volle Breite mit aktuellen Daten und wird nach und nach
auch in die Tiefe vollständiger, ohne dass ein einzelner Lauf an der
Drosselung von football-data.co.uk scheitert.

Seit Version 2 sind die gespielten Partien aller Hauptligen aus
football-data.co.uk, weil nur dort Spielstatistik steht (Schüsse, Ecken,
Karten, Fouls). openfootball ergänzt die Ansetzungen der großen Ligen; die
Vereinsnamen beider Quellen werden über gemeinsame Ergebnisse abgeglichen.

Die deutschen Ligen kommen live von OpenLigaDB. Ihre Quoten stehen getrennt in
odds_de.json (fussball_quoten_de.py) und werden im Browser zugeordnet.

Am Ende führt fussball_teams.py Teams zusammen, die aus verschiedenen Quellen
mit verschiedenen IDs kommen (Europapokal, Pokale, Auf- und Absteiger).

Aufruf:  python3 tools/fussball_fetch.py [--out data/fussball] [--seasons 2]
"""

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

BASE = "https://www.football-data.co.uk"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/csv,application/json,text/plain,*/*",
    "Accept-Language": "en-GB,en;q=0.9,de;q=0.8",
    "Referer": "https://football-data.co.uk/matches.php",
}
# Protokoll für die Fehlersuche, landet in meta.json
DIAG = []
# Team-ID -> Name in der Quelle, für den Namensabgleich zwischen den Quellen
NAMES = {}
UK = ZoneInfo("Europe/London")

# Hauptligen: eine Datei je Saison unter /mmz4281/<jjjj>/<code>.csv
MAIN = [
    ("E0", "Premier League", "England", 1), ("E1", "Championship", "England", 2),
    ("N1", "Eredivisie", "Niederlande", 1),
    ("E2", "League One", "England", 3), ("E3", "League Two", "England", 4),
    ("EC", "National League", "England", 5),
    ("SC0", "Premiership", "Schottland", 1), ("SC1", "Championship", "Schottland", 2),
    ("SC2", "League One", "Schottland", 3), ("SC3", "League Two", "Schottland", 4),
    ("I1", "Serie A", "Italien", 1), ("I2", "Serie B", "Italien", 2),
    ("SP1", "La Liga", "Spanien", 1), ("SP2", "La Liga 2", "Spanien", 2),
    ("F1", "Ligue 1", "Frankreich", 1), ("F2", "Ligue 2", "Frankreich", 2),
    ("B1", "Pro League", "Belgien", 1),
    ("P1", "Liga Portugal", "Portugal", 1),
    ("T1", "Süper Lig", "Türkei", 1),
    ("G1", "Super League", "Griechenland", 1),
]
# Deutsche Ligen: Spiele, Torschützen und Wappen kommen live von OpenLigaDB. Von
# football-data.co.uk werden nur Ecken, Karten und Schüsse übernommen und in
# stats_de.json abgelegt; die Seite ordnet sie den OpenLigaDB-Spielen zu.
GERMAN_STATS = [("D1", "bl1", "Bundesliga"), ("D2", "bl2", "2. Bundesliga")]

# Zusatzligen: eine Datei mit allen Saisons unter /new/<code>.csv
EXTRA = [
    ("ARG", "Primera División", "Argentinien", 1), ("AUT", "Bundesliga", "Österreich", 1),
    ("BRA", "Série A", "Brasilien", 1), ("CHN", "Super League", "China", 1),
    ("DNK", "Superliga", "Dänemark", 1), ("FIN", "Veikkausliiga", "Finnland", 1),
    ("IRL", "Premier Division", "Irland", 1), ("JPN", "J1 League", "Japan", 1),
    ("MEX", "Liga MX", "Mexiko", 1), ("NOR", "Eliteserien", "Norwegen", 1),
    ("POL", "Ekstraklasa", "Polen", 1), ("ROU", "Liga I", "Rumänien", 1),
    ("RUS", "Premier Liga", "Russland", 1), ("SWE", "Allsvenskan", "Schweden", 1),
    ("SWZ", "Super League", "Schweiz", 1), ("USA", "MLS", "USA", 1),
]

FIXTURE_FILES = [
    ("main", ["https://football-data.co.uk/fixtures.csv", "https://www.football-data.co.uk/fixtures.csv"]),
    ("extra", ["https://football-data.co.uk/new_league_fixtures.csv",
               "https://www.football-data.co.uk/new_league_fixtures.csv",
               "https://football-data.co.uk/fixtures_new_leagues.csv"]),
]

OF_BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
# Ligen mit vollem Spielplan (Ergebnisse und kommende Partien) aus openfootball.
# Diese Ligen werden NICHT bei football-data geholt, damit die Vereinsnamen
# aus einer einzigen Quelle stammen.
# Anstoßzeiten stehen dort in der jeweiligen Landeszeit.
OPENFOOTBALL = [
    ("E0", "en.1", "Premier League", "England", 1, "Europe/London"),
    ("E1", "en.2", "Championship", "England", 2, "Europe/London"),
    ("SP1", "es.1", "La Liga", "Spanien", 1, "Europe/Madrid"),
    ("I1", "it.1", "Serie A", "Italien", 1, "Europe/Rome"),
    ("F1", "fr.1", "Ligue 1", "Frankreich", 1, "Europe/Paris"),
    ("N1", "nl.1", "Eredivisie", "Niederlande", 1, "Europe/Amsterdam"),
    ("P1", "Liga Portugal", "Liga Portugal", "Portugal", 1, "Europe/Lisbon"),
]
OPENFOOTBALL[6] = ("P1", "pt.1", "Liga Portugal", "Portugal", 1, "Europe/Lisbon")
# Weitere Ligen, die openfootball manchmal führt. Sie werden bei jedem Lauf
# geprüft und übernommen, sobald die laufende Saison dort vorhanden ist.
OF_CANDIDATES = [
    ("E2", "en.3", "League One", "England", 3, "Europe/London"),
    ("E3", "en.4", "League Two", "England", 4, "Europe/London"),
    ("SC0", "sco.1", "Premiership", "Schottland", 1, "Europe/London"),
    ("SP2", "es.2", "La Liga 2", "Spanien", 2, "Europe/Madrid"),
    ("I2", "it.2", "Serie B", "Italien", 2, "Europe/Rome"),
    ("F2", "fr.2", "Ligue 2", "Frankreich", 2, "Europe/Paris"),
    ("B1", "be.1", "Pro League", "Belgien", 1, "Europe/Brussels"),
    ("T1", "tr.1", "Süper Lig", "Türkei", 1, "Europe/Istanbul"),
    ("G1", "gr.1", "Super League", "Griechenland", 1, "Europe/Athens"),
    ("AUT", "at.1", "Bundesliga", "Österreich", 1, "Europe/Vienna"),
    ("SWZ", "ch.1", "Super League", "Schweiz", 1, "Europe/Zurich"),
]


def probe_openfootball(current, delay):
    """Nimmt die Ligen dazu, für die openfootball die laufende Saison führt."""
    tag = f"{current}-{str(current + 1)[2:]}"
    for row in OF_CANDIDATES:
        data = get_json(f"{OF_BASE}/{tag}/{row[1]}.json")
        time.sleep(delay)
        if data and data.get("matches"):
            OPENFOOTBALL.append(row)
            OF_CODES.add(row[0])
            print(f"  zusätzlich aus openfootball: {row[3]} {row[2]}")


OF_CODES = {row[0] for row in OPENFOOTBALL}


def write_json(path, data, ensure_ascii=True):
    """Kompakt und atomar schreiben: erst in eine Temp-Datei, dann umbenennen.
    Bricht der Lauf mittendrin ab, bleibt die alte Datei vollständig."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"), ensure_ascii=ensure_ascii)
    os.replace(tmp, path)


# Antworten, die mehrere Module im selben Lauf brauchen (OpenLigaDB-Spielpläne)
RUN_CACHE = {}


def get_json_once(url):
    """Wie get_json, aber pro Lauf nur einmal geladen."""
    if url not in RUN_CACHE:
        RUN_CACHE[url] = get_json(url)
    return RUN_CACHE[url]


def get_text_once(url):
    """Wie get_text, aber pro Lauf nur einmal geladen (football-data drosselt)."""
    if ("text", url) not in RUN_CACHE:
        RUN_CACHE[("text", url)] = get_text(url)
    return RUN_CACHE[("text", url)]


def get_json(url, tries=3):
    text = get_text(url, tries)
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def get_text(url, tries=5):
    """Lädt eine Textdatei. football-data.co.uk drosselt schnell aufeinander-
    folgende Anfragen mit HTTP 429; darauf wird deutlich länger gewartet als
    bei einem gewöhnlichen Fehler, und ein Retry-After-Header wird beachtet."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as res:
                raw = res.read()
            text = None
            for enc in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                text = raw.decode("utf-8", "replace")
            # Manche Dateien tragen ein Byte-Order-Mark, das sonst im ersten
            # Spaltennamen landet und alle Zeilen unbrauchbar macht.
            text = text.lstrip("\ufeff").lstrip("ï»¿")
            if attempt:
                DIAG.append(f"{url}: nach {attempt} Wartezeit(en) erfolgreich")
            return text
        except urllib.error.HTTPError as err:
            DIAG.append(f"{url}: HTTP {err.code}")
            if err.code == 404:
                return None
            if err.code in (429, 403, 503):
                wait = None
                try:
                    wait = float(err.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    wait = None
                if wait is None:
                    wait = 15 * (attempt + 1) + random.uniform(0, 5)
                DIAG.append(f"{url}: gedrosselt, warte {wait:.0f}s")
                if attempt == tries - 1:
                    return None
                time.sleep(wait)
                continue
            if attempt == tries - 1:
                return None
        except Exception as err:
            DIAG.append(f"{url}: {type(err).__name__} {err}")
            if attempt == tries - 1:
                return None
        time.sleep(2 * (attempt + 1) + random.uniform(0, 1))
    return None


def ident(text, bits=45):
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest(), 16) % (1 << bits)


def parse_date(value, time_value, tz=None):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            day = dt.datetime.strptime(value, fmt)
            break
        except ValueError:
            day = None
    if day is None:
        return None
    hour, minute = 15, 0
    tv = (time_value or "").strip()
    if ":" in tv:
        try:
            hour, minute = int(tv.split(":")[0]), int(tv.split(":")[1])
        except ValueError:
            pass
    if not (0 <= hour <= 24 and 0 <= minute <= 59):
        hour, minute = 15, 0
    # Über timedelta, damit auch "24:00" (Mitternacht am Folgetag) geht
    local = day.replace(tzinfo=tz or UK) + dt.timedelta(hours=hour, minutes=minute)
    return int(local.timestamp() * 1000)


def num(value):
    value = (value or "").strip()
    if value in ("", "NA", "-"):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def season_start(label):
    """'2024/2025' -> 2024, '2024' -> 2024"""
    label = (label or "").strip()
    if "/" in label:
        label = label.split("/")[0]
    try:
        return int(label)
    except ValueError:
        return None


def odds(value):
    """Dezimalquote als Zahl, oder None. Unrealistische Werte (Tippfehler in
    der Quelle, 0 oder extrem hoch) werden verworfen statt verzerrend."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        v = float(value)
    except ValueError:
        return None
    return v if 1.01 <= v <= 100 else None


# Quotenspalten von football-data.co.uk, jeweils in der Reihenfolge der
# Bevorzugung: Durchschnitt vieler Anbieter, dann bet365, dann Pinnacle.
# Die Schlussquoten (kurz vor Anpfiff) bilden die Stärke am besten ab und
# zählen für beendete Spiele; die Zusatzligen führen überhaupt nur diese.
ODDS_CLOSE = [("AvgCH", "AvgCD", "AvgCA"), ("B365CH", "B365CD", "B365CA"), ("PSCH", "PSCD", "PSCA")]
ODDS_OPEN = [("AvgH", "AvgD", "AvgA"), ("B365H", "B365D", "B365A"), ("PSH", "PSD", "PSA")]
O25_CLOSE = ["AvgC>2.5", "B365C>2.5", "PC>2.5"]
O25_OPEN = ["Avg>2.5", "B365>2.5", "P>2.5"]


# Summe der Kehrwerte eines 1X2-Tripels (1 + Marge). Darunter oder darüber ist die Zeile kaputt,
# z. B. Akron–Krasnodar 5,0/4,33/11,73 (Summe 0,52); daraus entstanden falsche Markt-Torerwartungen.
OVERROUND_MIN, OVERROUND_MAX = 0.99, 1.20


def pick_1x2(row, keys):
    """Erstes Anbieter-Tripel, das in dieser Zeile vollständig und plausibel ist."""
    for kh, kd, ka in keys:
        trio = (odds(row.get(kh)), odds(row.get(kd)), odds(row.get(ka)))
        if None not in trio and OVERROUND_MIN <= sum(1 / v for v in trio) <= OVERROUND_MAX:
            return trio
    return None


def row_odds(row, played):
    """(oh, od, oa, o25) einer CSV-Zeile. Beendete Spiele bevorzugen die
    Schlussquoten und fallen sonst auf die bisherigen Quoten zurück; offene
    Spiele nehmen die aktuellen Quoten."""
    order_1x2 = (ODDS_CLOSE + ODDS_OPEN) if played else ODDS_OPEN
    order_o25 = (O25_CLOSE + O25_OPEN) if played else O25_OPEN
    trio = pick_1x2(row, order_1x2) or (None, None, None)
    o25 = next((v for v in (odds(row.get(k)) for k in order_o25) if v is not None), None)
    return trio[0], trio[1], trio[2], o25


def add_match(rows, teams, lg, country, season, row, cols):
    home = (row.get(cols["home"]) or "").strip()
    away = (row.get(cols["away"]) or "").strip()
    if not home or not away:
        return
    ts = parse_date(row.get("Date"), row.get("Time"))
    if ts is None:
        return
    hid, aid = ident(country + "|" + home), ident(country + "|" + away)
    teams.setdefault(str(hid), {"n": home, "s": home, "i": ""})
    teams.setdefault(str(aid), {"n": away, "s": away, "i": ""})
    NAMES[hid] = home
    NAMES[aid] = away
    hg, ag = num(row.get(cols["hg"])), num(row.get(cols["ag"]))
    hh, ha = num(row.get("HTHG")), num(row.get("HTAG"))
    played = hg is not None
    oh, od, oa, o25 = row_odds(row, played)
    # Spielstatistik (nur football-data-Hauptligen): Schüsse, aufs Tor, Ecken,
    # Gelb, Rot, Fouls. Bei kommenden Spielen und anderen Quellen bleibt None.
    stats = [num(row.get(k)) if played else None for k in STAT_KEYS]
    rows.append([
        ident(f"{lg}|{row.get('Date')}|{home}|{away}"), lg, season, ts, hid, aid,
        hg, ag, hh if played else None, ha if played else None,
        oh, od, oa, o25, *stats,
    ])


# Reihenfolge der Statistikspalten in matches.json (siehe COLS)
STAT_KEYS = ["HS", "AS", "HST", "AST", "HC", "AC", "HY", "AY", "HR", "AR", "HF", "AF"]
STAT_COLS = ["hs", "as", "hst", "ast", "hc", "ac", "hy", "ay", "hr", "ar", "hf", "af"]
# "neu" = 1 bei Spielen auf neutralem Platz (Länderspiele, Endspiele)
COLS = ["id", "lg", "season", "t", "h", "a", "hg", "ag", "hh", "ha", "oh", "od", "oa", "o25"] + STAT_COLS + ["neu"]
NSTAT = len(STAT_COLS)
# Ausgabe ohne Spielstatistik
# Ausgabe ohne Spielstatistik. "mlh"/"mla" = aus den Quoten zurückgerechnete
# Torerwartung (fussball_markt.py), angehängt, damit die alten Indizes bleiben.
OUT_COLS = COLS[:14] + ["neu", "mlh", "mla"]


def out_row(r):
    return r[:14] + [r[14 + NSTAT] if len(r) > 14 + NSTAT else None]

MAIN_COLS = {"home": "HomeTeam", "away": "AwayTeam", "hg": "FTHG", "ag": "FTAG"}
EXTRA_COLS = {"home": "Home", "away": "Away", "hg": "HG", "ag": "AG"}


def read_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        rows.append({(k or "").strip().lstrip("\ufeff"): v for k, v in row.items()})
    return rows


def cache_path(cache_dir, name):
    return os.path.join(cache_dir, name)


def load_pointer(cache_dir):
    path = cache_path(cache_dir, "backfill_pointer.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh).get("index", 0)
        except (ValueError, OSError):
            return 0
    return 0


def save_pointer(cache_dir, index):
    with open(cache_path(cache_dir, "backfill_pointer.json"), "w", encoding="utf-8") as fh:
        json.dump({"index": index}, fh)


def fetch_main(out_rows, teams, seasons, delay, cache_dir, current_season, backfill_batch):
    """Die laufende Saison zählt für Form und Ansetzungen und wird für JEDE
    Liga bei JEDEM Lauf neu geholt. Die Vorsaison dient nur als grober
    Anhaltspunkt für den Saisonstart und wird deshalb nur einmal geholt und
    dann zwischengespeichert; noch fehlende Vorsaisons werden reihum in
    kleinen Gruppen nachgeladen, damit ein einzelner Lauf nicht an der
    Drosselung von football-data.co.uk scheitert."""
    os.makedirs(cache_dir, exist_ok=True)
    order = list(MAIN)
    n = len(order)
    pointer = load_pointer(cache_dir) % max(n, 1)
    rotated = order[pointer:] + order[:pointer]

    # Wer in dieser Runde die fehlende Vorsaison nachladen darf.
    backfill_allowed = set()
    quota = backfill_batch
    for code, *_ in rotated:
        missing = any(
            not os.path.exists(cache_path(cache_dir, f"main_{code}_{s}.csv"))
            for s in seasons if s != current_season
        )
        if missing:
            if quota <= 0:
                continue
            backfill_allowed.add(code)
            quota -= 1

    done = []
    backfilled_this_run = []
    for code, name, country, tier in MAIN:
        count = 0
        for season in seasons:
            tag = f"{str(season)[2:]}{str(season + 1)[2:]}"
            cfile = cache_path(cache_dir, f"main_{code}_{season}.csv")
            text = None
            if season == current_season:
                text = get_text_once(f"{BASE}/mmz4281/{tag}/{code}.csv")
                time.sleep(delay + random.uniform(0, delay * 0.4))
            elif os.path.exists(cfile):
                with open(cfile, encoding="utf-8") as fh:
                    text = fh.read()
            elif code in backfill_allowed:
                text = get_text_once(f"{BASE}/mmz4281/{tag}/{code}.csv")
                time.sleep(delay + random.uniform(0, delay * 0.4))
                if text:
                    with open(cfile, "w", encoding="utf-8") as fh:
                        fh.write(text)
                    backfilled_this_run.append(code)
            else:
                continue  # Vorsaison fehlt noch, kommt in einem der nächsten Läufe
            if not text:
                continue
            for row in read_csv(text):
                if not (row.get("Div") or "").strip():
                    continue
                add_match(out_rows, teams, code, country, season, row, MAIN_COLS)
                count += 1
        if count:
            done.append({"id": code, "name": name, "country": country, "tier": tier})
            print(f"  {country} {name} ({code}): {count} Spiele")
        else:
            print(f"  {country} {name} ({code}): keine Daten", file=sys.stderr)
    if backfilled_this_run:
        print(f"  Vorsaison neu geladen für: {', '.join(backfilled_this_run)}")
    save_pointer(cache_dir, (pointer + max(backfill_batch, 1)) % max(n, 1))
    return done


def fetch_extra(out_rows, teams, min_season, delay):
    """Diese Dateien tragen eine eigene 'League'-Spalte. Bisher wurde alles
    unter einer Liga je Land zusammengeworfen; steckt in der Datei aber eine
    zweite Spielklasse (das kommt bei football-data.co.uk gelegentlich vor,
    zum Beispiel eine Wechselklasse oder eine zweite Liga desselben Landes),
    landen deren Vereine fälschlich in derselben Tabelle. Jetzt wird jede
    tatsächlich vorkommende Ligabezeichnung als eigene Liga behandelt: die
    Klasse mit den meisten Zeilen bekommt den bekannten Landesnamen, jede
    weitere erscheint unter ihrem eigenen Namen aus der Quelle, in der
    Regel eine zweite Liga."""
    from collections import Counter
    done = []
    for code, name, country, tier in EXTRA:
        text = get_text(f"{BASE}/new/{code}.csv")
        time.sleep(delay + random.uniform(0, delay * 0.4))
        if not text:
            print(f"  {country} {name} ({code}): keine Daten", file=sys.stderr)
            continue
        rows = [r for r in read_csv(text) if (season_start(r.get("Season")) or -1) >= min_season]
        if not rows:
            print(f"  {country} {name} ({code}): keine Daten", file=sys.stderr)
            continue
        counts = Counter((r.get("League") or "").strip() or name for r in rows)
        # Die Erstliga anhand des bekannten Namens erkennen, nicht anhand der
        # Zeilenzahl: Bei einer noch dünnen Fixture-Liste könnte sonst eine
        # zweite Liga mit zufällig mehr Zeilen fälschlich zur ersten werden.
        primary = next((k for k in counts if k.strip().lower() == name.strip().lower()), None)
        if primary is None:
            primary = next((k for k in counts if name.strip().lower() in k.strip().lower()
                             or k.strip().lower() in name.strip().lower()), None)
        if primary is None:
            primary = counts.most_common(1)[0][0]
        id_of = {primary: code}
        extra_idx = 2
        for lg_name in counts:
            if lg_name != primary:
                id_of[lg_name] = f"{code}{extra_idx}"
                extra_idx += 1
        seen = {}
        for row in rows:
            lg_name = (row.get("League") or "").strip() or name
            lid = id_of[lg_name]
            # Für die Ansetzungsdatei, die nur Land und Ligenname kennt
            EXTRA_IDS[((row.get("Country") or "").strip(), lg_name)] = lid
            add_match(out_rows, teams, lid, country, season_start(row.get("Season")), row, EXTRA_COLS)
            seen[lid] = lg_name
        for lid, lg_name in seen.items():
            done.append({
                "id": lid,
                "name": name if lid == code else lg_name,
                "country": country,
                "tier": tier if lid == code else 2,
            })
        extra_note = f", zusätzlich {', '.join(v for k, v in seen.items() if k != code)}" if len(seen) > 1 else ""
        print(f"  {country} {name} ({code}): {sum(counts.values())} Spiele{extra_note}")
    return done


def fetch_openfootball(seasons, delay):
    """Spielpläne aus openfootball. Liefert je Liga eine Liste von Partien mit den
    Namen aus der Quelle; zusammengeführt wird erst in merge_openfootball."""
    out = {}
    for code, of_code, name, country, tier, tzname in OPENFOOTBALL:
        tz = ZoneInfo(tzname)
        games = []
        for season in seasons:
            tag = f"{season}-{str(season + 1)[2:]}"
            data = get_json(f"{OF_BASE}/{tag}/{of_code}.json")
            time.sleep(delay)
            if not data or not data.get("matches"):
                continue
            for m in data["matches"]:
                home = (m.get("team1") or "").strip()
                away = (m.get("team2") or "").strip()
                if not home or not away:
                    continue
                ts = parse_date(m.get("date"), m.get("time"), tz)
                if ts is None:
                    continue
                # openfootball kennt zwei Schreibweisen: {"ft": [..], "ht": [..]} und [..]
                score = m.get("score")
                if isinstance(score, dict):
                    ft = score.get("ft") or []
                    ht = score.get("ht") or []
                elif isinstance(score, list):
                    ft, ht = score, []
                else:
                    ft, ht = [], []
                hg = ft[0] if len(ft) == 2 else None
                ag = ft[1] if len(ft) == 2 else None
                games.append({
                    "season": season, "t": ts, "date": (m.get("date") or "")[:10], "home": home, "away": away,
                    "hg": hg, "ag": ag,
                    "hh": ht[0] if (len(ht) == 2 and hg is not None) else None,
                    "ha": ht[1] if (len(ht) == 2 and hg is not None) else None,
                })
        if games:
            out[code] = {"name": name, "country": country, "tier": tier, "games": games}
            print(f"  {country} {name} ({code}): {len(games)} Spiele, davon {sum(g['hg'] is None for g in games)} kommende")
        else:
            print(f"  {country} {name} ({code}): keine Daten", file=sys.stderr)
    return out


def day_of(ts):
    return dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).date()


def name_map(of_games, fd_rows):
    """Ordnet openfootball-Namen den football-data-Namen zu. Beide Quellen führen
    dieselben gespielten Partien: gleicher Tag (±1) und gleiches Ergebnis ergeben
    Stimmen für ein Namenspaar. Gewählt wird je Name das Paar mit den meisten
    Stimmen, jeder Zielname nur einmal."""
    from collections import Counter, defaultdict
    by_key = defaultdict(list)
    for r in fd_rows:
        if r[6] is None:
            continue
        by_key[(day_of(r[3]), r[6], r[7])].append((NAMES.get(r[4]), NAMES.get(r[5])))
    votes = defaultdict(Counter)
    for g in of_games:
        if g["hg"] is None:
            continue
        d = day_of(g["t"])
        cands = []
        for off in (-1, 0, 1):
            cands += by_key.get((d + dt.timedelta(days=off), g["hg"], g["ag"]), [])
        if len(cands) == 1:
            votes[g["home"]][cands[0][0]] += 1
            votes[g["away"]][cands[0][1]] += 1
        elif 1 < len(cands) <= 4:
            for h, a in cands:  # schwache Stimmen, bei mehreren passenden Partien
                votes[g["home"]][h] += 0.25
                votes[g["away"]][a] += 0.25
    pairs = sorted(((c, of, fd) for of, cnt in votes.items() for fd, c in cnt.items()), reverse=True)
    out, used = {}, set()
    for c, of, fd in pairs:
        if of in out or fd in used or c < 1.5:
            continue
        out[of] = fd
        used.add(fd)
    return out


def merge_openfootball(rows, teams, of_data):
    """Gespielte Partien kommen aus football-data (mit Quoten und Statistik).
    openfootball ergänzt die kommenden Ansetzungen, umbenannt auf die
    football-data-Namen. Fehlt eine Liga bei football-data, bleiben die
    openfootball-Daten vollständig."""
    leagues = []
    for code, info in of_data.items():
        country = info["country"]
        fd_rows = [r for r in rows if r[1] == code]
        mapping = name_map(info["games"], fd_rows) if fd_rows else {}
        of_teams = {g["home"] for g in info["games"]} | {g["away"] for g in info["games"]}
        use_fd = bool(fd_rows) and len(mapping) >= 0.7 * len(of_teams)
        if use_fd:
            # Anzeige mit den vollständigen openfootball-Namen statt der Kürzel
            for of_name, fd_name in mapping.items():
                teams[str(ident(country + "|" + fd_name))] = {"n": of_name, "s": short_name(of_name), "i": ""}
        fd_keys = {(r[4], r[5], day_of(r[3])) for r in fd_rows}
        added = 0
        for g in info["games"]:
            if use_fd:
                h = mapping.get(g["home"], g["home"])
                a = mapping.get(g["away"], g["away"])
            else:
                h, a = g["home"], g["away"]
            hid, aid = ident(country + "|" + h), ident(country + "|" + a)
            d = day_of(g["t"])
            if use_fd and any((hid, aid, d + dt.timedelta(days=o)) in fd_keys for o in (-1, 0, 1)):
                continue  # schon aus football-data vorhanden
            if use_fd and g["hg"] is not None and d < dt.date.today() - dt.timedelta(days=3):
                continue  # ältere Ergebnisse nur aus football-data, sonst doppelt
            teams.setdefault(str(hid), {"n": h, "s": short_name(h) if not use_fd else h, "i": ""})
            teams.setdefault(str(aid), {"n": a, "s": short_name(a) if not use_fd else a, "i": ""})
            rows.append([
                ident(f"{code}|{g['date']}|{g['home']}|{g['away']}"), code, g["season"], g["t"], hid, aid,
                g["hg"], g["ag"], g["hh"], g["ha"], None, None, None, None, *([None] * NSTAT),
            ])
            added += 1
        if not fd_rows:
            leagues.append({"id": code, "name": info["name"], "country": country, "tier": info["tier"]})
        unmapped = sorted(of_teams - set(mapping)) if use_fd else []
        DIAG.append(f"openfootball {code}: {len(mapping)}/{len(of_teams)} Namen zugeordnet, {added} Spiele ergänzt"
                    + (f", ohne Zuordnung: {', '.join(unmapped[:5])}" if unmapped else ""))
        print(f"  {country} {info['name']} ({code}): {'Ansetzungen ergänzt' if use_fd else 'nur openfootball'}, "
              f"{len(mapping)}/{len(of_teams)} Namen zugeordnet, {added} Spiele übernommen")
    return leagues


def fetch_german_stats(seasons, delay, cache_dir, current):
    """Ecken, Karten und Schüsse der Bundesligen aus football-data.co.uk. Die
    Spiele selbst kommen live von OpenLigaDB; die Seite verknüpft beides über
    Tag und Ergebnis."""
    out = {}
    for code, lg, name in GERMAN_STATS:
        rows = []
        for season in seasons:
            tag = f"{str(season)[2:]}{str(season + 1)[2:]}"
            cfile = cache_path(cache_dir, f"main_{code}_{season}.csv")
            if season != current and os.path.exists(cfile):
                with open(cfile, encoding="utf-8") as fh:
                    text = fh.read()
            else:
                text = get_text_once(f"{BASE}/mmz4281/{tag}/{code}.csv")
                time.sleep(delay + random.uniform(0, delay * 0.4))
                if text and season != current:
                    with open(cfile, "w", encoding="utf-8") as fh:
                        fh.write(text)
            if not text:
                continue
            for row in read_csv(text):
                hg, ag = num(row.get("FTHG")), num(row.get("FTAG"))
                ts = parse_date(row.get("Date"), row.get("Time"))
                if hg is None or ts is None:
                    continue
                rows.append([day_of(ts).isoformat(), (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip(),
                             hg, ag] + [num(row.get(k)) for k in STAT_KEYS])
        if rows:
            out[lg] = rows
            print(f"  {name}: Statistik für {len(rows)} Spiele")
        else:
            print(f"  {name}: keine Statistik", file=sys.stderr)
    return out


STRIP_WORDS = {"fc", "afc", "cf", "ac", "sc", "cd", "ud", "rc", "rcd", "ss", "ssc", "as",
               "club", "calcio", "1907", "1909", "1913", "de", "futbol", "football"}


def short_name(name):
    """Kurzform für die Anzeige: 'Manchester United FC' -> 'Manchester United'."""
    parts = [w for w in name.split() if w.lower().strip(".") not in STRIP_WORDS]
    out = " ".join(parts) or name
    return out if len(out) <= 22 else out[:21] + "."


def fixture_code(row, known):
    """Liga einer Zeile aus den Ansetzungsdateien. Die Hauptdatei trägt das
    Kürzel in 'Div', die Datei der Zusatzligen nur Land und Ligenname."""
    code = (row.get("Div") or "").strip()
    if code:
        return code if code in known else None
    country, league = (row.get("Country") or "").strip(), (row.get("League") or "").strip()
    code = EXTRA_IDS.get((country, league))
    if code is None and not league:
        # Ligenname fehlt: nur zuordnen, wenn das Land genau eine Zusatzliga hat
        codes = {c for (cn, _), c in EXTRA_IDS.items() if cn == country}
        code = codes.pop() if len(codes) == 1 else None
    if code is None or code not in known:
        UNMATCHED_FIXTURES.add(f"{country or '?'}/{league or '?'}")
        return None
    return code


def odds_index(out_rows):
    """(Liga, Heim, Gast) -> Zeilennummern, für den Abgleich der Ansetzungen."""
    from collections import defaultdict
    index = defaultdict(list)
    for i, r in enumerate(out_rows):
        index[(r[1], r[4], r[5])].append(i)
    return index


def attach_fixture(out_rows, index, new):
    """Eine Ansetzung aus football-data einarbeiten. Gibt es die Partie schon
    (gleiche Liga, gleiche Teams, Datum ±2 Tage, etwa aus openfootball), werden
    nur ihre Quoten übernommen; ist sie dort schon gespielt, bleibt alles, wie
    es ist. Sonst kommt die Ansetzung neu dazu.
    Rückgabe: 'neu', 'quoten' oder None."""
    for i in index.get((new[1], new[4], new[5]), []):
        old = out_rows[i]
        if abs(old[3] - new[3]) > FIXTURE_TOLERANCE_MS:
            continue
        if old[6] is not None or new[10] is None:
            return None
        # Neue Zeile statt Änderung an Ort und Stelle: Anstoß und ID bleiben
        # die der vorhandenen Ansetzung, nur die Quoten kommen dazu.
        out_rows[i] = old[:10] + new[10:14] + old[14:]
        return "quoten"
    index[(new[1], new[4], new[5])].append(len(out_rows))
    out_rows.append(new)
    return "neu"


# Abweichung beim Abgleich einer Ansetzung mit einer vorhandenen Partie
FIXTURE_TOLERANCE_MS = 2 * 86400 * 1000
# (Land, Liga) aus den Zusatzliga-Dateien -> unser Kürzel, gefüllt von fetch_extra
EXTRA_IDS = {}
# Zeilen der Hauptdatei mit Ansetzungen, für die Quoten der deutschen Ligen
FIXTURE_ROWS = []
# Land/Liga aus der Zusatzliga-Ansetzungsdatei ohne Zuordnung, fürs Protokoll
UNMATCHED_FIXTURES = set()


def fetch_fixtures(out_rows, teams, leagues, current, delay):
    """Kommende Spiele samt aktueller Quoten. Die Dateinamen können sich
    ändern, deshalb tolerant."""
    known = {l["id"]: l for l in leagues}
    status = {}
    index = odds_index(out_rows)
    # Saison je Liga aus den vorhandenen Spielen; bei Kalenderjahr-Ligen kann
    # sie von der europäischen Saison abweichen.
    season_of = {}
    for r in out_rows:
        season_of[r[1]] = max(season_of.get(r[1], r[2]), r[2])
    for label, urls in FIXTURE_FILES:
        text = None
        used = None
        for url in urls:
            text = get_text(url)
            time.sleep(delay + random.uniform(0, delay * 0.4))
            if text and "," in text:
                used = url
                DIAG.append(f"{url}: {len(text)} Zeichen geladen")
                break
        if not text:
            status[label] = 0
            print(f"  {label}: keine der Adressen war erreichbar ({', '.join(urls)})", file=sys.stderr)
            continue
        cols = MAIN_COLS if label == "main" else EXTRA_COLS
        added, with_odds = 0, 0
        parsed = read_csv(text)
        if label == "main":
            FIXTURE_ROWS.extend(parsed)
        if not parsed:
            print(f"  {used}: Datei ohne verwertbare Zeilen", file=sys.stderr)
        for row in parsed:
            code = fixture_code(row, known)
            if code is None:
                continue
            tmp = []
            add_match(tmp, teams, code, known[code]["country"], season_of.get(code, current), row, cols)
            if not tmp:
                continue
            result = attach_fixture(out_rows, index, tmp[0])
            added += result == "neu"
            with_odds += result == "quoten"
        # Die Seite zeigt diese Zahl als "Partien geliefert": neue und schon bekannte zählen
        status[label] = added + with_odds
        status[label + "_odds"] = with_odds
        codes = sorted({(r.get("Div") or r.get("League") or "").strip() for r in parsed})
        DIAG.append(f"{used}: {len(parsed)} Zeilen, Ligakürzel {', '.join(c for c in codes if c)[:120]}, "
                    f"übernommen {added}, Quoten an {with_odds} vorhandene Ansetzungen")
        print(f"  {used}: {len(parsed)} Zeilen, davon {added} neue kommende Spiele, "
              f"Quoten für {with_odds} schon bekannte")
        if UNMATCHED_FIXTURES:
            DIAG.append(f"{used}: ohne Zuordnung {', '.join(sorted(UNMATCHED_FIXTURES))[:200]}")
            UNMATCHED_FIXTURES.clear()
        if parsed and not added and not with_odds:
            print(f"    Kürzel in der Datei: {', '.join(c for c in codes if c)}", file=sys.stderr)
            print(f"    Erwartet: {', '.join(sorted(known))}", file=sys.stderr)
    return status


def trim(row):
    """Leere Spalten am Ende weglassen, das spart bei zehntausenden Zeilen viel Platz.
    Die Seite liest fehlende Spalten als null."""
    end = len(row)
    while end > 10 and row[end - 1] is None:
        end -= 1
    return row[:end]


def market_columns(rows, cache_dir):
    """[mlh, mla] je Zeile; ohne 1X2-Quoten oder bei Fehlern [None, None]."""
    t0 = time.time()
    try:
        import fussball_markt
        out = [fussball_markt.safe_lambdas(r[10], r[11], r[12], r[13]) for r in rows]
        kept = fussball_markt.save(cache_dir)
        n = sum(1 for x in out if x[0] is not None)
        print(f"  Markt-Torerwartungen: {n} Spiele, {time.time() - t0:.1f}s, {kept} Quoten im Zwischenspeicher")
        return out
    except Exception as err:  # darf den Lauf nicht verhindern, dann bleiben die Spalten leer
        import traceback
        traceback.print_exc()
        DIAG.append(f"Markt-Torerwartungen: {type(err).__name__} {err}")
        return [[None, None] for _ in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/fussball")
    ap.add_argument("--seasons", type=int, default=2,
                    help="Saisons je Liga: laufende + so viele davor (Standard 2)")
    ap.add_argument("--backfill-batch", type=int, default=8,
                    help="Wie viele football-data-Ligen pro Lauf die fehlende Vorsaison neu laden dürfen")
    ap.add_argument("--delay", type=float, default=2.5)
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args()

    today = dt.date.today()
    current = today.year if today.month >= 7 else today.year - 1
    seasons = [current - i for i in range(args.seasons - 1, -1, -1)]
    os.makedirs(args.out, exist_ok=True)

    cache_dir = args.cache_dir or os.path.join(args.out, ".cache")
    rows, teams = [], {}
    print("Prüfe, welche Ligen openfootball aktuell führt:")
    probe_openfootball(current, 0.3)  # GitHub Raw drosselt nicht, kein langes Warten nötig
    print("Spielpläne (openfootball):")
    of_data = fetch_openfootball(seasons, 0.3)
    print(f"Hauptligen mit Statistik (football-data, Verzögerung {args.delay:.1f}s je Anfrage):")
    leagues = fetch_main(rows, teams, seasons, args.delay, cache_dir, current, args.backfill_batch)
    print("Zusatzligen:")
    leagues += fetch_extra(rows, teams, min(seasons), args.delay)
    print("Ansetzungen aus openfootball zuordnen:")
    leagues += merge_openfootball(rows, teams, of_data)
    print("Kommende Spiele (Zusatzdatei):")
    fixtures = fetch_fixtures(rows, teams, leagues, current, args.delay)
    fixtures["openfootball"] = sum(1 for r in rows if r[6] is None) > 0
    # Dasselbe Modul mit den Zusatzmodulen teilen (Namensliste, Protokoll), nicht ein zweites Mal laden
    sys.modules.setdefault("fussball_fetch", sys.modules[__name__])
    try:
        import fussball_markt
        fussball_markt.load(cache_dir)
    except Exception as err:  # ohne Zwischenspeicher wird eben alles neu gerechnet
        DIAG.append(f"Markt-Torerwartungen laden: {type(err).__name__} {err}")
    print("Quoten der Bundesligen (odds_de.json):")
    odds_de = {}
    try:
        import fussball_quoten_de
        odds_de = fussball_quoten_de.run(args.out, seasons, current, cache_dir, args.delay)
    except Exception as err:  # darf den Lauf nie verhindern, die alte Datei bleibt
        import traceback
        traceback.print_exc()
        DIAG.append(f"Quoten bl1/bl2: {type(err).__name__} {err}")
    # Pokale, Europapokal, Länderspiele, kleinere Ligen und fehlende Ansetzungen
    try:
        import fussball_espn
        espn_leagues = fussball_espn.run(rows, teams, seasons, cache_dir)
        known = {l["id"] for l in leagues}
        front = [l for l in espn_leagues if l["kind"] in ("intl", "euro") and l["id"] not in known]
        back = [l for l in espn_leagues if l["kind"] not in ("intl", "euro") and l["id"] not in known]
        leagues = front + leagues + back
        DIAG.extend(fussball_espn.LOG[-60:])
        fixtures["espn"] = len(espn_leagues)
    except Exception as err:  # Zusatzquelle darf den Lauf nie verhindern
        import traceback
        traceback.print_exc()
        DIAG.append(f"ESPN/Länderspiele: {type(err).__name__} {err}")

    print("Teams quellenübergreifend vereinen:")
    merges = []
    try:
        import fussball_teams
        names_before = {tid: fussball_teams.describe(tid, teams) for r in rows for tid in (r[4], r[5])}
        merges, skipped = fussball_teams.unify(rows, teams, leagues)
        for tid, canon, how, why in merges:
            print(f"  {how} ({why}): {names_before.get(tid, tid)} -> {names_before.get(canon, canon)}")
        for tid, why in skipped:
            print(f"  nicht vereint, {why}: {names_before.get(tid, tid)}")
        DIAG.append(f"Teams vereint: {len(merges)}, mehrdeutig und getrennt gelassen: {len(skipped)}")
    except Exception as err:  # ohne Vereinheitlichung bleiben die Daten wie bisher
        import traceback
        traceback.print_exc()
        DIAG.append(f"Teams vereinen: {type(err).__name__} {err}")

    rows.sort(key=lambda r: r[3])
    market = market_columns(rows, cache_dir)
    # Ecken, Karten und Schüsse werden nicht mehr ausgegeben: die Seite rechnet
    # nur noch mit Toren, das hält die Datei klein und das Laden schnell.
    write_json(os.path.join(args.out, "matches.json"),
               {"cols": OUT_COLS, "rows": [trim(out_row(r) + mk) for r, mk in zip(rows, market)]})
    write_json(os.path.join(args.out, "teams.json"), teams, ensure_ascii=False)
    write_json(os.path.join(args.out, "leagues.json"), leagues, ensure_ascii=False)
    write_json(os.path.join(args.out, "meta.json"), {
            "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "seasons": seasons, "current": current, "fixtures": fixtures,
            "odds_de": odds_de, "teams_merged": len(merges),
            "note": "Laufende Saison und Ansetzungen werden jeden Lauf frisch geholt; die Vorsaison füllt sich reihum über mehrere Läufe.",
            "source": "football-data.co.uk + openfootball + ESPN + international_results",
            "diagnose": DIAG[-120:],
        })
    played = sum(1 for r in rows if r[6] is not None)
    print(f"Fertig. {len(leagues)} Ligen, {len(rows)} Spiele ({played} gespielt, {len(rows) - played} offen), {len(teams)} Teams.")
    for lg in leagues:
        open_games = sum(1 for r in rows if r[1] == lg["id"] and r[6] is None)
        if not open_games:
            print(f"  Hinweis: {lg['country']} {lg['name']} hat keine kommenden Spiele in den Daten.", file=sys.stderr)


if __name__ == "__main__":
    main()
