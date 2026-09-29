"""Zusatzquellen für Wettbewerbe, die football-data.co.uk und openfootball nicht
führen: Pokale, Europapokal, Länderspiele, kleinere Ligen, und kommende Spiele
für alle Ligen, zu denen football-data.co.uk keine Ansetzungen mehr liefert.

  * ESPN (site.api.espn.com): öffentliche, schlüssellose Spielpläne und
    Ergebnisse vieler Wettbewerbe, meist mit Ecken, Schüssen und Karten.
    Inoffiziell, daher sehr tolerant gelesen: fehlt ein Wettbewerb oder ändert
    sich ein Feld, wird er übersprungen und im Protokoll vermerkt.
  * Länderspiel-Ergebnisse seit 1872 (github.com/martj42/international_results,
    CC0) für die Stärke der Nationalteams.

Vereine aus ESPN werden über gemeinsame gespielte Partien (Tag und Ergebnis)
den Namen aus football-data.co.uk bzw. den OpenLigaDB-IDs zugeordnet. So sind
Pokal- und Europapokalspiele mit denselben Teams verknüpft wie die Ligaspiele,
und die Seite kann für sie die Ligastärke der Teams heranziehen.
"""

import csv
import datetime as dt
import io
import json
import os
import random
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

import fussball_fetch as ff

ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"
OLDB = "https://api.openligadb.de"
INTL_CSV = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
BERLIN = ZoneInfo("Europe/Berlin")

# Ligen, die schon aus football-data.co.uk kommen. ESPN liefert hier nur die
# kommenden Spiele (wo sie fehlen) und die Zuordnung der ESPN-Vereine.
MAP_LEAGUES = [
    ("E0", "eng.1"), ("E1", "eng.2"), ("E2", "eng.3"), ("E3", "eng.4"), ("EC", "eng.5"),
    ("SC0", "sco.1"), ("SC1", "sco.2"),
    ("SP1", "esp.1"), ("SP2", "esp.2"), ("I1", "ita.1"), ("I2", "ita.2"),
    ("F1", "fra.1"), ("F2", "fra.2"), ("N1", "ned.1"), ("B1", "bel.1"), ("P1", "por.1"),
    ("T1", "tur.1"), ("G1", "gre.1"),
    ("ARG", "arg.1"), ("AUT", "aut.1"), ("BRA", "bra.1"), ("CHN", "chn.1"), ("DNK", "den.1"),
    ("JPN", "jpn.1"), ("MEX", "mex.1"), ("NOR", "nor.1"), ("RUS", "rus.1"), ("SWE", "swe.1"),
    ("USA", "usa.1"),
]
# Deutsche Ligen: Spiele kommen live von OpenLigaDB, hier nur die Zuordnung
GERMAN = [("bl1", "ger.1"), ("bl2", "ger.2")]

# Ligen nur aus ESPN: (id, slug, Name, Land, Spielklasse, Kalenderjahr-Saison)
NEW_LEAGUES = [
    ("NED2", "ned.2", "Eerste Divisie", "Niederlande", 2, False),
    ("CYP", "cyp.1", "First Division", "Zypern", 1, False),
    ("KSA", "ksa.1", "Saudi Pro League", "Saudi-Arabien", 1, False),
    ("RSA", "rsa.1", "Premiership", "Südafrika", 1, False),
    ("AUS", "aus.1", "A-League", "Australien", 1, False),
    ("MEX2", "mex.2", "Liga de Expansión", "Mexiko", 2, False),
    ("BRA2", "bra.2", "Série B", "Brasilien", 2, True),
    ("ARGB", "arg.2", "Primera Nacional", "Argentinien", 2, True),
    ("CHI", "chi.1", "Primera División", "Chile", 1, True),
    ("COL", "col.1", "Primera A", "Kolumbien", 1, True),
    ("URU", "uru.1", "Primera División", "Uruguay", 1, True),
    ("PER", "per.1", "Liga 1", "Peru", 1, True),
    ("ECU", "ecu.1", "LigaPro", "Ecuador", 1, True),
    ("PAR", "par.1", "Primera División", "Paraguay", 1, True),
    ("USL", "usa.usl.1", "USL Championship", "USA", 2, True),
]

