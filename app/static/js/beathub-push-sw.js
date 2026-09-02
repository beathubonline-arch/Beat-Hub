self.addEventListener('push', function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {body: event.data ? event.data.text() : ''}; }
  var title = data.title || 'BeatHub';
  var options = {
    body: data.body || 'You have a new BeatHub notification.',
    data: {link: data.link || '/notifications'},
    tag: data.tag || 'beathub-notification',
    renotify: true
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var target = (event.notification.data && event.notification.data.link) || '/notifications';
  event.waitUntil(clients.matchAll({type: 'window', includeUncontrolled: true}).then(function (windows) {
    for (var i = 0; i < windows.length; i += 1) {
      if ('focus' in windows[i]) {
        windows[i].navigate(target);
        return windows[i].focus();
      }
    }
    return clients.openWindow(target);
  }));
});
