// Bumped v6 -> v7: index.html/style.css/app.js changed again (issues now
// shown as small per-bot icons instead of the always-visible text panel).
// Reminder: bumping this version is what actually makes a shell-file change
// visible to an already-open browser -- the SW only detects an update by
// byte-diffing sw.js itself, so editing app.js/style.css/index.html without
// also bumping this touches nothing until this file changes too. Bump it
// EVERY time, not just when it's convenient to remember.
const SHELL_CACHE = 'shell-v7';
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
