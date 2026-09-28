"""Sucht aktuelle Monster-Energy-Angebote in Prospekten rund um Monheim und
Langenfeld und schreibt sie nach data/angebote/. Ohne Schlüssel, ohne Zusatzpakete.

Zwei Quellen:
  * marktguru.de wertet die Wochenprospekte der Ketten aus. Die Schlüssel für
    die Abfrage stehen offen im Seitenquelltext und werden bei jedem Lauf frisch
    geholt, damit der Job nicht an einem Wechsel scheitert.
  * OpenStreetMap (Overpass) liefert die Filialen im Umkreis, damit die
    Angebote auf einer Karte landen.

Aufruf:  python3 tools/angebote_fetch.py [--out data/angebote]
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MG_BASE = "https://api.marktguru.de/api/v1"
MG_SITE = "https://www.marktguru.de"
# Mehrere Overpass-Server, falls einer ablehnt oder überlastet ist
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
NOMINATIM = "https://nominatim.openstreetmap.org/search"
# OpenStreetMap-Dienste lehnen getarnte Browser-Kennungen ab (HTTP 406).
# Sie wollen eine ehrliche Kennung der Anwendung.
OSM_UA = "SchnockStats-MonsterRadar/1.1 (+https://github.com/schnockstats/schnockstats)"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Suchorte: die beiden Postleitzahlen plus Mittelpunkte für die Kartensuche
PLACES = [
    {"zip": "40789", "name": "Monheim am Rhein", "lat": 51.0919, "lon": 6.8925},
    {"zip": "40764", "name": "Langenfeld", "lat": 51.1084, "lon": 6.9481},
]
RADIUS_M = 9000

# Je Produkt: Suchbegriffe, Erkennungswort, Dateinamen und Vergleichsgröße.
#   metric "unit":  Preis je Einheit (Monster: immer 0,5-l-Dose)
#   metric "litre": Preis je Liter (Pepsi: Dosen, Flaschen, Packs gemischt)
PRODUCTS = {
    "monster": {
        "queries": ["monster energy", "monster"], "match": "monster",
        # Monster Munch (Chips von Lorenz) heißt auch "Monster"
        "exclude": ["munch", "lorenz"],
        "offers": "monster.json", "history": "history.json", "cache": "stores_cache.json",
        "metric": "unit", "unclear_above": 3.0,
    },
    "pepsi": {
        "queries": ["pepsi", "pepsi max", "pepsi cola"], "match": "pepsi",
        "offers": "pepsi.json", "history": "pepsi_history.json", "cache": "stores_cache_pepsi.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 6.0,
    },
    # Spezi ist eine Paulaner-Marke; "Spezi" allein trifft auch fremde Cola-Mix-Getränke.
    # Deshalb müssen beide Wörter vorkommen.
    "spezi": {
        "queries": ["paulaner spezi", "spezi"], "match": ["paulaner", "spezi"],
        "offers": "spezi.json", "history": "spezi_history.json", "cache": "stores_cache_spezi.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 6.0,
    },
    # Nur Coca-Cola: "Cola" allein träfe auch Pepsi, Fritz-Kola, Afri oder Discounter-Cola
    "cola": {
        "queries": ["coca cola", "coca-cola"], "match": ["coca", "cola"],
        "exclude": ["pepsi"],
        "offers": "cola.json", "history": "cola_history.json", "cache": "stores_cache_cola.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 6.0,
    },
    # Super Pop (Pamela Reif): funktionale Limo, nur in der 0,33-l-Dose
    "superpop": {
        "queries": ["super pop", "superpop"], "match": ["super", "pop"], "pattern": r"\bsuper\s*-?\s*pop\b",
        "exclude": ["popcorn", "lollipop", "pop-it", "pop it"],
        "offers": "superpop.json", "history": "superpop_history.json", "cache": "stores_cache_superpop.json",
        "metric": "unit", "unclear_above": 2.5,
    },
    # Rockstar Energy: wie Monster fast nur in der 0,5-l-Dose
    "rockstar": {
        "queries": ["rockstar energy", "rockstar"], "match": "rockstar",
        "offers": "rockstar.json", "history": "rockstar_history.json", "cache": "stores_cache_rockstar.json",
        "metric": "unit", "unclear_above": 3.0,
    },
    # Red Bull: 0,25 l, 0,355 l, 0,473 l ... deshalb Vergleich über den Literpreis
    "redbull": {
        "queries": ["red bull"], "match": ["red", "bull"], "pattern": r"\bred\s*-?\s*bull\b",
        "offers": "redbull.json", "history": "redbull_history.json", "cache": "stores_cache_redbull.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 12.0,
    },
    # Gönrgy: Energy in der 0,5-l-Dose; Prospekte schreiben mal "Gönrgy", mal "Goenrgy"
    "goenrgy": {
        "queries": ["gönrgy", "goenrgy"], "match": ["nrgy"], "pattern": r"\bg(?:ö|oe|o)nrgy\b",
        "offers": "goenrgy.json", "history": "goenrgy_history.json", "cache": "stores_cache_goenrgy.json",
        "metric": "unit", "unclear_above": 3.0,
    },
    # Capri-Sonne (früher Capri-Sun): 0,2-l-Beutel, meist im 10er-Pack, dazu größere Flaschen
    "caprisonne": {
        "queries": ["capri-sonne", "capri sonne", "capri-sun", "capri sun"], "match": ["capri"],
        "pattern": r"\bcapri[\s-]*(?:sonne|sun)\b",
        "offers": "caprisonne.json", "history": "caprisonne_history.json", "cache": "stores_cache_caprisonne.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 8.0,
    },
    # ---- Lecker Bierchen (eigene App, Daten unter data/bier) ----
    # Bier kommt als Kasten, Sixpack oder Flasche: verglichen wird der Literpreis
    "augustiner": {
        "queries": ["augustiner"], "match": ["augustiner"],
        "exclude": ["kloster", "schnaps", "likör", "glas "],
        "offers": "augustiner.json", "history": "augustiner_history.json", "cache": "stores_cache_augustiner.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 8.0,
    },
    "veltins": {
        "queries": ["veltins"], "match": ["veltins"],
        "offers": "veltins.json", "history": "veltins_history.json", "cache": "stores_cache_veltins.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 8.0,
    },
    # Nur das Helle: Spezi, Weißbier und Radler von Paulaner sind etwas anderes
    "paulanerhell": {
        "queries": ["paulaner hell", "paulaner münchner hell", "paulaner"], "match": ["paulaner"],
        "pattern": r"\bhell", "exclude": ["spezi", "weiß", "weiss", "weizen", "radler", "zwickl", "limo"],
        "offers": "paulanerhell.json", "history": "paulanerhell_history.json", "cache": "stores_cache_paulanerhell.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 8.0,
    },
    "peroni": {
        "queries": ["peroni"], "match": ["peroni"],
        "offers": "peroni.json", "history": "peroni_history.json", "cache": "stores_cache_peroni.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 9.0,
    },
    "corona": {
        "queries": ["corona extra", "corona"], "match": ["corona"], "pattern": r"\bcorona\b",
        "exclude": ["test", "maske", "virus"],
        "offers": "corona.json", "history": "corona_history.json", "cache": "stores_cache_corona.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 10.0,
    },
    "desperados": {
        "queries": ["desperados"], "match": ["desperados"],
        "offers": "desperados.json", "history": "desperados_history.json", "cache": "stores_cache_desperados.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 10.0,
    },
    "bayreuther": {
        "queries": ["bayreuther hell", "bayreuther"],
        "match": ["bayreuther"],
        "offers": "bayreuther.json", "history": "bayreuther_history.json", "cache": "stores_cache_bayreuther.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 9.0,
    },
    "floetzinger": {
        "queries": ["flötzinger", "floetzinger"],
        "match": ["tzinger"],
        "pattern": r"\bfl(?:ö|oe|o)tzinger\b",
        "offers": "floetzinger.json", "history": "floetzinger_history.json", "cache": "stores_cache_floetzinger.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 9.0,
    },
    "hackerpschorr": {
        "queries": ["hacker-pschorr", "hacker pschorr"],
        "match": ["pschorr"],
        "exclude": ["radler"],
        "offers": "hackerpschorr.json", "history": "hackerpschorr_history.json", "cache": "stores_cache_hackerpschorr.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 9.0,
    },
    "puelleken": {
        "queries": ["pülleken", "helles pülleken"],
        "match": ["lleken"],
        "pattern": r"\bp(?:ü|ue|u)lleken\b",
        "offers": "puelleken.json", "history": "puelleken_history.json", "cache": "stores_cache_puelleken.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 9.0,
    },
    "schreckenskammer": {
        "queries": ["schreckenskammer"],
        "match": ["schreckenskammer"],
        "offers": "schreckenskammer.json", "history": "schreckenskammer_history.json", "cache": "stores_cache_schreckenskammer.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 9.0,
    },
    "ozujsko": {
        "queries": ["ožujsko", "ozujsko"],
        "match": ["ujsko"],
        "pattern": r"\bo(?:ž|z)ujsko\b",
        "offers": "ozujsko.json", "history": "ozujsko_history.json", "cache": "stores_cache_ozujsko.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 9.0,
    },
    "tyskie": {
        "queries": ["tyskie"],
        "match": ["tyskie"],
        "offers": "tyskie.json", "history": "tyskie_history.json", "cache": "stores_cache_tyskie.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 9.0,
    },
    # Nur Bud von Anheuser-Busch. Es heißt in Deutschland "Bud"; "Budweiser" in
    # deutschen Prospekten ist fast immer das tschechische Budweiser Budvar.
    "budweiser": {
        "queries": ["bud", "bud lager", "busch bud"],
        "match": ["bud"],
        "pattern": r"\bbud\b",
        "exclude": ["budweiser", "budvar", "budějovick", "budejovick"],
        "offers": "budweiser.json", "history": "budweiser_history.json", "cache": "stores_cache_budweiser.json",
        "metric": "litre", "unclear_above": None, "unclear_litre_above": 10.0,
    },
}
PRODUCT = PRODUCTS["monster"]
QUERIES = PRODUCT["queries"]

# Bekannte Schlüssel als Rückfallebene, falls der Quelltext sie nicht mehr zeigt
FALLBACK_KEYS = {
    "x-apikey": "8Kk+pmbf7TgJ9nVj2cXeA7P5zBGv8iuutVVMRfOfvNE=",
    "x-clientkey": "WU/RH+PMGDi+gkZer3WbMelt6zcYHSTytNB7VpTia90=",
}

# Bekannte Ketten werden auf einen einheitlichen Namen gebracht. Alles andere
# behält seinen echten Namen, damit nie ein nichtssagendes "Getränkemarkt"
# stehen bleibt, bei dem unklar ist, welcher Laden gemeint ist.
RETAILERS = {
    "aldi": "Aldi", "lidl": "Lidl", "kaufland": "Kaufland", "netto": "Netto",
    "penny": "Penny", "rewe": "Rewe", "edeka": "Edeka", "real": "Real",
    "famila": "Famila", "marktkauf": "Marktkauf", "combi": "Combi",
    "norma": "Norma", "tegut": "tegut", "globus": "Globus",
    "dm-drogerie": "dm", "rossmann": "Rossmann", "trinkgut": "Trinkgut",
    "fristo": "Fristo", "getränke hoffmann": "Getränke Hoffmann",
    "getraenke hoffmann": "Getränke Hoffmann", "hol ab": "Hol ab",
    "hol'ab": "Hol ab", "dursty": "Dursty", "getränkeland": "Getränkeland",
    "trinkhalle": "Trinkhalle", "orterer": "Orterer",
}
DIAG = []


def request(url, headers=None, data=None, tries=3, timeout=60):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            DIAG.append(f"{url.split('?')[0]}: HTTP {err.code}")
            if err.code == 404 or attempt == tries - 1:
                return None
        except Exception as err:
            DIAG.append(f"{url.split('?')[0]}: {type(err).__name__}")
            if attempt == tries - 1:
                return None
        time.sleep(3 * (attempt + 1))
    return None


def api_keys():
    """Holt die aktuellen Schlüssel aus dem Seitenquelltext."""
    html = request(MG_SITE)
    keys = {}
    if html:
        for name in ("apiKey", "clientKey"):
            m = re.search(name + r'["\']?\s*[:=]\s*["\']([A-Za-z0-9+/=]{20,})["\']', html)
            if m:
                keys["x-apikey" if name == "apiKey" else "x-clientkey"] = m.group(1)
        for src in re.findall(r'src="([^"]+\.js)"', html)[:12]:
            if len(keys) >= 2:
                break
            js = request(urllib.parse.urljoin(MG_SITE, src))
            if not js:
                continue
            for name in ("apiKey", "clientKey"):
                key = "x-apikey" if name == "apiKey" else "x-clientkey"
                if key in keys:
                    continue
                m = re.search(name + r'["\']?\s*[:=]\s*["\']([A-Za-z0-9+/=]{20,})["\']', js)
                if m:
                    keys[key] = m.group(1)
    if len(keys) < 2:
        DIAG.append("Schlüssel nicht im Quelltext gefunden, nutze Rückfallwerte")
        keys = dict(FALLBACK_KEYS)
    return keys


# Großmärkte, in denen nur Gewerbekunden mit Karte einkaufen dürfen.
# Für Privatleute nutzlos, deshalb weder Angebote noch Filialen noch Verlauf.
WHOLESALE = ("handelshof", "metro", "selgros", "transgourmet", "c+c", "c&c", "cash & carry", "cash&carry", "cash and carry")


def is_not_a_drink(unit, *texts):
    """Gewichtsangaben verraten Lebensmittel statt Getränke:
    Einheit kg oder g, "75-g-Beutel", "200 g", Beutel, Chips."""
    u = (unit or "").strip().lower()
    if u in ("kg", "g", "100 g", "100g", "1 kg"):
        return True
    low = " ".join(t or "" for t in texts).lower()
    return bool(re.search(r"\d+\s*-?\s*g\b|\bbeutel\b|\bchips\b|\bsnack", low))


def is_wholesale(*names):
    low = " ".join(n or "" for n in names).lower()
    return any(w in low for w in WHOLESALE)


# Namen, die nichts aussagen: hier lohnt die Suche nach dem echten Betreiber
GENERIC = ("getränkemarkt", "getraenkemarkt", "getränke markt", "supermarkt",
           "verbrauchermarkt", "discounter", "lebensmittel", "markt", "getränke")


def is_generic(name):
    low = (name or "").strip().lower()
    return (not low) or low in GENERIC or low in ("unbekannt",)


def chain_in(blob):
    """Sucht im gesamten Datensatz nach einer bekannten Kette. Marktguru
    führt manche Prospekte unter einem Sammelnamen wie 'Getränkemarkt';
    der echte Betreiber steht dann meist an anderer Stelle im Datensatz."""
    low = (blob or "").lower()
    hits = [(low.index(needle), label) for needle, label in RETAILERS.items() if needle in low]
    return min(hits)[1] if hits else None


def retailer_of(name):
    low = (name or "").lower()
    for needle, label in RETAILERS.items():
        if needle in low:
            return label
    cleaned = re.sub(r"\s+(gmbh|kg|ohg|se|ag|& co\.? ?kg|markt|filiale)\b.*", "", (name or "").strip(),
                     flags=re.I)
    return cleaned.strip(" .,-") or (name or "").strip() or "Unbekannt"


def haversine(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, asin, sqrt
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return round(2 * 6371 * asin(sqrt(a)), 1)


def as_text(value, *keys):
    """Die API liefert manche Felder mal als Text, mal als Objekt.
    Holt in beiden Fällen eine brauchbare Zeichenkette heraus."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in keys or ("name", "shortName", "title", "value", "url", "text"):
            got = value.get(key)
            if isinstance(got, str) and got.strip():
                return got.strip()
        return ""
    if isinstance(value, list):
        for item in value:
            got = as_text(item, *keys)
            if got:
                return got
    return ""


