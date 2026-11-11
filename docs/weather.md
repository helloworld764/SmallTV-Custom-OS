# Weather widget and city preference

The account weather preference defaults to Kazan (`55.78874, 49.12214`, `Europe/Moscow`).
The owner can update it in the signed-in Control Panel. The value is stored per user in
SQLite, so a future Telegram bot can reuse the same preference after account linking.
The linked Storm Bot uses the same account city preference.

The clock widget requests current conditions while previewing the clock screen. The
backend caches observations for 15 minutes by coordinates. On provider errors it returns
an observation up to 24 hours old with `stale: true`; otherwise it returns
`available: false` and does not invent conditions. The API requires a logged-in account.

- `GET /api/v1/preferences/weather` reads `{city, timezone}`.
- `PATCH /api/v1/preferences/weather` accepts `{city}` and requires the usual session
  cookie and `X-CSRF-Token`. A successful geocoding lookup must complete before the
  account preference is changed.
- `GET /api/v1/weather` returns current conditions and attribution.
- `GET /api/v1/weather/forecast` returns today's current values and daily range,
  plus tomorrow's daily summary. It uses the same session and city preference.

The provider is [Open-Meteo](https://open-meteo.com/en/docs), with GeoNames used for geocoding through the [official geocoding API](https://open-meteo.com/en/docs/geocoding-api). Open-Meteo's free service
is limited to non-commercial use, has no uptime guarantee, and requires attribution.
See the [provider terms](https://open-meteo.com/en/terms) and [pricing/licence](https://open-meteo.com/en/pricing); review current provider terms before any commercial use. No API key or paid service is
used. Settings: `SMALLTV_WEATHER_CACHE_SECONDS` (300-1800, default 900) and
`SMALLTV_WEATHER_TIMEOUT_SECONDS` (default 4 seconds).

This feature is implemented for the authenticated browser simulator. It does not claim
firmware or physical-device compatibility; device-side delivery remains a later stage.

## Подробный экран погоды (11 ноября 2026)

В настройках устройства выберите стартовый экран «Погода». Экран 240×240 содержит:

- город и отметку «Сегодня»;
- оригинальный контурный погодный SVG и крупную текущую температуру;
- текущие ощущаемую температуру и скорость ветра в м/с, дневной минимум/максимум;
- восход/закат по времени выбранного города; «—» при отсутствии события;
- одну текстовую сводку на завтра: состояние, минимум/максимум, диапазон
  ощущаемой температуры и максимальная скорость ветра с пометкой «до»;
- время текущих данных источника, атрибуцию и признак устаревания.

Погода адаптирована к четырём стилям устройства и четырём палитрам. Другие дни
не запрашиваются и не выводятся. Город задаётся в прежних настройках панели/бота.
Это данные погодной модели Open-Meteo, а не измерения датчиков SmallTV.

API прогноза использует `forecast_days=2`, `wind_speed_unit=ms`, текущие
`temperature_2m,apparent_temperature,weather_code,wind_speed_10m` и дневные
`weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,
apparent_temperature_min,sunrise,sunset,wind_speed_10m_max` согласно
[документации Open-Meteo](https://open-meteo.com/en/docs).

Миграция 0019 создаёт отдельную таблицу `weather_forecast_cache`; существующий
кэш виджета, настройки города и прежний `/weather` сохраняются. Ключ включает
координаты и часовой пояс. TTL/timeout используют существующие параметры погоды.
При отказе источника допускается кэш не старше 24 часов с `stale:true`; более
старые значения недоступны. Интерфейс сверяет дату прогноза с сегодняшней датой
в часовом поясе города и скрывает вчерашний прогноз. На первом запуске или при
ошибке без кэша выводится «Погода временно недоступна». Восход/закат допускают
null для полярных условий. Некорректные диапазоны, нечисловые значения, NaN,
отрицательный ветер и неверные даты не сохраняются.

Симулятор запрашивает прогноз только на экране погоды, не чаще раза в минуту,
с отдельным тайм-аутом и защитой от параллельных запросов. Работа часов,
индикатора API и временных уведомлений не блокируется запросом прогноза.