# Pokale: (id, slug, Name, Land). Der DFB-Pokal kommt live von OpenLigaDB.
CUPS = [
    ("FAC", "eng.fa", "FA Cup", "England"),
    ("EFL", "eng.league_cup", "League Cup", "England"),
    ("EFLT", "eng.trophy", "EFL Trophy", "England"),
    ("CDR", "esp.copa_del_rey", "Copa del Rey", "Spanien"),
    ("CIT", "ita.coppa_italia", "Coppa Italia", "Italien"),
    ("CDF", "fra.coupe_de_france", "Coupe de France", "Frankreich"),
    ("KNVB", "ned.cup", "KNVB-Pokal", "Niederlande"),
    ("TDP", "por.taca.portugal", "Taça de Portugal", "Portugal"),
    ("SCUP", "sco.tennents", "Scottish Cup", "Schottland"),
    ("SLC", "sco.cis", "League Cup", "Schottland"),
    ("SCC", "sco.challenge", "Challenge Cup", "Schottland"),
    ("CDB", "bra.copa_do_brazil", "Copa do Brasil", "Brasilien"),
    ("KKC", "ksa.kings.cup", "King's Cup", "Saudi-Arabien"),
    ("USOC", "usa.open", "US Open Cup", "USA"),
]

# Internationale Vereinswettbewerbe: Teams aus vielen Ländern (id, slug, Name, Region)
EURO = [
    ("UCL", "uefa.champions", "Champions League", "Europa"),
    ("UEL", "uefa.europa", "Europa League", "Europa"),
    ("UECL", "uefa.europa.conf", "Conference League", "Europa"),
    ("CLIB", "conmebol.libertadores", "Copa Libertadores", "Südamerika"),
    ("CSUD", "conmebol.sudamericana", "Copa Sudamericana", "Südamerika"),
    ("CCC", "concacaf.champions", "Champions Cup", "Nord- und Mittelamerika"),
]

# Länderspiele: (id, ESPN-Slug oder None, Name, Turniernamen in der Ergebnisliste)
INTL = [
    ("UNL", "uefa.nations", "Nations League", ["UEFA Nations League"]),
    ("EMQ", "uefa.euroq", "EM-Qualifikation", ["UEFA Euro qualification"]),
    ("WMQ", None, "WM-Qualifikation", []),
    ("FRI", "fifa.friendly", "Testspiele", ["Friendly"]),
    ("CNL", "concacaf.nations.league", "CONCACAF Nations League", ["CONCACAF Nations League"]),
    ("AFQ", "caf.nations_qual", "Afrika-Cup-Qualifikation", ["African Cup of Nations qualification"]),
    ("WM", None, "Weltmeisterschaft", ["FIFA World Cup"]),
    ("EM", None, "Europameisterschaft", ["UEFA Euro"]),
    ("INTX", None, "Weitere Länderspiele", []),
]
INTL_WMQ = "FIFA World Cup qualification"

# Deutsche Namen der Nationalteams (Ergebnisliste und ESPN sind englisch)
DE_NATIONS = {
    "Germany": "Deutschland", "Austria": "Österreich", "Switzerland": "Schweiz", "France": "Frankreich",
    "Spain": "Spanien", "Italy": "Italien", "England": "England", "Scotland": "Schottland",
    "Wales": "Wales", "Northern Ireland": "Nordirland", "Republic of Ireland": "Irland",
    "Netherlands": "Niederlande", "Belgium": "Belgien", "Luxembourg": "Luxemburg", "Portugal": "Portugal",
    "Denmark": "Dänemark", "Norway": "Norwegen", "Sweden": "Schweden", "Finland": "Finnland",
    "Iceland": "Island", "Poland": "Polen", "Czech Republic": "Tschechien", "Slovakia": "Slowakei",
    "Hungary": "Ungarn", "Slovenia": "Slowenien", "Croatia": "Kroatien", "Serbia": "Serbien",
    "Bosnia and Herzegovina": "Bosnien-Herzegowina", "Montenegro": "Montenegro", "Albania": "Albanien",
    "North Macedonia": "Nordmazedonien", "Kosovo": "Kosovo", "Greece": "Griechenland", "Turkey": "Türkei",
    "Cyprus": "Zypern", "Bulgaria": "Bulgarien", "Romania": "Rumänien", "Moldova": "Moldau",
    "Ukraine": "Ukraine", "Belarus": "Belarus", "Russia": "Russland", "Lithuania": "Litauen",
    "Latvia": "Lettland", "Estonia": "Estland", "Georgia": "Georgien", "Armenia": "Armenien",
    "Azerbaijan": "Aserbaidschan", "Kazakhstan": "Kasachstan", "Israel": "Israel", "Malta": "Malta",
    "Andorra": "Andorra", "San Marino": "San Marino", "Liechtenstein": "Liechtenstein",
    "Gibraltar": "Gibraltar", "Faroe Islands": "Färöer", "United States": "USA", "Mexico": "Mexiko",
    "Canada": "Kanada", "Brazil": "Brasilien", "Argentina": "Argentinien", "Uruguay": "Uruguay",
    "Colombia": "Kolumbien", "Chile": "Chile", "Peru": "Peru", "Ecuador": "Ecuador", "Paraguay": "Paraguay",
    "Venezuela": "Venezuela", "Bolivia": "Bolivien", "Japan": "Japan", "South Korea": "Südkorea",
    "Australia": "Australien", "Iran": "Iran", "Saudi Arabia": "Saudi-Arabien", "Qatar": "Katar",
    "Morocco": "Marokko", "Tunisia": "Tunesien", "Algeria": "Algerien", "Egypt": "Ägypten",
    "Senegal": "Senegal", "Ivory Coast": "Elfenbeinküste", "Ghana": "Ghana", "Nigeria": "Nigeria",
    "Cameroon": "Kamerun", "South Africa": "Südafrika",
}
# Abweichende Schreibweisen bei ESPN -> Ergebnisliste
NATION_ALIAS = {
    "Czechia": "Czech Republic", "Türkiye": "Turkey", "Turkiye": "Turkey",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina", "Ireland": "Republic of Ireland",
    "Korea Republic": "South Korea", "IR Iran": "Iran", "Côte d'Ivoire": "Ivory Coast",
    "Cote d'Ivoire": "Ivory Coast", "USA": "United States", "Faroe Is.": "Faroe Islands",
    "Macedonia": "North Macedonia", "FYR Macedonia": "North Macedonia",
}

