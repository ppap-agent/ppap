const SHELL_CACHE = 'ppap-shell-v1'
const STATIC_CACHE = 'ppap-static-v1'

function isStaticAsset(request, url) {
  return request.method === 'GET'
    && url.origin === self.location.origin
    && (url.pathname.startsWith('/assets/')
      || url.pathname.startsWith('/icons/')
      || url.pathname === '/manifest.webmanifest'
      || url.pathname === '/favicon.ico')
}

function isJsonRequest(request, url) {
  return url.pathname === '/runtime-config.json'
    || request.headers.get('accept')?.includes('application/json') === true
    || request.destination === 'empty'
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(cache => cache.addAll(['/', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-maskable-192.png', '/icons/icon-maskable-512.png']))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys
        .filter(key => (key.startsWith('ppap-shell-') && key !== SHELL_CACHE) || (key.startsWith('ppap-static-') && key !== STATIC_CACHE))
        .map(key => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', event => {
  const request = event.request
  const url = new URL(request.url)

  // API calls, uploads, runtime configuration, and third-party resources must always remain network-only.
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/') || isJsonRequest(request)) return

  if (isStaticAsset(request, url)) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async cache => {
        const cached = await cache.match(request)
        if (cached) return cached
        const response = await fetch(request)
        if (response.ok && response.type === 'basic') await cache.put(request, response.clone())
        return response
      }),
    )
    return
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(new Request(request, {cache: 'no-cache'}))
        .then(async response => {
          if (response.ok && response.type === 'basic' && !response.headers.has('set-cookie')) {
            const cache = await caches.open(SHELL_CACHE)
            await cache.put('/', response.clone())
          }
          return response
        })
        .catch(async () => (await caches.match('/')) || Response.error()),
    )
  }
})
