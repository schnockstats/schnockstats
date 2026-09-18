"""Holt Ergebnisse und Spielpläne für die internationalen Ligen und schreibt sie
kompakt nach data/fussball/. Ohne Schlüssel, ohne Zusatzpakete.

Zwei Quellen:
  * openfootball (GitHub) für die großen Ligen. Enthält den kompletten
    Spielplan der laufenden Saison, also auch kommende Partien.
  * football-data.co.uk für alles Weitere. Dort gibt es nur gespielte
    Partien plus eine wöchentliche Datei mit kommenden Spielen.

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
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

BASE = "https://www.football-data.co.uk"
UA = "SchnockStats/1.0 (privates Statistikprojekt)"
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

FIXTURE_FILES = [("main", "/fixtures.csv"), ("extra", "/new_league_fixtures.csv")]

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
OF_CODES = {row[0] for row in OPENFOOTBALL}


def get_json(url, tries=3):
    text = get_text(url, tries)
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def get_text(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as res:
                raw = res.read()
            for enc in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if attempt == tries - 1:
                return None
        except Exception:
            if attempt == tries - 1:
                return None
        time.sleep(2 * (attempt + 1))
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
    return list(csv.DictReader(io.StringIO(text)))


def fetch_main(out_rows, teams, seasons, delay):
    done = []
    for code, name, country, tier in MAIN:
        if code in OF_CODES:
            continue
        count = 0
        for season in seasons:
            tag = f"{str(season)[2:]}{str(season + 1)[2:]}"
            text = get_text(f"{BASE}/mmz4281/{tag}/{code}.csv")
            time.sleep(delay)
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
    return done


def fetch_extra(out_rows, teams, min_season, delay):
    done = []
    for code, name, country, tier in EXTRA:
        text = get_text(f"{BASE}/new/{code}.csv")
        time.sleep(delay)
        if not text:
            print(f"  {country} {name} ({code}): keine Daten", file=sys.stderr)
            continue
        count = 0
        for row in read_csv(text):
            season = season_start(row.get("Season"))
            if season is None or season < min_season:
                continue
            add_match(out_rows, teams, code, country, season, row, EXTRA_COLS)
            count += 1
        if count:
            done.append({"id": code, "name": name, "country": country, "tier": tier})
            print(f"  {country} {name} ({code}): {count} Spiele")
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
    for label, path in FIXTURE_FILES:
        text = get_text(BASE + path)
        time.sleep(delay)
        if not text:
            status[label] = False
            print(f"  {path}: nicht erreichbar", file=sys.stderr)
            continue
        cols = MAIN_COLS if label == "main" else EXTRA_COLS
        added = 0
        for row in read_csv(text):
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
        status[label] = added > 0
        print(f"  {path}: {added} kommende Spiele")
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/fussball")
    ap.add_argument("--seasons", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    today = dt.date.today()
    current = today.year if today.month >= 7 else today.year - 1
    seasons = [current - i for i in range(args.seasons - 1, -1, -1)]
    os.makedirs(args.out, exist_ok=True)

    rows, teams = [], {}
    print("Große Ligen (openfootball, mit Spielplan):")
    leagues = fetch_openfootball(rows, teams, seasons, args.delay)
    print("Weitere Hauptligen (football-data):")
    leagues += fetch_main(rows, teams, seasons, args.delay)
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
            "source": "football-data.co.uk",
        }, fh, separators=(",", ":"))
    played = sum(1 for r in rows if r[6] is not None)
    print(f"Fertig. {len(leagues)} Ligen, {len(rows)} Spiele ({played} gespielt, {len(rows) - played} offen), {len(teams)} Teams.")
    for lg in leagues:
        open_games = sum(1 for r in rows if r[1] == lg["id"] and r[6] is None)
        if not open_games:
            print(f"  Hinweis: {lg['country']} {lg['name']} hat keine kommenden Spiele in den Daten.", file=sys.stderr)


if __name__ == "__main__":
    main()
