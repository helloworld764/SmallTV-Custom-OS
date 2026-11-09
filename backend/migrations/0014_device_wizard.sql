ALTER TABLE device_enrollment_codes ADD COLUMN device_id TEXT REFERENCES devices(id) ON DELETE SET NULL;

-- Explicitly requested previews have no device credential or heartbeat.
CREATE TABLE device_previews (
    device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES users(id),
    created_at INTEGER NOT NULL,
    UNIQUE (owner_id, request_id)
);
CREATE INDEX device_previews_owner_time ON device_previews(owner_id, created_at);
