ALTER TABLE device_settings ADD COLUMN current_screen TEXT DEFAULT NULL
 CHECK (current_screen IS NULL OR current_screen IN ('clock','weather','monitor','codex','tamagotchi','markets'));
CREATE TABLE device_control_requests (
 actor_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 request_id TEXT NOT NULL,
 device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
 fingerprint TEXT NOT NULL, result TEXT NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY (actor_id,request_id)
);