LOG = []
# Zeitbudget: ESPN darf den täglichen Lauf nie blockieren
BUDGET_S = 18 * 60
DEADLINE = [None]
ESPN_OK = [True]
ESPN_TEAM = {}   # ESPN-Team-ID -> unsere Team-ID
STAT_SEEN = set()


def log(msg):
    print("  " + msg, flush=True)
    LOG.append(msg)


def espn_json(url):
    """Kurzer Timeout, ein Wiederholversuch, und nichts mehr nach Ablauf des Budgets."""
    if not ESPN_OK[0] or (DEADLINE[0] and time.time() > DEADLINE[0]):
        return None
    for attempt in range(2):
        try:
            # Bewusst ohne Browser-Kennung: mit vorgetäuschtem Browser antwortet ESPN
            # aus Rechenzentren mit HTTP 403, mit der normalen Python-Kennung nicht.
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=20) as res:
                return json.loads(res.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as err:
            if err.code in (400, 404):
                return None
            ff.DIAG.append(f"{url}: HTTP {err.code}")
        except Exception as err:
            ff.DIAG.append(f"{url}: {type(err).__name__} {err}")
        time.sleep(1.5)
    return None


def split_season(ts, calendar=False):
    d = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc)
    if calendar:
        return d.year
    return d.year if d.month >= 7 else d.year - 1


