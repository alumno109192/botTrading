/**
 * Service Worker — BotTrading Push Notifications
 * Gestiona las notificaciones push del navegador.
 * Al hacer clic en la notificación abre el dashboard con los detalles de la señal.
 */

const DASHBOARD_URL = '/dashboard/activas';

// ── Activación inmediata: no esperar a que se cierren pestañas anteriores ──
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

// ── Recibir notificación push desde el servidor ───────────────────────────
self.addEventListener('push', function(event) {
    if (!event.data) return;

    let data;
    try {
        data = event.data.json();
    } catch {
        data = { title: '📊 BotTrading', body: event.data.text() };
    }

    const title   = data.title  || '📊 BotTrading — Nueva Señal';
    const options = {
        body:    data.body    || 'Nueva señal detectada. Toca para ver los detalles.',
        icon:    '/static/img/icon-192.png',
        badge:   '/static/img/badge-72.png',
        tag:     data.tag     || 'bottrading-signal',   // agrupa notificaciones del mismo tag
        renotify: true,                                  // notifica aunque haya una con el mismo tag
        vibrate: [200, 100, 200],
        data: {
            url:      data.url      || DASHBOARD_URL,
            senal_id: data.senal_id || null,
        },
        actions: [
            { action: 'open',    title: '📊 Ver señal' },
            { action: 'dismiss', title: '✕ Cerrar'     },
        ],
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

// ── Clic en la notificación ───────────────────────────────────────────────
self.addEventListener('notificationclick', function(event) {
    event.notification.close();

    if (event.action === 'dismiss') return;

    const targetUrl = (event.notification.data && event.notification.data.url)
        ? event.notification.data.url
        : DASHBOARD_URL;

    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(function(clientList) {
                // Si ya hay una pestaña del dashboard abierta → enfocarla
                for (const client of clientList) {
                    if (client.url.includes('/dashboard') && 'focus' in client) {
                        return client.focus().then(c => c.navigate(targetUrl));
                    }
                }
                // Si no → abrir nueva pestaña
                return self.clients.openWindow(targetUrl);
            })
    );
});
