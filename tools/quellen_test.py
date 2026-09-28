"""Einmaliger Test, welche Datenquellen aus GitHub Actions erreichbar sind.
Schreibt data/fussball/quellen_test.json."""
import json
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
URLS = [
    ("espn_site_ua", "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard", True),
    ("espn_site_plain", "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard", False),
    ("espn_web", "https://site.web.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard", True),
    ("espn_core", "https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1/events?limit=3", True),
    ("espn_cdn", "https://cdn.espn.com/core/soccer/scoreboard?xhr=1&league=eng.1", True),
    ("sportsdb", "https://www.thesportsdb.com/api/v1/json/3/eventsnextleague.php?id=4328", True),
    ("oldb_leagues", "https://api.openligadb.de/getavailableleagues", True),
    ("fotmob", "https://www.fotmob.com/api/leagues?id=47", True),
    ("fd_org", "https://api.football-data.org/v4/competitions", True),
]

out = {}
for key, url, ua in URLS:
    headers = {"User-Agent": UA, "Accept": "application/json"} if ua else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as res:
            body = res.read()
            out[key] = {"status": res.status, "len": len(body),
                        "cors": res.headers.get("Access-Control-Allow-Origin"),
                        "head": body[:300].decode("utf-8", "replace")}
            if key == "oldb_leagues":
                data = json.loads(body)
                out[key]["leagues"] = sorted(
                    f"{l.get('leagueShortcut')}|{l.get('leagueSeason')}|{l.get('leagueName')}"
                    for l in data if str(l.get("leagueSeason")) in ("2025", "2026"))
    except Exception as err:
        out[key] = {"error": f"{type(err).__name__} {err}"}
    print(key, {k: v for k, v in out[key].items() if k != "leagues"}, flush=True)

with open("data/fussball/quellen_test.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
