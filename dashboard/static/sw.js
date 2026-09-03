// Bumped v9 -> v10: style.css changed again -- added a real desktop
// breakpoint (min-width: 860px). Before this, .screen was max-width:480px
// unconditionally, so desktop was the same cramped single column as mobile,
// just centered on a bigger screen. Reminder: bumping this version is what
// actually makes a shell-file change visible to an already-open browser --
// the SW only detects an update by byte-diffing sw.js itself. Bump it EVERY
// time, not just when convenient.
const SHELL_CACHE = 'shell-v10';
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
