(function () {
  var state = document.getElementById('state');
  var btns = document.querySelectorAll('button[data-top]');
  var timer = null;

  function poll() {
    fetch('/api/update/status').then(function (r) { return r.json(); }).then(function (s) {
      state.textContent = (s.running ? '진행 중 ' + s.done + '/' + s.total + ' · ' : '') + s.msg;
      if (s.running) timer = setTimeout(poll, 1500);
      else Array.prototype.forEach.call(btns, function (b) { b.disabled = false; });
    });
  }
  Array.prototype.forEach.call(btns, function (b) {
    b.addEventListener('click', function () {
      Array.prototype.forEach.call(btns, function (x) { x.disabled = true; });
      fetch('/api/update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ top: parseInt(b.getAttribute('data-top'), 10) })
      }).then(function (r) { return r.json(); }).then(function () { clearTimeout(timer); poll(); });
    });
  });
  poll();
})();
