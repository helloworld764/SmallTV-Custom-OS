CREATE TABLE device_presence (
    device_id TEXT PRIMARY KEY NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    last_seen_at INTEGER NOT NULL CHECK (last_seen_at >= 0)
);