def parse_litres(text):
    """0,5-l-Dose, 500 ml, 0.355 l ... -> Liter je Einheit"""
    # Prospekttexte schreiben das l oft als großes I ("0,33-I-Dose", "1-I-PET-FI.")
    low = (text or "").lower().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*-?\s*(?:l|i|ltr|liter)\b", low)
    if m:
        try:
            v = float(m.group(1))
            if 0.1 <= v <= 3:
                return v
        except ValueError:
            pass
    m = re.search(r"(\d{3})\s*ml", low)
    if m:
        return int(m.group(1)) / 1000
    return None


PACK_PATTERNS = [
    r"(\d{1,2})\s*[x×]\s*(?:0[,.]5|0[,.]355|500|355)",   # 10 x 0,5 l
    r"(\d{1,2})\s*[x×]\s*(?:dose|dosen|can|flasche|flaschen)",  # 4x Dose
    r"(\d{1,2})\s*[x×]\s*\d",                               # 6 x 1,5 l
    r"(\d{1,2})\s*[x×]\s*je\b",                             # 14 x je 1-l-PET-Fl.
    r"(\d{1,2})\s*er[-\s]?(?:pack|packung|tray|kiste|karton|multipack)?\b",  # 10er-Pack
    r"(\d{1,2})\s*(?:dosen|stück|stk\.?)\b",              # 12 Dosen
    r"(?:pack|packung|tray|kiste|karton)\s*(?:mit|à|a)?\s*(\d{1,2})\b",  # Tray mit 24
]


