// Bumped v10 -> v11: app.js/style.css changed again -- positions now show
// each bot's actual stop-loss protection: Main/Sofi's peak price + trailing
// stop (highlighted green when it's ratcheted above the fixed entry stop),
// Nova's fixed stop/target (it has no trailing mechanism at all). Reminder:
// bumping this version is what actually makes a shell-file change visible
// to an already-open browser -- the SW only detects an update by byte-
// diffing sw.js itself. Bump it EVERY time, not just when convenient.
const SHELL_CACHE = 'shell-v11';
const SHELL_FILES = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_FILES)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
