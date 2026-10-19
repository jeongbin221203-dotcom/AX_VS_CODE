(function () {
  var BOOT = JSON.parse(document.getElementById('boot').textContent);
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  var UP = dark ? '#ee6a60' : '#c5392f', DOWN = dark ? '#6f9bf0' : '#2f62c2';
  var FG = dark ? '#c3c8d4' : '#3b4150', GRID = dark ? '#222833' : '#f0ede6', BG = dark ? '#151a22' : '#ffffff';
  var LC = LightweightCharts;
  var SETS = {
    a: { periods: [5, 14, 56, 112, 224], colors: { 5: '#e0a000', 14: '#d6479b', 56: '#2a9d5c', 112: '#0f9d9d', 224: '#555555' } },
    b: { periods: [14, 28, 56, 112, 224, 448], colors: { 14: '#e0a000', 28: '#d6479b', 56: '#2a9d5c', 112: '#0f9d9d', 224: '#555555', 448: '#2b62d9' } },
    none: { periods: [], colors: {} }
  };
  var bars = 250, syncing = false, crossing = false;
  var loadBars = 750;                 // 서버에서 받는 봉 수: 처음엔 약 3년(차트 데이터 4.7MB → 약 0.7MB), '전체'를 누르면 5000
  var MOBILE = window.matchMedia && window.matchMedia('(max-width: 760px)').matches;
  var tip = document.getElementById('tip');

  var INTRA = { '1m': 1, '5m': 1, '15m': 1, '30m': 1, '60m': 1 };
  function isIntra(tf) { return !!INTRA[tf]; }
  function fmtTime(t) { return typeof t === 'number' ? new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ') : t; }
  function msg(t) { var m = document.getElementById('cmsg'); m.hidden = !t; m.textContent = t || ''; }
  function fmt(n) { return Math.round(n).toLocaleString('ko-KR'); }
  // 동기화는 같은 종류끼리만 — 분봉의 '오늘 하루'를 일봉에 맞추면 일봉이 2개만 찌그러져 보인다
  function compat() {
    if (typeof panes === 'undefined' || !panes || !panes.a || !panes.b) return true;
    var ta = panes.a.settings().tf, tb = panes.b.settings().tf;
    return isIntra(ta) || isIntra(tb) ? ta === tb : true;   // 분봉은 같은 간격끼리만, 일·주·월봉은 서로 맞춘다
  }
  function syncOn() { return document.getElementById('sync').checked && compat(); }

  function Pane(side) {
    var self = this;
    this.side = side;
    this.root = document.querySelector('.cpane[data-side="' + side + '"]');
    this.el = this.root.querySelector('.cchart');
    this.code = BOOT[side].code;
    this.chart = LC.createChart(this.el, {
      height: MOBILE ? 420 : 520, width: this.el.clientWidth,
      layout: { background: { color: BG }, textColor: FG }, grid: { vertLines: { visible: false }, horzLines: { color: GRID } },
      rightPriceScale: { borderColor: GRID, minimumWidth: 78 }, timeScale: { borderColor: GRID, rightOffset: 4 },
      crosshair: { mode: LC.CrosshairMode.Normal }
    });
    this.candle = this.chart.addCandlestickSeries({ upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN });
    this.vol = this.chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
    this.chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    this.lines = [];
    this.bbLines = [];       // 볼린저밴드(위·가운데·아래)
    this.data = null;
    this.pred = null;        // 겹쳐 그린 '예측'(단테 기법) 또는 'AI' 응답
    this.predSeries = [];    // AI 확률 선
    this.probMap = {};       // 날짜 → AI 확률(툴팁용)
    this.predSeq = 0;
    this.riskSeries = [];    // 하락 위험(배경 띠 + 확률 선)
    this.riskSeq = 0;
    this.riskMap = {};       // 날짜 → 하락 위험 확률(툴팁용)
    this.riskCuts = null;    // {hi, mid} 단계 경계

    // 확대·이동 동기화 (시간 범위 기준 — 종목·봉 단위가 달라도 같은 기간을 보여 줌)
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(function () {
      if (syncing || !syncOn()) return;
      var r = self.chart.timeScale().getVisibleRange();
      if (!r) return;
      syncing = true;
      var o = other(self); if (o) try { o.chart.timeScale().setVisibleRange(r); } catch (e) { /* 상대 차트에 그 기간 데이터가 없음 */ }
      syncing = false;
    });
    // 십자선 동기화
    this.chart.subscribeCrosshairMove(function (p) {
      var o = other(self);
      if (crossing) return;
      if (o && syncOn() && o.data) {
        crossing = true;
        try {
          if (!p.time) o.chart.clearCrosshairPosition();
          else {
            var c = p.seriesData.get(self.candle);
            if (c) o.chart.setCrosshairPosition(c.close, p.time, o.candle);
          }
        } catch (e) { /* 상대 차트에 그 날짜 봉이 없음 */ }
        crossing = false;
      }
      if (p.time && p.point && p.seriesData.get(self.candle)) showTip(self, p);
      else tip.style.display = 'none';
    });
    this.bind();
  }

  // 오늘 할 일(종합 신호) — 서버(core/jb_rules.action)가 계산한 값. 예전 응답(스냅샷 등)에 없으면 AI 점수만
  function todayAction(j) {
    var a = j.action;
    if (a) return { text: a.text, title: a.title || '', cls: a.kind };
    var sc = j.latest && j.latest.prob != null ? 'AI ' + (j.latest.prob * 100).toFixed(0) + '점' : 'AI 점수 없음';
    return { text: ((j.rule && j.rule.group) ? j.rule.group + ' ' : '') + sc, title: '', cls: 'wait' };
  }
  // 위험 단계 이름: 높음(시험 기간 상위 10%) · 주의(10~25%) · 낮음
  function riskLevel(p, cuts) { return !cuts ? '' : p >= cuts.hi ? '높음' : p >= cuts.mid ? '주의' : '낮음'; }
  function other(p) { return p.side === 'a' ? panes.b : panes.a; }   // 한 차트(JB차트 기본)면 null
  function all() { return [panes.a, panes.b].filter(Boolean); }

  function showTip(pane, p) {
    var c = p.seriesData.get(pane.candle), v = p.seriesData.get(pane.vol);
    var pr = pane.probMap[p.time], rk = pane.riskMap[p.time];
    tip.textContent = fmtTime(p.time) + '\n시 ' + fmt(c.open) + '  고 ' + fmt(c.high) + '\n저 ' + fmt(c.low) + '  종 ' + fmt(c.close) +
      ' (' + ((c.close / c.open - 1) * 100).toFixed(2) + '%)' + (v ? '\n거래량 ' + fmt(v.value) : '') +
      (pr !== undefined ? '\n' + (pane.aiLabel === 'AI 점수' ? 'AI 점수 ' + (pr * 100).toFixed(0) + '점' : 'AI 확률 ' + (pr * 100).toFixed(1) + '%') : '') + (rk !== undefined ? '\n하락 위험 ' + riskLevel(rk, pane.riskCuts) + ' ' + (rk * 100).toFixed(0) + '% (20일 안 -10%↓)' : '');
    if (pane.riskEv && pane.riskEv[p.time]) tip.textContent += '\n' + pane.riskEv[p.time];
    var sg = pane.sigMap && pane.sigMap[p.time];
    if (sg) tip.textContent += '\n' + sg.slice(0, 6).map(function (m) {   // 그 봉의 매수·매도 신호와 이유
      return (m.side === 'sell' ? '▼ ' : (m.side === 'low' || m.side === 'high') ? '● ' : '▲ ') + m.label + (m.strong === true ? ' (강)' : m.strong === false ? ' (약)' : '') + (m.why ? ' — ' + String(m.why).slice(0, 140) : '');
    }).join('\n');
    tip.style.display = 'block';
    var r = pane.el.getBoundingClientRect();
    tip.style.left = Math.min(p.point.x + r.left + window.scrollX + 14, window.scrollX + document.documentElement.clientWidth - Math.min(tip.offsetWidth || 210, 420) - 16) + 'px';
    tip.style.top = (r.top + window.scrollY + 8) + 'px';
  }

  Pane.prototype.settings = function () {
    return { set: this.root.querySelector('.cset').value, tf: this.root.querySelector('.ctf').value, kind: this.root.querySelector('.ckind').value };
  };

  // ---- 겹쳐 그리기: 예측(단테 기법 신호 마커) / AI(확률 선 + 상위 10% 마커) ----
  Pane.prototype.clearPred = function () {
    var self = this;
    this.predSeries.forEach(function (s) { self.chart.removeSeries(s); });
    var ab = this.root.querySelector('.rb-ai'); if (ab) ab.hidden = true;
    if (this.legendAi) this.legendAi.hidden = true;
    this.chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.2, bottom: 0.1 } });
    this.predSeries = []; this.probMap = {}; this.pred = null; this.pred2 = null; this.sigMap = null;
    this.predMk = [];
    this.applyMarkers();
    this.el.classList.remove('hasleft');
    if (this.chart.options().leftPriceScale.visible) this.keepRange(function () { self.chart.applyOptions({ leftPriceScale: { visible: false } }); });
    this.root.querySelector('.cpred').textContent = '';
  };

  // 응답은 그대로 두고 그려 둔 선(AI 점수)만 지운다 — '근거 보기'를 바꿀 때 다시 받지 않고 다시 그리기
  Pane.prototype.clearPredSeries = function () {
    var self = this;
    this.predSeries.forEach(function (s) { self.chart.removeSeries(s); });
    this.predSeries = []; this.probMap = {};
    if (this.legendAi) this.legendAi.hidden = true;
    this.chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.2, bottom: 0.1 } });
  };

  // 골든·데드크로스: 보이는 봉의 종가로 직접 계산(일·주·월·분봉 모두) — 14/56
  var STRONG_STACK = 3;      // 같은 날 강 신호가 이만큼 겹치면 '강력 신호' 아이콘
  function sma(v, n) {
    var out = [], sum = 0;
    for (var i = 0; i < v.length; i++) { sum += v[i]; if (i >= n) sum -= v[i - n]; out.push(i >= n - 1 ? sum / n : null); }
    return out;
  }
  function crossPred(candles, setKey) {
    var c = candles.map(function (x) { return x.close; }), ms = [];
    [[14, 56]].forEach(function (pr) {
      var a = sma(c, pr[0]), b = sma(c, pr[1]), tag = '(' + pr[0] + '/' + pr[1] + ')';
      for (var i = 1; i < c.length; i++) {
        if (a[i] == null || b[i] == null || a[i - 1] == null || b[i - 1] == null) continue;
        if (a[i - 1] <= b[i - 1] && a[i] > b[i]) ms.push({ date: candles[i].time, side: 'buy', label: '골든' + tag, i: i });
        else if (a[i - 1] >= b[i - 1] && a[i] < b[i]) ms.push({ date: candles[i].time, side: 'sell', label: '데드' + tag, i: i });
      }
    });
    ms.sort(function (p, q) { return p.i - q.i; });
    var rec = ms.slice(-1)[0];
    return { name: '골든·데드크로스', kind: 'cross', markers: ms,
      summary: '14/56 이동평균 교차 — 빨강 ▲ 골든(상향), 파랑 ▼ 데드(하향). ' + (rec ? '가장 최근: ' + rec.label + ' (' + fmtTime(rec.date) + ')' : '표시할 교차가 없습니다.'),
      note: '이동평균 교차는 이미 오른·내린 뒤에 나오는 후행 신호라 횡보장에서는 가짜 신호가 잦습니다(성과 검증 전).' };
  }

  Pane.prototype.loadPred = function () {
    var self = this, s = this.settings(), seq = ++this.predSeq;
    this.clearPred();
    if (s.kind === 'none' || !this.data) return Promise.resolve();
    if (s.kind === 'cross') { self.pred = crossPred(this.data.candles, s.set); self.drawPred(); return Promise.resolve(); }
    if (s.kind === 'ai' && isIntra(s.tf)) { this.root.querySelector('.cpred').textContent = 'AI 점수는 일봉·주봉·월봉에서만 볼 수 있습니다.'; return Promise.resolve(); }
    if (s.kind === 'both' && isIntra(s.tf)) s = Object.assign({}, s, { kind: 'dante' });   // 분봉 '자동' = 예측(단테 기법)만(AI 는 일봉 이상)
    if (s.kind === 'both') {
      // '자동'(일·주·월봉) = 종합 신호 하나 — 시총 구간 규칙(AI 점수·볼린저·시장 조건이 모두 들어 있음)의 매수·2차 매수·매도만 그린다.
      // 예전에 따로 그리던 단테 동그라미·하락 위험 띠는 2026-10-19 사용자 지시("하나의 신호로")로 뺐다(시험에서 수익을 올린 조건만 규칙 안에).
      return fetch('/api/predict/' + encodeURIComponent(self.code) + '?kind=ai&tf=' + s.tf + '&bars=' + loadBars + '&v=2').then(function (r) {
        return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || '오류'); return j; });
      }).then(function (j) {
        if (seq !== self.predSeq || self.settings().kind !== 'both') return;
        self.pred = j;
        var g = j.rule && j.rule.group, opt = self.root.querySelector('.ckind option[value="both"]');
        if (opt) opt.textContent = (g ? g + ' ' : '') + '자동';
        self.drawPred();
      }).catch(function (e) { if (seq === self.predSeq) self.root.querySelector('.cpred').textContent = '종합 신호: ' + e.message; });
    }
    return fetch('/api/predict/' + encodeURIComponent(this.code) + '?kind=' + s.kind + '&tf=' + s.tf + '&bars=' + loadBars + (s.kind === 'ai' && /^\/jb\/?$/.test(location.pathname) ? '&v=2' : '')).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || '오류'); return j; });
    }).then(function (j) {
      if (seq !== self.predSeq || (self.settings().kind !== s.kind && !(self.settings().kind === 'both' && s.kind === 'dante'))) return;   // 그사이 설정이 바뀜
      self.pred = j;
      self.drawPred();
    }).catch(function (e) {
      if (seq === self.predSeq) self.root.querySelector('.cpred').textContent = (s.kind === 'ai' ? 'AI' : '예측') + ': ' + e.message;
    });
  };

  // 왼쪽 눈금(AI %)이 생기거나 없어지면 차트 폭이 바뀌어 보이는 범위가 밀린다 → 바꾸기 전 범위를 되돌린다
  Pane.prototype.keepRange = function (fn) {
    var self = this, keep = this.chart.timeScale().getVisibleRange();
    fn();
    function restore() { if (keep) { syncing = true; try { self.chart.timeScale().setVisibleRange(keep); } catch (e) { /* */ } syncing = false; } }
    restore(); setTimeout(restore, 60);
  };

  Pane.prototype.drawPred = function () {
    var self = this, j = this.pred, box = this.root.querySelector('.cpred');
    if (!j) return;
    var have = {};
    this.data.candles.forEach(function (c) { have[c.time] = true; });
    var ms = j.markers.filter(function (m) { return have[m.date]; });
    var verify = !!(this.root.querySelector('.cver') && this.root.querySelector('.cver').checked);   // '근거 보기': AI 점수 선 + 실제 저점·고점 점
    if (j.kind === 'ai' && !verify) ms = ms.filter(function (m) { return m.side !== 'low' && m.side !== 'high'; });
    var lastTrade = {};                                  // 글자는 가장 최근 매수·매도에만
    ms.forEach(function (m, i) { if (m.side === 'buy' || m.side === 'sell') lastTrade[m.side] = i; });
    // 저점(바닥) 신호는 하락 위험이 낮을 때만 '확실' — 기본은 확실한 것만 그리고, '저점 신호 전체 보기'를 켜면 모두(데이터는 그대로)
    var showAll = !!(document.getElementById('showall') && document.getElementById('showall').checked);
    var nBottom = 0, nCertain = 0;
    ms.forEach(function (m) { if (m.bottom) { nBottom++; if (m.certain) nCertain++; } });
    if (j.kind === 'dante' && !showAll) ms = ms.filter(function (m) { return !m.bottom || m.certain; });
    // 강력 신호: 같은 날 '강' 신호가 3개 이상 겹치면 화살표 여러 개 대신 금색 네모 하나(★ 개수)로 — 통계상 겹칠수록 좋아지진 않았으니 '눈에 띄게'만 한다
    var strongBy = {};
    if (j.kind === 'dante') ms.forEach(function (m) { if (m.strong && !(m.bottom && m.certain)) (strongBy[m.date] = strongBy[m.date] || []).push(m.label); });
    var big = {};
    Object.keys(strongBy).forEach(function (d) { if (strongBy[d].length >= STRONG_STACK) big[d] = strongBy[d]; });
    var mk = Object.keys(big).map(function (d) {
      return { time: d, position: 'belowBar', color: '#f5a300', shape: 'square', size: 2, text: '★' + big[d].length };
    });
    mk = mk.concat(ms.filter(function (m) { return !(j.kind === 'dante' && m.strong && !(m.bottom && m.certain) && big[m.date]); }).map(function (m, i, arr) {
      if (j.kind === 'cross') return { time: m.date, position: m.side === 'sell' ? 'aboveBar' : 'belowBar', color: m.side === 'sell' ? DOWN : UP, shape: m.side === 'sell' ? 'arrowDown' : 'arrowUp', text: i >= arr.length - 2 ? m.label : '' };
      if (m.side === 'low' || m.side === 'high')                        // 그 무렵 실제 저점(초록 점)·고점(주황 점) — 매수·매도가 얼마나 가까웠는지 직접 확인용
        return { time: m.date, position: m.side === 'low' ? 'belowBar' : 'aboveBar', color: m.side === 'low' ? 'rgba(30,132,73,0.75)' : 'rgba(224,138,0,0.8)', shape: 'circle', size: 0.6, text: '' };
      if (j.kind === 'ai' && j.label === 'AI 점수') {                   // 매수(빨강 ▲)·매도(파랑 ▼, 글자=그 거래 수익률). 약한 신호(90점)는 작고 옅게
        var wk = m.tier === '약';
        return { time: m.date, position: m.side === 'sell' ? 'aboveBar' : 'belowBar', color: m.side === 'sell' ? (wk ? 'rgba(43,98,217,0.6)' : DOWN) : (wk ? 'rgba(214,54,47,0.55)' : UP),
                 shape: m.side === 'sell' ? 'arrowDown' : 'arrowUp', size: wk ? 1 : 1.6, text: lastTrade[m.side] === ms.indexOf(m) ? m.label : '' };   // 글자는 마지막 매수·매도만(나머지는 툴팁)
      }
      return { time: m.date, position: 'belowBar', color: j.kind === 'ai' ? (j.label === 'AI 점수' ? '#d81b60' : '#8a5cd6') : (m.bottom && m.certain ? '#1e8449' : m.strong ? UP : '#8f98ab'),
               shape: j.kind === 'ai' ? 'circle' : 'arrowUp', size: (m.bottom && m.certain) ? 2 : 1, text: i >= arr.length - (j.kind === 'ai' ? 1 : 3) ? m.label : '' };   // 글자는 최근 3개만
    }));
    var sm = {}; ms.forEach(function (m) { (sm[m.date] = sm[m.date] || []).push(m); });
    // '예측 + AI 점수': 단테 신호는 봉 아래 작은 동그라미(초록 = 확실한 저점, 주황 = 강, 회색 = 약은 '저점 신호 전체 보기'에서만) — AI 화살표와 섞이지 않게
    var j2 = this.pred2, ms2 = [];
    if (j2 && j2.markers) {
      ms2 = j2.markers.filter(function (m) { return have[m.date] && (showAll || (m.bottom ? m.certain : m.strong)); });
      ms2.forEach(function (m) { (sm[m.date] = sm[m.date] || []).push(Object.assign({}, m, { label: '예측 · ' + m.label })); });
      mk = mk.concat(ms2.map(function (m) {
        return { time: m.date, position: 'belowBar', color: m.bottom && m.certain ? '#1e8449' : m.strong ? '#e08a00' : '#8f98ab', shape: 'circle', size: m.bottom && m.certain ? 1 : 0.7, text: '' };
      }));
    }
    this.sigMap = sm;
    mk = mk.concat(this.focusMarkers());
    mk.sort(function (p, q) { return p.time < q.time ? -1 : p.time > q.time ? 1 : 0; });
    this.predMk = mk;
    this.applyMarkers();
    if (j.kind === 'ai') {
      // AI 확률(%)·점수(점)는 왼쪽 눈금에 — 가격(오른쪽)과 섞이지 않게
      this.aiLabel = j.label || 'AI 확률';
      var isScore = this.aiLabel === 'AI 점수';
      var ab = this.root.querySelector('.rb-ai');
      if (ab && isScore) {                               // 이름 옆 배지 하나 = 오늘 할 일(종합 신호)
        var act = todayAction(j, this.data.candles);
        ab.textContent = act.text; ab.title = act.title; ab.className = 'rbadge rb-ai rb-' + act.cls; ab.hidden = false;
      } else if (ab && j.group) { ab.textContent = j.group.name + ' 후보 ' + j.group.rank + '위/' + j.group.n; ab.title = 'AI 후보 순위(' + j.group.date + ' 기준, 그룹 안)'; ab.hidden = false; }
      var line;
      if (isScore && !verify) {
        j.prob.forEach(function (p) { self.probMap[p.time] = p.value; });   // 선은 안 그려도 툴팁에는 AI 점수
      } else if (isScore) {
        // AI 점수(0~100): 차트 위쪽의 빈 띠(가격 위)에 따로 그린다 — 왼쪽 눈금을 쓰지 않아 가격·이평선과 겹치지 않고 눈금이 0~100 으로 고정된다.
        // 굵은 선 = 5봉 평균(잡음 줄임), 90점(상위 10%) 이상은 진한 분홍, 옅은 선 = 그날 값
        var raw = j.prob.map(function (p) { return p.value * 100; }), sm = raw.map(function (v, i) {
          var from = Math.max(0, i - 4), sum = 0; for (var k = from; k <= i; k++) sum += raw[k]; return sum / (i - from + 1);
        });
        var fixed = function () { return { priceRange: { minValue: 0, maxValue: 100 } }; };
        var pfmt = { type: 'custom', minMove: 1, formatter: function (v) { return v.toFixed(0) + '점'; } };   // (전역 fmt 함수를 가리지 않게 다른 이름)
        var base = { priceScaleId: 'aiscore', priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: fixed, priceFormat: pfmt };
        this.chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.27, bottom: 0.1 } });   // 캔들이 AI 띠 아래에 오도록
        line = this.chart.addLineSeries(Object.assign({ color: '#7b4fd0', lineWidth: 2, crosshairMarkerVisible: false }, base));
        line.setData(j.prob.map(function (p, i) { return { time: p.time, value: sm[i], color: sm[i] >= j.thr * 100 ? '#d81b60' : '#7b4fd0' }; }));
        line.createPriceLine({ price: j.thr * 100, color: '#d81b60', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' });
        if (j.thr_weak) line.createPriceLine({ price: j.thr_weak * 100, color: 'rgba(216,27,96,0.4)', lineWidth: 1, lineStyle: 3, axisLabelVisible: false, title: '' });
        line.createPriceLine({ price: 50, color: 'rgba(143,152,171,0.7)', lineWidth: 1, lineStyle: 3, axisLabelVisible: false, title: '' });
        this.chart.priceScale('aiscore').applyOptions({ scaleMargins: { top: 0.02, bottom: 0.80 }, visible: false });
        if (!this.legendAi) { this.legendAi = document.createElement('div'); this.legendAi.className = 'cleg cleg3'; this.el.appendChild(this.legendAi); }
        this.legendAi.textContent = 'AI 점수 ' + (j.latest && j.latest.prob != null ? (j.latest.prob * 100).toFixed(0) + '점' : '-') + ' · 분홍 점선 95점 = 강한 매수, 옅은 점선 90점';
        this.legendAi.hidden = false;
      } else {
        line = this.chart.addLineSeries({ color: '#8a5cd6', lineWidth: 1, priceScaleId: 'left', priceLineVisible: false, lastValueVisible: true,
                                          title: 'AI', priceFormat: { type: 'custom', minMove: 0.1, formatter: function (v) { return v.toFixed(1) + '%'; } } });
        line.setData(j.prob.map(function (p) { return { time: p.time, value: p.value * 100 }; }));
        line.createPriceLine({ price: j.thr * 100, color: '#8a5cd6', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '상위 10%' });
        this.el.classList.add('hasleft');   // 왼쪽 눈금이 생기면 범례를 그 오른쪽으로
        this.keepRange(function () { self.chart.applyOptions({ leftPriceScale: { visible: true, borderColor: GRID, scaleMargins: { top: 0.55, bottom: 0.17 } } }); });
      }
      if (line) this.predSeries.push(line);
      j.prob.forEach(function (p) { self.probMap[p.time] = p.value; });
    }
    box.textContent = '';
    var b = document.createElement('b'); b.textContent = j.name + ' · '; box.appendChild(b);
    box.appendChild(document.createTextNode(j.summary + (j.note ? '\n' + j.note : '')));
    if (j2) {
      var b2 = document.createElement('b'); b2.textContent = '\n' + j2.name + ' · '; box.appendChild(b2);
      box.appendChild(document.createTextNode(j2.summary + ' (차트의 작은 동그라미: 초록 = 확실한 저점 ' + ms2.filter(function (m) { return m.bottom && m.certain; }).length + '개 · 주황 = 강 ' + ms2.filter(function (m) { return !m.bottom && m.strong; }).length + '개)'));
    }
    if (j.kind === 'ai' && j.trades && j.trades.length) {
      var tr = j.trades.slice(-5).reverse().map(function (t) { return t.buy.slice(2) + ' 매수 ' + fmt(t.buy_px) + ' → ' + t.sell.slice(2) + ' 매도 ' + fmt(t.sell_px) + ' (' + (t.ret >= 0 ? '+' : '') + (t.ret * 100).toFixed(1) + '%)' + (t.seen ? '·학습구간' : ''); });
      box.appendChild(document.createTextNode('\n최근 거래: ' + tr.join(' / ')));
    }
    if (j.kind === 'dante' && nBottom) {
      box.appendChild(document.createTextNode('\n저점(바닥) 신호 ' + nBottom + '개 중 하락 위험이 낮은 "확실" ' + nCertain + '개(초록 ▲)' + (showAll ? ' + 나머지 ' + (nBottom - nCertain) + '개(전체 보기 켜짐)' : '만 표시 — 나머지 ' + (nBottom - nCertain) + '개는 "저점 신호 전체 보기"를 켜면 보임') + '. 확실 기준: 신호일 하락 위험 확률 18% 미만(시험 기간 −10%↓ 26%→13%). ※ 저점 신호는 진짜 저점보다 앞서 나오는 편이라(신호 뒤 20일 안에 평균 −7% 더 내려감) 나눠서 진입하는 것을 권합니다.'));
    }
    if (j.kind === 'dante' && ms.length) {
      var last = ms.slice(-5).reverse().map(function (m) { return (m.shown || m.date) + ' ' + m.label + (m.good ? ' ✔' : '') + (m.strong ? ' 강' : ' 약') + (m.stat ? ' — ' + m.stat : ''); });
      box.appendChild(document.createTextNode('\n최근 신호: ' + last.join(' / ')));
    }
  };

  // 분봉은 1분마다 새로 받는다(탭이 보일 때만)
  Pane.prototype.setupRefresh = function (tf) {
    var self = this;
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    if (!isIntra(tf)) return;
    this.timer = setInterval(function () {
      if (document.hidden || self.settings().tf !== tf) return;
      var keep = self.chart.timeScale().getVisibleLogicalRange();
      self.load().then(function () { if (keep) { syncing = true; try { self.chart.timeScale().setVisibleLogicalRange(keep); } catch (e) { /* */ } syncing = false; } });
    }, 60000);
  };

  Pane.prototype.showPrice = function () {
    var d = this.data, pr = this.root.querySelector('.cprice');
    pr.textContent = fmt(d.last.close) + '  ' + (d.last.change >= 0 ? '+' : '') + d.last.change.toFixed(2) + '%';
    pr.className = 'cprice ' + (d.last.change > 0 ? 'up' : d.last.change < 0 ? 'down' : '');
  };
  // 실시간: 장중 체결로 마지막 봉을 고친다(static/js/live.js). 같은 종목을 두 차트에 띄우면 연결 하나를 나눠 쓴다.
  Pane.prototype.liveShow = function (b) {
    if (!b) return;
    this.candle.update({ time: b.bar.time, open: b.bar.open, high: b.bar.high, low: b.bar.low, close: b.bar.close });   // 복사본 — 라이브러리가 넘긴 객체의 time 을 바꿔 놓아 다음 체결 비교가 깨지던 것 방지
    this.vol.update({ time: b.vol.time, value: b.vol.value, color: (b.vol.up ? UP : DOWN) + '99' });
    this.showPrice();
  };
  Pane.prototype.setupLive = function (d) {
    var self = this;
    if (!window.StockLive || BOOT.live === false) return;      // 휴대폰 스냅샷(Render)에는 실시간이 없음
    if (this.liveCode !== this.code) {
      if (this.unwatch) this.unwatch();
      var el = this.root.querySelector('.live');
      if (!el) { el = document.createElement('span'); el.className = 'live'; this.root.querySelector('.cprice').insertAdjacentElement('afterend', el); }
      var st = StockLive.status(el);
      this.liveCode = this.code;
      this.unwatch = StockLive.watch(this.code, function (m) {
        st(m);
        if (m.type === 'trade' && self.data && self.liveCode === self.code) self.liveShow(StockLive.applyTrade(self.data, self.data.tf, m));
      });
    }
    StockLive.today(this.code).then(function (r) { if (self.data === d) self.liveShow(StockLive.applyToday(d, d.tf, r.bar)); });
  };

  Pane.prototype.load = function () {
    var self = this, s = this.settings();
    return fetch('/api/chart/' + encodeURIComponent(this.code) + '?tf=' + s.tf + '&bars=' + loadBars).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || '오류'); return j; });
    }).then(function (d) {
      self.data = d;
      self.chart.applyOptions({ timeScale: { timeVisible: isIntra(s.tf), secondsVisible: false } });
      self.setupRefresh(s.tf);
      var big = d.last.close >= 1000;
      self.candle.applyOptions({ priceFormat: { type: 'price', precision: big ? 0 : 2, minMove: big ? 1 : 0.01 } });
      self.candle.setData(d.candles);
      self.vol.setData(d.volume.map(function (v) { return { time: v.time, value: v.value, color: (v.up ? UP : DOWN) + '99' }; }));
      self.drawMA();
      self.drawBB();
      self.showPrice();
      self.setupLive(d);
      msg('');
      return Promise.all([self.loadPred(), self.loadRisk()]);
    }).catch(function (e) { msg(self.code + ': ' + e.message); });
  };

  // 차트 표식은 한 곳에서 합친다: 예측·AI 마커 + 하락 위험(위험↑·과열) 마커 + 기준일
  Pane.prototype.applyMarkers = function () {
    var mk = (this.predMk || []).concat(this.riskMk || [], this.focusMarkers());
    mk.sort(function (p, q) { return p.time < q.time ? -1 : p.time > q.time ? 1 : 0; });
    this.candle.setMarkers(mk);
  };

  // ---- 하락 위험: 위험 높음·주의 구간 배경 띠 + '20일 안 −10%↓ 확률' 선 ----
  Pane.prototype.clearRisk = function () {
    var self = this;
    this.riskSeries.forEach(function (s) { self.chart.removeSeries(s); });
    this.riskSeries = []; this.riskMap = {}; this.riskMk = []; this.riskCuts = null; this.riskEv = null;
    var bd = this.root.querySelector('.rbadge'); if (bd) bd.hidden = true;
    this.applyMarkers();
    if (this.legendRisk) this.legendRisk.hidden = true;
    this.root.querySelector('.crisk').textContent = '';
  };

  Pane.prototype.loadRisk = function () {
    var self = this, s = this.settings(), seq = ++this.riskSeq, note = this.root.querySelector('.crisk');
    this.clearRisk();
    if (!this.root.querySelector('.crk').checked || !this.data) return Promise.resolve();
    if (isIntra(s.tf)) { note.textContent = '하락 위험: 일봉·주봉·월봉에서만 볼 수 있습니다.'; return Promise.resolve(); }
    return fetch('/api/risk/' + encodeURIComponent(this.code) + '?tf=' + s.tf + '&bars=' + loadBars).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || '오류'); return j; });
    }).then(function (j) {
      if (seq !== self.riskSeq || !self.root.querySelector('.crk').checked) return;
      var have = {};
      self.data.candles.forEach(function (c) { have[c.time] = true; });
      var fixed = function () { return { priceRange: { minValue: 0, maxValue: 1 } }; };
      // 배경 띠는 면(area) 두 개 — 막대로 그리면 봉 사이 틈이 줄무늬로 보인다. 위험 높음(빨강)·주의(주황), 띠가 없는 봉은 0 으로 내린다
      var bandScale = /^\/jb\/?$/.test(location.pathname) ? 'riskstrip' : 'riskband';
      var mkArea = function (rgb) { return self.chart.addAreaSeries({ priceScaleId: bandScale, lineVisible: false, topColor: rgb, bottomColor: rgb, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: fixed }); };
      var noBand = /^\/jb\/?$/.test(location.pathname);   // JB차트는 배경 대신 차트 맨 아래에 얇은 위험 띠(높음 빨강·주의 주황)
      var bandHi = mkArea(noBand ? 'rgba(214,54,47,0.95)' : 'rgba(214,54,47,0.20)'), bandMid = mkArea(noBand ? 'rgba(224,160,0,0.85)' : 'rgba(224,160,0,0.14)');
      self.chart.priceScale(bandScale).applyOptions({ scaleMargins: noBand ? { top: 0.975, bottom: 0 } : { top: 0, bottom: 0 }, visible: false });
      // 띄엄띄엄 켜진 날을 이어 붙여(사이 3봉 이하) 줄무늬 대신 연속된 띠로 보이게
      var lvl = {}, cs = self.data.candles, hi = [], mid = [], i, k;
      j.zones.forEach(function (z) { lvl[z.time] = z.level; });
      for (i = 0; i < cs.length; i++) {
        var L = lvl[cs[i].time] || 0;
        if (!L) {
          var prev = 0, next = 0;
          for (k = 1; k <= 3 && !prev; k++) if (i - k >= 0) prev = lvl[cs[i - k].time] || 0;
          for (k = 1; k <= 3 && !next; k++) if (i + k < cs.length) next = lvl[cs[i + k].time] || 0;
          if (prev && next) L = Math.min(prev, next);
        }
        hi.push(L === 2 ? { time: cs[i].time, value: 1 } : { time: cs[i].time, value: 0 });
        mid.push(L === 1 ? { time: cs[i].time, value: 1 } : { time: cs[i].time, value: 0 });
      }
      bandHi.setData(hi); bandMid.setData(mid);
      var line = noBand ? null : self.chart.addLineSeries({ color: '#c0392b', lineWidth: 1, priceScaleId: 'risk', priceLineVisible: false, lastValueVisible: false,
                                            autoscaleInfoProvider: function () { return { priceRange: { minValue: 0, maxValue: 0.7 } }; } });
      if (line) self.chart.priceScale('risk').applyOptions({ scaleMargins: { top: 0.72, bottom: 0.16 }, visible: false });
      // 확률이 높음 경계를 넘은 구간은 선을 진하게, 아래는 옅게
      if (line) line.setData(j.prob.filter(function (p) { return have[p.time]; }).map(function (p) {
        return { time: p.time, value: p.value, color: p.value >= j.cut_hi ? '#a30000' : 'rgba(192,57,43,0.45)' };
      }));
      // 하락 표식: 위험 확률이 높음 경계를 처음 넘어선 날(파란 ▼ 위험↑) · 급등 뒤 과열이 처음 된 날(주황 ● 과열)
      self.riskMk = j.events.filter(function (e) { return have[e.time]; }).map(function (e) {
        return e.kind === 'risk' ? { time: e.time, position: 'aboveBar', color: 'rgba(43,98,217,0.7)', shape: 'arrowDown', size: 0.8, text: '' }
                                 : { time: e.time, position: 'aboveBar', color: 'rgba(230,126,34,0.8)', shape: 'circle', size: 0.7, text: '' };
      });
      self.applyMarkers();
      if (line) {
        line.createPriceLine({ price: j.cut_hi, color: '#c0392b', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' });
        line.createPriceLine({ price: j.cut_mid, color: 'rgba(214,137,16,0.8)', lineWidth: 1, lineStyle: 3, axisLabelVisible: false, title: '' });   // 주의 경계(주황 점선)
        self.riskSeries.push(line);
      }
      j.events.forEach(function (e) { if (have[e.time]) (self.riskEv = self.riskEv || {})[e.time] = e.kind === 'risk' ? '▼ 위험↑ 하락 위험이 처음 높음 경계를 넘은 날' : '● 과열 급등 뒤 과열이 처음 된 날'; });
      self.riskSeries.push(bandHi, bandMid);
      j.prob.forEach(function (q) { self.riskMap[q.time] = q.value; });
      self.riskCuts = { hi: j.cut_hi, mid: j.cut_mid };
      // 제목 줄 옆에 오늘의 위험 단계 배지(높음·주의·낮음)
      var bd = self.root.querySelector('.rbadge');
      if (bd && j.latest && j.latest.prob != null) {
        var lv = riskLevel(j.latest.prob, self.riskCuts);
        bd.textContent = '하락 위험 ' + lv + ' ' + (j.latest.prob * 100).toFixed(0) + '%';
        bd.className = 'rbadge rb-' + (lv === '높음' ? 'hi' : lv === '주의' ? 'mid' : 'lo');
        bd.title = '20일 안에 -10% 이하로 끝날 확률(전체 평균 약 24%). 높음 ' + (j.cut_hi * 100).toFixed(0) + '%↑ · 주의 ' + (j.cut_mid * 100).toFixed(0) + '%↑';
        bd.hidden = false;
      }
      if (!noBand && !self.legendRisk) {
        self.legendRisk = document.createElement('div');
        self.legendRisk.className = 'cleg cleg2';
        self.el.appendChild(self.legendRisk);
      }
      if (self.legendRisk) {
        self.legendRisk.textContent = '빨간 선 = 하락 위험 확률 · 점선 = 높음(' + (j.cut_hi * 100).toFixed(0) + '%)·주의(' + (j.cut_mid * 100).toFixed(0) + '%) 경계 — 자세한 표시 설명은 위 "표시 설명"';
        self.legendRisk.hidden = !!noBand;
      }
      note.textContent = '';
      var b = document.createElement('b'); b.textContent = j.name + ' · '; note.appendChild(b);
      var txt = j.summary, pc = function (v) { return v == null ? '-' : (v * 100).toFixed(0) + '%'; }, sg = function (v) { return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; };
      if (j.forecast) {
        txt += '\n20일 전망(오늘 기준, 괄호 = 전체 평균): ' + ['dn10', 'dn20', 'up10', 'up20'].map(function (k) {
          var f = j.forecast.prob[k]; return f.label + ' ' + pc(f.p) + '(' + pc(f.base) + ')';
        }).join(' · ');
        var r = j.forecast.range;
        txt += '\n예상 20일 수익률 범위(80%): ' + sg(r.q10) + ' ~ ' + sg(r.q90) + ', 중앙 ' + sg(r.q50);
      }
      if (j.overheat) {
        txt += '\n⚠ 급등 뒤 과열 구간: 60일 ' + sg(j.overheat.ret60) + ', 224일선 대비 ' + sg(j.overheat.dist224) + '. 이런 조건에서 20일 안 -20%↓ 22~29%·-30%↓ 8~14% (전체 7%·2%, 2022년 전·후 모두 비슷)';
      }
      if (j.track) {
        var t = j.track;
        txt += '\n이 종목 2022~ 실제(예측 때 모르던 기간): 위험 높음 ' + t['높음'].n + '일 중 −10%↓ ' + pc(t['높음'].dn10) + ' · 주의 ' + pc(t['주의'].dn10) + ' · 낮음 ' + pc(t['낮음'].dn10) + ' · 전체 ' + pc(t['전체'].dn10);
      }
      var nt = noBand && j.note.indexOf('아래 선 = 확률. ') >= 0 ? '맨 아래 띠 = 위험 높음(빨강)·주의(주황), 아래 선 = 확률. ' + j.note.split('아래 선 = 확률. ')[1] : j.note;
      txt += '\n※ 표시가 없다고 안전하다는 뜻은 아닙니다(큰 하락의 대부분은 위험↑ 표시일이 아닌 날 시작).';
      note.appendChild(document.createTextNode(txt + '\n' + nt));
    }).catch(function (e) {
      if (seq === self.riskSeq) note.textContent = '하락 위험: ' + e.message;
    });
  };

  // 이동평균선 색 범례(차트 왼쪽 위) — 선택한 세트의 기간별 색
  Pane.prototype.drawLegend = function (set) {
    if (!this.legend) {
      this.legend = document.createElement('div');
      this.legend.className = 'cleg';
      this.el.appendChild(this.legend);
    }
    var box = this.legend;
    box.textContent = '';
    set.periods.forEach(function (n) {
      var it = document.createElement('span'), sw = document.createElement('i');
      sw.style.background = set.colors[n];
      sw.style.height = (n === 224 ? 3 : 2) + 'px';
      it.appendChild(sw); it.appendChild(document.createTextNode(n + '일'));
      box.appendChild(it);
    });
    box.hidden = !set.periods.length;
  };

  Pane.prototype.drawMA = function () {
    var self = this, set = SETS[this.settings().set];
    this.lines.forEach(function (l) { self.chart.removeSeries(l); });
    this.lines = [];
    if (!this.data) return;
    this.drawLegend(set);
    set.periods.forEach(function (n) {
      var d = self.data.ma[n]; if (!d || !d.length) return;
      var s = self.chart.addLineSeries({ color: set.colors[n] + (n === 224 ? '' : 'bb'), lineWidth: n === 224 ? 2 : 1, lineStyle: n === 448 ? 2 : 0, crosshairMarkerVisible: false,
                                         priceLineVisible: false, lastValueVisible: false, title: '' });
      s.setData(d); self.lines.push(s);
    });
  };

  // 볼린저밴드(20, 2): 위·아래 = 20봉 평균 ± 2 표준편차(모집단), 가운데 = 20봉 평균(점선). 분봉도 같은 식으로 화면에서 계산.
  Pane.prototype.drawBB = function () {
    var self = this;
    this.bbLines.forEach(function (l) { self.chart.removeSeries(l); });
    this.bbLines = [];
    if (!this.data || !this.root.querySelector('.cbb').checked) return;
    var cs = this.data.candles, N = 20, up = [], mid = [], lo = [], i, k;
    for (i = N - 1; i < cs.length; i++) {
      var sum = 0, sq = 0;
      for (k = i - N + 1; k <= i; k++) { sum += cs[k].close; sq += cs[k].close * cs[k].close; }
      var m = sum / N, sd = Math.sqrt(Math.max(0, sq / N - m * m));
      up.push({ time: cs[i].time, value: m + 2 * sd }); mid.push({ time: cs[i].time, value: m }); lo.push({ time: cs[i].time, value: m - 2 * sd });
    }
    var col = dark ? 'rgba(170,178,196,0.75)' : 'rgba(110,118,136,0.75)';
    [[up, 0], [mid, 2], [lo, 0]].forEach(function (x) {
      var s = self.chart.addLineSeries({ color: col, lineWidth: 1, lineStyle: x[1], priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: '' });
      s.setData(x[0]); self.bbLines.push(s);
    });
  };

  // 기준일(신호 화면에서 넘어온 날짜): 그날(없으면 그 뒤 첫 봉)의 봉 번호·표시 시각
  Pane.prototype.focusIndex = function () {
    if (!BOOT.focus || !this.data || isIntra(this.settings().tf)) return -1;
    var c = this.data.candles, i = c.findIndex(function (x) { return x.time >= BOOT.focus; });
    return i < 0 ? c.length - 1 : i;
  };
  Pane.prototype.focusMarkers = function () {
    var i = this.focusIndex();
    return i < 0 ? [] : [{ time: this.data.candles[i].time, position: 'aboveBar', color: '#e0a000', shape: 'arrowDown', text: '기준일' }];
  };

  Pane.prototype.showRange = function () {
    if (!this.data) return;
    var n = this.data.candles.length, show = Math.min(bars, n), f = this.focusIndex();
    if (f >= 0) {   // 기준일을 오른쪽 3분의 2 지점에 — 그 전 흐름과 그 뒤 며칠을 함께 본다
      var before = Math.min(show, f + 1), after = Math.max(5, Math.round(show * 0.35));
      this.chart.timeScale().setVisibleLogicalRange({ from: f - before, to: Math.min(f + after, n + 4) });   // 그 뒤 데이터가 모자라면 끝에서 멈춤
      return;
    }
    this.chart.timeScale().setVisibleLogicalRange({ from: n - show - 1, to: n + 4 });
  };

  Pane.prototype.setStock = function (code, name) {
    this.code = code;
    this.root.querySelector('.cq').value = name || code;
    this.root.querySelector('.cname').textContent = name || code;
    return this.load();
  };

  Pane.prototype.bind = function () {
    var self = this, q = this.root.querySelector('.cq'), box = this.root.querySelector('.cres'), timer = null, items = [];
    function pick(it) {
      box.hidden = true;
      // '같은 종목'이 켜져 있으면 한쪽에 입력해도 양쪽 모두 그 종목으로(왼쪽 예측 · 오른쪽 AI 를 한 종목으로 보는 용도)
      var targets = document.getElementById('same').checked && other(self) ? [self, other(self)] : [self];
      Promise.all(targets.map(function (p) { return p.setStock(it.code, it.name); })).then(function () {
        if (targets.length > 1) showBoth(); else self.showRange();
        saveUrl();
      });
    }
    q.addEventListener('input', function () {
      clearTimeout(timer);
      var v = q.value.trim();
      if (!v) { box.hidden = true; return; }
      timer = setTimeout(function () {
        fetch('/api/search?q=' + encodeURIComponent(v)).then(function (r) { return r.json(); }).then(function (list) {
          items = list; box.textContent = '';
          list.forEach(function (it) {
            var li = document.createElement('li');
            li.appendChild(document.createTextNode(it.name));
            var sm = document.createElement('small'); sm.textContent = it.code + ' ' + (it.market || ''); li.appendChild(sm);
            li.addEventListener('mousedown', function () { pick(it); });
            box.appendChild(li);
          });
          box.hidden = !list.length;
        });
      }, 150);
    });
    q.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        if (items[0]) pick(items[0]);
        else if (/^[A-Za-z0-9.\-]{1,12}$/.test(q.value.trim())) pick({ code: q.value.trim(), name: q.value.trim() });
      }
    });
    q.addEventListener('blur', function () { setTimeout(function () { box.hidden = true; }, 150); });
    this.root.querySelector('.cset').addEventListener('change', function () { self.drawMA(); if (self.settings().kind === 'cross') self.loadPred(); saveUrl(); });
    var sa = document.getElementById('showall');
    if (sa && !sa.dataset.bound) { sa.dataset.bound = '1'; sa.addEventListener('change', function () { all().forEach(function (p) { if (p.pred) p.drawPred(); }); saveUrl(); }); }
    this.root.querySelector('.crk').addEventListener('change', function () { self.loadRisk(); saveUrl(); });
    this.root.querySelector('.cbb').addEventListener('change', function () { self.drawBB(); saveUrl(); });
    var cv = this.root.querySelector('.cver');
    if (cv) cv.addEventListener('change', function () { self.clearPredSeries(); if (self.pred) self.drawPred(); saveUrl(); });
    var more = this.root.querySelector('.cmore');
    more.addEventListener('click', function () {
      var on = self.root.classList.toggle('expand');
      more.textContent = on ? '설명 접기 ▴' : '설명 더보기 ▾'; more.setAttribute('aria-expanded', on ? 'true' : 'false');
    });
    this.root.querySelector('.ckind').addEventListener('change', function () { self.loadPred(); saveUrl(); });
    this.root.querySelector('.ctf').addEventListener('change', function () {
      var v = self.root.querySelector('.ctf').value, o = other(self), ot = o ? o.settings().tf : v;
      var both = !!o && document.getElementById('sync').checked && document.getElementById('same').checked && ot !== v && (isIntra(v) || isIntra(ot));
      // 분봉으로 들어가거나 나올 때는 (동기화·같은 종목이면) 양쪽이 함께 바뀐다 — 분봉과 일봉은 서로 맞출 수 없어서 한쪽만 바뀌면 어색하다
      function fixAi() { /* '자동'은 분봉이면 예측(단테)만 그린다 — 선택은 그대로 */ }   // AI 는 일봉 이상에서만
      fixAi(self);
      if (both) { o.root.querySelector('.ctf').value = v; fixAi(o); }
      Promise.all(both ? [self.load(), o.load()] : [self.load()]).then(function () { showBoth(); saveUrl(); });
    });
  };

  function saveUrl() {
    try {
      var B = panes.b || panes.a, u = new URL(location.href), a = panes.a.settings(), b = B.settings();
      u.searchParams.set('a', panes.a.code); u.searchParams.set('b', B.code);
      u.searchParams.set('sa', a.set); u.searchParams.set('sb', b.set);
      u.searchParams.set('ta', a.tf); u.searchParams.set('tb', b.tf);
      u.searchParams.set('ka', a.kind); u.searchParams.set('kb', b.kind);
      u.searchParams.set('all', document.getElementById('showall') && document.getElementById('showall').checked ? '1' : '0');
      u.searchParams.set('ra', panes.a.root.querySelector('.crk').checked ? '1' : '0'); u.searchParams.set('rb', B.root.querySelector('.crk').checked ? '1' : '0');
      u.searchParams.set('ba', panes.a.root.querySelector('.cbb').checked ? '1' : '0'); u.searchParams.set('bb', B.root.querySelector('.cbb').checked ? '1' : '0');
      var va = !!(panes.a.root.querySelector('.cver') && panes.a.root.querySelector('.cver').checked);
      u.searchParams.set('va', va ? '1' : '0');
      history.replaceState(null, '', u);
      writeStore({ a: panes.a.code, b: B.code, sa: a.set, sb: b.set, ta: a.tf, tb: b.tf, ka: a.kind, kb: b.kind,
                   ra: panes.a.root.querySelector('.crk').checked, rb: B.root.querySelector('.crk').checked,
                   ba: panes.a.root.querySelector('.cbb').checked, bb: B.root.querySelector('.cbb').checked, va: va,
                   all: !!(document.getElementById('showall') && document.getElementById('showall').checked), bars: bars });
    } catch (e) { /* 주소 저장 실패는 무시 */ }
  }

  // ---- 설정 기억(브라우저): 주소에 설정이 없으면 마지막으로 쓴 종목·이평선 세트·봉 단위·표시 옵션으로 연다 ----
  var STORE_KEY = 'jb-ui-v4';  // v4(2026-10-19): 종합 신호 하나 — 하락 위험·단테 동그라미 없음, '근거 보기' 기본 꺼짐
  function readStore() { try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}; } catch (e) { return {}; } }
  function writeStore(o) { try { localStorage.setItem(STORE_KEY, JSON.stringify(o)); } catch (e) { /* 저장 불가(사생활 보호 모드 등)는 무시 */ } }
  (function restoreUi() {
    var q = new URLSearchParams(location.search), st = readStore();
    if (!q.has('a') && st.a && /^[A-Za-z0-9.-]{1,12}$/.test(st.a) && !window.__jbRestored) {   // 주소에 종목이 없으면 마지막으로 본 종목으로
      window.__jbRestored = true;
      location.replace(location.pathname + '?a=' + encodeURIComponent(st.a) + '&b=' + encodeURIComponent(/^[A-Za-z0-9.-]{1,12}$/.test(st.b || '') ? st.b : st.a));
      return;
    }
    if (!q.has('sa') && !q.has('ta') && !q.has('ka')) {
      ['a', 'b'].forEach(function (sd) {
        var root = document.querySelector('.cpane[data-side="' + sd + '"]');
        if (!root) return;   // 한 차트면 오른쪽(b)은 없음
        [['.cset', 's'], ['.ctf', 't'], ['.ckind', 'k']].forEach(function (pr) {
          var el = root.querySelector(pr[0]), v = st[pr[1] + sd];
          if (v && Array.prototype.some.call(el.options, function (o) { return o.value === v; })) el.value = v;
        });
        if (st['r' + sd] !== undefined) root.querySelector('.crk').checked = !!st['r' + sd];
        if (st['b' + sd] !== undefined) root.querySelector('.cbb').checked = !!st['b' + sd];
        if (st.va !== undefined && root.querySelector('.cver')) root.querySelector('.cver').checked = !!st.va;
      });
      var sa = document.getElementById('showall'); if (sa && st.all !== undefined) sa.checked = !!st.all;
    }
    if (st.bars && !q.has('date')) {
      var btn = document.querySelector('#range button[data-bars="' + st.bars + '"]');
      if (btn) { bars = st.bars; Array.prototype.forEach.call(btn.parentNode.children, function (x) { x.classList.toggle('on', x === btn); }); if (bars > loadBars) loadBars = 5000; }
    }
  })();

  var panes = { a: new Pane('a'), b: document.querySelector('.cpane[data-side="b"]') ? new Pane('b') : null };
  window.__compare = panes;  // 브라우저 콘솔·자동 점검용
  all().forEach(function (p) { p.root.querySelector('.cname').textContent = BOOT[p.side].name; });

  window.addEventListener('resize', function () {
    all().forEach(function (p) { p.chart.applyOptions({ width: p.el.clientWidth }); });
  });

  function showBoth() {
    // 한쪽(A) 범위를 기준으로 양쪽을 같은 기간에 맞춘다. 데이터를 막 바꾼 직후에는 차트가 옛 범위 이벤트를
    // 뒤늦게 내보내므로, 잠시 동기화 이벤트를 막고(syncing) 두 번에 걸쳐 맞춘다.
    function apply() {
      syncing = true;
      panes.a.showRange();
      var r = panes.a.chart.timeScale().getVisibleRange();
      if (panes.b) {
        if (syncOn() && r) { try { panes.b.chart.timeScale().setVisibleRange(r); } catch (e) { panes.b.showRange(); } }
        else panes.b.showRange();
      }
      setTimeout(function () { syncing = false; }, 120);
    }
    apply();
    setTimeout(apply, 150);
  }

  Promise.all(all().map(function (p) { return p.load(); })).then(function () { showBoth(); saveUrl(); });

  document.getElementById('range').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    Array.prototype.forEach.call(this.children, function (x) { x.classList.toggle('on', x === b); });
    bars = parseInt(b.getAttribute('data-bars'), 10);
    if (bars > loadBars) {            // 받아 둔 것보다 긴 기간 — 더 받아서 다시 그린다
      loadBars = 5000;
      Promise.all(all().map(function (p) { return p.load(); })).then(function () { showBoth(); });
      return;
    }
    showBoth();
  });
  document.getElementById('sync').addEventListener('change', function () { showBoth(); });

  if (document.getElementById('quick')) document.getElementById('quick').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    var q = b.getAttribute('data-q'), A = panes.a, B = panes.b;
    function setSel(p, set, tf, kind) {
      p.root.querySelector('.cset').value = set; p.root.querySelector('.ctf').value = tf;
      if (kind) p.root.querySelector('.ckind').value = kind;
    }
    if (q === 'swap') {
      var sa = A.settings(), sb = B.settings(), ca = A.code, cb = B.code;
      var na = A.root.querySelector('.cname').textContent, nb = B.root.querySelector('.cname').textContent;
      setSel(A, sb.set, sb.tf, sb.kind); setSel(B, sa.set, sa.tf, sa.kind);
      Promise.all([A.setStock(cb, nb), B.setStock(ca, na)]).then(function () { showBoth(); saveUrl(); });
      return;
    }
    var name = A.root.querySelector('.cname').textContent;
    if (q === 'sets') { setSel(A, 'a', 'D'); setSel(B, 'b', 'D'); }
    if (q === 'tf') { setSel(A, 'a', 'D'); setSel(B, 'a', 'W'); }
    if (q === 'scalp') { setSel(A, 'a', '5m', 'dante'); setSel(B, 'a', '15m', 'dante'); bars = 120; }
    Promise.all([A.setStock(A.code, name), B.setStock(A.code, name)]).then(function () { showBoth(); saveUrl(); });
  });

  // ---- 휴대폰: 도구줄 접기 · 두 번째 차트 보기 ----
  var tbt = document.getElementById('tbtoggle'), showb = document.getElementById('showb');
  if (tbt) tbt.addEventListener('click', function () {
    var tb = document.querySelector('.toolbar'); tb.classList.toggle('open');
    tbt.textContent = tb.classList.contains('open') ? '⚙ 도구 접기' : '⚙ 도구';
  });
  if (showb) showb.addEventListener('click', function () {
    var g = document.querySelector('.cgrid'); g.classList.toggle('show-b');
    var on = g.classList.contains('show-b');
    showb.textContent = on ? '두 번째 차트 숨기기' : '두 번째 차트 보기';
    if (on) { panes.b.chart.applyOptions({ width: panes.b.el.clientWidth }); showBoth(); }
  });
})();