def parse_pack(text):
    """Packungsgröße aus Beschreibung oder Einheit, None bei Einzeldose."""
    low = (text or "").lower()
    for pat in PACK_PATTERNS:
        m = re.search(pat, low)
        if m:
            n = int(m.group(1))
            if 2 <= n <= 48:
                return n
    return None


# "je 1,25-l-Fl.", "je 0,5 l" oder "je Dose": der Preis gilt schon für eine Einheit.
# Nicht aber "je 12 x 1-l-Fl.-Kasten", "Je 18x 0,33 l" oder "14 x je 1-l-Fl.":
# dort steht "je" vor bzw. hinter der Packungsangabe und der Preis gilt fürs Ganze.
PER_UNIT = re.compile(r"(?<![x×] )(?<![x×])\bje\s+(?:\d+(?:[.,]\d+)?\s*-?\s*(?:l|i|ltr|liter|ml)\b"
                      r"|flasche|fl\.|dose|stück)")


def is_per_unit(text):
    return bool(PER_UNIT.search((text or "").lower()))


def parse_date(value):
    if not value:
        return None
    value = as_text(value) or str(value)
    value = value[:10]
    try:
        dt.date.fromisoformat(value)
        return value
    except ValueError:
        return None


def matches_product(haystack):
    """Alle Erkennungswörter müssen vorkommen; "pattern" verlangt zusätzlich
    eine genaue Schreibweise (etwa "Super Pop" statt "Supermarkt ... Popcorn")."""
    words = PRODUCT["match"] if isinstance(PRODUCT["match"], list) else [PRODUCT["match"]]
    if not all(w in haystack for w in words):
        return False
    return not PRODUCT.get("pattern") or bool(re.search(PRODUCT["pattern"], haystack))


