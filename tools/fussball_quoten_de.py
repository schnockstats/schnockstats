"""Quoten der 1. und 2. Bundesliga für data/fussball/odds_de.json.

Die Spiele der deutschen Ligen lädt die Seite live von OpenLigaDB. Quoten gibt
es dort nicht, wohl aber bei football-data.co.uk (D1, D2). Diese Datei ordnet
die football-data-Namen den OpenLigaDB-Team-IDs zu und schreibt je Spiel:
Liga, Anstoß (Unix-Millisekunden UTC), Heim-ID, Gast-ID und die Quoten.
Beendete Spiele mit Schlussquoten (falls vorhanden), kommende mit den aktuellen
Quoten aus der Ansetzungsdatei.

Format (kompakt):
  {"cols":["lg","t","h","a","oh","od","oa","o25"],"rows":[["bl1",1760000000000,40,7,1.45,4.8,6.5,1.55], ...]}
"""

import datetime as dt
import os
import random
import re
import time
import unicodedata

import fussball_fetch as ff

OLDB = "https://api.openligadb.de"
# (football-data-Kürzel, OpenLigaDB-Kürzel)
LEAGUES = [("D1", "bl1"), ("D2", "bl2")]
COLS = ["lg", "t", "h", "a", "oh", "od", "oa", "o25"]
FILE = "odds_de.json"
# Erst schreiben, wenn genug Spiele sicher zugeordnet sind
MIN_ROWS = 100
MIN_SHARE = 0.95
# Höchstens so viele gespielte Partien dürfen im Ergebnis von OpenLigaDB abweichen,
# sonst ist vermutlich etwas falsch zugeordnet
MAX_RESULT_MISMATCH = 0.05
DAY_MS = 86400 * 1000

# Namen bei football-data, die sich nicht aus dem OpenLigaDB-Namen ableiten
# lassen (englische Schreibweise, Abkürzungen). Wert = Name wie bei OpenLigaDB.
ALIAS = {
    "M'gladbach": "Borussia Mönchengladbach",
    "Bayern Munich": "FC Bayern München",
}
# Bestandteile, die nur die Rechtsform oder das Gründungsjahr angeben
PREFIX = {"fc", "sv", "tsg", "vfb", "vfl", "sc", "fsv", "spvgg", "bsc", "ssv", "tsv", "sg", "1",
          "ev"}


