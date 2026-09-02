(function () {
  'use strict';

  function urlBase64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = window.atob(base64);
    var output = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) output[i] = raw.charCodeAt(i);
    return output;
  }

  function initPush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;
    if (!document.querySelector('.nav-actions')) return;

    navigator.serviceWorker.register('/static/js/beathub-push-sw.js', { scope: '/static/js/' }).then(function (registration) {
      return fetch('/notifications/push/vapid-public-key', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (response) {
          if (!response.ok) throw new Error('push unavailable');
          return response.json();
        })
        .then(function (config) {
          if (!config.enabled || !config.public_key) return null;
          return registration.pushManager.getSubscription().then(function (subscription) {
            if (subscription) return subscription;
            if (Notification.permission === 'denied') return null;
            return Notification.requestPermission().then(function (permission) {
              if (permission !== 'granted') return null;
              return registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(config.public_key)
              });
            });
          });
        })
        .then(function (subscription) {
          if (!subscription) return;
          var key = subscription.getKey('p256dh');
          var auth = subscription.getKey('auth');
          if (!key || !auth) return;
          return fetch('/notifications/push/subscribe', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            body: JSON.stringify({
              endpoint: subscription.endpoint,
              keys: {
                p256dh: btoa(String.fromCharCode.apply(null, new Uint8Array(key))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''),
                auth: btoa(String.fromCharCode.apply(null, new Uint8Array(auth))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
              }
            })
          });
        })
        .catch(function () {});
    }).catch(function () {});
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initPush);
  else initPush();
})();
