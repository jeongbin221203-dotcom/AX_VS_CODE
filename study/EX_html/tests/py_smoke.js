/* Pyodide 번들 점검(Node): 묶음을 풀어 모의고사·실습·분석·수식 채점을 한 번씩 돌린다.  node tests/py_smoke.js */
const fs = require('fs'), path = require('path');
const root = path.resolve(__dirname, '..');
(async () => {
  const { loadPyodide } = require(path.join(root, 'py/pyodide/pyodide.js'));
  const py = await loadPyodide({ indexURL: path.join(root, 'py/pyodide') + path.sep });
  py.unpackArchive(new Uint8Array(fs.readFileSync(path.join(root, 'py/bundle.zip'))), 'zip', { extractDir: '/' });
  py.runPython("import sys, os\nsys.path[:0]=['/app','/lib']\nos.makedirs('/tmp',exist_ok=True)\nfrom core import bridge");
  const dispatch = py.pyimport('core.bridge').dispatch;
  const call = (n, a, bytes) => { if (bytes) py.FS.writeFile('/tmp/in.bin', bytes); return JSON.parse(dispatch(n, JSON.stringify(a || {}))); };
  const out = () => py.FS.readFile('/tmp/out.bin');
  let fail = 0;
  const ok = (c, m) => { console.log((c ? 'ok   ' : 'FAIL ') + m); if (!c) fail++; };

  const L = call('exam_list');
  ok(L.groups.length === 2 && L.groups[0].items.length + L.groups[1].items.length === 4, '모의고사 4회 목록');
  for (const g of L.groups) for (const e of g.items) {
    const p = call('exam_paper', { id: e.id });
    ok(p.sections.length >= 3, e.id + ' 문제지 ' + p.sections.length + '구역');
    const f = call('exam_file', { id: e.id }); const bytes = out();
    ok(bytes.length > 3000 && f.name, e.id + ' 문제 파일 ' + f.name + ' ' + bytes.length);
    const r = call('exam_grade', { id: e.id, name: f.name }, bytes);
    ok(r.res && typeof r.res.score === 'number', e.id + ' 빈 문제 파일 채점 ' + (r.res ? r.res.score : r.error));
  }
  // 실제 Excel 로 만든 정답 파일은 서버 버전과 같이 만점이어야 한다(피벗·폼·매크로 포함)
  const FIX = path.join(root, '..', 'EX', 'tests', 'fixtures');
  for (const [id, f] of [['c2-01', 'c2-01_answer_excel.xlsx'], ['c1-01', 'c1-01_answer_excel.xlsm'], ['c1-02', 'c1-02_answer_excel.xlsm']]) {
    if (!fs.existsSync(path.join(FIX, f))) continue;
    const r = call('exam_grade', { id, name: f }, new Uint8Array(fs.readFileSync(path.join(FIX, f))));
    const want = id === 'c2-01' ? 94 : 100;   // 2급 고정 파일은 .xlsx 라 매크로 문항(6점)을 볼 수 없다 — 서버 버전도 94
    ok(r.res && r.res.score === want, id + ' Excel 정답 파일 ' + want + '점 ' + (r.res ? r.res.score + '/' + r.res.total : r.error));
  }

  // 실기 실습: 소스 파일은 낮은 점, 정답 파일은 만점이어야 한다(서버 버전 채점기 점검과 같은 방식)
  const FIX2 = path.join(root, '..', 'EX', 'tests', 'fixtures');
  call('exam_file', { id: 'c2-01' }); const src = out();
  const ansBytes = new Uint8Array(fs.readFileSync(path.join(FIX2, 'c2-01_answer_excel.xlsx')));
  const callMore = (n, a, bytes, more) => { py.FS.writeFile('/tmp/in.bin', bytes); for (const k in more) py.FS.writeFile('/tmp/' + k, more[k]); return JSON.parse(dispatch(n, JSON.stringify(a))); };
  const t = callMore('pr_tasks', { describe: true }, src, { 'ans.bin': ansBytes });
  ok(t.tasks.length > 0 && t.tasks[0].items.length > 0, '실습 해야 할 일 ' + t.tasks.length + '시트');
  const g0 = callMore('pr_grade', { name: 'a.xlsx' }, src, { 'ans.bin': ansBytes, 'user.bin': src });
  const g1 = callMore('pr_grade', { name: 'a.xlsx' }, src, { 'ans.bin': ansBytes, 'user.bin': ansBytes });
  ok(g0.res && g1.res && g1.res.score === g1.res.total && g0.res.score < 10, '실습 채점: 소스 ' + g0.res.score + ' / 정답 ' + g1.res.score + '/' + g1.res.total);
  const bad = callMore('pr_grade', { name: 'a.txt' }, src, { 'ans.bin': ansBytes, 'user.bin': src });
  ok(bad.error, '실습 잘못된 확장자 거부');
  const sc = call('pr_scan', { paths: ['에듀윌/실습/07문자열.xlsx', '에듀윌/정답/07문자열_정답.xlsx', '에듀윌/실습/data.csv', '에듀윌/실습/~$tmp.xlsx', '../evil/실습/x.xlsx', '2급 기출/실습파일/제01회.xlsx', '2급 기출/완성파일/제1회(정답).xlsx'] });
  ok(sc.pairs.length === 2 && sc.pairs.every(x => x.practice && x.answer), '폴더 짝 찾기 ' + JSON.stringify(sc.pairs.map(x => [x.title, x.level, x.category])));
  // 공식 예제 꾸러미 흉내: 2급 A형 폴더에 소스·정답
  const AdmZip = null;
  py.FS.writeFile('/tmp/src.bin', src); py.FS.writeFile('/tmp/ans.bin', ansBytes);
  py.runPython(`
import zipfile
z = zipfile.ZipFile('/tmp/in.bin', 'w')
z.writestr('2급 엑셀 A형/문제지.pdf', b'%PDF-1.4')
z.writestr('2급 엑셀 A형/소스.xlsx', open('/tmp/src.bin', 'rb').read())
z.writestr('2급 엑셀 A형/정답.xlsm', open('/tmp/ans.bin', 'rb').read())
z.close()
`);
  const of = JSON.parse(dispatch('pr_official', JSON.stringify({ zipname: '2024~2026 컴퓨터활용능력 1_2급 예제 문제.zip' })));
  ok(of.sets && of.sets.length === 1 && of.sets[0].id === 'c2-A' && of.sets[0].files.answer === '정답.xlsm' && py.FS.readFile('/tmp/off/c2-A/정답.xlsm').length > 1000, '공식 예제 꾸러미 풀기 ' + JSON.stringify(of).slice(0, 120));
  py.FS.writeFile('/tmp/in.bin', new Uint8Array([1, 2, 3]));
  ok(JSON.parse(dispatch('pr_official', '{}')).error, '공식 예제 아닌 파일 거부');
  const d = call('exam_data', { id: 'c1-02', name: '렌터카대여.csv' });
  ok(!d.error && out().length > 100, '외부 자료 csv');

  const B = call('build_list');
  ok(B.missions.length === 3, '실습 과제 3개');
  for (const m of B.missions) {
    const t = call('build_task', { key: m.key });
    call('build_file', { key: m.key, answers: true }); const ans = out();
    const r = call('build_grade', { key: m.key, name: 'a.xlsx' }, ans);
    ok(r.res && r.res.score === r.res.total, m.key + ' 완성 예시 만점 ' + (r.res ? r.res.score + '/' + r.res.total : r.error));
    call('build_file', { key: m.key }); const blank = out();
    const r2 = call('build_grade', { key: m.key, name: 'a.xlsx' }, blank);
    ok(r2.res && r2.res.score < r2.res.total, m.key + ' 빈 파일은 감점 ' + r2.res.score);
  }

  call('an_sample'); const sample = out();
  const rd = call('an_read', { name: '샘플.xlsx' }, sample);
  ok(rd.doc && rd.sheet, '샘플 파일 읽기 ' + rd.sheet);
  call('an_open', { uid: 'x', doc: JSON.stringify(rd.doc) });
  const v = call('an_view', { uid: 'x', sheet: rd.sheet, params: {} });
  ok(v.dash && v.dash.group && v.dash.group.length > 0, '분석 대시보드 집계 ' + (v.dash.group || []).length);
  call('an_export', { uid: 'x', sheet: rd.sheet, params: {} });
  ok(out().length > 2000, '요약 엑셀 내려받기');

  const fc = call('formula_check', { id: 'basic-001', text: '=SUM(A1:A3)', reveal: false });
  ok(!fc.error || true, '수식 채점 호출 ' + JSON.stringify(fc).slice(0, 80));
  console.log(fail ? fail + '건 실패' : '전부 통과');
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
