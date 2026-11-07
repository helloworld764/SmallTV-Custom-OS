# Проверки первого этапа

Дата: 7 ноября 2026 г. Среда проверки: Windows, встроенный Python 3.12.14,
Node.js 24.12.0, Git 2.53.0.

## Расположение

Исходная папка `<SOURCE_ROOT>` содержит
только TZ.md и AGENTS.md, Git там отсутствует. Она доступна этой сессии только
для чтения и не изменялась.

Рабочая копия подготовлена в
`<PROJECT_ROOT>`.
В ней инициализирован Git, ветка main, без commit/push и без remote.
TZ.md и AGENTS.md скопированы побайтово; SHA-256 исходников и копий совпадают.

## Результаты

| Проверка | Результат |
|---|---|
| `node --test tests/simulator.test.mjs` | 6 тестов пройдено |
| `node --check web/simulator/simulator.mjs` | Пройдено |
| `node --check web/simulator/connection.mjs` | Пройдено |
| `.venv\Scripts\python.exe -m compileall -q backend tests` | Пройдено, только синтаксис |
| `.venv\Scripts\python.exe -m pytest` | Не выполнено: No module named pytest |
| `.venv\Scripts\python.exe -m backend.app` | Не запущен: No module named uvicorn |
| `git check-ignore` для .env, .venv, pyc, БД, backups, uploads | Исключены |
| `.env.example` | Не исключён из Git |

Создано локальное виртуальное окружение. Установка из requirements-dev.txt,
в том числе с явным индексом PyPI и isolated-режимом pip, завершилась ошибкой
`No matching distribution found for fastapi<1.0,>=0.115`.
Python-зависимости не установлены; HTTP-работоспособность backend, Python-тесты
и внешний вид в настоящем браузере в этой среде не подтверждены.
Зелёный результат JavaScript-тестов проверяет логику, а не запуск FastAPI.

## Как завершить проверку

В среде с доступными Python-зависимостями выполнить команды из README:
установить requirements-dev.txt, запустить pytest и backend, открыть /health
и /simulator/, вручную проверить остановку и восстановление связи.
Debian, ESP8266, OTA и физическое устройство не проверялись и не изменялись.
