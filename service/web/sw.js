/* RHOBEAR Captur'd — service worker.
   Shell cache-first for offline app load; network-first (never cache) for API + auth + billing. */
const VERSION = 'capturd-v4-premium';
const SHELL = [
  '/',
  '/m',
  '/manifest.webmanifest',
  '/assets/icons/icon-192.png',
  '/assets/icons/icon-512.png',
  '/assets/capturd-bear.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const { request } = e;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // never cache dynamic / auth / billing surfaces
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/auth/') || url.pathname.startsWith('/billing/')) {
    return; // let it hit the network directly
  }
  // shell + static: cache-first, fall back to network, then to cached shell for navigations
  e.respondWith(
    caches.match(request).then((hit) => hit || fetch(request).then((res) => {
      if (res && res.status === 200 && res.type === 'basic') {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(request, copy));
      }
      return res;
    }).catch(() => request.mode === 'navigate' ? caches.match('/') : undefined))
  );
});
