/* 대시보드 만들기 실습: 연습 파일 내려받기 → 엑셀에서 완성 → 올리면 칸마다 채점(브라우저 안의 파이썬 엔진) */
(function () {
  'use strict';
  var main = document.getElementById('main');
  EX.header('build.html');
  var esc = EX.esc, store = EX.store, num = EX.num;
  var XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

  function results() { return store.get('b.res', []); }
  function saveResult(r) {
    var all = results();
    r.rid = all.reduce(function (m, x) { return Math.max(m, x.rid); }, 0) + 1;
    all.push(r);
    var per = {};
    all.slice().reverse().forEach(function (x) { per[x.task] = (per[x.task] || 0) + 1; if (per[x.task] > 10) x.res = null; });
    store.set('b.res', all);
    return r.rid;
  }
  function prefetch() { if (EX.py.available()) EX.py.ready().catch(function () {}); }

  var listCache = null, taskCache = {};
  async function list(s) { return listCache || (listCache = await EX.py.call('build_list', {}, null, s)); }
  async function task(k, s) { return taskCache[k] || (taskCache[k] = await EX.py.call('build_task', { key: k }, null, s)); }

  async function viewList() {
    main.innerHTML = '<div class="page-head"><div><h1>대시보드 만들기 실습</h1><p>연습 파일을 내려받아 엑셀에서 수식·차트·조건부 서식으로 대시보드를 완성하고, 저장한 파일을 올리면 칸마다 채점합니다.</p></div></div><div id="box"></div>';
    var box = document.getElementById('box');
    var d = await EX.withEngine(box, function (s) { return list(s); });
    if (!d) return;
    var best = {};
    results().forEach(function (r) {
      var b = best[r.task] || (best[r.task] = { score: 0, total: r.total, n: 0 });
      b.score = Math.max(b.score, r.score); b.n++;
    });
    var h = '<div class="grid g3">' + d.missions.map(function (m) {
      var b = best[m.key];
      return '<a class="card cat-card" href="#/' + m.key + '"><div class="top-line"><h3 style="margin:0">' + esc(m.title) + '</h3><span class="lv">' + '●'.repeat(m.level) + '○'.repeat(3 - m.level) + '</span></div><p>' + esc(m.desc) + '</p><div>' +
        m.tracks.map(function (t) { return '<span class="badge ' + t + '">' + esc(d.tracks[t]) + '</span>'; }).join(' ') + '</div><div class="small" style="margin-top:10px">' +
        (b ? '최고 <b>' + b.score + '/' + b.total + '</b> · ' + b.n + '번 제출' : '<span class="muted">아직 제출하지 않음 · 채점 항목 ' + m.count + '개</span>') + '</div></a>';
    }).join('') + '</div>' +
      '<section class="card" style="margin-top:14px"><h2>진행 방법</h2><ol class="steps">' +
      '<li>과제를 골라 <b>연습 파일</b>을 내려받습니다. \'과제\' 시트에 할 일과 힌트가, \'대시보드\' 시트에 노란 입력 칸이 있습니다.</li>' +
      '<li>노란 칸에 \'데이터\' 시트를 참조하는 <b>수식</b>을 넣습니다. 값을 직접 쳐 넣으면 맞아도 오답으로 봅니다.</li>' +
      '<li>차트와 조건부 서식을 추가하고 <b>.xlsx 로 저장</b>합니다.</li>' +
      '<li>파일을 올리면 칸마다 기대 값과 비교해 채점합니다. 막히면 \'완성 예시\'를 내려받아 수식을 확인하세요.</li></ol></section>';
    box.innerHTML = h;
  }

  async function viewTask(key, error) {
    main.innerHTML = '<div id="box"></div>';
    var box = document.getElementById('box');
    var m = await EX.withEngine(box, function (s) { return task(key, s); });
    if (!m) return;
    if (m.error) { box.innerHTML = '<div class="card empty">' + esc(m.error) + '<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    var hist = results().filter(function (r) { return r.task === key; }).reverse().slice(0, 10);
    var h = '<div class="page-head"><div><div class="crumb"><a href="#/">대시보드 실습</a> ›</div><h1>' + esc(m.title) + '</h1><p>' + esc(m.desc) + '</p></div>' +
      '<div class="btns"><button class="btn primary" id="dl" type="button">연습 파일 내려받기</button><button class="btn" id="dl-ans" type="button" title="수식·차트가 들어간 완성본">완성 예시</button></div></div>' +
      '<div id="err">' + (error ? '<div class="alert" role="alert">' + esc(error) + '</div>' : '') + '</div><div class="grid g3">' +
      '<section class="card span2"><h2>할 일 <span class="small muted" style="font-weight:400">\'대시보드\' 시트</span></h2><ol class="steps">' +
      m.checks.map(function (c) {
        return '<li><b>' + esc(c.label) + '</b>' + (c.at ? ' <code>' + esc(c.at) + '</code>' : '') + (c.optional ? ' <span class="badge">채점 제외</span>' : '') + '<div class="small muted">' + esc(c.hint) + '</div></li>';
      }).join('') + '</ol></section><div class="stack"><section class="card"><h2>완성한 파일 올리기</h2>' +
      '<div class="drop" id="drop"><input type="file" id="file" accept=".xlsx,.xlsm" aria-label="완성한 실습 파일"><p class="small muted" id="fname" style="margin:8px 0 0">Excel 에서 저장한 .xlsx</p></div>' +
      '<div class="btns" style="margin-top:10px"><button class="btn primary" id="go" type="button">채점하기</button></div></section>' +
      '<section class="card"><h2>제출 기록</h2>' + (hist.length ? '<ul class="list">' + hist.map(function (r) {
        return '<li><div class="grow">' + (r.res ? '<a href="#/result/' + r.rid + '"><b>' + r.score + '/' + r.total + '</b></a>' : '<b>' + r.score + '/' + r.total + '</b>') +
          '<div class="small muted">' + esc(r.file) + '</div></div><span class="small muted nowrap">' + EX.fmtDate(r.at) + '</span></li>';
      }).join('') + '</ul>' : '<div class="empty">아직 없습니다.</div>') + '</section></div></div>';
    box.innerHTML = h;
    window.scrollTo(0, 0);

    document.getElementById('dl').addEventListener('click', function () {
      EX.py.call('build_file', { key: key }).then(function (r) { EX.download(r.file, r.name, XLSX); });
    });
    document.getElementById('dl-ans').addEventListener('click', function () {
      EX.py.call('build_file', { key: key, answers: true }).then(function (r) { EX.download(r.file, r.name, XLSX); });
    });
    var drop = document.getElementById('drop'), fileEl = document.getElementById('file'), fname = document.getElementById('fname');
    ['dragenter', 'dragover'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('over'); }); });
    drop.addEventListener('drop', function (e) { if (e.dataTransfer.files.length) { fileEl.files = e.dataTransfer.files; fname.textContent = fileEl.files[0].name; prefetch(); } });
    fileEl.addEventListener('change', function () { if (fileEl.files.length) { fname.textContent = fileEl.files[0].name; prefetch(); } });
    var errEl = document.getElementById('err'), go = document.getElementById('go');
    go.addEventListener('click', async function () {
      var f = fileEl.files[0];
      if (!f) { errEl.innerHTML = '<div class="alert" role="alert">파일을 고르세요.</div>'; return; }
      if (!/\.(xlsx|xlsm)$/i.test(f.name)) { errEl.innerHTML = '<div class="alert" role="alert">.xlsx 파일로 저장해서 올려 주세요.</div>'; return; }
      go.disabled = true;
      var r = await EX.withEngine(errEl, async function (s) {
        var bytes = new Uint8Array(await f.arrayBuffer());
        s('채점하는 중…');
        return EX.py.call('build_grade', { key: key, name: f.name }, bytes, s);
      });
      go.disabled = false;
      if (!r) return;
      if (r.error) { errEl.innerHTML = '<div class="alert" role="alert">' + esc(r.error) + '</div>'; return; }
      var rid = saveResult({ task: key, score: r.res.score, total: r.res.total, file: f.name.slice(0, 200), at: Date.now(), res: r.res });
      location.hash = '#/result/' + rid;
    });
  }

  async function viewResult(rid) {
    var row = results().filter(function (r) { return r.rid === rid; })[0];
    if (!row || !row.res) { main.innerHTML = '<div class="card empty">채점 결과를 찾을 수 없습니다.<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    main.innerHTML = '<div id="box"></div>';
    var box = document.getElementById('box');
    var m = await EX.withEngine(box, function (s) { return task(row.task, s); });
    if (!m) return;
    var res = row.res;
    var h = '<div class="page-head"><div><div class="crumb"><a href="#/">대시보드 실습</a> › <a href="#/' + m.key + '">' + esc(m.title) + '</a> ›</div><h1>채점 결과</h1><p>' + esc(row.file) + ' · ' + EX.fmtDate(row.at) + '</p></div>' +
      '<div class="btns"><a class="btn primary" href="#/' + m.key + '">고쳐서 다시 올리기</a></div></div>' +
      '<div class="grid g4"><div class="kpi"><div class="label">점수</div><div class="value">' + res.score + '<small> / ' + res.total + '</small></div><div class="sub">' + Math.round(100 * res.score / res.total) + '%</div></div>' +
      '<div class="kpi" style="grid-column: span 3"><div class="label">안내</div><div class="small" style="margin-top:6px">' +
      (res.score === res.total ? '모든 항목을 통과했습니다. 다른 과제도 도전해 보세요.' : '틀린 항목을 펼쳐 내 수식·값과 기대 값을 비교해 보세요. 고친 뒤 같은 파일을 다시 올리면 됩니다.') +
      (res.notes || []).map(function (n) { return '<br>' + esc(n); }).join('') + '</div></div></div>';
    h += '<section class="card" style="margin-top:14px">' + res.items.map(function (it) {
      var out = '<div class="check-item"><div class="head">' + (it.ok ? '<span class="st ok">✓</span>' : it.optional ? '<span class="st new">·</span>' : '<span class="st bad">✕</span>') +
        '<b>' + esc(it.label) + '</b>' + (it.at ? ' <code>' + esc(it.at) + '</code>' : '') + (it.optional ? ' <span class="badge">채점 제외</span>' : '') +
        ' <span class="small muted">' + (it.msgs || []).map(esc).join(' · ') + '</span></div>';
      if (it.cells && it.cells.length) {
        out += '<details' + (it.ok ? '' : ' open') + ' style="margin-top:6px"><summary>칸별 결과</summary><div class="table-wrap" style="margin-top:6px"><table class="t"><thead><tr><th>셀</th><th>내 수식</th><th class="r">내 값</th><th class="r">기대 값</th><th></th></tr></thead><tbody>' +
          it.cells.map(function (c) {
            return '<tr><td class="mono">' + esc(c.addr) + '</td><td class="mono small">' + esc(c.formula || '(비어 있음)') + '</td><td class="r">' + esc(c.got) + '</td><td class="r">' + esc(c.expected) + '</td><td>' + (c.ok ? '✓' : '✕') + '</td></tr>';
          }).join('') + '</tbody></table></div>' + (it.ok ? '' : '<p class="small"><b>힌트</b> ' + esc(it.hint) + '<br><b>예시 수식</b> <code>' + esc(it.answer) + '</code></p>') + '</details>';
      } else if (!it.ok && !it.optional) {
        out += '<p class="small" style="margin:6px 0 0 32px"><b>힌트</b> ' + esc(it.hint) + '</p>';
      }
      return out + '</div>';
    }).join('') + '</section>';
    box.innerHTML = h;
    window.scrollTo(0, 0);
  }

  function route() {
    var hash = location.hash.replace(/^#\/?/, '');
    var m = /^result\/(\d+)$/.exec(hash);
    if (m) viewResult(Number(m[1]));
    else if (hash) viewTask(decodeURIComponent(hash));
    else viewList();
  }
  window.addEventListener('hashchange', route);
  route();
})();
