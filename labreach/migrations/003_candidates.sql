CREATE TABLE candidates (
  id INTEGER PRIMARY KEY,
  directory_id INTEGER NOT NULL REFERENCES directories(id),
  name TEXT NOT NULL,
  title TEXT,
  profile_url TEXT NOT NULL UNIQUE,
  processed INTEGER NOT NULL DEFAULT 0,
  outcome TEXT
);
CREATE INDEX idx_candidates_pending ON candidates(processed, directory_id);
