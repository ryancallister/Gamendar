// Service worker for Gamendar.
//
// Its only job is notifications. There is no offline caching on purpose: the
// week's availability is the whole point of the app, and showing a stale copy
// of it would be worse than showing nothing.

const TAG_PREFIX = 'gamendar-';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    payload = { title: 'Gamendar', body: event.data ? event.data.text() : '' };
  }

  const title = payload.title || 'Gamendar';
  const options = {
    body: payload.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    // One tag per kind of message, so a second daily summary replaces the
    // first in the shade instead of stacking up.
    tag: TAG_PREFIX + (payload.tag || title).slice(0, 40),
    renotify: true,
    data: { url: payload.url || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      // Focus the app if it is already open rather than opening a second copy.
      for (const client of windows) {
        if ('focus' in client) {
          if ('navigate' in client && new URL(client.url).pathname !== target) {
            return client.navigate(target).then((c) => (c || client).focus());
          }
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
