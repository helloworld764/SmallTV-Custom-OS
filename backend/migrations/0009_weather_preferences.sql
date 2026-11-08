CREATE TABLE user_preferences (
    user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    weather_city TEXT NOT NULL CHECK (length(trim(weather_city)) BETWEEN 1 AND 80),
    weather_latitude REAL NOT NULL CHECK (weather_latitude BETWEEN -90 AND 90),
    weather_longitude REAL NOT NULL CHECK (weather_longitude BETWEEN -180 AND 180),
    weather_timezone TEXT NOT NULL CHECK (length(trim(weather_timezone)) BETWEEN 1 AND 80),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE weather_cache (
    cache_key TEXT PRIMARY KEY NOT NULL,
    city TEXT NOT NULL,
    temperature REAL NOT NULL,
    weather_code INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    fetched_at INTEGER NOT NULL CHECK (fetched_at >= 0)
);
