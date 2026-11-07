CREATE TABLE device_enrollment_codes (
    id TEXT PRIMARY KEY NOT NULL,
    code_hash TEXT NOT NULL UNIQUE CHECK (length(code_hash) = 64),
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    device_name TEXT NOT NULL CHECK (length(trim(device_name)) BETWEEN 1 AND 80),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK (expires_at > created_at),
    consumed_at INTEGER
);
CREATE INDEX idx_device_enrollment_owner ON device_enrollment_codes(owner_id);
CREATE TABLE device_credentials (
    device_id TEXT PRIMARY KEY NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    created_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE TABLE device_enrollment_throttle (
    bucket_hash TEXT PRIMARY KEY NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts > 0),
    window_start INTEGER NOT NULL
);
