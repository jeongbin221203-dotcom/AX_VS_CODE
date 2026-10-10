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

  function viewHome() {
    var lv = level(), L = D.levels[lv], st = stats(lv), weak = weakTopics(st);
    var saved = store.get('w.mock.' + lv, null);
    var hist = store.get('w.hist', []).slice(-8).reverse();
    var h = '<div class="page-head"><div><h1>컴활 필기</h1><p>문제 ' + D.questions.length + '개 · 연습은 한 문제씩 바로 채점하고, 모의고사는 실제 시험 구성(과목마다 ' + D.perSubject +
      '문항)으로 풉니다. 합격 기준: 과목마다 ' + D.subjectCut + '점 이상, 평균 ' + D.averageCut + '점 이상.</p></div></div>';
    h += '<div class="tabs" role="tablist" aria-label="급 선택">' + Object.keys(D.levels).map(function (k) {
      return '<button class="tab' + (k === lv ? ' on' : '') + '" role="tab" aria-selected="' + (k === lv) + '" data-level="' + k + '">컴활 ' + D.levels[k].name + '</button>';
    }).join('') + '</div>';

    h += '<section class="card"><div class="row spread"><div><h2 style="margin:0">모의고사 (' + L.name + ' · ' + L.minutes + '분)</h2><p class="muted small" style="margin:2px 0 0">' +
      L.subjects.map(function (s) { return D.subjects[s].name; }).join(' · ') + ' 각 ' + D.perSubject + '문항, 최근 푼 문제는 되도록 피해서 뽑습니다.</p></div><div class="row">';
    if (saved) h += '<a class="btn primary" href="' + link('mock/' + lv, { resume: 1 }) + '">이어서 풀기</a><a class="btn" href="' + link('mock/' + lv, { 'new': 1 }) + '">새 문제지로 시작</a>';
    else h += '<a class="btn primary" href="' + link('mock/' + lv, {}) + '">모의고사 시작</a>';
    h += '</div></div></section>';

    if (weak.length) {
      h += '<section class="card"><div class="row spread"><h2 style="margin:0">약점 주제</h2><a class="btn primary" href="' + link('practice', { level: lv, mode: 'weak' }) + '">약점 주제 20문항 풀기</a></div>' +
        '<p class="muted small">마지막에 푼 결과 기준으로 맞힌 비율이 ' + Math.round(WEAK_BELOW * 100) + '% 미만인 주제입니다(주제마다 ' + WEAK_MIN + '문제 이상 푼 경우). 틀린 문제부터 나옵니다.</p><ul style="margin:0;padding-left:18px">' +
        weak.slice(0, 6).map(function (w) {
          return '<li><a href="' + link('practice', { level: lv, subject: w.subject, topic: w.name, mode: 'weak' }) + '">' + esc(w.name) + '</a> <span class="muted small">' +
            esc(D.subjects[w.subject].name) + ' · ' + Math.round(w.acc * 100) + '% (' + w.done + '문제)</span></li>';
        }).join('') + '</ul></section>';
    }

    h += '<div class="grid g3">';
    L.subjects.forEach(function (s) {
      var x = st[s], pct = x.total ? Math.round(100 * x.done / x.total) : 0, acc = x.done ? Math.round(100 * x.ok / x.done) : 0;
      h += '<section class="card"><h2>' + esc(x.name) + '</h2><div class="stat">' + x.done + '<span class="muted small" style="font-weight:400"> / ' + x.total + '문제 푼 것</span></div>' +
        '<div class="meter" aria-hidden="true"><i style="width:' + pct + '%"></i></div><p class="muted small" style="margin-top:6px">마지막 풀이 정답률 ' + (x.done ? acc + '%' : '-') + '</p>' +
        '<div class="row" style="margin-bottom:10px"><a class="btn sm primary" href="' + link('practice', { level: lv, subject: s, mode: 'new' }) + '">새 문제 20</a>' +
        '<a class="btn sm" href="' + link('practice', { level: lv, subject: s, mode: 'wrong' }) + '">틀린 문제 다시</a>' +
        '<a class="btn sm" href="' + link('practice', { level: lv, subject: s, mode: 'all' }) + '">전체 섞기</a></div>' +
        '<table class="t"><tbody>' + x.topics.map(function (t) {
          return '<tr><td><a href="' + link('practice', { level: lv, subject: s, topic: t.name, mode: 'new' }) + '">' + esc(t.name) + '</a></td><td class="muted small" style="text-align:right">' +
            (t.done ? t.ok + '/' + t.done + ' · ' : '') + t.total + '문제</td></tr>';
        }).join('') + '</tbody></table></section>';
    });
    h += '</div>';

    h += '<section class="card"><h2>최근 모의고사</h2>' + (hist.length ? '<table class="t"><thead><tr><th>날짜</th><th>급</th><th>평균</th><th>결과</th><th>걸린 시간</th></tr></thead><tbody>' +
      hist.map(function (r) {
        return '<tr><td>' + EX.fmtDate(r.at) + '</td><td>' + esc(D.levels[r.level] ? D.levels[r.level].name : r.level) + '</td><td>' + r.average + '점</td><td>' +
          (r.passed ? '<span class="badge ok">합격 기준 충족</span>' : '<span class="badge bad">미달</span>') + '</td><td>' + EX.fmtTime(r.seconds || 0) + '</td></tr>';
      }).join('') + '</tbody></table>' : '<p class="muted">아직 푼 모의고사가 없습니다.</p>') + '</section>';
    setMain(h);
    main.querySelectorAll('[data-level]').forEach(function (b) {
      b.addEventListener('click', function () { store.set('w.level', b.getAttribute('data-level')); viewHome(); });
    });
  }

  function optionButtons(q, prefix) {
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

  function viewPractice(o) {
    var lv = D.levels[o.level] ? o.level : level();
    var mode = ['new', 'wrong', 'all', 'weak'].indexOf(o.mode) >= 0 ? o.mode : 'new';
    var sel = pickPractice(lv, o.subject, o.topic, mode, 20, Math.random);
    var qs = sel.qs;
    var title = (o.subject ? D.subjects[o.subject].name : '전 과목') + (o.topic ? ' · ' + o.topic : '') +
      ({ wrong: ' · 틀린 문제 다시', weak: ' · 약점 주제' }[sel.mode] || '');
    if (!qs.length) {
      setMain('<div class="card empty">' + (mode === 'wrong' ? '틀린 문제가 없습니다. 먼저 문제를 풀어 보세요.' : '풀 문제가 없습니다.') + '<p><a class="btn" href="#/">처음으로</a></p></div>');
      return;
    }
    var h = '<div class="page-head"><div><h1>' + esc(title) + '</h1><p class="muted">컴활 ' + D.levels[lv].name + ' · 보기를 누르면 바로 채점됩니다. 키보드 1~4로도 답할 수 있습니다.</p></div>' +
      '<div class="row"><a class="btn" href="#/">그만하기</a></div></div>' +
      '<div class="stickybar row spread"><span id="p-prog">0 / ' + qs.length + '문제</span><span id="p-score" class="muted">정답 0</span></div><div id="p-list">';
    qs.forEach(function (q, i) {
      h += '<article class="q" id="q-' + q.id + '" data-id="' + q.id + '"><div class="row spread" style="margin-bottom:6px"><div><span class="qno">' + (i + 1) + '.</span><span class="badge">' + esc(q.topic) + '</span></div><span class="muted small">' + q.id + '</span></div>' +
        textOf(q) + optionButtons(q) + '<div class="explain hidden" aria-live="polite"></div></article>';
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
      ex.innerHTML = '<b>' + (ok ? '정답입니다.' : '오답입니다. 정답은 ' + (q.answer + 1) + '번') + '</b>\n' + esc(q.explain);
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
      end.innerHTML = '<h2>끝났습니다</h2><p class="stat">' + right + ' / ' + qs.length + '<span class="muted small" style="font-weight:400"> 정답</span></p><div class="row">' +
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

  function viewMock(lv, opts) {
    if (!D.levels[lv]) { location.hash = '#/'; return; }
    var L = D.levels[lv], key = 'w.mock.' + lv;
    var s = store.get(key, null);
    if (opts['new']) s = null;
    if (s) {
      var okPaper = s.qids && s.qids.length === D.perSubject * L.subjects.length && s.qids.every(function (id) { return byId[id] && byId[id].levels.indexOf(lv) >= 0; });
      if (!okPaper) s = null;
    }
    if (s && Date.now() - s.start > L.minutes * 60000 + 5000 && !opts.resume) s = null;
    if (!s) {
      s = { qids: buildMock(lv, Math.random, store.get('w.recent', [])), answers: {}, start: Date.now() };
      store.set(key, s);
    }
    var left = function () { return L.minutes * 60 - (Date.now() - s.start) / 1000; };
    var h = '<div class="page-head"><div><h1>컴활 ' + L.name + ' 모의고사</h1><p class="muted">' + L.subjects.length + '과목 ' + s.qids.length + '문항 · 제한 시간 ' + L.minutes +
      '분. 답은 자동으로 저장되어 새로 고쳐도 이어서 풀 수 있습니다.</p></div></div>' +
      '<div class="stickybar row spread"><span>남은 시간 <span class="timer" id="m-time">--:--</span></span><span id="m-prog" class="muted"></span>' +
      '<button class="btn primary" id="m-submit" type="button">제출하고 채점</button></div>';
    var n = 0;
    L.subjects.forEach(function (sub) {
      h += '<h2 style="margin-top:18px">' + esc(D.subjects[sub].name) + '</h2>';
      s.qids.filter(function (id) { return byId[id].subject === sub; }).forEach(function (id, i) {
        var q = byId[id]; n++;
        h += '<article class="q" data-id="' + id + '"><div style="margin-bottom:6px"><span class="qno">' + (i + 1) + '.</span><span class="badge">' + esc(q.topic) + '</span></div>' + textOf(q) + optionButtons(q) + '</article>';
      });
    });
    setMain(h);
    function paint() {
      var cnt = 0;
      main.querySelectorAll('.opt').forEach(function (b) {
        var id = b.getAttribute('data-q'), i = Number(b.getAttribute('data-i'));
        b.classList.toggle('sel', s.answers[id] === i);
      });
      Object.keys(s.answers).forEach(function (id) { if (byId[id] && s.qids.indexOf(id) >= 0) cnt++; });
      document.getElementById('m-prog').textContent = cnt + ' / ' + s.qids.length + ' 답함';
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
      if (!auto && un && !window.confirm('아직 답하지 않은 문제가 ' + un + '개 있습니다. 제출할까요?')) return;
      submitted = true;
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
      hist.push({ at: res.at, level: lv, average: res.average, passed: res.passed, seconds: res.seconds, subjects: res.subjects });
      store.set('w.hist', hist.slice(-30));
      store.remove(key);
      store.set('w.last', res);
      location.hash = '#/result';
    }
    document.getElementById('m-submit').addEventListener('click', function () { submit(false); });
    function tick() {
      var t = left(), el = document.getElementById('m-time');
      if (!el) { stopTimer(); return; }
      el.textContent = EX.fmtTime(t);
      el.classList.toggle('low', t < 300);
      if (t <= 0) { stopTimer(); submit(true); }
    }
    tick();
    timer = setInterval(tick, 1000);
  }

  function viewResult() {
    var res = store.get('w.last', null);
    if (!res) { location.hash = '#/'; return; }
    var L = D.levels[res.level] || { name: '' };
    var h = '<div class="page-head"><div><h1>채점 결과 · 컴활 ' + esc(L.name) + '</h1><p class="muted">' + EX.fmtDate(res.at) + ' · 걸린 시간 ' + EX.fmtTime(res.seconds) +
      (res.auto ? ' (시간이 끝나 자동 제출)' : '') + '</p></div><div class="row"><a class="btn primary" href="#/">처음으로</a><a class="btn" href="' + link('mock/' + res.level, { 'new': 1 }) + '">새 모의고사</a></div></div>';
    h += '<section class="card"><div class="row spread"><div><span class="stat">' + res.average + '점</span> <span class="muted">평균</span></div>' +
      (res.passed ? '<span class="badge ok">합격 기준 충족</span>' : '<span class="badge bad">합격 기준 미달</span>') + '</div>' +
      '<table class="t" style="margin-top:8px"><thead><tr><th>과목</th><th>맞힌 수</th><th>점수</th><th>과락(' + D.subjectCut + '점)</th></tr></thead><tbody>' +
      res.subjects.map(function (x) {
        return '<tr><td>' + esc(x.name) + '</td><td>' + x.ok + ' / ' + x.n + '</td><td>' + x.score + '점</td><td>' + (x.cut ? '<span class="badge ok">통과</span>' : '<span class="badge bad">과락</span>') + '</td></tr>';
      }).join('') + '</tbody></table><p class="muted small" style="margin-top:8px">합격 기준: 과목마다 ' + D.subjectCut + '점 이상이고 평균 ' + D.averageCut + '점 이상입니다.</p></section>';
    var wrong = res.rows.filter(function (r) { return !r.ok; });
    h += '<h2>틀린 문제 ' + wrong.length + '개</h2>' + (wrong.length ? '' : '<div class="card empty">모두 맞혔습니다.</div>');
    wrong.forEach(function (r, i) {
      var q = byId[r.id];
      if (!q) return;
      h += '<article class="q"><div class="row spread" style="margin-bottom:6px"><div><span class="qno">' + (i + 1) + '.</span><span class="badge">' + esc(D.subjects[q.subject].name) + ' · ' + esc(q.topic) + '</span></div><span class="muted small">' + q.id + '</span></div>' +
        textOf(q) + '<div class="opts">' + q.options.map(function (o, k) {
          var cls = k === q.answer ? ' right' : (k === r.picked ? ' wrong' : '');
          return '<div class="opt' + cls + '"><span class="n">' + (k + 1) + '</span><span>' + esc(o) + (k === r.picked ? ' <span class="muted small">(내 답)</span>' : '') + '</span></div>';
        }).join('') + '</div>' + (r.picked === null ? '<p class="muted small">답하지 않았습니다.</p>' : '') + '<div class="explain">' + esc(q.explain) + '</div></article>';
    });
    setMain(h);
  }

  function route() {
    document.onkeydown = null;
    main.onclick = null;
    var r = parseHash();
    if (r.path === 'practice') viewPractice({ level: r.q.level, subject: r.q.subject, topic: r.q.topic, mode: r.q.mode });
    else if (r.path.indexOf('mock/') === 0) viewMock(r.path.slice(5), { 'new': r.q['new'] === '1', resume: r.q.resume === '1' });
    else if (r.path === 'result') viewResult();
    else viewHome();
  }
  window.addEventListener('hashchange', route);
  window.__written = { grade: grade, buildMock: buildMock, pickPractice: pickPractice, weakTopics: weakTopics, stats: stats, byId: byId };
  route();
})();
