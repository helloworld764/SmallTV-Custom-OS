# Weather widget and city preference

The account weather preference defaults to Kazan (`55.78874, 49.12214`, `Europe/Moscow`).
The owner can update it in the signed-in Control Panel. The value is stored per user in
SQLite, so a future Telegram bot can reuse the same preference after account linking.
Telegram controls do not exist until the Storm Bot is implemented.

The simulator requests current conditions only while previewing the clock screen. The
backend caches observations for 15 minutes by coordinates. On provider errors it returns
an observation up to 24 hours old with `stale: true`; otherwise it returns
`available: false` and does not invent conditions. The API requires a logged-in account.

- `GET /api/v1/preferences/weather` reads `{city, timezone}`.
- `PATCH /api/v1/preferences/weather` accepts `{city}` and requires the usual session
  cookie and `X-CSRF-Token`. A successful geocoding lookup must complete before the
  account preference is changed.
- `GET /api/v1/weather` returns current conditions and attribution.

The provider is [Open-Meteo](https://open-meteo.com/en/docs), with GeoNames used for geocoding through the [official geocoding API](https://open-meteo.com/en/docs/geocoding-api). Open-Meteo's free service
is limited to non-commercial use, has no uptime guarantee, and requires attribution.
See the [provider terms](https://open-meteo.com/en/terms) and [pricing/licence](https://open-meteo.com/en/pricing); review current provider terms before any commercial use. No API key or paid service is
used. Settings: `SMALLTV_WEATHER_CACHE_SECONDS` (300-1800, default 900) and
`SMALLTV_WEATHER_TIMEOUT_SECONDS` (default 4 seconds).

This feature is implemented for the authenticated browser simulator. It does not claim
firmware or physical-device compatibility; device-side delivery remains a later stage.
