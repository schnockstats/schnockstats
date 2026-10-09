"""Holt die großen europäischen Eishockey-Ligen und schreibt sie kompakt nach
data/hockey/. Ohne Schlüssel, ohne Zusatzpakete.

Quelle: die öffentliche App-Schnittstelle von LiveScore. Eine Anfrage je Tag
liefert alle Eishockeyspiele weltweit, mit Ergebnis je Drittel. Daraus ergibt
sich der Stand nach regulärer Spielzeit, auf dem das Modell rechnet, und ob ein
Spiel in die Verlängerung oder ins Penaltyschießen ging.

Tagesweise geholt wird ab Februar der vorigen Saison bis zwei Wochen voraus.
Abgeschlossene Tage werden zwischengespeichert (data/hockey/.cache), jeder Lauf
holt also nur die letzten Tage und die kommenden zwei Wochen neu.

Eigenheit der Quelle: Ist in einer Liga die neue Saison gestartet, liefert die
Tagesabfrage die Hauptrunde der vorigen Saison nicht mehr (nur noch deren
Playoffs). Hauptrunde und Playoffs der Vorsaison kommen deshalb zusätzlich über
die Abfrage je Wettbewerb ("stage"), je eine Anfrage, danach zwischengespeichert.
Leere Tage gelten erst als endgültig leer, wenn die Quelle sie mit Abstand
bestätigt hat; bis dahin werden sie in kleinen Portionen erneut geprüft.

Aufruf:  python3 tools/hockey_fetch.py [--out data/hockey] [--months 14] [--refetch 40]
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
STAGE_API = "https://prod-public-api.livescore.com/v1/api/app/stage/hockey/{path}/0?MD=1"

# (id, Land in der Quelle, Wettbewerb in der Quelle, Anzeigename, Land,
#  Pfad der Hauptrunde, Pfad der Playoffs – beides für die Abfrage je Wettbewerb)
LEAGUES = [
    ("del", "Germany", "DEL", "DEL", "Deutschland", "germany/del", "germany/del-play-off"),
    ("ice", "Austria", "ICE Hockey League", "ICE Hockey League", "Österreich",
     "austria/ice-hockey-league", "austria/ice-hockey-league-playoff"),
    ("nl", "Switzerland", "National League", "National League", "Schweiz",
     "switzerland/national-league", "switzerland/national-league-play-off"),
    ("elh", "Czech Republic", "Extraliga", "Extraliga", "Tschechien",
     "czech-republic/extraliga", "czech-republic/extraliga-play-off"),
    ("shl", "Sweden", "SHL", "SHL", "Schweden", "sweden/shl", "sweden/shl-play-off"),
    ("liiga", "Finland", "Liiga", "Liiga", "Finnland", "finland/liiga", "finland/liiga-play-off"),
]
SKIP = re.compile(r"pre-?season|friendl|test|all-?star", re.I)
# Alles außer der Hauptrunde: Playoffs, Pre-Playoffs/Play-in, Play-out, Relegation, Qualifikation
PLAYOFF = re.compile(r"play-?off|play-?in|play-?out|play-?down|pre-?play|relegation|qualification|"
                     r"wild-?card|final|quarter|semi|kval", re.I)
FINISHED = {"FT", "AOT", "AP", "AET", "Pen", "AW"}
SO_STATUS, OT_STATUS = ("AP", "Pen"), ("OT", "AOT", "AET")
EMPTY_FINAL_DAYS = 21  # leerer Tag gilt als endgültig, wenn so lange danach noch bestätigt
EMPTY_RECHECK_DAYS = 3  # sonst frühestens nach so vielen Tagen erneut prüfen
TEAM_BASE = 1_000_000  # Abstand zu den NHL-Team-IDs
LOG = []
STATUS_SEEN = {}
DROPPED = [0]  # beendete Spiele, deren Ergebnis nicht stimmig war


def log(msg):
    print("  " + msg, flush=True)
    LOG.append(msg)


def get_json(url, tries=3):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=25) as res:
                return json.loads(res.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as err:
            if err.code in (404, 410):
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
    for lid, c, n, *_ in LEAGUES:
        # "SHL", "SHL: Play-off", "Liiga Play-off" – aber nicht "DEL2" oder "DEL 2" für "DEL"
        if country == c and re.match(re.escape(n) + r"(\s*:|\s+(?=" + PLAYOFF.pattern + r")|$)", name, re.I):
            return lid, bool(PLAYOFF.search(name[len(n):]))
    return None, False


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def result_of(e, status):
    """Endstand und Art des Spielendes (REG/OT/SO), oder None wenn unbrauchbar.

    Der Gesamtstand der Quelle ist vereinzelt falsch (z. B. 3:1 nach
    Penaltyschießen, obwohl die Drittel 3:3 ergeben). Nach Verlängerung oder
    Penaltyschießen muss die Differenz genau 1 sein; sonst wird aus den Dritteln
    und dem Sieger der Verlängerung bzw. des Penaltyschießens neu gerechnet.
    """
    hg, ag = num(e.get("Tr1")), num(e.get("Tr2"))
    if hg is None or ag is None:
        return None
    per = [(num(e.get(f"Tr1Pe{k}")), num(e.get(f"Tr2Pe{k}"))) for k in (1, 2, 3)]
    rh = ra = None
    if all(a is not None and b is not None for a, b in per):
        rh, ra = sum(a for a, _ in per), sum(b for _, b in per)
    # Tore der Verlängerung (Tr?OT, sonst vierter Abschnitt) und Sieger im Penaltyschießen (Trp?)
    o1 = num(e.get("Tr1OT", e.get("Tr1Pe4")))
    o2 = num(e.get("Tr2OT", e.get("Tr2Pe4")))
    p1, p2 = num(e.get("Trp1")), num(e.get("Trp2"))
    so_win = (p1 > p2) - (p1 < p2) if p1 is not None and p2 is not None else 0
    ot_win = (o1 > o2) - (o1 < o2) if o1 is not None and o2 is not None else 0

    if status in SO_STATUS or so_win:
        ot, win = "SO", so_win
    elif status in OT_STATUS or ot_win:
        ot, win = "OT", ot_win
    elif rh is not None and rh == ra and hg != ag:
        # Gleichstand nach 60 Minuten ohne Kennzeichnung: Tor im vierten Abschnitt, sonst Penaltyschießen
        ot = "SO" if e.get("Tr1Pe4") is not None and not (o1 or o2) else "OT"
        win = 0
    else:
        ot, win = "REG", 0

    if ot == "REG":
        if hg == ag and rh is not None and rh != ra:
            log(f"Spiel {e.get('Eid')}: Endstand {hg}:{ag} ohne Sieger, Drittel ergeben {rh}:{ra} – übernommen")
            return rh, ra, "REG"
        if hg == ag:
            log(f"Spiel {e.get('Eid')}: Endstand {hg}:{ag} ohne Sieger – ausgelassen")
            return None
        return hg, ag, "REG"
    if abs(hg - ag) == 1 and (not win or (hg > ag) == (win > 0)):
        return hg, ag, ot
    # Endstand passt nicht zu Verlängerung/Penaltyschießen: aus Dritteln und Sieger neu rechnen
    if rh is not None and rh == ra and win:
        log(f"Spiel {e.get('Eid')}: {ot} mit Endstand {hg}:{ag}, Drittel {rh}:{ra} – korrigiert")
        return (rh + 1, ra, ot) if win > 0 else (rh, ra + 1, ot)
    log(f"Spiel {e.get('Eid')}: {ot} mit Endstand {hg}:{ag} nicht stimmig – ausgelassen")
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
            STATUS_SEEN[status] = STATUS_SEEN.get(status, 0) + 1
            # Beendet: bekannter Endstatus, oder ein Ergebnis und Anpfiff vor über vier Stunden
            # (LiveScore kennzeichnet Verlängerung und Penaltyschießen nicht einheitlich)
            old = start < dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=4)
            if status in FINISHED or (old and num(e.get("Tr1")) is not None and num(e.get("Tr2")) is not None
                                      and not status.upper().startswith("NS")):
                res = result_of(e, status)
                if res is None:
                    DROPPED[0] += 1
                    continue
                g.update({"fin": True, "hg": res[0], "ag": res[1], "ot": res[2]})
            out.append(g)
    return out


def plausible(g):
    """Nach Verlängerung oder Penaltyschießen muss genau ein Tor Unterschied stehen."""
    return not g.get("fin") or g.get("ot", "REG") == "REG" or abs(g["hg"] - g["ag"]) == 1


def load_json(path):
    """Datei lesen; fehlend oder beschädigt (z. B. abgebrochener Lauf) ergibt None."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        if os.path.exists(path):
            log(f"{os.path.basename(path)}: unlesbar, wird neu geholt")
        return None


