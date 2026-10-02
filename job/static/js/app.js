// 잡핏 공통 스크립트 (CSP: 인라인 스크립트 금지라 여기서 이벤트를 붙인다)
(function () {
  // data-autosubmit 폼: 선택·체크를 바꾸면 바로 적용
  document.querySelectorAll("form[data-autosubmit]").forEach(function (form) {
    form.addEventListener("change", function (e) {
      var t = e.target;
      if (t.tagName === "SELECT" || t.type === "checkbox") form.requestSubmit ? form.requestSubmit() : form.submit();
    });
  });
  // data-confirm 버튼: 확인 후 제출
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-confirm]");
    if (el && !window.confirm(el.getAttribute("data-confirm"))) e.preventDefault();
  });
})();
