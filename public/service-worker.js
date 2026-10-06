const CACHE_NAME = 'tkb-dtc-v4';
const ASSETS_TO_CACHE = [
  './index.html',
  './logo.png',
  './apple-touch-icon.png',
  './icon-192.png',
  './icon-512.png',
  './manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return Promise.allSettled(
        ASSETS_TO_CACHE.map((url) => {
          return fetch(new Request(url, { cache: 'reload' })).then((response) => {
            if (response && (response.status === 200 || response.type === 'opaque')) {
              return cache.put(url, response).catch(() => {});
            }
          }).catch((err) => {
            console.warn('Could not cache asset:', url, err);
          });
        })
      );
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  // Ignore any non-http(s) requests (e.g. chrome-extension://, moz-extension://)
  if (!request.url.startsWith('http:') && !request.url.startsWith('https:')) return;

  // Always bypass Service Worker for service-worker.js itself
  if (request.url.includes('service-worker.js')) return;

  // 1. Navigation / HTML requests: Network First, fallback to cached index.html offline
  const isHtml = request.mode === 'navigate' || 
                 request.url.endsWith('/') || 
                 request.url.endsWith('index.html') ||
                 (request.headers.get('accept') && request.headers.get('accept').includes('text/html'));

  if (isHtml) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.status === 200) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseClone).catch(() => {});
              cache.put('./index.html', responseClone.clone()).catch(() => {});
            }).catch(() => {});
          }
          return response;
        })
        .catch(() => caches.match('./index.html'))
    );
    return;
  }

  // 2. API calls: Network first, fallback to cached data if offline
  if (request.url.includes('/api/')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.status === 200) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseClone).catch(() => {});
            }).catch(() => {});
          }
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // 3. Static assets (Images, Manifest): Cache First, then Network
  event.respondWith(
    caches.match(request).then((cached) => {
      return cached || fetch(request).then((response) => {
        if (response && response.status === 200) {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseClone).catch(() => {});
          }).catch(() => {});
        }
        return response;
      });
    }).catch(() => caches.match('./index.html'))
  );
});
