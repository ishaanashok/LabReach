-- Pipeline columns and tables added after the first scaffold.
ALTER TABLE targets ADD COLUMN first_name TEXT;
ALTER TABLE targets ADD COLUMN last_name TEXT;
ALTER TABLE targets ADD COLUMN informal INTEGER NOT NULL DEFAULT 0;   -- grad student page reads informal
ALTER TABLE targets ADD COLUMN lab_key TEXT;                          -- one active contact per lab
ALTER TABLE targets ADD COLUMN variant TEXT;                          -- initial_a | initial_b | initial_c
ALTER TABLE targets ADD COLUMN local INTEGER NOT NULL DEFAULT 0;
ALTER TABLE targets ADD COLUMN campus TEXT;
CREATE INDEX idx_targets_lab ON targets(lab_key);

ALTER TABLE emails ADD COLUMN send_after TEXT;        -- earliest send time (UTC ISO), includes jitter
ALTER TABLE emails ADD COLUMN slots_json TEXT;        -- model-written slots + choices used to render
ALTER TABLE emails ADD COLUMN personalization TEXT;   -- p1 + p2, for the n-gram overlap lint
ALTER TABLE emails ADD COLUMN pilot_file TEXT;

CREATE TABLE directories (
  id INTEGER PRIMARY KEY,
  institution TEXT NOT NULL,
  department_group TEXT NOT NULL,       -- me | ee | cs | bio
  url TEXT NOT NULL UNIQUE,
  verified_at TEXT
);

CREATE TABLE replies (
  id INTEGER PRIMARY KEY,
  email_id INTEGER REFERENCES emails(id),
  target_id INTEGER REFERENCES targets(id),
  received_at TEXT,
  message_id TEXT UNIQUE,
  label TEXT NOT NULL,                  -- positive | ambiguous | decline_hs | decline_other | out_of_office | unsafe
  confidence REAL,
  excerpt TEXT,                         -- truncated; the only reply text we keep
  draft_saved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
