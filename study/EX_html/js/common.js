/* 공통: 저장소, 머리글(메뉴), 테마, 도우미 */
(function () {
  'use strict';
  var EX = window.EX = window.EX || {};
  var PREFIX = 'exh:';

  /* 학습 기록은 이 브라우저의 localStorage 에만 저장된다(서버 없음). 막혀 있으면 이번 탭 메모리에만 둔다. */
  var mem = {};
  EX.store = {
    get: function (key, fallback) {
      try {
        var raw = window.localStorage.getItem(PREFIX + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (e) {
        return key in mem ? mem[key] : fallback;
      }
    },
    set: function (key, value) {
      mem[key] = value;
      try { window.localStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch (e) { /* 저장 불가 — 메모리에만 */ }
    },
    remove: function (key) {
      delete mem[key];
      try { window.localStorage.removeItem(PREFIX + key); } catch (e) { /* 무시 */ }
    },
    keys: function () {
      var out = [];
      try {
        for (var i = 0; i < window.localStorage.length; i++) {
          var k = window.localStorage.key(i);
          if (k && k.indexOf(PREFIX) === 0) out.push(k.slice(PREFIX.length));
        }
      } catch (e) { out = Object.keys(mem); }
      return out;
    }
  };

  EX.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };

  /* 시드를 줄 수 있는 난수(테스트에서 같은 문제지를 만들 수 있게) */
  EX.rng = function (seed) {
    if (seed == null) return Math.random;
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  };
  EX.shuffle = function (arr, rnd) {
    rnd = rnd || Math.random;
    var a = arr.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(rnd() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  };
  EX.fmtTime = function (sec) {
    sec = Math.max(0, Math.round(sec));
    var m = Math.floor(sec / 60), s = sec % 60;
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  };
  EX.fmtDate = function (ts) {
    var d = new Date(ts);
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  };

  /* 테마 */
  var theme = EX.store.get('theme', null);
  if (theme) document.documentElement.setAttribute('data-theme', theme);

  var PAGES = [
    ['index.html', '홈'], ['learn.html', '문제 풀기'], ['functions.html', '함수 사전'], ['shortcuts.html', '단축키'],
    ['written.html', '컴활 필기']
  ];
  EX.header = function (active) {
    var nav = PAGES.map(function (p) {
      return '<a href="' + p[0] + '"' + (p[0] === active ? ' class="on" aria-current="page"' : '') + '>' + p[1] + '</a>';
    }).join('');
    var el = document.getElementById('site-header');
    if (!el) return;
    el.innerHTML = '<div class="top-in"><a class="brand" href="index.html"><span class="brand-mark" aria-hidden="true">X</span>엑셀 연습장</a>' +
      '<nav class="nav" aria-label="주 메뉴">' + nav + '</nav>' +
      '<button class="theme-btn" type="button" id="theme-btn" title="밝게/어둡게" aria-label="밝게 또는 어둡게 바꾸기">◐</button></div>';
    document.getElementById('theme-btn').addEventListener('click', function () {
      var cur = document.documentElement.getAttribute('data-theme');
      var dark = cur ? cur === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
      var next = dark ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      EX.store.set('theme', next);
    });
  };

  /* 기록 백업·복원(이 브라우저의 기록을 파일로) */
  EX.exportAll = function () {
    var out = {};
    EX.store.keys().forEach(function (k) { if (k !== 'theme') out[k] = EX.store.get(k); });
    var blob = new Blob([JSON.stringify({ app: 'excel-html', version: 1, at: Date.now(), data: out })], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'excel-html-기록-' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  };
  EX.importAll = function (file, done) {
    var r = new FileReader();
    r.onload = function () {
      try {
        var doc = JSON.parse(String(r.result));
        if (!doc || doc.app !== 'excel-html' || typeof doc.data !== 'object') throw new Error('형식');
        var n = 0;
        Object.keys(doc.data).forEach(function (k) { EX.store.set(k, doc.data[k]); n++; });
        done(null, n);
      } catch (e) { done('이 앱에서 내려받은 기록 파일이 아닙니다.'); }
    };
    r.onerror = function () { done('파일을 읽지 못했습니다.'); };
    r.readAsText(file);
  };

  document.addEventListener('DOMContentLoaded', function () {
    var f = document.getElementById('site-footer');
    if (f) f.innerHTML = '개인 학습용 · 문제와 데이터는 모두 새로 만든 연습용입니다. 컴활 범위 표시는 출제 경향을 참고한 분류이며 공식 자료가 아닙니다. 학습 기록은 이 브라우저에만 저장됩니다.';
  });
})();
