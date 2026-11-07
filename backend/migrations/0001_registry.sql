CREATE TABLE users (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(id)) > 0),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 80),
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'super_admin')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE devices (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(id)) > 0),
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_devices_owner_id ON devices(owner_id);

CREATE TABLE device_settings (
    device_id TEXT PRIMARY KEY NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    brightness INTEGER NOT NULL DEFAULT 70
        CHECK (typeof(brightness) = 'integer' AND brightness BETWEEN 0 AND 100),
    theme TEXT NOT NULL DEFAULT 'BlackArch Red' CHECK (length(trim(theme)) BETWEEN 1 AND 80),
    start_screen TEXT NOT NULL DEFAULT 'clock'
        CHECK (start_screen IN ('clock', 'weather', 'monitor', 'codex', 'null', 'tamagotchi'))
);
