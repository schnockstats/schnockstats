"""Einmaliger Test der ESPN-Abfrageformen aus GitHub Actions.
Schreibt data/fussball/quellen_test.json."""
import json
import urllib.error
import urllib.request

B = "https://site.api.espn.com/apis/site/v2/sports/soccer"
URLS = [
    ("plain", f"{B}/eng.1/scoreboard"),
    ("range_limit", f"{B}/eng.1/scoreboard?dates=20260901-20260930&limit=1000"),
    ("range", f"{B}/eng.1/scoreboard?dates=20260901-20260930"),
    ("range_l200", f"{B}/eng.1/scoreboard?dates=20260901-20260930&limit=200"),
    ("day", f"{B}/eng.1/scoreboard?dates=20260926"),
    ("month", f"{B}/eng.1/scoreboard?dates=202609"),
    ("jpn_range", f"{B}/jpn.1/scoreboard?dates=20260901-20261015&limit=500"),
    ("den2", f"{B}/den.2/scoreboard"),
    ("fa", f"{B}/eng.fa/scoreboard"),
    ("unl", f"{B}/uefa.nations/scoreboard"),
    ("ucl", f"{B}/uefa.champions/scoreboard"),
    ("cdr", f"{B}/esp.copa_del_rey/scoreboard"),
    ("teams_jpn", f"{B}/jpn.1/teams"),
    ("web_range", "https://site.web.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard?dates=20260901-20260930&limit=500"),
]

out = {}
for key, url in URLS:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=20) as res:
            body = res.read()
            data = json.loads(body)
            evs = data.get("events") or []
            info = {"status": res.status, "len": len(body), "events": len(evs)}
            if evs:
                e = evs[0]
                c = (e.get("competitions") or [{}])[0]
                info["first"] = e.get("date"), e.get("name")
                info["last"] = evs[-1].get("date")
                comp = (c.get("competitors") or [{}])[0]
                info["stat_names"] = [s.get("name") for s in comp.get("statistics") or []]
                info["has_details"] = bool(c.get("details"))
                info["detail_sample"] = (c.get("details") or [None])[0]
                info["linescores"] = comp.get("linescores")
                info["status"] = (c.get("status") or {}).get("type")
            out[key] = info
    except urllib.error.HTTPError as err:
        out[key] = {"http": err.code, "body": err.read()[:200].decode("utf-8", "replace")}
    except Exception as err:
        out[key] = {"error": f"{type(err).__name__} {err}"}
    print(key, out[key], flush=True)

with open("data/fussball/quellen_test.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
