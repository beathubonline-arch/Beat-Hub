(function () {
  'use strict';

  var registrationPromise = null;

  function urlBase64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = window.atob(base64);
    var output = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) output[i] = raw.charCodeAt(i);
    return output;
  }

  function toBase64Url(buffer) {
    return btoa(String.fromCharCode.apply(null, new Uint8Array(buffer)))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function supported() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }

  function registration() {
    if (!supported()) return Promise.reject(new Error('push unsupported'));
    if (!registrationPromise) registrationPromise = navigator.serviceWorker.register('/static/js/beathub-push-sw.js', {scope: '/static/js/'});
    return registrationPromise;
  }

  function config() {
    return fetch('/notifications/push/vapid-public-key', {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
      .then(function (response) {
        if (!response.ok) throw new Error('push unavailable');
        return response.json();
      });
  }

  function saveSubscription(subscription) {
    var key = subscription.getKey('p256dh');
    var auth = subscription.getKey('auth');
    if (!key || !auth) return Promise.reject(new Error('invalid subscription'));
    return fetch('/notifications/push/subscribe', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: JSON.stringify({endpoint: subscription.endpoint, keys: {p256dh: toBase64Url(key), auth: toBase64Url(auth)}})
    }).then(function (response) {
      if (!response.ok) throw new Error('subscription save failed');
      return response.json();
    });
  }

  function getState() {
    if (!supported()) return Promise.resolve({enabled: false, subscribed: false, permission: 'unsupported'});
    return config().then(function (cfg) {
      return registration().then(function (reg) {
        return reg.pushManager.getSubscription().then(function (subscription) {
          return {enabled: !!(cfg.enabled && cfg.public_key), subscribed: !!subscription, permission: Notification.permission};
        });
      });
    }).catch(function () { return {enabled: false, subscribed: false, permission: Notification.permission}; });
  }

  function enable() {
    if (!supported()) return Promise.resolve(false);
    return config().then(function (cfg) {
      if (!cfg.enabled || !cfg.public_key) return false;
      return registration().then(function (reg) {
        return reg.pushManager.getSubscription().then(function (subscription) {
          if (subscription) return saveSubscription(subscription).then(function () { return true; });
          return Notification.requestPermission().then(function (permission) {
            if (permission !== 'granted') return false;
            return reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(cfg.public_key)})
              .then(function (newSubscription) { return saveSubscription(newSubscription).then(function () { return true; }); });
          });
        });
      });
    });
  }

  window.BeatHubPush = {enable: enable, getState: getState};

  // Register quietly; permission is requested only after the user clicks Enable.
  if (supported() && document.querySelector('.nav-actions')) registration().catch(function () {});
})();