def tokens(name):
    """'1. FC Köln' -> ['koln'], "M'gladbach" -> ['mgladbach']"""
    s = (name or "").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['’`´.]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in s.split() if t not in PREFIX and not t.isdigit()]


def token_hit(a, b):
    return a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def match_team(fd_name, candidates):
    """OpenLigaDB-ID zu einem football-data-Namen oder None. Alle Bestandteile
    des (meist kürzeren) football-data-Namens müssen im OpenLigaDB-Namen
    vorkommen, und das nur bei genau einem Verein. Kein Raten."""
    want = tokens(ALIAS.get(fd_name, fd_name))
    if not want:
        return None
    hits = [tid for tid, have in candidates.items()
            if all(any(token_hit(w, h) for h in have) for w in want)]
    if len(hits) == 1:
        return hits[0]
    # Bei mehreren Treffern den Verein nehmen, dessen Name genau so lautet
    exact = [tid for tid in hits if candidates[tid] == want]
    return exact[0] if len(exact) == 1 else None


def oldb_season(lg, season):
    """Vereine und Endergebnisse einer Saison von OpenLigaDB."""
    data = ff.get_json_once(f"{OLDB}/getmatchdata/{lg}/{season}")
    time.sleep(0.3)
    # Bei Fehlern kommt manchmal ein Objekt statt einer Liste, dann gilt die Saison als nicht geladen
    if not data or not isinstance(data, list):
        return None, []
    teams, results = {}, []
    for m in data:
        if not isinstance(m, dict):
            continue
        t1, t2 = m.get("team1") or {}, m.get("team2") or {}
        for t in (t1, t2):
            if t.get("teamId") is not None:
                teams[t["teamId"]] = t.get("teamName") or t.get("shortName") or ""
        res = [r for r in (m.get("matchResults") or []) if r.get("resultTypeID") == 2]
        when = m.get("matchDateTimeUTC") or ""
        if res and t1.get("teamId") is not None and t2.get("teamId") is not None and when[:10]:
            results.append((t1["teamId"], t2["teamId"], when[:10],
                            res[0].get("pointsTeam1"), res[0].get("pointsTeam2")))
    return teams, results


def season_csv(code, season, current, cache_dir, delay):
    """CSV einer Saison; abgeschlossene Saisons aus dem Zwischenspeicher, den
    auch fetch_main für die Vorsaisons nutzt."""
    tag = f"{str(season)[2:]}{str(season + 1)[2:]}"
    cfile = ff.cache_path(cache_dir, f"main_{code}_{season}.csv")
    if season != current and os.path.exists(cfile):
        with open(cfile, encoding="utf-8") as fh:
            return fh.read()
    text = ff.get_text_once(f"{ff.BASE}/mmz4281/{tag}/{code}.csv")
    time.sleep(delay + random.uniform(0, delay * 0.4))
    if text and season != current:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text


def kickoff(row):
    """Anstoß in Unix-Millisekunden UTC. Die Zeiten bei football-data sind
    britische Ortszeit; fehlt die Uhrzeit, gilt 12:00 UTC des Tages."""
    if ":" in (row.get("Time") or ""):
        return ff.parse_date(row.get("Date"), row.get("Time"))
    day = ff.parse_date(row.get("Date"), "00:00", dt.timezone.utc)
    return None if day is None else day + 12 * 3600 * 1000


def odds_row(lg, row, ids, played):
    """Eine Ausgabezeile, oder None ohne Zuordnung bzw. ohne vollständige 1X2-Quoten."""
    home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
    h, a = ids.get(home), ids.get(away)
    t = kickoff(row)
    oh, od, oa, o25 = ff.row_odds(row, played)
    if h is None or a is None or t is None or None in (oh, od, oa):
        return None
    return [lg, t, h, a, oh, od, oa, o25]


def check_results(lg_rows, results):
    """Anteil gespielter Partien, deren Ergebnis bei OpenLigaDB anders lautet.
    Ein hoher Wert deutet auf eine falsche Zuordnung hin."""
    by_key = {}
    for h, a, day, g1, g2 in results:
        by_key[(h, a, day)] = (g1, g2)
    checked, wrong = 0, 0
    for (h, a, t), goals in lg_rows:
        d = ff.day_of(t)
        found = None
        for off in (-1, 0, 1):
            found = by_key.get((h, a, (d + dt.timedelta(days=off)).isoformat()))
            if found:
                break
        if found is None:
            continue
        checked += 1
        wrong += found != goals
    return checked, wrong


def build(seasons, current, cache_dir, delay):
    """Zeilen für odds_de.json und ein kleines Protokoll."""
    rows, unmapped = [], set()
    info = {"complete": True, "checked": 0, "wrong": 0, "total": 0, "mapped": 0}
    for code, lg in LEAGUES:
        for season in seasons:
            text = season_csv(code, season, current, cache_dir, delay)
            teams, results = oldb_season(lg, season)
            if not text or teams is None:
                info["complete"] = False
                ff.DIAG.append(f"Quoten {lg} {season}: {'CSV' if not text else 'OpenLigaDB'} nicht geladen")
                continue
            cand = {tid: tokens(n) for tid, n in teams.items()}
            csv_rows = ff.read_csv(text)
            if season == current:
                csv_rows += [r for r in ff.FIXTURE_ROWS if (r.get("Div") or "").strip() == code]
            ids = {}
            for r in csv_rows:
                for nm in ((r.get("HomeTeam") or "").strip(), (r.get("AwayTeam") or "").strip()):
                    if nm and nm not in ids:
                        ids[nm] = match_team(nm, cand)
            unmapped |= {f"{lg}:{nm}" for nm, tid in ids.items() if tid is None}
            kickoffs = {}   # (Heim, Gast) -> Anstoßzeiten, gegen doppelte Ansetzungen
            season_res = []
            for r in csv_rows:
                hg, ag = ff.num(r.get("FTHG")), ff.num(r.get("FTAG"))
                played = hg is not None
                info["total"] += 1
                info["mapped"] += all(ids.get((r.get(k) or "").strip()) is not None for k in ("HomeTeam", "AwayTeam"))
                out = odds_row(lg, r, ids, played)
                if out is None:
                    continue
                # Ansetzungen nur, wenn die Partie nicht schon gespielt in der Saisondatei steht
                known = kickoffs.setdefault((out[2], out[3]), [])
                if not played and any(abs(t - out[1]) <= 2 * DAY_MS for t in known):
                    continue
                known.append(out[1])
                rows.append(out)
                if played:
                    season_res.append(((out[2], out[3], out[1]), (hg, ag)))
            checked, wrong = check_results(season_res, results)
            info["checked"] += checked
            info["wrong"] += wrong
    info["unmapped"] = sorted(unmapped)
    return rows, info


def run(out_dir, seasons, current, cache_dir, delay):
    """Schreibt odds_de.json, wenn genug sicher zugeordnet ist. Bei Netzfehlern
    oder zu wenigen Zeilen bleibt der letzte Stand erhalten. Gibt eine kurze
    Zusammenfassung für meta.json zurück."""
    rows, info = build(seasons[-2:], current, cache_dir, delay)
    path = os.path.join(out_dir, FILE)
    share = info["mapped"] / info["total"] if info["total"] else 0
    wrong_share = info["wrong"] / info["checked"] if info["checked"] else 1
    reason = None
    if len(rows) < MIN_ROWS:
        reason = f"nur {len(rows)} Zeilen"
    elif share < MIN_SHARE:
        reason = f"nur {share:.0%} der Spiele zugeordnet"
    elif wrong_share > MAX_RESULT_MISMATCH:
        reason = f"{info['wrong']}/{info['checked']} Ergebnisse weichen von OpenLigaDB ab"
    elif not info["complete"] and os.path.exists(path):
        reason = "nicht alle Quellen geladen"
    summary = {"rows": len(rows), "unmapped": info["unmapped"], "mapped_share": round(share, 3),
               "checked": info["checked"], "wrong": info["wrong"], "written": reason is None}
    if info["unmapped"]:
        ff.DIAG.append(f"Quoten bl1/bl2 ohne Zuordnung: {', '.join(info['unmapped'])}")
    if reason:
        ff.DIAG.append(f"{FILE} nicht geschrieben ({reason}), letzter Stand bleibt")
        print(f"  {FILE}: nicht geschrieben ({reason})")
        return summary
    rows.sort(key=lambda r: (r[1], r[0]))
    ff.write_json(path, {"cols": COLS, "rows": rows})
    ff.DIAG.append(f"{FILE}: {len(rows)} Spiele mit Quoten, {info['wrong']}/{info['checked']} Ergebnisabweichungen")
    print(f"  {FILE}: {len(rows)} Spiele mit Quoten, ohne Zuordnung: {', '.join(info['unmapped']) or 'keine'}")
    return summary