def price_details(price, desc, unit, product):
    """Rechnet den Prospektpreis auf eine Einheit um (Dose bzw. Flasche) und
    ergänzt Packungsgröße, Füllmenge und Literpreis. price ist der Preis, wie er
    im Prospekt steht: bei Mehrfachpackungen also der Preis für die ganze Packung."""
    full_text = " ".join([desc or "", unit or "", product or ""]).lower()
    # Mehrfachpackungen auf den Preis je Dose umrechnen, damit alles vergleichbar bleibt
    pack = parse_pack(full_text)
    pack_price = price
    pack_unclear = False
    per_unit_already = is_per_unit(full_text)
    litres = parse_litres(desc) or parse_litres(unit) or parse_litres(product)
    if pack and price is not None and not per_unit_already:
        unit_price = price / pack
        too_low = (unit_price < 0.35) if PRODUCT["metric"] == "unit" else \
                  bool(litres and unit_price / litres < 0.25)
        if too_low:
            DIAG.append(f"Packpreis unplausibel, nicht geteilt: {(desc or '')[:60]} ({price})")
            pack_price = round(price * pack, 2)
        else:
            price = round(unit_price, 2)
    elif pack and price is not None:
        pack_price = round(price * pack, 2)
    elif price is not None and PRODUCT["unclear_above"] and price >= PRODUCT["unclear_above"]:
        # Deutlich über jedem Dosenpreis, aber keine Anzahl erkennbar
        pack_unclear = True
    per_litre = round(price / litres, 2) if (price and litres) else None
    limit = PRODUCT.get("unclear_litre_above")
    if per_litre and limit and per_litre >= limit:
        # Literpreis weit über jedem Ladenpreis: fast immer ein Kasten oder Tray,
        # dessen Stückzahl nicht erkannt wurde. Zeigen, aber nicht werten.
        DIAG.append(f"Literpreis unplausibel ({per_litre} €/l): {(desc or '')[:60]}")
        pack_unclear = True
    return {
        "price": price,
        "packSize": pack,
        "packPrice": pack_price if pack else None,
        "packUnclear": pack_unclear,
        "litres": litres,
        "pricePerLitre": per_litre,
    }


