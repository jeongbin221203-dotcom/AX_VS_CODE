(function () {
  // 위쪽 메뉴 묶음(▾): 하나를 열면 다른 묶음은 닫고, 바깥을 누르거나 Esc 면 닫는다
  var menus = Array.prototype.slice.call(document.querySelectorAll('nav details.menu'));
  menus.forEach(function (m) {
    m.addEventListener('toggle', function () { if (m.open) menus.forEach(function (o) { if (o !== m) o.open = false; }); });
  });
  document.addEventListener('click', function (e) { if (!e.target.closest('details.menu')) menus.forEach(function (o) { o.open = false; }); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') menus.forEach(function (o) { o.open = false; }); });
})();

(function () {
  var q = document.getElementById('q'), box = document.getElementById('results');
  if (!q) return;
  var timer = null, idx = -1, items = [];

  function hide() { box.hidden = true; idx = -1; }
  function render(list) {
    items = list;
    box.textContent = '';
    list.forEach(function (r) {
      var li = document.createElement('li');
      li.appendChild(document.createTextNode(r.name));
      var s = document.createElement('small');
      s.textContent = r.code + ' ' + (r.market || '');
      li.appendChild(s);
      li.addEventListener('mousedown', function () { go(r.code); });
      box.appendChild(li);
    });
    box.hidden = !list.length;
  }
  function go(code) { location.href = '/chart/' + encodeURIComponent(code); }
  function mark() {
    Array.prototype.forEach.call(box.children, function (li, i) { li.classList.toggle('on', i === idx); });
  }

  q.addEventListener('input', function () {
    clearTimeout(timer);
    var v = q.value.trim();
    if (!v) return hide();
    timer = setTimeout(function () {
      fetch('/api/search?q=' + encodeURIComponent(v)).then(function (r) { return r.json(); }).then(render);
    }, 150);
  });
  q.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { idx = Math.min(idx + 1, items.length - 1); mark(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { idx = Math.max(idx - 1, 0); mark(); e.preventDefault(); }
    else if (e.key === 'Enter') {
      if (items[idx] || items[0]) go((items[idx] || items[0]).code);
      else if (/^[A-Za-z0-9.\-]{1,12}$/.test(q.value.trim())) go(q.value.trim());
    } else if (e.key === 'Escape') hide();
  });
  q.addEventListener('blur', function () { setTimeout(hide, 150); });

  document.addEventListener('click', function (e) {
    var tr = e.target.closest && e.target.closest('tr[data-href]');
    if (tr && !e.target.closest('a')) location.href = tr.getAttribute('data-href');
  });
})();
