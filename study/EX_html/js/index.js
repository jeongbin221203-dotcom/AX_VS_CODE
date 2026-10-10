/* 홈: 소개, 바로 가기, 기록 요약, 기록 백업·복원 */
(function () {
  'use strict';
  var main = document.getElementById('main');
  EX.header('index.html');
  var W = window.EXDATA.written, P = window.EXDATA.problems, F = window.EXDATA.functions;
  var wAtt = EX.store.get('w.att', {}), lAtt = EX.store.get('l.att', {});
  var wDone = Object.keys(wAtt).length, wOk = Object.keys(wAtt).filter(function (k) { return wAtt[k].ok; }).length;
  var lDone = Object.keys(lAtt).length, lOk = Object.keys(lAtt).filter(function (k) { return lAtt[k].ok; }).length;
  var hist = EX.store.get('w.hist', []);
  var last = hist.length ? hist[hist.length - 1] : null;
  var pct = function (a, b) { return b ? Math.round(100 * a / b) : 0; };

  var h = '<div class="page-head"><div><h1>엑셀 연습장 <span class="badge">HTML 버전</span></h1><p>서버 없이 파일만으로 열리는 학습 도구입니다. 이 폴더를 그대로 열거나 정적 호스팅에 올려도 동작합니다.</p></div></div>';
  /* 학습 대시보드(문제 풀기 기록) */
  var esc = EX.esc, track = (EX.store.get('l.filter', {}) || {}).track || '';
  var D = EX.learn.dashboard(track), nxtId = EX.learn.nextProblem('', track);
  h = h.replace('</div></div>', '</div><div class="row"><a class="btn" href="learn.html#/review">오답 노트</a><a class="btn primary" href="learn.html#/' + (nxtId ? encodeURIComponent(nxtId) : '') + '">' + (D.tried ? '이어서 풀기' : '첫 문제 풀기') + ' →</a></div></div>');
  h += '<section class="g4" aria-label="요약 지표">' +
    '<div class="kpi"><div class="label">맞힌 문제</div><div class="value">' + D.solved + '<small> / ' + D.total + '</small></div><div class="sub">' + pct(D.solved, D.total) + '% 완료</div></div>' +
    '<div class="kpi"><div class="label">첫 시도 정답률</div><div class="value">' + (D.acc === null ? '–' : D.acc + '<small>%</small>') + '</div><div class="sub">처음 풀 때 맞힌 비율</div></div>' +
    '<div class="kpi"><div class="label">오늘 푼 횟수</div><div class="value">' + D.today + '</div><div class="sub">다시 볼 문제 ' + D.wrong_count + '개</div></div>' +
    '<div class="kpi"><div class="label">연속 학습</div><div class="value">' + D.streak + '<small>일</small></div><div class="sub">하루 한 문제라도 이어 가기</div></div></section>';
  h += '<div class="grid g3" style="margin-top:14px"><section class="card span2"><h2>최근 14일 풀이</h2><div class="chart" data-chart="daily" data-classes="ok,bad"></div></section>' +
    '<section class="card"><h2>범위별 진도</h2>' + D.tracks.map(function (t) {
      return '<div style="margin:10px 0"><div class="small" style="display:flex;justify-content:space-between"><span>' + esc(t.name) + '</span><span class="muted">' + t.solved + ' / ' + t.total + '</span></div>' +
        '<div class="meter" role="meter" aria-valuenow="' + t.pct + '" aria-valuemin="0" aria-valuemax="100" aria-label="' + esc(t.name) + ' 진도"><i style="width:' + t.pct + '%"></i></div></div>';
    }).join('') + (D.weak.length ? '<h3 style="margin-top:16px">보강하면 좋은 분류</h3><ul class="list">' + D.weak.map(function (c) {
      return '<li><div class="grow"><a href="learn.html#/cat/' + c.key + '">' + esc(c.name) + '</a></div><span class="small muted">정답률 ' + c.acc + '%</span></li>';
    }).join('') + '</ul>' : '') + '</section></div>';
  h += '<div class="grid g2" style="margin-top:14px"><section class="card"><h2>분류별 진도 <span class="small muted" style="font-weight:400">맞힌 문제 / 전체 · 첫 시도 정답률</span></h2>' +
    (D.cats.length ? D.cats.map(function (c) {
      return '<div class="prog-row"><a href="learn.html#/cat/' + c.key + '">' + esc(c.name) + '</a><div class="meter" aria-hidden="true"><i style="width:' + c.pct + '%"></i></div>' +
        '<span class="n">' + c.solved + '/' + c.total + '</span><span class="n acc">' + (c.acc === null ? '–' : c.acc + '%') + '</span></div>';
    }).join('') : '<div class="empty">문제가 없습니다.</div>') + '</section><div class="stack"><section class="card"><h2>다시 볼 문제</h2>' +
    (D.wrong_now.length ? '<ul class="list">' + D.wrong_now.map(function (p) {
      return '<li><span class="st bad" title="틀림">✕</span><div class="grow"><a class="title" href="learn.html#/' + encodeURIComponent(p.id) + '">' + esc(p.title) + '</a></div></li>';
    }).join('') + '</ul>' + (D.wrong_count > D.wrong_now.length ? '<p class="small"><a href="learn.html#/review">오답 노트에서 ' + D.wrong_count + '개 모두 보기 →</a></p>' : '') : '<div class="empty">마지막에 틀린 문제가 없습니다.</div>') + '</section></div></div>';
  if (D.recent.length) {
    h += '<section class="card" style="margin-top:14px"><h2>최근 풀이</h2><div class="table-wrap"><table class="t"><thead><tr><th>시각</th><th>문제</th><th>분류</th><th>결과</th></tr></thead><tbody>' +
      D.recent.map(function (r) {
        return '<tr><td class="nowrap muted">' + r.at + '</td><td><a href="learn.html#/' + encodeURIComponent(r.pid) + '">' + esc(r.p.title) + '</a></td><td>' + esc(EX.learn.catName(r.p)) + '</td><td>' +
          (r.ok ? '<span class="st ok">✓</span> 정답' : '<span class="st bad">✕</span> 오답') + '</td></tr>';
      }).join('') + '</tbody></table></div></section>';
  }
  h += '<h2 style="margin-top:22px">시험·참고 자료</h2><div class="grid g3">' +
    '<section class="card"><h2>컴활 필기</h2><div class="stat">' + wDone + '<span class="muted small" style="font-weight:400"> / ' + W.questions.length + '문제</span></div><div class="meter" aria-hidden="true"><i style="width:' + pct(wDone, W.questions.length) + '%"></i></div>' +
    '<p class="muted small" style="margin-top:6px">마지막 풀이 정답 ' + wOk + '개' + (last ? ' · 최근 모의고사 ' + last.average + '점 (' + (last.passed ? '합격 기준 충족' : '미달') + ')' : '') + '</p><a class="btn primary" href="written.html">필기 연습·모의고사</a></section>' +
    '<section class="card"><h2>문제 풀기</h2><div class="stat">' + lDone + '<span class="muted small" style="font-weight:400"> / ' + P.items.length + '문제</span></div><div class="meter" aria-hidden="true"><i style="width:' + pct(lDone, P.items.length) + '%"></i></div>' +
    '<p class="muted small" style="margin-top:6px">마지막 풀이 정답 ' + lOk + '개 · 함수와 기능 문제 (실무·컴활 2급·1급)</p><a class="btn primary" href="learn.html">문제 풀기</a></section>' +
    '<section class="card"><h2>참고 자료</h2><p class="muted small">함수 사전 ' + F.items.length + '개, 엑셀 단축키 ' + window.EXDATA.shortcuts.groups.reduce(function (t, g) { return t + g.items.length; }, 0) + '개를 검색해서 볼 수 있습니다.</p>' +
    '<div class="row"><a class="btn" href="functions.html">함수 사전</a><a class="btn" href="shortcuts.html">단축키</a></div></section></div>';

  var xr = EX.store.get('x.res', []), br = EX.store.get('b.res', []), pr = EX.store.get('p.res', []);
  var xBest = xr.reduce(function (m, r) { return Math.max(m, r.score); }, 0);
  h += '<div class="grid g3" style="margin-top:14px">' +
    '<section class="card"><h2>컴활 실기 모의고사</h2><p class="muted small">실제 시험 구성·배점의 문제 4회분(2급·1급). 문제 파일을 받아 Excel 에서 풀고 저장한 파일을 올리면 항목별로 채점합니다.</p>' +
    '<p class="small">' + (xr.length ? xr.length + '번 응시 · 최고 ' + xBest + '점' : '아직 응시하지 않음') + '</p><a class="btn primary" href="exam.html">실기 모의고사</a></section>' +
    '<section class="card"><h2>컴활 실기 실습</h2><p class="muted small">공식 예제(zip)나 내 교재의 실습·정답 파일을 가져와 풀고, 올리면 정답 파일과 비교해 항목별로 채점합니다.</p>' +
    '<p class="small">' + (pr.length ? pr.length + '번 제출' : '아직 제출하지 않음') + '</p><a class="btn primary" href="practice.html">실기 실습</a></section>' +
    '<section class="card"><h2>대시보드 실습</h2><p class="muted small">연습 파일의 노란 칸에 수식·차트·조건부 서식으로 대시보드를 완성해 올리면 칸마다 채점합니다.</p>' +
    '<p class="small">' + (br.length ? br.length + '번 제출' : '아직 제출하지 않음') + '</p><a class="btn primary" href="build.html">대시보드 실습</a></section>' +
    '<section class="card"><h2>파일 분석</h2><p class="muted small">엑셀·CSV 를 올리면 핵심 지표·분류별 집계·월별 추이·피벗 표를 만들고 같은 결과를 내는 엑셀 수식을 알려 줍니다.</p>' +
    '<p class="small">&nbsp;</p><a class="btn primary" href="analyze.html">파일 분석</a></section></div>';

  h += '<section class="card"><h2>이 HTML 버전에서 달라지는 점</h2><ul style="margin:0;padding-left:18px">' +
    '<li><b>서버 대신 브라우저 안의 파이썬</b> — 실기 모의고사·대시보드 실습·파일 분석·수식 값 채점은 Flask 버전의 채점·계산 코드를 그대로 브라우저(Pyodide)에서 돌립니다. 처음 쓸 때 약 13MB 를 읽고 이후엔 브라우저가 보관합니다. 올린 파일은 어디로도 전송되지 않습니다.</li>' +
    '<li><b>파일을 직접 열어도 됩니다</b> — 엔진 파일(py/embed)을 함께 읽기 때문에 <code>index.html</code> 을 더블클릭해 열어도(file://) 실기 모의고사·대시보드 실습·파일 분석·수식 값 채점이 됩니다. 처음 쓸 때 엔진을 읽느라 10초쯤 걸리고, 웹 주소(start.bat·정적 호스팅)에서는 더 빨리 뜹니다.</li>' +
    '<li><b>학습 기록</b> — 이 브라우저(localStorage·IndexedDB)에만 저장됩니다. 브라우저를 바꾸거나 사이트 데이터를 지우면 사라지니 [기록 내려받기]로 백업하거나, 아래 [기기 간 동기화]로 파일에 맞춰 두세요.</li>' +
    '<li><b>교재·공식 예제</b> — 저작물이라 저장소에 들어 있지 않습니다. [컴활 실기 실습]에서 내 PC 의 폴더·zip 을 가져오면 이 브라우저에만 보관됩니다(서버로 전송 안 함).</li></ul></section>';

  var syncOk = EX.sync && EX.sync.supported;
  h += '<section class="card"><h2>기기 간 동기화 <span class="small muted" style="font-weight:400">서버 없이 파일로 맞추기</span></h2>' +
    '<p class="small muted" style="margin-top:0">서버 대신 <b>동기화 파일 하나</b>를 OneDrive·Google Drive·Dropbox 같은 동기화 폴더에 두고 각 기기의 브라우저에서 연결합니다. 열 때·기록이 바뀔 때·1분마다 파일과 <b>합쳐서</b>(푼 기록은 많이 푼 쪽, 모의고사·제출 기록은 합집합) 다시 저장합니다. 분석 파일·실습 파일처럼 큰 자료는 포함되지 않습니다.</p>' +
    (syncOk ? '<div class="row"><button class="btn" id="s-new" type="button">새 동기화 파일 만들기</button><button class="btn" id="s-pick" type="button">기존 동기화 파일 고르기</button><button class="btn primary" id="s-now" type="button">지금 동기화</button><button class="btn ghost" id="s-off" type="button">연결 끊기</button></div>'
      : '<div class="alert info small">이 브라우저는 파일 연결(File System Access)을 지원하지 않습니다 — Chrome·Edge 에서 쓸 수 있습니다. 다른 브라우저에서는 아래 [기록 합쳐서 불러오기]로 파일을 옮기세요.</div>') +
    '<p class="small muted" id="s-msg" aria-live="polite" style="margin:8px 0 0"></p></section>';

  h += '<section class="card"><h2>기록 백업·복원</h2><div class="row"><button class="btn" id="b-export" type="button">기록 내려받기</button>' +
    '<label class="btn" for="b-file" style="cursor:pointer">기록 불러오기(바꾸기)</label><input type="file" id="b-file" accept=".json,application/json" class="visually-hidden">' +
    '<label class="btn" for="b-merge" style="cursor:pointer">기록 합쳐서 불러오기</label><input type="file" id="b-merge" accept=".json,application/json" class="visually-hidden">' +
    '<button class="btn" id="b-reset" type="button">기록 모두 지우기</button><span id="b-msg" class="muted small" aria-live="polite"></span></div></section>';
  main.innerHTML = h;
  EX.charts.draw(main, { daily: D.chart });

  var msg = document.getElementById('b-msg');
  document.getElementById('b-export').addEventListener('click', function () { EX.exportAll(); msg.textContent = '내려받았습니다.'; });
  document.getElementById('b-file').addEventListener('change', function (e) {
    var f = e.target.files[0];
    if (!f) return;
    EX.importAll(f, function (err, n) {
      if (err) { msg.textContent = err; return; }
      msg.textContent = '기록 ' + n + '묶음을 불러왔습니다.';
      setTimeout(function () { location.reload(); }, 600);
    });
  });
  document.getElementById('b-merge').addEventListener('change', function (e) {
    var f = e.target.files[0];
    if (!f) return;
    EX.importAll(f, function (err, n) {
      if (err) { msg.textContent = err; return; }
      msg.textContent = '내 기록과 합쳤습니다(새로 반영 ' + n + '묶음).';
      setTimeout(function () { location.reload(); }, 600);
    }, true);
  });
  var sm = document.getElementById('s-msg');
  if (sm) {
    var show = function (r) {
      sm.textContent = r && r.ok ? EX.sync.state.text : (r && r.message) || EX.sync.state.text || '';
      if (r && r.ok && r.changed) setTimeout(function () { location.reload(); }, 900);
    };
    EX.sync.handle().then(function (h) { sm.textContent = h ? '연결됨: ' + h.name + (EX.sync.state.text ? ' · ' + EX.sync.state.text : '') : '아직 연결되지 않았습니다.'; });
    var wrap = function (fn) { return function () { fn().then(show).catch(function (e) { if (e && e.name !== 'AbortError') sm.textContent = '실패: ' + e.message; }); }; };
    var bn = document.getElementById('s-new'), bp = document.getElementById('s-pick'), bs = document.getElementById('s-now'), bo = document.getElementById('s-off');
    if (bn) bn.addEventListener('click', wrap(function () { return EX.sync.connect(true); }));
    if (bp) bp.addEventListener('click', wrap(function () { return EX.sync.connect(false); }));
    if (bs) bs.addEventListener('click', wrap(function () { return EX.sync.run(true); }));
    if (bo) bo.addEventListener('click', function () { EX.sync.disconnect().then(function () { sm.textContent = '연결을 끊었습니다(파일은 그대로 남아 있습니다).'; }); });
  }
  document.getElementById('b-reset').addEventListener('click', function () {
    if (!window.confirm('이 브라우저에 저장된 학습 기록을 모두 지울까요? 되돌릴 수 없습니다.')) return;
    EX.store.keys().forEach(function (k) { if (k !== 'theme') EX.store.remove(k); });
    location.reload();
  });
})();
