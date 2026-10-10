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
  h += '<div class="grid g3">' +
    '<section class="card"><h2>컴활 필기</h2><div class="stat">' + wDone + '<span class="muted small" style="font-weight:400"> / ' + W.questions.length + '문제</span></div><div class="meter" aria-hidden="true"><i style="width:' + pct(wDone, W.questions.length) + '%"></i></div>' +
    '<p class="muted small" style="margin-top:6px">마지막 풀이 정답 ' + wOk + '개' + (last ? ' · 최근 모의고사 ' + last.average + '점 (' + (last.passed ? '합격 기준 충족' : '미달') + ')' : '') + '</p><a class="btn primary" href="written.html">필기 연습·모의고사</a></section>' +
    '<section class="card"><h2>문제 풀기</h2><div class="stat">' + lDone + '<span class="muted small" style="font-weight:400"> / ' + P.items.length + '문제</span></div><div class="meter" aria-hidden="true"><i style="width:' + pct(lDone, P.items.length) + '%"></i></div>' +
    '<p class="muted small" style="margin-top:6px">마지막 풀이 정답 ' + lOk + '개 · 함수와 기능 문제 (실무·컴활 2급·1급)</p><a class="btn primary" href="learn.html">문제 풀기</a></section>' +
    '<section class="card"><h2>참고 자료</h2><p class="muted small">함수 사전 ' + F.items.length + '개, 엑셀 단축키 ' + window.EXDATA.shortcuts.groups.reduce(function (t, g) { return t + g.items.length; }, 0) + '개를 검색해서 볼 수 있습니다.</p>' +
    '<div class="row"><a class="btn" href="functions.html">함수 사전</a><a class="btn" href="shortcuts.html">단축키</a></div></section></div>';

  h += '<section class="card"><h2>이 HTML 버전에서 달라지는 점</h2><ul style="margin:0;padding-left:18px">' +
    '<li><b>채점</b> — 선택형 문제는 서버 버전과 같습니다. 수식 문제는 서버의 엑셀 계산기 대신 정답 수식과 글자로 비교하고, 값이 같은 다른 수식은 스스로 판단합니다.</li>' +
    '<li><b>학습 기록</b> — 이 브라우저(localStorage)에만 저장됩니다. 브라우저를 바꾸거나 사이트 데이터를 지우면 사라지니, 아래 [기록 내려받기]로 백업하세요.</li>' +
    '<li><b>들어 있지 않은 것</b> — 엑셀 파일을 올려 채점하는 기능(컴활 실기 모의고사·실기 실습), 파일 분석, 대시보드 실습, 개인 교재 자료는 서버가 필요해 Flask 버전(study/EX)에서만 쓸 수 있습니다.</li></ul></section>';

  h += '<section class="card"><h2>기록 백업·복원</h2><div class="row"><button class="btn" id="b-export" type="button">기록 내려받기</button>' +
    '<label class="btn" for="b-file" style="cursor:pointer">기록 불러오기</label><input type="file" id="b-file" accept=".json,application/json" class="visually-hidden">' +
    '<button class="btn" id="b-reset" type="button">기록 모두 지우기</button><span id="b-msg" class="muted small" aria-live="polite"></span></div></section>';
  main.innerHTML = h;

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
  document.getElementById('b-reset').addEventListener('click', function () {
    if (!window.confirm('이 브라우저에 저장된 학습 기록을 모두 지울까요? 되돌릴 수 없습니다.')) return;
    EX.store.keys().forEach(function (k) { if (k !== 'theme') EX.store.remove(k); });
    location.reload();
  });
})();
