CREATE TABLE device_settings_new (
 device_id TEXT PRIMARY KEY NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
 brightness INTEGER NOT NULL DEFAULT 70 CHECK (typeof(brightness)='integer' AND brightness BETWEEN 0 AND 100),
 theme TEXT NOT NULL DEFAULT 'BlackArch Red' CHECK (length(trim(theme)) BETWEEN 1 AND 80),
 start_screen TEXT NOT NULL DEFAULT 'clock' CHECK (start_screen IN ('clock','weather','monitor','codex','null','tamagotchi','markets')),
 clock_format TEXT NOT NULL DEFAULT '24h' CHECK (clock_format IN ('12h','24h')),
 clock_seconds INTEGER NOT NULL DEFAULT 1 CHECK (clock_seconds IN (0,1)),
 clock_style TEXT NOT NULL DEFAULT 'classic' CHECK (clock_style IN ('classic','minimal','terminal')),
 device_style TEXT NOT NULL DEFAULT 'classic' CHECK (device_style IN ('classic','minimal','terminal','orbit')),
 clock_layout TEXT NOT NULL DEFAULT 'regular' CHECK (clock_layout IN ('regular','tall','stacked','analog'))
);
INSERT INTO device_settings_new SELECT * FROM device_settings;
DROP TABLE device_settings;
ALTER TABLE device_settings_new RENAME TO device_settings;
