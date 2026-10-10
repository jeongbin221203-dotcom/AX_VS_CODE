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
      if (EX.store.onChange && !/^(theme|sync\..*)$/.test(key)) EX.store.onChange(key);
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
    ['index.html', '홈'], ['learn.html', '문제 풀기'], ['written.html', '컴활 필기'], ['exam.html', '실기 모의고사'],
    ['practice.html', '실기 실습'], ['build.html', '대시보드 실습'], ['analyze.html', '파일 분석'], ['functions.html', '함수 사전'], ['shortcuts.html', '단축키']
  ];
  /* |num 필터와 같은 숫자 표시(천 단위 쉼표, 정수가 아니면 소수 2자리까지) */
  EX.num = function (v, digits) {
    if (v === null || v === undefined || v === '') return '–';
    if (typeof v === 'string') return v;
    digits = digits || 0;
    if (digits === 0 && v !== Math.trunc(v)) return v.toLocaleString('en-US', { maximumFractionDigits: 2 });
    return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  };

  /* 파이썬 엔진을 부르는 동안 화면에 진행 상태를 보여 주고, 실패하면 이유를 알려 준다 */
  EX.withEngine = async function (box, fn) {
    var msg = '엔진을 불러오는 중…';
    var tick = function (m) { msg = m; if (box) box.innerHTML = '<div class="card engine-status"><span class="spin"></span><span>' + EX.esc(m) + '</span></div>'; };
    if (!EX.py || !EX.py.available()) {
      if (box) box.innerHTML = '<div class="card"><b>이 기능은 웹 주소(http)로 열어야 합니다.</b><p class="muted small" style="margin:6px 0 0">엑셀 파일을 채점·분석하는 파이썬 엔진은 브라우저 보안 규칙상 파일을 직접 연 상태(file://)에서는 실행되지 않습니다. 폴더의 <code>start.bat</code> 으로 열거나(또는 <code>python -m http.server</code>) 인터넷에 올린 주소로 접속하세요.</p></div>';
      return null;
    }
    tick(msg);
    try { var r = await fn(tick); if (box) box.innerHTML = ''; return r; }
    catch (e) { if (box) box.innerHTML = '<div class="alert" role="alert">' + EX.esc(e && e.message ? e.message : String(e)) + '</div>'; return null; }
  };


  /* ---------- 큰 자료 보관(IndexedDB): 분석한 표·실습 파일·동기화 파일 핸들 ---------- */
  var STORES = ['uploads', 'practice', 'meta'];
  var memIdb = {};
  function idbOpen() {
    return new Promise(function (ok, bad) {
      try {
        var rq = indexedDB.open('excel-html', 2);
        rq.onupgradeneeded = function () {
          STORES.forEach(function (n) { if (!rq.result.objectStoreNames.contains(n)) rq.result.createObjectStore(n, { keyPath: 'id' }); });
        };
        rq.onsuccess = function () { ok(rq.result); };
        rq.onerror = function () { bad(rq.error); };
      } catch (e) { bad(e); }
    });
  }
  function tx(store, mode, fn) {
    return idbOpen().then(function (db) {
      return new Promise(function (ok, bad) {
        var t = db.transaction(store, mode), req = fn(t.objectStore(store));
        t.oncomplete = function () { db.close(); ok(req ? req.result : undefined); };
        t.onerror = function () { bad(t.error); };
      });
    });
  }
  EX.idb = {
    all: function (store) {
      return tx(store, 'readonly', function (os) { return os.getAll(); })
        .catch(function () { return Object.keys(memIdb[store] || {}).map(function (k) { return memIdb[store][k]; }); });
    },
    get: function (store, id) {
      return EX.idb.all(store).then(function (rows) { return rows.filter(function (r) { return r.id === id; })[0]; });
    },
    put: function (store, rec) {
      (memIdb[store] = memIdb[store] || {})[rec.id] = rec;
      return tx(store, 'readwrite', function (os) { return os.put(rec); }).catch(function () { /* 이번 탭 메모리에만 */ });
    },
    remove: function (store, id) {
      if (memIdb[store]) delete memIdb[store][id];
      return tx(store, 'readwrite', function (os) { return os.delete(id); }).catch(function () { /* 무시 */ });
    }
  };

  /* ---------- 기록 합치기 / 기기 간 동기화 ---------- */
  var SKIP = /^(theme|w\.last|l\.filter|w\.level|w\.mock\..*|x\.start\..*|sync\..*)$/;
  function syncData() {
    var out = {};
    EX.store.keys().forEach(function (k) { if (!SKIP.test(k)) out[k] = EX.store.get(k); });
    return out;
  }
  function ident(x) {
    if (x && typeof x === 'object') return [x.at || '', x.exam || x.task || x.key || x.level || '', x.file || '', x.score != null ? x.score : (x.average != null ? x.average : '')].join('|');
    return JSON.stringify(x);
  }
  /* 두 기록을 합친다: 푼 기록은 많이 푼 쪽, 목록은 합집합(같은 항목은 한 번만) */
  EX.mergeData = function (mine, theirs) {
    var out = {};
    Object.keys(mine).concat(Object.keys(theirs)).forEach(function (k) {
      if (k in out || SKIP.test(k)) return;
      var a = mine[k], b = theirs[k];
      if (b === undefined) { out[k] = a; return; }
      if (a === undefined) { out[k] = b; return; }
      if (/\.att$/.test(k) && a && b && typeof a === 'object' && typeof b === 'object') {
        var m = {};
        Object.keys(a).concat(Object.keys(b)).forEach(function (id) {
          var x = a[id], y = b[id];
          m[id] = !y ? x : !x ? y : ((y.n || 0) > (x.n || 0) ? y : x);
        });
        out[k] = m;
      } else if (Array.isArray(a) && Array.isArray(b)) {
        var seen = {}, all = [];
        a.concat(b).forEach(function (x) { var id = ident(x); if (!seen[id]) { seen[id] = 1; all.push(x); } });
        if (all.length && all.every(function (x) { return x && typeof x === 'object' && 'at' in x; })) all.sort(function (p, q) { return p.at - q.at; });
        if (all.some(function (x) { return x && typeof x === 'object' && 'rid' in x; })) all.forEach(function (x, i) { x.rid = i + 1; });
        out[k] = k === 'w.recent' ? all.slice(-200) : all;
      } else { out[k] = a; }
    });
    return out;
  };
  EX.applyData = function (data) {
    var n = 0;
    Object.keys(data).forEach(function (k) {
      if (SKIP.test(k) || JSON.stringify(EX.store.get(k)) === JSON.stringify(data[k])) return;
      EX.store.set(k, data[k]); n++;
    });
    return n;
  };

  var canFile = typeof window.showOpenFilePicker === 'function' && typeof window.showSaveFilePicker === 'function';
  var syncState = { text: '' };
  var syncing = false;
  EX.sync = {
    supported: canFile,
    state: syncState,
    handle: function () { return EX.idb.get('meta', 'sync').then(function (r) { return r && r.handle; }).catch(function () { return null; }); },
    connect: async function (create) {
      var opts = { types: [{ description: '엑셀 연습장 동기화 파일', accept: { 'application/json': ['.json'] } }] };
      var h;
      if (create) { opts.suggestedName = 'excel-html-sync.json'; h = await window.showSaveFilePicker(opts); }
      else { h = (await window.showOpenFilePicker(opts))[0]; }
      await EX.idb.put('meta', { id: 'sync', handle: h });
      return EX.sync.run(true);
    },
    disconnect: function () { return EX.idb.remove('meta', 'sync').then(function () { syncState.text = ''; }); },
    /* 연결된 파일과 합친다. 권한이 없으면 사용자 동작(버튼) 안에서만 다시 묻는다. */
    run: async function (ask) {
      if (syncing) return { ok: false, reason: 'busy' };
      var h = await EX.sync.handle();
      if (!h) return { ok: false, reason: 'none' };
      syncing = true;
      try {
        var perm = await h.queryPermission({ mode: 'readwrite' });
        if (perm !== 'granted' && ask) perm = await h.requestPermission({ mode: 'readwrite' });
        if (perm !== 'granted') { syncState.text = '동기화 파일 권한이 필요합니다 — 홈에서 [지금 동기화]를 누르세요.'; return { ok: false, reason: 'perm' }; }
        var text = await (await h.getFile()).text(), theirs = {};
        if (text.trim()) {
          var doc = JSON.parse(text);
          if (!doc || doc.app !== 'excel-html' || typeof doc.data !== 'object') throw new Error('동기화 파일이 아닙니다.');
          theirs = doc.data;
        }
        var merged = EX.mergeData(syncData(), theirs);
        var changed = EX.applyData(merged);
        var w = await h.createWritable();
        await w.write(JSON.stringify({ app: 'excel-html', version: 1, at: Date.now(), data: merged }));
        await w.close();
        syncState.text = '동기화됨 ' + EX.fmtDate(Date.now()) + (changed ? ' · 새 기록 ' + changed + '묶음 반영' : '');
        return { ok: true, changed: changed };
      } catch (e) {
        syncState.text = '동기화 실패: ' + (e && e.message ? e.message : e);
        return { ok: false, reason: 'error', message: syncState.text };
      } finally { syncing = false; }
    }
  };
  /* 기록이 바뀐 뒤 4초 뒤, 그리고 1분마다 조용히 맞춘다(권한이 이미 있을 때만) */
  var syncTimer;
  EX.store.onChange = function () { if (!canFile) return; clearTimeout(syncTimer); syncTimer = setTimeout(function () { EX.sync.run(false); }, 4000); };
  if (canFile) setInterval(function () { EX.sync.run(false); }, 60000);

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
  EX.importAll = function (file, done, merge) {
    var r = new FileReader();
    r.onload = function () {
      try {
        var doc = JSON.parse(String(r.result));
        if (!doc || doc.app !== 'excel-html' || typeof doc.data !== 'object') throw new Error('형식');
        var n = 0;
        if (merge) n = EX.applyData(EX.mergeData(syncData(), doc.data));
        else Object.keys(doc.data).forEach(function (k) { EX.store.set(k, doc.data[k]); n++; });
        done(null, n);
      } catch (e) { done('이 앱에서 내려받은 기록 파일이 아닙니다.'); }
    };
    r.onerror = function () { done('파일을 읽지 못했습니다.'); };
    r.readAsText(file);
  };

  document.addEventListener('DOMContentLoaded', function () {
    if (canFile) {
      EX.sync.run(false).then(function (r) {
        var f2 = document.getElementById('site-footer');
        if (r && r.ok && f2) f2.insertAdjacentHTML('beforeend', '<div>' + EX.esc(syncState.text) + '</div>');
      });
    }
    var f = document.getElementById('site-footer');
    if (f) f.innerHTML = '개인 학습용 · 문제와 데이터는 모두 새로 만든 연습용입니다. 컴활 범위 표시는 출제 경향을 참고한 분류이며 공식 자료가 아닙니다. 학습 기록은 이 브라우저에만 저장됩니다.';
  });
})();