def fetch_offers(keys):
    headers = dict(keys)
    headers["User-Agent"] = UA
    headers["Accept"] = "application/json"
    found = {}
    for place in PLACES:
        for q in QUERIES:
            url = (f"{MG_BASE}/offers/search?as=web&limit=80&offset=0"
                   f"&q={urllib.parse.quote(q)}&zipCode={place['zip']}")
            raw = request(url, headers=headers)
            time.sleep(1.5)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                DIAG.append(f"Antwort für {q}/{place['zip']} war kein JSON")
                continue
            results = data.get("results") or []
            DIAG.append(f"{q} @ {place['zip']}: {len(results)} Treffer")
            for r in results:
                brand = as_text(r.get("brand"))
                desc = re.sub(r"\u00ad\s*", "", as_text(r.get("description")))
                product = as_text(r.get("product"))
                haystack = (brand + " " + desc + " " + product).lower()
                if not matches_product(haystack):
                    continue
                if any(w in haystack for w in PRODUCT.get("exclude", [])) or \
                        is_not_a_drink(as_text(r.get("unit"), "shortName", "name"), desc, product):
                    DIAG.append(f"Kein Getränk, übersprungen: {brand} {desc[:50]}")
                    continue
                adv = as_text(r.get("advertisers"))
                if is_wholesale(adv):
                    DIAG.append(f"Großmarkt übersprungen: {adv}")
                    continue
                if is_generic(retailer_of(adv)):
                    # Sammelname: im ganzen Datensatz nach der echten Kette suchen
                    chain = chain_in(json.dumps(r, ensure_ascii=False))
                    if chain:
                        DIAG.append(f"Sammelname '{adv}' als {chain} erkannt")
                        adv = chain
                validity = (r.get("validityDates") or [{}])[0]
                if not isinstance(validity, dict):
                    validity = {}
                price = r.get("price")
                if isinstance(price, dict):
                    price = price.get("value") or price.get("amount")
                if isinstance(price, str):
                    price = price.replace("\u20ac", "").replace(",", ".").strip()
                try:
                    price = float(price) if price not in (None, "") else None
                except (TypeError, ValueError):
                    price = None
                unit = as_text(r.get("unit"), "shortName", "name")
                pd = price_details(price, desc, unit, product)
                price = pd["price"]
                key = (retailer_of(adv), price, as_text(validity.get("from"))[:10], as_text(validity.get("to"))[:10])
                if key in found:
                    found[key]["zips"] = sorted(set(found[key]["zips"] + [place["zip"]]))
                    continue
                found[key] = {
                    "retailer": retailer_of(adv),
                    "advertiser": adv,
                    "brand": brand,
                    "description": desc or product,
                    "price": price,
                    "packSize": pd["packSize"],
                    "packPrice": pd["packPrice"],
                    "packUnclear": pd["packUnclear"],
                    "unit": unit,
                    "litres": pd["litres"],
                    "pricePerLitre": pd["pricePerLitre"],
                    "from": parse_date(as_text(validity.get("from"))),
                    "to": parse_date(as_text(validity.get("to"))),
                    "image": as_text(r.get("imageUrl")) or as_text(r.get("images"), "url"),
                    "zips": [place["zip"]],
                }
    offers = list(found.values())
    # Monster gibt es in der 0,5-l-Dose, Angebote gelten für alle Sorten: es zählt nur der Dosenpreis
    offers.sort(key=lambda o: (metric_of(o) is None or o.get("packUnclear"), metric_of(o) or 999))
    return offers


