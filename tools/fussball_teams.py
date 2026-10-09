"""Vereint die Team-IDs über alle Quellen hinweg.

Dasselbe Team kann aus mehreren Quellen mit verschiedenen IDs kommen:
football-data.co.uk (ID aus Land und Name), ESPN (Pokale, Europapokal, weitere
Ligen) und TheSportsDB. Der Abgleich über gemeinsame Ergebnisse in
fussball_espn.py fängt viel ab, aber nicht alles (Ligen ohne ESPN-Gegenstück,
Auf- und Absteiger zwischen Quellen). Dieser Schritt läuft ganz am Ende und
führt die Reste über den Namen zusammen:

  1. Ligateams eines Landes: gleicher Name in verschiedenen Spielklassen ist
     derselbe Verein (Auf-/Absteiger), sofern beide nie in derselben Saison
     Ligaspiele haben (dann wären es zwei Vereine).
  2. Pokalteams: gegen die Ligateams desselben Landes.
  3. Europapokal: gegen die Ligateams aller Länder des Kontinentalverbands.

Vorsichtsregeln: Die ID der football-data-Liga bleibt immer erhalten, sie ist
die stabilste. Mehrdeutiges wird nicht zusammengeführt, Reserve-, Jugend- und
Frauenteams nie mit der ersten Mannschaft. Spielt das Zielteam schon unter
seiner eigenen ID im selben Pokal oder Europapokal, ist das andere ein anderer
Verein (ESPN führt jeden Verein mit fester ID). Wollen zwei Teams auf dieselbe
ID, bleiben beide getrennt.
"""

import re
import unicodedata
from collections import defaultdict

import fussball_fetch as ff

# Rechtsformen und Füllwörter, die zum Vereinsnamen nichts beitragen
STRIP = {"fc", "afc", "cf", "ac", "sc", "cd", "ud", "rc", "rcd", "ss", "ssc", "as", "fk", "nk", "gnk",
         "hnk", "sk", "bk", "if", "ifk", "kv", "krc", "sv", "vfb", "vfl", "tsg", "fsv", "bsc", "ssv",
         "tsv", "spvgg", "ca", "club", "calcio", "cfc", "pfc", "kf", "de", "fotball", "football",
         "futbol", "boldklub", "sad", "bc", "fbc", "ks", "sl", "jk", "e", "y", "of", "the", "and"}
# Kennzeichen für zweite Mannschaften, Nachwuchs und Frauen
RESERVE = {"ii", "b", "u17", "u18", "u19", "u20", "u21", "u23", "jong", "amateure", "reserves", "res",
           "women", "w", "frauen", "fem", "femenino", "feminino", "ladies", "youth", "primavera",
           "academy", "sub"}
# Wörter, die allein keinen Verein bestimmen ("Red Star" ist nicht "Red Star Belgrade",
# "Kilwinning Rangers" nicht "Rangers")
GENERIC = {"real", "sporting", "inter", "united", "city", "athletic", "atletico", "racing", "dynamo",
           "dinamo", "olympic", "olympique", "union", "red", "star", "young", "boys", "rovers", "town",
           "county", "wanderers", "albion", "sport", "sports", "deportivo", "nacional", "universidad",
           "independiente", "central", "st", "saint", "san", "santa", "royal", "stade", "olympia",
           "juventud", "cs", "csu", "univ", "universitatea", "new", "la", "el", "al", "academica",
           "lokomotiv", "spartak", "slavia", "sparta", "hapoel", "maccabi", "beitar", "fortuna", "rapid",
           "admira", "austria", "vitoria", "america", "rangers", "hearts", "academical", "thistle",
           "villa", "borough", "harriers", "celtic", "north", "south", "east", "west", "fc"}

# Namensvarianten, die die Normalisierung nicht auflöst (normalisiert -> normalisiert)
ALIAS = {
    "csu craiova": "univ craiova",
    "universitatea craiova": "univ craiova",
    "red star belgrade": "crvena zvezda",
    "olympiacos": "olympiakos",
    "paok salonika": "paok",
    "steaua bucuresti": "fcsb",
    "godoy cruz antonio tomba": "godoy cruz",
    "san martin san juan": "san martin sj",
}