def parse_iso(value):
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if len(v) == 16 + 6 and v[16] in "+-":  # 2026-10-03T13:00+00:00
        v = v[:16] + ":00" + v[16:]
    try:
        d = dt.datetime.fromisoformat(v)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def to_int(v):
    if isinstance(v, dict):
        v = v.get("value", v.get("displayValue"))
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_event(ev):
    comp = (ev.get("competitions") or [{}])[0]
    st = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
    name = (st.get("name") or "").upper()
    if any(x in name for x in ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "SUSPENDED", "FORFEIT")):
        return None
    cs = comp.get("competitors") or []
    home = next((c for c in cs if c.get("homeAway") == "home"), None)
    away = next((c for c in cs if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    ts = parse_iso(comp.get("date") or ev.get("date"))
    if ts is None:
        return None
    played = st.get("state") == "post" and bool(st.get("completed"))
    th, ta = home.get("team") or {}, away.get("team") or {}
    g = {
        "eid": str(ev.get("id") or comp.get("id") or ""), "t": ts,
        "date": dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).date().isoformat(),
        "home": (th.get("displayName") or th.get("name") or "").strip(),
        "away": (ta.get("displayName") or ta.get("name") or "").strip(),
        "hs_": (th.get("shortDisplayName") or "").strip(), "as_": (ta.get("shortDisplayName") or "").strip(),
        "hid": str(th.get("id") or ""), "aid": str(ta.get("id") or ""),
        "hg": None, "ag": None, "hh": None, "ha": None,
        "neu": 1 if comp.get("neutralSite") else 0,
        "stats": None,
    }
    if not g["home"] or not g["away"]:
        return None
    if played:
        g["hg"], g["ag"] = to_int(home.get("score")), to_int(away.get("score"))
        if g["hg"] is None or g["ag"] is None:
            return None
        lh, la = home.get("linescores") or [], away.get("linescores") or []
        if lh and la:
            g["hh"], g["ha"] = to_int(lh[0]), to_int(la[0])
        g["stats"] = parse_stats(comp, home, away)
    return g


def parse_stats(comp, home, away):
    """Schüsse, aufs Tor, Ecken, Gelb, Rot, Fouls je Team, soweit vorhanden."""
    def table(c):
        out = {}
        for s in c.get("statistics") or []:
            key = s.get("name") or s.get("abbreviation")
            if key:
                out[key] = s.get("displayValue", s.get("value"))
                STAT_SEEN.add(key)
        return out
    sh, sa = table(home), table(away)
    if not sh or not sa:
        return None
    # Karten aus dem Spielverlauf, falls nicht als Statistik vorhanden
    cards = {"h": [0, 0], "a": [0, 0]}
    hid, aid = str((home.get("team") or {}).get("id")), str((away.get("team") or {}).get("id"))
    for d in comp.get("details") or []:
        tid = str((d.get("team") or {}).get("id"))
        side = "h" if tid == hid else "a" if tid == aid else None
        if not side:
            continue
        if d.get("redCard"):
            cards[side][1] += 1
        elif d.get("yellowCard"):
            cards[side][0] += 1

    def pick(s, *keys):
        for k in keys:
            if k in s:
                return to_int(s[k])
        return None
    yh = pick(sh, "yellowCards")
    ya = pick(sa, "yellowCards")
    rh = pick(sh, "redCards")
    ra = pick(sa, "redCards")
    has_details = bool(comp.get("details"))
    if yh is None and has_details:
        yh, ya = cards["h"][0], cards["a"][0]
    if rh is None and has_details:
        rh, ra = cards["h"][1], cards["a"][1]
    return [
        pick(sh, "totalShots"), pick(sa, "totalShots"),
        pick(sh, "shotsOnTarget"), pick(sa, "shotsOnTarget"),
        pick(sh, "wonCorners"), pick(sa, "wonCorners"),
        yh, ya, rh, ra,
        pick(sh, "foulsCommitted"), pick(sa, "foulsCommitted"),
    ]


def month_windows(start, end):
    cur = dt.date(start.year, start.month, 1)
    while cur <= end:
        nxt = dt.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        yield cur, nxt - dt.timedelta(days=1)
        cur = nxt


def fetch_espn(slug, start, end, cache_dir, delay):
    """Alle Spiele eines Wettbewerbs zwischen start und end, monatsweise.
    Abgeschlossene Monate werden zwischengespeichert."""
    today = dt.date.today()
    games, fails, ok = [], 0, 0
    os.makedirs(cache_dir, exist_ok=True)
    for a, b in month_windows(start, end):
        cfile = os.path.join(cache_dir, f"{slug}_{a:%Y%m}.json")
        if b < today - dt.timedelta(days=4) and os.path.exists(cfile):
            with open(cfile, encoding="utf-8") as fh:
                games += json.load(fh)
            ok += 1
            continue
        # ESPN nimmt keine Datumsbereiche, aber ganze Monate (dates=JJJJMM)
        data = espn_json(f"{ESPN}/{slug}/scoreboard?dates={a:%Y%m}&limit=500")
        if data is None:
            data = espn_json(f"{ESPN}/{slug}/scoreboard?dates={a:%Y%m}")
        time.sleep(delay + random.uniform(0, delay * 0.3))
        if data is None:
            fails += 1
            if fails >= 2 and not ok:
                break  # Wettbewerb gibt es bei ESPN nicht
            continue
        ok += 1
        month = [g for g in (parse_event(ev) for ev in data.get("events") or []) if g]
        if b < today - dt.timedelta(days=4):
            with open(cfile, "w", encoding="utf-8") as fh:
                json.dump(month, fh, separators=(",", ":"), ensure_ascii=False)
        games += month
    seen, out = set(), []
    for g in sorted(games, key=lambda x: x["t"]):
        key = g["eid"] or (g["date"], g["home"], g["away"])
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    return out, ok > 0


def stats_cols(g):
    return g["stats"] if g.get("stats") else [None] * ff.NSTAT


def make_row(code, season, g, hid, aid):
    return [
        ff.ident(f"espn|{code}|{g['eid'] or g['date'] + g['home'] + g['away']}"), code, season, g["t"], hid, aid,
        g["hg"], g["ag"], g["hh"], g["ha"], None, None, None, None, *stats_cols(g), g.get("neu") or None,
    ]


def near_keys(rows, code):
    return {(r[4], r[5], ff.day_of(r[3])) for r in rows if r[1] == code}


def has_near(keys, hid, aid, t):
    d = ff.day_of(t)
    return any((hid, aid, d + dt.timedelta(days=o)) in keys for o in (-1, 0, 1))


def map_league(rows, teams, code, slug, cache_dir, delay, start, end):
    """ESPN-Vereine eines football-data-Landes zuordnen und fehlende kommende
    Spiele ergänzen."""
    fd_rows = [r for r in rows if r[1] == code]
    if not fd_rows:
        return 0
    games, found = fetch_espn(slug, start, end, cache_dir, delay)
    if not found or not games:
        log(f"ESPN {slug}: nicht verfügbar")
        return 0
    return apply_mapping(rows, code, fd_rows, games, f"ESPN {slug}")


def apply_mapping(rows, code, fd_rows, games, label):
    """Vereine über gemeinsame Ergebnisse zuordnen, fehlende kommende Spiele ergänzen."""
    mapping = ff.name_map(games, fd_rows)
    by_name = {}
    for r in fd_rows:
        by_name[ff.NAMES.get(r[4])] = r[4]
        by_name[ff.NAMES.get(r[5])] = r[5]
    espn_ids = {}
    for g in games:
        espn_ids[g["home"]] = g["hid"]
        espn_ids[g["away"]] = g["aid"]
    for en, fdn in mapping.items():
        if fdn in by_name and espn_ids.get(en):
            ESPN_TEAM[espn_ids[en]] = by_name[fdn]
    season = max(r[2] for r in fd_rows)
    keys = near_keys(rows, code)
    now = dt.datetime.now(dt.timezone.utc).timestamp() * 1000
    added, skipped = 0, 0
    for g in games:
        if g["hg"] is not None or g["t"] < now - 6 * 3600 * 1000:
            continue
        hid, aid = ESPN_TEAM.get(g["hid"]), ESPN_TEAM.get(g["aid"])
        if hid is None or aid is None:
            skipped += 1
            continue
        if has_near(keys, hid, aid, g["t"]):
            continue
        rows.append(make_row(code, season, g, hid, aid))
        keys.add((hid, aid, ff.day_of(g["t"])))
        added += 1
    teams_n = len({g["home"] for g in games} | {g["away"] for g in games})
    log(f"{label} -> {code}: {len(mapping)}/{teams_n} Vereine zugeordnet, {added} kommende Spiele ergänzt"
        + (f", {skipped} ohne Zuordnung" if skipped else ""))
    return added


def map_german(cache_dir, delay, seasons, start, end):
    """ESPN-Vereine der deutschen Ligen den OpenLigaDB-Team-IDs zuordnen."""
    for lg, slug in GERMAN:
        pseudo = []
        for season in seasons[-2:]:
            data = ff.get_json(f"{OLDB}/getmatchdata/{lg}/{season}")
            time.sleep(0.3)
            for m in data or []:
                t1, t2 = m.get("team1") or {}, m.get("team2") or {}
                res = [r for r in (m.get("matchResults") or []) if r.get("resultTypeID") == 2]
                ts = parse_iso(m.get("matchDateTimeUTC"))
                if not res or ts is None or t1.get("teamId") is None:
                    continue
                ff.NAMES[t1["teamId"]] = t1.get("teamName") or ""
                ff.NAMES[t2["teamId"]] = t2.get("teamName") or ""
                pseudo.append([0, lg, season, ts, t1["teamId"], t2["teamId"], res[0].get("pointsTeam1"), res[0].get("pointsTeam2")])
        if not pseudo:
            log(f"OpenLigaDB {lg}: keine Daten für die Zuordnung")
            continue
        games, found = fetch_espn(slug, start, end, cache_dir, delay)
        if not found:
            log(f"ESPN {slug}: nicht verfügbar")
            continue
        mapping = ff.name_map(games, pseudo)
        by_name = {ff.NAMES[r[4]]: r[4] for r in pseudo}
        by_name.update({ff.NAMES[r[5]]: r[5] for r in pseudo})
        n = 0
        for g in games:
            for nm, eid in ((g["home"], g["hid"]), (g["away"], g["aid"])):
                if nm in mapping and eid and mapping[nm] in by_name:
                    ESPN_TEAM[eid] = by_name[mapping[nm]]
                    n += 1
        log(f"ESPN {slug} -> OpenLigaDB {lg}: {len(mapping)} Vereine zugeordnet")


def team_for(teams, g, side):
    eid = g["hid"] if side == "h" else g["aid"]
    if eid in ESPN_TEAM:
        return ESPN_TEAM[eid]
    tid = ff.ident("espn|" + (eid or g["home" if side == "h" else "away"]))
    name = g["home"] if side == "h" else g["away"]
    short = (g["hs_"] if side == "h" else g["as_"]) or name
    teams.setdefault(str(tid), {"n": name, "s": ff.short_name(short), "i": ""})
    ESPN_TEAM[eid] = tid
    return tid


def espn_competition(rows, teams, code, slug, name, country, kind, tier, calendar, cache_dir, delay, start, end):
    games, found = fetch_espn(slug, start, end, cache_dir, delay)
    if not found or not games:
        log(f"ESPN {slug} ({name}): nicht verfügbar")
        return None
    n_played = 0
    for g in games:
        hid, aid = team_for(teams, g, "h"), team_for(teams, g, "a")
        rows.append(make_row(code, split_season(g["t"], calendar), g, hid, aid))
        n_played += g["hg"] is not None
    with_stats = sum(1 for g in games if g.get("stats") and g["stats"][4] is not None)
    log(f"ESPN {slug} ({name}): {n_played} gespielt, {len(games) - n_played} kommend, {with_stats} mit Ecken")
    return {"id": code, "name": name, "country": country, "tier": tier, "kind": kind}


# ---------- TheSportsDB: Ligen, die ESPN nicht führt ----------
# Schlüssel optional als Secret THESPORTSDB_KEY. Mit dem freien Testschlüssel "3"
# kommen je Spieltag höchstens fünf Spiele, das reicht für Form und Stärke.
TSDB = "https://www.thesportsdb.com/api/v1/json/"
TSDB_KEY = os.environ.get("THESPORTSDB_KEY") or "3"
TSDB_DELAY = 2.2 if TSDB_KEY == "3" else 0.6   # frei: höchstens 30 Anfragen pro Minute
TSDB_BUDGET_S = 12 * 60
# (id, TheSportsDB-Liga, Name, Land, Spielklasse, "map" = nur Ansetzungen zu football-data)
TSDB_LEAGUES = [
    ("DNK2", 4683, "1. Division", "Dänemark", 2, "new"),
    ("POL", 4422, "Ekstraklasa", "Polen", 1, "map"),
    ("ROU", 4691, "Liga I", "Rumänien", 1, "map"),
    ("FIN", 4636, "Veikkausliiga", "Finnland", 1, "map"),
    ("IRL", 4643, "Premier Division", "Irland", 1, "map"),
    ("AUT2", 4796, "2. Liga", "Österreich", 2, "new"),
    ("SWZ2", 4713, "Challenge League", "Schweiz", 2, "new"),
    ("POL2", 4661, "I liga", "Polen", 2, "new"),
    ("NOR2", 4457, "1. divisjon", "Norwegen", 2, "new"),
    ("JPN2", 4824, "J2 League", "Japan", 2, "new"),
    ("CRO", 4629, "HNL", "Kroatien", 1, "new"),
    ("SRB", 4671, "SuperLiga", "Serbien", 1, "new"),
    ("DNK3", 4632, "2. Division", "Dänemark", 3, "new"),
]
TSDB_DEADLINE = [None]


def tsdb_json(path):
    if TSDB_DEADLINE[0] and time.time() > TSDB_DEADLINE[0]:
        return None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(TSDB + TSDB_KEY + path), timeout=25) as res:
                data = json.loads(res.read().decode("utf-8", "replace"))
            time.sleep(TSDB_DELAY)
            return data
        except urllib.error.HTTPError as err:
            ff.DIAG.append(f"TheSportsDB {path}: HTTP {err.code}")
            if err.code == 429:
                time.sleep(20)
                continue
            if err.code == 404:
                return None
        except Exception as err:
            ff.DIAG.append(f"TheSportsDB {path}: {type(err).__name__} {err}")
        time.sleep(3)
    return None