def _store_from(name, retailer, lat, lon, street="", city="", zip_="", hours=""):
    nearest = min(PLACES, key=lambda pl: haversine(lat, lon, pl["lat"], pl["lon"]))
    return {
        "name": name,
        "retailer": retailer,
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "street": street.strip(),
        "city": city,
        "zip": zip_,
        "hours": hours,
        "near": nearest["name"],
        "dist": haversine(lat, lon, nearest["lat"], nearest["lon"]),
    }


def parse_overpass(data):
    out = []
    for el in (data or {}).get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name") or tags.get("brand")
        if not name:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        street = tags.get("addr:street", "")
        if tags.get("addr:housenumber"):
            street += " " + tags["addr:housenumber"]
        out.append(_store_from(name, retailer_of(tags.get("brand") or tags.get("operator") or name),
                               lat, lon, street, tags.get("addr:city", ""), tags.get("addr:postcode", ""),
                               tags.get("opening_hours", "")))
    return out


def parse_nominatim(items, retailer):
    out = []
    for it in items or []:
        try:
            lat, lon = float(it.get("lat")), float(it.get("lon"))
        except (TypeError, ValueError):
            continue
        addr = it.get("address") or {}
        name = it.get("name") or retailer
        # Nur echte Geschäfte, keine Straßen oder Orte mit gleichem Namen
        kind = it.get("category") or it.get("class")
        if kind not in ("shop", "amenity"):
            continue
        street = (addr.get("road", "") + " " + addr.get("house_number", "")).strip()
        city = addr.get("city") or addr.get("town") or addr.get("village") or ""
        hours = ((it.get("extratags") or {}).get("opening_hours") or "").strip()
        out.append(_store_from(name, retailer_of(name) if retailer_of(name) != name else retailer,
                               lat, lon, street, city, addr.get("postcode", ""), hours))
    return out


