ALTER TABLE device_settings ADD COLUMN advanced TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(advanced));
ALTER TABLE device_settings ADD COLUMN screen_changed_at INTEGER NOT NULL DEFAULT 0;
