/* 파일 → 대시보드: 엑셀·CSV 를 올리면 핵심 지표·분류별 집계·월별 추이·피벗 표와 같은 결과를 내는 엑셀 수식을 보여 준다.
   파일은 브라우저 안에서만 처리하고, 요약 표만 이 브라우저(IndexedDB)에 최근 20개까지 보관한다. */
(function () {
  'use strict';
  var main = document.getElementById('main');
  EX.header('analyze.html');
  var esc = EX.esc, num = EX.num;
  var XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  var KEEP = 20;
  var TYPE = { number: '숫자', date: '날짜', text: '문자' };

  /* ---------- 보관(IndexedDB, 막혀 있으면 이번 탭 메모리) ---------- */
  function all() { return EX.idb.all('uploads'); }
  async function put(rec) {
    await EX.idb.put('uploads', rec);
    var items = (await all()).sort(function (a, b) { return b.at - a.at; });
    for (var i = KEEP; i < items.length; i++) await remove(items[i].id);
  }
  function remove(id) { return EX.idb.remove('uploads', id); }
  function get(id) { return EX.idb.get('uploads', id); }

  /* ---------- 목록 ---------- */
  async function viewList(error) {
    var uploads = (await all()).sort(function (a, b) { return b.at - a.at; });
    var h = '<div class="page-head"><div><h1>파일 → 대시보드</h1><p>엑셀·CSV 파일을 올리면 핵심 지표, 분류별 집계, 월별 추이, 피벗 표를 만들고 같은 결과를 내는 엑셀 수식도 알려 줍니다.</p></div></div>' +
      '<div id="err">' + (error ? '<div class="alert" role="alert">' + esc(error) + '</div>' : '') + '</div><div class="grid g3"><section class="card span2">' +
      '<div class="drop" id="drop"><p style="margin:0 0 10px"><b>파일을 여기에 끌어 놓거나</b> 골라 주세요</p><input type="file" id="file" accept=".xlsx,.xlsm,.csv" aria-label="분석할 파일"><p class="small muted" id="fname" style="margin:8px 0 0">.xlsx · .xlsm · .csv, 8MB · 2만 행까지</p></div>' +
      '<div class="btns" style="margin-top:12px"><button class="btn primary" id="go" type="button">올리고 분석하기</button></div>' +
      '<p class="small muted" style="margin-bottom:0">첫 행(또는 위쪽의 열 이름 행)을 머리글로 읽습니다. 파일은 이 브라우저 안에서만 처리되며 서버로 보내지 않습니다. 요약 데이터만 이 브라우저에 최근 20개까지 남깁니다.</p></section>' +
      '<section class="card"><h2>파일이 없다면</h2><p class="small">실습용 상반기 매출 120건으로 바로 해 볼 수 있습니다.</p><button class="btn" id="sample" type="button">샘플로 분석해 보기</button>' +
      '<p class="small"><a href="#" id="sample-dl">샘플 파일 내려받기</a> — 엑셀에서 직접 피벗을 만들어 결과를 비교해 보세요.</p></section></div>' +
      '<section class="card"><h2>올린 파일</h2>' + (uploads.length ? '<div class="table-wrap"><table class="t"><thead><tr><th>파일</th><th>시트</th><th class="r">행</th><th class="r">열</th><th>올린 시각</th><th></th></tr></thead><tbody>' +
        uploads.map(function (u) {
          return '<tr><td><a href="#/' + u.id + '">' + esc(u.name) + '</a></td><td>' + esc(u.sheet) + '</td><td class="r">' + num(u.rows) + '</td><td class="r">' + u.cols + '</td><td class="nowrap muted">' + EX.fmtDate(u.at) +
            '</td><td><button class="btn sm ghost danger" data-del="' + u.id + '" type="button">지우기</button></td></tr>';
        }).join('') + '</tbody></table></div>' : '<div class="empty">아직 올린 파일이 없습니다.</div>') + '</section>';
    main.innerHTML = h;
    window.scrollTo(0, 0);
    var errEl = document.getElementById('err'), drop = document.getElementById('drop'), fileEl = document.getElementById('file'), fname = document.getElementById('fname');
    ['dragenter', 'dragover'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('over'); }); });
    drop.addEventListener('drop', function (e) { if (e.dataTransfer.files.length) { fileEl.files = e.dataTransfer.files; fname.textContent = fileEl.files[0].name; prefetch(); } });
    fileEl.addEventListener('change', function () { if (fileEl.files.length) { fname.textContent = fileEl.files[0].name; prefetch(); } });
    document.getElementById('go').addEventListener('click', function () {
      var f = fileEl.files[0];
      if (!f) { errEl.innerHTML = '<div class="alert" role="alert">파일을 고르세요.</div>'; return; }
      if (f.size > 8 * 1024 * 1024) { errEl.innerHTML = '<div class="alert" role="alert">파일이 너무 큽니다(8MB까지).</div>'; return; }
      f.arrayBuffer().then(function (b) { ingest(new Uint8Array(b), f.name, errEl); });
    });
    document.getElementById('sample').addEventListener('click', async function () {
      var s = await EX.withEngine(errEl, function (st) { return EX.py.call('an_sample', {}, null, st); });
      if (s && !s.error) ingest(s.file, '샘플_상반기매출.xlsx', errEl);
    });
    document.getElementById('sample-dl').addEventListener('click', async function (e) {
      e.preventDefault();
      var s = await EX.withEngine(errEl, function (st) { return EX.py.call('an_sample', {}, null, st); });
      if (s && !s.error) EX.download(s.file, s.name, XLSX);
    });
    main.querySelectorAll('[data-del]').forEach(function (b) {
      b.addEventListener('click', async function () {
        if (!window.confirm('이 분석 결과를 지울까요?')) return;
        await remove(b.getAttribute('data-del'));
        viewList();
      });
    });
  }
  function prefetch() { if (EX.py.available()) EX.py.ready().catch(function () {}); }

  async function ingest(bytes, name, errEl) {
    var r = await EX.withEngine(errEl, async function (st) {
      st('파일을 읽는 중…');
      return EX.py.call('an_read', { name: name }, bytes, st);
    });
    if (!r) return;
    if (r.error) { errEl.innerHTML = '<div class="alert" role="alert">' + esc(r.error) + '</div>'; return; }
    var def = r.doc.tables[r.sheet];
    var id = Array.prototype.map.call(crypto.getRandomValues(new Uint8Array(8)), function (b) { return (b < 16 ? '0' : '') + b.toString(16); }).join('');
    await put({ id: id, name: name.slice(0, 200), sheet: r.sheet, rows: def.rows.length, cols: def.columns.length, at: Date.now(), doc: r.doc });
    location.hash = '#/' + id;
  }

  /* ---------- 분석 화면 ---------- */
  function parseHash() {
    var raw = location.hash.replace(/^#\/?/, ''), q = {};
    var i = raw.indexOf('?'), id = i < 0 ? raw : raw.slice(0, i);
    if (i >= 0) raw.slice(i + 1).split('&').forEach(function (p) { var kv = p.split('='); if (kv[0]) q[kv[0]] = decodeURIComponent((kv[1] || '').replace(/\+/g, ' ')); });
    return { id: id, q: q };
  }
  function link(id, q, over) {
    var o = Object.assign({}, q, over || {}), parts = [];
    Object.keys(o).forEach(function (k) { if (o[k] !== undefined && o[k] !== '') parts.push(k + '=' + encodeURIComponent(o[k])); });
    return '#/' + id + (parts.length ? '?' + parts.join('&') : '');
  }

  async function viewDash(id, q) {
    main.innerHTML = '<div id="box"></div>';
    var box = document.getElementById('box');
    var rec = await get(id);
    if (!rec) { box.innerHTML = '<div class="card empty">분석 결과를 찾을 수 없습니다.<p><a class="btn" href="#/">목록으로</a></p></div>'; return; }
    var v = await EX.withEngine(box, async function (st) {
      var has = await EX.py.call('an_has', { uid: id }, null, st);
      if (!has.has) await EX.py.call('an_open', { uid: id, doc: JSON.stringify(rec.doc) }, null, st);
      var params = {};
      ['g', 'v', 'c', 'a', 'f', 'fv'].forEach(function (k) { if (q[k] !== undefined) params[k] = q[k]; });
      return EX.py.call('an_view', { uid: id, sheet: q.s || '', params: params }, null, st);
    });
    if (!v) return;
    if (v.error) { box.innerHTML = '<div class="alert" role="alert">' + esc(v.error) + '</div>'; return; }
    var d = v.dash, t = v.table, cols = t.columns, aggs = v.aggs;
    var vname = d.v !== null && d.a !== 'count' ? cols[d.v].name : '건수';
    var gname = d.g !== null ? cols[d.g].name : '';
    var aggName = d.a !== 'count' ? aggs[d.a] : '';
    var charts = {};
    if (d.group) charts.group = { labels: d.group.map(function (x) { return String(x[0]); }), series: [{ name: aggs[d.a], values: d.group.map(function (x) { return x[1]; }) }] };
    if (d.trend) charts.trend = { labels: d.trend.map(function (x) { return x[0]; }), series: [{ name: aggs[d.a], values: d.trend.map(function (x) { return x[1]; }) }] };
    d.cat_counts.forEach(function (cc, i) { charts['cat' + i] = { labels: cc.items.map(function (x) { return String(x[0]); }), series: [{ name: '건수', values: cc.items.map(function (x) { return x[1]; }) }] }; });

    function sel(name, options, cur, blank) {
      return '<select data-p="' + name + '">' + (blank ? '<option value="">' + blank + '</option>' : '') +
        options.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (String(o[0]) === String(cur) ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('') + '</select>';
    }
    var h = '<div class="page-head"><div><div class="crumb"><a href="#/">파일 분석</a> ›</div><h1>' + esc(v.name) + '</h1><p>' + esc(t.sheet) + ' 시트 · ' + num(v.rowCount) + '행 × ' + cols.length + '열 · 머리글 ' + t.head_row + '행' +
      (t.truncated ? ' · 앞 2만 행만 읽음' : '') + (t.skipped ? ' · 합계·메모 행 ' + t.skipped + '개는 빼고 분석' : '') + (t.wide ? ' · 60열이 넘어 앞 60열만 읽음' : '') + '</p></div>' +
      '<button class="btn" id="export" type="button">요약을 엑셀로 내려받기</button></div>';
    if (v.sheets.length > 1) {
      h += '<nav class="sheet-tabs" aria-label="시트">' + v.sheets.map(function (n) { return '<a href="' + link(id, {}, { s: n }) + '" class="' + (n === t.sheet ? 'on' : '') + '">' + esc(n) + '</a>'; }).join('') + '</nav>';
    }
    var textOpts = d.text_idx.map(function (k) { return [k, cols[k].name]; });
    var numOpts = d.num_idx.map(function (k) { return [k, cols[k].name]; });
    h += '<div class="card controls" id="controls">' +
      '<label class="field">기준(행) 열' + sel('g', textOpts, d.g) + '</label>' +
      '<label class="field">값 열' + (numOpts.length ? sel('v', numOpts, d.v) : '<select data-p="v"><option value="">(숫자 열 없음)</option></select>') + '</label>' +
      '<label class="field">집계' + sel('a', Object.keys(aggs).map(function (k) { return [k, aggs[k]]; }), d.a) + '</label>' +
      '<label class="field">피벗 열' + sel('c', textOpts.filter(function (o) { return o[0] !== d.g; }), d.c, '(없음)') + '</label>' +
      '<label class="field">필터 열' + sel('f', textOpts, d.f, '(없음)') + '</label>' +
      (d.f !== null ? '<label class="field">필터 값' + sel('fv', (d.filter_values || []).map(function (x) { return [x, x]; }), d.fv, '(모두)') + '</label>' : '') + '</div>';
    h += '<section class="grid g4" style="margin-top:14px" aria-label="핵심 지표">' + d.kpis.map(function (k) {
      return '<div class="kpi"><div class="label">' + esc(k.label) + (d.fv ? ' <span class="muted">(' + esc(d.fv) + ')</span>' : '') + '</div><div class="value">' + num(k.value) + '</div>' + (k.sub ? '<div class="sub">' + esc(k.sub) + '</div>' : '') + '</div>';
    }).join('') + '</section><div class="grid g2" style="margin-top:14px">';
    if (d.group) {
      h += '<section class="card"><h2>' + esc(gname) + '별 ' + esc(vname) + ' ' + esc(aggName) + '</h2><div class="chart" data-chart="group" data-type="hbar"></div>';
      if (d.group_formula) {
        h += '<div class="formula-box" style="margin-top:12px"><div class="small muted">엑셀 수식으로 첫 줄(' + esc(d.group[0][0]) + ') 값을 구하면</div><code>' + esc(d.group_formula) + '</code>' +
          '<div class="small muted" style="margin-top:4px">조건 칸을 셀 참조로 바꾸고 범위에 $ 를 붙이면 아래로 채울 수 있습니다. 피벗 테이블로는 행: ' + esc(gname) + ', 값: ' + esc(vname) + ' ' + esc(aggs[d.a]) + '.</div></div>';
      }
      h += '</section>';
    }
    if (d.trend) {
      var yearly = d.trend.length && String(d.trend[0][0]).length === 4;
      h += '<section class="card"><h2>' + (yearly ? '연도별' : '월별') + ' ' + esc(vname) + ' ' + esc(aggName) + ' <span class="small muted" style="font-weight:400">기준: ' + esc(d.date_name) + '</span></h2><div class="chart" data-chart="trend" data-type="line"></div>' +
        '<details style="margin-top:8px"><summary>수치 보기</summary><div class="table-wrap" style="margin-top:6px"><table class="t"><thead><tr><th>' + (yearly ? '연도' : '월') + '</th><th class="r">' + esc(aggs[d.a]) + '</th><th class="r">건수</th></tr></thead><tbody>' +
        d.trend.map(function (x) { return '<tr><td>' + esc(x[0]) + '</td><td class="r">' + num(x[1]) + '</td><td class="r">' + x[2] + '</td></tr>'; }).join('') + '</tbody></table></div></details></section>';
    }
    h += '</div>';
    if (d.group) {
      var tot = (d.a === 'sum' || d.a === 'count') ? d.group.reduce(function (s, x) { return s + (x[1] || 0); }, 0) : null;
      h += '<section class="card" style="margin-top:14px"><h2>' + esc(gname) + '별 집계표</h2><div class="table-wrap"><table class="t"><thead><tr><th>' + esc(gname) + '</th><th class="r">' + esc(vname) + ' ' + esc(aggName) + '</th><th class="r">건수</th><th class="r">비중</th></tr></thead><tbody>' +
        d.group.map(function (x) {
          return '<tr><td>' + esc(x[0]) + '</td><td class="r">' + num(x[1]) + '</td><td class="r">' + num(x[2]) + '</td><td class="r">' + (tot && x[1] !== null ? (100 * x[1] / tot).toFixed(1) + '%' : '–') + '</td></tr>';
        }).join('') + (tot ? '<tr class="total"><td>합계</td><td class="r">' + num(tot) + '</td><td class="r">' + num(d.rows_n) + '</td><td class="r">100%</td></tr>' : '') + '</tbody></table></div></section>';
    }
    if (d.pivot) {
      var cname = cols[d.c].name;
      h += '<section class="card"><h2>피벗 표 <span class="small muted" style="font-weight:400">행: ' + esc(gname) + ' · 열: ' + esc(cname) + ' · 값: ' + esc(vname) + ' ' + esc(aggs[d.a]) + '</span></h2><div class="table-wrap"><table class="t"><thead><tr><th>' + esc(gname) + ' ↓ · ' + esc(cname) + ' →</th>' +
        d.pivot.cols.map(function (c) { return '<th class="r">' + esc(c) + '</th>'; }).join('') + '<th class="r">합계</th></tr></thead><tbody>' +
        d.pivot.rows.map(function (row) { return '<tr><td>' + esc(row.key) + '</td>' + row.vals.map(function (x) { return '<td class="r">' + num(x) + '</td>'; }).join('') + '<td class="r"><b>' + num(row.total) + '</b></td></tr>'; }).join('') +
        '<tr class="total"><td>합계</td>' + d.pivot.totals.map(function (x) { return '<td class="r">' + num(x) + '</td>'; }).join('') + '<td class="r">' + num(d.pivot.grand) + '</td></tr></tbody></table></div>' +
        (d.pivot.more_cols || d.pivot.more_rows ? '<p class="small muted">항목이 많아 많이 나온 것만 표시했습니다.</p>' : '') +
        '<p class="small muted" style="margin-bottom:0">엑셀에서는 [삽입] › [피벗 테이블] → 행에 ' + esc(gname) + ', 열에 ' + esc(cname) + ', 값에 ' + esc(vname) + ' 을 끌어 놓고 값 요약 기준을 \'' + esc(aggs[d.a]) + '\'로 바꾸면 같은 표가 됩니다.</p></section>';
    }
    if (d.cat_counts.length) {
      h += '<div class="grid g3" style="margin-top:14px">' + d.cat_counts.map(function (cc, i) {
        return '<section class="card"><h2>' + esc(cc.name) + '별 건수</h2><div class="chart" data-chart="cat' + i + '" data-type="hbar"></div></section>';
      }).join('') + '</div>';
    }
    h += '<section class="card" style="margin-top:14px"><h2>열 정보</h2><div class="table-wrap"><table class="t"><thead><tr><th>열</th><th>이름</th><th>종류</th><th class="r">값</th><th class="r">빈칸</th><th class="r">고유값</th><th>요약</th></tr></thead><tbody>' +
      d.profile.map(function (p) {
        var sum = '';
        if (p.type === 'number' && p.filled) sum = '합계 ' + num(p.sum) + ' · 평균 ' + num(p.avg, 2) + ' · ' + num(p.min) + ' ~ ' + num(p.max);
        else if (p.type === 'date' && p.filled) sum = p.min + ' ~ ' + p.max;
        else if (p.top) sum = p.top.map(function (x) { return x[0] + '(' + x[1] + ')'; }).join(', ');
        return '<tr><td class="mono">' + esc(p.letter) + '</td><td>' + esc(p.name) + '</td><td>' + TYPE[p.type] + '</td><td class="r">' + num(p.filled) + '</td><td class="r">' + num(p.blank) + '</td><td class="r">' + num(p.unique) + '</td><td class="small">' + esc(sum) + '</td></tr>';
      }).join('') + '</tbody></table></div></section>';
    h += '<section class="card"><h2>데이터 미리 보기 <span class="small muted" style="font-weight:400">앞 15행</span></h2><div class="table-wrap"><table class="t"><thead><tr>' +
      cols.map(function (c) { return '<th class="' + (c.type === 'number' ? 'r' : '') + '">' + esc(c.name) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      d.preview.map(function (r) {
        return '<tr>' + r.map(function (x, i) { return '<td class="' + (cols[i].type === 'number' ? 'r ' : '') + 'nowrap">' + (cols[i].type === 'number' ? num(x) : esc(x === null ? '' : x)) + '</td>'; }).join('') + '</tr>';
      }).join('') + '</tbody></table></div></section>';
    box.innerHTML = h;
    window.scrollTo(0, 0);
    main.__chartData = null;
    EX.charts.draw(main, charts);

    document.getElementById('controls').addEventListener('change', function (e) {
      var p = e.target.getAttribute('data-p');
      if (!p) return;
      var over = {};
      over[p] = e.target.value;
      if (p === 'f') over.fv = '';
      location.hash = link(id, q, over);
    });
    document.getElementById('export').addEventListener('click', async function () {
      var params = {};
      ['g', 'v', 'c', 'a', 'f', 'fv'].forEach(function (k) { if (q[k] !== undefined) params[k] = q[k]; });
      var r = await EX.py.call('an_export', { uid: id, sheet: q.s || '', params: params });
      if (!r.error) EX.download(r.file, r.name, XLSX);
    });
  }

  function route() {
    var p = parseHash();
    if (p.id) viewDash(p.id, p.q); else viewList();
  }
  window.addEventListener('hashchange', route);
  route();
})();
