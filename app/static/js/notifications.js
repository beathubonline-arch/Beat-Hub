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

  function initBeatCreatorFolders() {
    // The marketplace's beat grid is the authoritative signal. Do not
    // depend on a pathname because the same marketplace template can be
    // reached through compatibility routes or rewritten URLs.
    var grid = document.querySelector('.market-grid');
    if (!grid || grid.dataset.creatorFoldersReady === 'true') return false;

    var cards = Array.prototype.slice.call(grid.querySelectorAll('.beat-card'));
    if (!cards.length) return false;

    var groups = new Map();
    cards.forEach(function (card) {
      var nameNode = card.querySelector('.producer-name');
      var producer = nameNode ? nameNode.textContent.trim() : 'BeatHub Creator';
      if (!producer) producer = 'BeatHub Creator';

      if (!groups.has(producer)) groups.set(producer, []);
      groups.get(producer).push(card);
    });

    if (!groups.size) return false;

    if (!document.getElementById('bh-beat-folder-styles')) {
      var style = document.createElement('style');
      style.id = 'bh-beat-folder-styles';
      style.textContent = [
        '.bh-beat-folders{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;align-items:start}',
        '.bh-beat-folder{min-width:0;overflow:hidden;border:1px solid #292929;border-radius:20px;background:linear-gradient(145deg,#141414,#0b0b0b);box-shadow:0 18px 50px rgba(0,0,0,.28)}',
        '.bh-beat-folder-head{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:18px 20px;border-bottom:1px solid #252525;background:linear-gradient(180deg,#171714,#10100f)}',
        '.bh-beat-folder-title{display:flex;align-items:center;gap:12px;min-width:0}',
        '.bh-beat-folder-icon{width:38px;height:32px;display:grid;place-items:center;flex:0 0 auto;border:1px solid rgba(244,211,94,.28);border-radius:9px;background:#1b1b17;color:#f4d35e;font-size:17px}',
        '.bh-beat-folder-name{min-width:0;color:#f2f2ef;font-size:14px;font-weight:900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
        '.bh-beat-folder-count{display:block;margin-top:3px;color:#777;font-size:11px}',
        '.bh-beat-folder-latest{color:#f4d35e;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.1em;white-space:nowrap}',
        '.bh-beat-folder-beats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));justify-content:start;align-items:start;gap:14px;padding:16px;max-width:680px}',
        '.bh-beat-folder-beats .beat-card{width:100%;max-width:310px;min-width:0;margin:0;align-self:start;overflow:hidden}',
        '.bh-beat-folder-beats .beat-card .artwork{width:100%;height:auto;aspect-ratio:1/1;min-height:0;max-height:none;overflow:hidden}',
        '.bh-beat-folder-beats .beat-card .artwork img{display:block;width:100%;height:100%;min-width:0;min-height:0;max-width:none;max-height:none;aspect-ratio:1/1;object-fit:cover;object-position:center}',
        '.bh-beat-folder-beats .beat-card .artwork-fallback{width:100%;height:100%;aspect-ratio:1/1;display:grid;place-items:center;overflow:hidden}',
        '.bh-beat-folder-beats .beat-card:hover{transform:translateY(-3px)}',
        '@media(max-width:900px){.bh-beat-folders{grid-template-columns:1fr}.bh-beat-folder-beats{max-width:680px}}',
        '@media(max-width:560px){.bh-beat-folder-beats{grid-template-columns:1fr;max-width:330px;padding:14px}.bh-beat-folder-beats .beat-card{max-width:310px}.bh-beat-folder-head{padding:15px}.bh-beat-folder-latest{display:none}}'
      ].join('');
      document.head.appendChild(style);
    }

    var folderGrid = document.createElement('div');
    folderGrid.className = 'bh-beat-folders';

    groups.forEach(function (producerCards, producer) {
      var folder = document.createElement('section');
      folder.className = 'bh-beat-folder';

      var head = document.createElement('div');
      head.className = 'bh-beat-folder-head';

      var title = document.createElement('div');
      title.className = 'bh-beat-folder-title';
      title.innerHTML = '<span class="bh-beat-folder-icon" aria-hidden="true">♫</span>' +
        '<span><span class="bh-beat-folder-name"></span><span class="bh-beat-folder-count"></span></span>';
      title.querySelector('.bh-beat-folder-name').textContent = producer;
      title.querySelector('.bh-beat-folder-count').textContent = producerCards.length + (producerCards.length === 1 ? ' beat' : ' beats');

      var latest = document.createElement('span');
      latest.className = 'bh-beat-folder-latest';
      latest.textContent = 'Newest first';
      head.appendChild(title);
      head.appendChild(latest);

      var beatGrid = document.createElement('div');
      beatGrid.className = 'bh-beat-folder-beats';
      producerCards.forEach(function (card) { beatGrid.appendChild(card); });

      folder.appendChild(head);
      folder.appendChild(beatGrid);
      folderGrid.appendChild(folder);
    });

    grid.replaceWith(folderGrid);
    folderGrid.dataset.creatorFoldersReady = 'true';
    return true;
  }

  function initCreatorMerchFolders() {
    // Only operate on the public merchandise catalogue. Detail pages do not
    // contain .bh-merch-grid, so purchase/checkout UI is untouched.
    var grid = document.querySelector('.bh-merch-grid');
    if (!grid || grid.dataset.creatorFoldersReady === 'true') return false;

    var cards = Array.prototype.slice.call(grid.querySelectorAll('.bh-merch-card'));
    if (!cards.length) return false;

    var groups = new Map();
    cards.forEach(function (card) {
      var nameNode = card.querySelector('.bh-merch-creator');
      var creator = nameNode ? nameNode.textContent.trim() : 'BeatHub Creator';
      if (!creator) creator = 'BeatHub Creator';
      if (!groups.has(creator)) groups.set(creator, []);
      groups.get(creator).push(card);
    });

    var organized = document.createElement('div');
    organized.className = 'bh-merch-organized';

    groups.forEach(function (creatorCards, creator) {
      if (creatorCards.length === 1) {
        var single = document.createElement('div');
        single.className = 'bh-merch-singleton';
        single.appendChild(creatorCards[0]);
        organized.appendChild(single);
        return;
      }

      var folder = document.createElement('section');
      folder.className = 'bh-merch-creator-folder';

      var head = document.createElement('div');
      head.className = 'bh-merch-folder-head';
      var title = document.createElement('div');
      title.className = 'bh-merch-folder-title';
      var icon = document.createElement('span');
      icon.className = 'bh-merch-folder-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = '✦';
      var copy = document.createElement('span');
      var name = document.createElement('span');
      name.className = 'bh-merch-folder-name';
      name.textContent = creator;
      var count = document.createElement('span');
      count.className = 'bh-merch-folder-count';
      count.textContent = creatorCards.length + ' merch items';
      copy.appendChild(name);
      copy.appendChild(count);
      title.appendChild(icon);
      title.appendChild(copy);

      var newest = document.createElement('span');
      newest.className = 'bh-merch-folder-latest';
      newest.textContent = 'Newest first';
      head.appendChild(title);
      head.appendChild(newest);

      var cardGrid = document.createElement('div');
      cardGrid.className = 'bh-merch-folder-items';
      creatorCards.forEach(function (card) { cardGrid.appendChild(card); });
      folder.appendChild(head);
      folder.appendChild(cardGrid);
      organized.appendChild(folder);
    });

    grid.replaceWith(organized);
    organized.dataset.creatorFoldersReady = 'true';
    return true;
  }

  function init() {
    initNotifications();
    if (initBeatCreatorFolders()) return;
    if (initCreatorMerchFolders()) return;

    // Give server-rendered/late-inserted marketplace cards a short second
    // chance without polling the page indefinitely.
    var attempts = 0;
    var retry = window.setInterval(function () {
      attempts += 1;
      if (initBeatCreatorFolders() || initCreatorMerchFolders() || attempts >= 10) window.clearInterval(retry);
    }, 100);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
