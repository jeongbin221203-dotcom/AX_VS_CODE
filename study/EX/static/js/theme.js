/* 저장한 화면 테마를 그리기 전에 적용(어두운 테마에서 밝게 깜빡이지 않게) */
try { var t = localStorage.getItem('ex-theme'); if (t) document.documentElement.setAttribute('data-theme', t); } catch (e) { /* 저장소 없음 */ }
