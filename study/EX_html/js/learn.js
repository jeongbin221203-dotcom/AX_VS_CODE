/* 문제 풀기: 선택형은 바로 채점. 수식 문제는 브라우저 안의 엑셀 계산기로 값을 계산해 채점(웹 주소로 열 때), file:// 에서는 정답 수식과 글자로 비교 */
(function () {
  'use strict';
  var P = window.EXDATA && window.EXDATA.problems;
  var F = window.EXDATA && window.EXDATA.functions;
  var main = document.getElementById('main');
  EX.header('learn.html');
  if (!P || !F) { main.innerHTML = '<div class="card empty">문제 자료(data/problems.js)를 읽지 못했습니다.</div>'; return; }
  var esc = EX.esc, store = EX.store;
  var TRACKS = F.tracks;
  var cats = F.categories;
  var catName = {};
  cats.forEach(function (c) { catName[c[0]] = c[1]; });
  var byId = {};
  P.items.forEach(function (p) { byId[p.id] = p; });

  function att() { return store.get('l.att', {}); }
  function record(id, ok) {
    var a = att(), r = a[id] || { n: 0, c: 0, ok: false };
    r.n++; r.c += ok ? 1 : 0; r.ok = ok;
    a[id] = r;
    store.set('l.att', a);
  }
  function category(p) { return p.category || p.id.replace(/-\d+$/, ''); }

  /* ---------- 수식 비교 ---------- */
  function norm(s) {
    s = String(s == null ? '' : s).trim();
    var out = '', q = false;
    for (var i = 0; i < s.length; i++) {
      var ch = s[i];
      if (ch === '"') { q = !q; out += ch; continue; }
      if (q) { out += ch; continue; }
      if (/\s/.test(ch) || ch === '{' || ch === '}') continue;     // 배열 수식 중괄호는 Ctrl+Shift+Enter 로 붙는 것이라 비교에서 뺀다
      out += ch.toUpperCase();
    }
    return out.charAt(0) === '=' ? out : '=' + out;
  }
  function judge(p, input) {
    var mine = norm(input);
    if (mine === '=') return { kind: 'empty' };
    var good = [p.answer].concat(p.alts || []);
    if (good.some(function (g) { return norm(g) === mine; })) return { kind: 'exact' };
    var loose = function (x) { return x.replace(/\$/g, ''); };
    if (good.some(function (g) { return loose(norm(g)) === loose(mine); })) return { kind: 'loose' };
    if ((p.wrong || []).some(function (w) { return norm(w) === mine; })) return { kind: 'wrong' };
    return { kind: 'unknown' };
  }

  /* ---------- 시트 그림 ---------- */
  function colName(n) { var s = ''; n++; while (n > 0) { var m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); } return s; }
  function parseAddr(a) {
    var m = /^([A-Za-z]+)(\d+)$/.exec(a);
    if (!m) return null;
    var c = 0;
    m[1].toUpperCase().split('').forEach(function (ch) { c = c * 26 + (ch.charCodeAt(0) - 64); });
    return { r: Number(m[2]) - 1, c: c - 1 };
  }
  function fmtCell(v, fmt) {
    if (typeof v === 'string' && v.charAt(0) === '@') return v.slice(1);
    if (typeof v === 'number') {
      if (fmt && fmt.indexOf('#,##0') >= 0) return v.toLocaleString('ko-KR');
      return String(v);
    }
    return v == null ? '' : String(v);
  }
  function sheetHtml(p) {
    var sh = p.sheet;
    if (!sh) return '';
    var grid = {}, fmts = (sh.formats || {}), maxR = 0, maxC = 0, base = null;
    if (sh.base && P.datasets[sh.base]) { base = P.datasets[sh.base]; fmts = Object.assign({}, base.formats || {}, sh.formats || {}); }
    var rows = base ? base.rows : (sh.rows || []);
    rows.forEach(function (row, r) { row.forEach(function (v, c) { grid[r + ',' + c] = { v: v, f: fmts[colName(c)] }; maxR = Math.max(maxR, r); maxC = Math.max(maxC, c); }); });
    Object.keys(sh.cells || {}).forEach(function (a) {
      var pos = parseAddr(a);
      if (!pos) return;
      grid[pos.r + ',' + pos.c] = { v: sh.cells[a], f: fmts[colName(pos.c)] };
      maxR = Math.max(maxR, pos.r); maxC = Math.max(maxC, pos.c);
    });
    var tgt = p.target ? parseAddr(p.target) : null;
    var fill = null;                       // 채우기 범위(H2:H16) — 결과가 그려질 칸
    if (p.fill) {
      var fm = /^([A-Za-z]+\d+):([A-Za-z]+\d+)$/.exec(p.fill);
      if (fm) fill = { a: parseAddr(fm[1]), b: parseAddr(fm[2]) };
    }
    if (tgt) { maxR = Math.max(maxR, tgt.r); maxC = Math.max(maxC, tgt.c); }
    if (fill && fill.a && fill.b) { maxR = Math.max(maxR, fill.b.r); maxC = Math.max(maxC, fill.b.c); }
    var h = '<div class="sheet-wrap"><table class="sheet"><thead><tr><th class="rn"></th>';
    for (var c = 0; c <= maxC; c++) h += '<th>' + colName(c) + '</th>';
    h += '</tr></thead><tbody>';
    for (var r = 0; r <= maxR; r++) {
      h += '<tr><th class="rn">' + (r + 1) + '</th>';
      for (c = 0; c <= maxC; c++) {
        var cell = grid[r + ',' + c], isT = tgt && tgt.r === r && tgt.c === c;
        var inFill = fill && fill.a && fill.b && r >= fill.a.r && r <= fill.b.r && c >= fill.a.c && c <= fill.b.c;
        var num = cell && typeof cell.v === 'number';
        h += '<td data-a="' + colName(c) + (r + 1) + '" class="' + (num ? 'num' : '') + (isT || inFill ? ' tgt' : '') + '"' + (isT ? ' title="답을 입력할 셀"' : '') + '>' + (cell ? esc(fmtCell(cell.v, cell.f)) : '') + '</td>';
      }
      h += '</tr>';
    }
    return h + '</tbody></table></div>';
  }

  /* ---------- 목록 ---------- */
  var filter = store.get('l.filter', { track: '', cat: '', q: '' });
  function viewList() {
    var a = att();
    var done = P.items.filter(function (p) { return a[p.id]; }).length, okN = P.items.filter(function (p) { return a[p.id] && a[p.id].ok; }).length;
    var h = '<div class="page-head"><div><h1>문제 풀기</h1><p>문제 ' + P.items.length + '개 · 푼 문제 ' + done + '개 · 마지막에 맞힌 것 ' + okN + '개</p></div>' +
      '<div class="row"><select id="f-track" aria-label="학습 범위"><option value="">전체 범위</option>' + Object.keys(TRACKS).map(function (k) {
        return '<option value="' + k + '"' + (filter.track === k ? ' selected' : '') + '>' + esc(TRACKS[k]) + '</option>';
      }).join('') + '</select><input id="f-q" type="search" placeholder="문제·함수 검색" value="' + esc(filter.q) + '" aria-label="문제 검색"></div></div>' +
      '<div class="tabs" id="f-cats"><button class="tab' + (!filter.cat ? ' on' : '') + '" data-cat="">전체</button>' +
      cats.map(function (c) {
        var n = P.items.filter(function (p) { return category(p) === c[0]; }).length;
        return n ? '<button class="tab' + (filter.cat === c[0] ? ' on' : '') + '" data-cat="' + c[0] + '">' + esc(c[1]) + ' ' + n + '</button>' : '';
      }).join('') + '</div><div id="f-list"></div>';
    main.innerHTML = h;
    window.scrollTo(0, 0);
    function paint() {
      var q = filter.q.replace(/\s+/g, '').toUpperCase();
      var items = P.items.filter(function (p) {
        if (filter.track && (p.tracks || []).indexOf(filter.track) < 0) return false;
        if (filter.cat && category(p) !== filter.cat) return false;
        if (q) {
          var hay = (p.title + ' ' + p.prompt + ' ' + (p.functions || []).join(' ') + ' ' + p.id).replace(/\s+/g, '').toUpperCase();
          if (hay.indexOf(q) < 0) return false;
        }
        return true;
      });
      var A = att();
      document.getElementById('f-list').innerHTML = items.length ? '<div class="card"><table class="t"><thead><tr><th>문제</th><th>범위</th><th>유형</th><th>상태</th></tr></thead><tbody>' +
        items.map(function (p) {
          var r = A[p.id];
          return '<tr><td><a href="#/' + encodeURIComponent(p.id) + '">' + esc(p.title) + '</a> <span class="muted small">' + esc(p.id) + '</span></td><td>' +
            (p.tracks || []).map(function (t) { return '<span class="badge ' + t + '">' + esc(TRACKS[t] || t) + '</span>'; }).join(' ') + '</td><td class="small">' +
            (p.type === 'formula' ? '수식 입력' : '선택형') + '</td><td>' + (r ? (r.ok ? '<span class="badge ok">맞힘</span>' : '<span class="badge bad">틀림</span>') : '<span class="muted small">-</span>') + '</td></tr>';
        }).join('') + '</tbody></table></div>' : '<div class="card empty">조건에 맞는 문제가 없습니다.</div>';
    }
    paint();
    document.getElementById('f-track').addEventListener('change', function (e) { filter.track = e.target.value; store.set('l.filter', filter); paint(); });
    document.getElementById('f-q').addEventListener('input', function (e) { filter.q = e.target.value; store.set('l.filter', filter); paint(); });
    document.getElementById('f-cats').addEventListener('click', function (e) {
      var b = e.target.closest('[data-cat]');
      if (!b) return;
      filter.cat = b.getAttribute('data-cat');
      store.set('l.filter', filter);
      document.querySelectorAll('#f-cats .tab').forEach(function (t) { t.classList.toggle('on', t === b); });
      paint();
    });
  }

  /* ---------- 문제 하나 ---------- */
  function nextId(id) {
    var q = filter.q.replace(/\s+/g, '').toUpperCase();
    var list = P.items.filter(function (p) {
      if (filter.track && (p.tracks || []).indexOf(filter.track) < 0) return false;
      if (filter.cat && category(p) !== filter.cat) return false;
      return true;
    });
    var i = list.findIndex(function (p) { return p.id === id; });
    return i >= 0 && i < list.length - 1 ? list[i + 1].id : null;
  }
  function viewProblem(id) {
    var p = byId[id];
    if (!p) { main.innerHTML = '<div class="card empty">문제를 찾을 수 없습니다.<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    var nxt = nextId(id);
    var h = '<p><a href="#/">← 문제 목록</a></p><div class="page-head"><div><h1>' + esc(p.title) + '</h1><p class="muted small">' + esc(p.id) + ' · ' + esc(catName[category(p)] || category(p)) + ' · ' +
      (p.tracks || []).map(function (t) { return esc(TRACKS[t] || t); }).join(' / ') + '</p></div></div>';
    h += '<article class="q"><p class="qtext">' + esc(p.prompt) + '</p>' + sheetHtml(p);
    if (p.type === 'choice') {
      h += '<div class="opts">' + p.options.map(function (o, i) {
        return '<button type="button" class="opt" data-i="' + i + '"><span class="n">' + (i + 1) + '</span><span>' + esc(o) + '</span></button>';
      }).join('') + '</div>';
    } else {
      var useEngine = EX.py && EX.py.available();
      h += '<div class="fbar"><span class="fx">fx</span><input class="formula" id="f-input" type="text" autocomplete="off" spellcheck="false" placeholder="' + esc((p.target || '') + ' 에 입력할 수식 (예: =SUM(A1:A5))') + '" aria-label="수식 입력"></div>' +
        '<div class="row">' + (useEngine ? '<button class="btn" id="f-try" type="button">계산해 보기</button>' : '') + '<button class="btn primary" id="f-check" type="button">채점</button><button class="btn" id="f-show" type="button">정답 보기</button></div>' +
        '<p class="muted small" style="margin-top:8px">' + (useEngine
          ? '브라우저 안의 엑셀 계산기로 수식을 채우기 범위 전체에 계산해 값을 비교합니다(처음 한 번 엔진을 불러오느라 몇 초 걸립니다). 같은 값을 내는 다른 수식도 정답으로 봅니다.'
          : '파일을 직접 연 상태(file://)라 엑셀 계산기를 쓸 수 없어, 정답 수식과 글자(공백·대소문자·$ 무시)로 비교합니다. 값이 같은 다른 수식은 스스로 판단합니다. 웹 주소(start.bat)로 열면 값을 계산해 채점합니다.') + '</p>';
    }
    h += '<div id="p-out"></div></article>';
    main.innerHTML = h;
    window.scrollTo(0, 0);
    var out = document.getElementById('p-out');
    function nextBtn() {
      return '<div class="row" style="margin-top:10px">' + (nxt ? '<a class="btn primary" href="#/' + encodeURIComponent(nxt) + '">다음 문제</a>' : '') + '<a class="btn" href="#/">목록으로</a></div>';
    }
    function related() {
      var fs = (p.functions || []).map(function (n) { return '<a href="functions.html#' + encodeURIComponent(n) + '">' + esc(n) + '</a>'; });
      return fs.length ? '<p class="small muted" style="margin-top:8px">관련 함수: ' + fs.join(', ') + '</p>' : '';
    }
    function showAnswer(box) {
      return box + '<p style="margin-top:8px"><b>정답 수식</b></p><code class="answer-code">' + esc(p.answer) + '</code>' +
        (p.alts && p.alts.length ? '<p class="muted small" style="margin:6px 0 2px">같은 결과를 내는 다른 정답</p>' + p.alts.map(function (a) { return '<code class="answer-code">' + esc(a) + '</code>'; }).join('') : '') +
        '<div class="explain">' + esc(p.explain) + '</div>' + related();
    }
    if (p.type === 'choice') {
      main.onclick = function (ev) {
        var b = ev.target.closest('.opt');
        if (!b || out.getAttribute('data-done')) return;
        out.setAttribute('data-done', '1');
        var i = Number(b.getAttribute('data-i')), ok = i === p.answer;
        record(p.id, ok);
        main.querySelectorAll('.opt').forEach(function (x) {
          var k = Number(x.getAttribute('data-i'));
          x.disabled = true;
          if (k === p.answer) x.classList.add('right'); else if (k === i) x.classList.add('wrong');
        });
        out.innerHTML = '<div class="result-box ' + (ok ? 'ok' : 'bad') + '"><b>' + (ok ? '정답입니다.' : '오답입니다. 정답은 ' + (p.answer + 1) + '번') + '</b></div><div class="explain">' + esc(p.explain) + '</div>' + related() + nextBtn();
      };
    } else {
      var input = document.getElementById('f-input');
      /* 한 번 계산한 결과를 시트에 그린다(맞/틀림 색, 칸 위에 마우스를 올리면 그 칸의 수식) */
      var tds = {};
      main.querySelectorAll('table.sheet td[data-a]').forEach(function (td) { tds[td.getAttribute('data-a')] = td; td.setAttribute('data-orig', td.textContent); });
      function resetGrid() {
        Object.keys(tds).forEach(function (a) { var td = tds[a]; td.classList.remove('okc', 'badc', 'spill'); td.textContent = td.getAttribute('data-orig'); td.removeAttribute('title'); });
      }
      function paint(res, graded) {
        resetGrid();
        (res.cells || []).forEach(function (c, i) {
          var td = tds[c.addr];
          if (!td) return;
          td.textContent = c.value;
          if (c.formula) td.title = c.formula;
          if (c.spill) td.classList.add('spill');
          else if (graded && res.per) td.classList.add(res.per[i] ? 'okc' : 'badc');
        });
      }
      function fixText(t) { t = t.trim(); return /^[=+]|^\{=/.test(t) ? t : '=' + t; }

      /* 글자 비교(엔진을 쓸 수 없을 때) */
      function textCheck(note) {
        var j = judge(p, input.value), box = '';
        if (j.kind === 'empty') { out.innerHTML = '<div class="result-box warn">수식을 입력하세요.</div>'; return; }
        var pre = note ? '<div class="result-box warn small">' + esc(note) + '</div>' : '';
        if (j.kind === 'exact' || j.kind === 'loose') {
          box = '<div class="result-box ok"><b>정답입니다.</b>' + (j.kind === 'loose' ? ' (정답과 $ 절대·상대 참조 표시만 다릅니다)' : '') + '</div>';
          record(p.id, true);
          out.innerHTML = pre + box + '<div class="explain ok">' + esc(p.explain) + '</div>' + related() + nextBtn();
          return;
        }
        if (j.kind === 'wrong') {
          record(p.id, false);
          out.innerHTML = pre + showAnswer('<div class="result-box bad"><b>자주 하는 실수와 같은 수식입니다.</b></div>') + nextBtn();
          return;
        }
        out.innerHTML = pre + showAnswer('<div class="result-box warn"><b>정답 수식과 글자가 다릅니다.</b> 같은 값을 내는 다른 수식일 수 있어 자동으로 알 수 없습니다. 아래 정답과 비교해 직접 선택하세요.</div>') +
          '<div class="row" style="margin-top:10px"><button class="btn" data-self="1" type="button">내 수식도 맞다</button><button class="btn" data-self="0" type="button">내 수식은 틀렸다</button></div>';
        out.querySelectorAll('[data-self]').forEach(function (b) {
          b.addEventListener('click', function () {
            record(p.id, b.getAttribute('data-self') === '1');
            out.querySelector('.row').outerHTML = '<p class="muted small">기록했습니다.</p>' + nextBtn();
          });
        });
      }

      /* 값 계산 채점(브라우저 안의 파이썬 엑셀 계산기) */
      async function run(mode) {
        if (!input.value.trim()) { out.innerHTML = '<div class="result-box warn">수식을 입력하세요. 예: <code>=SUM(B2:B5)</code></div>'; return; }
        if (!EX.py || !EX.py.available()) { textCheck(); return; }
        var r = await EX.withEngine(out, function (st) { return EX.py.call('formula_check', { id: p.id, text: fixText(input.value), reveal: mode !== 'try' }, null, st); });
        if (!r) { textCheck('엑셀 계산기를 불러오지 못해 글자 비교로 채점합니다.'); return; }
        if (r.error && !r.cells) { resetGrid(); out.innerHTML = '<div class="result-box bad">' + esc(r.error) + '</div>'; return; }
        if (mode === 'try') {
          paint(r, false);
          var first = r.cells && r.cells[0];
          out.innerHTML = '<div class="result-box warn"><b>계산 결과</b> ' + (first ? esc(first.addr) + ' = <code>' + esc(first.value) + '</code>' : '') +
            (r.cells && r.cells.length > 1 ? ' · 채우기 범위를 복사해 계산했습니다(칸에 마우스를 올리면 그 칸의 수식).' : '') +
            (r.unknown && r.unknown.length ? '<br>모르는 함수: ' + esc(r.unknown.join(', ')) : '') + '<div class="small muted">기록되지 않습니다. 맞는지 보려면 [채점].</div></div>';
          return;
        }
        paint(r, true);
        if (r.error) { out.innerHTML = '<div class="result-box bad">' + esc(r.error) + '</div>'; return; }   // 순환 참조 등: 기록하지 않음
        record(p.id, !!r.ok);
        var html = '<div class="result-box ' + (r.ok ? 'ok' : 'bad') + '"><b>' + (r.ok ? '정답입니다.' : '다시 생각해 보세요.') + '</b>' +
          (r.notes && r.notes.length ? '<ul style="margin:6px 0 0">' + r.notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>' : '');
        if (!r.ok && r.expected) {
          var bad = r.expected.filter(function (e, i) { return !r.per[i]; }).slice(0, 4);
          if (bad.length) html += '<div class="small" style="margin-top:6px">기대한 값: ' + bad.map(function (e) { return esc(e.addr) + ' = <code>' + esc(e.value) + '</code>'; }).join(', ') + '</div>';
        }
        html += '</div>';
        out.innerHTML = showAnswer(html) + nextBtn();
      }
      var tryBtn = document.getElementById('f-try');
      if (tryBtn) tryBtn.addEventListener('click', function () { run('try'); });
      document.getElementById('f-check').addEventListener('click', function () { run('check'); });
      input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); run('check'); } });
      document.getElementById('f-show').addEventListener('click', function () { out.innerHTML = showAnswer('') + nextBtn(); });
      input.addEventListener('focus', function () { if (EX.py && EX.py.available()) EX.py.ready().catch(function () {}); }, { once: true });
      input.focus();
    }
  }

  function route() {
    main.onclick = null;
    var id = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
    if (id) viewProblem(id); else viewList();
  }
  window.addEventListener('hashchange', route);
  window.__learn = { norm: norm, judge: judge };
  route();
})();
