# Windows Codex Monitor для Storm OS

Добавлены диагностика CLI, парсер и ограниченный локальный мост smoke → outbox →
HTTP с heartbeat и Windows DPAPI для ключа. Есть `run-task` для новых явно
запускаемых read-only задач; постоянного Monitor всех/интерактивных сессий нет.
Codex на Debian не переносится. [Инструкция run-task](../docs/task-runner.md).
Транспорт поддерживает проверяемый удалённый HTTPS и отдельную очередь для
нового endpoint: [monitor-server.md](../docs/monitor-server.md).
Настоящий Debian endpoint этим этапом не подключался.

```powershell
.\.venv\Scripts\python.exe codex-monitor/local_monitor.py run-task --cwd . --show-answer
```
Инструкция: [local-monitor.md](../docs/local-monitor.md).

Сначала проверить хранилище без реального ключа:

```powershell
.\.venv\Scripts\python.exe codex-monitor/local_monitor.py check-key-store
```

Из корня проекта в PowerShell:

```powershell
.\.venv\Scripts\python.exe codex-monitor/check_cli.py
```

JSON-отчёт содержит распознанную версию, признаки флагов и статус проверки,
без сырых stdout/stderr и путей установки. `manual_verification_required` не
подтверждает реальную совместимость: `real_run_checked` всегда false.
Результаты исследования и план адаптера:
[codex-cli-research.md](../docs/codex-cli-research.md).

Серверная регистрация и отдельные ключи будущих агентов реализованы в backend:
[протокол идентичностей](../docs/agent-identities.md). Это не агент на Windows,
не отслеживание задач Codex. Сервер уже принимает heartbeat и вычисляет свежесть
связи; мост отправляет их при явном run-smoke/run-task/flush. Клиентские credentials
нельзя хранить в Git.

Серверный контракт событий с run_id/sequence, идемпотентностью и раздельным
состоянием связи: [codex-events.md](../docs/codex-events.md). Это собственный
протокол Storm OS, а не API самого Codex CLI. Адаптер проверен с CLI 0.167.0.

`exec_events.py` преобразует синтетически проверенный профиль JSONL в START и
конечное событие только при согласованном завершении процесса. Повреждённый
поток/EOF не создают DONE. Это библиотека без запуска процесса или транспорта:
[exec-parser.md](../docs/exec-parser.md). Владелец подтвердил один штатный поток CLI 0.167.0.

Для одного контролируемого реального запуска после диагностики:

```powershell
.\.venv\Scripts\python.exe codex-monitor/smoke_exec.py --run
```

Это одна настоящая задача Codex в отдельной системной временной папке и read-only,
а не запуск Monitor. Потоки не сохраняются, в отчёте только обезличенные признаки:
[exec-smoke.md](../docs/exec-smoke.md). Без --run задача не запускается.
