# Windows Codex Monitor для Storm OS

Будущий агент на основном Windows-ноутбуке ещё не реализован. Добавлена только
безопасная диагностика локального CLI; она не запускает задачи и не отправляет
heartbeat/события. Codex на Debian не переносится.

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
связи, но отправляющего их Windows-приложения ещё нет. Клиентские credentials
нельзя хранить в Git.

Серверный контракт событий с run_id/sequence, идемпотентностью и раздельным
состоянием связи: [codex-events.md](../docs/codex-events.md). Это собственный
протокол Storm OS, не проверенный интерфейс установленного Codex CLI.
