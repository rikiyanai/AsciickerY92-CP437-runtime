async function deleteAllCaches() {
  var cacheNames = await caches.keys();
  await Promise.all(cacheNames.map(function(cacheName) {
    return caches.delete(cacheName);
  }));
}

self.addEventListener('install', function(event) {
  event.waitUntil((async function() {
    await deleteAllCaches();
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', function(event) {
  event.waitUntil((async function() {
    await deleteAllCaches();
    await self.clients.claim();
    await self.registration.unregister();
  })());
});

self.addEventListener('fetch', function() {
  // Legacy worker retained only so previously-registered clients can uninstall it.
});
