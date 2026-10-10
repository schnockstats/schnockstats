/* schnockstats → bwin: legt einen Bon in den bwin-Wettschein.
   Läuft als Lesezeichen-Knopf auf www.bwin.de. Der Knopf auf dem Bon öffnet bwin mit
   #schnock=… (Teams, Anstoß, Ereignis). Dieses Skript sucht die Spiele über die
   Quoten-Schnittstelle der bwin-Seite, die nur von bwin.de selbst aus lesbar ist,
   zeigt, was es gefunden hat, und füllt auf Wunsch den Wettschein über
   /de/sports?options=Spiel-Markt-Option,… Platziert wird nichts: Einsatz und
   „Wette platzieren“ bleiben bei dir. Die Schnittstelle ist inoffiziell; ändert bwin
   sie, findet das Skript eben nichts, kaputt geht dabei nichts. */
(function () {
  'use strict';
  const AID = 'NWQyNmIwMjUtZDQ3NC00NDQxLWI5YTktNjdkYjZjOTg1OWEz';
  const API = '/cds-api/bettingoffer/';
  const Q = `x-bwin-accessid=${AID}&lang=de&country=DE&userCountry=DE`;
  const SPORT = { f: 4, h: 12 };
  const HOUR = 3600 * 1000;
  // Gleiche Paarung binnen 12 Stunden um den Anstoß: fängt verschobene Zeiten ab,
  // zwei Spiele derselben Teams liegen nie so dicht beieinander
  const WINDOW = 12 * HOUR;
  const PAGE = 500;
  const PARALLEL = 6;
  // Höchstzahl Tipps je Schein. bwin nennt keine Zahl und lehnt 166 ab; 20 ist bei
  // Buchmachern üblich und passt zu den 20er-Batterien
  const SLIP_MAX = 20;
  const BOX_ID = 'schnock-bwin';

  /* ---------- Anzeige ---------- */
  function box() {
    const old = document.getElementById(BOX_ID);
    if (old) old.remove();
    const el = document.createElement('div');
    el.id = BOX_ID;
    el.style.cssText = 'position:fixed;z-index:2147483647;top:12px;left:50%;transform:translateX(-50%);width:min(440px,calc(100vw - 24px));max-height:calc(100vh - 24px);overflow:auto;'
      + 'background:#f6f4ec;color:#1d1c19;font:14px/1.4 ui-monospace,Menlo,Consolas,monospace;border-radius:10px;box-shadow:0 10px 40px rgba(0,0,0,.55);padding:16px;box-sizing:border-box';
    document.body.appendChild(el);
    return el;
  }
  function node(tag, css, text) {
    const n = document.createElement(tag);
    if (css) n.style.cssText = css;
    if (text != null) n.textContent = text;
    return n;
  }
  function button(label, primary, onClick) {
    const b = node('button', 'font:inherit;font-weight:700;padding:8px 14px;border-radius:6px;cursor:pointer;margin:12px 8px 0 0;'
      + (primary ? 'background:#126e4c;color:#fff;border:0' : 'background:none;color:#1d1c19;border:1px solid #9b988b'), label);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }
  let statusLine = null;
  const progress = (text) => { if (statusLine) statusLine.textContent = text; };
  function message(title, text) {
    const el = box();
    el.append(node('b', 'display:block;font-size:16px;margin-bottom:6px', title), node('div', '', text), button('Schließen', false, () => el.remove()));
  }

  /* ---------- Bon aus dem Link ---------- */
  // Den Bon für die Sitzung merken: Nach dem Füllen des Wettscheins fehlt #schnock=… in der
  // Adresse, für das nächste Paket soll das Lesezeichen trotzdem wissen, um welchen Bon es geht
  const SAVE_KEY = 'schnockstats-bon';
  function savedHash() {
    try { return sessionStorage.getItem(SAVE_KEY); } catch (err) { return null; }
  }
  function readBon() {
    const m = location.hash.match(/schnock=([A-Za-z0-9_-]+)/) || [null, savedHash()];
    if (!m[1]) return null;
    try { sessionStorage.setItem(SAVE_KEY, m[1]); } catch (err) { /* ohne Speicher eben nur dieses Mal */ }
    try {
      const b64 = m[1].replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((m[1].length + 3) % 4);
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const bon = JSON.parse(new TextDecoder().decode(bytes));
      if (bon.v !== 1 || !SPORT[bon.s] || !Array.isArray(bon.p)) return null;
      // [Heim lang, Heim kurz, Gast lang, Gast kurz, Anstoß ms, Ereignis, Beschriftung, Chance]
      // (Chance erst seit dem Bon-Builder; ältere Links haben sie nicht)
      return { sport: bon.s, hash: m[1], picks: bon.p.filter((p) => Array.isArray(p) && p.length >= 7 && typeof p[4] === 'number') };
    } catch (err) {
      return null;
    }
  }

  /* ---------- Teamnamen abgleichen ---------- */
  const GENERIC = new Set(['fc', 'sc', 'sv', 'cf', 'ac', 'afc', 'as', 'ss', 'us', 'cd', 'ud', 'sd', 'rc', 'rcd', 'fk', 'sk', 'if', 'bk', 'ik',
    'hc', 'ehc', 'hk', 'ec', 'tsg', 'vfb', 'vfl', 'fsv', 'spvgg', 'bsc', 'ssv', 'tsv', 'sg', 'ev', 'club', 'de', 'la', 'el', 'the', 'and']);
  const RESERVE = /\b(ii|b|u\d\d|frauen|women|w|reserves?|amateure)\b/;
  // Gleiche Schreibweise für beide Seiten: ohne Akzente, ä/ae zu a usw. (Malmö = Malmoe)
  function norm(name) {
    return String(name || '').toLowerCase().replace(/ß/g, 'ss').normalize('NFKD').replace(/[̀-ͯ]/g, '')
      .replace(/ae/g, 'a').replace(/oe/g, 'o').replace(/ue/g, 'u').replace(/['’`´.]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
  }
  // Kürzel, die bwin ausschreibt (AaB = Aalborg BK, Man Utd = Manchester United)
  const SHORT = { aab: 'aalborg', agf: 'aarhus', fck: 'kobenhavn', ob: 'odense', utd: 'united', man: 'manchester', psg: 'paris', qpr: 'queens', wba: 'west', mk: 'milton' };
  const tokens = (name) => norm(name).split(' ').map((t) => SHORT[t] || t).filter((t) => t && !GENERIC.has(t) && !/^\d+$/.test(t));
  const hit = (a, b) => a === b || (Math.min(a.length, b.length) >= 4 && (a.startsWith(b) || b.startsWith(a)));
  // Anteil unserer Namensteile, die bei bwin vorkommen; 0, wenn nur eine Seite eine Reserve- oder Frauenmannschaft ist
  function sideScore(ours, theirs) {
    if (RESERVE.test(norm(ours.join(' '))) !== RESERVE.test(norm(theirs))) return 0;
    const have = tokens(theirs);
    let best = 0;
    for (const n of ours) {
      const want = tokens(n);
      if (want.length) best = Math.max(best, want.filter((w) => have.some((h) => hit(w, h))).length / want.length);
    }
    return best;
  }
  // Heim und Gast bei bwin: Fußball liefert sie als Teilnehmer, Eishockey nur im Namen
  // ("A - B", in der NHL "Gast bei Heim")
  function sides(f) {
    const ps = f.participants || [];
    const byType = (t) => ps.find((p) => p.properties && p.properties.type === t);
    const h = byType('HomeTeam'), a = byType('AwayTeam');
    if (h && a) return [h.name.value, a.name.value];
    const name = (f.name && f.name.value) || '';
    const bei = name.split(' bei ');
    if (bei.length === 2) return [bei[1], bei[0]];
    const dash = name.split(' - ');
    return dash.length === 2 ? dash : null;
  }
  function findFixture(pick, fixtures) {
    const [hn, hs, an, as, t] = pick;
    const better = (x, y) => !y || x.score > y.score || (x.score === y.score && x.dt < y.dt);
    let best = null, second = null;
    for (const f of fixtures) {
      const s = sides(f);
      if (!s) continue;
      const sh = sideScore([hn, hs], s[0]), sa = sideScore([an, as], s[1]);
      if (!sh || !sa) continue;
      const cand = { f, score: sh + sa, dt: Math.abs(Date.parse(f.startDate) - t), names: s };
      if (better(cand, best)) { second = best; best = cand; } else if (better(cand, second)) second = cand;
    }
    // Gleich gut passende zweite Partie: lieber nichts nehmen als die falsche
    if (best && second && second.score === best.score && second.dt === best.dt) return null;
    return best;
  }

  /* ---------- Schnittstelle ---------- */
  async function getJSON(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`bwin antwortet mit ${res.status}`);
    return res.json();
  }
  async function fixturesAround(sportId, picks) {
    const times = picks.map((p) => p[4]);
    const from = new Date(Math.min(...times) - WINDOW).toISOString(), to = new Date(Math.max(...times) + WINDOW).toISOString();
    const all = [];
    for (let skip = 0; skip < 10 * PAGE; skip += PAGE) {
      const j = await getJSON(`${API}fixtures?${Q}&fixtureTypes=Standard&state=Latest&offerMapping=None&sportIds=${sportId}&from=${from}&to=${to}&skip=${skip}&take=${PAGE}&sortBy=StartDate`);
      const list = j.fixtures || [];
      all.push(...list);
      if (list.length < PAGE) break;
    }
    return all;
  }
  async function fixtureView(id) {
    const j = await getJSON(`${API}fixture-view?${Q}&offerMapping=All&scoreboardMode=None&fixtureIds=${encodeURIComponent(id)}&state=Latest&includePrecreatedBetBuilder=false&supportVirtual=false&useRegionalisedConfiguration=true&includeRelatedFixtures=false`);
    return j.fixture;
  }

  /* ---------- Ereignis → Markt ----------
     Neuere Spiele (Fußball) tragen Markttyp, Spielabschnitt, Linie und Seite als Daten.
     Ältere (Eishockey, manche Fußballligen) nur Namen, dort entscheiden Muster.
     Eishockey-Ereignisse zählen bei schnockstats inklusive Verlängerung; Märkte nur
     für die reguläre Spielzeit wären eine andere Wette und werden nicht genommen. */
  const visible = (x) => !x.status || x.status === 'Visible' || x.visibility === 'Visible';
  const params = (m) => Object.fromEntries((m.parameters || []).map((x) => [x.key, x.value]));
  const sideOf = (m) => (((m.grouping && m.grouping.detailed) || [])[0] || {}).fixtureParticipantType || '';
  const optTypes = (o) => (o.parameters && o.parameters.optionTypes) || [];
  const LINES = { o05: 0.5, o15: 1.5, o25: 2.5, o35: 3.5, ht05: 0.5, ht15: 1.5, h15: 1.5, a15: 1.5,
    o45: 4.5, o55: 5.5, o65: 6.5, h25: 2.5, h35: 3.5, a25: 2.5, a35: 3.5 };
  const isOver = (o) => optTypes(o).includes('Over') || /^(mehr|uber|over)/.test(norm(o.name.value));
  // bwin-Quote einer Auswahl: neues Format unter price, altes direkt als odds
  const quote = (o) => (o.price && o.price.odds) || o.odds || null;
  const sameTeam = (a, b) => norm(a) === norm(b) || sideScore([a], b) >= 1;

  function pickV2(f, key, home, away) {
    const ms = (f.optionMarkets || []).filter(visible);
    const one = (test, opt) => {
      for (const m of ms) {
        if (!test(params(m), m)) continue;
        const o = m.options.filter(visible).find((x) => opt(x, m));
        if (o) return [m.id, o.id, m.name.value + ': ' + o.name.value, quote(o)];
      }
      return null;
    };
    const ou = (period, line, side) => one((p, m) => p.MarketType === 'Over/Under' && p.Period === period
      && Math.abs(parseFloat(p.DecimalValue) - line) < 1e-6 && sideOf(m) === side, isOver);
    const scores = (side) => one((p, m) => p.MarketType === 'ToScore' && p.Period === 'RegularTime' && sideOf(m) === side,
      (o) => optTypes(o).includes('Over') || norm(o.name.value) === 'ja') || ou('RegularTime', 0.5, side);
    const result = (team) => one((p) => p.MarketType === '3way' && p.Period === 'RegularTime', (o) => sameTeam(o.name.value, team));
    const dbl = (team) => one((p) => p.MarketType === 'DoubleChance' && p.Period === 'RegularTime',
      (o) => optTypes(o).includes('Draw') && sameTeam(o.name.value.replace(/\s+oder\s+X$|^X\s+oder\s+/, ''), team));
    switch (key) {
      case 'o05': case 'o15': case 'o25': case 'o35': return ou('RegularTime', LINES[key], '');
      case 'ht05': case 'ht15': return ou('FirstHalf', LINES[key], '');
      case 'h15': return ou('RegularTime', 1.5, 'Home');
      case 'a15': return ou('RegularTime', 1.5, 'Away');
      // "Heimteam kassiert" heißt: das Gastteam trifft, und umgekehrt
      case 'hs': case 'ac': return scores('Home');
      case 'as': case 'hc': return scores('Away');
      case 'btts': return one((p) => p.MarketType === 'BTTS' && p.Period === 'RegularTime', (o) => optTypes(o)[0] === 'ToHappen' || norm(o.name.value) === 'ja');
      case '1': return result(home);
      case '2': return result(away);
      case '1X': return dbl(home);
      case 'X2': return dbl(away);
      default: return null;
    }
  }

  function pickV1(f, key, home, away, sport) {
    // Manche Spiele (NHL) haben Märkte im neuen Format, aber ohne die Angaben dazu: mit durchsuchen
    const asGames = (f.optionMarkets || []).map((m) => ({ id: m.id, name: m.name, status: m.status, results: m.options }));
    const gs = (f.games || []).concat(asGames).filter(visible);
    const one = (nameTest, resTest) => {
      for (const g of gs) {
        if (!nameTest(norm(g.name.value), g)) continue;
        const r = g.results.filter(visible).find((x) => resTest(norm(x.name.value), x));
        if (r) return [g.id, r.id, g.name.value + ': ' + r.name.value, quote(r)];
      }
      return null;
    };
    const withOt = (gn) => /verlangerung|overtime|inkl|einschliesslich/.test(gn) && !/regular/.test(gn);
    const over = (n) => (rn) => /^(mehr|uber|over)/.test(rn) && rn.endsWith(String(n).replace('.', ' '));   // "4,5" → "4 5"
    const teamRes = (team) => (rn, r) => sideScore([team], r.name.value) > 0 && sideScore([team === home ? away : home], r.name.value) === 0;
    if (sport === 'h') {
      switch (key) {
        case 'win1': case 'win2': return one((gn) => /(2 weg|zwei weg)/.test(gn) && withOt(gn), teamRes(key === 'win1' ? home : away));
        case 'reg1': case 'reg2': return one((gn) => /3 weg/.test(gn) && /regular/.test(gn), teamRes(key === 'reg1' ? home : away));
        case 'o45': case 'o55': case 'o65': return one((gn) => /^gesamt/.test(gn) && withOt(gn), over(LINES[key]));
        // "Beide treffen" ist mit und ohne Verlängerung dasselbe: ein 0:0 bleibt einseitig
        case 'btts': return one((gn) => /beide (teams|mannschaften) treffen/.test(gn) && !/drittel|periode/.test(gn), (rn) => rn === 'ja');
        case 'h25': case 'h35': case 'a25': case 'a35': {
          const team = key[0] === 'h' ? home : away;
          return one((gn, g) => /gesamt/.test(gn) && withOt(gn) && sideScore([team], g.name.value) > 0, over(LINES[key]));
        }
        case 'hpl': case 'apl': {
          const team = key === 'hpl' ? home : away;
          return one((gn) => /handicap/.test(gn) && withOt(gn), (rn, r) => teamRes(team)(rn, r) && /-1,5\s*$/.test(r.name.value));
        }
        case 'ot': return one((gn) => /verlangerung/.test(gn) && !/weg/.test(gn), (rn) => rn === 'ja');
        default: return null;
      }
    }
    switch (key) {
      case '1': case '2': return one((gn) => /(3 weg|spielresultat|ergebnis)/.test(gn) && !/halfte|halbzeit|handicap/.test(gn), teamRes(key === '1' ? home : away));
      case 'o05': case 'o15': case 'o25': case 'o35': return one((gn) => /(gesamt|anzahl tore|uber unter)/.test(gn) && !/halfte|halbzeit|team/.test(gn), over(LINES[key]));
      case 'btts': return one((gn) => /beide (teams|mannschaften) treffen/.test(gn) && !/halfte|halbzeit/.test(gn), (rn) => rn === 'ja');
      default: return null;
    }
  }

  /* ---------- Ablauf ---------- */
  async function lookup(bon) {
    const fixtures = await fixturesAround(SPORT[bon.sport], bon.picks);
    const views = new Map();
    const found = bon.picks.map((pick) => findFixture(pick, fixtures));
    // Jedes Spiel einmal laden, mehrere gleichzeitig: Große Bons hätten sonst eine Minute gebraucht
    const ids = [...new Set(found.filter(Boolean).map((x) => x.f.id))];
    let next = 0;
    const worker = async () => {
      while (next < ids.length) {
        const id = ids[next++];
        views.set(id, await fixtureView(id).catch(() => null));
        progress(`${views.size} von ${ids.length} Spielen geladen …`);
      }
    };
    await Promise.all(Array.from({ length: PARALLEL }, worker));
    const rows = [];
    bon.picks.forEach((pick, i) => {
      const label = `${pick[1] || pick[0]} – ${pick[3] || pick[2]} · ${pick[6]}`;
      if (!found[i]) { rows.push({ label, miss: 'Spiel bei bwin nicht gefunden' }); return; }
      const f = views.get(found[i].f.id);
      if (!f) { rows.push({ label, miss: 'Spiel konnte nicht geladen werden' }); return; }
      const [home, away] = found[i].names;
      const sel = (f.optionMarkets && f.optionMarkets.length ? pickV2(f, pick[5], home, away) : null) || pickV1(f, pick[5], home, away, bon.sport);
      if (!sel) { rows.push({ label, miss: `bwin bietet „${pick[6]}“ hier nicht an` }); return; }
      rows.push({ label, opt: `${f.id}-${sel[0]}-${sel[1]}`, what: `${home} – ${away} · ${sel[2]}`, game: f.id,
        odds: sel[3], p: typeof pick[7] === 'number' ? pick[7] : null });
    });
    return rows;
  }

  /* ---------- Bon-Builder ----------
     Zeigt jeden Tipp mit bwin-Quote, fairer Quote (1 ÷ Chance) und Rückfluss je Euro
     (Chance × bwin-Quote: was von 1 € im Schnitt zurückkommt) und schlägt Kombis vor,
     die eine Ziel-Quote erreichen. Unlohnende Tipps bleiben sichtbar, kommen aber in
     keinen Vorschlag. Gelegte Kombis merkt sich die Sitzung, damit nichts doppelt landet. */
  // Unter MIN_ODDS bringt ein Tipp kaum Gewinn, nur Risiko; unter MIN_RETURN zahlt bwin
  // deutlich weniger, als der Tipp nach unserer Chance wert ist
  const MIN_ODDS = 1.03, MIN_RETURN = 0.9;
  const TARGETS = [1.5, 2, 3, 5, 10];
  const fmt = (x, d) => x.toFixed(d).replace('.', ',');
  const ret = (r) => (r.p != null && r.odds ? r.p * r.odds : null);
  const prodOdds = (legs) => legs.reduce((a, r) => a * r.odds, 1);
  const prodP = (legs) => legs.reduce((a, r) => a * r.p, 1);
  function weakReason(r) {
    if (!r.odds) return 'keine bwin-Quote';
    if (r.odds < MIN_ODDS) return `lohnt nicht: Quote ${fmt(r.odds, 2)} bringt kaum etwas`;
    const x = ret(r);
    if (x != null && x < MIN_RETURN) return `lohnt nicht: bwin zahlt zu wenig (${fmt(x, 2)} € je €)`;
    return null;
  }
  function sessionGet(key, fallback) {
    try { const v = JSON.parse(sessionStorage.getItem(key)); return v == null ? fallback : v; } catch (err) { return fallback; }
  }
  function sessionSet(key, val) {
    try { sessionStorage.setItem(key, JSON.stringify(val)); } catch (err) { /* ohne Speicher gilt die Auswahl nur bis zum Neuladen */ }
  }

  // Vorschläge: die Tipps mit dem besten Rückfluss zuerst, jede Kombi so lange auffüllen, bis
  // sie die Ziel-Quote erreicht. Jede Kombi endet knapp über dem Ziel, so reichen die Tipps
  // für möglichst viele Kombis; die schwächsten landen hinten oder im Rest.
  function suggest(legs, target) {
    const pool = legs.slice().sort((a, b) => ret(b) - ret(a) || b.odds - a.odds);
    const combos = [];
    let cur = [];
    for (const r of pool) {
      cur.push(r);
      if (prodOdds(cur) >= target - 1e-9 || cur.length >= SLIP_MAX) { combos.push(cur); cur = []; }
    }
    return { combos, rest: cur };
  }

  function show(rows, bon) {
    const tag = 'schnockstats-bon:' + bon.hash.slice(-24) + ':';
    const st = { target: sessionGet(tag + 'target', 2), hideWeak: sessionGet(tag + 'hide', false),
      off: new Set(sessionGet(tag + 'off', [])), used: new Set(sessionGet(tag + 'used', [])) };
    const save = () => {
      sessionSet(tag + 'target', st.target); sessionSet(tag + 'hide', st.hideWeak);
      sessionSet(tag + 'off', [...st.off]); sessionSet(tag + 'used', [...st.used]);
    };
    const found = rows.filter((r) => r.opt), missing = rows.filter((r) => !r.opt);
    const noChance = found.length > 0 && found.every((r) => r.p == null);
    const byReturn = (a, b) => (ret(b) || 0) - (ret(a) || 0);
    const sep = 'border-top:1px dashed #b9b5a6;padding-top:8px;margin-top:10px';
    const small = 'font-size:12px;color:#5a5850';
    const legLine = (r) => `bwin ${r.odds ? fmt(r.odds, 2) : '–'}`
      + (r.p != null ? ` · fair ${fmt(1 / r.p, 2)} · Chance ${Math.round(r.p * 100)} % · ${fmt(ret(r) || 0, 2)} € je €` : '');
    const place = (legs) => {
      legs.forEach((r) => st.used.add(r.opt));
      save();
      location.href = '/de/sports?options=' + legs.map((r) => r.opt).join(',');
    };

    function controls() {
      const bar = node('div', 'display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin-top:8px');
      const lab = node('label', 'font-weight:700', 'Ziel-Quote je Kombi ');
      const sel = node('select', 'font:inherit;margin-left:4px');
      for (const t of TARGETS) {
        const o = node('option', '', fmt(t, t % 1 ? 1 : 0));
        o.value = t; o.selected = t === st.target;
        sel.append(o);
      }
      sel.addEventListener('change', () => { st.target = +sel.value; save(); render(); });
      lab.append(sel);
      const hide = node('label', 'cursor:pointer');
      const cb = node('input');
      cb.type = 'checkbox'; cb.checked = st.hideWeak;
      cb.addEventListener('change', () => { st.hideWeak = cb.checked; save(); render(); });
      hide.append(cb, document.createTextNode(' Unlohnende ausblenden'));
      bar.append(lab, hide);
      return bar;
    }

    function suggestions() {
      // Je Spiel ein Tipp: Zwei Tipps aus einem Spiel lässt bwin nicht kombinieren
      const legs = [], games = new Set();
      for (const r of found.slice().sort(byReturn)) {
        if (r.p == null || weakReason(r) || st.off.has(r.opt) || st.used.has(r.opt) || games.has(r.game)) continue;
        games.add(r.game);
        legs.push(r);
      }
      const { combos, rest } = suggest(legs, st.target);
      const sec = node('div', sep);
      sec.append(node('b', 'display:block', combos.length ? `Vorschläge: ${combos.length} ${combos.length === 1 ? 'Kombi' : 'Kombis'} ab Quote ${fmt(st.target, 1)}` : 'Vorschläge'));
      if (!combos.length) {
        sec.append(node('div', small, legs.length ? `Die ${legs.length} lohnenden Tipps erreichen zusammen nicht Quote ${fmt(st.target, 1)}. Ziel-Quote senken.` : 'Kein lohnender Tipp übrig.'));
      }
      combos.forEach((c, i) => {
        const o = prodOdds(c), p = prodP(c), item = node('div', 'margin-top:8px');
        item.append(node('div', 'font-weight:700', `Kombi ${i + 1} · ${c.length} ${c.length === 1 ? 'Tipp' : 'Tipps'} · Quote ${fmt(o, 2)} · Chance ${Math.round(p * 100)} % · ${fmt(o * p, 2)} € je €`
          + (o < st.target ? ` · Ziel nicht erreicht (höchstens ${SLIP_MAX} Tipps)` : '')));
        item.append(node('div', small, c.map((r) => r.label.replace(/ · .*$/, '')).join(' · ')));
        const b = button(`Kombi ${i + 1} in den Wettschein`, true, () => place(c));
        b.style.marginTop = '4px';
        item.append(b);
        sec.append(item);
      });
      if (rest.length) sec.append(node('div', small + ';margin-top:8px', `${rest.length} ${rest.length === 1 ? 'Tipp bleibt' : 'Tipps bleiben'} übrig, zusammen nur Quote ${fmt(prodOdds(rest), 2)}.`));
      return sec;
    }

    function tipList() {
      const sec = node('div', sep);
      const weakCount = found.filter((r) => weakReason(r)).length;
      sec.append(node('b', 'display:block', `Alle Tipps (${found.length})${weakCount ? ` · ${weakCount} lohnen nicht${st.hideWeak ? ', ausgeblendet' : ''}` : ''}`));
      sec.append(node('div', small, 'Haken weg = Tipp nicht für Vorschläge verwenden. „€ je €“: was von 1 € Einsatz im Schnitt zurückkommt, wenn unsere Chance stimmt. Unter 1 verdient bwin.'));
      for (const r of found.slice().sort(byReturn)) {
        const why = weakReason(r), used = st.used.has(r.opt);
        if (why && st.hideWeak) continue;
        const row = node('label', 'display:flex;gap:8px;align-items:flex-start;padding:4px 0;cursor:pointer' + (why ? ';opacity:.6' : ''));
        const c = node('input', 'margin-top:3px');
        c.type = 'checkbox'; c.checked = !why && !st.off.has(r.opt); c.disabled = !!why || used;
        c.addEventListener('change', () => { if (c.checked) st.off.delete(r.opt); else st.off.add(r.opt); save(); render(); });
        const txt = node('div', '');
        txt.append(node('div', 'font-weight:700', r.label), node('div', small, legLine(r)));
        if (why) txt.append(node('div', 'font-size:12px;color:#a33', why));
        if (used) txt.append(node('div', 'font-size:12px;color:#126e4c', 'schon in den Wettschein gelegt'));
        row.append(c, txt);
        sec.append(row);
      }
      return sec;
    }

    function render() {
      const old = document.getElementById(BOX_ID), top = old ? old.scrollTop : 0;
      const el = box();
      el.style.width = 'min(560px,calc(100vw - 24px))';
      el.append(node('b', 'display:block;font-size:16px', `Bon-Builder · ${found.length} von ${rows.length} ${rows.length === 1 ? 'Tipp' : 'Tipps'} bei bwin gefunden`), controls());
      if (noChance) el.append(node('div', 'margin-top:8px;color:#a33', 'Dieser Bon kommt aus einer älteren Fassung ohne Chancen. Öffne ihn auf schnockstats noch einmal über „Bon zu bwin“, dann rechnet der Builder.'));
      el.append(suggestions(), tipList());
      if (missing.length) {
        const miss = node('div', sep);
        miss.append(node('b', 'display:block', `Nicht gefunden (${missing.length})`));
        for (const r of missing) miss.append(node('div', small, `${r.label}: ${r.miss}`));
        el.append(miss);
      }
      el.append(node('div', small + ';margin-top:10px', 'Pro Klick kommt eine Kombi in den Wettschein. Danach platzieren oder den Schein leeren und das Lesezeichen erneut antippen: Gelegte Kombis sind markiert. Liegen schon Tipps im Schein, kommen die neuen dazu. Einsatz und „Wette platzieren“ machst du selbst.'));
      if (st.used.size) el.append(button('Markierung „gelegt“ zurücksetzen', false, () => { st.used.clear(); save(); render(); }));
      el.append(button('Schließen', false, () => el.remove()));
      el.scrollTop = top;
    }
    render();
  }

  async function run() {
    if (!/(^|\.)bwin\.de$/.test(location.hostname)) {
      message('Das ist der bwin-Knopf', 'Er funktioniert nur auf www.bwin.de. Leg ihn als Lesezeichen an und tipp ihn an, nachdem du auf einem schnockstats-Bon „Bon zu bwin“ geöffnet hast.');
      return;
    }
    const bon = readBon();
    if (!bon || !bon.picks.length) {
      message('Kein Bon gefunden', 'Öffne bwin über den Knopf „Bon zu bwin“ auf einem schnockstats-Bon und tipp dann dieses Lesezeichen an.');
      return;
    }
    const el = box();
    statusLine = node('div', 'color:#5a5850', `Suche ${bon.picks.length} ${bon.picks.length === 1 ? 'Tipp' : 'Tipps'} bei bwin …`);
    el.append(node('b', 'display:block;font-size:16px', 'schnockstats → bwin'), statusLine);
    let rows;
    try {
      rows = await lookup(bon);
    } catch (err) {
      message('bwin hat nicht geantwortet', `${err.message}. Später noch einmal versuchen; vielleicht hat bwin die Schnittstelle geändert.`);
      return;
    }
    show(rows, bon);
  }

  run().catch((err) => message('Da ging etwas schief', String((err && err.message) || err)));
})();
