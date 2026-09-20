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
OVERPASS = "https://overpass-api.de/api/interpreter"
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

RETAILERS = {
    "aldi": "Aldi", "lidl": "Lidl", "kaufland": "Kaufland", "netto": "Netto",
    "penny": "Penny", "rewe": "Rewe", "edeka": "Edeka", "real": "Real",
    "trinkgut": "Trinkgut", "getränke": "Getränkemarkt", "getranke": "Getränkemarkt",
    "famila": "Famila", "marktkauf": "Marktkauf", "combi": "Combi", "hit": "HIT",
    "norma": "Norma", "tegut": "tegut", "globus": "Globus", "dm": "dm", "rossmann": "Rossmann",
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


def retailer_of(name):
    low = (name or "").lower()
    for needle, label in RETAILERS.items():
        if needle in low:
            return label
    return (name or "").strip() or "Unbekannt"


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
                desc = as_text(r.get("description"))
                product = as_text(r.get("product"))
                if "monster" not in (brand + " " + desc + " " + product).lower():
                    continue
                adv = as_text(r.get("advertisers"))
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
    offers.sort(key=lambda o: (o["pricePerLitre"] is None, o["pricePerLitre"] or 999, o["price"] or 999))
    return offers


def fetch_stores():
    """Supermärkte und Getränkemärkte im Umkreis, aus OpenStreetMap."""
    parts = []
    for place in PLACES:
        for kind in ("supermarket", "convenience", "beverages"):
            parts.append(f'node["shop"="{kind}"](around:{RADIUS_M},{place["lat"]},{place["lon"]});')
            parts.append(f'way["shop"="{kind}"](around:{RADIUS_M},{place["lat"]},{place["lon"]});')
    query = "[out:json][timeout:90];(" + "".join(parts) + ");out center tags;"
    raw = request(OVERPASS, headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"},
                  data=urllib.parse.urlencode({"data": query}).encode(), timeout=120)
    if not raw:
        DIAG.append("Overpass nicht erreichbar, Karte bleibt leer")
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    stores = {}
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name") or tags.get("brand")
        if not name:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        retailer = retailer_of(tags.get("brand") or name)
        key = (round(lat, 5), round(lon, 5))
        stores[key] = {
            "name": name,
            "retailer": retailer,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "street": tags.get("addr:street", "") + (" " + tags.get("addr:housenumber", "") if tags.get("addr:housenumber") else ""),
            "city": tags.get("addr:city", ""),
        }
    out = list(stores.values())
    DIAG.append(f"Overpass: {len(out)} Filialen im Umkreis")
    return out


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
              f"({o['pricePerLitre'] or '?'} €/l), bis {o['to'] or '?'}")
    print("Hole Filialen …")
    stores = fetch_stores()

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
