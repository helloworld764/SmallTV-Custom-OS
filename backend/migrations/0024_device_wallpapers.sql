CREATE TABLE device_wallpapers (
 id TEXT PRIMARY KEY,
 device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
 label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 40),
 sha256 TEXT NOT NULL,
 image BLOB NOT NULL CHECK (length(image) BETWEEN 1 AND 262144),
 created_at INTEGER NOT NULL,
 UNIQUE(device_id,sha256)
);
CREATE INDEX idx_wallpapers_device ON device_wallpapers(device_id,created_at);
CREATE TABLE wallpaper_upload_limits (
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 window_start INTEGER NOT NULL, attempts INTEGER NOT NULL
);
