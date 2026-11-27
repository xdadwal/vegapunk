ALTER TABLE conversations ADD COLUMN finding_id INTEGER REFERENCES findings(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX conversations_finding ON conversations (finding_id) WHERE finding_id IS NOT NULL;