def tsdb_event(e):
    status = (e.get("strStatus") or "").lower()
    if any(x in status for x in ("postp", "cancel", "abandon", "susp")):
        return None
    ts = parse_iso((e.get("strTimestamp") or "").replace(" ", "T")[:19] + "+00:00") if e.get("strTimestamp") else None
    if ts is None and e.get("dateEvent"):
        ts = parse_iso(f"{e['dateEvent']}T{(e.get('strTime') or '15:00:00')[:8]}+00:00")
    if ts is None or not e.get("strHomeTeam") or not e.get("strAwayTeam"):
        return None
    hg, ag = to_int(e.get("intHomeScore")), to_int(e.get("intAwayScore"))
    done = hg is not None and ag is not None and ("finish" in status or "ft" == status or ts < time.time() * 1000 - 3 * 3600 * 1000)
    return {
        "eid": "tsdb" + str(e.get("idEvent") or ""), "t": ts,
        "date": dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).date().isoformat(),
        "home": e["strHomeTeam"].strip(), "away": e["strAwayTeam"].strip(), "hs_": "", "as_": "",
        "hid": "tsdb" + str(e.get("idHomeTeam") or e["strHomeTeam"]), "aid": "tsdb" + str(e.get("idAwayTeam") or e["strAwayTeam"]),
        "hg": hg if done else None, "ag": ag if done else None, "hh": None, "ha": None, "neu": 0, "stats": None,
    }


