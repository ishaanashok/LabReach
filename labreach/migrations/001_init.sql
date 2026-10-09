CREATE TABLE targets (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  name_norm TEXT NOT NULL,
  title TEXT,
  role TEXT,                         -- professor | postdoc | grad_student | lab_manager | staff
  university TEXT NOT NULL,
  university_norm TEXT NOT NULL,
  department TEXT,
  lab TEXT,
  lab_url TEXT,
  profile_url TEXT,
  email TEXT UNIQUE,
  email_source_url TEXT,
  email_source_snippet TEXT,
  timezone TEXT,
  research_interests TEXT,
  hs_policy_note TEXT,
  hs_policy_source TEXT,
  fit_score REAL,
  status TEXT NOT NULL DEFAULT 'new',
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX idx_targets_name_uni ON targets(name_norm, university_norm);

CREATE TABLE sources (
  id INTEGER PRIMARY KEY,
  target_id INTEGER NOT NULL REFERENCES targets(id),
  type TEXT NOT NULL,                -- paper | lab_news | project | profile
  title TEXT,
  year INTEGER,
  url TEXT NOT NULL,
  snippet TEXT NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE emails (
  id INTEGER PRIMARY KEY,
  target_id INTEGER NOT NULL REFERENCES targets(id),
  kind TEXT NOT NULL CHECK (kind IN ('initial','fu1','fu2')),
  state TEXT NOT NULL DEFAULT 'draft',   -- draft | pilot | queued | sent
  template_version TEXT,
  subject TEXT,
  body TEXT,
  claims_json TEXT,
  lint_report_json TEXT,
  word_count INTEGER,
  gate_results_json TEXT,
  message_id TEXT,
  thread_root_message_id TEXT,
  sent_at TEXT,
  bounced_at TEXT,
  replied_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  UNIQUE (target_id, kind)
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  type TEXT NOT NULL,
  target_id INTEGER,
  detail_json TEXT
);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

CREATE TABLE programs (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT,
  deadline TEXT,
  status TEXT
);

CREATE TABLE do_not_contact (
  id INTEGER PRIMARY KEY,
  email TEXT,
  domain TEXT,
  reason TEXT,
  added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (email IS NOT NULL OR domain IS NOT NULL)
);

CREATE TABLE claude_calls (
  id INTEGER PRIMARY KEY,
  timestamp TEXT NOT NULL,
  purpose TEXT NOT NULL,
  success INTEGER NOT NULL
);

CREATE TABLE settings_state (
  key TEXT PRIMARY KEY,
  value TEXT
);
