"""Einmaliger Test: Quellen für europäisches Eishockey aus GitHub Actions.
Schreibt data/fussball/quellen_test.json."""
import json
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
URLS = [
    ("livescore_day", "https://prod-public-api.livescore.com/v1/api/app/date/hockey/20260926/0?MD=1", False),
    ("livescore_day_ua", "https://prod-public-api.livescore.com/v1/api/app/date/hockey/20260926/0?MD=1", True),
    ("espn_hockey_leagues", "https://sports.core.api.espn.com/v2/sports/hockey/leagues?limit=100", False),
    ("liiga", "https://liiga.fi/api/v2/games?tournament=runkosarja&season=2027", True),
    ("oldb_del", "https://api.openligadb.de/getmatchdata/del/2026", True),
    ("sofascore", "https://api.sofascore.com/api/v1/sport/ice-hockey/scheduled-events/2026-09-26", True),
    ("tsdb_round", "https://www.thesportsdb.com/api/v1/json/3/eventsround.php?id=4419&r=1&s=2026-2027", False),
]
for c in ["Sweden", "Finland", "Switzerland", "Czech Republic", "Austria", "Germany"]:
    URLS.append(("tsdb_" + c, "https://www.thesportsdb.com/api/v1/json/3/search_all_leagues.php?c="
                 + urllib.parse.quote(c) + "&s=Ice_Hockey", False))

out = {}
for key, url, ua in URLS:
    headers = {"User-Agent": UA, "Accept": "application/json"} if ua else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as res:
            body = res.read()
            info = {"status": res.status, "len": len(body), "cors": res.headers.get("Access-Control-Allow-Origin")}
            try:
                d = json.loads(body)
            except ValueError:
                d = None
            if key.startswith("livescore") and d:
                stages = d.get("Stages") or []
                info["stages"] = [(s.get("Cnm"), s.get("Snm"), len(s.get("Events") or [])) for s in stages][:80]
                if stages and stages[0].get("Events"):
                    info["sample"] = stages[0]["Events"][0]
            elif key.startswith("tsdb_") and d:
                ls = d.get("countries") or d.get("countrys") or []
                info["leagues"] = [(l.get("idLeague"), l.get("strLeague"), l.get("strCurrentSeason")) for l in ls]
            elif key == "tsdb_round" and d:
                info["events"] = [(e.get("strTimestamp"), e.get("strEvent"), e.get("intHomeScore")) for e in d.get("events") or []]
            elif key == "espn_hockey_leagues" and d:
                info["refs"] = [i.get("$ref", "")[:120] for i in d.get("items") or []]
            elif key == "liiga" and isinstance(d, list):
                info["n"] = len(d)
                info["sample"] = d[:1]
            elif key == "oldb_del" and isinstance(d, list):
                info["n"] = len(d)
                info["fin"] = sum(1 for m in d if m.get("matchIsFinished"))
            else:
                info["head"] = body[:300].decode("utf-8", "replace")
            out[key] = info
    except Exception as err:
        out[key] = {"error": f"{type(err).__name__} {err}"}
    print(key, json.dumps(out[key], ensure_ascii=False)[:400], flush=True)
    time.sleep(2.2 if "thesportsdb" in url else 0.5)

with open("data/fussball/quellen_test.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
