ALTER TABLE device_settings ADD COLUMN device_style TEXT NOT NULL DEFAULT 'classic' CHECK (device_style IN ('classic','minimal','terminal','orbit'));
ALTER TABLE device_settings ADD COLUMN clock_layout TEXT NOT NULL DEFAULT 'regular' CHECK (clock_layout IN ('regular','tall','stacked','analog'));
UPDATE device_settings SET device_style=clock_style;
UPDATE device_settings SET start_screen='tamagotchi' WHERE start_screen='null';
CREATE TABLE telegram_device_intents_new (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
 field TEXT NOT NULL CHECK (field IN ('menu','brightness','theme','start_screen','clock_format','clock_seconds','clock_style','device_style','clock_layout')),
 value TEXT NOT NULL, expected_value TEXT, expires_at INTEGER NOT NULL,
 consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0,1))
);
INSERT INTO telegram_device_intents_new SELECT * FROM telegram_device_intents ORDER BY rowid;
DROP TABLE telegram_device_intents;
ALTER TABLE telegram_device_intents_new RENAME TO telegram_device_intents;
