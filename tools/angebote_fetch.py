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
QUERIES = ["monster energy", "monster"]

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
    low = (text or "").lower().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-)?\s*l\b", low)
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
                if "monster" not in (brand + " " + desc + " " + product).lower():
                    continue
                adv = as_text(r.get("advertisers"))
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
                litres = parse_litres(desc) or parse_litres(unit) or parse_litres(product)
                key = (retailer_of(adv), desc or product, price)
                if key in found:
                    found[key]["zips"] = sorted(set(found[key]["zips"] + [place["zip"]]))
                    continue
                found[key] = {
                    "retailer": retailer_of(adv),
                    "advertiser": adv,
                    "brand": brand,
                    "description": desc or product,
                    "price": price,
                    "unit": unit,
                    "litres": litres,
                    "pricePerLitre": round(price / litres, 2) if (price and litres) else None,
                    "from": parse_date(as_text(validity.get("from"))),
                    "to": parse_date(as_text(validity.get("to"))),
                    "image": as_text(r.get("imageUrl")) or as_text(r.get("images"), "url"),
                    "zips": [place["zip"]],
                }
    offers = list(found.values())
    # Monster gibt es in der 0,5-l-Dose, Angebote gelten für alle Sorten: es zählt nur der Dosenpreis
    offers.sort(key=lambda o: (o["price"] is None, o["price"] or 999))
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
    for o in offers:
        if o.get("price") is None:
            continue
        day = offer_day(o.get("from"), o.get("to"), today)
        key = iso_week(day)
        monday = day - dt.timedelta(days=day.weekday())
        wk = weeks.setdefault(key, {"monday": monday.isoformat(), "shops": {}})
        cur = wk["shops"].get(o["retailer"])
        if cur is None or o["price"] < cur["price"]:
            wk["shops"][o["retailer"]] = {"price": o["price"], "from": o.get("from"), "to": o.get("to")}
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
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print("Hole Schlüssel …")
    keys = api_keys()
    print("Suche Angebote …")
    offers = fetch_offers(keys)
    print(f"  {len(offers)} Monster-Angebote gefunden")
    for o in offers[:10]:
        print(f"  {o['retailer']}: {o['description']} {o['price']} € "
              f"bis {o['to'] or '?'}")
    history = update_history(offers, os.path.join(args.out, "history.json"))
    print(f"  Preisverlauf: {len(history['weeks'])} Wochen gespeichert")
    print("Hole Filialen …")
    stores = fetch_stores([o["retailer"] for o in offers], os.path.join(args.out, "stores_cache.json"))
    print(f"  {len(stores)} Märkte im Umkreis")

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "places": PLACES,
        "radius": RADIUS_M,
        "offers": offers,
        "stores": stores,
        "diagnose": DIAG[-25:],
    }
    with open(os.path.join(args.out, "monster.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), ensure_ascii=False)
    print(f"Fertig. {len(offers)} Angebote, {len(stores)} Filialen.")
    if not offers:
        print("Hinweis: gerade kein Monster im Angebot, das ist ein gültiges Ergebnis.", file=sys.stderr)


if __name__ == "__main__":
    main()
