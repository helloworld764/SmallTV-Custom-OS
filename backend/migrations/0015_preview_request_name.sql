-- Preserve the original idempotency payload independently of later device renames.
ALTER TABLE device_previews ADD COLUMN request_name TEXT;
UPDATE device_previews SET request_name = (SELECT name FROM devices WHERE devices.id = device_previews.device_id);
