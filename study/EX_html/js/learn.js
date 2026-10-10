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

  function record(id, ok) { EX.learn.record(id, ok); }
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

  /* ---------- 목록·분류·오답 노트 ---------- */
  var filter = store.get('l.filter', { track: '', q: '' });
  if (typeof filter.track !== 'string') filter = { track: '', q: '' };
  var L = EX.learn;
  function lvDots(n) { n = n || 1; return '<span class="lv" title="난이도">' + new Array(n + 1).join('●') + new Array(4 - n).join('○') + '</span>'; }
  function stIcon(s) {
    if (!s) return '<span class="st new" title="안 푼 문제">·</span>';
    return s.last_ok ? '<span class="st ok" title="맞힘">✓</span>' : '<span class="st bad" title="틀림">✕</span>';
  }
  function trackBadges(p) { return (p.tracks || []).map(function (t) { return '<span class="badge ' + t + '">' + esc(TRACKS[t] || t) + '</span>'; }).join(' '); }
  function problemRow(p, st, stars) {
    return '<li>' + stIcon(st[p.id]) + '<div class="grow"><a class="title" href="#/' + encodeURIComponent(p.id) + '">' + esc(p.title) + '</a>' +
      (stars && stars.indexOf(p.id) >= 0 ? ' <span title="별표">★</span>' : '') +
      '<div class="small muted">' + esc(catName[category(p)] || category(p)) + ' · ' + (p.type === 'formula' ? '수식' : '보기 고르기') + ' ' + lvDots(p.level) + '</div></div>' +
      '<div class="nowrap">' + trackBadges(p) + '</div></li>';
  }
  function trackSelect() {
    return '<select id="f-track" aria-label="학습 범위"><option value="">전체 범위</option>' + Object.keys(TRACKS).map(function (k) {
      return '<option value="' + k + '"' + (filter.track === k ? ' selected' : '') + '>' + esc(TRACKS[k]) + '</option>';
    }).join('') + '</select>';
  }
  function bindTrack(rerender) {
    var sel = document.getElementById('f-track');
    if (sel) sel.addEventListener('change', function (e) { filter.track = e.target.value; store.set('l.filter', filter); rerender(); });
  }
  function goNext(cat) {
    var id = L.nextProblem(cat, filter.track);
    location.hash = id ? '#/' + encodeURIComponent(id) : (cat ? '#/cat/' + cat : '#/');
  }

  function viewList() {
    var st = L.status(L.att());
    function paintCards() {
      var q = (filter.q || '').replace(/\s+/g, '').toUpperCase(), box = document.getElementById('f-body');
      if (q) {
        var items = L.problems('', filter.track).filter(function (p) {
          return (p.title + ' ' + p.prompt + ' ' + (p.functions || []).join(' ') + ' ' + p.id).replace(/\s+/g, '').toUpperCase().indexOf(q) >= 0;
        });
        box.innerHTML = '<section class="card">' + (items.length ? '<ul class="list">' + items.slice(0, 200).map(function (p) { return problemRow(p, st, L.stars()); }).join('') + '</ul>' +
          (items.length > 200 ? '<p class="small muted">앞의 200개만 보입니다. 검색어를 더 구체적으로 입력하세요.</p>' : '') : '<div class="empty">검색 결과가 없습니다.</div>') + '</section>';
        return;
      }
      var h = '';
      [['formula', '함수'], ['feature', '기능']].forEach(function (kt) {
        var cards = cats.filter(function (c) { return c[2] === kt[0]; }).map(function (c) {
          var items = L.problems(c[0], filter.track);
          if (!items.length) return '';
          var solved = items.filter(function (p) { return st[p.id] && st[p.id].solved; }).length;
          var lv = [1, 2, 3].map(function (n) { return items.filter(function (p) { return p.level === n; }).length; });
          return '<a class="card cat-card" href="#/cat/' + c[0] + '"><div class="top-line"><h3 style="margin:0">' + esc(c[1]) + '</h3><span class="small muted">' + solved + '/' + items.length + '</span></div>' +
            '<p>' + esc(c[3]) + '</p><div class="meter" aria-hidden="true"><i style="width:' + Math.round(100 * solved / items.length) + '%"></i></div>' +
            '<div class="small muted" style="margin-top:6px">기초 ' + lv[0] + ' · 응용 ' + lv[1] + ' · 심화 ' + lv[2] + '</div></a>';
        }).join('');
        h += '<h2 style="margin-top:18px">' + kt[1] + '</h2><div class="grid g3">' + (cards || '<div class="empty">이 범위에는 문제가 없습니다.</div>') + '</div>';
      });
      box.innerHTML = h;
    }
    var h = '<div class="page-head"><div><h1>문제 풀기</h1><p>함수 문제는 시트에 직접 수식을 넣어 계산 결과로 채점하고, 기능 문제(피벗·서식·데이터 도구 등)는 보기를 고릅니다.</p></div>' +
      '<div class="row">' + trackSelect() + '<input id="f-q" type="search" placeholder="문제·함수 검색" value="' + esc(filter.q || '') + '" aria-label="문제 검색">' +
      '<a class="btn" href="#/review">오답 노트</a><button class="btn primary" id="f-next" type="button">안 푼 문제부터 →</button></div></div><div id="f-body"></div>';
    main.innerHTML = h;
    window.scrollTo(0, 0);
    paintCards();
    bindTrack(viewList);
    document.getElementById('f-next').addEventListener('click', function () { goNext(''); });
    document.getElementById('f-q').addEventListener('input', function (e) { filter.q = e.target.value; store.set('l.filter', filter); paintCards(); });
  }

  function viewCat(cat, level) {
    if (!catName[cat]) { main.innerHTML = '<div class="card empty">없는 분류입니다.<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    var items = L.problems(cat, filter.track).filter(function (p) { return !level || p.level === level; });
    var st = L.status(L.att()), stars = L.stars();
    var h = '<div class="page-head"><div><div class="crumb"><a href="#/">문제 풀기</a> ›</div><h1>' + esc(catName[cat]) + '</h1></div><div class="row">' + trackSelect() +
      [[0, '전체'], [1, '기초'], [2, '응용'], [3, '심화']].map(function (l) {
        return '<a class="btn sm' + ((level || 0) === l[0] ? ' primary' : '') + '" href="#/cat/' + cat + (l[0] ? '?level=' + l[0] : '') + '">' + l[1] + '</a>';
      }).join('') + '<button class="btn sm" id="f-next" type="button">이 분류 이어서 →</button></div></div>' +
      '<section class="card">' + (items.length ? '<ul class="list">' + items.map(function (p) { return problemRow(p, st, stars); }).join('') + '</ul>'
        : '<div class="empty">조건에 맞는 문제가 없습니다. 위쪽 학습 범위를 \'전체 범위\'로 바꿔 보세요.</div>') + '</section>';
    main.innerHTML = h;
    window.scrollTo(0, 0);
    bindTrack(function () { viewCat(cat, level); });
    document.getElementById('f-next').addEventListener('click', function () { goNext(cat); });
  }

  function viewReview() {
    var st = L.status(L.att()), stars = L.stars(), items = L.problems('', filter.track);
    var wrong = items.filter(function (p) { return st[p.id] && !st[p.id].last_ok; });
    var often = items.filter(function (p) { return st[p.id] && st[p.id].wrong >= 2; }).sort(function (a, b) { return st[b.id].wrong - st[a.id].wrong; });
    var starred = items.filter(function (p) { return stars.indexOf(p.id) >= 0; });
    var h = '<div class="page-head"><div><div class="crumb"><a href="#/">문제 풀기</a> ›</div><h1>오답 노트</h1><p>마지막에 틀린 문제, 두 번 이상 틀린 문제, 별표한 문제를 모았습니다.</p></div><div class="row">' + trackSelect() + '</div></div><div class="grid g3">';
    [['마지막에 틀린 문제', wrong, '없습니다. 잘하고 있어요.'], ['자주 틀린 문제 (2번 이상)', often, '아직 없습니다.'], ['별표한 문제', starred, '문제 제목 옆 ☆ 를 누르면 여기에 모입니다.']].forEach(function (g) {
      h += '<section class="card"><h2>' + g[0] + ' <span class="small muted">' + g[1].length + '</span></h2>' +
        (g[1].length ? '<ul class="list">' + g[1].map(function (p) { return problemRow(p, st, stars); }).join('') + '</ul>' : '<div class="empty">' + g[2] + '</div>') + '</section>';
    });
    main.innerHTML = h + '</div>';
    window.scrollTo(0, 0);
    bindTrack(viewReview);
  }

  /* ---------- 문제 하나 ---------- */
  function neighbors(p) {
    var ids = L.problems(category(p), filter.track).map(function (x) { return x.id; }), i = ids.indexOf(p.id);
    return i < 0 ? [null, null] : [i ? ids[i - 1] : null, i + 1 < ids.length ? ids[i + 1] : null];
  }
  function viewProblem(id) {
    var p = byId[id];
    if (!p) { main.innerHTML = '<div class="card empty">문제를 찾을 수 없습니다.<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    var nb = neighbors(p), prv = nb[0], nxt = nb[1];
    var starred = EX.learn.stars().indexOf(id) >= 0;
    var h = '<div class="crumb"><a href="#/">문제 풀기</a> › <a href="#/cat/' + category(p) + '">' + esc(catName[category(p)] || category(p)) + '</a></div>' +
      '<div class="page-head"><div><h1>' + esc(p.title) + ' <button class="star-btn" id="p-star" type="button" aria-pressed="' + starred + '" title="별표(오답 노트에 모읍니다)">' + (starred ? '★' : '☆') + '</button></h1><p class="muted small">' + esc(p.id) + ' · ' + esc(catName[category(p)] || category(p)) + ' · ' +
      (p.tracks || []).map(function (t) { return esc(TRACKS[t] || t); }).join(' / ') + ' · ' + lvDots(p.level) + '</p></div></div>';
    h += '<article class="q"><p class="qtext">' + esc(p.prompt) + '</p>';
    if (p.type === 'formula') {
      h += '<p class="small muted" style="margin:8px 0 0">입력할 셀: <b class="mono">' + esc(p.target || '') + '</b>' + (p.fill ? ' → <b class="mono">' + esc(p.fill) + '</b> 까지 채우기(첫 셀 수식을 복사해 모든 칸을 채점)' : '') + '</p>';
    }
    if (p.hint) h += '<details style="margin-top:10px"><summary>힌트</summary><p>' + esc(p.hint) + '</p></details>';
    h += sheetHtml(p);
    if (p.type === 'formula' && p.sheet) h += '<div class="legend"><span><i class="sw-target"></i>수식을 넣을 칸</span><span><i class="sw-ok"></i>맞은 칸</span><span><i class="sw-bad"></i>다른 칸</span></div>';
    if (p.type === 'choice') {
      h += '<div class="opts">' + p.options.map(function (o, i) {
        return '<button type="button" class="opt" data-i="' + i + '"><span class="n">' + (i + 1) + '</span><span>' + esc(o) + '</span></button>';
      }).join('') + '</div>';
    } else {
      var useEngine = EX.py && EX.py.available();
      h += '<div class="fbar"><div class="namebox" id="namebox">' + esc(p.target || '') + '</div><span class="fx">fx</span><input class="formula" id="f-input" type="text" autocomplete="off" spellcheck="false" placeholder="' + esc((p.target || '') + ' 에 입력할 수식 (예: =SUM(A1:A5))') + '" aria-label="수식 입력"></div>' +
        '<div class="row">' + (useEngine ? '<button class="btn" id="f-try" type="button">계산해 보기</button>' : '') + '<button class="btn primary" id="f-check" type="button">채점</button><button class="btn" id="f-show" type="button">정답 보기</button><button class="btn ghost" id="f-reset" type="button">시트 원래대로</button></div>' +
        '<div class="tip">셀을 누르거나 끌면 수식에 그 주소·범위가 들어갑니다. <code>=av</code> 를 치면 AV 로 시작하는 함수가 뜹니다(↑↓ 고르고 Tab). F4 로 $ 고정을 바꾸고, Ctrl+Enter 는 계산해 보기입니다. 배열 수식은 <code>{=…}</code> 처럼 입력해도 됩니다.</div>' +
        '<p class="muted small" style="margin-top:8px">' + (useEngine
          ? '브라우저 안의 엑셀 계산기로 수식을 채우기 범위 전체에 계산해 값을 비교합니다(처음 한 번 엔진을 불러오느라 몇 초~10초 걸립니다). 같은 값을 내는 다른 수식도 정답으로 봅니다.'
          : '엑셀 계산기를 쓸 수 없어, 정답 수식과 글자(공백·대소문자·$ 무시)로 비교합니다. 값이 같은 다른 수식은 스스로 판단합니다.') + '</p>';
    }
    h += '<div id="p-out"></div></article>' +
      '<div class="pager">' + (prv ? '<a class="btn" href="#/' + encodeURIComponent(prv) + '">← 이전 문제</a>' : '<span></span>') +
      (nxt ? '<a class="btn primary" href="#/' + encodeURIComponent(nxt) + '">다음 문제 →</a>' : '<a class="btn primary" href="#/cat/' + category(p) + '">분류 목록으로</a>') + '</div>';
    main.innerHTML = h;
    window.scrollTo(0, 0);
    var out = document.getElementById('p-out');
    document.getElementById('p-star').addEventListener('click', function (e) {
      var on = EX.learn.toggleStar(id);
      e.currentTarget.textContent = on ? '★' : '☆';
      e.currentTarget.setAttribute('aria-pressed', on);
    });
    function nextBtn() {
      return '<div class="row" style="margin-top:10px">' + (nxt ? '<a class="btn primary" href="#/' + encodeURIComponent(nxt) + '">다음 문제</a>' : '') + '<a class="btn" href="#/cat/' + category(p) + '">분류 목록으로</a></div>';
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
        Object.keys(tds).forEach(function (a) { var td = tds[a]; td.classList.remove('okc', 'badc', 'spill', 'picked', 'sel'); td.textContent = td.getAttribute('data-orig'); td.removeAttribute('title'); });
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
      document.getElementById('f-reset').addEventListener('click', function () { resetGrid(); out.innerHTML = ''; });
      /* ---------- 셀 고르기: 클릭·끌기(드래그)·Shift+클릭 → 수식에 주소 넣기 ---------- */
      var sheet = main.querySelector('table.sheet'), namebox = document.getElementById('namebox');
      var anchor = null, lastIns = null, dragging = false, pointing = false;
      function parseA(a) {
        var m = /^([A-Z]+)(\d+)$/.exec(a), c = 0;
        for (var i = 0; i < m[1].length; i++) c = c * 26 + m[1].charCodeAt(i) - 64;
        return [parseInt(m[2], 10), c];
      }
      function rangeText(a, b) {
        if (a === b) return a;
        var p1 = parseA(a), q = parseA(b);
        return colName(Math.min(p1[1], q[1]) - 1) + Math.min(p1[0], q[0]) + ':' + colName(Math.max(p1[1], q[1]) - 1) + Math.max(p1[0], q[0]);
      }
      function highlight(a, b) {
        main.querySelectorAll('table.sheet td.picked, table.sheet td.sel').forEach(function (x) { x.classList.remove('picked', 'sel'); });
        var p1 = parseA(a), q = parseA(b);
        for (var r = Math.min(p1[0], q[0]); r <= Math.max(p1[0], q[0]); r++) {
          for (var c = Math.min(p1[1], q[1]); c <= Math.max(p1[1], q[1]); c++) { var td = tds[colName(c - 1) + r]; if (td) td.classList.add('picked'); }
        }
        if (tds[a]) tds[a].classList.add('sel');
      }
      function clearPicked() { main.querySelectorAll('table.sheet td.picked').forEach(function (x) { x.classList.remove('picked'); }); }
      /* 이 입력창은 늘 수식 칸이다(채점할 때 = 를 붙여 줌) */
      function ensureEq() {
        if (input.value && !/^[=+]|^\{=/.test(input.value)) {
          var a = input.selectionStart, b = input.selectionEnd;
          input.value = '=' + input.value;
          input.setSelectionRange(a + 1, b + 1);
          if (lastIns) { lastIns.start++; lastIns.end++; }
          acStart++;
        }
      }
      function canPoint() {
        if (/^['"]/.test(input.value)) return false;
        var pos = input.selectionStart;
        if (lastIns && pos === lastIns.end && input.selectionEnd === pos) return true;
        var before = input.value.slice(0, pos).replace(/\s+$/, '');
        return before === '' || /[=(,+\-*/^&<>:;{]$/.test(before);
      }
      function putRef(text) {
        if (!input.value) { input.value = '='; input.setSelectionRange(1, 1); }
        if (lastIns && input.selectionStart === lastIns.end) input.setRangeText(text, lastIns.start, lastIns.end, 'end');
        else input.setRangeText(text, input.selectionStart, input.selectionEnd, 'end');
        lastIns = { start: input.selectionEnd - text.length, end: input.selectionEnd };
      }
      if (sheet) {
        sheet.addEventListener('mousedown', function (ev) {
          var td = ev.target.closest('td[data-a]');
          if (!td || ev.button !== 0) return;
          ev.preventDefault();
          var a = td.getAttribute('data-a'), editing = document.activeElement === input;
          if (ev.shiftKey && anchor) {
            highlight(anchor, a);
            if (namebox) namebox.textContent = rangeText(anchor, a);
            if (editing && pointing) putRef(rangeText(anchor, a));
            return;
          }
          anchor = a; dragging = true;
          highlight(a, a);
          if (namebox) namebox.textContent = a;
          pointing = editing && canPoint();
          if (pointing) { putRef(a); hideAc(); }
        });
        sheet.addEventListener('mouseover', function (ev) {
          if (!dragging) return;
          var td = ev.target.closest('td[data-a]');
          if (!td) return;
          var a = td.getAttribute('data-a'), ref = rangeText(anchor, a);
          highlight(anchor, a);
          if (namebox) namebox.textContent = ref;
          if (pointing) putRef(ref);
        });
        document.addEventListener('mouseup', function () {
          if (!dragging) return;
          dragging = false;
          if (pointing) { input.focus(); updateTips(); }
        });
      }

      /* ---------- 함수 자동 완성(=s → SUM, SUMIF…) + 인수 도움말 ---------- */
      var FUNCS = [], seenF = {};
      F.items.forEach(function (f) {
        var desc = String(f.desc || '').split('.')[0].slice(0, 60);
        [f.name].concat(f.aliases || []).forEach(function (n) {
          var key = n.toUpperCase();
          if (seenF[key]) return;
          seenF[key] = 1;
          var syn = f.syntax || key + '()';
          if (key !== f.name.toUpperCase() && syn.toUpperCase().indexOf(f.name.toUpperCase()) === 0) syn = key + syn.slice(f.name.length);
          FUNCS.push([key, syn, desc]);
        });
      });
      (F.engine || []).forEach(function (n) { if (!seenF[n]) { seenF[n] = 1; FUNCS.push([n, n + '()', '']); } });
      FUNCS.sort(function (a, b) { return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0; });
      var wrap = input.closest('.fbar');
      var ac = document.createElement('ul');
      ac.className = 'ac hidden'; ac.id = 'ac-list'; ac.setAttribute('role', 'listbox');
      var argtip = document.createElement('div');
      argtip.className = 'argtip hidden';
      wrap.appendChild(ac); wrap.appendChild(argtip);
      input.setAttribute('aria-autocomplete', 'list');
      input.setAttribute('aria-controls', 'ac-list');
      var acItems = [], acIndex = 0, acStart = 0;
      function inStr(t) { return (t.match(/"/g) || []).length % 2 === 1; }
      function tokenBeforeCaret() {
        var pos = input.selectionStart, before = input.value.slice(0, pos);
        if (/^['"]/.test(input.value) || inStr(before)) return null;
        var m = /(^|[=(,+\-*/^&<>:;{\s])([A-Za-z][A-Za-z0-9._]*)$/.exec(before);
        if (!m || /^\$?[A-Za-z]{1,3}\$?\d+$/.test(m[2])) return null;
        return { text: m[2], start: pos - m[2].length };
      }
      function hideAc() { ac.classList.add('hidden'); ac.innerHTML = ''; acItems = []; }
      function renderAc() {
        ac.innerHTML = acItems.map(function (f, i) {
          return '<li role="option" data-i="' + i + '"' + (i === acIndex ? ' class="on" aria-selected="true"' : '') + '><b>' + esc(f[0]) + '</b><span>' + esc(f[2] || '') + '</span></li>';
        }).join('');
        ac.classList.toggle('hidden', !acItems.length);
        var on = ac.querySelector('li.on');
        if (on) on.scrollIntoView({ block: 'nearest' });
        if (acItems.length) { argtip.innerHTML = '<span class="mono">' + esc(acItems[acIndex][1]) + '</span>'; argtip.classList.remove('hidden'); }
      }
      function updateAc() {
        var t = tokenBeforeCaret();
        if (!t) { hideAc(); return false; }
        var q = t.text.toUpperCase();
        acItems = FUNCS.filter(function (f) { return f[0].indexOf(q) === 0; }).slice(0, 12);
        if (acItems.length === 1 && acItems[0][0] === q) acItems = [];
        acIndex = 0; acStart = t.start;
        renderAc();
        return acItems.length > 0;
      }
      function pickAc(i) {
        var f = acItems[i];
        if (!f) return;
        ensureEq();
        var pos = input.selectionStart, hasParen = input.value.charAt(pos) === '(';
        input.setRangeText(f[0] + (hasParen ? '' : '('), acStart, pos, 'end');
        if (hasParen) input.setSelectionRange(input.selectionEnd + 1, input.selectionEnd + 1);
        hideAc(); lastIns = null; input.focus(); updateTips();
      }
      function currentCall() {
        var before = input.value.slice(0, input.selectionStart), depth = 0, args = 0, q = false;
        for (var i = before.length - 1; i >= 0; i--) {
          var ch = before.charAt(i);
          if (ch === '"') q = !q;
          if (q) continue;
          if (ch === ')') depth++;
          else if (ch === '(') {
            if (depth === 0) { var m = /([A-Za-z][A-Za-z0-9._]*)$/.exec(before.slice(0, i)); return m ? { name: m[1].toUpperCase(), arg: args } : null; }
            depth--;
          } else if (ch === ',' && depth === 0) args++;
        }
        return null;
      }
      function updateTips() {
        if (updateAc()) return;
        var call = currentCall();
        var f = call && FUNCS.filter(function (x) { return x[0] === call.name; })[0];
        if (!f) { argtip.classList.add('hidden'); return; }
        var m = /^([^(]*)\((.*)\)\s*$/.exec(f[1]), html;
        if (m && m[2]) {
          var parts = m[2].split(/,\s*/);
          html = esc(m[1]) + '(' + parts.map(function (p, i) { return i === Math.min(call.arg, parts.length - 1) ? '<b>' + esc(p) + '</b>' : esc(p); }).join(', ') + ')';
        } else html = esc(f[1]);
        argtip.innerHTML = '<span class="mono">' + html + '</span>';
        argtip.classList.remove('hidden');
      }
      input.addEventListener('input', function () { lastIns = null; clearPicked(); updateTips(); });
      input.addEventListener('click', function () { lastIns = null; updateTips(); });
      input.addEventListener('blur', function () { setTimeout(function () { if (document.activeElement !== input) { hideAc(); argtip.classList.add('hidden'); } }, 150); });
      ac.addEventListener('mousedown', function (ev) {
        var li = ev.target.closest('li[data-i]');
        if (!li) return;
        ev.preventDefault();
        pickAc(parseInt(li.getAttribute('data-i'), 10));
      });
      input.addEventListener('keydown', function (e) {
        if (e.isComposing || e.keyCode === 229) return;
        var open = acItems.length > 0;
        if (open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) { e.preventDefault(); acIndex = (acIndex + (e.key === 'ArrowDown' ? 1 : -1) + acItems.length) % acItems.length; renderAc(); return; }
        if (open && (e.key === 'Tab' || e.key === 'Enter')) { e.preventDefault(); pickAc(acIndex); return; }
        if (open && e.key === 'Escape') { e.preventDefault(); hideAc(); argtip.classList.add('hidden'); return; }
        if (e.key === 'F4') {
          /* F4: 커서가 있는 셀 주소의 $ 를 A1 → $A$1 → A$1 → $A1 순서로 */
          e.preventDefault();
          var v = input.value, pos = input.selectionStart;
          var re = /(\$?)([A-Za-z]{1,3})(\$?)(\d+)(?::(\$?)([A-Za-z]{1,3})(\$?)(\d+))?/g, m;
          while ((m = re.exec(v))) {
            var st = m.index, en = st + m[0].length;
            if (pos >= st && pos <= en) {
              var next = { 0: 3, 3: 1, 1: 2, 2: 0 }[(m[1] ? 2 : 0) + (m[3] ? 1 : 0)];
              var one = function (col, row) { return (next & 2 ? '$' : '') + col + (next & 1 ? '$' : '') + row; };
              var rp = one(m[2], m[4]) + (m[6] ? ':' + one(m[6], m[8]) : '');
              input.value = v.slice(0, st) + rp + v.slice(en);
              input.setSelectionRange(st + rp.length, st + rp.length);
              break;
            }
          }
          lastIns = null;
        } else if (e.key === 'Enter') { e.preventDefault(); hideAc(); run(e.ctrlKey ? 'try' : 'check'); }
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight' || e.key === 'Home' || e.key === 'End') { lastIns = null; setTimeout(updateTips, 0); }
      });
      document.getElementById('f-show').addEventListener('click', function () { out.innerHTML = showAnswer('') + nextBtn(); });
      input.addEventListener('focus', function () { if (EX.py && EX.py.available()) EX.py.ready().catch(function () {}); }, { once: true });
      input.focus();
    }
  }

  function route() {
    main.onclick = null;
    var raw = location.hash.replace(/^#\/?/, ''), qs = raw.split('?'), m;
    var id = decodeURIComponent(qs[0]);
    if (id === 'review') viewReview();
    else if ((m = /^cat\/([\w-]+)$/.exec(id))) viewCat(m[1], parseInt((/level=(\d)/.exec(qs[1] || '') || [])[1], 10) || 0);
    else if (id) viewProblem(id);
    else viewList();
  }
  window.addEventListener('hashchange', route);
  window.__learn = { norm: norm, judge: judge };
  route();
})();
