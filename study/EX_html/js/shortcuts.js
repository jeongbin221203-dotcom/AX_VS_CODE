/* 엑셀 단축키 모음: 검색, 자주 쓰는 키만 보기 */
(function () {
  'use strict';
  var D = window.EXDATA && window.EXDATA.shortcuts;
  var main = document.getElementById('main');
  EX.header('shortcuts.html');
  if (!D) { main.innerHTML = '<div class="card empty">단축키 자료(data/shortcuts.js)를 읽지 못했습니다.</div>'; return; }
  var esc = EX.esc;

  function keys(list, seq) {
    return list.map(function (k, i) {
      var join = i < list.length - 1 ? '<span class="sc-join" aria-hidden="true">' + (seq ? ',' : '+') + '</span><span class="visually-hidden">' + (seq ? ', 그다음 ' : ' 와 함께 ') + '</span>' : '';
      return '<kbd>' + esc(k) + '</kbd>' + join;
    }).join('');
  }
  var total = 0, hot = 0;
  D.groups.forEach(function (g) { g.items.forEach(function (it) { total++; if (it.hot) hot++; }); });

  var h = '<div class="page-head"><div><h1>엑셀 단축키</h1><p>Windows용 Excel(2016 이상) 기준 ' + total + '개 · 시험·실무에서 자주 쓰는 ' + hot + '개는 <span class="sc-hot" title="자주 쓰는 키">★</span> 로 표시했습니다.</p></div>' +
    '<div class="sc-tools"><input id="sc-search" type="search" placeholder="기능·키 검색 (예: 저장, Ctrl+1, 날짜)" aria-label="단축키 검색">' +
    '<label class="small muted" style="white-space:nowrap"><input type="checkbox" id="sc-hot"> ★ 자주 쓰는 키만</label></div></div>' +
    '<p class="muted small">표기: <kbd>Ctrl</kbd><span class="sc-join">+</span><kbd>C</kbd> 는 두 키를 <b>동시에</b> 누르고, <kbd>Alt</kbd><span class="sc-join">,</span><kbd>H</kbd><span class="sc-join">,</span><kbd>O</kbd> 는 <b>한 키씩 차례로</b> 누릅니다. 한글 입력 상태에서는 일부 키가 동작하지 않을 수 있으니 영문으로 바꾼 뒤 누르세요.</p>' +
    '<div id="sc-none" class="card empty hidden">찾는 단축키가 없습니다. \'저장\', \'복사\', \'날짜\', \'필터\'처럼 기능의 일부 단어로 찾아 보세요.</div>';
  D.groups.forEach(function (g) {
    h += '<section class="card" data-sc-group><h2>' + esc(g.name) + '</h2>' + (g.note ? '<p class="muted small">' + esc(g.note) + '</p>' : '') +
      '<table class="t"><thead><tr><th scope="col">단축키</th><th scope="col">기능</th></tr></thead><tbody>';
    g.items.forEach(function (it) {
      var all = [it.k].concat(it.also || []);
      var hay = g.name + ' ' + it.d + ' ' + (it.also_d || '') + ' ' + all.map(function (a) { return a.join('+') + ' ' + a.join(' '); }).join(' ');
      h += '<tr data-sc="' + esc(hay) + '"' + (it.hot ? ' data-hot="1"' : '') + '><td class="sc-keys">' +
        (it.hot ? '<span class="sc-hot" title="자주 쓰는 키"><span class="visually-hidden">자주 쓰는 키 </span>★</span>' : '') + keys(it.k, it.seq) + '</td><td>' + esc(it.d) +
        (it.also_d ? '<div class="muted small">' + esc(it.also_d) + '</div>' : '') + '</td></tr>';
    });
    h += '</tbody></table></section>';
  });
  main.innerHTML = h;

  var search = document.getElementById('sc-search'), only = document.getElementById('sc-hot');
  function apply() {
    var q = search.value.replace(/\s+/g, '').toUpperCase(), onlyHot = only.checked;
    document.querySelectorAll('[data-sc]').forEach(function (r) {
      var miss = q && r.getAttribute('data-sc').replace(/\s+/g, '').toUpperCase().indexOf(q) < 0;
      r.classList.toggle('hidden', !!miss || (onlyHot && !r.hasAttribute('data-hot')));
    });
    document.querySelectorAll('[data-sc-group]').forEach(function (g) {
      g.classList.toggle('hidden', !g.querySelector('[data-sc]:not(.hidden)'));
    });
    document.getElementById('sc-none').classList.toggle('hidden', !!document.querySelector('[data-sc]:not(.hidden)'));
  }
  search.addEventListener('input', apply);
  only.addEventListener('change', apply);
})();
