/* 수식 문제 전부: 정답·대체 정답은 정답, 흔한 오답은 오답으로 나와야 한다.  node tests/py_formula_all.js */
const fs = require('fs'), path = require('path');
const root = path.resolve(__dirname, '..');
(async () => {
  const { loadPyodide } = require(path.join(root, 'py/pyodide/pyodide.js'));
  const py = await loadPyodide({ indexURL: path.join(root, 'py/pyodide') + path.sep });
  py.unpackArchive(new Uint8Array(fs.readFileSync(path.join(root, 'py/bundle.zip'))), 'zip', { extractDir: '/' });
  py.runPython("import sys, os\nsys.path[:0]=['/app','/lib']\nos.makedirs('/tmp',exist_ok=True)\nfrom core import bridge");
  const dispatch = py.pyimport('core.bridge').dispatch;
  const call = (n, a) => JSON.parse(dispatch(n, JSON.stringify(a)));
  const src = fs.readFileSync(path.join(root, 'data/problems.js'), 'utf8');
  const P = JSON.parse(src.split('EXDATA.problems = ')[1].trim().replace(/;$/, '')).items.filter(p => p.type === 'formula');
  let bad = 0, wrongOk = 0, n = 0;
  for (const p of P) {
    for (const a of [p.answer, ...(p.alts || [])]) {
      n++; const r = call('formula_check', { id: p.id, text: a, reveal: true });
      if (!r.ok) { bad++; console.log('정답이 오답 처리:', p.id, a, (r.error || (r.notes || []).join('|')).slice(0, 100)); }
    }
    for (const w of (p.wrong || [])) {
      const r = call('formula_check', { id: p.id, text: w, reveal: true });
      if (r.ok) { wrongOk++; console.log('오답이 정답 처리:', p.id, w); }
    }
  }
  console.log(`수식 문제 ${P.length}개, 정답 ${n}건 중 오처리 ${bad}, 오답 정답처리 ${wrongOk}`);
  process.exit(bad || wrongOk ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
