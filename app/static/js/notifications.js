(function () {
  'use strict';

  function initNotifications() {
    var nav = document.querySelector('.nav-actions');
    if (!nav || !nav.querySelector('form[action="/logout"]')) return;

    var wrap = document.querySelector('[data-notification-center]');
    if (!wrap) {
      if (document.querySelector('.bh-notification')) return;
      wrap = document.createElement('div');
      wrap.className = 'bh-notification';
      nav.insertBefore(wrap, nav.firstChild);
    } else if (!wrap.classList.contains('bh-notification')) {
      wrap.classList.add('bh-notification');
    }

    if (wrap.dataset.ready === 'true') return;
    wrap.dataset.ready = 'true';
    wrap.innerHTML = '<button type="button" class="bh-notification-bell" aria-label="Notifications" aria-haspopup="true" aria-expanded="false">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"></path><path d="M10 21h4"></path></svg>' +
      '<span class="bh-notification-badge" hidden>0</span></button>' +
      '<div class="bh-notification-dropdown" role="dialog" aria-label="Recent notifications">' +
        '<div class="bh-notification-head"><span class="bh-notification-title">Notifications</span><button type="button" class="bh-notification-mark">Mark all read</button></div>' +
        '<div class="bh-notification-list"><div class="bh-notification-empty">Loading notifications…</div></div>' +
        '<div class="bh-notification-push" hidden><span>Get important BeatHub alerts</span><button type="button">Enable</button></div>' +
        '<div class="bh-notification-foot"><a href="/notifications">View all notifications</a></div>' +
      '</div>';

    var bell = wrap.querySelector('.bh-notification-bell');
    var badge = wrap.querySelector('.bh-notification-badge');
    var dropdown = wrap.querySelector('.bh-notification-dropdown');
    var list = wrap.querySelector('.bh-notification-list');
    var markAll = wrap.querySelector('.bh-notification-mark');
    var pushRow = wrap.querySelector('.bh-notification-push');
    var pushButton = pushRow.querySelector('button');
    var timer = null;

    function close() {
      dropdown.classList.remove('open');
      bell.setAttribute('aria-expanded', 'false');
    }

    function setCount(count) {
      count = Number(count) || 0;
      badge.hidden = count < 1;
      badge.textContent = count > 99 ? '99+' : String(count);
    }

    function text(value) { return value == null ? '' : String(value); }

    function render(items) {
      list.textContent = '';
      if (!items || !items.length) {
        var empty = document.createElement('div');
        empty.className = 'bh-notification-empty';
        empty.textContent = 'You’re all caught up.';
        list.appendChild(empty);
        return;
      }
      items.forEach(function (item) {
        var a = document.createElement('a');
        a.className = 'bh-notification-item' + (item.is_read ? '' : ' unread');
        a.href = item.link || '/notifications';
        var dot = document.createElement('span');
        dot.className = 'bh-notification-dot' + (item.is_read ? ' read' : '');
        dot.setAttribute('aria-hidden', 'true');
        var copy = document.createElement('span');
        copy.className = 'bh-notification-copy';
        var title = document.createElement('span');
        title.className = 'bh-notification-item-title';
        title.textContent = text(item.title);
        var message = document.createElement('span');
        message.className = 'bh-notification-item-message';
        message.textContent = text(item.message);
        var time = document.createElement('span');
        time.className = 'bh-notification-time';
        time.textContent = text(item.time_ago);
        copy.appendChild(title); copy.appendChild(message); copy.appendChild(time);
        a.appendChild(dot); a.appendChild(copy); list.appendChild(a);
      });
    }

    function fetchJson(url) {
      return fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } }).then(function (response) {
        if (response.status === 401 || response.status === 403) throw new Error('unauthorized');
        if (!response.ok) throw new Error('request failed');
        return response.json();
      });
    }

    function refresh() {
      return fetchJson('/notifications/recent').then(function (data) {
        render(data.notifications || []);
        setCount(data.unread_count);
      }).catch(function (error) {
        if (error.message === 'unauthorized') {
          wrap.remove();
          if (timer) clearInterval(timer);
        }
      });
    }

    function refreshPushState() {
      if (!window.BeatHubPush || !window.BeatHubPush.getState) return;
      window.BeatHubPush.getState().then(function (state) {
        if (!state.enabled || state.subscribed || state.permission === 'denied') {
          pushRow.hidden = true;
          return;
        }
        pushRow.hidden = false;
      }).catch(function () {});
    }

    bell.addEventListener('click', function () {
      var open = !dropdown.classList.contains('open');
      if (open) { dropdown.classList.add('open'); bell.setAttribute('aria-expanded', 'true'); refresh(); refreshPushState(); }
      else close();
    });

    pushButton.addEventListener('click', function () {
      if (!window.BeatHubPush || !window.BeatHubPush.enable) return;
      pushButton.disabled = true;
      window.BeatHubPush.enable().then(function (ok) {
        pushButton.disabled = false;
        if (ok) pushRow.hidden = true;
      }).catch(function () { pushButton.disabled = false; });
    });

    markAll.addEventListener('click', function () {
      fetch('/notifications/read-all', { method: 'POST', credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function () { return refresh(); })
        .catch(function () {});
    });

    document.addEventListener('click', function (event) {
      if (!wrap.contains(event.target)) close();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') close();
    });

    refresh();
    timer = window.setInterval(refresh, 45000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initNotifications);
  else initNotifications();
})();