def dedupe(stores):
    seen = {}
    for st in stores:
        if is_wholesale(st.get("name"), st.get("retailer")):
            continue
        key = (round(st["lat"], 4), round(st["lon"], 4))
        if key not in seen or (not seen[key]["street"] and st["street"]):
            seen[key] = st
    return sorted(seen.values(), key=lambda st: st["dist"])


def fetch_overpass():
    parts = []
    for place in PLACES:
        for kind in ("supermarket", "convenience", "beverages"):
            parts.append(f'node["shop"="{kind}"](around:{RADIUS_M},{place["lat"]},{place["lon"]});')
            parts.append(f'way["shop"="{kind}"](around:{RADIUS_M},{place["lat"]},{place["lon"]});')
    query = "[out:json][timeout:90];(" + "".join(parts) + ");out center tags;"
    headers = {"User-Agent": OSM_UA, "Accept": "application/json",
               "Content-Type": "application/x-www-form-urlencoded"}
    body = urllib.parse.urlencode({"data": query}).encode()
    for url in OVERPASS_MIRRORS:
        raw = request(url, headers=headers, data=body, tries=2, timeout=120)
        if not raw:
            # Manche Server nehmen nur GET an
            raw = request(url + "?" + urllib.parse.urlencode({"data": query}),
                          headers={"User-Agent": OSM_UA, "Accept": "application/json"}, tries=1, timeout=120)
        if not raw:
            continue
        try:
            stores = parse_overpass(json.loads(raw))
        except ValueError:
            continue
        if stores:
            DIAG.append(f"Overpass ({url.split('/')[2]}): {len(stores)} Märkte")
            return stores
    return []


def fetch_nominatim(retailers):
    """Gezielte Suche je Kette und Ort. Langsam (1 Anfrage je Sekunde),
    aber zuverlässig und genau für die Ketten, die gerade zählen."""
    out = []
    for retailer in sorted(set(retailers)):
        for place in PLACES:
            params = {"q": f"{retailer} {place['name']}", "format": "jsonv2", "addressdetails": 1, "extratags": 1,
                      "limit": 15, "countrycodes": "de",
                      "viewbox": f"{place['lon'] - .12},{place['lat'] + .08},{place['lon'] + .12},{place['lat'] - .08}",
                      "bounded": 1}
            raw = request(NOMINATIM + "?" + urllib.parse.urlencode(params),
                          headers={"User-Agent": OSM_UA, "Accept": "application/json"}, tries=2, timeout=40)
            time.sleep(1.2)
            if not raw:
                continue
            try:
                items = json.loads(raw)
            except ValueError:
                continue
            found = [st for st in parse_nominatim(items, retailer) if st["dist"] <= RADIUS_M / 1000]
            out.extend(found)
    DIAG.append(f"Nominatim: {len(out)} Treffer für {len(set(retailers))} Ketten")
    return out


def fetch_stores(offer_retailers, cache_path):
    """Erst alle Märkte über Overpass, dann gezielt die Ketten mit Angebot über
    Nominatim ergänzen. Klappt beides nicht, bleibt die letzte gute Liste."""
    stores = fetch_overpass()
    have = {st["retailer"] for st in stores}
    missing = [r for r in offer_retailers if r not in have]
    wanted = missing if stores else list(set(offer_retailers) | {
        "Aldi", "Lidl", "Netto", "Penny", "Rewe", "Edeka", "Kaufland", "Getränke Hoffmann", "Trinkgut"})
    if wanted:
        stores += fetch_nominatim(wanted)
    stores = dedupe(stores)
    if stores:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(stores, fh, ensure_ascii=False, separators=(",", ":"))
        return stores
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cached = json.load(fh)
        DIAG.append(f"Filialdienste nicht erreichbar, nutze letzte gespeicherte Liste ({len(cached)} Märkte)")
        return cached
    DIAG.append("Keine Filialdaten verfügbar")
    return []


