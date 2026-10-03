/* 엑셀 연습장 화면 동작 — 인라인 스크립트 없이(CSP) data- 속성으로 연결 */
(function () {
  'use strict';
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) throw new Error('서버 응답 ' + r.status);
      return r.json();
    });
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* 테마 */
  var root = document.documentElement;
  try { var saved = localStorage.getItem('ex-theme'); if (saved) root.setAttribute('data-theme', saved); } catch (e) { /* 저장소 없음 */ }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-theme-toggle]');
    if (!b) return;
    var dark = root.getAttribute('data-theme') === 'dark' ||
      (!root.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
    var next = dark ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('ex-theme', next); } catch (e) { /* 무시 */ }
  });

  /* 선택하면 바로 제출 */
  document.addEventListener('change', function (ev) {
    var t = ev.target;
    if (t.matches('[data-autosubmit]')) t.form.submit();
  });
  /* 정답 파일 같은 링크는 한 번 묻고 연다 */
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest('[data-confirm-link]');
    if (a && !window.confirm('정답 파일을 받으면 답이 모두 보입니다. 받을까요?')) ev.preventDefault();
  });
  /* 확인 후 제출 */
  document.addEventListener('submit', function (ev) {
    var msg = ev.target.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) ev.preventDefault();
  });

  /* 함수 사전 검색 */
  var fsearch = document.getElementById('fn-search');
  if (fsearch) {
    fsearch.addEventListener('input', function () {
      var q = fsearch.value.trim().toUpperCase();
      document.querySelectorAll('[data-fn]').forEach(function (c) {
        c.classList.toggle('hidden', q && c.getAttribute('data-fn').toUpperCase().indexOf(q) < 0);
      });
      document.querySelectorAll('[data-fn-group]').forEach(function (g) {
        g.classList.toggle('hidden', !g.querySelector('[data-fn]:not(.hidden)'));
      });
    });
  }

  /* 파일 끌어 놓기 */
  document.querySelectorAll('[data-drop]').forEach(function (zone) {
    var input = zone.querySelector('input[type=file]');
    var name = zone.querySelector('[data-file-name]');
    ['dragenter', 'dragover'].forEach(function (e) {
      zone.addEventListener(e, function (ev) { ev.preventDefault(); zone.classList.add('over'); });
    });
    ['dragleave', 'drop'].forEach(function (e) {
      zone.addEventListener(e, function (ev) { ev.preventDefault(); zone.classList.remove('over'); });
    });
    zone.addEventListener('drop', function (ev) {
      if (ev.dataTransfer.files.length) {
        input.files = ev.dataTransfer.files;
        if (name) name.textContent = input.files[0].name;
      }
    });
    input.addEventListener('change', function () { if (name && input.files.length) name.textContent = input.files[0].name; });
  });
  /* 두 번 제출 방지 */
  document.querySelectorAll('form[data-once]').forEach(function (f) {
    f.addEventListener('submit', function () {
      var b = f.querySelector('button[type=submit]');
      if (b) { b.disabled = true; b.textContent = '처리 중…'; }
    });
  });

  /* 모의고사 시간: [시험 시작]을 누른 시각을 기억(새로 고쳐도 이어짐), 제출할 때 걸린 시간을 함께 보냄 */
  document.querySelectorAll('[data-exam-timer]').forEach(function (box) {
    var minutes = parseInt(box.getAttribute('data-exam-timer'), 10);
    var key = 'ex-exam-start:' + box.getAttribute('data-exam-id');
    var text = box.querySelector('[data-timer-text]');
    var wrap = box.querySelector('.timer');
    var secondsInput = box.querySelector('[data-timer-seconds]');
    var startBtn = box.querySelector('[data-timer-start]');
    function getStart() { try { return parseInt(localStorage.getItem(key) || '0', 10); } catch (e) { return 0; } }
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function tick() {
      var start = getStart();
      if (!start) { text.textContent = minutes + ':00'; startBtn.disabled = false; wrap.classList.remove('over'); return; }
      startBtn.disabled = true;
      var used = Math.floor((Date.now() - start) / 1000);
      var left = minutes * 60 - used;
      wrap.classList.toggle('over', left < 0);
      var a = Math.abs(left);
      text.textContent = (left < 0 ? '시간 초과 +' : '') + Math.floor(a / 60) + ':' + pad(a % 60);
      if (secondsInput) secondsInput.value = used;
    }
    startBtn.addEventListener('click', function () {
      try { localStorage.setItem(key, String(Date.now())); } catch (e) { /* 저장 불가면 이 화면에서만 */ }
      tick();
    });
    box.querySelector('[data-timer-reset]').addEventListener('click', function () {
      try { localStorage.removeItem(key); } catch (e) { /* 무시 */ }
      tick();
    });
    box.querySelector('form').addEventListener('submit', function () {
      tick();
      try { localStorage.removeItem(key); } catch (e) { /* 무시 */ }
    });
    tick();
    setInterval(tick, 1000);
  });

  /* 별표 */
  document.querySelectorAll('[data-star]').forEach(function (b) {
    b.addEventListener('click', function () {
      post('/api/star', { pid: b.getAttribute('data-star') }).then(function (r) {
        b.classList.toggle('on', r.on);
        b.textContent = r.on ? '★' : '☆';
        b.setAttribute('aria-pressed', r.on ? 'true' : 'false');
      });
    });
  });

  /* ---------------- 문제 풀이 ---------------- */
  var pdata = document.getElementById('problem-data');
  if (!pdata) return;
  var P = JSON.parse(pdata.textContent);
  var resultBox = document.getElementById('result');

  function showResult(kind, html) {
    resultBox.className = 'result show ' + kind;
    resultBox.innerHTML = html;
  }

  if (P.type === 'choice') {
    var form = document.getElementById('choice-form');
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var picked = form.querySelector('input[name=opt]:checked');
      if (!picked) { showResult('info', '보기를 고르세요.'); return; }
      post('/api/check', { pid: P.pid, answer: picked.value }).then(function (r) {
        form.querySelectorAll('.choice').forEach(function (c, i) {
          c.classList.remove('right', 'wrong');
          if (i === r.answer) c.classList.add('right');
          else if (String(i) === picked.value) c.classList.add('wrong');
        });
        showResult(r.ok ? 'ok' : 'bad', '<h3>' + (r.ok ? '정답입니다' : '아쉬워요 — 정답은 ' + (r.answer + 1) + '번') + '</h3>' +
          '<div class="explain">' + esc(r.explain) + '</div>');
        var nx = document.getElementById('next-link');
        if (nx) nx.focus();
      }).catch(function (e) { showResult('bad', esc(e.message)); });
    });
    return;
  }

  var input = document.getElementById('formula');
  var namebox = document.getElementById('namebox');
  var sheet = document.getElementById('sheet');
  var cells = {};
  sheet.querySelectorAll('td[data-a]').forEach(function (td) {
    cells[td.getAttribute('data-a')] = td;
    td.setAttribute('data-orig', td.textContent);
  });

  function resetGrid() {
    Object.keys(cells).forEach(function (a) {
      var td = cells[a];
      td.classList.remove('ok', 'bad', 'spill', 'picked');
      td.textContent = td.getAttribute('data-orig');
      td.removeAttribute('title');
    });
  }

  function paint(res, graded) {
    resetGrid();
    (res.cells || []).forEach(function (c, i) {
      var td = cells[c.addr];
      if (!td) return;
      td.textContent = c.value;
      if (c.formula) td.title = c.formula;
      if (c.spill) td.classList.add('spill');
      else if (graded && res.per) td.classList.add(res.per[i] ? 'ok' : 'bad');
    });
  }

  /* ---------- 셀 고르기: 클릭·끌기(드래그)·Shift+클릭 → 수식에 주소 넣기 ---------- */
  function parseA(a) {
    var m = /^([A-Z]+)(\d+)$/.exec(a);
    var c = 0;
    for (var i = 0; i < m[1].length; i++) c = c * 26 + m[1].charCodeAt(i) - 64;
    return [parseInt(m[2], 10), c];
  }
  function colName(n) { var s = ''; while (n) { var r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); } return s; }
  function rangeText(a, b) {
    if (a === b) return a;
    var p = parseA(a), q = parseA(b);
    return colName(Math.min(p[1], q[1])) + Math.min(p[0], q[0]) + ':' + colName(Math.max(p[1], q[1])) + Math.max(p[0], q[0]);
  }
  function highlight(a, b) {
    sheet.querySelectorAll('td.picked, td.sel').forEach(function (x) { x.classList.remove('picked', 'sel'); });
    var p = parseA(a), q = parseA(b);
    for (var r = Math.min(p[0], q[0]); r <= Math.max(p[0], q[0]); r++) {
      for (var c = Math.min(p[1], q[1]); c <= Math.max(p[1], q[1]); c++) {
        var td = cells[colName(c) + r];
        if (td) td.classList.add('picked');
      }
    }
    if (cells[a]) cells[a].classList.add('sel');
  }
  function clearPicked() {
    sheet.querySelectorAll('td.picked').forEach(function (x) { x.classList.remove('picked'); });
  }
  /* 이 입력창은 늘 수식 칸이다(채점할 때 = 를 붙여 줌) — = 없이 av 를 쳐도 자동 완성 */
  function formulaMode() { return !/^['"]/.test(input.value); }
  function ensureEq() {
    if (input.value && !/^[=+]|^\{=/.test(input.value)) {
      var a = input.selectionStart, b = input.selectionEnd;
      input.value = '=' + input.value;
      input.setSelectionRange(a + 1, b + 1);
      if (lastIns) { lastIns.start++; lastIns.end++; }
      if (typeof acStart === 'number') acStart++;
    }
  }
  /* 엑셀처럼: 연산자·( · , · = 뒤이거나, 방금 넣은 주소 바로 뒤면 주소를 넣는다 */
  function canPoint() {
    if (!formulaMode()) return false;
    var pos = input.selectionStart;
    if (lastIns && pos === lastIns.end && input.selectionEnd === pos) return true;
    var before = input.value.slice(0, pos).replace(/\s+$/, '');
    return before === '' || /[=(,+\-*/^&<>:;{]$/.test(before);
  }

  var anchor = null, lastIns = null, dragging = false, pointing = false;
  function putRef(text) {
    if (!input.value) { input.value = '='; input.setSelectionRange(1, 1); }
    if (lastIns && input.selectionStart === lastIns.end) {
      input.setRangeText(text, lastIns.start, lastIns.end, 'end');
    } else {
      input.setRangeText(text, input.selectionStart, input.selectionEnd, 'end');
    }
    lastIns = { start: input.selectionEnd - text.length, end: input.selectionEnd };
  }
  sheet.addEventListener('mousedown', function (ev) {
    var td = ev.target.closest('td[data-a]');
    if (!td || ev.button !== 0) return;
    ev.preventDefault();                       // 글자 선택·포커스 이동 막기
    var a = td.getAttribute('data-a');
    var editing = document.activeElement === input;
    if (ev.shiftKey && anchor) {
      highlight(anchor, a);
      namebox.textContent = rangeText(anchor, a);
      if (editing && pointing) putRef(rangeText(anchor, a));
      return;
    }
    anchor = a;
    dragging = true;
    highlight(a, a);
    namebox.textContent = a;
    pointing = editing && canPoint();
    if (pointing) { putRef(a); hideAc(); }
  });
  sheet.addEventListener('mouseover', function (ev) {
    if (!dragging) return;
    var td = ev.target.closest('td[data-a]');
    if (!td) return;
    var a = td.getAttribute('data-a');
    highlight(anchor, a);
    var ref = rangeText(anchor, a);
    namebox.textContent = ref;
    if (pointing) putRef(ref);
  });
  document.addEventListener('mouseup', function () {
    if (!dragging) return;
    dragging = false;
    if (pointing) { input.focus(); updateTips(); }
  });

  /* ---------- 함수 자동 완성(엑셀처럼 =av → AVERAGE…) + 인수 도움말 ---------- */
  var FUNCS = P.funcs || [];
  var wrap = input.closest('.fbar');
  var ac = document.createElement('ul');
  ac.className = 'ac hidden';
  ac.id = 'ac-list';
  ac.setAttribute('role', 'listbox');
  var argtip = document.createElement('div');
  argtip.className = 'argtip hidden';
  wrap.appendChild(ac);
  wrap.appendChild(argtip);
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-controls', 'ac-list');
  var acItems = [], acIndex = 0, acStart = 0;

  function insideString(text) { return (text.match(/"/g) || []).length % 2 === 1; }
  function tokenBeforeCaret() {
    var pos = input.selectionStart;
    var before = input.value.slice(0, pos);
    if (!formulaMode() || insideString(before)) return null;
    var m = /(^|[=(,+\-*/^&<>:;{\s])([A-Za-z][A-Za-z0-9._]*)$/.exec(before);
    if (!m) return null;
    var tok = m[2];
    if (/^\$?[A-Za-z]{1,3}\$?\d+$/.test(tok)) return null;  // 셀 주소
    return { text: tok, start: pos - tok.length };
  }
  function hideAc() { ac.classList.add('hidden'); ac.innerHTML = ''; acItems = []; }
  function renderAc() {
    ac.innerHTML = acItems.map(function (f, i) {
      return '<li role="option" data-i="' + i + '"' + (i === acIndex ? ' class="on" aria-selected="true"' : '') +
        '><b>' + esc(f[0]) + '</b><span>' + esc(f[2] || '') + '</span></li>';
    }).join('');
    ac.classList.toggle('hidden', !acItems.length);
    var on = ac.querySelector('li.on');
    if (on) on.scrollIntoView({ block: 'nearest' });
    if (acItems.length) {
      argtip.innerHTML = '<span class="mono">' + esc(acItems[acIndex][1]) + '</span>';
      argtip.classList.remove('hidden');
    }
  }
  function updateAc() {
    var t = tokenBeforeCaret();
    if (!t) { hideAc(); return false; }
    var q = t.text.toUpperCase();
    acItems = FUNCS.filter(function (f) { return f[0].indexOf(q) === 0; }).slice(0, 12);
    if (acItems.length === 1 && acItems[0][0] === q) acItems = [];  // 이미 다 입력함
    acIndex = 0;
    acStart = t.start;
    renderAc();
    return acItems.length > 0;
  }
  function pickAc(i) {
    var f = acItems[i];
    if (!f) return;
    ensureEq();
    var pos = input.selectionStart;
    var hasParen = input.value.charAt(pos) === '(';
    input.setRangeText(f[0] + (hasParen ? '' : '('), acStart, pos, 'end');
    if (hasParen) input.setSelectionRange(input.selectionEnd + 1, input.selectionEnd + 1);
    hideAc();
    lastIns = null;
    input.focus();
    updateTips();
  }
  /* 커서가 들어 있는 함수와 몇 번째 인수인지 → 형식에서 그 인수를 굵게 */
  function currentCall() {
    var before = input.value.slice(0, input.selectionStart);
    var depth = 0, args = 0, inStr = false;
    for (var i = before.length - 1; i >= 0; i--) {
      var ch = before.charAt(i);
      if (ch === '"') inStr = !inStr;
      if (inStr) continue;
      if (ch === ')') depth++;
      else if (ch === '(') {
        if (depth === 0) {
          var m = /([A-Za-z][A-Za-z0-9._]*)$/.exec(before.slice(0, i));
          return m ? { name: m[1].toUpperCase(), arg: args } : null;
        }
        depth--;
      } else if (ch === ',' && depth === 0) args++;
    }
    return null;
  }
  function updateTips() {
    if (updateAc()) return;
    var call = formulaMode() ? currentCall() : null;
    var f = call && FUNCS.filter(function (x) { return x[0] === call.name; })[0];
    if (!f) { argtip.classList.add('hidden'); return; }
    var m = /^([^(]*)\((.*)\)\s*$/.exec(f[1]);
    var html;
    if (m && m[2]) {
      var parts = m[2].split(/,\s*/);
      html = esc(m[1]) + '(' + parts.map(function (p, i) {
        return i === Math.min(call.arg, parts.length - 1) ? '<b>' + esc(p) + '</b>' : esc(p);
      }).join(', ') + ')';
    } else html = esc(f[1]);
    argtip.innerHTML = '<span class="mono">' + html + '</span>';
    argtip.classList.remove('hidden');
  }
  input.addEventListener('input', function () { lastIns = null; clearPicked(); updateTips(); });
  input.addEventListener('click', function () { lastIns = null; updateTips(); });
  input.addEventListener('blur', function () {
    setTimeout(function () { if (document.activeElement !== input) { hideAc(); argtip.classList.add('hidden'); } }, 150);
  });
  ac.addEventListener('mousedown', function (ev) {
    var li = ev.target.closest('li[data-i]');
    if (!li) return;
    ev.preventDefault();
    pickAc(parseInt(li.getAttribute('data-i'), 10));
  });

  input.addEventListener('keydown', function (ev) {
    var open = acItems.length > 0;
    if (open && (ev.key === 'ArrowDown' || ev.key === 'ArrowUp')) {
      ev.preventDefault();
      acIndex = (acIndex + (ev.key === 'ArrowDown' ? 1 : -1) + acItems.length) % acItems.length;
      renderAc();
      return;
    }
    if (open && ev.key === 'Tab') { ev.preventDefault(); pickAc(acIndex); return; }
    if (open && ev.key === 'Escape') { ev.preventDefault(); hideAc(); argtip.classList.add('hidden'); return; }
    if (ev.key === 'F4') {
      /* F4: 커서가 있는 셀 주소의 $ 를 A1 → $A$1 → A$1 → $A1 순서로 */
      ev.preventDefault();
      var v = input.value, pos = input.selectionStart;
      var re = /(\$?)([A-Za-z]{1,3})(\$?)(\d+)/g, m;
      while ((m = re.exec(v))) {
        var s = m.index, e = s + m[0].length;
        if (pos >= s && pos <= e) {
          var state = (m[1] ? 2 : 0) + (m[3] ? 1 : 0); // 0:A1 3:$A$1 1:A$1 2:$A1
          var next = { 0: 3, 3: 1, 1: 2, 2: 0 }[state];
          var rep = (next & 2 ? '$' : '') + m[2] + (next & 1 ? '$' : '') + m[4];
          input.value = v.slice(0, s) + rep + v.slice(e);
          input.setSelectionRange(s + rep.length, s + rep.length);
          break;
        }
      }
      lastIns = null;
    } else if (ev.key === 'Enter') {
      ev.preventDefault();
      hideAc();
      send(ev.ctrlKey ? 'try' : 'check');
    } else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight' || ev.key === 'Home' || ev.key === 'End') {
      lastIns = null;
      setTimeout(updateTips, 0);
    }
  });

  document.getElementById('btn-try').addEventListener('click', function () { send('try'); });
  document.getElementById('btn-check').addEventListener('click', function () { send('check'); });
  var btnReset = document.getElementById('btn-reset');
  if (btnReset) btnReset.addEventListener('click', function () { resetGrid(); resultBox.className = 'result'; });

  function send(mode) {
    var text = input.value.trim();
    if (!text) { showResult('info', '수식을 입력하세요. 예: <code>=SUM(B2:B5)</code>'); input.focus(); return; }
    if (!/^[=+]|^\{=/.test(text)) text = '=' + text;
    post('/api/check', { pid: P.pid, answer: text, mode: mode }).then(function (r) {
      if (r.error) { resetGrid(); showResult('bad', esc(r.error)); return; }
      if (mode === 'try') {
        paint(r, false);
        var first = r.cells && r.cells[0];
        showResult('info', '<b>계산 결과</b> ' + (first ? esc(first.addr) + ' = <code>' + esc(first.value) + '</code>' : '') +
          (r.cells && r.cells.length > 1 ? ' · 채우기 범위 ' + P.fill.length + '칸에 복사해 계산했습니다(셀에 마우스를 올리면 그 칸의 수식).' : '') +
          (r.unknown && r.unknown.length ? '<br>모르는 함수: ' + esc(r.unknown.join(', ')) : '') +
          '<div class="tip">기록되지 않습니다. 맞는지 보려면 [채점].</div>');
        return;
      }
      paint(r, true);
      var html = '<h3>' + (r.ok ? '정답입니다' : '다시 생각해 보세요') + '</h3>';
      if (r.notes && r.notes.length) html += '<ul>' + r.notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>';
      if (!r.ok && r.expected) {
        var bad = r.expected.filter(function (e, i) { return !r.per[i]; }).slice(0, 4);
        if (bad.length) html += '<div class="small" style="margin-top:6px">기대한 값: ' +
          bad.map(function (e) { return esc(e.addr) + ' = <code>' + esc(e.value) + '</code>'; }).join(', ') + '</div>';
      }
      showResult(r.ok ? 'ok' : 'bad', html);
      var after = document.getElementById('after');
      after.classList.remove('hidden');
      after.open = !!r.ok;
      document.getElementById('answer-list').innerHTML = [r.answer].concat(r.alts || []).map(function (a) {
        return '<code>' + esc(a) + '</code>';
      }).join('<br>');
      document.getElementById('explain').textContent = r.explain || '';
    }).catch(function (e) { showResult('bad', esc(e.message)); });
  }
})();