def prev_season(label):
    if "-" in label:
        a, b = label.split("-")
        return f"{int(a) - 1}-{int(b) - 1}"
    return str(int(label) - 1)


def tsdb_rounds(lid, season, cache_dir, until_ts):
    """Alle Spieltage einer Saison; abgeschlossene Spieltage aus dem Zwischenspeicher."""
    games, empty = [], 0
    now = time.time() * 1000
    for rnd in range(1, 60):
        cfile = os.path.join(cache_dir, f"tsdb_{lid}_{season}_{rnd}.json")
        if os.path.exists(cfile):
            with open(cfile, encoding="utf-8") as fh:
                part = json.load(fh)
        else:
            data = tsdb_json(f"/eventsround.php?id={lid}&r={rnd}&s={season}")
            if data is None and TSDB_DEADLINE[0] and time.time() > TSDB_DEADLINE[0]:
                break
            part = [g for g in (tsdb_event(e) for e in (data or {}).get("events") or []) if g]
            if part and all(g["hg"] is not None for g in part) and max(g["t"] for g in part) < now - 3 * 86400000:
                with open(cfile, "w", encoding="utf-8") as fh:
                    json.dump(part, fh, separators=(",", ":"), ensure_ascii=False)
        if not part:
            empty += 1
            if empty >= 2:
                break
            continue
        empty = 0
        games += part
        if min(g["t"] for g in part) > until_ts:
            break
    return games


