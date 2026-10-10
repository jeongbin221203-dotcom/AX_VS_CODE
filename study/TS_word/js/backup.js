/* 백업·복원 (window.Backup) — 단어 기록(Ward, `ward:v1`)과 시험 기록(TSStore, `ts:v1`)을 한 파일로.
   파일 모양: 예전 단어 백업({app, cards, log, quiz, settings}) 위에 `ts` 항목({settings, sessions, attempts, notes, ext})을 얹은 것이라
   예전 백업 파일도, TS 앱에서 변환한 파일(tools/import_ts_db.py)도 그대로 불러올 수 있다. */
(function (root) {
  "use strict";
  const Ward = () => root.Ward, TSStore = () => root.TSStore;
  const exportObject = () => ({ ...JSON.parse(Ward().exportJSON()), v: 2, ts: TSStore().exportData() });
  const exportText = () => JSON.stringify(exportObject());

  /** mode: "merge"(합치기) | "replace"(바꾸기). 파일에 들어 있는 부분(단어/시험)만 처리한다 — 단어만 든 예전 백업으로 시험 기록을 지우지 않는다. */
  function importText(text, mode = "merge") {
    const raw = JSON.parse(text);
    if (!raw || typeof raw !== "object") throw new Error("백업 파일 모양이 아닙니다");
    const hasWard = raw.cards && typeof raw.cards === "object";
    const hasTs = raw.ts && typeof raw.ts === "object" && !Array.isArray(raw.ts);
    if (!hasWard && !hasTs) throw new Error("백업 파일 모양이 아닙니다");
    const out = {};
    if (hasWard) out.ward = Ward().importJSON(text, mode);
    if (hasTs) out.ts = TSStore().importData(raw.ts, mode);
    return out;
  }

  /** 지금 저장된 양 (설정 화면 표시용) */
  const summary = () => {
    const d = TSStore().data();
    return { words: Object.keys(JSON.parse(Ward().exportJSON()).cards).length, sessions: d.sessions.length, attempts: d.attempts.length,
             notes: Object.keys(d.notes).length, bytes: TSStore().usageBytes() };
  };

  root.Backup = { exportObject, exportText, importText, summary };
  if (typeof module !== "undefined") module.exports = root.Backup;
})(typeof window !== "undefined" ? window : globalThis);
