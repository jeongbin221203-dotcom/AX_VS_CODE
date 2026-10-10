(function () {
  var form = document.getElementById('go');
  if (!form) return;
  var tech = form.getAttribute('data-tech');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var v = document.getElementById('gcode').value.trim();
    if (!v) return;
    function open(code) { location.href = '/chart/' + encodeURIComponent(code) + '?tech=' + encodeURIComponent(tech); }
    if (/^[A-Za-z0-9.\-]{1,12}$/.test(v) && /\d/.test(v)) return open(v);
    fetch('/api/search?q=' + encodeURIComponent(v)).then(function (r) { return r.json(); }).then(function (l) {
      if (l.length) open(l[0].code); else alert('종목을 찾지 못했습니다');
    });
  });
})();
