"""Einmaliger Test: TheSportsDB (freier Schlüssel 3) für Ligen, die ESPN nicht führt.
Schreibt data/fussball/quellen_test.json."""
import json
import time
import urllib.parse
import urllib.request

B = "https://www.thesportsdb.com/api/v1/json/3"


def get(path):
    try:
        with urllib.request.urlopen(urllib.request.Request(B + path), timeout=30) as res:
            return json.loads(res.read())
    except Exception as err:
        return {"error": f"{type(err).__name__} {err}"}


out = {"leagues": {}}
for country in ["Denmark", "Poland", "Switzerland", "Romania", "Finland", "Ireland", "Czech Republic",
                "Croatia", "Serbia", "Austria", "Norway", "Sweden", "Japan", "South-Korea", "Germany"]:
    d = get("/search_all_leagues.php?c=" + urllib.parse.quote(country) + "&s=Soccer")
    ls = d.get("countries") or d.get("countrys") or []
    out["leagues"][country] = [(l.get("idLeague"), l.get("strLeague"), l.get("strCurrentSeason")) for l in ls]
    time.sleep(2.2)

# Stichprobe dänische 1. Division, falls gefunden
den = [l for l in out["leagues"].get("Denmark", []) if "1st" in (l[1] or "") or "1. Div" in (l[1] or "")]
if den:
    lid, name, season = den[0]
    for key, path in [("next", f"/eventsnextleague.php?id={lid}"), ("past", f"/eventspastleague.php?id={lid}"),
                      ("season", f"/eventsseason.php?id={lid}&s={season}")]:
        d = get(path)
        ev = d.get("events") or []
        out[key] = {"n": len(ev), "sample": ev[:2], "error": d.get("error")}
        time.sleep(2.2)
print(json.dumps(out, ensure_ascii=False)[:3000], flush=True)
with open("data/fussball/quellen_test.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
