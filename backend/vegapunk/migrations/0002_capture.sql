CREATE TABLE host (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  hostname TEXT,
  os TEXT,
  kernel TEXT,
  docker_version TEXT,
  cpu_count INTEGER,
  mem_total INTEGER,
  boot_time TEXT
);

-- res 0 is a raw sample; res 300 is a 5-minute average. ts is Unix seconds.
CREATE TABLE host_metrics (
  res INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  cpu_pct REAL,
  mem_used INTEGER,
  mem_total INTEGER,
  disk_used INTEGER,
  disk_total INTEGER,
  PRIMARY KEY (res, ts)
) WITHOUT ROWID;

CREATE TABLE containers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  image TEXT NOT NULL,
  state TEXT NOT NULL,
  health TEXT,
  restart_count INTEGER NOT NULL,
  started_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  removed_at TEXT
);
CREATE INDEX containers_name ON containers (name);

CREATE TABLE container_metrics (
  container_id TEXT NOT NULL,
  res INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  cpu_pct REAL,
  mem_used INTEGER,
  mem_limit INTEGER,
  PRIMARY KEY (container_id, res, ts)
) WITHOUT ROWID;

CREATE TABLE capture_status (
  source TEXT PRIMARY KEY,
  last_attempt_at TEXT NOT NULL,
  last_success_at TEXT,
  last_error TEXT
);
