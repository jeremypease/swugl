const CACHE = 'peapod-v1';

const PRECACHE = [
  '/offline',
  '/static/css/swugl.css',
  '/static/assets/swugl-mark.svg',
  '/static/assets/icon-192.png',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // Static assets: cache-first, refresh in background
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE).then(cache =>
        cache.match(event.request).then(cached => {
          const network = fetch(event.request).then(res => {
            if (res.ok) cache.put(event.request, res.clone());
            return res;
          });
          return cached || network;
        })
      )
    );
    return;
  }

  // HTML navigation: network-first, fall back to /offline
  if (event.request.headers.get('accept') && event.request.headers.get('accept').includes('text/html')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match('/offline').then(r => r || new Response('You are offline.', { headers: { 'Content-Type': 'text/html' } }))
      )
    );
  }
});