def dump_json(path, obj, **kw):
    """Atomar schreiben: erst in eine Hilfsdatei, dann umbenennen. Ein Abbruch
    hinterlässt so nie eine halbe Datei, die später committet würde."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), **kw)
    os.replace(tmp, path)


def checked_on(part):
    try:
        return dt.date.fromisoformat(part.get("checked"))
    except (TypeError, ValueError):
        return None


def read_cache(cfile, day, today):
    """Liefert (Spiele, neu_holen). Ein leerer Tag gilt erst als endgültig leer, wenn die
    Quelle ihn mit genügend Abstand bestätigt hat; bis dahin wird er gelegentlich nachgeprüft."""
    part = load_json(cfile)
    if isinstance(part, dict):  # leerer Tag mit Prüfdatum
        checked = checked_on(part)
        if checked is None:
            return [], True
        if (checked - day).days >= EMPTY_FINAL_DAYS:
            return [], False
        return [], (today - checked).days >= EMPTY_RECHECK_DAYS
    if not isinstance(part, list) or not part:  # fehlt, kaputt oder alt-leer ohne Prüfdatum
        return [], True
    return part, not all(plausible(g) for g in part)


def write_cache(cfile, part, today):
    dump_json(cfile, part or {"games": [], "checked": today.isoformat()}, ensure_ascii=False)


def valid(data):
    """Nur eine echte Antwort (mit Liste "Stages") zählt; Fehler oder Leerantwort nicht."""
    return isinstance(data, dict) and isinstance(data.get("Stages"), list)


def fetch_days(start, end, today, cache, args):
    """Tagesabfrage. Zurückliegende Tage kommen aus dem Zwischenspeicher; nachgeprüft werden
    höchstens args.refetch Tage je Lauf, damit der Lauf kurz und die Quelle geschont bleibt.
    Vorrang haben Tage mit unstimmigen Ergebnissen, danach die jüngsten."""
    days, stale = [], []
    day = start
    while day <= end:
        cfile = os.path.join(cache, f"{day:%Y%m%d}.json")
        if day < today - dt.timedelta(days=2):
            part, again = read_cache(cfile, day, today)
            if again:
                stale.append((not all(plausible(g) for g in part), day))
        else:
            part = None  # die letzten Tage und die Zukunft immer frisch
        days.append((day, cfile, part))
        day += dt.timedelta(days=1)
    refetch = {d for _, d in sorted(stale, reverse=True)[:max(args.refetch, 0)]}

    games, fetched, failed = {}, 0, 0
    for day, cfile, part in days:
        if part is None or day in refetch:
            data = get_json(API.format(day=f"{day:%Y%m%d}"))
            time.sleep(args.delay)
            if not valid(data):
                failed += 1  # alter Stand (falls vorhanden) bleibt
            else:
                fetched += 1
                new = parse_day(data)
                # Nachprüfung ersetzt einen gespeicherten Tag nicht durch einen dünneren,
                # außer der alte war unstimmig
                old = part or []
                if (sum(g["fin"] for g in new) >= sum(g["fin"] for g in old)
                        or not all(plausible(g) for g in old)):
                    part = new
                    if day < today - dt.timedelta(days=2):
                        write_cache(cfile, part, today)
        for g in part or []:
            if g["id"] is not None:
                games[g["id"]] = g
    log(f"Tage: {fetched} geladen, {len(days) - fetched - failed} aus dem Zwischenspeicher, "
        f"{failed} fehlgeschlagen, {len(stale) - len(refetch)} Nachprüfungen auf später verschoben")
    return games, failed


def read_stage_cache(cfile, today):
    """Liefert (Spiele, neu_holen) für einen gespeicherten Wettbewerb. Endgültig ist er nur,
    wenn beim Speichern kein Ergebnis verworfen wurde; sonst nach einer Woche erneut prüfen."""
    part = load_json(cfile)
    if not isinstance(part, dict) or not isinstance(part.get("games"), list) or not part["games"]:
        return [], True
    checked = checked_on(part)
    again = not part.get("final") and (checked is None or (today - checked).days >= 7)
    return part["games"], again


def fetch_stages(prev, since, today, cache, args):
    """Hauptrunde und Playoffs der Vorsaison je Liga über die Abfrage je Wettbewerb.
    Einmal vollständig (alles beendet), bleibt das Ergebnis im Zwischenspeicher."""
    y0, y1 = prev // 10000, prev % 10000
    games, fetched, cached = {}, 0, 0
    for lid, *_, regular, playoff in LEAGUES:
        for path, tag in ((f"{regular}-{y0}-{y1}", "regular"), (playoff, "playoff")):
            cfile = os.path.join(cache, f"stage-{lid}-{tag}-{prev}.json")
            part, again = read_stage_cache(cfile, today)
            if not again:
                cached += 1
            else:
                data = get_json(STAGE_API.format(path=path))
                time.sleep(args.delay)
                if not valid(data):
                    log(f"{path}: keine Antwort")
                else:
                    fetched += 1
                    dropped = DROPPED[0]
                    # Der Playoff-Pfad zeigt immer die jüngsten Playoffs: nur die der Vorsaison behalten
                    new = [g for g in parse_day(data) if g["season"] == prev]
                    dropped = DROPPED[0] - dropped
                    last = max((g["t"] for g in new), default=0) / 1000
                    # Leeres Ergebnis nie speichern (z. B. Playoffs der laufenden Saison)
                    if new and all(g["fin"] for g in new) and last < time.time() - 3 * 86400:
                        if dropped:
                            log(f"{path}: {dropped} Ergebnisse verworfen, in einer Woche erneut prüfen")
                        dump_json(cfile, {"games": new, "checked": today.isoformat(), "final": not dropped},
                                  ensure_ascii=False)
                    if len(new) >= len(part):
                        part = new
            for g in part:
                if g["id"] is not None and g["t"] >= since:
                    games[g["id"]] = g
    log(f"Wettbewerbe der Vorsaison: {fetched} geladen, {cached} aus dem Zwischenspeicher")
    return games


def prune_cache(cache, first_day, first_season):
    """Zwischenspeicher begrenzen: Tage vor dem Zeitfenster und Wettbewerbe älterer Saisons löschen."""
    removed = 0
    for name in os.listdir(cache):
        m = re.fullmatch(r"(\d{8})\.json|stage-.+-(\d{8})\.json|.+\.tmp", name)
        if not m:
            continue
        if m.group(1):
            old = m.group(1) < f"{first_day:%Y%m%d}"
        elif m.group(2):
            old = int(m.group(2)) < first_season
        else:
            old = True  # Rest eines abgebrochenen Laufs
        if old:
            os.remove(os.path.join(cache, name))
            removed += 1
    if removed:
        log(f"Zwischenspeicher: {removed} veraltete Dateien gelöscht")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/hockey")
    ap.add_argument("--ahead", type=int, default=14)
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--months", type=int, default=14, help="so weit zurück reicht die Vorsaison")
    ap.add_argument("--refetch", type=int, default=40, help="höchstens so viele Tage je Lauf nachprüfen")
    args = ap.parse_args()

    began = time.time()
    today = dt.datetime.now(dt.timezone.utc).date()
    cur = season_of(today)
    # Ende der vorigen Saison als Anlauf, dann die laufende
    start = dt.date(cur // 10000, 2, 1)
    end = today + dt.timedelta(days=args.ahead)
    first = today - dt.timedelta(days=round(args.months * 30.44))
    since = dt.datetime.combine(first, dt.time(), dt.timezone.utc).timestamp() * 1000
    cache = os.path.join(args.out, ".cache")
    os.makedirs(cache, exist_ok=True)
    prune_cache(cache, min(first, start), min(season_of(first), cur - 10001))

    games = fetch_stages(cur - 10001, since, today, cache, args)
    days, failed = fetch_days(start, end, today, cache, args)
    games.update(days)  # die Tagesabfrage ist aktueller und hat Vorrang
    log("Status in der Quelle: " + ", ".join(f"{k or '-'}={v}" for k, v in sorted(STATUS_SEEN.items())))
    for g in [g for g in games.values() if not plausible(g)]:
        # Altlast im Zwischenspeicher, deren Tag noch nicht nachgeprüft wurde
        log(f"Spiel {g['id']}: {g['ot']} mit Endstand {g['hg']}:{g['ag']} nicht stimmig – ausgelassen")
        del games[g["id"]]
    log(f"Laufzeit {time.time() - began:.0f} s")

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
    for lid, _, _, name, country, *_ in LEAGUES:
        lg = [g for g in out if g["lg"] == lid]
        played = sum(1 for g in lg if g["fin"] and g["season"] == cur)
        upcoming = sum(1 for g in lg if not g["fin"] and g["t"] > now)
        log(f"{country} {name}: {played} Spiele dieser Saison, {upcoming} kommend, {sum(1 for g in lg if g['fin'])} gesamt")
        if lg:
            leagues.append({"id": lid, "name": name, "country": country})

    os.makedirs(args.out, exist_ok=True)
    dump_json(os.path.join(args.out, "games.json"), out)
    dump_json(os.path.join(args.out, "teams.json"), teams, ensure_ascii=False)
    dump_json(os.path.join(args.out, "meta.json"), {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current": cur, "leagues": leagues, "source": "LiveScore",
        "diagnose": LOG[-40:],
    }, ensure_ascii=False)
    print(f"Fertig: {len(out)} Spiele, {len(teams)} Teams, {len(leagues)} Ligen.")


if __name__ == "__main__":
    main()