def iso_week(day):
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def offer_day(frm, to, today):
    """Stichtag für die Wochenzuordnung: die Mitte des Angebotszeitraums.
    Prospekte starten oft schon am Sonntag, gelten aber für die folgende Woche;
    der Starttag allein würde sie der Vorwoche zuschlagen."""
    def parse(v):
        try:
            return dt.date.fromisoformat(v) if v else None
        except ValueError:
            return None
    a, b = parse(frm), parse(to)
    if a and b and b >= a:
        return a + (b - a) // 2
    return a or b or today


def metric_of(o):
    return o.get("pricePerLitre") if PRODUCT["metric"] == "litre" else o.get("price")


def update_history(offers, path, today=None, keep_weeks=156):
    """Preisverlauf: je Kalenderwoche und Kette der günstigste Dosenpreis.
    Maßgeblich ist die Woche, in der ein Angebot startet. Jeder Lauf ergänzt
    oder bestätigt die Einträge; ein Angebot, das mehrmals gesehen wird,
    zählt nur einmal. Bereits abgeschlossene Wochen bleiben unverändert."""
    today = today or dt.date.today()
    history = {"weeks": {}}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                history = json.load(fh)
        except (ValueError, OSError):
            history = {"weeks": {}}
    weeks = history.setdefault("weeks", {})
    # Ältere Einträge nach derselben Regel neu einsortieren (einmalige Korrektur)
    fixed = {}
    for key, wk in weeks.items():
        for name, v in (wk.get("shops") or {}).items():
            day = offer_day(v.get("from"), v.get("to"), None)
            k2 = iso_week(day) if day else key
            mon = (day - dt.timedelta(days=day.weekday())).isoformat() if day else wk.get("monday")
            slot = fixed.setdefault(k2, {"monday": mon, "shops": {}})
            cur = slot["shops"].get(name)
            if cur is None or v["price"] < cur["price"]:
                slot["shops"][name] = v
    weeks.clear()
    weeks.update(fixed)
    # Großmärkte auch aus älteren Einträgen entfernen
    for wk in list(weeks.values()):
        for name in [n for n in wk["shops"] if is_wholesale(n)]:
            del wk["shops"][name]
    for key in [k for k, wk in weeks.items() if not wk["shops"]]:
        del weeks[key]
    for o in offers:
        value = metric_of(o)
        if value is None or o.get("packUnclear"):
            continue
        day = offer_day(o.get("from"), o.get("to"), today)
        key = iso_week(day)
        monday = day - dt.timedelta(days=day.weekday())
        wk = weeks.setdefault(key, {"monday": monday.isoformat(), "shops": {}})
        cur = wk["shops"].get(o["retailer"])
        if cur is None or value < cur["price"]:
            wk["shops"][o["retailer"]] = {"price": value, "from": o.get("from"), "to": o.get("to")}
    # Ältestes abschneiden, damit die Datei klein bleibt
    for key in sorted(weeks)[:-keep_weeks]:
        del weeks[key]
    history["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(history, fh, ensure_ascii=False, separators=(",", ":"))
    return history


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/angebote")
    ap.add_argument("--product", default="monster", choices=sorted(PRODUCTS))
    args = ap.parse_args()
    global PRODUCT, QUERIES
    PRODUCT = PRODUCTS[args.product]
    QUERIES = PRODUCT["queries"]
    os.makedirs(args.out, exist_ok=True)

    print("Hole Schlüssel …")
    keys = api_keys()
    print("Suche Angebote …")
    offers = fetch_offers(keys)
    print(f"  {len(offers)} Angebote gefunden ({args.product})")
    for o in offers[:10]:
        print(f"  {o['retailer']}: {o['description']} {o['price']} € "
              f"bis {o['to'] or '?'}")
    history = update_history(offers, os.path.join(args.out, PRODUCT["history"]))
    print(f"  Preisverlauf: {len(history['weeks'])} Wochen gespeichert")
    print("Hole Filialen …")
    stores = fetch_stores([o["retailer"] for o in offers], os.path.join(args.out, PRODUCT["cache"]))
    print(f"  {len(stores)} Märkte im Umkreis")

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "places": PLACES,
        "radius": RADIUS_M,
        "offers": offers,
        "stores": stores,
        "diagnose": DIAG[-25:],
    }
    with open(os.path.join(args.out, PRODUCT["offers"]), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), ensure_ascii=False)
    print(f"Fertig. {len(offers)} Angebote, {len(stores)} Filialen.")
    if not offers:
        print(f"Hinweis: gerade kein {args.product} im Angebot, das ist ein gültiges Ergebnis.", file=sys.stderr)


if __name__ == "__main__":
    main()