def run_tsdb(rows, teams, cache_dir):
    leagues = []
    TSDB_DEADLINE[0] = time.time() + TSDB_BUDGET_S
    until = (time.time() + 21 * 86400) * 1000
    for code, lid, name, country, tier, mode in TSDB_LEAGUES:
        info = tsdb_json(f"/lookupleague.php?id={lid}")
        season = ((info or {}).get("leagues") or [{}])[0].get("strCurrentSeason")
        if not season:
            log(f"TheSportsDB {name} ({country}): nicht verfügbar")
            continue
        calendar = "-" not in season
        if mode == "map":
            fd_rows = [r for r in rows if r[1] == code]
            if not fd_rows:
                continue
            games = tsdb_rounds(lid, season, cache_dir, until)
            apply_mapping(rows, code, fd_rows, games, f"TheSportsDB {name}")
            continue
        # Erst die laufende Saison (Form, Ansetzungen), dann die Vorsaison als Anlauf
        games = tsdb_rounds(lid, season, cache_dir, until) + tsdb_rounds(lid, prev_season(season), cache_dir, until)
        if not games:
            log(f"TheSportsDB {name} ({country}): keine Spiele")
            continue
        seen = set()
        n_played = 0
        for g in games:
            if g["eid"] in seen:
                continue
            seen.add(g["eid"])
            hid, aid = team_for(teams, g, "h"), team_for(teams, g, "a")
            rows.append(make_row(code, split_season(g["t"], calendar), g, hid, aid))
            n_played += g["hg"] is not None
        log(f"TheSportsDB {name} ({country}): {n_played} gespielt, {len(seen) - n_played} kommend")
        leagues.append({"id": code, "name": name, "country": country, "tier": tier, "kind": "league"})
    if time.time() > TSDB_DEADLINE[0]:
        log("TheSportsDB-Zeitbudget aufgebraucht, Rest beim nächsten Lauf")
    return leagues


