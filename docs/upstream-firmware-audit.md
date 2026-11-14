# Аудит исходного firmware-проекта

Дата проверки: 14 ноября 2026. Репозиторий `iodn/geekmagic-tv-esp8266`,
ветка master, изученный commit `6113fb9c86e2e2024883253fd99a3d2550ffccb2`
от14 октября2026. Источники и SHA256: [upstream-sources.json](upstream-sources.json).
Сторонний firmware-код, бинарники и конфигурация платы в Storm OS не переносились;
PlatformIO/esptool не устанавливались, чужие audit-скрипты не запускались.

## Лицензия

В [LICENSE проверенного коммита](https://github.com/iodn/geekmagic-tv-esp8266/blob/6113fb9c86e2e2024883253fd99a3d2550ffccb2/LICENSE)
указана MIT, copyright2026 B. van Weerd. Если позднее копировать существенные
части исходников, вместе с ними сохраняются copyright и текст разрешения.
Это не выбор лицензии всего Storm OS. Для реально выбранных embedded-библиотек
нужны отдельные версии и notices; выбор остаётся после идентификации платы.
Уже включённый шрифт Storm OS имеет свою SIL OFL, сохранённую в assets/fonts.

## Что подтверждено чтением исходного проекта

[platformio.ini](https://github.com/iodn/geekmagic-tv-esp8266/blob/6113fb9c86e2e2024883253fd99a3d2550ffccb2/platformio.ini):

| Параметр upstream | Значение, не настройка платы Storm OS |
|---|---|
| Платформа/board | espressif8266@4.2.1, nodemcuv2, Arduino |
| Дисплей | ST7789,240×240 |
| Выводы дисплея | MOSI13, SCLK14, DC0, RST2, BL5, CS−1 |
| Файловая система | LittleFS |
| SPI |40MHz, чтение20MHz |
| Зависимости | TFT_eSPI^2.5.43, WiFiManager^2.0.17, ArduinoJson^7.0.0 |

Версии библиотек заданы диапазонами: этот файл сам по себе не обеспечивает
воспроизводимый набор transitive dependencies. Не копировать flags в проект
до проверки платы; совпадение названия SmallTV недостаточно.

В [README](https://github.com/iodn/geekmagic-tv-esp8266/blob/6113fb9c86e2e2024883253fd99a3d2550ffccb2/README.md)
заявлена проверка одной Ultra-ревизии ESP8266; Pro/ESP32 не является целью.
Есть локальное хранение, настройки Wi-Fi, ранний recovery, OTA для уже установленной
этой прошивки. Описанный автором первый переход с заводской версии — проводной.
Recovery от повторных ранних сбоев и factory reset — разные процедуры:
factory reset стирает настройки и не заменяет восстановление заводского образа.

Upstream предлагает insecure/self-signed или fingerprint HTTPS для feeds.
Это не переносится автоматически: Storm требует проверяемый защищённый канал
и не должен отправлять device/agent key после неуспешной проверки сервера.
Протокол feeds/dashboard upstream не совместим с нашим `/api/v1/devices/...`.

## Что известно только из отчётов автора

[hardware-verification.txt](https://github.com/iodn/geekmagic-tv-esp8266/blob/6113fb9c86e2e2024883253fd99a3d2550ffccb2/audit/hardware-verification.txt)
описывает экземпляр ESP8266EX с4MiB Flash и отдельную заводскую копию.
В одном из последних описанных образов статическая RAM50752/81920байт,
application Flash736827/1044464байт; после старта free DRAM около18.5KiB.
Описаны успехи HTTP/HTTPS и короткие наблюдения без reset, а также ошибки и
исправления clone/cancellation/TLS heap. Это чужие измерения, не бюджет Storm OS.
Даже одна RGB565-картинка240×240 требует115200байт — нельзя планировать полный
framebuffer без измерений. Подготовленный STP1 допускает построчную обработку,
но TLS, JSON, стек и драйвер дополнительно занимают память.

Автор отдельно оставляет непроверенными часть HTTP/mixed-load сценариев и
физическую оценку экрана. Эти отчёты не закрывают72-часовой тест Storm OS.

## Результат для Storm OS

Upstream пригоден как материал исследования. Сейчас нет доказательства,
что его образ, распиновка, USB auto-reset или OTA подходят нашему устройству.
До доставки не создаём PlatformIO target, не собираем «готовую прошивку»
по предположенной плате и не выдаём инструкции стирания Flash как универсальные.
Порядок проверки и критерии допуска: [first-device-check.md](first-device-check.md).
