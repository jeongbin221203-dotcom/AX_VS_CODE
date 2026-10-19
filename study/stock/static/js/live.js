// 실시간 시세 — 장중 체결로 차트의 마지막 봉(일·주·월·분봉)을 고친다. 서버 주소는 views/live.py(/api/live/<코드>, /api/live/<코드>/today).
// 체결이 오면: 같은 봉이면 고가·저가·종가·거래량을 고치고, 다음 봉 시각이면 새 봉을 붙인다(이전 봉 시각이면 버림).
// 봉 시각: 일봉 'YYYY-MM-DD'(한국 날짜), 주봉 = 그 주 금요일, 월봉 = 그 달 마지막 날, 분봉 = 한국 시각(+9시간) unix 초의 09:00 기준 N분 묶음 시작.
(function () {
  var KST = 9 * 3600, STEP = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '60m': 60 };
  function ymd(ms) { return new Date(ms).toISOString().slice(0, 10); }
  function bucket(tf, sec) {
    var k = (sec + KST) * 1000, d = new Date(k);
    if (STEP[tf]) {
      var day = Math.floor((sec + KST) / 86400), m = Math.floor(((sec + KST) % 86400) / 60), n = STEP[tf];
      return day * 86400 + (540 + Math.floor((m - 540) / n) * n) * 60;
    }
    if (tf === 'W') return ymd(k + ((5 - d.getUTCDay() + 7) % 7) * 86400000);
    if (tf === 'M') return ymd(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0));
    return ymd(k);
  }
  // 차트 라이브러리가 update() 로 받은 봉의 time 을 {year, month, day} 로 바꿔 놓는 일이 있어 비교 전에 문자열로 되돌린다
  function key(t) { return t && typeof t === 'object' ? t.year + '-' + ('0' + t.month).slice(-2) + '-' + ('0' + t.day).slice(-2) : t; }
  function cmp(a, b) { a = key(a); b = key(b); return a < b ? -1 : a > b ? 1 : 0; }
  // d = /api/chart 응답(candles, volume, last). 봉 하나를 고치거나 붙이고 { bar, vol, isNew } 를 돌려준다.
  function merge(d, t, o, h, l, c, v, addVol) {
    var cs = d.candles, vs = d.volume, last = cs[cs.length - 1];
    if (last && cmp(t, last.time) < 0) return null;
    var bar, vol, isNew = !last || cmp(t, last.time) > 0;
    if (isNew) {
      if (last) d.prevClose = last.close;
      bar = { time: t, open: o, high: h, low: l, close: c }; cs.push(bar);
      vol = { time: t, value: v, up: c >= o }; vs.push(vol);
    } else {
      bar = last; bar.high = Math.max(bar.high, h); bar.low = Math.min(bar.low, l); bar.close = c;
      vol = vs[vs.length - 1];
      if (!vol || vol.time !== t) { vol = { time: t, value: 0 }; vs.push(vol); }
      vol.value = addVol ? vol.value + v : v; vol.up = c >= bar.open;
    }
    if (d.prevClose === undefined) d.prevClose = cs.length > 1 ? cs[cs.length - 2].close : bar.open;
    d.last = { date: String(t), close: c, change: d.prevClose ? (c / d.prevClose - 1) * 100 : 0, volume: vol.value };
    return { bar: bar, vol: vol, isNew: isNew };
  }
  function applyTrade(d, tf, tr) {
    if (!d || !d.candles || !tr || !(tr.price > 0)) return null;
    var p = tr.price, sec = tr.time || Math.floor(Date.now() / 1000);
    if (STEP[tf]) {   // 분봉 자료는 정규장(09:00~15:30)만 있으므로 프리·애프터마켓 체결은 분봉에 붙이지 않는다(일·주·월봉에는 반영)
      var md = Math.floor(((sec + KST) % 86400) / 60);
      if (md < 540 || md >= 930) return null;
    }
    return merge(d, bucket(tf, tr.time || Math.floor(Date.now() / 1000)), p, p, p, p, tr.volume || 0, true);
  }
  // 오늘 일봉(장중이라 DB 에 아직 없는 것)을 일·주·월봉에 붙인다. 분봉은 차트 자료에 이미 들어 있어 건드리지 않는다.
  function applyToday(d, tf, b) {
    if (!d || !d.candles || !b || STEP[tf]) return null;
    var t = tf === 'D' ? b.date : bucket(tf, Date.parse(b.date + 'T12:00:00+09:00') / 1000);
    var cs = d.candles, last = cs[cs.length - 1];
    if (last && cmp(t, last.time) < 0) return null;
    if (!last || cmp(t, last.time) > 0) { d.todayVol = b.volume; return merge(d, t, b.open, b.high, b.low, b.close, b.volume, false); }
    if (tf === 'D') {   // 같은 날 봉이 이미 있으면(장 마감 뒤 저장된 경우 등) 받은 값으로 바꾼다
      last.open = b.open; last.high = b.high; last.low = b.low;
      d.todayVol = 0;
      return merge(d, t, b.open, b.high, b.low, b.close, b.volume, false);
    }
    // 주봉·월봉: 이번 주(달) 봉에 오늘 값을 더한다 — 거래량은 지난번에 더한 만큼을 빼고 더해 두 번 세지 않는다
    var add = b.volume - (d.todayVol || 0); d.todayVol = b.volume;
    return merge(d, t, last.open, b.high, b.low, b.close, add, true);
  }
  // 종목 하나의 체결 흐름. handler({type:'trade'|'feed'|'source', ...}). 돌려준 함수를 부르면 끊는다.
  var streams = {};
  function watch(code, handler) {
    var s = streams[code];
    if (!s) {
      s = streams[code] = { es: new EventSource('/api/live/' + encodeURIComponent(code)), hs: [] };
      s.es.onmessage = function (e) {
        var m; try { m = JSON.parse(e.data); } catch (x) { return; }
        if (m.type === 'source') s.src = m; else if (m.type === 'feed') s.feed = m;   // 나중에 붙는 차트에 다시 알려 줄 마지막 상태
        s.hs.slice().forEach(function (h) { h(m); });
      };
      s.es.onerror = function () { s.hs.slice().forEach(function (h) { h({ type: 'feed', state: 'reconnect' }); }); };
    }
    s.hs.push(handler);
    if (s.src) handler(s.src);          // 같은 종목을 두 번째로 띄운 차트도 연결 상태를 바로 알게
    if (s.feed) handler(s.feed);
    return function () {
      s.hs = s.hs.filter(function (h) { return h !== handler; });
      if (!s.hs.length) { s.es.close(); delete streams[code]; }
    };
  }
  function today(code) {
    return fetch('/api/live/' + encodeURIComponent(code) + '/today').then(function (r) { return r.ok ? r.json() : { bar: null }; }).catch(function () { return { bar: null }; });
  }
  function marketOpen() {   // 평일 08:00~20:00(한국 시각, 넥스트레이드 프리·애프터마켓 포함)
    var k = new Date(Date.now() + KST * 1000), m = k.getUTCHours() * 60 + k.getUTCMinutes(), w = k.getUTCDay();
    return w >= 1 && w <= 5 && m >= 480 && m <= 1200;
  }
  // 상태 문구(작은 글씨) 붙이기: el 에 연결 상태와 마지막 체결 시각을 보여 준다. 돌려준 함수에 메시지를 넘긴다.
  function status(el) {
    var src = '', lastT = 0;
    function show(extra) {
      if (!el) return;
      var txt, on = false;
      if (extra) txt = extra;
      else if (!marketOpen()) txt = '장 시간이 아님 — 평일 08:00~20:00(넥스트레이드 포함)에 실시간으로 바뀝니다';
      else if (src === 'relay') { txt = '● 실시간 체결'; on = true; }
      else if (src === 'poll') { txt = '● 실시간(5초마다 현재가, 거래량 제외)'; on = true; }
      else if (src === 'none') txt = '실시간 없음(토스 키 없음)';
      else txt = '실시간 연결 중…';
      if (on && lastT) txt += ' · ' + new Date((lastT + KST) * 1000).toISOString().slice(11, 19);
      el.textContent = txt; el.className = 'live' + (on ? ' on' : '');
    }
    show();
    return function (m) {
      if (m.type === 'source') { src = m.src; show(); }
      else if (m.type === 'trade') { lastT = m.time || Math.floor(Date.now() / 1000); if (m.src === 'poll') src = 'poll'; show(); }
      else if (m.type === 'feed') {
        if (m.state === 'reconnect') show('실시간 다시 연결 중…');
        else if (m.state === 'error' || m.state === 'rejected') show('실시간 오류' + (m.detail ? ': ' + m.detail : ''));
        else if (m.state === 'nokey') { src = 'none'; show(); }
        else show();
      }
    };
  }
  window.StockLive = { bucket: bucket, applyTrade: applyTrade, applyToday: applyToday, watch: watch, today: today, status: status, marketOpen: marketOpen };
})();
