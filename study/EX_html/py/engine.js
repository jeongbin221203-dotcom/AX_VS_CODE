/* 브라우저 파이썬(Pyodide) 엔진: Flask 버전의 core/ 를 그대로 돌려 엑셀 파일 채점·분석·수식 계산을 한다.
   처음 부를 때만 약 13MB(런타임)를 읽고 이후엔 브라우저가 보관한다. file:// 에서는 py/embed/*.js(base64)로 읽는다. */
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

  /* file:// 에서는 fetch 가 막히므로, 엔진 파일을 base64 스크립트(py/embed/*.js)로 읽어 메모리 Blob 으로 만들고
     엔진이 같은 이름의 파일을 fetch 할 때 그 Blob 을 돌려준다. */
  var EMBED = ['pyodide.asm.wasm', 'python_stdlib.zip', 'pyodide-lock.json', 'bundle.zip'];
  var MIME = { wasm: 'application/wasm', js: 'text/javascript', zip: 'application/zip', json: 'application/json' };
  var embedded = null;
  function b64bytes(s) {
    var bin = atob(s), u = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return u;
  }
  async function useEmbedded(say) {
    if (embedded) return embedded;
    embedded = (async function () {
      var urls = {};
      for (var i = 0; i < EMBED.length; i++) {
        var n = EMBED[i];
        say('엔진 파일을 읽는 중… (' + (i + 1) + '/' + EMBED.length + ')');
        await loadScript(base + 'embed/' + n + '.js');
        urls[n] = URL.createObjectURL(new Blob([b64bytes(window.EXEMBED[n])], { type: MIME[n.split('.').pop()] }));
        delete window.EXEMBED[n];
      }
      await loadScript(base + 'pyodide/pyodide.asm.js');   // import() 는 file:// 에서 막히므로 일반 스크립트로 먼저 올려 둔다
      var orig = window.fetch;
      window.fetch = function (input, init) {
        var u = typeof input === 'string' ? input : (input && input.url) || String(input);
        var name = u.split('?')[0].split('/').pop();
        if (urls[name] && /^file:/.test(u)) return orig.call(window, urls[name]);
        return orig.apply(window, arguments);
      };
      return urls;
    })();
    embedded.catch(function () { embedded = null; });
    return embedded;
  }

  EX.py = {
    available: function () { return true; },
    ready: function (onStatus) {
      if (state) return state;
      var say = onStatus || function () {};
      state = (async function () {
        say('엔진을 불러오는 중… (처음 한 번, 약 13MB)');
        if (location.protocol === 'file:') await useEmbedded(say);
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
