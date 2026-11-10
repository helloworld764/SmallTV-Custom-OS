ALTER TABLE device_settings ADD COLUMN clock_format TEXT NOT NULL DEFAULT '24h' CHECK (clock_format IN ('12h','24h'));
ALTER TABLE device_settings ADD COLUMN clock_seconds INTEGER NOT NULL DEFAULT 1 CHECK (clock_seconds IN (0,1));
ALTER TABLE device_settings ADD COLUMN clock_style TEXT NOT NULL DEFAULT 'classic' CHECK (clock_style IN ('classic','minimal','terminal'));

-- Extend the allowed fields while preserving pending/consumed device buttons.
CREATE TABLE telegram_device_intents_new (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    field TEXT NOT NULL CHECK (field IN ('menu','brightness','theme','start_screen','clock_format','clock_seconds','clock_style')),
    value TEXT NOT NULL,
    expected_value TEXT,
    expires_at INTEGER NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0,1))
);
INSERT INTO telegram_device_intents_new SELECT * FROM telegram_device_intents ORDER BY rowid;
DROP TABLE telegram_device_intents;
ALTER TABLE telegram_device_intents_new RENAME TO telegram_device_intents;
