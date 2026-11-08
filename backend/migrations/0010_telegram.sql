CREATE TABLE telegram_links (
    telegram_id INTEGER PRIMARY KEY CHECK (telegram_id > 0),
    user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
    selected_device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
    input_mode TEXT NOT NULL DEFAULT '' CHECK (input_mode IN ('', 'city')),
    linked_at INTEGER NOT NULL
);
CREATE TABLE telegram_link_codes (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
    code_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE telegram_preferences (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    codex INTEGER NOT NULL DEFAULT 1 CHECK (codex IN (0,1)),
    connection INTEGER NOT NULL DEFAULT 1 CHECK (connection IN (0,1)),
    pet INTEGER NOT NULL DEFAULT 1 CHECK (pet IN (0,1)),
    system INTEGER NOT NULL DEFAULT 1 CHECK (system IN (0,1))
);
CREATE TABLE telegram_throttle (
    bucket TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    attempts INTEGER NOT NULL
);
CREATE TABLE telegram_processed (
    update_id INTEGER PRIMARY KEY,
    processed_at INTEGER NOT NULL
);
CREATE TABLE telegram_worker_state (
    id INTEGER PRIMARY KEY CHECK (id=1),
    bot_id INTEGER,
    next_offset INTEGER NOT NULL DEFAULT 0
);
INSERT INTO telegram_worker_state(id) VALUES (1);
CREATE TABLE telegram_replies (
    update_id INTEGER PRIMARY KEY,
    telegram_id INTEGER NOT NULL,
    user_id TEXT REFERENCES users(id) ON DELETE RESTRICT,
    text TEXT NOT NULL,
    markup TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
