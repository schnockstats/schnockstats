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

Die deutschen Ligen kommen weiterhin live von OpenLigaDB und fehlen hier
deshalb bewusst.

Aufruf:  python3 tools/fussball_fetch.py [--out data/fussball] [--seasons 3]
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
UK = ZoneInfo("Europe/London")

# Hauptligen: eine Datei je Saison unter /mmz4281/<jjjj>/<code>.csv
MAIN = [
    ("E0", "Premier League", "England", 1), ("E1", "Championship", "England", 2),
    ("E2", "League One", "England", 3), ("E3", "League Two", "England", 4),
    ("EC", "National League", "England", 5),
    ("SC0", "Premiership", "Schottland", 1), ("SC1", "Championship", "Schottland", 2),
    ("SC2", "League One", "Schottland", 3), ("SC3", "League Two", "Schottland", 4),
    ("I1", "Serie A", "Italien", 1), ("I2", "Serie B", "Italien", 2),
    ("SP1", "La Liga", "Spanien", 1), ("SP2", "La Liga 2", "Spanien", 2),
    ("F1", "Ligue 1", "Frankreich", 1), ("F2", "Ligue 2", "Frankreich", 2),
    ("N1", "Eredivisie", "Niederlande", 1),
    ("B1", "Pro League", "Belgien", 1),
    ("P1", "Liga Portugal", "Portugal", 1),
    ("T1", "Süper Lig", "Türkei", 1),
    ("G1", "Super League", "Griechenland", 1),
]

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
    local = day.replace(hour=hour, minute=minute, tzinfo=tz or UK)
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
    hg, ag = num(row.get(cols["hg"])), num(row.get(cols["ag"]))
    hh, ha = num(row.get("HTHG")), num(row.get("HTAG"))
    rows.append([
        ident(f"{lg}|{row.get('Date')}|{home}|{away}"), lg, season, ts, hid, aid,
        hg, ag, hh if hg is not None else None, ha if hg is not None else None,
    ])


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
    order = [row for row in MAIN if row[0] not in OF_CODES]
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
        if code in OF_CODES:
            continue
        count = 0
        for season in seasons:
            tag = f"{str(season)[2:]}{str(season + 1)[2:]}"
            cfile = cache_path(cache_dir, f"main_{code}_{season}.csv")
            text = None
            if season == current_season:
                text = get_text(f"{BASE}/mmz4281/{tag}/{code}.csv")
                time.sleep(delay + random.uniform(0, delay * 0.4))
            elif os.path.exists(cfile):
                with open(cfile, encoding="utf-8") as fh:
                    text = fh.read()
            elif code in backfill_allowed:
                text = get_text(f"{BASE}/mmz4281/{tag}/{code}.csv")
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


def fetch_openfootball(out_rows, teams, seasons, delay):
    done = []
    for code, of_code, name, country, tier, tzname in OPENFOOTBALL:
        tz = ZoneInfo(tzname)
        total = upcoming = 0
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
                hid, aid = ident(country + "|" + home), ident(country + "|" + away)
                teams.setdefault(str(hid), {"n": home, "s": short_name(home), "i": ""})
                teams.setdefault(str(aid), {"n": away, "s": short_name(away), "i": ""})
                hg = ft[0] if len(ft) == 2 else None
                ag = ft[1] if len(ft) == 2 else None
                out_rows.append([
                    ident(f"{code}|{m.get('date')}|{home}|{away}"), code, season, ts, hid, aid,
                    hg, ag,
                    ht[0] if (len(ht) == 2 and hg is not None) else None,
                    ht[1] if (len(ht) == 2 and hg is not None) else None,
                ])
                total += 1
                if hg is None:
                    upcoming += 1
        if total:
            done.append({"id": code, "name": name, "country": country, "tier": tier})
            print(f"  {country} {name} ({code}): {total} Spiele, davon {upcoming} kommende")
        else:
            print(f"  {country} {name} ({code}): keine Daten", file=sys.stderr)
    return done


STRIP_WORDS = {"fc", "afc", "cf", "ac", "sc", "cd", "ud", "rc", "rcd", "ss", "ssc", "as",
               "club", "calcio", "1907", "1909", "1913", "de", "futbol", "football"}


