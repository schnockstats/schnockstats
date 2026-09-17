#!/usr/bin/env python3
"""Holt NHL-Daten von der öffentlichen NHL-Schnittstelle und schreibt sie
als kompakte JSON-Dateien nach data/nhl/. Ohne Schlüssel, ohne Zusatzpakete.

Aufruf:  python3 tools/nhl_fetch.py [--out data/nhl] [--seasons 3]
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"
UA = "SchnockHockey/1.0 (privates Statistikprojekt)"

# Kürzel inklusive ausgelaufener Standorte, damit auch ältere Saisons vollständig sind.
TEAMS = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET", "EDM", "FLA",
    "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS",
    "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "WSH", "ARI",
]


def get_json(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if attempt == tries - 1:
                raise
        except Exception:
            if attempt == tries - 1:
                raise
        time.sleep(2 * (attempt + 1))
    return None


def current_season(today=None):
    today = today or dt.date.today()
    start = today.year if today.month >= 7 else today.year - 1
    return start * 10000 + start + 1


def parse_games(games, teams):
    """Wandelt NHL-Spiele in das kompakte Format der Seite um."""
    out = {}
    for g in games or []:
        gt = g.get("gameType")
        if gt not in (2, 3):  # nur Hauptrunde und Playoffs, keine Vorbereitung
            continue
        home, away = g.get("homeTeam") or {}, g.get("awayTeam") or {}
        hid, aid = home.get("id"), away.get("id")
        if hid is None or aid is None:
            continue
        for side in (home, away):
            tid = side.get("id")
            name = (side.get("placeName") or {}).get("default", "")
            common = (side.get("commonName") or {}).get("default", "")
            full = (name + " " + common).strip() or side.get("abbrev", str(tid))
            teams[str(tid)] = {"n": full, "s": common or side.get("abbrev", full), "i": side.get("logo", "")}
        start = g.get("startTimeUTC")
        ts = int(dt.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() * 1000) if start else 0
        state = (g.get("gameState") or "").upper()
        fin = state in ("FINAL", "OFF") and home.get("score") is not None
        row = {
            "id": g.get("id"), "season": g.get("season"), "t": ts, "gt": gt,
            "h": hid, "a": aid, "fin": bool(fin),
        }
        if fin:
            row["hg"] = home.get("score")
            row["ag"] = away.get("score")
            row["ot"] = (g.get("gameOutcome") or {}).get("lastPeriodType", "REG")
        out[row["id"]] = row
    return out


def fetch_season(season, teams, delay=0.35):
    games = {}
    missing = 0
    for abbrev in TEAMS:
        data = get_json(f"{WEB}/club-schedule-season/{abbrev}/{season}")
        time.sleep(delay)
        if not data or not data.get("games"):
            missing += 1
            continue
        games.update(parse_games(data["games"], teams))
    rows = sorted(games.values(), key=lambda r: r["t"])
    print(f"  {season}: {len(rows)} Spiele, {sum(1 for r in rows if r['fin'])} beendet, {missing} Kürzel ohne Daten")
    return rows


def fetch_scorers(season, abbrev_to_id, delay=0.35):
    url = (f"{STATS}/skater/summary?isAggregate=false&isGame=false&limit=-1&start=0"
           f"&cayenneExp=seasonId={season}%20and%20gameTypeId=2")
    data = get_json(url)
    time.sleep(delay)
    rows = []
    for p in (data or {}).get("data", []):
        abbrevs = (p.get("teamAbbrevs") or "").split(",")
        team = None
        for ab in reversed(abbrevs):
            team = abbrev_to_id.get(ab.strip())
            if team:
                break
        if not team or not p.get("gamesPlayed"):
            continue
        rows.append({
            "id": p.get("playerId"),
            "n": p.get("skaterFullName", ""),
            "team": team,
            "g": p.get("goals", 0) or 0,
            "gp": p.get("gamesPlayed", 0) or 0,
            "pp": p.get("ppGoals", 0) or 0,
        })
    rows.sort(key=lambda r: -r["g"])
    print(f"  {season}: {len(rows)} Feldspieler mit Einsätzen")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/nhl")
    ap.add_argument("--seasons", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.35)
    args = ap.parse_args()

    cur = current_season()
    seasons = [cur - 10001 * i for i in range(args.seasons - 1, -1, -1)]
    os.makedirs(args.out, exist_ok=True)

    teams = {}
    written = []
    print("Lade Spielpläne:")
    for season in seasons:
        rows = fetch_season(season, teams, args.delay)
        if not rows and season != cur:
            print(f"  {season}: keine Daten, übersprungen", file=sys.stderr)
            continue
        with open(os.path.join(args.out, f"season-{season}.json"), "w", encoding="utf-8") as fh:
            json.dump(rows, fh, separators=(",", ":"))
        written.append(season)

    abbrev_to_id = {}
    for tid, info in teams.items():
        abbrev_to_id[info["s"][:3].upper()] = int(tid)
    # verlässliche Zuordnung über die Logo-URL (enthält das Kürzel)
    for tid, info in teams.items():
        icon = info.get("i") or ""
        part = icon.rsplit("/", 1)[-1].split("_")[0]
        if len(part) == 3:
            abbrev_to_id[part.upper()] = int(tid)

    print("Lade Torschützen:")
    for season in written:
        rows = fetch_scorers(season, abbrev_to_id, args.delay)
        with open(os.path.join(args.out, f"scorers-{season}.json"), "w", encoding="utf-8") as fh:
            json.dump(rows, fh, separators=(",", ":"))

    with open(os.path.join(args.out, "teams.json"), "w", encoding="utf-8") as fh:
        json.dump(teams, fh, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "seasons": written,
            "current": cur,
            "source": "api-web.nhle.com",
        }, fh, separators=(",", ":"))
    print(f"Fertig. Saisons: {written}, Teams: {len(teams)}")


if __name__ == "__main__":
    main()
