/* 컴활 필기: 연습(새 문제·틀린 문제·약점 주제), 실제 구성 모의고사, 채점, 기록 */
(function () {
  'use strict';
  var D = window.EXDATA && window.EXDATA.written;
  var main = document.getElementById('main');
  EX.header('written.html');
  if (!D) { main.innerHTML = '<div class="card empty">문제 자료(data/written.js)를 읽지 못했습니다.</div>'; return; }

  var esc = EX.esc, store = EX.store;
  var WEAK_MIN = 3, WEAK_BELOW = 0.7, RECENT_MAX = 400;
  var byId = {};
  D.questions.forEach(function (q) { byId[q.id] = q; });
  var subjectOrder = Object.keys(D.subjects);

  /* ---------- 기록 ---------- */
  function att() { return store.get('w.att', {}); }
  function saveAnswer(q, picked) {
    var a = att();
    var r = a[q.id] || { n: 0, c: 0, ok: false };
    var ok = picked === q.answer;
    r.n += 1; r.c += ok ? 1 : 0; r.ok = ok;
    a[q.id] = r;
    store.set('w.att', a);
    var rec = store.get('w.recent', []).filter(function (x) { return x !== q.id; });
    rec.push(q.id);
    store.set('w.recent', rec.slice(-RECENT_MAX));
    return ok;
  }
  function level() { var l = store.get('w.level', 'c1'); return D.levels[l] ? l : 'c1'; }
  function pool(lv, subject, topic) {
    return D.questions.filter(function (q) {
      return q.levels.indexOf(lv) >= 0 && (!subject || q.subject === subject) && (!topic || q.topic === topic);
    });
  }
  function stats(lv) {
    var a = att(), out = {};
    D.levels[lv].subjects.forEach(function (s) {
      var qs = pool(lv, s), done = 0, ok = 0, topics = {};
      D.subjects[s].topics.forEach(function (t) { topics[t] = { name: t, total: 0, done: 0, ok: 0 }; });
      qs.forEach(function (q) {
        var t = topics[q.topic] || (topics[q.topic] = { name: q.topic, total: 0, done: 0, ok: 0 });
        t.total++;
        if (a[q.id]) { done++; t.done++; if (a[q.id].ok) { ok++; t.ok++; } }
      });
      out[s] = { name: D.subjects[s].name, total: qs.length, done: done, ok: ok,
        topics: D.subjects[s].topics.map(function (t) { return topics[t]; }).filter(function (t) { return t.total; }) };
    });
    return out;
  }
  function weakTopics(st) {
    var out = [];
    Object.keys(st).forEach(function (s) {
      st[s].topics.forEach(function (t) {
        if (t.done >= WEAK_MIN && t.ok / t.done < WEAK_BELOW) out.push({ subject: s, name: t.name, acc: t.ok / t.done, done: t.done });
      });
    });
    return out.sort(function (x, y) { return x.acc - y.acc || y.done - x.done; });
  }

  /* ---------- 문제 고르기 ---------- */
  function pickPractice(lv, subject, topic, mode, n, rnd) {
    var a = att();
    var p = pool(lv, subject, topic);
    var mark = function (q) { return a[q.id] ? (a[q.id].ok ? 2 : 0) : 1; };   // 0 틀림, 1 안 푼 것, 2 맞힘
    if (mode === 'wrong') p = p.filter(function (q) { return a[q.id] && !a[q.id].ok; });
    var realMode = mode;
    if (mode === 'weak') {
      var wk = {};
      weakTopics(stats(lv)).forEach(function (w) { wk[w.subject + '|' + w.name] = 1; });
      var wp = p.filter(function (q) { return wk[q.subject + '|' + q.topic]; });
      if (wp.length) p = wp; else realMode = 'new';
    }
    var sh = EX.shuffle(p, rnd);
    if (realMode === 'weak') sh.sort(function (x, y) { return mark(x) - mark(y); });
    else if (realMode === 'new') sh.sort(function (x, y) { return (a[x.id] ? 1 : 0) - (a[y.id] ? 1 : 0); });
    return { qs: sh.slice(0, n), mode: realMode };
  }

  /* 실제 구성 모의고사: 과목마다 20문항, 주제 비율대로(최근에 푼 문제는 피함) */
  function buildMock(lv, rnd, avoid) {
    var skip = {};
    (avoid || []).forEach(function (id) { skip[id] = 1; });
    var picked = [];
    D.levels[lv].subjects.forEach(function (s) {
      var p = pool(lv, s);
      var fresh = p.filter(function (q) { return !skip[q.id]; });
      if (fresh.length >= D.perSubject) p = fresh;
      var byTopic = {};
      p.forEach(function (q) { (byTopic[q.topic] = byTopic[q.topic] || []).push(q); });
      var topics = Object.keys(byTopic);
      topics.forEach(function (t) { byTopic[t] = EX.shuffle(byTopic[t], rnd); });
      var n = Math.min(D.perSubject, p.length), want = {}, take = {}, used = 0;
      topics.forEach(function (t) { want[t] = n * byTopic[t].length / p.length; take[t] = Math.floor(want[t]); used += take[t]; });
      var tie = {};
      topics.forEach(function (t) { tie[t] = rnd(); });
      topics.slice().sort(function (x, y) { return (want[y] - take[y]) - (want[x] - take[x]) || tie[y] - tie[x]; })
        .slice(0, n - used).forEach(function (t) { take[t]++; });
      var chosen = [];
      topics.forEach(function (t) { chosen = chosen.concat(byTopic[t].slice(0, take[t])); });
      EX.shuffle(chosen, rnd).forEach(function (q) { picked.push(q.id); });
    });
    return picked;
  }

  function grade(qids, answers) {
    var per = {}, rows = [];
    qids.forEach(function (id) {
      var q = byId[id];
      if (!q) return;
      var pick = answers[id];
      var ok = typeof pick === 'number' && pick === q.answer;
      var s = per[q.subject] = per[q.subject] || { n: 0, ok: 0 };
      s.n++; if (ok) s.ok++;
      rows.push({ id: id, subject: q.subject, picked: typeof pick === 'number' ? pick : null, answer: q.answer, ok: ok });
    });
    var subjects = Object.keys(per).sort(function (a, b) { return subjectOrder.indexOf(a) - subjectOrder.indexOf(b); }).map(function (s) {
      var score = per[s].n ? Math.round(100 * per[s].ok / per[s].n) : 0;
      return { subject: s, name: D.subjects[s].name, n: per[s].n, ok: per[s].ok, score: score, cut: score >= D.subjectCut };
    });
    var avg = subjects.length ? Math.round(subjects.reduce(function (t, x) { return t + x.score; }, 0) / subjects.length * 10) / 10 : 0;
    return { subjects: subjects, average: avg, rows: rows,
      passed: subjects.length > 0 && avg >= D.averageCut && subjects.every(function (x) { return x.cut; }) };
  }

  /* ---------- 화면 ---------- */
  var timer = null;
  function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }
  function setMain(html) { stopTimer(); main.innerHTML = html; window.scrollTo(0, 0); }

  function parseHash() {
    var h = location.hash.replace(/^#\/?/, '');
    var parts = h.split('?');
    var q = {};
    (parts[1] || '').split('&').forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf('=');
      q[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1));
    });
    return { path: parts[0], q: q };
  }
  function link(path, q) {
    var s = Object.keys(q || {}).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(q[k]); }).join('&');
    return '#/' + path + (s ? '?' + s : '');
  }

  var CIRCLED = '①②③④';
  function durText(sec) { return sec ? (sec >= 60 ? Math.floor(sec / 60) + '분' : sec + '초') : '–'; }

  function viewHome() {
    var lv = level(), L = D.levels[lv], st = stats(lv), weak = weakTopics(st);
    var saved = store.get('w.mock.' + lv, null);
    var hist = store.get('w.hist', []).slice(-10).reverse();
    var h = '<div class="page-head"><div><h1>컴활 필기</h1><p>' + L.name + ' — ' + L.subjects.map(function (s) { return D.subjects[s].name; }).join(' · ') +
      '. 실제 시험처럼 과목마다 ' + D.perSubject + '문항, ' + L.minutes + '분 · 과목별 ' + D.subjectCut + '점 이상이면서 평균 ' + D.averageCut + '점 이상이면 합격. (문제 ' + D.questions.length + '개)</p></div></div>';
    h += '<div class="tabs" role="tablist" aria-label="급 선택">' + Object.keys(D.levels).map(function (k) {
      return '<button class="tab' + (k === lv ? ' on' : '') + '" role="tab" aria-selected="' + (k === lv) + '" data-level="' + k + '">컴활 ' + D.levels[k].name + '</button>';
    }).join('') + '</div>';

    h += '<section class="card"><div class="row spread"><div><h2 style="margin:0">모의고사 (' + L.name + ' · ' + L.subjects.length * D.perSubject + '문항 · ' + L.minutes + '분)</h2></div><div class="row">';
    if (saved) h += '<a class="btn primary" href="' + link('mock/' + lv, { resume: 1 }) + '">이어서 풀기</a><a class="btn" href="' + link('mock/' + lv, { 'new': 1 }) + '">새 문제지로 시작</a>';
    else h += '<a class="btn primary" href="' + link('mock/' + lv, {}) + '">' + L.name + ' 모의고사 시작 (' + L.subjects.length * D.perSubject + '문항 · ' + L.minutes + '분)</a>';
    h += '</div></div><p class="small muted">최근에 푼 문제는 되도록 빼고, 과목마다 문제 은행의 주제 비율대로 ' + D.perSubject + '문항씩 섞어 뽑습니다. 풀던 문제지는 새로 고쳐도 이어지고, 시간이 다 되면 자동 제출됩니다. 답안을 내면 과목별 점수·과락·해설을 보여 줍니다.</p>';
    if (hist.length) {
      h += '<div class="table-wrap"><table class="t"><thead><tr><th>날짜</th><th>급</th><th class="r">평균</th><th>결과</th><th class="r">시간</th><th></th></tr></thead><tbody>' +
        hist.map(function (r) {
          return '<tr><td>' + EX.fmtDate(r.at) + '</td><td>' + esc(D.levels[r.level] ? D.levels[r.level].name : r.level) + '</td><td class="r">' + r.average + '</td><td>' + (r.passed ? '합격' : '불합격') +
            '</td><td class="r">' + durText(r.seconds) + '</td><td>' + (r.rows ? '<a href="' + link('result/' + r.at, {}) + '">결과</a>' : '') + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    }
    h += '</section>';

    if (weak.length) {
      h += '<section class="card"><div class="row spread"><h2 style="margin:0">약점 주제</h2><a class="btn primary" href="' + link('practice', { level: lv, mode: 'weak' }) + '">약점 주제 20문항 풀기</a></div>' +
        '<p class="muted small">마지막에 푼 결과 기준으로 맞힌 비율이 ' + Math.round(WEAK_BELOW * 100) + '% 미만인 주제입니다(주제마다 ' + WEAK_MIN + '문제 이상 푼 경우). 틀린 문제부터 나옵니다.</p><ul class="list">' +
        weak.slice(0, 6).map(function (w) {
          return '<li><div class="grow"><a class="title" href="' + link('practice', { level: lv, subject: w.subject, topic: w.name, mode: 'weak' }) + '">' + esc(w.name) + '</a><div class="small muted">' + esc(L.name) + ' · ' +
            esc(D.subjects[w.subject].name) + '</div></div><span class="nowrap small bad-text"><b>' + Math.round(w.acc * 100) + '%</b> <span class="muted">(' + w.done + '문제)</span></span></li>';
        }).join('') + '</ul></section>';
    }

    h += '<div class="grid g3">';
    L.subjects.forEach(function (s) {
      var x = st[s], pct = x.total ? Math.round(100 * x.done / x.total) : 0, wrong = x.done - x.ok;
      h += '<section class="card"><div class="row spread"><h2 style="margin:0">' + esc(x.name) + '</h2><span class="small muted">' + x.done + '/' + x.total + '</span></div>' +
        '<div class="meter" aria-hidden="true" style="margin-top:8px"><i style="width:' + pct + '%"></i></div><p class="muted small" style="margin:6px 0">맞힘 ' + x.ok + ' · 틀림 ' + wrong + '</p>' +
        '<div class="row" style="margin-bottom:10px"><a class="btn sm primary" href="' + link('practice', { level: lv, subject: s, mode: 'new' }) + '">' + D.perSubject + '문항 풀기</a>' +
        (wrong ? '<a class="btn sm" href="' + link('practice', { level: lv, subject: s, mode: 'wrong' }) + '">틀린 문제 ' + wrong + '</a>' : '') +
        '<a class="btn sm" href="' + link('practice', { level: lv, subject: s, mode: 'all' }) + '">전체 섞기</a></div>' +
        '<table class="t"><thead><tr><th>주제</th><th class="r">푼 문제</th><th class="r">정답률</th></tr></thead><tbody>' + x.topics.map(function (t) {
          return '<tr><td><a href="' + link('practice', { level: lv, subject: s, topic: t.name, mode: 'new' }) + '">' + esc(t.name) + '</a></td><td class="r">' + t.done + '/' + t.total +
            '</td><td class="r">' + (t.done ? Math.round(100 * t.ok / t.done) + '%' : '–') + '</td></tr>';
        }).join('') + '</tbody></table></section>';
    });
    h += '</div>';
    setMain(h);
    main.querySelectorAll('[data-level]').forEach(function (b) {
      b.addEventListener('click', function () { store.set('w.level', b.getAttribute('data-level')); viewHome(); });
    });
  }

  function optionButtons(q) {
    return '<div class="opts" role="group" aria-label="보기">' + q.options.map(function (o, i) {
      return '<button type="button" class="opt" data-q="' + q.id + '" data-i="' + i + '"><span class="n">' + (i + 1) + '</span><span>' + esc(o) + '</span></button>';
    }).join('') + '</div>';
  }
  function textOf(q) {
    // '|' 줄이 있는 문제는 표로 보여 준다
    var lines = q.q.split('\n');
    if (!lines.some(function (l) { return l.indexOf('|') >= 0; })) return '<p class="qtext">' + esc(q.q) + '</p>';
    var html = '', rows = [];
    lines.forEach(function (l) {
      if (l.indexOf('|') >= 0) rows.push(l.split('|').map(function (c) { return c.trim(); }));
      else {
        if (rows.length) { html += tableOf(rows); rows = []; }
        if (l.trim()) html += '<p class="qtext">' + esc(l) + '</p>';
      }
    });
    if (rows.length) html += tableOf(rows);
    return html;
  }
  function tableOf(rows) {
    return '<div class="sheet-wrap"><table class="sheet"><thead><tr>' + rows[0].map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.slice(1).map(function (r) { return '<tr>' + r.map(function (c) { return '<td>' + esc(c) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>';
  }
  function crumb(lv) { return '<div class="crumb"><a href="#/">컴활 필기 ' + esc(D.levels[lv].name) + '</a> ›</div>'; }

  function viewPractice(o) {
    var lv = D.levels[o.level] ? o.level : level();
    if (o.subject && D.subjects[o.subject] && D.levels[lv].subjects.indexOf(o.subject) < 0) lv = 'c1';   // 2급에서 데이터베이스(1급 과목)를 열면 1급으로
    var mode = ['new', 'wrong', 'all', 'weak'].indexOf(o.mode) >= 0 ? o.mode : 'new';
    var sel = pickPractice(lv, o.subject, o.topic, mode, 20, Math.random);
    var qs = sel.qs;
    var title = (o.subject && D.subjects[o.subject] ? D.subjects[o.subject].name : '전 과목') + (o.topic ? ' · ' + o.topic : '') +
      ({ wrong: ' · 틀린 문제 다시', weak: ' · 약점 주제' }[sel.mode] || '');
    if (!qs.length) {
      setMain('<div class="page-head"><div>' + crumb(lv) + '<h1>' + esc(title) + '</h1></div></div><div class="card empty">' + (mode === 'wrong' ? '틀린 문제가 없습니다.' : '문제가 없습니다.') + '<p><a class="btn" href="#/">처음으로</a></p></div>');
      return;
    }
    var h = '<div class="page-head"><div>' + crumb(lv) + '<h1>' + esc(title) + '</h1><p class="muted">' + qs.length + '문항 · 보기를 누르면 바로 채점하고 해설을 보여 줍니다. 키보드: 1~4 로 고르기.</p></div>' +
      '<div class="row"><a class="btn" href="' + link('practice', { level: lv, subject: o.subject || '', topic: o.topic || '', mode: mode, t: Date.now() }) + '">다른 문제로</a><a class="btn" href="#/">그만하기</a></div></div>' +
      '<div class="stickybar row spread"><span id="p-prog">0 / ' + qs.length + '문제</span><span id="p-score" class="muted">정답 0</span></div><div id="p-list">';
    qs.forEach(function (q, i) {
      h += '<article class="q" id="q-' + q.id + '" data-id="' + q.id + '"><div class="row spread" style="margin-bottom:6px"><div><span class="qno">' + (i + 1) + '.</span><span class="badge">' + esc(D.subjects[q.subject].name) + ' · ' + esc(q.topic) + '</span>' +
        (q.levels.length === 1 && q.levels[0] === 'c1' ? ' <span class="badge c1">1급</span>' : '') + '</div><span class="muted small">' + q.id + '</span></div>' +
        textOf(q) + optionButtons(q) + '<div class="explain hidden" role="status" aria-live="polite" tabindex="-1"></div></article>';
    });
    h += '</div><div id="p-end" class="card hidden"></div>';
    setMain(h);

    var answered = 0, right = 0;
    function answer(q, i) {
      var card = document.getElementById('q-' + q.id);
      if (!card || card.getAttribute('data-done')) return;
      card.setAttribute('data-done', '1');
      var ok = saveAnswer(q, i);
      answered++; if (ok) right++;
      card.querySelectorAll('.opt').forEach(function (b) {
        var k = Number(b.getAttribute('data-i'));
        b.disabled = true;
        if (k === q.answer) b.classList.add('right');
        else if (k === i) b.classList.add('wrong');
      });
      var ex = card.querySelector('.explain');
      ex.className = 'explain ' + (ok ? 'ok' : 'bad');
      ex.innerHTML = '<b>' + (ok ? '정답입니다.' : '오답입니다. 정답은 ' + CIRCLED.charAt(q.answer) + '번') + '</b>\n' + esc(q.explain);
      document.getElementById('p-prog').textContent = answered + ' / ' + qs.length + '문제';
      document.getElementById('p-score').textContent = '정답 ' + right;
      if (answered === qs.length) finish();
      else focusNext();
    }
    function focusNext() {
      var nxt = qs.find(function (q) { return !document.getElementById('q-' + q.id).getAttribute('data-done'); });
      if (nxt) document.getElementById('q-' + nxt.id).scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    function finish() {
      var end = document.getElementById('p-end');
      end.classList.remove('hidden');
      end.innerHTML = '<h2>끝났습니다</h2><p class="stat">' + right + ' / ' + qs.length + '<span class="muted small" style="font-weight:400"> 정답 (' + Math.round(100 * right / qs.length) + '점)</span></p>' +
        '<p class="small muted">틀린 문제는 [컴활 필기] 화면의 "틀린 문제"에서 다시 풀 수 있습니다.</p><div class="row">' +
        '<a class="btn primary" href="' + link('practice', { level: lv, subject: o.subject || '', topic: o.topic || '', mode: mode, t: Date.now() }) + '">같은 조건으로 20문제 더</a>' +
        '<a class="btn" href="' + link('practice', { level: lv, subject: o.subject || '', topic: o.topic || '', mode: 'wrong' }) + '">틀린 문제 다시</a><a class="btn" href="#/">처음으로</a></div>';
      end.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    main.onclick = onClick;
    function onClick(ev) {
      var b = ev.target.closest('.opt');
      if (!b) return;
      var q = byId[b.getAttribute('data-q')];
      answer(q, Number(b.getAttribute('data-i')));
    }
    document.onkeydown = function (ev) {
      if (ev.target && /input|textarea|select/i.test(ev.target.tagName)) return;
      if (ev.ctrlKey || ev.altKey || ev.metaKey) return;
      if (ev.key >= '1' && ev.key <= '4') {
        var nxt = qs.find(function (q) { return !document.getElementById('q-' + q.id).getAttribute('data-done'); });
        if (nxt) { answer(nxt, Number(ev.key) - 1); ev.preventDefault(); }
      }
    };
  }

  var leaveGuard = null;
  function clearLeaveGuard() { if (leaveGuard) { window.removeEventListener('beforeunload', leaveGuard); leaveGuard = null; } }

  function viewMock(lv, opts) {
    if (!D.levels[lv]) { location.hash = '#/'; return; }
    var L = D.levels[lv], key = 'w.mock.' + lv;
    var s = store.get(key, null);
    if (opts['new']) s = null;
    if (s) {
      var okPaper = s.qids && s.qids.length === D.perSubject * L.subjects.length && s.qids.every(function (id) { return byId[id] && byId[id].levels.indexOf(lv) >= 0; });
      if (!okPaper) s = null;
    }
    var gradeNow = false;
    if (s && Date.now() - s.start > L.minutes * 60000 + 5000 && !opts.resume) {   // 시간이 지난 채 다시 연 문제지
      if (window.confirm('이전에 풀던 모의고사는 시간이 끝났습니다.\n[확인] 새 문제지로 시작  /  [취소] 그 문제지를 지금 제출해 채점')) s = null;
      else gradeNow = true;
    }
    if (!s) {
      s = { qids: buildMock(lv, Math.random, store.get('w.recent', [])), answers: {}, start: Date.now() };
      store.set(key, s);
    }
    var left = function () { return L.minutes * 60 - (Date.now() - s.start) / 1000; };
    var h = '<div class="page-head"><div>' + crumb(lv) + '<h1>필기 모의고사 ' + L.name + '</h1><p class="muted">' + L.subjects.length + '과목 ' + s.qids.length + '문항 · ' + L.minutes + '분 · 과목별 ' + D.subjectCut + '점 이상, 평균 ' + D.averageCut +
      '점 이상 합격 · 고른 답과 시간은 이 브라우저에 저장되어 새로 고쳐도 이어지며, 시간이 다 되면 자동으로 제출합니다.</p></div></div>' +
      '<div class="stickybar row spread"><span>남은 시간 <span class="timer" id="m-time">--:--</span></span><span id="m-prog" class="muted"></span>' +
      '<span class="row"><a class="btn ghost" href="' + link('mock/' + lv, { 'new': 1 }) + '" id="m-new">새 문제지</a><button class="btn primary" id="m-submit" type="button">답안 제출·채점</button></span></div>';
    var n = 0;
    L.subjects.forEach(function (sub, si) {
      h += '<h2 style="margin-top:18px">' + (si + 1) + '과목 ' + esc(D.subjects[sub].name) + '</h2>';
      s.qids.filter(function (id) { return byId[id].subject === sub; }).forEach(function (id) {
        var q = byId[id]; n++;
        h += '<article class="q" data-id="' + id + '"><div style="margin-bottom:6px"><span class="qno">' + n + '.</span><span class="badge">' + esc(q.topic) + '</span></div>' + textOf(q) + optionButtons(q) + '</article>';
      });
    });
    setMain(h);
    clearLeaveGuard();
    if (opts['new'] || opts.resume) { try { history.replaceState(null, '', location.pathname + location.search + '#/mock/' + lv); } catch (e) { /* 무시 */ } }   // 새로 고쳐도 또 새 문제지가 되지 않게
    function paint() {
      var cnt = 0;
      main.querySelectorAll('.opt').forEach(function (b) {
        var id = b.getAttribute('data-q'), i = Number(b.getAttribute('data-i'));
        b.classList.toggle('sel', s.answers[id] === i);
      });
      Object.keys(s.answers).forEach(function (id) { if (byId[id] && s.qids.indexOf(id) >= 0) cnt++; });
      document.getElementById('m-prog').textContent = cnt + ' / ' + s.qids.length + ' 문항 답함';
    }
    paint();
    main.onclick = function (ev) {
      var b = ev.target.closest('.opt');
      if (!b) return;
      s.answers[b.getAttribute('data-q')] = Number(b.getAttribute('data-i'));
      store.set(key, s);
      paint();
    };
    var submitted = false;
    function submit(auto) {
      if (submitted) return;
      var un = s.qids.filter(function (id) { return typeof s.answers[id] !== 'number'; }).length;
      if (!auto && un && !window.confirm('아직 ' + un + '문항을 풀지 않았습니다. 그래도 제출할까요?')) return;
      submitted = true;
      clearLeaveGuard();
      var res = grade(s.qids, s.answers);
      res.level = lv; res.at = Date.now(); res.seconds = Math.min(L.minutes * 60, Math.round((Date.now() - s.start) / 1000)); res.auto = !!auto;
      var a = att();                       // 안 푼 문제도 오답으로 기록(어떤 주제가 약한지 보이도록)
      s.qids.forEach(function (id) {
        var q = byId[id], r = a[id] || { n: 0, c: 0, ok: false }, ok = s.answers[id] === q.answer;
        r.n++; r.c += ok ? 1 : 0; r.ok = ok; a[id] = r;
      });
      store.set('w.att', a);
      store.set('w.recent', store.get('w.recent', []).concat(s.qids).slice(-RECENT_MAX));
      var hist = store.get('w.hist', []);
      hist.push({ at: res.at, level: lv, average: res.average, passed: res.passed, seconds: res.seconds, auto: res.auto, subjects: res.subjects, rows: res.rows });
      store.set('w.hist', hist.slice(-30));
      store.remove(key);
      store.set('w.last', res);
      location.hash = '#/result/' + res.at;
    }
    document.getElementById('m-submit').addEventListener('click', function () { submit(false); });
    document.getElementById('m-new').addEventListener('click', function (ev) {
      if (Object.keys(s.answers).length && !window.confirm('지금 문제지의 답을 버리고 새 문제지를 받을까요?')) { ev.preventDefault(); return; }
      clearLeaveGuard();
    });
    leaveGuard = function (ev) { if (!submitted && Object.keys(s.answers).length) { ev.preventDefault(); ev.returnValue = ''; } };
    window.addEventListener('beforeunload', leaveGuard);   // 답을 고른 채 창을 닫으면 한 번 묻기
    function tick() {
      var t = left(), el = document.getElementById('m-time');
      if (!el) { stopTimer(); return; }
      el.textContent = EX.fmtTime(t);
      el.classList.toggle('low', t < 300);
      if (t <= 0) { stopTimer(); submit(true); }
    }
    tick();
    timer = setInterval(tick, 1000);
    if (gradeNow) { stopTimer(); submit(true); }
  }

  function viewResult(at) {
    var res = at ? store.get('w.hist', []).filter(function (r) { return r.at === at; })[0] : store.get('w.last', null);
    if (!res || !res.rows) {
      setMain('<div class="card empty">채점 결과를 찾을 수 없습니다(오래된 기록은 점수만 남습니다).<p><a class="btn" href="#/">처음으로</a></p></div>');
      return;
    }
    var L = D.levels[res.level] || { name: '' };
    var h = '<div class="page-head"><div>' + crumb(res.level) + '<h1>필기 ' + esc(L.name) + ' 결과 — ' + (res.passed ? '합격' : '불합격') + '</h1><p class="muted">' + EX.fmtDate(res.at) + ' · 걸린 시간 ' + EX.fmtTime(res.seconds || 0) +
      (res.auto ? ' (시간이 끝나 자동 제출)' : '') + ' · 평균 ' + res.average + '점</p></div><div class="row"><a class="btn primary" href="' + link('mock/' + res.level, { 'new': 1 }) + '">새 모의고사</a>' +
      '<a class="btn" href="' + link('practice', { level: res.level, mode: 'wrong' }) + '">틀린 문제 다시</a></div></div>';
    h += '<div class="grid g3">' + res.subjects.map(function (x) {
      return '<section class="card"><div class="row spread"><h2 style="margin:0">' + esc(x.name) + '</h2><b class="' + (x.cut ? 'ok-text' : 'bad-text') + '">' + x.score + '점</b></div>' +
        '<p class="small muted">' + x.ok + '/' + x.n + ' 정답 · ' + (x.cut ? '통과' : '과락(' + D.subjectCut + '점 미만)') + '</p><div class="meter" aria-hidden="true"><i style="width:' + x.score + '%"></i></div></section>';
    }).join('') + '</div>';
    h += '<section class="card"><div class="row spread"><div><span class="stat">' + res.average + '점</span> <span class="muted">평균</span></div>' +
      (res.passed ? '<span class="badge ok">합격 기준 충족</span>' : '<span class="badge bad">합격 기준 미달</span>') + '</div>' +
      '<p class="muted small" style="margin:6px 0 0">합격 기준: 과목마다 ' + D.subjectCut + '점 이상이고 평균 ' + D.averageCut + '점 이상입니다.</p></section>';
    h += '<section class="card"><div class="row spread"><h2 style="margin:0">문항별 해설</h2><label class="small"><input type="checkbox" id="wrong-only"> 틀린 문제만</label></div>';
    res.rows.forEach(function (r, i) {
      var q = byId[r.id];
      if (!q) return;
      h += '<article class="q" data-ok="' + (r.ok ? 1 : 0) + '" style="border-left:3px solid ' + (r.ok ? 'var(--ok)' : 'var(--bad)') + ';padding-left:10px"><div class="row spread" style="margin-bottom:6px"><div><span class="qno">' + (i + 1) + '.</span> ' + (r.ok ? '○' : '✕') +
        ' <span class="badge">' + esc(D.subjects[q.subject].name) + ' · ' + esc(q.topic) + '</span></div><span class="muted small">' + q.id + '</span></div>' +
        textOf(q) + '<div class="opts">' + q.options.map(function (o, k) {
          var cls = k === q.answer ? ' right' : (k === r.picked && !r.ok ? ' wrong' : '');
          return '<div class="opt' + cls + '"><span class="n">' + (k + 1) + '</span><span>' + esc(o) + (k === r.picked ? ' <span class="muted small">← 고른 답</span>' : '') + '</span></div>';
        }).join('') + '</div>' + (r.picked === null ? '<p class="small bad-text">답을 고르지 않았습니다.</p>' : '') + '<div class="explain">' + esc(q.explain) + '</div></article>';
    });
    h += '</section>';
    setMain(h);
    document.getElementById('wrong-only').addEventListener('change', function (ev) {
      main.querySelectorAll('article[data-ok="1"]').forEach(function (x) { x.classList.toggle('hidden', ev.target.checked); });
    });
  }

  function route() {
    document.onkeydown = null;
    main.onclick = null;
    clearLeaveGuard();
    var r = parseHash();
    if (r.path === 'practice') viewPractice({ level: r.q.level, subject: r.q.subject, topic: r.q.topic, mode: r.q.mode });
    else if (r.path.indexOf('mock/') === 0) viewMock(r.path.slice(5), { 'new': r.q['new'] === '1', resume: r.q.resume === '1' });
    else if (r.path === 'result' || r.path.indexOf('result/') === 0) viewResult(Number(r.path.slice(7)) || 0);
    else viewHome();
  }
  window.addEventListener('hashchange', route);
  window.__written = { grade: grade, buildMock: buildMock, pickPractice: pickPractice, weakTopics: weakTopics, stats: stats, byId: byId };
  route();
})();
