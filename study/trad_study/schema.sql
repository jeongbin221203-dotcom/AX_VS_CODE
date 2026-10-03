PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, round INTEGER, subject INTEGER,
 mode TEXT NOT NULL, started_at REAL NOT NULL, deadline REAL,
 submitted_at REAL, score REAL, last_index INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS responses (
 attempt_id TEXT NOT NULL REFERENCES attempts(id), qid INTEGER NOT NULL,
 position INTEGER NOT NULL, choice INTEGER CHECK(choice BETWEEN 1 AND 4),
 flagged INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(attempt_id,qid), UNIQUE(attempt_id,position)
);
CREATE TABLE IF NOT EXISTS notes (
 qid INTEGER PRIMARY KEY, body TEXT NOT NULL DEFAULT '',
 mastered INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS explanations (
 qid INTEGER PRIMARY KEY, body TEXT NOT NULL, model TEXT NOT NULL,
 created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'AI 초안'
);
CREATE INDEX IF NOT EXISTS response_question ON responses(qid);
