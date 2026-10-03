import { api } from './lib/api.js';

export async function existingBrowserPush() {
  if (!globalThis.navigator?.serviceWorker?.getRegistration) return null;
  const registration = await navigator.serviceWorker.getRegistration('/');
  return registration?.pushManager?.getSubscription() ?? null;
}

// Fail closed: keep the session until both remote and local cleanup succeed.
export async function detachBrowserPush(authenticated = true) {
  const subscription = await existingBrowserPush();
  if (!subscription) return;
  try {
    if (authenticated) await api.unregisterPush(subscription.endpoint);
  } catch (error) {
    // Expired authentication cannot remove the old account's stored row. Revoke
    // this browser destination locally, including expiry after the session check.
    if (error.status !== 401) {
      throw new Error('Could not disconnect browser notifications. Please retry before signing out or switching accounts.');
    }
  }
  try {
    const removed = await subscription.unsubscribe();
    if (!removed && await existingBrowserPush()) throw new Error('Subscription still active');
  } catch {
    throw new Error('Could not disable browser notifications. Please retry before signing out or switching accounts.');
  }
}

export async function logoutWithPushCleanup() {
  await detachBrowserPush();
  await api.logout();
}

export async function authenticateWithPushCleanup(action, ...args) {
  // Read the current cookie-backed account rather than trusting stale tab state.
  let authenticated = true;
  try {
    await api.me();
  } catch (error) {
    if (error.status !== 401) throw error;
    authenticated = false;
  }
  // An expired session cannot delete another account's rows. Revoke its browser
  // destination locally instead; never carry that endpoint into the new account.
  await detachBrowserPush(authenticated);
  return action(...args);
}
