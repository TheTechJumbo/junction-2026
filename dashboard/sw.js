// Service worker for the installed family app.
// It caches only the app's own static files, so the app opens without a network and shows its
// offline message. Household data (timeline, summary, evidence images) and the access token are
// never cached: those requests pass straight through to the network.
const CACHE = 'daylight-shell-v1';
const SHELL = ['./', 'index.html', 'styles.css', 'app.js', 'client.js', 'manifest.json',
  'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png'];
const shellUrls = new Set(SHELL.map(path => new URL(path, self.registration.scope).href));

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET' || request.headers.has('Authorization')) return;
  const url = new URL(request.url);
  url.search = '';
  const isShell = request.mode === 'navigate' ? url.href.startsWith(self.registration.scope) : shellUrls.has(url.href);
  if (!isShell) return;
  // Network first, so a new version shows up on the next launch; the cached copy is the fallback.
  event.respondWith(fetch(request).then(response => {
    if (response.ok && request.mode !== 'navigate') {
      const copy = response.clone();
      caches.open(CACHE).then(cache => cache.put(url.href, copy));
    }
    return response;
  }).catch(() => caches.match(request.mode === 'navigate' ? new URL('./', self.registration.scope).href : url.href)));
});
