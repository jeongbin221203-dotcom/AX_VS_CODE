/* 브라우저 파이썬(Pyodide) 엔진: Flask 버전의 core/ 를 그대로 돌려 엑셀 파일 채점·분석·수식 계산을 한다.
   처음 부를 때만 약 13MB(런타임)를 읽고 이후엔 브라우저가 보관한다. http(s) 에서만 동작 — file:// 은 막혀 있다. */
(function () {
  'use strict';
  var EX = window.EX = window.EX || {};
  var base = (function () {
    var s = document.currentScript;
    return s ? s.src.replace(/engine\.js(\?.*)?$/, '') : 'py/';
  })();
  var state = null;      // Promise<pyodide>

  function loadScript(src) {
    return new Promise(function (ok, bad) {
      var s = document.createElement('script');
      s.src = src; s.onload = ok; s.onerror = function () { bad(new Error('불러오지 못했습니다: ' + src)); };
      document.head.appendChild(s);
    });
  }

  EX.py = {
    available: function () { return location.protocol === 'http:' || location.protocol === 'https:'; },
    ready: function (onStatus) {
      if (state) return state;
      var say = onStatus || function () {};
      state = (async function () {
        if (!EX.py.available()) throw new Error('이 기능은 파일을 직접 연 상태(file://)에서는 쓸 수 없습니다. start.bat 으로 열거나 인터넷 주소로 접속하세요.');
        say('엔진을 불러오는 중… (처음 한 번, 약 13MB)');
        await loadScript(base + 'pyodide/pyodide.js');
        var py = await window.loadPyodide({ indexURL: base + 'pyodide/' });
        say('채점 프로그램을 푸는 중…');
        var buf = await (await fetch(base + 'bundle.zip')).arrayBuffer();
        py.unpackArchive(buf, 'zip', { extractDir: '/' });
        py.runPython("import sys\nsys.path[:0] = ['/app', '/lib']\nimport os\nos.makedirs('/tmp', exist_ok=True)\nfrom core import bridge\n");
        return py;
      })();
      state.catch(function () { state = null; });
      return state;
    },
    /* 엔진 파일시스템의 파일 읽기(예: 공식 예제 꾸러미를 푼 /tmp/off/...) */
    readFile: async function (path) { var py = await EX.py.ready(); return py.FS.readFile(path); },
    /* 호출: 이름, 인수(JSON 으로 바뀜), 올릴 파일 바이트(선택) → {결과, file: Uint8Array|null} */
    call: async function (name, args, bytes, onStatus, more) {
      var py = await EX.py.ready(onStatus);
      if (bytes) py.FS.writeFile('/tmp/in.bin', bytes);
      if (more) Object.keys(more).forEach(function (k) { py.FS.writeFile('/tmp/' + k, more[k]); });   // 예: ans.bin(정답), user.bin(내 답안)
      var dispatch = py.pyimport('core.bridge').dispatch;
      var text;
      try { text = dispatch(name, JSON.stringify(args || {})); } finally { dispatch.destroy && dispatch.destroy(); }
      var res = JSON.parse(text);
      if (res && !res.error && /^(exam_file|exam_data|build_file|an_sample|an_export)$/.test(name)) {
        res.file = py.FS.readFile('/tmp/out.bin');
      }
      return res;
    }
  };

  /* 만든 파일 내려받기 */
  EX.download = function (bytes, name, type) {
    var blob = new Blob([bytes], { type: type || 'application/octet-stream' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 800);
  };
})();
