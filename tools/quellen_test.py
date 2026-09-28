"""Einmaliger Test: welche Fußball-Wettbewerbe ESPN führt (Kürzel und Name).
Schreibt data/fussball/quellen_test.json."""
import json
import re
import urllib.request

out = {}
url = "https://sports.core.api.espn.com/v2/sports/soccer/leagues?limit=1000"
try:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as res:
        data = json.loads(res.read())
    refs = [i.get("$ref", "") for i in data.get("items") or []]
    out["count"] = data.get("count")
    out["slugs"] = sorted({m.group(1) for r in refs for m in [re.search(r"/leagues/([^/?]+)", r)] if m})
except Exception as err:
    out["error"] = f"{type(err).__name__} {err}"
print(out.get("count"), len(out.get("slugs") or []), out.get("error"), flush=True)
with open("data/fussball/quellen_test.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
