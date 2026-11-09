ALTER TABLE telegram_replies ADD COLUMN need_fields TEXT;
CREATE TABLE telegram_notice_sequence (
    id INTEGER PRIMARY KEY CHECK (id=1),
    next_id INTEGER NOT NULL CHECK (next_id>0)
);
INSERT INTO telegram_notice_sequence
SELECT 1,max(coalesce((SELECT max(id) FROM pet_sleep_sessions),0),
             coalesce((SELECT -min(update_id) FROM telegram_replies WHERE update_id<0),0))+1;
CREATE TABLE pet_need_tracking (
    pet_id TEXT PRIMARY KEY REFERENCES pets(id) ON DELETE RESTRICT,
    round INTEGER NOT NULL,
    last_scan INTEGER NOT NULL,
    satiety INTEGER NOT NULL CHECK (satiety IN (0,1)),
    energy INTEGER NOT NULL CHECK (energy IN (0,1)),
    mood INTEGER NOT NULL CHECK (mood IN (0,1)),
    last_notice_at INTEGER
);
CREATE TABLE telegram_device_intents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    field TEXT NOT NULL CHECK (field IN ('menu','brightness','theme','start_screen')),
    value TEXT NOT NULL,
    expected_value TEXT,
    expires_at INTEGER NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0,1))
);
CREATE TABLE telegram_device_drafts (
    telegram_id INTEGER PRIMARY KEY REFERENCES telegram_links(telegram_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL,
    expected_brightness INTEGER NOT NULL
);
