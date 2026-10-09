"""Implizite Markt-Torerwartungen aus den Quoten, vorberechnet für die Seite.

Aus 1-X-2 (und, falls vorhanden, Über 2.5) werden die erwarteten Tore beider
Teams zurückgerechnet, die mit dem Modell der Seite (Poisson mit Dixon-Coles-
Korrektur, rho = -0.05) dieselben Wahrscheinlichkeiten ergeben. Die Seite
braucht dafür sonst eine Rastersuche je Spiel im Browser.

Der Algorithmus bildet marketTarget, mkGrid, mkSummary und marketProbs aus
index.html Schritt für Schritt nach (gleiche Raster, gleiche Reihenfolge der
Nachbarn, gleiche Abbruchregeln), damit dieselben Werte herauskommen. Wer dort
etwas ändert, muss es hier nachziehen.

Ergebnisse werden in .cache/markt_lambdas.json zwischengespeichert (Schlüssel
aus den Quoten), so rechnet ein Lauf nur neue Quoten.
"""

import json
import math
import os

import fussball_fetch as ff

MK_N = 9
MK_OVER_MARGIN = 0.972   # wie in index.html
RHO = -0.05
CACHE_FILE = "markt_lambdas.json"
# Bei jeder Änderung am Rechenweg erhöhen, dann wird der Zwischenspeicher verworfen
CACHE_VERSION = 1

_POISSON = {}
CACHE = {}
USED = set()


def poisson_vector(lam):
    v = [0.0] * (MK_N + 1)
    v[0] = math.exp(-lam)
    for k in range(1, MK_N + 1):
        v[k] = v[k - 1] * lam / k
    return v


def mk_poisson(lam):
    """Wie mkPoisson: auf Tausendstel gerundet (Math.round, also halbe nach oben)."""
    k = math.floor(lam * 1000 + 0.5)
    v = _POISSON.get(k)
    if v is None:
        v = poisson_vector(k / 1000)
        _POISSON[k] = v
    return v


def tau(i, j, lh, la, rho):
    if i == 0 and j == 0:
        return 1 - lh * la * rho
    if i == 0 and j == 1:
        return 1 + lh * rho
    if i == 1 and j == 0:
        return 1 + la * rho
    if i == 1 and j == 1:
        return 1 - rho
    return 1


def mk_grid(lh, la, rho):
    ph, pa = mk_poisson(lh), mk_poisson(la)
    total = 0.0
    mat = []
    for i in range(MK_N + 1):
        row = []
        for j in range(MK_N + 1):
            x = max(0.0, ph[i] * pa[j] * tau(i, j, lh, la, rho))
            row.append(x)
            total += x
        mat.append(row)
    return mat, total


def mk_summary(mat, total):
    p_h = p_x = p_a = o25 = 0.0
    for i in range(MK_N + 1):
        row = mat[i]
        for j in range(MK_N + 1):
            v = row[j] / total
            if i > j:
                p_h += v
            elif i == j:
                p_x += v
            else:
                p_a += v
            if i + j > 2:
                o25 += v
    return p_h, p_x, p_a, o25


def market_target(oh, od, oa, o25):
    if oh is None or od is None or oa is None or oh < 1.01 or od < 1.01 or oa < 1.01:
        return None
    inv = (1 / oh, 1 / od, 1 / oa)
    z = inv[0] + inv[1] + inv[2]
    over = min(0.95, (1 / o25) * MK_OVER_MARGIN) if o25 is not None and o25 > 1.01 else None
    return inv[0] / z, inv[1] / z, inv[2] / z, over


def implied_lambdas(oh, od, oa, o25, rho=RHO):
    """(lh, la) auf drei Stellen gerundet, oder None ohne vollständige 1X2-Quoten."""
    tgt = market_target(oh, od, oa, o25)
    if tgt is None:
        return None

    def err(lh, la):
        sm = mk_summary(*mk_grid(lh, la, rho))
        e = (sm[0] - tgt[0]) ** 2 + (sm[1] - tgt[1]) ** 2 + (sm[2] - tgt[2]) ** 2
        if tgt[3] is not None:
            e += (sm[3] - tgt[3]) ** 2
        return e

    # Grobes Raster (Gleitkomma-Schritte wie in JS aufaddiert), danach feiner suchen
    best_e, best_lh, best_la = math.inf, 1.4, 1.1
    lh = 0.3
    while lh <= 3.61:
        la = 0.2
        while la <= 3.01:
            e = err(lh, la)
            if e < best_e:
                best_e, best_lh, best_la = e, lh, la
            la += 0.3
        lh += 0.3
    step = 0.15
    while step >= 0.0095:
        moved, guard = True, 0
        while moved and guard < 12:
            guard += 1
            moved = False
            for dh, da in ((step, 0), (-step, 0), (0, step), (0, -step),
                           (step, step), (-step, -step), (step, -step), (-step, step)):
                lh = min(5.0, max(0.15, best_lh + dh))
                la = min(5.0, max(0.15, best_la + da))
                e = err(lh, la)
                if e < best_e - 1e-12:
                    best_e, best_lh, best_la = e, lh, la
                    moved = True
        step /= 2
    # Runden wie Math.round(x * 1000) / 1000 in JS (halbe nach oben), nicht wie
    # Pythons round(): die Suche landet oft genau auf ...5 in der vierten Stelle
    return math.floor(best_lh * 1000 + 0.5) / 1000, math.floor(best_la * 1000 + 0.5) / 1000


def load(cache_dir):
    """Zwischenspeicher laden; eine kaputte Datei wird ignoriert (dann neu rechnen)."""
    CACHE.clear()
    USED.clear()
    path = os.path.join(cache_dir, CACHE_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("_v") == CACHE_VERSION:
            CACHE.update({k: v for k, v in data.items()
                          if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)})
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as err:
        ff.DIAG.append(f"{CACHE_FILE} unlesbar ({type(err).__name__}), wird neu berechnet")


def lambdas(oh, od, oa, o25):
    """[lh, la] für diese Quoten (aus dem Zwischenspeicher oder neu), sonst None."""
    if oh is None or od is None or oa is None:
        return None
    key = f"{oh}|{od}|{oa}|{o25}"
    USED.add(key)
    hit = CACHE.get(key)
    if hit is None:
        res = implied_lambdas(oh, od, oa, o25)
        hit = list(res) if res else None
        if hit:
            CACHE[key] = hit
    return hit


def safe_lambdas(oh, od, oa, o25):
    """Wie lambdas, aber Fehler werden protokolliert statt den Lauf abzubrechen."""
    try:
        return lambdas(oh, od, oa, o25) or [None, None]
    except Exception as err:
        if len(ff.DIAG) < 1000:
            ff.DIAG.append(f"Markt-Torerwartung {oh}/{od}/{oa}/{o25}: {type(err).__name__} {err}")
        return [None, None]


def save(cache_dir):
    """Nur die in diesem Lauf benutzten Schlüssel behalten, atomar schreiben."""
    os.makedirs(cache_dir, exist_ok=True)
    keep = {"_v": CACHE_VERSION}
    keep.update({k: CACHE[k] for k in sorted(USED) if k in CACHE})
    ff.write_json(os.path.join(cache_dir, CACHE_FILE), keep)
    return len(keep) - 1
