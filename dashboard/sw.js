// Service worker for the installed family app.
// It caches only the app's own static files (everything under /dashboard/), so the app opens
// without a network and shows its offline message. Household data lives outside this scope
// (/timeline, /summary, /evidence) and is never cached; neither is any request carrying the token.
const CACHE = 'tuttu-shell-v6';
const SHELL = ['./', 'index.html', 'base.css', 'app.js', 'client.js', 'day.js', 'voice.js', 'manifest.json',
  'themes/themes.json', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png'];

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
  if (url.origin !== self.location.origin || !url.href.startsWith(self.registration.scope)) return;
  if (url.pathname.includes('/fixtures/')) return;
  // Network first, so a new version shows up on the next launch; the cached copy is the fallback.
  event.respondWith(fetch(request).then(response => {
    if (response.ok && request.mode !== 'navigate') {
      const copy = response.clone();
      caches.open(CACHE).then(cache => cache.put(url.href, copy));
    }
    return response;
  }).catch(() => caches.match(request.mode === 'navigate' ? new URL('./', self.registration.scope).href : url.href)));
});
