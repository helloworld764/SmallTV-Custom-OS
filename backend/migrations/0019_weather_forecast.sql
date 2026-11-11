CREATE TABLE weather_forecast_cache (
 cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at INTEGER NOT NULL
);
