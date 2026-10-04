/* 자재 찾기 · 바코드 스캔 · 여러 줄 입출고.

   자재 찾기 (data-picker)
   - 자재코드·이름·규격·바코드·SAP 번호의 일부를 치면 바로 목록이 뜬다(띄어 쓴 낱말은 모두 들어 있어야 함).
     ↑↓로 고르고 Enter. 자재가 수천 개여도 서버에 매번 묻지 않는다 — 처음 한 번 받은 목록(/materials/lookup.json)에서 찾는다.
   - USB·블루투스 바코드 스캐너는 키보드처럼 코드를 치고 Enter를 누르므로, 코드·바코드·SAP 번호가 정확히 같으면 바로 고른다.
   - 📷: 휴대폰·태블릿 카메라로 바코드를 찍는다(브라우저가 BarcodeDetector를 지원할 때만 보인다 — 안드로이드 크롬 등, HTTPS).
   - data-mode="select": 숨은 칸(name)에 자재 id를 넣는다. data-autosubmit-pick 이면 고르자마자 조회(입출고 화면의 자재 선택).
     data-mode="add": 고른 자재를 'mm:pick' 이벤트로 알린다(여러 줄 입출고 — 같은 자재면 수량 +1). 카메라는 계속 찍는다.
   - 목록은 이 브라우저에 사용자별로 보관해 두어 서버 연결이 끊겨도 찾을 수 있다.

   여러 줄 입출고 (form[data-batch]) — 찍을 때마다 줄이 생기고, 같은 자재는 수량이 1씩 는다.
   CSP 때문에 인라인 스크립트 없이 이 파일이 문서 전체에서 이벤트를 받는다(화면을 바꿔 끼워도 그대로 동작). */
