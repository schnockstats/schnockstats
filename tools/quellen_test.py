"""Einmaliger Test: TheSportsDB-Abfragen nach Spieltag und Tag (freier Schlüssel 3).
Schreibt data/fussball/quellen_test.json."""
import json
import time
import urllib.request

B = "https://www.thesportsdb.com/api/v1/json/3"
TESTS = [
    ("round9", "/eventsround.php?id=4683&r=9&s=2026-2027"),
    ("round10", "/eventsround.php?id=4683&r=10&s=2026-2027"),
    ("day_league", "/eventsday.php?d=2026-10-03&l=4683"),
    ("day_soccer", "/eventsday.php?d=2026-10-03&s=Soccer"),
    ("round_pl", "/eventsround.php?id=4422&r=11&s=2026-2027"),
    ("table", "/lookuptable.php?l=4683&s=2026-2027"),
]
out = {}
for key, path in TESTS:
    try:
        with urllib.request.urlopen(urllib.request.Request(B + path), timeout=30) as res:
            d = json.loads(res.read())
        ev = d.get("events") or d.get("table") or []
        out[key] = {"n": len(ev), "first": [(e.get("strTimestamp"), e.get("strEvent"), e.get("intHomeScore"), e.get("intAwayScore"), e.get("strLeague")) for e in ev[:3]]}
    except Exception as err:
        out[key] = {"error": f"{type(err).__name__} {err}"}
    print(key, out[key], flush=True)
    time.sleep(2.5)
with open("data/fussball/quellen_test.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
