/* 컴활 실기 모의고사: 문제지 → 문제 파일 내려받기 → 답안 파일 올리기 → 항목별 채점(브라우저 안의 파이썬 엔진) */
(function () {
  'use strict';
  var main = document.getElementById('main');
  EX.header('exam.html');
  var esc = EX.esc, store = EX.store, num = EX.num;
  var XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  var LEVEL_NOTE = {
    c2: '엑셀 40분 · 기본 20 · 계산 40 · 분석 20 · 기타 20', c1: '엑셀 45분 · 기본 15 · 계산 30 · 분석 20 · 기타 35'
  };

  function results() { return store.get('x.res', []); }
  function saveResult(r) {
    var all = results();
    r.rid = (all.reduce(function (m, x) { return Math.max(m, x.rid); }, 0)) + 1;
    all.push(r);
    // 시험마다 최근 10번만 자세히 보관(저장 공간 절약)
    var per = {};
    all.slice().reverse().forEach(function (x) { per[x.exam] = (per[x.exam] || 0) + 1; if (per[x.exam] > 10) x.res = null; });
    store.set('x.res', all);
    return r.rid;
  }
  function best() {
    var b = {};
    results().forEach(function (r) {
      var o = b[r.exam] || (b[r.exam] = { score: 0, n: 0, passed: false });
      o.score = Math.max(o.score, r.score); o.n++; o.passed = o.passed || r.passed;
    });
    return b;
  }

  var listCache = null, paperCache = {};
  async function list(status) { return listCache || (listCache = await EX.py.call('exam_list', {}, null, status)); }
  async function paper(id, status) { return paperCache[id] || (paperCache[id] = await EX.py.call('exam_paper', { id: id }, null, status)); }

  /* ---------- 목록 ---------- */
  async function viewList() {
    main.innerHTML = '<div class="page-head"><div><h1>컴활 실기 모의고사</h1><p>실제 시험과 같은 구성·배점·지시문 형식으로 새로 만든 문제입니다. 문제 파일을 내려받아 Excel 에서 풀고, 저장한 파일을 올리면 항목별로 채점합니다.</p></div></div><div id="box"></div>';
    var box = document.getElementById('box');
    var data = await EX.withEngine(box, function (s) { return list(s); });
    if (!data) return;
    var B = best();
    var h = '';
    data.groups.forEach(function (g) {
      h += '<h2 style="margin-top:18px">' + esc(g.name) + ' <span class="small muted" style="font-weight:400">' + LEVEL_NOTE[g.level] + ' · 70점 이상 합격</span></h2><div class="grid g3">';
      h += g.items.map(function (e) {
        var b = B[e.id];
        return '<a class="card cat-card" href="#/' + e.id + '"><div class="top-line"><h3 style="margin:0">' + esc(e.title) + '</h3><span class="small muted">' + e.minutes + '분</span></div><p>' +
          Object.keys(e.sections).map(function (s) { return esc(s) + ' ' + e.sections[s]; }).join(' · ') + '</p><div class="small">' +
          (b ? '최고 <b>' + num(b.score) + '점</b> · ' + b.n + '번 응시 ' + (b.passed ? '<span class="badge work">합격선 통과</span>' : '') : '<span class="muted">아직 응시하지 않음</span>') + '</div></a>';
      }).join('') || '<div class="empty">준비 중입니다.</div>';
      h += '</div>';
    });
    h += '<section class="card" style="margin-top:16px"><h2>채점 방식</h2><ul class="small" style="margin:0;padding-left:1.2em">' +
      '<li>수식은 값과 함께 지시된 함수를 썼는지, 배열 수식인지까지 봅니다. 값만 쳐 넣으면 오답입니다.</li>' +
      '<li>조건부 서식·고급 필터는 규칙 수식을 행마다 계산해서 서식/추출 대상이 맞는지 비교하므로, 같은 뜻의 다른 수식도 정답입니다.</li>' +
      '<li>피벗·부분합·시나리오·데이터 표·목표값 찾기·차트·페이지 설정·시트 보호·유효성 검사는 파일 속 설정을 읽어 확인합니다.</li>' +
      '<li>매크로·VBA 는 .xlsm 으로 저장해야 코드와 단추를 확인할 수 있습니다. VBA 는 지시한 코드가 들어 있는지(문자 패턴)만 봅니다.</li>' +
      '<li>채점은 이 브라우저 안에서 일어납니다. 올린 파일은 어디로도 전송되지 않습니다.</li>' +
      '<li>실제 시험 기출 문제가 아니며, 실제 시험의 채점 기준과 다를 수 있습니다.</li></ul></section>';
    box.innerHTML = h;
  }

  /* ---------- 문제지 ---------- */
  var timerHandle = null;
  async function viewPaper(id, error) {
    main.innerHTML = '<div id="box"></div>';
    var box = document.getElementById('box');
    var e = await EX.withEngine(box, function (s) { return paper(id, s); });
    if (!e) return;
    if (e.error) { box.innerHTML = '<div class="card empty">' + esc(e.error) + '<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    var hist = results().filter(function (r) { return r.exam === id; }).reverse().slice(0, 10);
    var h = '<div class="page-head"><div><div class="crumb"><a href="#/">컴활 실기 모의고사</a> ›</div><h1>' + esc(e.title) + '</h1><p>' + esc(e.levelName) + ' · 시험 시간 ' + e.minutes + '분 · 100점 만점 ' + e.pass + '점 이상 합격</p></div>' +
      '<div class="btns"><button class="btn primary" id="dl-main" type="button">문제 파일 내려받기' + (e.forms.length ? ' (.xlsm)' : '') + '</button>' +
      e.dataFiles.map(function (n) { return '<button class="btn" data-data="' + esc(n) + '" type="button">자료 파일 ' + esc(n) + '</button>'; }).join('') + '</div></div>';
    if (e.forms.length) {
      h += '<div class="alert ok-alert small">이 문제 파일에는 사용자 정의 폼(' + e.forms.map(function (f) { return '&lt;' + esc(f) + '&gt;'; }).join(', ') + ')과 단추가 들어 있어 매크로 사용 통합 문서(.xlsm)로 받습니다. ' +
        '인터넷에서 받은 파일은 Excel 이 매크로를 막으므로, 열기 전에 파일을 마우스 오른쪽 단추로 눌러 [속성] → 아래쪽 \'차단 해제\'에 체크하고 [확인]을 누르세요. 그다음 Excel 에서 열고 노란 막대의 [콘텐츠 사용]을 누릅니다.</div>';
    }
    h += '<div id="err">' + (error ? '<div class="alert" role="alert">' + esc(error) + '</div>' : '') + '</div>';
    h += '<section class="card exam-bar"><div class="timer" id="timer"><span class="small muted">남은 시간</span> <b id="timer-text">' + e.minutes + ':00</b></div>' +
      '<div class="btns"><button class="btn" id="t-start" type="button">시험 시작</button><button class="btn ghost" id="t-reset" type="button">시간 초기화</button></div>' +
      '<div class="exam-upload"><input type="file" id="ans" accept=".xlsx,.xlsm" aria-label="답안 파일"><button class="btn primary" id="submit" type="button">답안 제출·채점</button></div></section>';
    if (e.intro) h += '<p class="small muted">' + esc(e.intro) + '</p>';
    e.sections.forEach(function (s, si) {
      h += '<section class="card exam-section"><h2>문제 ' + (si + 1) + ' ' + esc(s.name) + ' <span class="small muted" style="font-weight:400">(' + s.points + '점)</span></h2>';
      s.tasks.forEach(function (t, ti) {
        h += '<div class="exam-task"><h3>' + (ti + 1) + '. ' + esc(t.title) + ' <span class="small muted" style="font-weight:400">(' + t.points + '점)</span></h3><p class="prompt">' + esc(t.text) + '</p>';
        if (t.table) {
          h += '<div class="table-wrap" style="max-width:780px"><table class="t sheet-like"><thead><tr><th></th>' + t.table[0].map(function (_, i) { return '<th>' + 'ABCDEFGHIJKL'.charAt(i) + '</th>'; }).join('') + '</tr></thead><tbody>' +
            t.table.map(function (row, ri) { return '<tr><th>' + (ri + 1) + '</th>' + row.map(function (v) { return '<td>' + esc(v) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>';
        }
        if (t.items.length) {
          h += '<ol class="exam-items">' + t.items.map(function (it) {
            if (it.kind === 'code') return '<li class="code"><pre class="mono">' + esc(it.text) + '</pre></li>';
            if (it.kind === 'sub') return '<li class="sub">– ' + esc(it.text) + '</li>';
            return '<li>' + (it.num ? '<span class="num">' + esc(it.num) + '</span> ' : '') + esc(it.text) + '</li>';
          }).join('') + '</ol>';
        }
        h += '</div>';
      });
      h += '</section>';
    });
    h += '<section class="card"><h2>응시 기록</h2>' + (hist.length ? '<div class="table-wrap"><table class="t"><thead><tr><th>시각</th><th class="r">점수</th><th>결과</th><th class="r">걸린 시간</th><th>파일</th></tr></thead><tbody>' +
      hist.map(function (r) {
        return '<tr><td class="nowrap">' + (r.res ? '<a href="#/result/' + r.rid + '">' + EX.fmtDate(r.at) + '</a>' : EX.fmtDate(r.at)) + '</td><td class="r"><b>' + num(r.score) + '</b> / ' + num(r.total) + '</td><td>' +
          (r.passed ? '<span class="st ok">✓</span> 합격선' : '<span class="st bad">✕</span> 미달') + '</td><td class="r">' + (r.seconds ? Math.floor(r.seconds / 60) + '분' : '–') + '</td><td class="small muted">' + esc(r.file) + '</td></tr>';
      }).join('') + '</tbody></table></div>' : '<div class="empty">아직 제출한 답안이 없습니다.</div>') + '</section>';
    box.innerHTML = h;
    window.scrollTo(0, 0);

    /* 내려받기 */
    document.getElementById('dl-main').addEventListener('click', function () {
      prefetch();
      EX.py.call('exam_file', { id: id }).then(function (r) { EX.download(r.file, r.name, XLSX); });
    });
    box.querySelectorAll('[data-data]').forEach(function (b) {
      b.addEventListener('click', function () {
        EX.py.call('exam_data', { id: id, name: b.getAttribute('data-data') }).then(function (r) { if (!r.error) EX.download(r.file, r.name, 'text/csv'); });
      });
    });

    /* 시간 */
    var key = 'x.start.' + id;
    var text = document.getElementById('timer-text'), wrap = document.getElementById('timer'), startBtn = document.getElementById('t-start');
    function tick() {
      var start = store.get(key, 0);
      if (!start) { text.textContent = e.minutes + ':00'; startBtn.disabled = false; wrap.classList.remove('over'); return null; }
      startBtn.disabled = true;
      var used = Math.floor((Date.now() - start) / 1000), left = e.minutes * 60 - used, a = Math.abs(left);
      wrap.classList.toggle('over', left < 0);
      text.textContent = (left < 0 ? '시간 초과 +' : '') + Math.floor(a / 60) + ':' + (a % 60 < 10 ? '0' : '') + (a % 60);
      return used;
    }
    clearInterval(timerHandle);
    startBtn.addEventListener('click', function () { store.set(key, Date.now()); prefetch(); tick(); });
    document.getElementById('t-reset').addEventListener('click', function () {
      if (store.get(key, 0) && !window.confirm('시간을 처음으로 되돌릴까요?')) return;
      store.remove(key); tick();
    });
    tick();
    timerHandle = setInterval(tick, 1000);

    /* 제출 */
    var fileEl = document.getElementById('ans'), errEl = document.getElementById('err'), btn = document.getElementById('submit');
    fileEl.addEventListener('change', prefetch);
    btn.addEventListener('click', async function () {
      var f = fileEl.files[0];
      if (!f) { errEl.innerHTML = '<div class="alert" role="alert">답안 파일을 고르세요.</div>'; return; }
      if (!/\.(xlsx|xlsm)$/i.test(f.name)) { errEl.innerHTML = '<div class="alert" role="alert">.xlsx 또는 .xlsm 파일만 올릴 수 있습니다.</div>'; return; }
      var used = tick();
      btn.disabled = true;
      var res = await EX.withEngine(errEl, async function (s) {
        var bytes = new Uint8Array(await f.arrayBuffer());
        s('채점하는 중… (파일 크기에 따라 몇 초 걸립니다)');
        return EX.py.call('exam_grade', { id: id, name: f.name }, bytes, s);
      });
      btn.disabled = false;
      if (!res) return;
      if (res.error) { errEl.innerHTML = '<div class="alert" role="alert">' + esc(res.error) + '</div>'; return; }
      var secs = used == null ? null : Math.max(0, Math.min(used, e.minutes * 60 * 5));
      var rid = saveResult({ exam: id, score: res.res.score, total: res.res.total, passed: !!res.res.passed, seconds: secs, file: f.name.slice(0, 200), at: Date.now(), res: res.res });
      store.remove(key);
      location.hash = '#/result/' + rid;
    });
  }
  function prefetch() { if (EX.py.available()) EX.py.ready().catch(function () {}); }

  /* ---------- 결과 ---------- */
  async function viewResult(rid) {
    var row = results().filter(function (r) { return r.rid === rid; })[0];
    if (!row || !row.res) { main.innerHTML = '<div class="card empty">채점 결과를 찾을 수 없습니다(오래된 기록은 점수만 남습니다).<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    main.innerHTML = '<div id="box"></div>';
    var box = document.getElementById('box');
    var e = await EX.withEngine(box, function (s) { return paper(row.exam, s); });
    if (!e) return;
    var res = row.res;
    var h = '<div class="page-head"><div><div class="crumb"><a href="#/">컴활 실기 모의고사</a> › <a href="#/' + e.id + '">' + esc(e.title) + '</a> ›</div><h1>채점 결과</h1><p>' + esc(row.file) + ' · ' + EX.fmtDate(row.at) +
      (row.seconds ? ' · ' + Math.floor(row.seconds / 60) + '분 ' + (row.seconds % 60) + '초' : '') + '</p></div><a class="btn primary" href="#/' + e.id + '">문제지로</a></div>';
    h += '<div class="grid g4"><div class="kpi"><div class="label">점수</div><div class="value">' + num(res.score) + '<small> / ' + num(res.total) + '</small></div><div class="sub">' +
      (res.passed ? '<span class="st ok">✓</span> 합격선(' + e.pass + '점) 이상' : '<span class="st bad">✕</span> 합격선 ' + e.pass + '점 미달') + '</div></div>';
    res.sections.forEach(function (s) {
      h += '<div class="kpi"><div class="label">' + esc(s.name) + '</div><div class="value">' + num(s.got) + '<small> / ' + num(s.points) + '</small></div><div class="meter" style="margin-top:8px" aria-hidden="true"><i style="width:' + (s.points ? Math.round(100 * s.got / s.points) : 0) + '%"></i></div></div>';
    });
    h += '</div>';
    if (!res.has_vba && res.tasks.some(function (t) { return t.section === '기타작업'; })) h += '<p class="small muted">이 파일에는 매크로(VBA)가 없습니다. 매크로 문제는 Excel 매크로 사용 통합 문서(.xlsm)로 저장해야 채점됩니다.</p>';
    h += '<section class="card" style="margin-top:14px">' + res.tasks.map(function (t) {
      return '<div class="check-item"><div class="head">' + (t.got === t.points ? '<span class="st ok">✓</span>' : t.got > 0 ? '<span class="st new">△</span>' : '<span class="st bad">✕</span>') +
        '<b>' + esc(t.section) + ' ' + esc(t.no) + ' ' + esc(t.title) + '</b> <span class="small muted">' + num(t.got) + ' / ' + num(t.points) + '점</span></div><ul class="list" style="margin-left:32px">' +
        t.items.map(function (it) {
          return '<li><span class="st ' + (it.ok ? 'ok' : 'bad') + '">' + (it.ok ? '✓' : '✕') + '</span><div class="grow"><div>' + esc(it.label) + ' <span class="small muted">' + num(it.points) + '점</span></div>' +
            (!it.ok && it.msgs && it.msgs.length ? '<div class="small" style="color:var(--bad)">' + it.msgs.map(esc).join(' · ') + '</div>' : '') + '</div></li>';
        }).join('') + '</ul></div>';
    }).join('') + '</section>';
    box.innerHTML = h;
    window.scrollTo(0, 0);
  }

  function route() {
    clearInterval(timerHandle);
    var hash = location.hash.replace(/^#\/?/, '');
    var m = /^result\/(\d+)$/.exec(hash);
    if (m) viewResult(Number(m[1]));
    else if (hash) viewPaper(decodeURIComponent(hash));
    else viewList();
  }
  window.addEventListener('hashchange', route);
  route();
})();
