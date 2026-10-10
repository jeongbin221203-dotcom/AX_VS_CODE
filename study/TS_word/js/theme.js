/* 화면 색상: 자동(기기 설정 따름) / 다크 / 라이트. 깜빡임을 막으려고 <head> 에서 먼저 불러온다. */
(function () {
  "use strict";
  var KEY = "ts-theme";
  function get() { try { var v = localStorage.getItem(KEY); return v === "dark" || v === "light" ? v : "auto"; } catch (e) { return "auto"; } }
  function apply(v) {
    var r = document.documentElement;
    if (v === "dark" || v === "light") r.setAttribute("data-theme", v); else r.removeAttribute("data-theme");
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute("content", v === "dark" || (v === "auto" && matchMedia("(prefers-color-scheme: dark)").matches) ? "#15171B" : "#2F5BD3");
  }
  function set(v) { try { if (v === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, v); } catch (e) { /* 저장 못 해도 이번엔 적용 */ } apply(v); }
  apply(get());
  window.Theme = { get: get, set: set, cycle: function () { var n = { auto: "dark", dark: "light", light: "auto" }[get()]; set(n); return n; } };
})();
