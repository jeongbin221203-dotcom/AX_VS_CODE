/* 문제 풀기 기록·통계(원본 core/study.py 와 같은 계산). 기록은 브라우저 localStorage 에만 둔다.
   l.att   { 문제id: {n 시도, c 맞힌 횟수, ok 마지막 결과, f 첫 시도 결과(1/0)} }
   l.log   [ {at, key(문제id), ok} ]  — 최근 14일 그래프·연속 학습·최근 풀이용(최대 3000개)
   l.stars [ 문제id ]                  — 별표 */
(function () {
  'use strict';
  var store = EX.store;
  var P = window.EXDATA && window.EXDATA.problems;
  var F = window.EXDATA && window.EXDATA.functions;
  if (!P || !F) return;
  var byId = {};
  P.items.forEach(function (p) { byId[p.id] = p; });
  var LOG_MAX = 3000;

  function category(p) { return p.category || p.id.replace(/-\d+$/, ''); }
  function att() { return store.get('l.att', {}); }
  function stars() { return store.get('l.stars', []); }
  function problems(cat, track) {
    return P.items.filter(function (p) {
      if (cat && category(p) !== cat) return false;
      if (track && (p.tracks || []).indexOf(track) < 0) return false;
      return true;
    });
  }

  function record(id, ok) {
    var a = att(), r = a[id] || { n: 0, c: 0, ok: false };
    if (!r.n) r.f = ok ? 1 : 0;
    r.n++; r.c += ok ? 1 : 0; r.ok = !!ok;
    a[id] = r;
    store.set('l.att', a);
    var log = store.get('l.log', []);
    log.push({ at: Date.now(), key: id, ok: ok ? 1 : 0 });
    if (log.length > LOG_MAX) log = log.slice(-LOG_MAX);
    store.set('l.log', log);
  }
  /* 문제별 상태: solved(한 번이라도 맞힘) · last_ok · tries · wrong */
  function status(a) {
    var out = {};
    Object.keys(a).forEach(function (id) {
      var r = a[id];
      out[id] = { tries: r.n, solved: r.c > 0, wrong: r.n - r.c, last_ok: !!r.ok };
    });
    return out;
  }
  function toggleStar(id) {
    var s = stars(), i = s.indexOf(id);
    if (i >= 0) s.splice(i, 1); else s.push(id);
    store.set('l.stars', s);
    return i < 0;
  }

  function dayKey(t) {
    var d = new Date(t);
    return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
  }
  function streak(log) {
    var days = {};
    log.forEach(function (r) { days[dayKey(r.at)] = 1; });
    var n = 0, d = new Date();
    if (!days[dayKey(d)]) d.setDate(d.getDate() - 1);
    while (days[dayKey(d)]) { n++; d.setDate(d.getDate() - 1); }
    return n;
  }

  function dashboard(track) {
    var probs = problems('', track), st = status(att()), A = att(), log = store.get('l.log', []);
    var ids = {};
    probs.forEach(function (p) { ids[p.id] = 1; });
    var solved = probs.filter(function (p) { return st[p.id] && st[p.id].solved; }).length;
    var tried = probs.filter(function (p) { return st[p.id]; }).length;
    var ft = probs.filter(function (p) { return A[p.id] && A[p.id].f != null; });
    var acc = ft.length ? Math.round(100 * ft.filter(function (p) { return A[p.id].f; }).length / ft.length) : null;
    var cats = [];
    F.categories.forEach(function (c) {
      var items = probs.filter(function (p) { return category(p) === c[0]; });
      if (!items.length) return;
      var s = items.filter(function (p) { return st[p.id] && st[p.id].solved; }).length;
      var f2 = items.filter(function (p) { return A[p.id] && A[p.id].f != null; });
      cats.push({ key: c[0], name: c[1], kind: c[2], total: items.length, solved: s, pct: Math.round(100 * s / items.length), tried: f2.length,
        acc: f2.length ? Math.round(100 * f2.filter(function (p) { return A[p.id].f; }).length / f2.length) : null });
    });
    var days = [], per = {}, i, d;
    for (i = 13; i >= 0; i--) { d = new Date(); d.setDate(d.getDate() - i); days.push(d); per[dayKey(d)] = { ok: 0, bad: 0 }; }
    log.forEach(function (r) {
      var k = dayKey(r.at);
      if (ids[r.key] && per[k]) per[k][r.ok ? 'ok' : 'bad']++;
    });
    var tracks = Object.keys(F.tracks).map(function (k) {
      var items = problems('', k), s = items.filter(function (p) { return st[p.id] && st[p.id].solved; }).length;
      return { key: k, name: F.tracks[k], total: items.length, solved: s, pct: items.length ? Math.round(100 * s / items.length) : 0 };
    });
    var weak = cats.filter(function (c) { return c.acc !== null && c.tried >= 2; })
      .sort(function (x, y) { return x.acc - y.acc || y.tried - x.tried; }).slice(0, 3);
    var wrong = probs.filter(function (p) { return st[p.id] && !st[p.id].last_ok; });
    var tk = per[dayKey(new Date())];
    var recent = log.filter(function (r) { return byId[r.key]; }).slice(-8).reverse().map(function (r) {
      var t = new Date(r.at);
      return { pid: r.key, ok: r.ok, at: (t.getMonth() + 1) + '/' + t.getDate() + ' ' + ('0' + t.getHours()).slice(-2) + ':' + ('0' + t.getMinutes()).slice(-2), p: byId[r.key] };
    });
    return {
      total: probs.length, solved: solved, tried: tried, acc: acc, cats: cats, tracks: tracks, weak: weak, wrong_now: wrong.slice(0, 6), wrong_count: wrong.length,
      today: tk.ok + tk.bad, streak: streak(log), recent: recent,
      chart: { labels: days.map(function (x) { return (x.getMonth() + 1) + '/' + x.getDate(); }),
        series: [{ name: '정답', values: days.map(function (x) { return per[dayKey(x)].ok; }) }, { name: '오답', values: days.map(function (x) { return per[dayKey(x)].bad; }) }] }
    };
  }

  /* 안 푼 문제 → 없으면 마지막에 틀린 문제 → 없으면 null */
  function nextProblem(cat, track) {
    var st = status(att()), items = problems(cat, track), i;
    for (i = 0; i < items.length; i++) if (!st[items[i].id]) return items[i].id;
    for (i = 0; i < items.length; i++) if (!st[items[i].id].last_ok) return items[i].id;
    return null;
  }

  function catName(p) {
    var k = category(p), n = k;
    F.categories.forEach(function (c) { if (c[0] === k) n = c[1]; });
    return n;
  }

  EX.learn = { catName: catName, category: category, att: att, stars: stars, problems: problems, record: record, status: status, toggleStar: toggleStar,
    dashboard: dashboard, nextProblem: nextProblem, byId: byId };
})();
