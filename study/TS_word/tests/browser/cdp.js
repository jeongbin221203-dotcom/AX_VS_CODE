// 최소 CDP 도우미 (node 22+ 내장 WebSocket, 추가 패키지 없음). 헤드리스 크롬/엣지를 띄워 화면을 직접 눌러 보는 점검용 — node --test 에는 포함되지 않는다.
// 실행: node tests/browser/smoke_pages.js | flow_practice.js | flow_real_mock.js | flow_resume.js  (환경변수 CHROME 으로 브라우저 경로 지정 가능)
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");

const CHROME = process.env.CHROME || ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium"].find(p => fs.existsSync(p));
const get = url => new Promise((res, rej) => http.get(url, r => { let d = ""; r.on("data", c => d += c); r.on("end", () => res(JSON.parse(d))); }).on("error", rej));
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function launch(port, profile, extra = []) {
  fs.rmSync(profile, { recursive: true, force: true });
  const proc = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--no-first-run", "--disable-gpu",
    "--autoplay-policy=no-user-gesture-required", "--window-size=1280,900", ...extra, "about:blank"], { stdio: "ignore" });
  for (let i = 0; i < 50; i++) { try { await get(`http://127.0.0.1:${port}/json/version`); break; } catch (e) { await sleep(200); } }
  return {
    proc,
    async newPage() {
      const t = (await get(`http://127.0.0.1:${port}/json`)).find(x => x.type === "page");
      return page(t.webSocketDebuggerUrl);
    },
    close() { try { proc.kill(); } catch (e) { /* 무시 */ } },
  };
}

function page(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  const logs = [];
  const handlers = [];
  const ready = new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(m.error.message)) : res(m.result); }
    else if (m.method === "Runtime.exceptionThrown") logs.push("EXC " + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
    else if (m.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(m.params.type)) logs.push(m.params.type.toUpperCase() + " " + m.params.args.map(a => a.value ?? a.description).join(" "));
    else if (m.method === "Log.entryAdded" && m.params.entry.level === "error") logs.push("LOG " + m.params.entry.text + " " + (m.params.entry.url || ""));
    for (const h of handlers) h(m);
  };
  const send = (method, params = {}) => ready.then(() => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); }));
  const api = {
    send, logs,
    async init() { await send("Runtime.enable"); await send("Page.enable"); await send("Log.enable"); return api; },
    async goto(url) { await send("Page.navigate", { url }); await sleep(300); await api.waitFor("document.readyState === 'complete'", 15000); },
    async eval(expr) {
      const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    },
    async waitFor(expr, ms = 10000) {
      const t0 = Date.now();
      for (;;) {
        let ok = false;
        try { ok = await api.eval(`!!(${expr})`); } catch (e) { /* 페이지 이동 중 */ }
        if (ok) return true;
        if (Date.now() - t0 > ms) throw new Error("waitFor 시간 초과: " + expr + "\n" + logs.slice(-5).join("\n"));
        await sleep(100);
      }
    },
    async click(sel) { await api.eval(`document.querySelector(${JSON.stringify(sel)}).click()`); },
    async shot(path) { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path, Buffer.from(r.data, "base64")); },
    onEvent(h) { handlers.push(h); },
    close() { try { ws.close(); } catch (e) { /* 무시 */ } },
  };
  return api.init();
}
module.exports = { launch, sleep };
