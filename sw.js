// =============================================
// RAKESH MART ADMIN — SERVICE WORKER
// Push notifications handle karta hai
// =============================================

const CACHE_NAME = 'rk-admin-v2';
const STATIC_ASSETS = [
  './',
  './index.html'
];

// ---- Install ----
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
  console.log('[SW] Installed');
});

// ---- Activate ----
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
  console.log('[SW] Activated');
});

// ---- Fetch (cache strategy) ----
self.addEventListener('fetch', event => {
  const url = event.request.url;

  // Google APIs aur external URLs ko cache mat karo
  if (
    url.includes('script.google.com') ||
    url.includes('googleapis.com') ||
    url.includes('fonts.googleapis.com') ||
    url.includes('cdnjs.cloudflare.com') ||
    url.includes('onrender.com') ||
    url.includes('api.telegram.org')
  ) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then(cached => {
      return cached || fetch(event.request).catch(() => caches.match('./index.html'));
    })
  );
});

// =============================================
// PUSH NOTIFICATION RECEIVE
// =============================================
self.addEventListener('push', event => {
  console.log('[SW] Push received');

  let payload = {
    title: '🛒 Rakesh Mart',
    body: 'Naya order aaya!',
    icon: './icon-192.png',
    badge: './icon-192.png',
    data: { url: '/' },
    requireInteraction: true,
    vibrate: [200, 100, 200, 100, 200]
  };

  // Server se aaya data parse karo
  if (event.data) {
    try {
      const data = event.data.json();
      payload = {
        title: data.title || '🛒 Rakesh Mart',
        body: data.body || 'Naya order aaya!',
        icon: data.icon || './icon-192.png',
        badge: data.badge || './icon-192.png',
        image: data.image || '',
        data: data.data || { url: '/' },
        requireInteraction: true,
        vibrate: data.vibrate || [200, 100, 200],
        actions: [
          { action: 'view', title: '👁 Order Dekho' },
          { action: 'close', title: '✕ Band Karo' }
        ]
      };
    } catch (e) {
      // Plain text hai
      payload.body = event.data.text();
    }
  }

  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: payload.icon,
      badge: payload.badge,
      image: payload.image,
      data: payload.data,
      requireInteraction: payload.requireInteraction,
      vibrate: payload.vibrate,
      actions: payload.actions || []
    })
  );
});

// =============================================
// NOTIFICATION CLICK
// =============================================
self.addEventListener('notificationclick', event => {
  event.notification.close();

  const action = event.action;
  const data = event.notification.data || {};

  if (action === 'close') return;

  // App kholo ya focus karo
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      // Agar app pehle se khuli hai toh focus karo
      for (const client of clientList) {
        if ('focus' in client) {
          client.focus();
          // Orders page pe navigate karo
          client.postMessage({ type: 'NAVIGATE', page: 'orders' });
          return;
        }
      }
      // Nahi khuli toh naya window kholo
      return clients.openWindow('./index.html');
    })
  );
});

// =============================================
// PUSH SUBSCRIPTION CHANGE
// =============================================
self.addEventListener('pushsubscriptionchange', event => {
  console.log('[SW] Push subscription changed');
  event.waitUntil(
    self.registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: self.VAPID_PUBLIC_KEY
    }).then(subscription => {
      // Naya subscription server ko bhejo
      return fetch('/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(subscription)
      });
    })
  );
});
