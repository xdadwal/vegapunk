ALTER TABLE check_settings ADD COLUMN watch_enabled INTEGER NOT NULL DEFAULT 1 CHECK (watch_enabled IN (0, 1));