(function () {
  "use strict";
  const USER = document.body.dataset.user || "";
  if (!USER) return;
  const DEFAULT_SRC = "/materials/lookup.json";
  const lists = {}, pending = {};
  let items = [];                                        // 지금 쓰는 picker 의 목록 (load 가 채움)

  function key(src) { return "mm-mat-lookup:" + USER + ":" + src; }
  function cacheGet(src) { try { return JSON.parse(localStorage.getItem(key(src)) || "null"); } catch (e) { return null; } }
  function cachePut(src, v) { try { localStorage.setItem(key(src), JSON.stringify(v)); } catch (e) { /* 저장소가 막혀도 동작 */ } }

  function prepare(rows) {
    return rows.map(function (r) {
      const it = { id: r[0], code: r[1], name: r[2], spec: r[3] || "", unit: r[4] || "", barcode: r[5] || "",
                   sap: r[6] || "", lot: !!r[7], price: r[8] || 0, expiry: !!r[9],
                   units: (r[10] || []).map(function (u) { return { unit: u[0], factor: u[1], barcode: u[2] || "" }; }) };
      it.hay = (it.code + " " + it.name + " " + it.spec + " " + it.barcode + " " + it.sap + " " +
                it.units.map(function (u) { return u.barcode; }).join(" ")).toLowerCase();
      it.label = "[" + it.code + "] " + it.name + (it.spec ? " (" + it.spec + ")" : "");
      return it;
    });
  }
  function srcOf(p) { return (p && p.dataset.src) || DEFAULT_SRC; }
  // 목록은 화면(주소)마다 한 번 받는다. 서버에 닿지 않으면 이 브라우저에 보관한 목록으로 찾는다.
  function load(p) {
    const src = srcOf(p);
    if (lists[src]) { items = lists[src]; return Promise.resolve(items); }
    if (pending[src]) return pending[src];
    pending[src] = fetch(src, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { if (!r.ok || r.redirected) throw new Error("lookup"); return r.json(); })
      .then(function (data) {
        if (data.remote) { const r = []; r.remote = src.replace("lookup.json", "search.json"); return r; }  // 서버에서 찾기
        cachePut(src, data);
        return prepare(data.items);
      })
      .catch(function () { const c = cacheGet(src); return c ? prepare(c.items) : []; })
      .then(function (list) { lists[src] = list; items = list; delete pending[src]; return list; });
    return pending[src];
  }

  // 정확히 같은 코드·바코드·SAP 번호 → {it, unit}. 상자(단위) 바코드면 그 단위.
  function exact(q) {
    const u = q.trim().toUpperCase();
    if (!u) return null;
    const it = items.find(function (x) { return x.code.toUpperCase() === u || (x.barcode && x.barcode === u) ||
                                                (x.sap && x.sap.toUpperCase() === u); });
    if (it) return { it: it, unit: "" };
    for (let i = 0; i < items.length; i++) {
      const un = items[i].units.find(function (x) { return x.barcode && x.barcode === u; });
      if (un) return { it: items[i], unit: un.unit };
    }
    return null;
  }
  // 자재가 아주 많으면(서버 설정 MM_LOOKUP_MAX) 목록을 받지 않고 서버에 묻는다
  const known = {};
  function remember(list) { list.forEach(function (it) { known[it.id] = it; }); return list; }
  function remoteGet(params) {
    const sep = items.remote.indexOf("?") >= 0 ? "&" : "?";
    return fetch(items.remote + sep + params, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.json(); });
  }
  function searchAsync(q) {
    if (!items.remote) return Promise.resolve(search(q));
    if (!q.trim()) return Promise.resolve([]);
    return remoteGet("q=" + encodeURIComponent(q)).then(function (d) { return remember(prepare(d.items)); })
      .catch(function () { return []; });
  }
  function exactAsync(q) {
    if (!items.remote) return Promise.resolve(exact(q));
    if (!q.trim()) return Promise.resolve(null);
    return remoteGet("exact=" + encodeURIComponent(q.trim())).then(function (d) {
      const list = remember(prepare(d.items));
      return list.length ? { it: list[0], unit: d.unit || "" } : null;
    }).catch(function () { return null; });
  }
  // '24*코드'·'24 x 코드' → 이번 한 번만 수량 24
  function splitQty(q) {
    const m = q.match(/^\s*(\d+(?:\.\d+)?)\s*[*xX×]\s*(\S.*)$/);
    return m ? { qty: parseFloat(m[1]), q: m[2] } : { qty: null, q: q };
  }
  function search(q) {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return items.slice(0, 30);
    const hit = items.filter(function (it) { return words.every(function (w) { return it.hay.indexOf(w) >= 0; }); });
    const u = q.trim().toLowerCase();
    hit.sort(function (a, b) {
      const ra = a.code.toLowerCase().indexOf(u) === 0 ? 0 : 1, rb = b.code.toLowerCase().indexOf(u) === 0 ? 0 : 1;
      return ra - rb || (a.code < b.code ? -1 : 1);
    });
    return hit.slice(0, 30);
  }

  function parts(p) {
    return { q: p.querySelector(".picker-q"), list: p.querySelector(".picker-list"),
             hidden: p.querySelector("input[type=hidden]"), mode: p.dataset.mode || "select" };
  }
  function show(p, found, note) {
    const x = parts(p);
    x.list.innerHTML = "";
    if (note) {
      const li = document.createElement("li");
      li.className = "picker-note";
      li.textContent = note;
      x.list.appendChild(li);
    }
    found.forEach(function (it, i) {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.dataset.id = it.id;
      li.className = i === 0 ? "active" : "";
      const code = document.createElement("strong");
      code.textContent = it.code;
      const rest = document.createElement("span");
      rest.textContent = " " + it.name + (it.spec ? " · " + it.spec : "") + (it.barcode ? " · " + it.barcode : "");
      li.appendChild(code);
      li.appendChild(rest);
      x.list.appendChild(li);
    });
    x.list.hidden = !found.length && !note;
  }
  function hide(p) { const l = p.querySelector(".picker-list"); if (l) l.hidden = true; }
  function byId(id) { return known[id] || items.find(function (it) { return String(it.id) === String(id); }); }

  function pick(p, it, extra) {
    const x = parts(p);
    hide(p);
    if (x.mode === "add") {
      x.q.value = "";
      const d = Object.assign({}, it, { scanUnit: (extra && extra.unit) || "", scanQty: extra ? extra.qty : null });
      p.dispatchEvent(new CustomEvent("mm:pick", { bubbles: true, detail: d }));
      x.q.focus();
      return;
    }
    x.q.value = it.label;
    if (x.hidden) {
      x.hidden.value = it.id;
      x.hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (p.hasAttribute("data-autosubmit-pick")) {
      const form = p.closest("form");
      if (window.mmNet && window.mmNet.down && form.hasAttribute("data-offline-select")) {
        // 끊긴 동안: 화면을 다시 받지 않고 입력 폼의 자재만 바꾼다 (app.js 오프라인 입력)
        window.mmNet.switchSelect({ name: x.hidden.name, value: String(it.id), selectedIndex: 0, options: [{ text: it.label }] });
        return;
      }
      // 상자 바코드(단위)·'24*코드'(수량)는 화면을 다시 받을 때 함께 넘겨 그 단위·수량으로 채운다
      [["unit", extra && extra.unit], ["qty", extra && extra.qty != null ? String(extra.qty) : ""]].forEach(function (kv) {
        let el = form.querySelector("input[type=hidden][name=" + kv[0] + "]");
        if (!kv[1]) { if (el) el.remove(); return; }
        if (!el) { el = document.createElement("input"); el.type = "hidden"; el.name = kv[0]; form.appendChild(el); }
        el.value = kv[1];
      });
      form.submit();
    }
  }
  function notFound(p, q) {
    show(p, [], "찾지 못했습니다: " + q + " — 자재 마스터의 코드·바코드를 확인하세요.");
    p.classList.add("picker-miss");
    setTimeout(function () { p.classList.remove("picker-miss"); }, 900);
  }
  function enter(p) {
    const x = parts(p);
    const sq = x.mode === "add" || p.hasAttribute("data-autosubmit-pick") ? splitQty(x.q.value) : { qty: null, q: x.q.value };
    const q = sq.q;
    load(p).then(function () { return exactAsync(q); }).then(function (ex) {
      const active = x.list.hidden ? null : x.list.querySelector("li.active[data-id]");
      if (ex) return pick(p, ex.it, { unit: ex.unit, qty: sq.qty });      // 스캐너·코드 직접 입력
      if (active) return pick(p, byId(active.dataset.id), { unit: "", qty: sq.qty });
      return searchAsync(q).then(function (found) { return finish(found); });
    });
    function finish(found) {
      if (found.length === 1) return pick(p, found[0], { unit: "", qty: sq.qty });
      if (!found.length) return notFound(p, q.trim());
      show(p, found);
    }
  }

  document.addEventListener("input", function (e) {
    const p = e.target.closest && e.target.closest("[data-picker]");
    if (!p || !e.target.classList.contains("picker-q")) return;
    const x = parts(p);
    if (x.hidden && x.mode === "select") x.hidden.value = "";      // 글자를 바꾸면 고른 자재를 비운다
    clearTimeout(p._t);
    p._t = setTimeout(function () {                                  // 서버에서 찾을 때 글자마다 묻지 않게
      const q = x.q.value;
      load(p).then(function () { return searchAsync(q); }).then(function (found) {
        if (x.q.value === q) show(p, found);
      });
    }, items.remote ? 250 : 0);
  });
  let justFocused = null;
  document.addEventListener("mouseup", function (e) {
    if (justFocused && e.target === justFocused) e.preventDefault();
    justFocused = null;
  });
  document.addEventListener("focusin", function (e) {
    const p = e.target.closest && e.target.closest("[data-picker]");
    if (p && e.target.classList.contains("picker-q")) {
      // 이미 고른 자재 이름이 들어 있으면 전체 선택 → 스캔·입력이 그 이름을 바꿔 쓴다(뒤에 붙지 않게).
      // 마우스로 눌러 들어온 경우 뒤따르는 mouseup 이 선택을 풀지 않게 한 번 막는다.
      if (e.target.value) {
        justFocused = e.target;
        setTimeout(function () { if (document.activeElement === e.target) e.target.select(); }, 0);
      }
      load(p).then(function () { if (document.activeElement === e.target && e.target.value === "") show(p, search("")); });
    }
  });
  document.addEventListener("keydown", function (e) {
    const p = e.target.closest && e.target.closest("[data-picker]");
    if (!p || !e.target.classList.contains("picker-q")) return;
    const x = parts(p);
    if (e.key === "Enter") { e.preventDefault(); enter(p); return; }            // 폼 제출 대신 자재 고르기
    if (e.key === "Escape") { hide(p); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const opts = Array.prototype.slice.call(x.list.querySelectorAll("li[data-id]"));
    if (!opts.length) return;
    let i = opts.findIndex(function (li) { return li.classList.contains("active"); });
    i = e.key === "ArrowDown" ? Math.min(i + 1, opts.length - 1) : Math.max(i - 1, 0);
    opts.forEach(function (li, j) { li.classList.toggle("active", i === j); });
    opts[i].scrollIntoView({ block: "nearest" });
  });
  document.addEventListener("mousedown", function (e) {           // blur 보다 먼저 고른다
    const li = e.target.closest && e.target.closest(".picker-list li[data-id]");
    if (!li) return;
    e.preventDefault();
    const p = li.closest("[data-picker]");
    load(p).then(function () { pick(p, byId(li.dataset.id)); });
  });
  document.addEventListener("focusout", function (e) {
    const p = e.target.closest && e.target.closest("[data-picker]");
    if (p) setTimeout(function () { if (!p.contains(document.activeElement)) hide(p); }, 150);
  });
  // 고르지 않고 글자만 남긴 채 제출하면 막는다 (window 단계에서 — app.js의 두 번 제출 표시보다 먼저)
  window.addEventListener("submit", function (e) {
    const bad = Array.prototype.find.call(e.target.querySelectorAll("[data-picker][data-mode=select]"), function (p) {
      const x = parts(p);
      return x.hidden && !x.hidden.value && (x.q.value.trim() || x.q.required);
    });
    if (bad) {
      e.preventDefault();
      e.stopImmediatePropagation();
      parts(bad).q.focus();
      notFound(bad, parts(bad).q.value.trim() || "(비어 있음)");
    }
  }, true);

  // ── 카메라 바코드 ──
  // 브라우저에 바코드 인식(BarcodeDetector: 안드로이드 크롬 등)이 있으면 그것을, 없으면(아이폰 Safari·PC 크롬 등)
  // 같은 서버의 ZXing(static/vendor, 처음 📷를 누를 때만 받음)으로 읽는다. 카메라는 HTTPS(또는 localhost)에서만 열린다.
  const ZXING = "/static/vendor/zxing-library-0.21.3.min.js";
  const canScan = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) && window.isSecureContext !== false;
  if (canScan) document.documentElement.classList.add("can-scan");
  let scanning = null, zxingLoading = null;
  function loadZxing() {
    if (window.ZXing) return Promise.resolve(window.ZXing);
    if (zxingLoading) return zxingLoading;
    zxingLoading = new Promise(function (ok, fail) {
      const sc = document.createElement("script");
      sc.src = ZXING;
      sc.onload = function () { window.ZXing ? ok(window.ZXing) : fail(new Error("ZXing")); };
      sc.onerror = function () { zxingLoading = null; fail(new Error("ZXing")); };
      document.head.appendChild(sc);
    });
    return zxingLoading;
  }
  function stopScan() {
    if (!scanning) return;
    scanning.stop = true;
    if (scanning.reader) { try { scanning.reader.reset(); } catch (e) { /* 무시 */ } }
    if (scanning.stream) scanning.stream.getTracks().forEach(function (t) { t.stop(); });
    scanning.box.remove();
    scanning = null;
  }
  function onCode(p, st, msg, raw) {
    raw = String(raw || "").trim();
    const now = Date.now();
    if (!raw || (raw === st.last && now - st.lastAt < 1500)) return;     // 같은 상자를 계속 비추면 1.5초에 한 번
    st.last = raw; st.lastAt = now;
    load(p).then(function () { return exactAsync(raw); }).then(function (ex) {
      if (!ex) { msg.textContent = "등록되지 않은 바코드: " + raw; return; }
      if (navigator.vibrate) navigator.vibrate(60);
      if (parts(p).mode === "add") {
        pick(p, ex.it, { unit: ex.unit, qty: null });
        msg.textContent = "추가: " + ex.it.label + (ex.unit ? " · " + ex.unit : "");
      } else { stopScan(); pick(p, ex.it); }
    });
  }
  function camError(msg, err) {
    msg.textContent = "카메라를 쓸 수 없습니다 (" + (err && err.name || "오류") + "). 브라우저의 카메라 권한을 확인하세요" +
                      (location.protocol === "https:" ? "." : " (카메라는 https 주소에서만 열립니다).");
  }
  function startScan(p) {
    stopScan();
    const box = document.createElement("div");
    box.className = "scan-overlay";
    box.innerHTML = "<div class='scan-frame'><video playsinline muted></video><p class='scan-msg'>바코드를 화면 가운데에 비춰 주세요</p>" +
                    "<button type='button' class='btn' data-scan-close>닫기</button></div>";
    document.body.appendChild(box);
    const video = box.querySelector("video"), msg = box.querySelector(".scan-msg");
    const st = scanning = { box: box, stop: false, last: "", lastAt: 0 };
    const constraints = { video: { facingMode: "environment" }, audio: false };
    if ("BarcodeDetector" in window) {
      Promise.all([navigator.mediaDevices.getUserMedia(constraints),
                   window.BarcodeDetector.getSupportedFormats ? window.BarcodeDetector.getSupportedFormats() : []])
        .then(function (r) {
          if (st.stop) { r[0].getTracks().forEach(function (t) { t.stop(); }); return; }
          st.stream = r[0];
          video.srcObject = r[0];
          video.play();
          const detector = r[1].length ? new window.BarcodeDetector({ formats: r[1] }) : new window.BarcodeDetector();
          const tick = function () {
            if (st.stop) return;
            detector.detect(video).then(function (codes) { if (codes.length) onCode(p, st, msg, codes[0].rawValue); })
              .catch(function () { /* 프레임 하나 실패는 무시 */ }).finally(function () { setTimeout(tick, 200); });
          };
          tick();
        })
        .catch(function (err) { camError(msg, err); });
      return;
    }
    msg.textContent = "바코드 인식기를 준비하는 중…";
    loadZxing().then(function (Z) {
      if (st.stop) return;
      const reader = st.reader = new Z.BrowserMultiFormatReader();
      msg.textContent = "바코드를 화면 가운데에 비춰 주세요";
      return reader.decodeFromConstraints(constraints, video, function (result) {
        if (result && !st.stop) onCode(p, st, msg, result.getText());
      });
    }).catch(function (err) {
      if (err && err.message === "ZXing") msg.textContent = "바코드 인식기를 받지 못했습니다. 연결을 확인하거나 바코드를 직접 입력하세요.";
      else camError(msg, err);
    });
  }
  document.addEventListener("click", function (e) {
    const b = e.target.closest && e.target.closest("[data-scan]");
    if (b) { e.preventDefault(); startScan(b.closest("[data-picker]")); return; }
    if (e.target.closest && e.target.closest("[data-scan-close]")) stopScan();
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") stopScan(); });

  // ── 여러 줄 입출고 ──
  // 줄마다 그 창고 현재고(출고면 모자란 줄 빨강) · '발주 불러오기'(입고, 잔량을 줄로, 발주 번호는 line_po = "PO|품목")
  // 찍던 줄은 이 브라우저에 사용자별로 보관한다(연결 끊김·실수로 닫음) → 다시 열면 '되살리기'. 등록에 성공하면 지운다.
  const BKEY = "mm-batch:" + USER;
  function money(v) { return "₩" + Math.round(v).toLocaleString("ko-KR"); }
  function batchOf(el) { return el.closest && el.closest("form[data-batch]"); }
  function rowsOf(form) { return Array.prototype.slice.call(form.querySelectorAll("tbody[data-lines] tr")); }
  function val(tr, n) { return tr.querySelector("[name=" + n + "]").value; }
  function saveBatch(form) {
    // 줄을 새로 찍기 시작하면 보관본이 지금 줄로 바뀐다 → 예전 '되살리기'는 치운다 (누르면 지금 줄이 두 번 들어감)
    const old = form.querySelector(".batch-restore");
    if (old && !rowsOf(form).length) return;            // 빈 화면에서 구분·창고만 바꿨으면 보관본(되살리기)을 지우지 않는다
    if (old) old.remove();
    const lines = rowsOf(form).map(function (tr) {
      const sel = tr.querySelector("[name=line_unit]");
      return { id: val(tr, "line_mid"), label: tr.querySelector("[data-label]").textContent,
               unit: sel.options[0].textContent, entry_unit: sel.value,
               units: Array.prototype.slice.call(sel.options, 1).map(function (o) {
                 return { unit: o.value, factor: parseFloat(o.dataset.factor) || 1 }; }),
               lot_managed: tr.classList.contains("lot-line"),
               qty: val(tr, "line_qty"), lot: val(tr, "line_lot"), exp: val(tr, "line_exp"),
               price: val(tr, "line_price"), note: val(tr, "line_note"), po: val(tr, "line_po") };
    });
    try {
      if (lines.length) localStorage.setItem(BKEY, JSON.stringify({ at: Date.now(), kind: form.querySelector("[name=kind]").value,
                                                                     lines: lines }));
      else localStorage.removeItem(BKEY);
    } catch (e) { /* 저장소가 막혀도 동작 */ }
  }
  function factorOf(tr) {
    const sel = tr.querySelector("[name=line_unit]");
    return sel.selectedIndex > 0 ? parseFloat(sel.options[sel.selectedIndex].dataset.factor) || 1 : 1;
  }
  function paintStock(form) {
    const stock = form._stock || {};
    const out = form.querySelector("[name=kind]").value === "OUT";
    const need = {};                                   // 같은 자재 여러 줄이면 합쳐서 비교
    rowsOf(form).forEach(function (tr) {
      const id = val(tr, "line_mid");
      need[id] = (need[id] || 0) + (parseFloat(val(tr, "line_qty")) || 0) * factorOf(tr);
    });
    rowsOf(form).forEach(function (tr) {
      const cell = tr.querySelector("[data-stock]");
      if (!cell) return;
      const id = val(tr, "line_mid");
      if (!(id in stock)) { cell.textContent = "…"; cell.classList.remove("short"); return; }
      const st = stock[id];
      const unit = tr.querySelector("[name=line_unit]").options[0].textContent;
      const short = out && need[id] > st + 1e-9;
      cell.textContent = (Math.round(st * 10000) / 10000).toLocaleString("ko-KR") + " " + unit + (short ? " ⚠ 부족" : "");
      cell.title = short ? "출고 합계 " + need[id] + " " + unit + " > 현재고" : "이 창고 현재고 (기본 단위)";
      cell.classList.toggle("short", short);
    });
  }
  function loadStock(form, all) {
    const url = form.dataset.stockUrl;
    const wh = form.querySelector("[name=warehouse_id]");
    if (!url || !wh) return;
    if (all || form._stockWh !== wh.value) { form._stock = {}; form._stockWh = wh.value; }
    const ids = rowsOf(form).map(function (tr) { return val(tr, "line_mid"); })
      .filter(function (id, i, a) { return id && a.indexOf(id) === i && !(id in form._stock); });
    if (!ids.length) { paintStock(form); return; }
    fetch(url + "?wh=" + encodeURIComponent(wh.value) + "&ids=" + ids.join(","), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && j.ok && form._stockWh === wh.value) Object.assign(form._stock, j.stock);
        paintStock(form);
      })
      .catch(function () { paintStock(form); });        // 끊김: '…' 그대로, 등록 때 서버가 판정
  }
  // 발주 불러오기 — 창고를 바꾸면 그 창고의 열린 발주 목록을 다시 받는다
  function loadOrders(form) {
    const sel = form.querySelector("[data-po-load]");
    const wh = form.querySelector("[name=warehouse_id]");
    if (!sel || !wh || !form.dataset.poUrl) return;
    fetch(form.dataset.poUrl + "?wh=" + encodeURIComponent(wh.value), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        form._orders = {};
        sel.length = 1;
        ((j && j.orders) || []).forEach(function (o) {
          form._orders[o.po_no] = o;
          const op = document.createElement("option");
          op.value = o.po_no;
          op.textContent = o.po_no + " · " + (o.supplier || "") + (o.delivery ? " · 납기 " + o.delivery : "") + " · " + o.lines.length + "품목";
          sel.appendChild(op);
        });
        sel.options[0].textContent = sel.length > 1 ? "— 이 창고의 입고할 발주 (" + (sel.length - 1) + ") —" : "— 입고할 발주 없음 —";
      })
      .catch(function () { /* 끊김: 목록 없이 */ });
  }
  function refresh(form) {
    const rows = rowsOf(form);
    let total = 0;
    rows.forEach(function (tr, i) {
      tr.querySelector("[data-no]").textContent = i + 1;
      const sel = tr.querySelector("[name=line_unit]");
      const f = sel.selectedIndex > 0 ? parseFloat(sel.options[sel.selectedIndex].dataset.factor) || 1 : 1;
      total += (parseFloat(val(tr, "line_qty")) || 0) * f * (parseFloat(val(tr, "line_price")) || 0);
    });
    const empty = form.querySelector("[data-empty]");
    if (empty) empty.hidden = rows.length > 0;
    const sum = form.querySelector("[data-summary]");
    if (sum) sum.textContent = rows.length + "줄 · 합계 " + money(total);
    loadStock(form, false);
  }
  function lotHint(form, tr) {
    const lot = tr.querySelector("[name=line_lot]");
    const isIn = form.querySelector("[name=kind]").value === "IN";
    lot.required = tr.classList.contains("lot-line") && isIn;
    lot.placeholder = tr.classList.contains("lot-line") ? (isIn ? "로트 (필수)" : "비우면 기한 빠른 로트부터") : "";
  }
  function addLine(form, d) {
    const tpl = form.querySelector("template[data-line-tpl]");
    const tr = tpl.content.firstElementChild.cloneNode(true);
    tr.querySelector("[name=line_mid]").value = d.id;
    tr.querySelector("[data-label]").textContent = d.label;
    const sel = tr.querySelector("[name=line_unit]");
    sel.innerHTML = "";
    const base = document.createElement("option");
    base.value = "";
    base.textContent = d.unit || "";
    sel.appendChild(base);
    (d.units || []).forEach(function (u) {
      const o = document.createElement("option");
      o.value = u.unit;
      o.dataset.factor = u.factor;
      o.textContent = u.unit + "(×" + u.factor + ")";
      sel.appendChild(o);
    });
    sel.value = d.entry_unit || "";
    tr.dataset.factor = sel.selectedIndex > 0 ? parseFloat(sel.options[sel.selectedIndex].dataset.factor) || 1 : 1;
    ["qty", "lot", "exp", "price", "note", "po"].forEach(function (k) {
      if (d[k] !== undefined && d[k] !== null) tr.querySelector("[name=line_" + k + "]").value = d[k];
    });
    const tag = tr.querySelector("[data-po-tag]");
    if (tag) tag.textContent = d.po ? "발주 " + d.po.replace("|", " / ") : "";
    tr.classList.toggle("lot-line", !!d.lot_managed);
    lotHint(form, tr);
    form.querySelector("tbody[data-lines]").appendChild(tr);
    return tr;
  }
  function flash(tr) { tr.classList.remove("flash"); void tr.offsetWidth; tr.classList.add("flash"); }
  document.addEventListener("mm:pick", function (e) {
    const form = batchOf(e.target);
    if (!form) return;
    const it = e.detail;
    // 더할 수량: '24*코드'로 넣은 수량 > 스캔 수량 칸(바꿀 때까지 유지) > 1
    const box = form.querySelector("[data-scan-qty]");
    const add = it.scanQty != null ? it.scanQty : (parseFloat(box && box.value) > 0 ? parseFloat(box.value) : 1);
    const su = (it.units || []).find(function (u) { return u.unit === (it.scanUnit || ""); });
    const scanF = su ? parseFloat(su.factor) || 1 : 1;
    // 발주에서 불러온 같은 자재 줄이 있으면 그 줄에 센다 (단위는 그 줄 단위로 환산).
    // 불러온 잔량은 '예정'이므로 첫 스캔이 그 수량을 대신하고, 이후 스캔은 더한다 → 실제로 센 수량만 입고
    const poRow = rowsOf(form).find(function (tr) {
      return val(tr, "line_mid") === String(it.id) && val(tr, "line_po") && !val(tr, "line_lot");
    });
    const same = poRow || rowsOf(form).find(function (tr) {
      return val(tr, "line_mid") === String(it.id) && val(tr, "line_unit") === (it.scanUnit || "") && !val(tr, "line_lot");
    });
    if (same) {
      const q = same.querySelector("[name=line_qty]");
      const conv = add * scanF / factorOf(same);
      const start = (same === poRow && !same.dataset.scanned) ? 0 : (parseFloat(q.value) || 0);
      same.dataset.scanned = "1";
      q.value = Math.round((start + conv) * 10000) / 10000;
      flash(same);
    } else {
      flash(addLine(form, { id: it.id, label: it.label, unit: it.unit, units: it.units, entry_unit: it.scanUnit,
                            lot_managed: it.lot, qty: add, price: it.price }));
    }
    refresh(form);
    saveBatch(form);
  });
  document.addEventListener("input", function (e) {
    const f = batchOf(e.target);
    if (f && !e.target.classList.contains("picker-q")) { refresh(f); saveBatch(f); }
  });
  // 줄의 칸에서 Enter(스캐너가 잘못된 칸에 찍은 경우 포함)는 제출하지 않고 스캔 칸으로 돌아간다
  document.addEventListener("keydown", function (e) {
    const f = batchOf(e.target);
    if (!f || e.key !== "Enter" || e.target.tagName !== "INPUT" || e.target.classList.contains("picker-q")) return;
    e.preventDefault();
    const q = f.querySelector("[data-picker] .picker-q");
    if (q) q.focus();
  });
  document.addEventListener("click", function (e) {
    const b = e.target.closest && e.target.closest("[data-line-remove]");
    if (b) {
      const form = batchOf(b);
      b.closest("tr").remove();
      refresh(form);
      saveBatch(form);
      return;
    }
    const r = e.target.closest && e.target.closest("[data-batch-restore]");
    if (r) {
      const form = r.closest("form[data-batch]");
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(BKEY) || "null"); } catch (err) { saved = null; }
      if (saved) {
        form.querySelector("[name=kind]").value = saved.kind || "IN";
        form.dataset.kind = form.querySelector("[name=kind]").value;
        saved.lines.forEach(function (d) { addLine(form, d); });
        refresh(form);
      }
      r.closest(".batch-restore").remove();
    }
  });
  document.addEventListener("change", function (e) {
    const form = batchOf(e.target);
    if (!form) return;
    if (e.target.matches("[data-po-load]")) {
      const o = (form._orders || {})[e.target.value];
      e.target.value = "";
      if (!o) return;
      const have = rowsOf(form).map(function (tr) { return val(tr, "line_po"); });
      let added = 0;
      o.lines.forEach(function (l) {
        if (have.indexOf(l.po) >= 0) return;            // 이미 불러온 발주 품목은 다시 넣지 않음
        flash(addLine(form, { id: l.id, label: l.label, unit: l.unit, units: l.units, lot_managed: l.lot_managed,
                              qty: l.qty, price: l.price, po: l.po }));
        added++;
      });
      const partner = form.querySelector("[name=partner]");
      if (partner && !partner.value && o.supplier) partner.value = o.supplier;
      const ref = form.querySelector("[name=ref_no]");
      if (ref && !ref.value) ref.value = o.po_no;
      if (!added) window.alert("이 발주의 품목은 이미 줄에 있습니다.");
      refresh(form);
      saveBatch(form);
      return;
    }
    if (e.target.name === "warehouse_id") { loadStock(form, true); loadOrders(form); return; }
    if (e.target.name === "line_unit") {                  // 단위를 바꾸면 같은 양이 되게 수량을 환산 (상자 2 → 개 24)
      const tr = e.target.closest("tr");
      const q = tr.querySelector("[name=line_qty]");
      const oldF = parseFloat(tr.dataset.factor) || 1, newF = factorOf(tr);
      if (q.value !== "" && oldF !== newF) q.value = Math.round((parseFloat(q.value) || 0) * oldF / newF * 10000) / 10000;
      tr.dataset.factor = newF;
      refresh(form);
      paintStock(form);
      saveBatch(form);
      return;
    }
    if (e.target.name !== "kind") return;
    form.dataset.kind = e.target.value;
    rowsOf(form).forEach(function (tr) { lotHint(form, tr); });
    paintStock(form);
    saveBatch(form);
  });
  // 끊긴 동안 등록 → 오프라인 대기열에 담았으면(app.js) 줄을 비운다 (같은 줄을 두 번 보내지 않게)
  document.addEventListener("mm:queued", function (e) {
    const form = e.target.closest && e.target.closest("form[data-batch]");
    if (!form) return;
    rowsOf(form).forEach(function (tr) { tr.remove(); });
    try { localStorage.removeItem(BKEY); } catch (err) { /* 무시 */ }
    refresh(form);
  });
  function offerRestore(form) {
    if (document.querySelector(".alert-success")) {           // 방금 등록에 성공한 화면 → 보관한 줄은 등록된 것
      try { localStorage.removeItem(BKEY); } catch (e) { /* 무시 */ }
      return;
    }
    if (rowsOf(form).length) { saveBatch(form); return; }      // 서버가 되돌려 준 줄(오류)이 있으면 그걸 보관
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(BKEY) || "null"); } catch (e) { saved = null; }
    if (!saved || !saved.lines || !saved.lines.length) return;
    const box = document.createElement("div");
    box.className = "alert alert-info batch-restore";
    box.textContent = "등록하지 않은 줄 " + saved.lines.length + "개가 이 브라우저에 남아 있습니다 (" +
                      new Date(saved.at).toLocaleString("ko-KR") + "). ";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm";
    btn.setAttribute("data-batch-restore", "");
    btn.textContent = "되살리기";
    box.appendChild(btn);
    form.insertBefore(box, form.firstChild);
  }

  function init(root) {
    root.querySelectorAll("form[data-batch]").forEach(function (f) {
      const k = f.querySelector("[name=kind]");
      if (k) f.dataset.kind = k.value;
      rowsOf(f).forEach(function (tr) { lotHint(f, tr); });
      refresh(f);
      loadOrders(f);
      offerRestore(f);
    });
    root.querySelectorAll("[data-picker]").forEach(function (p) { load(p); });
  }
  init(document);
  // 본문만 바꿔 끼운 화면 전환(app.js)에서도 다시
  window.mmPicker = { refresh: init };
})();
