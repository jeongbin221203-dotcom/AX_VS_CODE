/* 화면 테마: 저장된 선택(다크/라이트)을 첫 화면을 그리기 전에 적용한다. 선택이 없으면 운영체제 설정(prefers-color-scheme)을 따른다.
 * <head> 에서 defer 없이 불러와야 밝은 화면이 번쩍이지 않는다. 전환 버튼은 app.js 가 맡는다. */
(function () {
  try {
    var t = localStorage.getItem('trade-theme');
    if (t === 'dark' || t === 'light') document.documentElement.dataset.theme = t;
  } catch (e) { /* 저장소를 못 쓰면 운영체제 설정을 따름 */ }
})();