def short_name(name):
    """Kurzform für die Anzeige: 'Manchester United FC' -> 'Manchester United'."""
    parts = [w for w in name.split() if w.lower().strip(".") not in STRIP_WORDS]
    out = " ".join(parts) or name
    return out if len(out) <= 22 else out[:21] + "."


def fetch_fixtures(out_rows, teams, leagues, current, delay):
    """Kommende Spiele. Die Dateinamen können sich ändern, deshalb tolerant."""
    known = {l["id"]: l for l in leagues}
    status = {}
    seen = {(r[1], r[3], r[4], r[5]) for r in out_rows}
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
        added = 0
        parsed = read_csv(text)
        if not parsed:
            print(f"  {used}: Datei ohne verwertbare Zeilen", file=sys.stderr)
        for row in parsed:
            code = (row.get("Div") or row.get("League") or "").strip()
            if code not in known:
                continue
            before = len(out_rows)
            add_match(out_rows, teams, code, known[code]["country"], current, row, cols)
            if len(out_rows) > before:
                r = out_rows[-1]
                if (r[1], r[3], r[4], r[5]) in seen:
                    out_rows.pop()
                else:
                    added += 1
        status[label] = added
        codes = sorted({(r.get("Div") or r.get("League") or "").strip() for r in parsed})
        DIAG.append(f"{used}: {len(parsed)} Zeilen, Ligakürzel {', '.join(c for c in codes if c)[:120]}, übernommen {added}")
        print(f"  {used}: {len(parsed)} Zeilen, davon {added} passende kommende Spiele")
        if parsed and not added:
            print(f"    Kürzel in der Datei: {', '.join(c for c in codes if c)}", file=sys.stderr)
            print(f"    Erwartet: {', '.join(sorted(known))}", file=sys.stderr)
    return status


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
    print("Große Ligen (openfootball, mit Spielplan):")
    leagues = fetch_openfootball(rows, teams, seasons, 0.3)
    print(f"Weitere Hauptligen (football-data, Verzögerung {args.delay:.1f}s je Anfrage):")
    leagues += fetch_main(rows, teams, seasons, args.delay, cache_dir, current, args.backfill_batch)
    print("Zusatzligen:")
    leagues += fetch_extra(rows, teams, min(seasons), args.delay)
    print("Kommende Spiele (Zusatzdatei):")
    fixtures = fetch_fixtures(rows, teams, [l for l in leagues if l["id"] not in OF_CODES], current, args.delay)
    fixtures["openfootball"] = sum(1 for r in rows if r[6] is None) > 0

    rows.sort(key=lambda r: r[3])
    with open(os.path.join(args.out, "matches.json"), "w", encoding="utf-8") as fh:
        json.dump({"cols": ["id", "lg", "season", "t", "h", "a", "hg", "ag", "hh", "ha"], "rows": rows},
                  fh, separators=(",", ":"))
    with open(os.path.join(args.out, "teams.json"), "w", encoding="utf-8") as fh:
        json.dump(teams, fh, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(args.out, "leagues.json"), "w", encoding="utf-8") as fh:
        json.dump(leagues, fh, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "seasons": seasons, "current": current, "fixtures": fixtures,
            "note": "Laufende Saison und Ansetzungen werden jeden Lauf frisch geholt; die Vorsaison füllt sich reihum über mehrere Läufe.",
            "source": "openfootball + football-data.co.uk",
            "diagnose": DIAG[-40:],
        }, fh, separators=(",", ":"))
    played = sum(1 for r in rows if r[6] is not None)
    print(f"Fertig. {len(leagues)} Ligen, {len(rows)} Spiele ({played} gespielt, {len(rows) - played} offen), {len(teams)} Teams.")
    for lg in leagues:
        open_games = sum(1 for r in rows if r[1] == lg["id"] and r[6] is None)
        if not open_games:
            print(f"  Hinweis: {lg['country']} {lg['name']} hat keine kommenden Spiele in den Daten.", file=sys.stderr)


if __name__ == "__main__":
    main()