# Kontinentalverband je Land, wie in den Wettbewerben aus fussball_espn.EURO.
# Länder ohne Eintrag gehören zu keinem der geführten Europapokale.
SOUTH = {"Argentinien", "Brasilien", "Chile", "Kolumbien", "Uruguay", "Peru", "Ecuador", "Paraguay",
         "Bolivien", "Venezuela"}
NORTH = {"Mexiko", "USA", "Kanada", "Costa Rica", "Honduras", "Guatemala", "Panama", "Jamaika"}
NO_REGION = {"China", "Japan", "Saudi-Arabien", "Australien", "Südafrika"}


def region(country):
    if country in SOUTH:
        return "Südamerika"
    if country in NORTH:
        return "Nord- und Mittelamerika"
    return None if country in NO_REGION else "Europa"


def words(name):
    s = (name or "").replace("ß", "ss").replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("ł", "l")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['’`´.]", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).split()


def norm(name):
    """'FC Basel 1893' -> 'basel', 'Lech Poznań' -> 'lech poznan'"""
    raw = words(name)
    kept = [t for t in raw if t not in STRIP and not t.isdigit()]
    key = " ".join(kept) or " ".join(raw)
    return ALIAS.get(key, key)


def is_reserve(name):
    return any(t in RESERVE for t in words(name))


def subset_ok(short, long_):
    """Der längere Name ist der kürzere plus genau ein Wort, angehängt
    ('Hamilton Academical', 'Legia Warsaw') oder als allgemeines Wort davor
    ('Real Zaragoza'). Ein Ortsname davor ('Kilwinning Rangers') ist ein
    anderer Verein. Der kürzere Name braucht ein aussagekräftiges Wort."""
    a, b = short.split(), long_.split()
    if len(b) != len(a) + 1 or not any(len(t) >= 4 and t not in GENERIC for t in a):
        return False
    return b[:-1] == a or (b[1:] == a and b[0] in GENERIC)


def same_name(x, y, info, loose):
    tx, ty = info[x], info[y]
    if tx["reserve"] != ty["reserve"]:
        return False
    for k in tx["keys"]:
        for k2 in ty["keys"]:
            if k == k2 or (loose and (subset_ok(k, k2) or subset_ok(k2, k))):
                return True
    return False


def find(team, pool, info):
    """Eindeutigen Kandidaten aus pool oder (None, Grund). Erst gleicher Name,
    dann Teilname."""
    for loose in (False, True):
        hits = [c for c in pool if same_name(team, c, info, loose)]
        if len(hits) == 1:
            return hits[0], "Teilname" if loose else "Name"
        if len(hits) > 1:
            return None, f"mehrdeutig ({len(hits)})"
    return None, None


def team_info(rows, teams, leagues):
    """Je Team: Namen, Wettbewerbe, Land, Art und Rang der Quelle."""
    meta = {l["id"]: l for l in leagues}
    info = defaultdict(lambda: {"names": set(), "comps": set(), "lgs": set(), "kinds": set(),
                                "countries": set(), "regions": set(), "league_seasons": set(),
                                "top": False, "prio": 9, "rows": 0})
    for r in rows:
        lg = meta.get(r[1])
        if lg is None:
            continue
        kind = lg.get("kind") or "fd"
        for tid in (r[4], r[5]):
            t = info[tid]
            t["comps"].add((r[1], r[2]))
            t["lgs"].add(r[1])
            t["kinds"].add(kind)
            t["rows"] += 1
            if kind in ("fd", "league"):
                t["countries"].add(lg["country"])
                t["league_seasons"].add(r[2])
                t["top"] = t["top"] or lg.get("tier") == 1
                t["prio"] = min(t["prio"], 0 if kind == "fd" else 2)
            elif kind == "cup":
                t["countries"].add(lg["country"])
            elif kind == "euro":
                t["regions"].add(lg["country"])
    for tid, t in info.items():
        # Nur volle Namen: ESPN-Kurznamen wie "Portland" oder "Sunderland"
        # (für die U21) sind zu ungenau
        t["names"] |= {(teams.get(str(tid)) or {}).get("n"), ff.NAMES.get(tid)} - {None, ""}
        t["keys"] = {norm(n) for n in t["names"]}
        t["reserve"] = any(is_reserve(n) for n in t["names"])
    return info


def merge_leagues(league_teams, info, merge):
    """Schritt 1: Ligateams eines Landes aus verschiedenen Quellen."""
    by_country = defaultdict(list)
    for tid in league_teams:
        t = info[tid]
        country = sorted(t["countries"])[0] if t["countries"] else ""
        pool = [c for c in by_country[country]
                if info[c]["prio"] < t["prio"] and not (info[c]["league_seasons"] & t["league_seasons"])]
        hit, why = find(tid, pool, info) if pool else (None, None)
        if hit is not None:
            merge(tid, hit, "Liga", why)
        else:
            by_country[country].append(tid)
    return [tid for c in by_country.values() for tid in c]


def propose(candidates, pool_of, info, label, need_top_loose=False):
    """Schritte 2 und 3: Vorschläge sammeln, dann nur eindeutige übernehmen."""
    proposals, skipped = {}, []
    for tid in candidates:
        t = info[tid]
        # Spielt das Zielteam schon im selben Wettbewerb, ist es ein anderer Verein
        pool = [c for c in pool_of(t) if not (info[c]["lgs"] & t["lgs"])]
        hit, why = find(tid, pool, info)
        if hit is not None and why == "Teilname" and need_top_loose and not info[hit]["top"]:
            hit = None
        if hit is not None:
            proposals[tid] = (hit, why)
        elif why:
            skipped.append((tid, why))
    count = defaultdict(int)
    for hit, _ in proposals.values():
        count[hit] += 1
    out = []
    for tid, (hit, why) in proposals.items():
        if count[hit] == 1:
            out.append((tid, hit, label, why))
        else:
            skipped.append((tid, f"Ziel mehrfach beansprucht ({count[hit]})"))
    return out, skipped


def unify(rows, teams, leagues):
    """Schreibt vereinheitlichte IDs in rows (als neue Zeilen) und räumt teams
    auf. Gibt (Zusammenführungen, übersprungene mehrdeutige Fälle) zurück.

    Teams ohne Eintrag in teams (die OpenLigaDB-IDs der deutschen Ligen, deren
    Namen die Seite erst im Browser ergänzt) bleiben außen vor: weder Ziel noch
    Quelle, sonst stünden IDs ohne Namen in matches.json. Mit ihren Ligadaten
    verknüpft sind sie schon über den Ergebnisabgleich in fussball_espn."""
    info = team_info(rows, teams, leagues)
    for tid in [tid for tid in info if str(tid) not in teams]:
        del info[tid]
    league_teams = [tid for tid, t in info.items() if t["kinds"] & {"fd", "league"}]
    league_teams.sort(key=lambda tid: (info[tid]["prio"], -info[tid]["rows"], str(tid)))
    target, merges = {}, []

    def merge(tid, canon, how, why):
        target[tid] = canon
        a, b = info[tid], info[canon]
        for key in ("names", "keys", "comps", "lgs", "league_seasons"):
            b[key] |= a[key]
        b["top"] = b["top"] or a["top"]
        merges.append((tid, canon, how, why))

    canon = merge_leagues(league_teams, info, merge)
    canon_set = set(canon)
    rest = sorted((tid for tid in info if tid not in target and tid not in canon_set), key=str)

    cups = [tid for tid in rest if "cup" in info[tid]["kinds"]]
    found, skipped = propose(cups, lambda t: [c for c in canon if info[c]["countries"] & t["countries"]],
                             info, "Pokal")
    for tid, hit, how, why in found:
        merge(tid, hit, how, why)

    euro = [tid for tid in rest if tid not in target and "euro" in info[tid]["kinds"]
            and "intl" not in info[tid]["kinds"]]
    found, skipped_euro = propose(
        euro, lambda t: [c for c in canon if any(region(x) in t["regions"] for x in info[c]["countries"])],
        info, "Europapokal", need_top_loose=True)
    for tid, hit, how, why in found:
        merge(tid, hit, how, why)

    for i, r in enumerate(rows):
        h, a = target.get(r[4], r[4]), target.get(r[5], r[5])
        if h != r[4] or a != r[5]:
            rows[i] = r[:4] + [h, a] + r[6:]
    for tid in target:
        teams.pop(str(tid), None)
    return merges, skipped + skipped_euro


def describe(tid, teams):
    entry = teams.get(str(tid)) or {}
    return entry.get("n") or ff.NAMES.get(tid) or str(tid)
