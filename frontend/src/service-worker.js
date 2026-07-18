// Service worker: receives Web Push messages and shows an OS notification.
/* eslint-env serviceworker */

self.addEventListener('push', (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title ?? 'Match update', {
      body: data.body ?? '',
      icon: data.icon ?? '/icon.png',
      badge: '/icon.png',
    }),
  );
});

// Focus (or open) the app when a notification is clicked.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) return client.focus();
      }
      return self.clients.openWindow('/');
    }),
  );
});
