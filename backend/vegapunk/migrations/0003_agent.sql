CREATE TABLE ai_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  base_url TEXT NOT NULL,
  model TEXT NOT NULL,
  instructions TEXT NOT NULL,
  api_key_encrypted TEXT,
  key_hint TEXT,
  key_rejected_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX conversations_updated ON conversations (updated_at);
