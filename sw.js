/* Radar-Apps: Offline-Start.
   Seiten und Angebotsdaten: erst Netz, bei Ausfall der letzte gespeicherte Stand.
   Symbole, Schriften, Kartenbibliothek: aus dem Zwischenspeicher, sobald einmal geladen.
   Antworten aus dem Zwischenspeicher tragen die Kennung x-radar-cache, damit die
   Seite "offline" anzeigen kann. */
const VERSION = 'e5a07ceeb2';
const STATIC_CACHE = 'radar-static-' + VERSION;
const DATA_CACHE = 'radar-data';
const CORE = ['monster.html', 'pepsi.html', 'radar.html'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(DATA_CACHE).then((c) => c.addAll(CORE).catch(() => {})));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k.startsWith('radar-static-') && k !== STATIC_CACHE).map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

function withoutQuery(request) {
  const url = new URL(request.url);
  url.search = '';
  return url.toString();
}

async function networkFirst(request, mark) {
  const cache = await caches.open(DATA_CACHE);
  const key = withoutQuery(request);
  try {
    const response = await fetch(request);
    if (response && response.ok) cache.put(key, response.clone());
    return response;
  } catch (err) {
    const hit = await cache.match(key);
    if (!hit) throw err;
    if (!mark) return hit;
    const headers = new Headers(hit.headers);
    headers.set('x-radar-cache', '1');
    return new Response(await hit.blob(), { status: 200, statusText: 'OK', headers });
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(STATIC_CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response && (response.ok || response.type === 'opaque')) cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  const sameOrigin = url.origin === self.location.origin;
  const isData = sameOrigin && url.pathname.includes('/data/angebote/') && url.pathname.endsWith('.json');
  const isRadarPage = sameOrigin && /\/(monster|pepsi|radar)\.html$/.test(url.pathname);
  const isStatic = (sameOrigin && /\.(png|webmanifest)$/.test(url.pathname))
    || /(^|\.)fonts\.(googleapis|gstatic)\.com$/.test(url.hostname)
    || url.hostname === 'cdnjs.cloudflare.com';
  if (isData) event.respondWith(networkFirst(request, true));
  else if (isRadarPage) event.respondWith(networkFirst(request, false));
  else if (isStatic) event.respondWith(cacheFirst(request));
  // Alles andere (Schnockstats, Kartenkacheln) läuft unverändert übers Netz
});
