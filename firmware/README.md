# Storm OS — прошивка

Место для будущей прошивки. Драйвер платы и PlatformIO-конфигурация намеренно отсутствуют:
ревизия SmallTV, GPIO, Flash, дисплей и безопасное восстановление ещё не проверены.
Официальное название прошивки и надпись будущего экрана загрузки — Storm OS (кратко Storm).

Подготовлены без выбора платы: `protocol/raster.h` (переносимый C++17 валидатор),
`assets/catalog-v1/` (27кадров и bitmap-кириллица), `tools/device_probe.py`
(программное чтение device API без heartbeat и изменения данных).
Инструкции: [протокол](../docs/device-display-protocol.md),
[ресурсы и бюджет](../docs/device-assets.md).
Native g++-тест не подтверждает сборку под ESP8266 или работоспособность LCD.

JSON-схемы и host-side проверка ответов: [protocol/README.md](protocol/README.md).
Исследование upstream/лицензии и границы совместимости:
[upstream-firmware-audit.md](../docs/upstream-firmware-audit.md).
Порядок допуска к первой сборке и записи после доставки:
[first-device-check.md](../docs/first-device-check.md).
