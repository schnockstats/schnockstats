/* TorShow – Torjubel für die Tore-Kombi.
 * Eine Stadionszene aus Sicht des Schützen (Fußball) bzw. von der blauen Linie
 * (Eishockey), gezeichnet auf ein Canvas, ohne Bibliotheken. Für jeden Tipp der
 * Kombi fällt ein Tor: Ball oder Puck ins federnde Netz, der Torhüter in der
 * falschen Ecke, die Anzeigetafel zählt Tore und Gesamtquote hoch. Nach dem
 * letzten Treffer Pyro, Konfetti und Stroboskop. Ton nur auf Wunsch.
 */
(function (root) {
  'use strict';
  const TAU = Math.PI * 2;
  const rnd = (a, b) => a + Math.random() * (b - a);
  const lerp = (a, b, t) => a + (b - a) * t;
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const quad = (a, b, c, t) => (1 - t) * (1 - t) * a + 2 * (1 - t) * t * b + t * t * c;
  const reduceMotion = () => !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const fmt = (x) => x.toFixed(2).replace('.', ',');
  const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* ---------- Ton: Jubel, Schuss, Tor-Hupe (Web Audio, erst nach Klick) ---------- */
  let actx = null;
  function audio() {
    if (!actx) { const A = root.AudioContext || root.webkitAudioContext; if (A) actx = new A(); }
    if (actx && actx.state === 'suspended') actx.resume();
    return actx;
  }
  function noise(a, len) {
    const buf = a.createBuffer(1, Math.floor(a.sampleRate * len), a.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    const src = a.createBufferSource(); src.buffer = buf; return src;
  }
  function sfxRoar(strength) {
    const a = audio(); if (!a) return;
    const t = a.currentTime, src = noise(a, 3.2);
    const bp = a.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = 700; bp.Q.value = 0.5;
    const g = a.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.28 * strength, t + 0.15);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 3.1);
    src.connect(bp).connect(g).connect(a.destination); src.start(t);
  }
  function sfxKick(hockey) {
    const a = audio(); if (!a) return;
    const t = a.currentTime, o = a.createOscillator(), g = a.createGain();
    o.type = hockey ? 'square' : 'sine';
    o.frequency.setValueAtTime(hockey ? 420 : 160, t);
    o.frequency.exponentialRampToValueAtTime(hockey ? 90 : 45, t + 0.12);
    g.gain.setValueAtTime(hockey ? 0.12 : 0.4, t); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
    o.connect(g).connect(a.destination); o.start(t); o.stop(t + 0.2);
  }
  function sfxHorn() {
    const a = audio(); if (!a) return;
    const t = a.currentTime, g = a.createGain(), lp = a.createBiquadFilter();
    lp.type = 'lowpass'; lp.frequency.value = 1400;
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.16, t + 0.06);
    g.gain.setValueAtTime(0.16, t + 1.5); g.gain.exponentialRampToValueAtTime(0.0001, t + 1.9);
    for (const f of [155, 196, 233]) { const o = a.createOscillator(); o.type = 'sawtooth'; o.frequency.value = f; o.connect(lp); o.start(t); o.stop(t + 2); }
    lp.connect(g).connect(a.destination);
  }

  /* ---------- Szene ---------- */
  function mount(host, opts) {
    const hockey = opts.sport === 'eishockey';
    const legs = opts.legs || [];
    host.innerHTML = `
      <div class="ts-board" aria-hidden="true">
        <div class="ts-cell"><small>Tore</small><b class="ts-count">0/${legs.length}</b></div>
        <div class="ts-ticker"><span class="ts-ticker-text">${hockey ? 'Bully' : 'Anstoß'} · Tore-Kombi · ${legs.length} Tipps</span></div>
        <div class="ts-cell ts-right"><small>Quote</small><b class="ts-odds">1,00</b></div>
      </div>
      <div class="ts-stage"><canvas class="ts-canvas" aria-hidden="true"></canvas>
        <div class="ts-caption" aria-live="polite"></div>
        <div class="ts-controls">
          <button type="button" class="ts-btn ts-sound" aria-pressed="${opts.sound ? 'true' : 'false'}" title="Ton an oder aus"><i>${opts.sound ? '🔊' : '🔈'}</i><span>Ton</span></button>
          <button type="button" class="ts-btn ts-replay" title="Noch einmal abspielen"><i>↻</i><span>Nochmal</span></button>
        </div>
      </div>`;
    const canvas = host.querySelector('canvas'), ctx = canvas.getContext('2d');
    const elCount = host.querySelector('.ts-count'), elOdds = host.querySelector('.ts-odds');
    const elTicker = host.querySelector('.ts-ticker-text'), elCap = host.querySelector('.ts-caption');
    const stage = host.querySelector('.ts-stage');
    let sound = !!opts.sound;
    let W = 0, H = 0, L = null, dpr = 1;
    let crowd = [], net = [], particles = [], flashes = [];
    let shake = 0, flash = 0, celebrate = 0, strobe = 0, goalLight = 0, time = 0;
    let shots = [], cur = -1, shotT = 0, phase = 'idle', scored = 0, product = 1, shownOdds = 1;
    let raf = 0, last = 0, alive = true, ro = null;
    const keeper = { x: 0, dive: 0, dir: 1 };
    const ball = { x: 0, y: 0, r: 0, spin: 0, vis: true, behind: false };

    /* Layout abhängig von der Größe */
    function layout() {
      const rect = stage.getBoundingClientRect();
      W = Math.max(280, rect.width || 600); H = Math.round(clamp(W * 0.42, 230, 360));
      dpr = Math.min(2, root.devicePixelRatio || 1);
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const cx = W / 2;
      if (!hockey) {
        const gw = Math.min(W * 0.6, 560), gh = gw * 0.34, gy = H * 0.73;
        const front = { l: cx - gw / 2, r: cx + gw / 2, t: gy - gh, b: gy };
        const back = { l: front.l + gw * 0.07, r: front.r - gw * 0.07, t: front.t + gh * 0.07, b: gy - gh * 0.16 };
        L = { cx, gw, gh, gy, front, back, by: back.b - gh * 0.04, spot: { x: cx, y: H * 0.95 }, R0: clamp(W * 0.028, 11, 22), post: Math.max(3, gw * 0.013) };
      } else {
        const gw = Math.min(W * 0.34, 290), gh = gw * 0.62, gy = H * 0.7;
        const front = { l: cx - gw / 2, r: cx + gw / 2, t: gy - gh, b: gy };
        const back = { l: front.l + gw * 0.13, r: front.r - gw * 0.13, t: front.t + gh * 0.14, b: gy - gh * 0.2 };
        L = { cx, gw, gh, gy, front, back, by: gy - gh * 0.62, spot: { x: cx, y: H * 0.93 }, R0: clamp(W * 0.022, 9, 16), post: Math.max(4, gw * 0.024) };
      }
      // Publikum
      crowd = [];
      const top = H * 0.03, bottom = L.by - (hockey ? 52 : 18);
      const pal = ['#e11d48', '#f59e0b', '#22c55e', '#3b82f6', '#e5e7eb', '#a855f7', '#14b8a6'];
      for (let y = top; y < bottom; y += 7) {
        const shade = (y - top) / Math.max(1, bottom - top);
        for (let x = 2 + (Math.round(y / 7) % 2) * 3.5; x < W; x += 7) {
          if (Math.random() < 0.08) continue;
          crowd.push({ x, y, c: pal[(Math.random() * pal.length) | 0], a: 0.25 + shade * 0.45, ph: Math.random() * TAU });
        }
      }
      // Netz (Rückwand) als Feder-Gitter
      net = [];
      const cols = hockey ? 12 : 22, rows = hockey ? 8 : 9, b = L.back;
      for (let j = 0; j <= rows; j++) {
        const row = [];
        for (let i = 0; i <= cols; i++) row.push({ x: lerp(b.l, b.r, i / cols), y: lerp(b.t, b.b, j / rows), dx: 0, dy: 0, vx: 0, vy: 0 });
        net.push(row);
      }
      if (phase === 'idle' || phase === 'done') placeBallRest();
    }

    function placeBallRest() {
      if (phase === 'done' && shots.length) {
        const s = shots[shots.length - 1];
        ball.x = lerp(L.back.l, L.back.r, s.u); ball.y = L.back.b - L.R0 * 0.45; ball.r = L.R0 * 0.45; ball.behind = true; ball.vis = true;
      } else { ball.x = L.spot.x; ball.y = L.spot.y - L.R0; ball.r = L.R0; ball.behind = false; ball.vis = true; }
    }

    /* Schüsse vorbereiten: Ziele in verschiedenen Ecken */
    const TARGETS = [[0.12, 0.2], [0.88, 0.78], [0.86, 0.18], [0.14, 0.8], [0.5, 0.15], [0.3, 0.55], [0.72, 0.5]];
    function aim(s) {
      s.tx = lerp(L.front.l, L.front.r, s.u); s.ty = lerp(L.front.t, L.front.b, s.v);
      s.bx = lerp(L.back.l, L.back.r, s.u); s.by = lerp(L.back.t, L.back.b, s.v);
      s.sx = L.spot.x + s.off * W;
    }
    function planShots() {
      const order = TARGETS.slice().sort(() => Math.random() - 0.5);
      shots = legs.map((leg, k) => {
        const [u, v] = order[k % order.length];
        const s = { leg, u: clamp(u + rnd(-0.05, 0.05), 0.08, 0.92), v: clamp(v + rnd(-0.06, 0.06), 0.1, 0.9),
          off: hockey ? rnd(-0.12, 0.12) : 0, curve: rnd(-0.35, 0.35) };
        aim(s);
        return s;
      });
    }

    function netImpulse(x, y, power) {
      const R = L.gw * (hockey ? 0.35 : 0.24);
      const vpx = L.cx, vpy = lerp(L.back.t, L.back.b, 0.45);
      for (const row of net) for (const p of row) {
        const d = Math.hypot(p.x - x, p.y - y);
        if (d > R) continue;
        const f = Math.pow(1 - d / R, 1.6) * power;
        const kx = (x - p.x) * 0.55 + (vpx - p.x) * 0.12, ky = (y - p.y) * 0.55 + (vpy - p.y) * 0.12 + 18;
        const n = Math.hypot(kx, ky) || 1;
        p.vx += kx / n * f; p.vy += ky / n * f;
      }
    }

    function stepNet(dt) {
      const k = 70, c = 7.5, coup = 110;
      for (let j = 0; j < net.length; j++) for (let i = 0; i < net[j].length; i++) {
        const p = net[j][i];
        let nx = 0, ny = 0, nn = 0;
        for (const [a, b] of [[j - 1, i], [j + 1, i], [j, i - 1], [j, i + 1]]) {
          if (net[a] && net[a][b]) { nx += net[a][b].dx; ny += net[a][b].dy; nn++; }
        }
        const ax = -k * p.dx - c * p.vx + coup * (nx / nn - p.dx);
        const ay = -k * p.dy - c * p.vy + coup * (ny / nn - p.dy);
        p.vx += ax * dt; p.vy += ay * dt;
      }
      for (const row of net) for (const p of row) { p.dx += p.vx * dt; p.dy += p.vy * dt; }
    }

    function burst(x, y, n, spread, colors, up) {
      for (let i = 0; i < n; i++) {
        const a = rnd(0, TAU), s = rnd(0.3, 1) * spread;
        particles.push({
          x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s - (up || 0) * rnd(0.6, 1.2),
          life: rnd(1.4, 2.8), age: 0, rot: rnd(0, TAU), vr: rnd(-8, 8),
          w: rnd(4, 8), h: rnd(2, 4), c: colors[(Math.random() * colors.length) | 0], spark: false
        });
      }
    }
    function sparks(x, y, n) {
      for (let i = 0; i < n; i++) {
        particles.push({ x, y, vx: rnd(-50, 50), vy: -rnd(300, 520), life: rnd(0.7, 1.3), age: 0, rot: 0, vr: 0, w: 2, h: 2,
          c: ['#fff4c2', '#ffd166', '#ff9f1c'][(Math.random() * 3) | 0], spark: true });
      }
    }

    /* ---------- Zeichnen ---------- */
    function drawBackground() {
      const g = ctx.createLinearGradient(0, 0, 0, H);
      if (hockey) { g.addColorStop(0, '#060b17'); g.addColorStop(0.5, '#0b1630'); g.addColorStop(1, '#0b1630'); }
      else { g.addColorStop(0, '#04070f'); g.addColorStop(0.55, '#0a1222'); g.addColorStop(1, '#0a1222'); }
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      // Publikum, beim Torjubel springend, mit Handyblitzen
      const jump = celebrate > 0 ? Math.min(1, celebrate) : 0;
      for (const d of crowd) {
        const y = d.y - (jump ? Math.abs(Math.sin(time * 11 + d.ph)) * 3.2 * jump : Math.sin(time * 1.3 + d.ph) * 0.3);
        ctx.globalAlpha = d.a * (0.75 + 0.25 * Math.sin(time * 2 + d.ph));
        ctx.fillStyle = d.c; ctx.fillRect(d.x, y, 3.2, 3.2);
      }
      ctx.globalAlpha = 1;
      for (const f of flashes) { ctx.globalAlpha = clamp(1 - f.age / f.life, 0, 1); ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(f.x, f.y, 2.2, 0, TAU); ctx.fill(); }
      ctx.globalAlpha = 1;
      // Flutlicht: Masten oben links und rechts, Lichtkegel
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      for (const side of [-1, 1]) {
        const mx = L.cx + side * W * 0.44, my = H * 0.05;
        const sweep = Math.sin(time * 0.35 + (side > 0 ? 1.4 : 0)) * 0.12;
        const on = strobe > 0 ? (Math.floor(time * 14 + (side > 0 ? 1 : 0)) % 2 ? 1 : 0.25) : 1;
        const tx = L.cx - side * W * (0.12 + sweep), ty = L.gy + H * 0.15;
        const bg = ctx.createLinearGradient(mx, my, tx, ty);
        bg.addColorStop(0, `rgba(255,250,225,${0.22 * on})`); bg.addColorStop(1, 'rgba(255,250,225,0)');
        ctx.fillStyle = bg; ctx.beginPath();
        ctx.moveTo(mx - 6, my); ctx.lineTo(tx - W * 0.2, ty); ctx.lineTo(tx + W * 0.2, ty); ctx.lineTo(mx + 6, my); ctx.fill();
        const rg = ctx.createRadialGradient(mx, my, 0, mx, my, 38);
        rg.addColorStop(0, `rgba(255,255,240,${0.95 * on})`); rg.addColorStop(1, 'rgba(255,255,240,0)');
        ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(mx, my, 38, 0, TAU); ctx.fill();
      }
      ctx.restore();
    }

    function drawBoards() {
      const bh = hockey ? 22 : 15, y0 = L.by - bh;
      if (hockey) { ctx.fillStyle = 'rgba(180,220,255,0.07)'; ctx.fillRect(0, y0 - 26, W, 26); } // Plexiglas
      ctx.fillStyle = hockey ? '#f3f6fb' : '#05080f'; ctx.fillRect(0, y0, W, bh);
      const txt = celebrate > 0 ? '  TOR!  TOR!  TOR!  ' : '  SCHNOCKSTATS  ·  TORE-KOMBI  ·  ';
      ctx.font = `700 ${Math.round(bh * 0.72)}px system-ui, sans-serif`; ctx.textBaseline = 'middle';
      const tw = ctx.measureText(txt).width, off = -((time * 60) % tw);
      ctx.fillStyle = celebrate > 0 ? (Math.floor(time * 8) % 2 ? '#ffd166' : '#22d38f') : (hockey ? '#0a7fb8' : '#22d38f');
      for (let x = off; x < W; x += tw) ctx.fillText(txt, x, y0 + bh / 2 + 1);
      if (hockey) { ctx.fillStyle = '#c8102e'; ctx.fillRect(0, y0 + bh - 3, W, 3); }
    }

    function drawSurface() {
      const y0 = L.by;
      if (!hockey) {
        const n = 12;
        for (let k = 0; k < n; k++) {
          const a = y0 + (H - y0) * Math.pow(k / n, 1.5), b = y0 + (H - y0) * Math.pow((k + 1) / n, 1.5);
          ctx.fillStyle = k % 2 ? '#10622f' : '#0d5528'; ctx.fillRect(0, a, W, b - a + 1);
        }
        const lg = ctx.createLinearGradient(0, y0, 0, H); lg.addColorStop(0, 'rgba(0,0,0,.35)'); lg.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = lg; ctx.fillRect(0, y0, W, H - y0);
        ctx.strokeStyle = 'rgba(255,255,255,.75)'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(0, L.gy); ctx.lineTo(W, L.gy); ctx.stroke();
        // Fünfmeterraum in Perspektive
        const y6 = L.gy + (H - L.gy) * 0.3, w0 = L.gw * 1.25, w1 = L.gw * 1.6;
        ctx.beginPath(); ctx.moveTo(L.cx - w0 / 2, L.gy); ctx.lineTo(L.cx - w1 / 2, y6); ctx.lineTo(L.cx + w1 / 2, y6); ctx.lineTo(L.cx + w0 / 2, L.gy); ctx.stroke();
        ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.beginPath(); ctx.ellipse(L.spot.x, L.spot.y, 7, 3, 0, 0, TAU); ctx.fill();
      } else {
        const g = ctx.createLinearGradient(0, y0, 0, H); g.addColorStop(0, '#cfe2ee'); g.addColorStop(1, '#f2f8fc');
        ctx.fillStyle = g; ctx.fillRect(0, y0, W, H - y0);
        ctx.strokeStyle = 'rgba(120,150,175,.18)'; ctx.lineWidth = 1;
        for (let k = 0; k < 18; k++) { const y = y0 + (k * 37 % Math.max(1, H - y0)); ctx.beginPath(); ctx.moveTo((k * 97) % W, y); ctx.lineTo((k * 97) % W + 60, y + 6); ctx.stroke(); }
        ctx.fillStyle = 'rgba(200,16,46,.85)'; ctx.fillRect(0, L.gy - 1.5, W, 3);
        ctx.fillStyle = 'rgba(60,140,235,.35)'; ctx.beginPath(); ctx.ellipse(L.cx, L.gy, L.gw * 0.62, (H - L.gy) * 0.3, 0, 0, Math.PI); ctx.fill();
        ctx.strokeStyle = 'rgba(200,16,46,.7)'; ctx.lineWidth = 2; ctx.beginPath(); ctx.ellipse(L.cx, L.gy, L.gw * 0.62, (H - L.gy) * 0.3, 0, 0, Math.PI); ctx.stroke();
        ctx.fillStyle = 'rgba(10,80,180,.8)'; ctx.fillRect(0, H - 10, W, 10);
      }
    }

    function drawNet() {
      const f = L.front, b = L.back;
      ctx.lineWidth = 1; ctx.strokeStyle = hockey ? 'rgba(255,255,255,.55)' : 'rgba(255,255,255,.32)';
      // Seitennetze und Dach
      const k = 7;
      ctx.beginPath();
      for (let i = 0; i <= k; i++) {
        const t = i / k;
        ctx.moveTo(f.l, lerp(f.t, f.b, t)); ctx.lineTo(b.l, lerp(b.t, b.b, t));
        ctx.moveTo(f.r, lerp(f.t, f.b, t)); ctx.lineTo(b.r, lerp(b.t, b.b, t));
        ctx.moveTo(lerp(f.l, b.l, t), lerp(f.t, b.t, t)); ctx.lineTo(lerp(f.l, b.l, t), lerp(f.b, b.b, t));
        ctx.moveTo(lerp(f.r, b.r, t), lerp(f.t, b.t, t)); ctx.lineTo(lerp(f.r, b.r, t), lerp(f.b, b.b, t));
      }
      for (let i = 0; i <= 14; i++) { const t = i / 14; ctx.moveTo(lerp(f.l, f.r, t), f.t); ctx.lineTo(lerp(b.l, b.r, t), b.t); }
      ctx.stroke();
      // Rückwand mit Federn
      ctx.beginPath();
      for (const row of net) row.forEach((p, i) => (i ? ctx.lineTo(p.x + p.dx, p.y + p.dy) : ctx.moveTo(p.x + p.dx, p.y + p.dy)));
      for (let i = 0; i < net[0].length; i++) net.forEach((row, j) => (j ? ctx.lineTo(row[i].x + row[i].dx, row[i].y + row[i].dy) : ctx.moveTo(row[i].x + row[i].dx, row[i].y + row[i].dy)));
      ctx.stroke();
      ctx.fillStyle = 'rgba(0,0,0,.18)'; ctx.fillRect(b.l, b.t, b.r - b.l, b.b - b.t);
    }

    function drawFrame() {
      const f = L.front, w = L.post;
      ctx.lineCap = 'round'; ctx.lineWidth = w;
      ctx.strokeStyle = hockey ? '#d7142c' : '#f8fafc';
      ctx.shadowColor = hockey ? 'rgba(255,40,60,.35)' : 'rgba(255,255,255,.45)'; ctx.shadowBlur = 8;
      ctx.beginPath(); ctx.moveTo(f.l, f.b); ctx.lineTo(f.l, f.t); ctx.lineTo(f.r, f.t); ctx.lineTo(f.r, f.b); ctx.stroke();
      ctx.shadowBlur = 0;
      if (hockey) { // Torlicht auf dem Plexiglas
        const lx = L.cx, ly = L.by - 40;
        ctx.fillStyle = goalLight > 0 ? '#ff2a3d' : '#5b0d15'; ctx.beginPath(); ctx.arc(lx, ly, 7, 0, TAU); ctx.fill();
        if (goalLight > 0) {
          ctx.save(); ctx.globalCompositeOperation = 'lighter';
          for (const off of [0, Math.PI]) {
            const a = time * 7 + off;
            const g = ctx.createRadialGradient(lx, ly, 0, lx, ly, W * 0.5);
            g.addColorStop(0, 'rgba(255,40,60,.32)'); g.addColorStop(1, 'rgba(255,40,60,0)');
            ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(lx, ly); ctx.arc(lx, ly, W * 0.5, a - 0.18, a + 0.18); ctx.fill();
          }
          ctx.restore();
        }
      }
    }

    function roundRect(x, y, w, h, r) {
      ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
    }

    function drawKeeper() {
      const baseX = L.cx + keeper.x, feet = L.gy - 1, h = L.gh * (hockey ? 0.6 : 0.7);
      const d = keeper.dive, dir = keeper.dir;
      ctx.save();
      ctx.translate(baseX + dir * d * L.gw * (hockey ? 0.24 : 0.3), feet - d * h * (hockey ? 0.04 : 0.18));
      ctx.rotate(dir * d * (hockey ? 0.18 : 1.25));
      const shirt = hockey ? '#1f2a44' : '#18e08b', trim = hockey ? '#e8eef7' : '#0b3d2a';
      if (!hockey) {
        ctx.fillStyle = '#0f172a'; // Beine
        ctx.fillRect(-h * 0.12, -h * 0.42, h * 0.09, h * 0.42); ctx.fillRect(h * 0.03, -h * 0.42, h * 0.09, h * 0.42);
        ctx.fillStyle = shirt; roundRect(-h * 0.16, -h * 0.82, h * 0.32, h * 0.44, h * 0.06); ctx.fill();
        ctx.strokeStyle = shirt; ctx.lineWidth = h * 0.08; ctx.lineCap = 'round';
        const arm = 0.35 + d * 0.8 + Math.sin(time * 5) * 0.05 * (1 - d);
        ctx.beginPath(); ctx.moveTo(-h * 0.14, -h * 0.76); ctx.lineTo(-h * 0.14 - Math.cos(arm) * h * 0.3, -h * 0.76 - Math.sin(arm) * h * 0.3); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(h * 0.14, -h * 0.76); ctx.lineTo(h * 0.14 + Math.cos(arm) * h * 0.3, -h * 0.76 - Math.sin(arm) * h * 0.3); ctx.stroke();
        ctx.fillStyle = '#f5f5f4';
        ctx.beginPath(); ctx.arc(-h * 0.14 - Math.cos(arm) * h * 0.33, -h * 0.76 - Math.sin(arm) * h * 0.33, h * 0.055, 0, TAU); ctx.fill();
        ctx.beginPath(); ctx.arc(h * 0.14 + Math.cos(arm) * h * 0.33, -h * 0.76 - Math.sin(arm) * h * 0.33, h * 0.055, 0, TAU); ctx.fill();
        ctx.fillStyle = trim; ctx.fillRect(-h * 0.16, -h * 0.5, h * 0.32, h * 0.05);
        ctx.fillStyle = '#e0b48a'; ctx.beginPath(); ctx.arc(0, -h * 0.92, h * 0.09, 0, TAU); ctx.fill();
      } else {
        ctx.fillStyle = '#e8eef7'; // Schoner in Butterfly-Stellung
        roundRect(-h * 0.52, -h * 0.2, h * 0.42, h * 0.2, h * 0.05); ctx.fill();
        roundRect(h * 0.1, -h * 0.2, h * 0.42, h * 0.2, h * 0.05); ctx.fill();
        ctx.fillStyle = shirt; roundRect(-h * 0.26, -h * 0.72, h * 0.52, h * 0.54, h * 0.1); ctx.fill();
        ctx.fillStyle = trim; ctx.fillRect(-h * 0.26, -h * 0.42, h * 0.52, h * 0.06);
        ctx.fillStyle = '#e8eef7'; ctx.beginPath(); ctx.arc(h * 0.34, -h * 0.5, h * 0.12, 0, TAU); ctx.fill(); // Fanghand
        ctx.fillStyle = '#c9d4e3'; ctx.fillRect(-h * 0.5, -h * 0.62, h * 0.16, h * 0.26); // Stockhand
        ctx.fillStyle = '#111827'; ctx.fillRect(-h * 0.46, -h * 0.36, h * 0.07, h * 0.36);
        ctx.fillStyle = '#e8eef7'; ctx.beginPath(); ctx.arc(0, -h * 0.84, h * 0.14, 0, TAU); ctx.fill(); // Maske
        ctx.strokeStyle = '#1f2937'; ctx.lineWidth = 1.2;
        for (let k = -2; k <= 2; k++) { ctx.beginPath(); ctx.moveTo(k * h * 0.035, -h * 0.9); ctx.lineTo(k * h * 0.035, -h * 0.76); ctx.stroke(); }
      }
      ctx.restore();
    }

    function drawBall() {
      if (!ball.vis) return;
      const r = ball.r;
      if (!hockey) {
        const sy = phase === 'flight' ? lerp(L.spot.y, L.gy, clamp(shotT / T_FLY, 0, 1)) : ball.y + r * 0.9;
        ctx.fillStyle = 'rgba(0,0,0,.28)'; ctx.beginPath(); ctx.ellipse(ball.x, Math.max(sy, ball.y + r * 0.8), r * 0.9, r * 0.28, 0, 0, TAU); ctx.fill();
        const g = ctx.createRadialGradient(ball.x - r * 0.35, ball.y - r * 0.35, r * 0.1, ball.x, ball.y, r);
        g.addColorStop(0, '#ffffff'); g.addColorStop(0.75, '#e5e9ef'); g.addColorStop(1, '#9aa5b4');
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(ball.x, ball.y, r, 0, TAU); ctx.fill();
        ctx.fillStyle = '#111827';
        for (let k = 0; k < 5; k++) {
          const a = ball.spin + k * TAU / 5, px = ball.x + Math.cos(a) * r * 0.62, py = ball.y + Math.sin(a) * r * 0.62;
          ctx.beginPath(); ctx.arc(px, py, r * 0.2, 0, TAU); ctx.fill();
        }
        ctx.beginPath(); ctx.arc(ball.x, ball.y, r * 0.24, 0, TAU); ctx.fill();
      } else {
        ctx.fillStyle = 'rgba(0,0,0,.22)'; ctx.beginPath(); ctx.ellipse(ball.x, ball.y + r * 0.55, r * 1.1, r * 0.3, 0, 0, TAU); ctx.fill();
        ctx.fillStyle = '#0b0d12'; ctx.beginPath(); ctx.ellipse(ball.x, ball.y + r * 0.18, r, r * 0.42, 0, 0, TAU); ctx.fill();
        ctx.fillRect(ball.x - r, ball.y - r * 0.05, r * 2, r * 0.23);
        ctx.fillStyle = '#2a2f3a'; ctx.beginPath(); ctx.ellipse(ball.x, ball.y - r * 0.05, r, r * 0.42, 0, 0, TAU); ctx.fill();
      }
    }

    function drawParticles(dt) {
      ctx.save();
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.age += dt; if (p.age > p.life) { particles.splice(i, 1); continue; }
        p.vy += (p.spark ? 520 : 160) * dt; p.vx *= p.spark ? 0.99 : 0.985;
        p.x += (p.vx + (p.spark ? 0 : Math.sin(p.age * 6 + p.rot) * 30)) * dt; p.y += p.vy * dt; p.rot += p.vr * dt;
        ctx.globalAlpha = clamp(1 - p.age / p.life, 0, 1);
        if (p.spark) { ctx.globalCompositeOperation = 'lighter'; ctx.fillStyle = p.c; ctx.fillRect(p.x, p.y, 2.4, 2.4); ctx.globalCompositeOperation = 'source-over'; }
        else { ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.age * 7))); ctx.restore(); }
      }
      ctx.restore();
    }

    /* ---------- Ablauf ---------- */
    const T_AIM = 0.45, T_FLY = hockey ? 0.34 : 0.6, T_NET = 0.28, T_DROP = 0.75, T_NEXT = 0.35;
    function startShot(k) {
      cur = k; shotT = 0; phase = 'aim';
      const s = shots[k];
      ball.x = s.sx; ball.y = L.spot.y - L.R0; ball.r = L.R0; ball.vis = true; ball.behind = false;
      keeper.dir = s.u < 0.5 ? 1 : -1; if (Math.abs(s.u - 0.5) < 0.12) keeper.dir = Math.random() < 0.5 ? 1 : -1;
      elCap.innerHTML = `<b>${k + 1}. ${esc(s.leg.label)}</b><span>${esc(s.leg.match)} · Quote ${esc(s.leg.odds)}</span>`;
      elCap.classList.remove('is-in'); void elCap.offsetWidth; elCap.classList.add('is-in');
    }

    function goal(k) {
      scored = k + 1; product *= shots[k].leg.oddsNum || 1;
      elCount.textContent = `${scored}/${shots.length}`;
      elCount.parentElement.classList.remove('pop'); void elCount.offsetWidth; elCount.parentElement.classList.add('pop');
      flash = 1; shake = 1; celebrate = Math.max(celebrate, 1.6); if (hockey) goalLight = 2.6;
      for (let i = 0; i < 26; i++) { const d = crowd[(Math.random() * crowd.length) | 0]; if (d) flashes.push({ x: d.x, y: d.y, age: 0, life: rnd(0.15, 0.5) }); }
      burst(shots[k].bx, shots[k].by, 18, 180, ['#ffffff', '#d1fae5', '#fde68a'], 60);
      elTicker.textContent = `${hockey ? 'Tor!' : 'Toooor!'} ${shots[k].leg.label} · ${shots[k].leg.match}`;
      if (sound) { if (hockey) sfxHorn(); else sfxRoar(0.7 + k * 0.08); }
      if (opts.onGoal) opts.onGoal(k, false);
      if (k === shots.length - 1) finale();
    }

    function finale() {
      strobe = 3; celebrate = 4.5;
      const cols = ['#22d38f', '#ffd166', '#ff4d6d', '#4cc9f0', '#ffffff', '#a78bfa'];
      burst(W * 0.08, H * 0.1, 90, 260, cols, 120);
      burst(W * 0.92, H * 0.1, 90, 260, cols, 120);
      const pyro = () => { for (const x of [L.front.l - L.gw * 0.14, L.front.r + L.gw * 0.14, W * 0.06, W * 0.94]) sparks(x, L.gy, 55); };
      pyro(); setTimeout(() => alive && pyro(), 350); setTimeout(() => alive && pyro(), 700);
      elTicker.textContent = `${shots.length} von ${shots.length} · Gesamtquote ${fmt(product)} · Kombi komplett!`;
      host.classList.add('is-done');
      if (sound) setTimeout(() => alive && sfxRoar(1.1), 150);
    }

    function update(dt) {
      time += dt;
      shake = Math.max(0, shake - dt * 2.4); flash = Math.max(0, flash - dt * 3);
      celebrate = Math.max(0, celebrate - dt); strobe = Math.max(0, strobe - dt); goalLight = Math.max(0, goalLight - dt);
      for (let i = flashes.length - 1; i >= 0; i--) { flashes[i].age += dt; if (flashes[i].age > flashes[i].life) flashes.splice(i, 1); }
      if (celebrate > 0 && Math.random() < 0.5) { const d = crowd[(Math.random() * crowd.length) | 0]; if (d) flashes.push({ x: d.x, y: d.y, age: 0, life: rnd(0.1, 0.35) }); }
      stepNet(Math.min(dt, 1 / 30));
      // Quote auf der Tafel weich nachziehen
      shownOdds += (product - shownOdds) * Math.min(1, dt * 6);
      elOdds.textContent = fmt(shownOdds);

      if (phase === 'idle' || phase === 'done') {
        keeper.dive = Math.max(0, keeper.dive - dt * 1.5); keeper.x = Math.sin(time * 1.2) * L.gw * 0.03;
        return;
      }
      shotT += dt;
      const s = shots[cur];
      if (phase === 'aim') {
        keeper.dive = Math.max(0, keeper.dive - dt * 3); keeper.x = Math.sin(time * 6) * L.gw * 0.025;
        ball.r = L.R0 * (1 + Math.sin(shotT * 20) * 0.02);
        if (shotT >= T_AIM) { phase = 'flight'; shotT = 0; if (sound) sfxKick(hockey); }
      } else if (phase === 'flight') {
        const t = clamp(shotT / T_FLY, 0, 1), e = hockey ? t : easeOut(t) * 0.35 + t * 0.65;
        const cxp = (s.sx + s.tx) / 2 + s.curve * L.gw * 0.5, cyp = Math.min(L.spot.y, s.ty) - (hockey ? L.gh * 0.2 : L.gh * 0.9);
        ball.x = quad(s.sx, cxp, s.tx, e); ball.y = quad(L.spot.y - L.R0, cyp, s.ty, e);
        ball.r = lerp(L.R0, L.R0 * (hockey ? 0.55 : 0.5), e); ball.spin += dt * 18;
        keeper.dive = Math.min(1, keeper.dive + dt / (T_FLY * 0.9));
        if (t >= 1) { phase = 'net'; shotT = 0; ball.behind = true; }
      } else if (phase === 'net') {
        const t = clamp(shotT / T_NET, 0, 1), e = easeOut(t);
        ball.x = lerp(s.tx, s.bx, e); ball.y = lerp(s.ty, s.by, e); ball.r = lerp(L.R0 * 0.5, L.R0 * 0.45, e); ball.spin += dt * 10;
        keeper.dive = Math.min(1, keeper.dive + dt * 2);
        if (t >= 1) { netImpulse(s.bx, s.by, hockey ? 230 : 300); goal(cur); phase = 'drop'; shotT = 0; }
      } else if (phase === 'drop') {
        const t = clamp(shotT / T_DROP, 0, 1);
        const floor = L.back.b - L.R0 * 0.45;
        const bounce = Math.abs(Math.cos(t * Math.PI * 1.5)) * (1 - t);
        ball.y = lerp(s.by, floor, easeInOut(Math.min(1, t * 1.6))) - bounce * L.gh * 0.08;
        if (t >= 1) { phase = cur < shots.length - 1 ? 'pause' : 'done'; shotT = 0; }
      } else if (phase === 'pause') {
        keeper.dive = Math.max(0, keeper.dive - dt * 2.5);
        if (shotT >= T_NEXT) startShot(cur + 1);
      }
    }

    function draw(dt) {
      ctx.save();
      if (shake > 0) ctx.translate(rnd(-1, 1) * shake * 6, rnd(-1, 1) * shake * 4);
      drawBackground(); drawBoards(); drawSurface(); drawNet();
      if (ball.behind) drawBall();
      drawKeeper();
      drawFrame();
      if (!ball.behind) drawBall();
      drawParticles(dt);
      if (flash > 0) { ctx.fillStyle = `rgba(255,255,255,${flash * 0.35})`; ctx.fillRect(0, 0, W, H); }
      if (strobe > 0 && Math.floor(time * 12) % 2) { ctx.fillStyle = 'rgba(255,250,220,.06)'; ctx.fillRect(0, 0, W, H); }
      if (goalLight > 0) { ctx.fillStyle = `rgba(255,30,50,${0.05 + 0.04 * Math.sin(time * 14)})`; ctx.fillRect(0, 0, W, H); }
      ctx.restore();
    }

    function frame(now) {
      if (!alive) return;
      if (!canvas.isConnected) { destroy(); return; }
      const dt = Math.min(0.05, (now - (last || now)) / 1000); last = now;
      update(dt); draw(dt);
      raf = requestAnimationFrame(frame);
    }

    function showFinal() {
      product = 1; for (const s of shots) product *= s.leg.oddsNum || 1;
      shownOdds = product; scored = shots.length; phase = 'done';
      elCount.textContent = `${scored}/${shots.length}`; elOdds.textContent = fmt(product);
      elTicker.textContent = `${shots.length} Tipps · Gesamtquote ${fmt(product)} · Nochmal ansehen mit ↻`;
      elCap.innerHTML = '';
      host.classList.add('is-done');
      if (opts.onGoal) shots.forEach((_, k) => opts.onGoal(k, true));
      placeBallRest();
    }

    function play() {
      host.classList.remove('is-done');
      product = 1; shownOdds = 1; scored = 0; particles = []; celebrate = 0; strobe = 0;
      elCount.textContent = `0/${shots.length}`; elOdds.textContent = '1,00';
      if (opts.onReset) opts.onReset();
      planShots();
      startShot(0);
    }

    function destroy() { alive = false; cancelAnimationFrame(raf); if (ro) ro.disconnect(); }

    host.querySelector('.ts-replay').addEventListener('click', () => { if (reduceMotion()) return; if (sound) audio(); play(); });
    host.querySelector('.ts-sound').addEventListener('click', (e) => {
      sound = !sound; if (sound) audio();
      const b = e.currentTarget;
      b.setAttribute('aria-pressed', sound ? 'true' : 'false');
      b.querySelector('i').textContent = sound ? '🔊' : '🔈';
      if (opts.onSound) opts.onSound(sound);
    });
    layout();
    planShots();
    if (root.ResizeObserver) {
      ro = new ResizeObserver(() => { const w = W; layout(); if (Math.abs(w - W) > 1) shots.forEach(aim); });
      ro.observe(stage);
    }
    if (reduceMotion() || !opts.autoplay) showFinal(); else play();
    if (reduceMotion()) { update(0); draw(0); return { destroy }; }
    raf = requestAnimationFrame(frame);
    return { destroy, replay: play };
  }

  root.TorShow = { mount };
})(typeof window !== 'undefined' ? window : globalThis);
