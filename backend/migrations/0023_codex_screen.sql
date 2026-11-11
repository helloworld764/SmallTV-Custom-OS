-- Preserve custom hidden-screen lists; append the new screen only to lists with all four previous screens.
UPDATE device_settings
 SET advanced=json_set(advanced,'$.screens[#]','codex'),screen_changed_at=CAST(strftime('%s','now') AS INTEGER)
 WHERE json_type(advanced,'$.screens')='array' AND json_array_length(advanced,'$.screens')=4
 AND NOT EXISTS (SELECT 1 FROM json_each(advanced,'$.screens') WHERE value='codex');
