"""Holt die großen europäischen Eishockey-Ligen und schreibt sie kompakt nach
data/hockey/. Ohne Schlüssel, ohne Zusatzpakete.

Quelle: die öffentliche App-Schnittstelle von LiveScore. Eine Anfrage je Tag
liefert alle Eishockeyspiele weltweit, mit Ergebnis je Drittel. Daraus ergibt
sich der Stand nach regulärer Spielzeit, auf dem das Modell rechnet, und ob ein
Spiel in die Verlängerung oder ins Penaltyschießen ging.

Bewusst nur die laufende Saison und das Ende der vorigen (ab Februar): gerechnet
wird mit der aktuellen Form, nicht mit alten Spielzeiten. Abgeschlossene Tage
werden zwischengespeichert (data/hockey/.cache), jeder Lauf holt also nur die
letzten Tage und die kommenden zwei Wochen neu.

Aufruf:  python3 tools/hockey_fetch.py [--out data/hockey]
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://prod-public-api.livescore.com/v1/api/app/date/hockey/{day}/0?MD=1"

# (id, Land in der Quelle, Wettbewerb in der Quelle, Anzeigename, Land)
LEAGUES = [
    ("del", "Germany", "DEL", "DEL", "Deutschland"),
    ("ice", "Austria", "ICE Hockey League", "ICE Hockey League", "Österreich"),
    ("nl", "Switzerland", "National League", "National League", "Schweiz"),
    ("elh", "Czech Republic", "Extraliga", "Extraliga", "Tschechien"),
    ("shl", "Sweden", "SHL", "SHL", "Schweden"),
    ("liiga", "Finland", "Liiga", "Liiga", "Finnland"),
]
SKIP = re.compile(r"pre-?season|friendl|test|all-?star", re.I)
FINISHED = {"FT", "AOT", "AP", "AET", "Pen", "AW"}
TEAM_BASE = 1_000_000  # Abstand zu den NHL-Team-IDs
LOG = []


def log(msg):
    print("  " + msg, flush=True)
    LOG.append(msg)


def get_json(url, tries=3):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=25) as res:
                return json.loads(res.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            LOG.append(f"{url}: HTTP {err.code}")
        except Exception as err:
            LOG.append(f"{url}: {type(err).__name__} {err}")
        time.sleep(2 * (attempt + 1))
    return None


def season_of(day):
    start = day.year if day.month >= 7 else day.year - 1
    return start * 10000 + start + 1


def league_of(stage):
    country, name = (stage.get("Cnm") or "").strip(), (stage.get("Snm") or "").strip()
    if SKIP.search(name):
        return None, False
    for lid, c, n, _, _ in LEAGUES:
        # "SHL", "SHL: Playoffs" – aber nicht "DEL 2" für "DEL"
        if country == c and re.match(re.escape(n) + r"(\s*:|$)", name):
            return lid, bool(re.search(r"play-?off|final|quarter|semi", name, re.I))
    return None, False


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_day(data):
    out = []
    for stage in (data or {}).get("Stages") or []:
        lid, playoff = league_of(stage)
        if not lid:
            continue
        for e in stage.get("Events") or []:
            t1, t2 = (e.get("T1") or [{}])[0], (e.get("T2") or [{}])[0]
            if not t1.get("ID") or not t2.get("ID"):
                continue
            esd = str(e.get("Esd") or "")
            try:
                start = dt.datetime.strptime(esd[:12], "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
            status = (e.get("Eps") or "").strip()
            if status.lower().startswith(("postp", "canc", "aband", "susp")):
                continue
            g = {
                "id": num(e.get("Eid")), "lg": lid, "gt": 3 if playoff else 2,
                "t": int(start.timestamp() * 1000), "season": season_of(start.date()),
                "h": TEAM_BASE + int(t1["ID"]), "a": TEAM_BASE + int(t2["ID"]),
                "hn": (t1.get("Nm") or "").strip(), "an": (t2.get("Nm") or "").strip(),
                "fin": False,
            }
            if status in FINISHED:
                hg, ag = num(e.get("Tr1")), num(e.get("Tr2"))
                if hg is None or ag is None:
                    continue
                per = [(num(e.get(f"Tr1Pe{k}")), num(e.get(f"Tr2Pe{k}"))) for k in (1, 2, 3)]
                ot = "REG"
                if all(a is not None and b is not None for a, b in per):
                    rh, ra = sum(a for a, _ in per), sum(b for _, b in per)
                    if rh == ra and hg != ag:
                        ot = "SO" if status in ("AP", "Pen") else "OT"
                elif status in ("AOT", "AET"):
                    ot = "OT"
                elif status in ("AP", "Pen"):
                    ot = "SO"
                g.update({"fin": True, "hg": hg, "ag": ag, "ot": ot})
            out.append(g)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/hockey")
    ap.add_argument("--ahead", type=int, default=14)
    ap.add_argument("--delay", type=float, default=0.35)
    args = ap.parse_args()

    today = dt.datetime.now(dt.timezone.utc).date()
    cur = season_of(today)
    # Ende der vorigen Saison als Anlauf, dann die laufende
    start = dt.date(cur // 10000, 2, 1)
    end = today + dt.timedelta(days=args.ahead)
    cache = os.path.join(args.out, ".cache")
    os.makedirs(cache, exist_ok=True)

    games, fetched, cached, failed = {}, 0, 0, 0
    day = start
    while day <= end:
        cfile = os.path.join(cache, f"{day:%Y%m%d}.json")
        if day < today - dt.timedelta(days=2) and os.path.exists(cfile):
            with open(cfile, encoding="utf-8") as fh:
                part = json.load(fh)
            cached += 1
        else:
            data = get_json(API.format(day=f"{day:%Y%m%d}"))
            time.sleep(args.delay)
            if data is None:
                failed += 1
                day += dt.timedelta(days=1)
                continue
            fetched += 1
            part = parse_day(data)
            if day < today - dt.timedelta(days=2):
                with open(cfile, "w", encoding="utf-8") as fh:
                    json.dump(part, fh, separators=(",", ":"), ensure_ascii=False)
        for g in part:
            if g["id"] is not None:
                games[g["id"]] = g
        day += dt.timedelta(days=1)
    log(f"Tage: {fetched} geladen, {cached} aus dem Zwischenspeicher, {failed} fehlgeschlagen")

    if not games:
        print("Keine Spiele gefunden, alte Daten bleiben stehen.", file=sys.stderr)
        sys.exit(1 if failed else 0)

    teams, out = {}, []
    for g in sorted(games.values(), key=lambda x: x["t"]):
        teams[str(g["h"])] = {"n": g.pop("hn"), "lg": g["lg"]}
        teams[str(g["a"])] = {"n": g.pop("an"), "lg": g["lg"]}
        out.append(g)
    for tid, t in teams.items():
        t["s"] = t["n"]
    leagues = []
    now = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
    for lid, _, _, name, country in LEAGUES:
        lg = [g for g in out if g["lg"] == lid]
        played = sum(1 for g in lg if g["fin"] and g["season"] == cur)
        upcoming = sum(1 for g in lg if not g["fin"] and g["t"] > now)
        log(f"{country} {name}: {played} Spiele dieser Saison, {upcoming} kommend, {sum(1 for g in lg if g['fin'])} gesamt")
        if lg:
            leagues.append({"id": lid, "name": name, "country": country})

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "games.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    with open(os.path.join(args.out, "teams.json"), "w", encoding="utf-8") as fh:
        json.dump(teams, fh, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "current": cur, "leagues": leagues, "source": "LiveScore",
            "diagnose": LOG[-40:],
        }, fh, separators=(",", ":"), ensure_ascii=False)
    print(f"Fertig: {len(out)} Spiele, {len(teams)} Teams, {len(leagues)} Ligen.")


if __name__ == "__main__":
    main()