def fetch_international(rows, teams, cache_dir, delay, start_year):
    """Länderspiele: Historie aus der Ergebnisliste, kommende Spiele aus ESPN."""
    text = ff.get_text(INTL_CSV)
    leagues = []
    if not text:
        log("Länderspiel-Ergebnisse nicht erreichbar")
        return leagues
    by_tour = {}
    for code, _, _, tours in INTL:
        for t in tours:
            by_tour[t] = code
    by_tour[INTL_WMQ] = "WMQ"
    used = set()
    names = set()
    now_day = dt.date.today()
    pseudo = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            day = dt.date.fromisoformat(r["date"])
        except (KeyError, ValueError):
            continue
        if day.year < start_year:
            continue
        hg, ag = ff.num(r.get("home_score")), ff.num(r.get("away_score"))
        if hg is None or ag is None:
            continue  # kommende Spiele kommen aus ESPN, mit Uhrzeit
        code = by_tour.get(r.get("tournament", ""), "INTX")
        home, away = r["home_team"].strip(), r["away_team"].strip()
        ts = int(dt.datetime(day.year, day.month, day.day, 20, 45, tzinfo=BERLIN).timestamp() * 1000)
        hid, aid = ff.ident("INT|" + home), ff.ident("INT|" + away)
        for nm, tid in ((home, hid), (away, aid)):
            if nm not in names:
                names.add(nm)
                de = DE_NATIONS.get(nm, nm)
                teams[str(tid)] = {"n": de, "s": de, "i": ""}
                ff.NAMES[tid] = nm
        neu = 1 if (r.get("neutral") or "").upper() == "TRUE" else None
        row = [ff.ident(f"INT|{r['date']}|{home}|{away}"), code, split_season(ts), ts, hid, aid, hg, ag,
               None, None, None, None, None, None, *([None] * ff.NSTAT), neu]
        rows.append(row)
        pseudo.append(row)
        used.add(code)
    log(f"Länderspiele: {len(pseudo)} Ergebnisse seit {start_year}")
    # Kommende Spiele aus ESPN
    today = dt.date.today()
    for code, slug, name, _ in INTL:
        if not slug:
            continue
        games, found = fetch_espn(slug, today - dt.timedelta(days=120), today + dt.timedelta(days=75), cache_dir, delay)
        if not found:
            log(f"ESPN {slug} ({name}): nicht verfügbar")
            continue
        mapping = ff.name_map(games, pseudo)
        keys = near_keys(rows, code)
        added = 0
        for g in games:
            # Gespielte Partien nur, wenn sie in der Ergebnisliste noch fehlen
            # (die wird mit etwas Verzögerung nachgetragen)
            ids = []
            for nm in (g["home"], g["away"]):
                en = mapping.get(nm) or NATION_ALIAS.get(nm, nm)
                tid = ff.ident("INT|" + en)
                if str(tid) not in teams:
                    de = DE_NATIONS.get(en, en)
                    teams[str(tid)] = {"n": de, "s": de, "i": ""}
                ids.append(tid)
            if has_near(keys, ids[0], ids[1], g["t"]):
                continue
            rows.append(make_row(code, split_season(g["t"]), g, ids[0], ids[1]))
            keys.add((ids[0], ids[1], ff.day_of(g["t"])))
            used.add(code)
            added += 1
        log(f"ESPN {slug} ({name}): {added} Spiele ergänzt, davon {sum(1 for g in games if g['hg'] is None)} kommend")
    for code, _, name, _ in INTL:
        if code in used:
            leagues.append({"id": code, "name": name, "country": "Länderspiele", "tier": 1, "kind": "intl"})
    return leagues


def run(rows, teams, seasons, cache_root, delay=0.25):
    cache_dir = os.path.join(cache_root, "espn")
    today = dt.date.today()
    DEADLINE[0] = time.time() + BUDGET_S
    t0 = time.time()
    probe = espn_json(f"{ESPN}/eng.1/scoreboard")
    if probe is None:
        ESPN_OK[0] = False
        log("ESPN nicht erreichbar, nur zwischengespeicherte Daten und Länderspiel-Historie")
    else:
        log(f"ESPN erreichbar ({time.time() - t0:.1f}s)")
    hist_start = today - dt.timedelta(days=300)             # laufende Saison und die Monate davor
    map_start = today - dt.timedelta(days=75)               # reicht für die Zuordnung
    end = today + dt.timedelta(days=45)
    leagues = []
    # Reihenfolge nach Wichtigkeit: erst Ansetzungen und Zuordnung, dann Länderspiele,
    # Europapokal und Pokale, zuletzt die Historie weiterer Ligen. Läuft das Zeitbudget
    # ab, fehlt so höchstens Historie, die der nächste Lauf aus dem Zwischenspeicher ergänzt.
    print("ESPN: Vereine der vorhandenen Ligen zuordnen, fehlende Ansetzungen ergänzen")
    for code, slug in MAP_LEAGUES:
        map_league(rows, teams, code, slug, cache_dir, delay, map_start, end)
    map_german(cache_dir, delay, seasons, map_start, end)
    print("Länderspiele")
    intl = fetch_international(rows, teams, cache_dir, delay, today.year - 3)
    print("ESPN: Europapokal und Pokale")
    for code, slug, name, region in EURO:
        lg = espn_competition(rows, teams, code, slug, name, region, "euro", 1, False, cache_dir, delay, hist_start, end)
        if lg:
            leagues.append(lg)
    for code, slug, name, country in CUPS:
        lg = espn_competition(rows, teams, code, slug, name, country, "cup", 1, False, cache_dir, delay, hist_start, end)
        if lg:
            leagues.append(lg)
    print("ESPN: weitere Ligen")
    for code, slug, name, country, tier, cal in NEW_LEAGUES:
        start = dt.date(today.year - 1, 1, 1) if cal else hist_start
        lg = espn_competition(rows, teams, code, slug, name, country, "league", tier, cal, cache_dir, delay, start, end)
        if lg:
            leagues.append(lg)
    if DEADLINE[0] and time.time() > DEADLINE[0]:
        log("ESPN-Zeitbudget aufgebraucht, Rest beim nächsten Lauf")
    print("TheSportsDB: weitere Ligen")
    leagues += run_tsdb(rows, teams, cache_dir)
    leagues = intl + leagues
    if STAT_SEEN:
        log("ESPN-Statistikfelder: " + ", ".join(sorted(STAT_SEEN))[:300])
    return leagues
