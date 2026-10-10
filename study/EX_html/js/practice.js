/* 컴활 실기 실습: 실습 파일을 풀어 올리면 정답 파일과 비교해 채점한다(브라우저 안의 파이썬 엔진).
   교재·공식 예제는 저작물이라 저장소에 없다. 사용자가 자기 PC 의 파일(폴더·zip)을 가져오면 이 브라우저(IndexedDB)에만 보관한다. */
(function () {
  'use strict';
  var main = document.getElementById('main');
  EX.header('practice.html');
  var esc = EX.esc, store = EX.store, num = EX.num;
  var XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  var CACHE_VER = 7;                 // 채점·지문 규칙이 바뀌면 올린다(저장해 둔 할 일·지문을 다시 만든다)
  var OFFICIAL = [
    ['c2-A', 'c2', '2급 엑셀 A형 (2024~2026 예제)'], ['c2-B', 'c2', '2급 엑셀 B형 (2024~2026 예제)'],
    ['c1-A', 'c1', '1급 엑셀 A형 (2024~2026 예제)'], ['c1-B', 'c1', '1급 엑셀 B형 (2024~2026 예제)']
  ];   // 2015 연습 예제는 정답 파일이 없어 채점할 수 없으므로 뺐다
  var LEVEL = { c1: '컴활 1급', c2: '컴활 2급' };

  function prefetch() { if (EX.py.available()) EX.py.ready().catch(function () {}); }
  function results() { return store.get('p.res', []); }
  function saveResult(r) {
    var all = results();
    r.rid = all.reduce(function (m, x) { return Math.max(m, x.rid); }, 0) + 1;
    all.push(r);
    var per = {};
    all.slice().reverse().forEach(function (x) { per[x.key] = (per[x.key] || 0) + 1; if (per[x.key] > 10) x.res = null; });
    store.set('p.res', all);
    return r.rid;
  }
  function bytesOf(file) { return file.arrayBuffer().then(function (b) { return new Uint8Array(b); }); }
  function blobUrl(f, type) { return URL.createObjectURL(new Blob([f.data], { type: type || 'application/octet-stream' })); }
  function save(f) { EX.download(f.data, f.name, XLSX); }
  function hashId(text) {
    var h = 5381;
    for (var i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0;
    return h.toString(16) + text.length.toString(16);
  }
  function keyOf(it) { return it.kind + ':' + it.id; }
  function notice(box, msg, ok) { box.innerHTML = msg ? '<div class="alert' + (ok ? ' ok-alert' : '') + '" role="' + (ok ? 'status' : 'alert') + '">' + esc(msg) + '</div>' : ''; }

  /* ---------- 목록 ---------- */
  async function viewList(msg, err) {
    var items = (await EX.idb.all('practice')).sort(function (a, b) { return (a.group || '').localeCompare(b.group || '', 'ko') || (a.order || 0) - (b.order || 0) || a.title.localeCompare(b.title, 'ko', { numeric: true }); });
    var off = {}, lib = [];
    items.forEach(function (it) { if (it.kind === 'official') off[it.id] = it; else lib.push(it); });
    var best = {};
    results().forEach(function (r) { var b = best[r.key] || (best[r.key] = { score: 0, n: 0 }); b.score = Math.max(b.score, r.score); b.n++; });
    var h = '<div class="page-head"><div><h1>컴활 실기 실습</h1><p>실습 파일을 Excel 에서 풀어 올리면 <b>정답 파일과 비교</b>해 항목별로 채점합니다. 해야 할 일 목록과 힌트도 정답 파일에서 뽑아 보여 줍니다.</p></div></div>' +
      '<div id="note">' + (msg ? '<div class="alert ok-alert" role="status">' + esc(msg) + '</div>' : '') + (err ? '<div class="alert" role="alert">' + esc(err) + '</div>' : '') + '</div>';
    h += '<section class="card"><h2>대한상공회의소 공식 예제 문제</h2><p class="small muted" style="margin-top:0">실제 시험 기출은 공개되지 않습니다. 대신 대한상공회의소가 공개한 <b>2024~2026 출제 기준 예제(1·2급 엑셀 A·B형)</b>가 있습니다. ' +
      '<a href="https://license.korcham.net/co/examguide02Sub.do?cd=0103&mm=21&num=2941771" target="_blank" rel="noopener">공식 예제 페이지</a>에서 zip 을 받아 아래에서 고르면 이 브라우저에만 풀어 둡니다(저작권: 대한상공회의소 — 서버·저장소로 보내지 않음).</p>' +
      '<div class="controls"><label class="field" style="flex:1;min-width:240px">받은 zip 파일(여러 개 가능)<input type="file" id="zips" accept=".zip" multiple></label><button class="btn primary" id="zip-go" type="button">가져오기</button></div>' +
      '<div class="grid g3" style="margin-top:12px">' + OFFICIAL.map(function (o) {
        var it = off[o[0]], b = it && best['official:' + o[0]];
        if (!it) return '<div class="card cat-card pick-zip" role="button" tabindex="0" title="눌러서 받은 zip 파일 고르기" style="cursor:pointer"><div class="top-line"><h3 style="margin:0">' + esc(o[2]) + '</h3><span class="badge ' + o[1] + '">' + LEVEL[o[1]] + '</span></div><p class="muted">아직 가져오지 않음</p><div class="small"><b>눌러서 zip 파일 고르기</b></div></div>';
        return '<div class="card cat-card"><div class="top-line"><h3 style="margin:0">' + esc(o[2]) + '</h3><span class="badge ' + o[1] + '">' + LEVEL[o[1]] + '</span></div><p>' + (it.answer ? '문제지 PDF · 소스 · 정답 파일' : '문제지 PDF · 소스 파일(정답 파일 없음 — 채점 불가)') + '</p><div class="btns">' +
          (it.pdf ? '<a class="btn sm" href="' + blobUrl(it.pdf, 'application/pdf') + '" target="_blank" rel="noopener">문제지</a>' : '') +
          (it.answer ? '<a class="btn sm primary" href="#/official/' + esc(it.id) + '">풀고 채점하기</a>' : '<button class="btn sm" data-src="' + esc(it.id) + '" type="button">소스 파일</button>') +
          '</div><div class="small" style="margin-top:8px">' + (b ? '최고 <b>' + num(b.score) + '점</b> · ' + b.n + '번' : (it.answer ? '<span class="muted">미응시</span>' : '')) + '</div></div>';
      }).join('') + '</div></section>';

    h += '<section class="card"><h2>내 교재 실습 파일 <span class="small muted" style="font-weight:400">' + lib.length + '개</span></h2>' +
      '<div class="card add-pair" style="background:var(--surface-2)"><h3 style="margin:0 0 8px">실습·정답 파일 한 쌍 등록</h3><div class="controls">' +
      '<label class="field">제목<input type="text" id="a-title" placeholder="예: 제10회 기출변형문제"></label><label class="field">묶음<input type="text" id="a-group" placeholder="예: 2급 실기 기출변형"></label>' +
      '<label class="field">실습(문제) 파일<input type="file" id="a-prac" accept=".xlsx,.xlsm,.xltm"></label><label class="field">정답(완성) 파일<input type="file" id="a-ans" accept=".xlsx,.xlsm,.xltm"></label>' +
      '<label class="field">함께 쓰는 파일(선택)<input type="file" id="a-extra" multiple accept=".txt,.csv,.accdb,.xml,.prn"></label><button class="btn primary" id="a-go" type="button">등록</button></div></div>' +
      '<div class="controls"><label class="field" style="flex:1;min-width:260px">폴더째 가져오기 — 하위 폴더의 \'실습\'·\'정답\' 또는 \'실습파일\'·\'완성파일\' 짝을 찾습니다<input type="file" id="folder" webkitdirectory multiple></label><button class="btn" id="folder-go" type="button">가져오기</button></div>' +
      '<p class="small muted">교재 파일은 출판사 저작물이라 저장소에는 올리지 않았고 <b>이 브라우저(IndexedDB)에만</b> 보관합니다. 사이트 데이터를 지우면 사라지니 원본 파일은 따로 두세요. 문제 지문은 교재를 보세요 — 여기서는 정답 파일에서 뽑은 \'해야 할 일\'과 힌트를 보여 줍니다.</p><div id="prog"></div>';
    var groups = {};
    lib.forEach(function (it) { (groups[it.group || '직접 등록'] = groups[it.group || '직접 등록'] || []).push(it); });
    Object.keys(groups).forEach(function (g) {
      h += '<h3 style="margin-top:16px">' + esc(g) + '</h3><ul class="list">' + groups[g].map(function (it) {
        var b = best['lib:' + it.id];
        return '<li><div class="grow"><a class="title" href="#/lib/' + esc(it.id) + '">' + esc(it.title) + '</a><div class="small muted">' + esc(it.category || '') + (it.level ? ' · ' + LEVEL[it.level] : '') + '</div></div><span class="small nowrap">' +
          (b ? '최고 <b>' + num(b.score) + '</b>' : '<span class="muted">미제출</span>') + '</span></li>';
      }).join('') + '</ul>';
    });
    if (!lib.length) h += '<div class="empty">아직 가져온 파일이 없습니다.</div>';
    h += '</section>';
    main.innerHTML = h;
    window.scrollTo(0, 0);
    var note = document.getElementById('note'), prog = document.getElementById('prog');

    /* 안 가져온 카드를 누르면 바로 zip 고르기 창이 열리고, 고르면 곧바로 가져온다 */
    var zipsEl = document.getElementById('zips');
    main.querySelectorAll('.pick-zip').forEach(function (c) {
      c.addEventListener('click', function () { zipsEl.click(); });
      c.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); zipsEl.click(); } });
    });
    zipsEl.addEventListener('change', function () { if (zipsEl.files.length) document.getElementById('zip-go').click(); });
    document.getElementById('zip-go').addEventListener('click', async function () {
      var files = Array.prototype.slice.call(document.getElementById('zips').files);
      if (!files.length) { notice(note, '받은 zip 파일을 고르세요.'); return; }
      var got = 0;
      for (var i = 0; i < files.length; i++) {
        var r = await EX.withEngine(prog, async function (st) {
          st(files[i].name + ' 푸는 중…');
          return EX.py.call('pr_official', { zipname: files[i].name }, await bytesOf(files[i]), st);
        });
        if (!r) return;
        if (r.error) { notice(note, files[i].name + ': ' + r.error); return; }
        for (var k = 0; k < r.sets.length; k++) {
          var s = r.sets[k], dir = '/tmp/off/' + s.id + '/';
          var read = async function (name) { return name ? { name: name, data: await EX.py.readFile(dir + name) } : null; };
          await EX.idb.put('practice', {
            id: s.id, kind: 'official', title: s.title, group: '대한상공회의소 공식 예제', level: s.level, category: '공식 예제',
            practice: await read(s.files.source), answer: await read(s.files.answer), pdf: await read(s.files.pdf),
            extras: await Promise.all((s.files.extra || []).map(read)), at: Date.now()
          });
          got++;
        }
      }
      viewList(got + '세트를 가져왔습니다.');
    });
    document.getElementById('a-go').addEventListener('click', async function () {
      var p = document.getElementById('a-prac').files[0], a = document.getElementById('a-ans').files[0];
      if (!p || !a) { notice(note, '실습 파일과 정답 파일을 모두 고르세요.'); return; }
      if (!/\.(xlsx|xlsm|xltm)$/i.test(p.name) || !/\.(xlsx|xlsm|xltm)$/i.test(a.name)) { notice(note, '엑셀 파일(.xlsx·.xlsm)만 등록할 수 있습니다.'); return; }
      var pd = await bytesOf(p), ad = await bytesOf(a);
      var chk = await EX.withEngine(prog, function (st) { st('채점할 수 있는 짝인지 확인하는 중…'); return EX.py.call('pr_tasks', {}, pd, st, { 'ans.bin': ad }); });
      if (!chk) return;
      if (chk.warn) { notice(note, chk.warn); return; }
      var title = (document.getElementById('a-title').value.trim() || p.name.replace(/\.[^.]+$/, '')).slice(0, 100);
      var group = (document.getElementById('a-group').value.trim() || '직접 등록').slice(0, 100);
      var extras = [];
      for (var f of Array.prototype.slice.call(document.getElementById('a-extra').files)) extras.push({ name: f.name, data: await bytesOf(f) });
      var id = hashId(title + '|' + Date.now());
      await EX.idb.put('practice', { id: id, kind: 'lib', title: title, group: group, level: /1급/.test(title + group) ? 'c1' : /2급/.test(title + group) ? 'c2' : null, category: '직접 등록',
        practice: { name: p.name, data: pd }, answer: { name: a.name, data: ad }, extras: extras, at: Date.now(), cache: { v: CACHE_VER, tasks: chk.tasks, problem: [] } });
      location.hash = '#/lib/' + id;
    });
    document.getElementById('folder-go').addEventListener('click', async function () {
      var files = Array.prototype.slice.call(document.getElementById('folder').files);
      if (!files.length) { notice(note, '가져올 폴더를 고르세요.'); return; }
      var byPath = {}, paths = [];
      files.forEach(function (f) {
        var rel = (f.webkitRelativePath || f.name).split('/').slice(1).join('/');     // 맨 앞은 고른 폴더 이름
        if (rel) { byPath[rel] = f; paths.push(rel); }
      });
      var r = await EX.withEngine(prog, function (st) { st('실습·정답 짝을 찾는 중…'); return EX.py.call('pr_scan', { paths: paths }, null, st); });
      if (!r) return;
      if (!r.pairs.length) { notice(note, '\'실습\'·\'정답\'(또는 \'실습파일\'·\'완성파일\') 폴더 짝을 찾지 못했습니다.'); return; }
      for (var i = 0; i < r.pairs.length; i++) {
        var p = r.pairs[i];
        prog.innerHTML = '<div class="card engine-status"><span class="spin"></span><span>' + (i + 1) + ' / ' + r.pairs.length + ' 저장하는 중… ' + esc(p.title) + '</span></div>';
        var rd = async function (rel) { return { name: rel.split('/').pop(), data: await bytesOf(byPath[rel]) }; };
        await EX.idb.put('practice', { id: hashId(p.rel), kind: 'lib', title: p.title, group: p.group || '가져온 교재', level: p.level, category: p.category, order: i,
          practice: await rd(p.practice), answer: await rd(p.answer), extras: await Promise.all(p.extras.map(rd)), at: Date.now() });
      }
      viewList('실습·정답 파일 ' + r.pairs.length + '쌍을 가져왔습니다.');
    });
    main.querySelectorAll('[data-src]').forEach(function (b) {
      b.addEventListener('click', function () { var it = off[b.getAttribute('data-src')]; if (it && it.practice) save(it.practice); });
    });
    prefetch();
  }

  /* ---------- 항목 하나 ---------- */
  var timerHandle = null;
  async function viewItem(kind, id, error) {
    var it = await EX.idb.get('practice', id);
    if (!it || it.kind !== kind) { main.innerHTML = '<div class="card empty">항목을 찾을 수 없습니다.<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    main.innerHTML = '<div id="box"></div>';
    var box = document.getElementById('box');
    var cache = it.cache && it.cache.v === CACHE_VER && (kind !== 'lib' || it.cache.described) ? it.cache : null;
    if (!cache) {
      var r = await EX.withEngine(box, function (st) {
        st('정답 파일에서 해야 할 일을 뽑는 중…');
        return EX.py.call('pr_tasks', { describe: kind === 'lib' }, it.practice.data, st, { 'ans.bin': it.answer.data });
      });
      if (!r) return;
      cache = { v: CACHE_VER, tasks: r.tasks, problem: r.problem, described: kind === 'lib', warn: r.warn };
      if (!r.warn) { it.cache = cache; EX.idb.put('practice', it); }
    }
    var key = keyOf(it), minutes = it.level === 'c2' ? 40 : 45;
    var hist = results().filter(function (r) { return r.key === key; }).reverse().slice(0, 10);
    var h = '<div class="page-head"><div><div class="crumb"><a href="#/">컴활 실기 실습</a> › ' + esc(it.group) + '</div><h1>' + esc(it.title) + '</h1><p>' + (it.level ? LEVEL[it.level] + ' · ' : '') + '실습 파일을 풀어 올리면 정답 파일과 비교합니다.</p></div><div class="btns">' +
      (it.pdf ? '<a class="btn" href="' + blobUrl(it.pdf, 'application/pdf') + '" target="_blank" rel="noopener">문제지 PDF</a>' : '') + '<button class="btn primary" id="dl-prac" type="button">실습 파일 받기</button></div></div>' +
      '<div id="err">' + (error ? '<div class="alert" role="alert">' + esc(error) + '</div>' : '') + (cache.warn ? '<div class="alert info" role="status">' + esc(cache.warn) + '</div>' : '') + '</div>' +
      '<section class="card exam-bar"><div class="timer" id="timer"><span class="small muted">시간</span> <b id="timer-text">' + minutes + ':00</b></div><div class="btns"><button class="btn" id="t-start" type="button">시작</button><button class="btn ghost" id="t-reset" type="button">초기화</button></div>' +
      '<div class="exam-upload"><input type="file" id="ans" accept=".xlsx,.xlsm" aria-label="내 답안 파일"><button class="btn primary" id="submit" type="button">제출·채점</button></div></section>';
    if (it.extras && it.extras.length) h += '<p class="small">함께 쓰는 파일: ' + it.extras.map(function (e, i) { return '<a href="#" data-extra="' + i + '">' + esc(e.name) + '</a>'; }).join(', ') + '</p>';
    if (cache.problem && cache.problem.length) {
      h += '<section class="card exam-section"><h2>문제 <span class="small muted" style="font-weight:400">정답 파일에서 자동으로 만든 지문 — 교재 지문과 표현이 다를 수 있습니다</span></h2>' + cache.problem.map(function (s) {
        return '<div class="exam-task"><h3>' + esc(s.sheet) + '</h3><ol class="exam-items">' + s.tasks.map(function (t, i) {
          return '<li><span class="num">' + (i + 1) + '.</span><div>' + esc(t.text) +
            (t.table ? '<div class="table-wrap" style="max-width:780px;margin:6px 0"><table class="t sheet-like"><tbody>' + t.table.map(function (row) { return '<tr>' + row.map(function (v) { return '<td>' + esc(v) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>' : '') +
            (t.items && t.items.length ? '<ul class="small" style="margin:4px 0 0;padding-left:1.1em">' + t.items.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '') + '</div></li>';
        }).join('') + '</ol></div>';
      }).join('') + '</section>';
    }
    h += '<section class="card"><h2>' + (cache.problem && cache.problem.length ? '채점 항목' : '해야 할 일') + ' <span class="small muted" style="font-weight:400">정답 파일과 실습 파일의 차이에서 뽑음 · 힌트는 눌러서 보기</span></h2>' +
      (cache.tasks.length ? cache.tasks.map(function (s) {
        return '<h3 style="margin-top:14px">' + esc(s.name) + (s.points ? ' <span class="small muted" style="font-weight:400">' + (s.points === Math.floor(s.points) ? s.points : s.points.toFixed(1)) + '점</span>' : '') + '</h3><ul class="list">' + s.items.map(hintItem).join('') + '</ul>';
      }).join('') : '<div class="empty">정답 파일과 다른 점을 찾지 못했습니다.</div>') +
      '<p class="small muted" style="margin-bottom:0">점수는 공식 배점(시트별)을 항목 수로 나눈 추정치입니다. 정답 파일과 다른 방법으로 같은 결과를 낸 경우 일부 항목이 다르게 나올 수 있습니다. <a href="#" id="dl-ans">정답 파일 받기</a></p></section>';
    if (kind === 'lib') h += '<p><button class="btn sm ghost danger" id="del" type="button">이 항목 지우기</button></p>';
    h += '<section class="card"><h2>제출 기록</h2>' + (hist.length ? '<ul class="list">' + hist.map(function (r) {
      return '<li><div class="grow">' + (r.res ? '<a href="#/result/' + r.rid + '"><b>' + num(r.score) + '</b> / ' + num(r.total) + '</a>' : '<b>' + num(r.score) + '</b> / ' + num(r.total)) + ' <span class="small muted">' + esc(r.file) + '</span></div><span class="small muted nowrap">' + EX.fmtDate(r.at) + '</span></li>';
    }).join('') + '</ul>' : '<div class="empty">아직 없습니다.</div>') + '</section>';
    box.innerHTML = h;
    window.scrollTo(0, 0);

    document.getElementById('dl-prac').addEventListener('click', function () { save(it.practice); });
    document.getElementById('dl-ans').addEventListener('click', function (e) { e.preventDefault(); if (window.confirm('정답 파일을 먼저 보면 연습 효과가 줄어듭니다. 받을까요?')) save(it.answer); });
    box.querySelectorAll('[data-extra]').forEach(function (a) { a.addEventListener('click', function (e) { e.preventDefault(); var f = it.extras[Number(a.getAttribute('data-extra'))]; EX.download(f.data, f.name); }); });
    var del = document.getElementById('del');
    if (del) del.addEventListener('click', async function () { if (window.confirm('이 실습 파일 쌍을 지울까요?')) { await EX.idb.remove('practice', it.id); location.hash = '#/'; } });

    var tkey = 'p.start.' + key, text = document.getElementById('timer-text'), wrap = document.getElementById('timer'), startBtn = document.getElementById('t-start');
    function tick() {
      var start = store.get(tkey, 0);
      if (!start) { text.textContent = minutes + ':00'; startBtn.disabled = false; wrap.classList.remove('over'); return null; }
      startBtn.disabled = true;
      var used = Math.floor((Date.now() - start) / 1000), left = minutes * 60 - used, a = Math.abs(left);
      wrap.classList.toggle('over', left < 0);
      text.textContent = (left < 0 ? '시간 초과 +' : '') + Math.floor(a / 60) + ':' + (a % 60 < 10 ? '0' : '') + (a % 60);
      return used;
    }
    clearInterval(timerHandle);
    startBtn.addEventListener('click', function () { store.set(tkey, Date.now()); prefetch(); tick(); });
    document.getElementById('t-reset').addEventListener('click', function () { if (store.get(tkey, 0) && !window.confirm('시간을 처음으로 되돌릴까요?')) return; store.remove(tkey); tick(); });
    tick();
    timerHandle = setInterval(tick, 1000);

    var fileEl = document.getElementById('ans'), errEl = document.getElementById('err'), btn = document.getElementById('submit');
    fileEl.addEventListener('change', prefetch);
    btn.addEventListener('click', async function () {
      var f = fileEl.files[0];
      if (!f) { notice(errEl, '답안 파일을 고르세요.'); return; }
      if (!/\.(xlsx|xlsm|xltm)$/i.test(f.name)) { notice(errEl, '.xlsx 또는 .xlsm 파일을 올려 주세요.'); return; }
      var used = tick();
      btn.disabled = true;
      var res = await EX.withEngine(errEl, async function (st) {
        var ub = await bytesOf(f);
        st('채점하는 중… (파일 크기에 따라 몇 초 걸립니다)');
        return EX.py.call('pr_grade', { name: f.name, level: kind === 'official' ? it.level : null }, it.practice.data, st, { 'ans.bin': it.answer.data, 'user.bin': ub });
      });
      btn.disabled = false;
      if (!res) return;
      if (res.error) { notice(errEl, res.error); return; }
      var secs = used == null ? null : Math.max(0, Math.min(used, 6 * 3600));
      var rid = saveResult({ key: key, score: res.res.score, total: res.res.total, passed: !!res.res.passed, seconds: secs, file: f.name.slice(0, 200), at: Date.now(), res: res.res });
      store.remove(tkey);
      location.hash = '#/result/' + rid;
    });
  }
  function hintItem(t) {
    return '<li><div class="grow">' + esc(t.label) + (t.hint || t.code ? '<details><summary>힌트</summary>' + (t.hint ? '<div class="small mono" style="white-space:pre-wrap">' + esc(t.hint) + '</div>' : '') + (t.code ? '<pre class="small mono">' + esc(t.code) + '</pre>' : '') + '</details>' : '') + '</div></li>';
  }

  /* ---------- 결과 ---------- */
  async function viewResult(rid) {
    var row = results().filter(function (r) { return r.rid === rid; })[0];
    if (!row || !row.res) { main.innerHTML = '<div class="card empty">채점 결과를 찾을 수 없습니다(오래된 기록은 점수만 남습니다).<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    var parts = row.key.split(':'), kind = parts[0], id = parts.slice(1).join(':');
    var it = await EX.idb.get('practice', id);
    var title = it ? it.title : id, res = row.res, href = '#/' + kind + '/' + encodeURIComponent(id);
    var h = '<div class="page-head"><div><div class="crumb"><a href="#/">컴활 실기 실습</a> › <a href="' + href + '">' + esc(title) + '</a> ›</div><h1>채점 결과</h1><p>' + esc(row.file) + ' · ' + EX.fmtDate(row.at) +
      (row.seconds ? ' · ' + Math.floor(row.seconds / 60) + '분 ' + (row.seconds % 60) + '초' : '') + '</p></div><a class="btn primary" href="' + href + '">다시 풀기</a></div>' +
      '<div class="grid g4"><div class="kpi"><div class="label">점수(추정)</div><div class="value">' + num(res.score) + '<small> / ' + num(res.total) + '</small></div><div class="sub">' +
      (res.level ? (res.passed ? '<span class="st ok">✓</span> 합격선 이상' : '<span class="st bad">✕</span> 합격선 70점 미달') : '일치한 항목 비율') + '</div></div>' +
      res.sections.map(function (s) {
        return '<div class="kpi"><div class="label">' + esc(s.name) + '</div><div class="value">' + num(s.got) + '<small> / ' + num(s.points) + '</small></div><div class="meter" style="margin-top:8px" aria-hidden="true"><i style="width:' + (s.points ? Math.round(100 * s.got / s.points) : 0) + '%"></i></div></div>';
      }).join('') + '</div><section class="card" style="margin-top:14px">' + res.sheets.filter(function (s) { return s.items.length; }).map(function (s) {
        var okN = s.items.filter(function (i) { return i.ok; }).length;
        return '<div class="check-item"><div class="head">' + (okN === s.items.length ? '<span class="st ok">✓</span>' : okN ? '<span class="st new">△</span>' : '<span class="st bad">✕</span>') +
          '<b>' + esc(s.name) + '</b> <span class="small muted">' + okN + ' / ' + s.items.length + ' 항목 · ' + num(s.got) + ' / ' + num(s.points) + '점</span></div><ul class="list" style="margin-left:32px">' +
          s.items.map(function (i) {
            return '<li><span class="st ' + (i.ok ? 'ok' : 'bad') + '">' + (i.ok ? '✓' : '✕') + '</span><div class="grow"><div>' + esc(i.label) + '</div>' +
              (!i.ok && i.msgs && i.msgs.length ? '<div class="small" style="color:var(--bad)">' + i.msgs.map(esc).join(' · ') + '</div>' : '') +
              (!i.ok && (i.hint || i.code) ? '<details><summary>힌트</summary>' + (i.hint ? '<div class="small mono" style="white-space:pre-wrap">' + esc(i.hint) + '</div>' : '') + (i.code ? '<pre class="small mono">' + esc(i.code) + '</pre>' : '') + '</details>' : '') + '</div></li>';
          }).join('') + '</ul></div>';
      }).join('') + '</section>';
    main.innerHTML = h;
    window.scrollTo(0, 0);
  }

  function route() {
    clearInterval(timerHandle);
    var hash = location.hash.replace(/^#\/?/, '');
    var m = /^result\/(\d+)$/.exec(hash), k = /^(lib|official)\/(.+)$/.exec(hash);
    if (m) viewResult(Number(m[1]));
    else if (k) viewItem(k[1], decodeURIComponent(k[2]));
    else viewList();
  }
  window.addEventListener('hashchange', route);

  /* 내 PC 용 자료(data/private.js — tools/pack_private.py 가 만듦)가 있으면 아직 넣지 않은 항목을 브라우저에 자동으로 넣는다.
     한 번 넣은 항목은 기록해 두어, 목록에서 지운 것이 다시 살아나지 않게 한다. */
  function unb64(s) { var bin = atob(s), u = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; }
  async function seedPrivate() {
    var P = window.EXPRIVATE;
    if (!P || !P.items) return;
    var rec = await EX.idb.get('meta', 'seeded').catch(function () { return null; }), done = (rec && rec.list) || [], seen = {};   // 기록은 자료와 같은 곳(IndexedDB)에 둔다 — 백업·동기화로 다른 브라우저에 번지지 않게
    done.forEach(function (k) { seen[k] = 1; });
    var fresh = P.items.filter(function (it) { return !seen[it.kind + ':' + it.id]; });
    if (!fresh.length) return;
    main.innerHTML = '<div class="card engine-status"><span class="spin"></span><span>내 자료 ' + fresh.length + '개를 이 브라우저에 넣는 중…</span></div>';
    var file = function (f) { return f ? { name: f.name, data: unb64(f.b64) } : null; };
    for (var i = 0; i < fresh.length; i++) {
      var it = fresh[i];
      await EX.idb.put('practice', { id: it.id, kind: it.kind, title: it.title, group: it.group, level: it.level, category: it.category, order: it.order,
        practice: file(it.practice), answer: file(it.answer), pdf: file(it.pdf), extras: (it.extras || []).map(file), at: Date.now() });
      done.push(it.kind + ':' + it.id);
    }
    await EX.idb.put('meta', { id: 'seeded', list: done });
  }
  seedPrivate().catch(function () { /* 자료를 못 넣어도 화면은 연다 */ }).then(route);
})();
