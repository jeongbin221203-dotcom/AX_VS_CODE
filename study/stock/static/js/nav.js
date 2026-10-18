// 상단 '더보기' 메뉴: 바깥을 누르거나 Esc 를 누르면 닫고, 항목을 고르면 닫는다.
(function () {
  var menus = Array.prototype.slice.call(document.querySelectorAll('.top details.menu'));
  if (!menus.length) return;
  function closeAll(except) { menus.forEach(function (m) { if (m !== except) m.removeAttribute('open'); }); }
  menus.forEach(function (m) { m.addEventListener('toggle', function () { if (m.open) closeAll(m); }); });
  document.addEventListener('click', function (e) { if (!e.target.closest('.top details.menu')) closeAll(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeAll(); });
})();
