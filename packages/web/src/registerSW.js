import { api } from './lib/api.js';

// Base64 URL -> Uint8Array, required by pushManager.subscribe.
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

// Registers the service worker, requests notification permission, subscribes to
// push, and POSTs the subscription to the API. Call from a user gesture.
export async function enablePushNotifications() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    throw new Error('Push notifications are not supported in this browser');
  }

  const registration = await navigator.serviceWorker.register('/service-worker.js', {
    type: 'module',
  });

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    throw new Error('Notification permission denied');
  }

  const { key } = await api.vapidKey();
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(key),
  });

  const json = subscription.toJSON();
  await api.registerPush({ endpoint: json.endpoint, keys: json.keys });
  return true;
}
