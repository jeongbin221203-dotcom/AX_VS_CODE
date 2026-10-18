// 대형주 순환 매매 화면 — rotation.json(tools/rotation_build.mjs 가 만든 결과)을 그린다.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = (v, d = 1) => (v >= 0 ? '+' : '') + v.toFixed(d) + '%';
const cls = (v) => (v >= 0 ? 'pos' : 'neg');
const won = (v) => Math.round(v).toLocaleString('ko-KR');

function symLink(code, name) {
  return `<a class="sym" href="index.html" data-code="${esc(code)}">${esc(name)}</a> <span class="q-time">${esc(code)}</span>`;
}

const TIER = new URLSearchParams(location.search).get('tier') === 'mid' ? 'mid' : 'large';

async function main() {
  let j;
  for (const b of document.querySelectorAll('#tierTabs button')) {
    b.classList.toggle('on', b.dataset.tier === TIER);
    b.addEventListener('click', () => { location.search = b.dataset.tier === 'mid' ? '?tier=mid' : ''; });
  }
  try { j = await (await fetch(TIER === 'mid' ? 'rotation_mid.json' : 'rotation.json', { cache: 'no-store' })).json(); }
  catch { document.querySelector('main').insertAdjacentHTML('afterbegin', '<p class="note">순환 매매 결과 파일을 불러오지 못했습니다. <code>node tools/rotation_build.mjs</code> 로 만든 뒤 다시 열어 주세요.</p>'); return; }
  const lab = j.label || '대형주', rule = j.timeStop ? `시간 손절: 매수 후 ${j.timeStop}거래일이 지나도 손실이면 정리` : `손절: 종가가 매수가보다 ${j.stop * 100}% 이상 내려가면 다음 거래일 시가에 정리`;
  document.title = `${lab} 순환 매매`;
  $('warn').innerHTML = `<b>과거 데이터 시뮬레이션입니다. 수익을 보장하지 않고, 투자 권유가 아닙니다.</b> 현재 시총 순위로 고른 ${lab}만으로 계산해서 상장폐지·부진 종목이 빠진 <b>생존 편향</b>이 있어 실제보다 높게 나옵니다. ${esc(j.study.note)} 시험 결과 장기 연수익은 <b>${pct(j.study.cagr)}</b>(낙폭 ${pct(j.study.mdd, 0)}, 해별 최저 ${pct(j.study.worstYear, 0)})였습니다.`;
  $('rules').innerHTML = `<li>대상: ${j.tier === 'mid' ? '시가총액 101~300위이면서 5,000억 원 이상' : '시가총액 1~100위'}(코스피·코스닥 보통주), 20일 평균 거래대금 1억 원 이상인 날만 매수.</li>
    <li>한 번에 <b>${j.slots}종목</b>만 보유(자리마다 같은 비중), 빈 자리는 현금.</li>
    <li>매수: 그날 대형주 규칙의 매수 신호(T1 하단 이탈 · T6 과대 낙폭 · T3/T3b 200일선 · 돌파)가 난 종목. T4는 제외. 이미 가진 종목은 사지 않음.</li>
    <li>매도: 그 종목의 규칙 매도 신호(상단 터치 + 음봉 + RSI 과열, 장악형, 거래량 폭발, 5/60 데드크로스 등) 또는 <b>${esc(rule)}</b>${j.stop && j.timeStop ? ` 또는 <b>손절: 종가가 매수가보다 ${j.stop * 100}% 이상 내려가면 정리</b>` : ''}. 손실 종목이 자리를 계속 막는 것을 막는 장치입니다${j.tier === 'mid' ? '(중형주는 −25~−30% 손절은 수익을 깎았고 −40% 손절과 시간 손절은 도움이 됐습니다)' : ''}.</li>
    <li>체결은 신호 다음 거래일 시가, 왕복 비용 0.6%를 반영했습니다. 규칙은 <code>storyrules.js</code>, 계산은 <code>tools/rotation_build.mjs</code>입니다.</li>`;
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
  $('prioNote').textContent = j.tier === 'mid' ? '자리가 비면 위에서부터 삽니다(변동폭이 큰 종목 먼저 — 중형주는 가벼워서 더 크게 움직입니다).' : '자리가 비면 위에서부터 삽니다(T1 → T6 → T3b → T3 → 돌파 순, 같은 종류는 낙폭이 깊은 종목 먼저).';
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
