/* Shubh Laabh Creations: offline support (service worker).
   - The app itself (index.html, icons, manifest) is saved on the phone and opens with no internet.
     If there is internet, the saved copy is refreshed in the background, so a new version
     appears the next time the app is opened.
   - The AI scanner library, its engine file and the PDF library are saved too (best effort).
   - The scanner's model files are saved by the app itself the first time the scanner loads.
   - /api/* (sending by WhatsApp or email) always goes to the network and is never saved.
   Change VERSION whenever you change the lists below, so phones pick up the new lists. */
const VERSION = 'v2';
const SHELL = 'slc-shell-' + VERSION;
const CDN = 'slc-cdn-' + VERSION;

const SHELL_FILES = ['manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png', 'icon-512-maskable.png'];
const TF = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.5';
const CDN_FILES = [
  TF,
  TF + '/dist/ort-wasm-simd-threaded.jsep.mjs',
  TF + '/dist/ort-wasm-simd-threaded.jsep.wasm',          // ~22 MB, the engine that runs the scanner
  'https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js',
  'https://www.gstatic.com/firebasejs/13.4.0/firebase-app.js',
  'https://www.gstatic.com/firebasejs/13.4.0/firebase-auth.js',
  'https://www.gstatic.com/firebasejs/13.4.0/firebase-firestore.js',
  'https://fonts.googleapis.com/css2?family=Marcellus&family=Figtree:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@500;600&display=swap'
];
const CDN_HOSTS = ['cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'fonts.googleapis.com', 'fonts.gstatic.com', 'www.gstatic.com'];   // www.gstatic.com = the sign-in / database library

/* a redirected response cannot be served to a page navigation, so store a clean copy */
async function clean(res) {
  if (!res.redirected) return res;
  return new Response(await res.blob(), { status: res.status, statusText: res.statusText, headers: res.headers });
}

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const shell = await caches.open(SHELL);
    const page = await fetch('./', { cache: 'reload' });
    if (!page.ok) throw new Error('app page not available');
    await shell.put('index.html', await clean(page));
    await shell.addAll(SHELL_FILES.map(f => new Request(f, { cache: 'reload' })));
    // big libraries: best effort, one failing (no signal, blocked) must not stop the app from installing
    const cdn = await caches.open(CDN);
    await Promise.allSettled(CDN_FILES.map(async u => {
      if (await cdn.match(u)) return;
      const r = await fetch(u);
      if (r.ok) await cdn.put(u, r);
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith('slc-') && k !== SHELL && k !== CDN) await caches.delete(k);
    await self.clients.claim();
  })());
});

/* saved copy first (fast, works offline), refreshed from the network in the background */
async function appFile(req, key) {
  const cache = await caches.open(SHELL);
  const saved = await cache.match(key);
  const refresh = fetch(req).then(async r => { if (r.ok) await cache.put(key, await clean(r.clone())); return r; }).catch(() => null);
  if (saved) { refresh.catch(() => {}); return saved; }
  const r = await refresh;
  return r || new Response('Offline, and this file was not saved yet. Open the app once with internet.', { status: 503 });
}

async function libFile(req) {
  const cache = await caches.open(CDN);
  const saved = await cache.match(req);
  if (saved) return saved;
  const r = await fetch(req);
  if (r.ok || r.type === 'opaque') cache.put(req, r.clone());
  return r;
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin === location.origin) {
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/config')) return;      // sending: always live
    if (req.mode === 'navigate') return e.respondWith(appFile(req, 'index.html'));
    if (url.pathname.endsWith('/sw.js')) return;                                               // let the browser check for a new version itself
    return e.respondWith(appFile(req, req.url.slice(location.origin.length) .replace(/^\//, '') || 'index.html'));
  }
  if (CDN_HOSTS.includes(url.hostname)) e.respondWith(libFile(req));
});
