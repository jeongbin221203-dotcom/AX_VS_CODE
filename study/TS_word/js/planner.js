/* 목표 점수·시험일·약한 파트로 오늘의 학습 계획을 만든다 (window.Planner) — TS 앱 core/planner.py + views/main.vocab_hero.
   단어 쪽 정보(복습·새 단어 개수)는 Ward.queue 를 쓴다. 토익 단어 세트가 불러와진 상태여야 한다
   (토익 화면은 UI.boot("toeic") 가 알아서 맞춘다). */
(function (root) {
  "use strict";
  const U = root.TSU || require("./tsutil.js");
  const S = root.Score || require("./scoring.js");
  const St = () => root.Stats;
  const MIN_ATTEMPTS_FOR_WEAKNESS = 8;
  const LEVEL_UP_RATE = 0.8;        // 이 정답률을 넘으면 한 등급 위 문제를 권한다
  const LEVEL_UP_MIN_N = 20;

  const toInt = (v, def = null) => { const s = String(v ?? "").trim(); return /^[+-]?\d+$/.test(s) ? parseInt(s, 10) : def; };
  const validDate = s => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s || "")) return false;
    const [y, m, d] = s.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    return dt.getFullYear() === y && dt.getMonth() === m - 1 && dt.getDate() === d;
  };

  /** 현재 점수: 가장 최근 진단·모의고사 추정치 → 없으면 설정의 직접 입력 점수. → [점수|null, 출처] */
  function currentScore(settings) {
    const est = St().latestEstimate();
    const manual = toInt(settings.current_score);
    if (est && est.total_est !== null) {
      if (manual !== null && est.finished_at < (settings.current_score_at || "")) return [manual, "직접 입력"];
      return [est.total_est, "추정 (" + (est.mode === "diagnostic" ? "진단" : "모의고사") + ")"];
    }
    if (manual !== null) return [manual, "직접 입력"];
    return [null, ""];
  }

  /** 기본은 현재 등급. 그 등급에서 충분히 맞히면 한 단계 위를 권한다. */
  function recommendedLevel(part, gradeLevel, lvlAcc) {
    const a = St().levelAcc(lvlAcc, part, gradeLevel);
    if (a && a.n >= LEVEL_UP_MIN_N && a.rate >= LEVEL_UP_RATE && gradeLevel < 5) return gradeLevel + 1;
    return gradeLevel;
  }

  /** opts: {today:'YYYY-MM-DD', vocabQueue:(level)=>{due,new}, openNotes:number} — 모두 생략 가능(테스트용) */
  function build(settings, opts = {}) {
    const today = opts.today || U.todayStr();
    const G = root.TS_GUIDE.GUIDE;
    const [score, source] = currentScore(settings);
    const grade = score !== null ? S.gradeFor(score) : null;
    const target = toInt(settings.target_score, 800);
    const targetGrade = S.gradeFor(target);
    let exam = null, daysLeft = null;
    if (settings.exam_date && validDate(settings.exam_date)) { exam = settings.exam_date; daysLeft = U.daysBetween(today, exam); }
    const gap = score !== null ? target - score : null;
    let weeklyGain = null;
    if (gap !== null && gap > 0 && daysLeft && daysLeft > 0) weeklyGain = U.pyRound(gap / Math.max(daysLeft / 7, 1));

    const lv = grade ? grade.level : 1;
    const guide = G[lv];
    const acc = St().partAccuracy({ lastN: 60 });
    const lvlAcc = St().levelAccuracy();

    // 약한 파트: 기록이 충분하면 (등급 목표 정답률 - 실제 정답률)이 큰 순, 아니면 등급 기본 순서
    let focus = guide.focus_parts.slice();
    const measured = {};
    for (const [p, a] of Object.entries(acc)) if (a.n >= MIN_ATTEMPTS_FOR_WEAKNESS) measured[p] = a.rate;
    if (Object.keys(measured).length) {
      const shortfall = p => guide.targets[p] - measured[p];
      // 이 등급의 목표가 없는 파트(예: 1등급의 Part 3·7)는 약점 계산에서 뺀다 — 기초 파트를 밀어내지 않게
      let weak = Object.keys(measured).filter(p => p in guide.targets);
      weak.sort((a, b) => shortfall(b) - shortfall(a));
      weak = weak.filter(p => shortfall(p) > 0).map(Number);
      const top = weak.slice(0, 2);
      focus = top.concat(focus.filter(p => !top.includes(p)));
    }
    focus = focus.slice(0, 3);

    const dailyQ = toInt(settings.daily_questions, 40);
    const dailyNew = toInt(settings.daily_new_words, guide.daily_new_words);
    const todayDone = St().todayCounts();
    const vq = opts.vocabQueue ? opts.vocabQueue(lv, dailyNew) : root.Ward.queue({ startLevel: lv, dailyNew });
    const openNotes = opts.openNotes !== undefined ? opts.openNotes : root.TSStore.wrongNotes("open").length;

    const tasks = [];
    tasks.push({ kind: "vocab", title: "단어 복습 + 새 단어", detail: `복습 ${vq.due.length}개 · 새 단어 ${vq.new.length}개`,
                 done: !vq.due.length && !vq.new.length, href: "study.html" });
    const share = [0.5, 0.3, 0.2];
    focus.forEach((p, i) => {
      let n = Math.max(5, U.pyRound(dailyQ * share[i]));
      if (p === 3 || p === 4) n = Math.max(3, U.pyRound(n / 3) * 3);
      const rl = recommendedLevel(p, lv, lvlAcc);
      const done = todayDone.per_part[p] || 0;
      tasks.push({ kind: "part", part: p, level: rl, title: `Part ${p} ${S.PART_INFO[p].name}`,
                   detail: `${S.GRADE_BY_LEVEL[rl].name} 등급 ${n}문항` + (done ? ` · 오늘 ${done}문항 풂` : ""),
                   done: done >= n, href: `practice.html?start=1&part=${p}&level=${rl}&n=${n}` });
    });
    if (openNotes) {
      tasks.push({ kind: "review", title: "오답노트 복습", detail: `남은 오답 ${openNotes}문항 중 10문항`,
                   done: todayDone.reviewed >= Math.min(10, openNotes), href: "review.html?start=1&n=10" });
    }
    const last = St().latestEstimate();
    if (!last) {
      tasks.unshift({ kind: "diagnostic", title: "진단 테스트", detail: "약 20분 · 현재 등급을 먼저 확인", done: false, href: "diagnostic.html" });
    } else {
      const ago = U.daysBetween(last.finished_at.slice(0, 10), today);
      if (ago >= 7) {
        const form = lv >= 3 ? "half" : "mini";
        tasks.push({ kind: "mock", title: S.MOCK_FORMS[form].name, detail: `마지막 점수 확인 ${ago}일 전`, done: false, href: `mock.html?form=${form}` });
      }
    }
    return { score, score_source: source, grade, target, target_grade: targetGrade, gap, exam, days_left: daysLeft, weekly_gain: weeklyGain,
             guide, focus, tasks, today: todayDone, daily_q: dailyQ, open_notes: openNotes,
             vocab_due: vq.due.length, vocab_new: vq.new.length };
  }

  /** 오늘 화면 맨 위 '오늘의 단어' 카드 — 복습·새 단어 개수, 내 등급 단어를 얼마나 봤는지 (Ward 의 토익 세트 기준) */
  function vocabHero(plan, settings) {
    const W = root.Ward;
    const lv = plan.grade ? plan.grade.level : null;
    const daily = toInt(W.settings().daily_new, 0);
    const q = W.queue({ startLevel: lv, dailyNew: daily });
    const prog = W.levelProgress().find(p => p.level === (lv || 1));
    return { due: q.due.length, new: q.new.length, daily_new: daily, words_today: plan.today.words, level: lv || 1,
             grade_name: plan.grade ? plan.grade.name : "Orange", seen: prog.seen, total: prog.total, mastered: prog.mastered,
             done: !q.due.length && !q.new.length };
  }

  root.Planner = { currentScore, recommendedLevel, build, vocabHero, MIN_ATTEMPTS_FOR_WEAKNESS, LEVEL_UP_RATE, LEVEL_UP_MIN_N };
  if (typeof module !== "undefined") module.exports = root.Planner;
})(typeof window !== "undefined" ? window : globalThis);
