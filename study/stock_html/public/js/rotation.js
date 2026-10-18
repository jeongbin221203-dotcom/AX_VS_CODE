// 대형주 순환 매매 화면 — rotation.json(tools/rotation_build.mjs 가 만든 결과)을 그린다.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = (v, d = 1) => (v >= 0 ? '+' : '') + v.toFixed(d) + '%';
const cls = (v) => (v >= 0 ? 'pos' : 'neg');
const won = (v) => Math.round(v).toLocaleString('ko-KR');

function symLink(code, name) {
  return `<a class="sym" href="index.html" data-code="${esc(code)}">${esc(name)}</a> <span class="q-time">${esc(code)}</span>`;
}

async function main() {
  let j;
  try { j = await (await fetch('rotation.json', { cache: 'no-store' })).json(); }
  catch { document.querySelector('main').insertAdjacentHTML('afterbegin', '<p class="note">rotation.json 을 불러오지 못했습니다. <code>node tools/rotation_build.mjs</code> 로 만든 뒤 다시 열어 주세요.</p>'); return; }
  $('asof').textContent = `기준 ${j.asof} 종가 · 시총 순위 ${j.capsDate} · ${j.universe}종목`;
  const st = j.stats;
  const years = Object.entries(j.yearly);
  const worstYear = years.reduce((a, b) => (a[1].ret < b[1].ret ? a : b));
  const plus = years.filter(([, v]) => v.ret >= 20).length;
  $('statNote').textContent = '(2017~ 과거 시뮬레이션)';
  $('kpis').innerHTML = [
    ['연평균 수익', pct(st.cagr), '이 종목군 기준(생존 편향)'],
    ['최대 낙폭', pct(st.mdd), '고점 대비 최대 하락'],
    ['승률', `${st.win.toFixed(0)}%`, `청산 ${st.closed}건 중`],
    ['거래 수', `${st.trades}건`, `평균 보유 ${Math.round(st.avgDays)}일`],
    ['연 +20% 이상인 해', `${plus} / ${years.length}년`, '나머지 해는 그 미만'],
    ['가장 나쁜 해', `${worstYear[0]} ${pct(worstYear[1].ret, 0)}`, '손실 가능성 확인'],
  ].map(([t, v, s]) => `<div class="kpi"><span>${t}</span><b class="${t.includes('낙폭') || (t.includes('나쁜') && worstYear[1].ret < 0) ? 'neg' : ''}">${esc(v)}</b><span>${esc(s)}</span></div>`).join('');

  // 보유
  const hb = $('hold').querySelector('tbody');
  hb.innerHTML = j.holdings.length ? j.holdings.map((h) => `<tr><td>${symLink(h.code, h.name)}</td><td><span class="tag">${esc(h.tag)}</span></td><td>${esc(h.entryDate)}</td><td>${won(h.entryPrice)}</td><td>${won(h.last)}</td><td class="${cls(h.pnl)}">${pct(h.pnl)}</td><td>${h.sellSignal ? `<span class="badge-sell">매도 신호 · ${esc(h.sellSignal)}</span>` : '보유'}</td></tr>`).join('') : '<tr><td colspan="7">보유 종목 없음</td></tr>';
  $('holdNote').textContent = `자리 ${j.slots}개 중 ${j.holdings.length}개 사용, 빈 자리 ${j.freeSlots}개`;

  // 후보
  const cb = $('cand').querySelector('tbody');
  cb.innerHTML = j.candidates.length ? j.candidates.map((c) => `<tr><td>${c.rank}</td><td>${symLink(c.code, c.name)}</td><td><span class="tag">${esc(c.tag)}</span></td><td class="${cls(c.dd)}">${pct(c.dd)}</td><td>${won(c.close)}</td><td>${c.buy ? '<span class="badge-buy">다음 시가 매수</span>' : (j.freeSlots === 0 ? '자리 없음(대기)' : '대기')}</td></tr>`).join('') : '<tr><td colspan="6">오늘은 매수 신호가 난 종목이 없습니다.</td></tr>';
  $('candNote').textContent = `${j.candidates.length}종목`;

  // 해별 막대
  const max = Math.max(...years.map(([, v]) => Math.abs(v.ret)), 1);
  $('years').innerHTML = years.map(([y, v]) => {
    const h = Math.max(2, Math.round(Math.abs(v.ret) / max * 130));
    return `<div class="bar-col"><span class="v ${cls(v.ret)}">${pct(v.ret, 0)}</span><div class="b" style="height:${h}px;background:${v.ret >= 0 ? 'var(--up)' : 'var(--down)'}"></div><span class="y">${y}</span></div>`;
  }).join('');

  // 자산 곡선(로그)
  const eq = j.equity; const W = 1000, H = 220, pad = 16;
  const lv = eq.map((p) => Math.log(p[1])); const lo = Math.min(...lv), hi = Math.max(...lv);
  const pts = eq.map((p, i) => `${(i / (eq.length - 1) * (W - 2 * pad) + pad).toFixed(1)},${(H - pad - (Math.log(p[1]) - lo) / (hi - lo || 1) * (H - 2 * pad)).toFixed(1)}`).join(' ');
  $('eq').innerHTML = `<polyline fill="none" stroke="#4c8dff" stroke-width="2" points="${pts}"/><line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2a313c"/>`;
  $('eqNote').textContent = `${eq[0][0]} → ${eq[eq.length - 1][0]}: 1 → ${eq[eq.length - 1][1].toFixed(2)} (낙폭 ${pct(st.mdd, 0)})`;

  // 최근 거래
  $('recent').querySelector('tbody').innerHTML = j.recent.map((t) => `<tr><td>${symLink(t.code, t.name)}</td><td><span class="tag">${esc(t.tag)}</span></td><td>${esc(t.entryDate)}</td><td>${esc(t.exitDate)}</td><td class="${cls(t.ret)}">${pct(t.ret)}</td><td>${t.days}</td><td style="text-align:left;white-space:normal">${esc(t.why || '')}</td></tr>`).join('');

  // 종목 이름을 누르면 차트로: 앱이 기억하는 종목·일봉을 바꾼 뒤 이동
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a.sym'); if (!a) return;
    try { localStorage.setItem('stock:symbol', a.dataset.code); localStorage.setItem('stock:iv', '1d'); } catch { /* 저장 불가 */ }
  });
}
main();
