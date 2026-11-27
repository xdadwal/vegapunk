CREATE TABLE findings (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL,
    subject TEXT NOT NULL,
    title TEXT NOT NULL,
    diagnosis TEXT NOT NULL,
    suggested_fix TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'reviewed')),
    trigger TEXT NOT NULL CHECK (trigger IN ('scheduled', 'watch')),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    reviewed_at TEXT
);

CREATE INDEX findings_status_seen ON findings (status, last_seen_at);
CREATE UNIQUE INDEX findings_open_key ON findings (key) WHERE status = 'open';

CREATE TABLE finding_evidence (
    finding_id INTEGER NOT NULL REFERENCES findings (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('log', 'metric')),
    text TEXT NOT NULL,
    at TEXT,
    PRIMARY KEY (finding_id, position)
);

CREATE TABLE check_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1,
    hour INTEGER NOT NULL DEFAULT 2 CHECK (hour BETWEEN 0 AND 23),
    last_run_at TEXT,
    last_error TEXT
);

INSERT INTO check_settings (id, enabled, hour) VALUES (1, 1, 2);

CREATE TABLE investigations (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('finding', 'nothing', 'discarded', 'failed')),
    error TEXT,
    marker TEXT
);

CREATE INDEX investigations_key_started ON investigations (key, started_at);

ALTER TABLE containers ADD COLUMN oom_killed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE containers ADD COLUMN exit_marker TEXT;

CREATE TABLE container_restarts (
    container_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    restart_count INTEGER NOT NULL,
    previous_count INTEGER NOT NULL,
    PRIMARY KEY (container_id, ts)
) WITHOUT ROWID;
