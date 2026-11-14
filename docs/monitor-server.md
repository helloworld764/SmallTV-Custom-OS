# Windows Monitor → HTTPS backend

Monitor остаётся на Windows, использует прежние agent_id/события/outbox/DPAPI.
Добавлен удалённый HTTPS origin. HTTP разрешён только буквальному loopback
с явным портом; удалённый plaintext, userinfo, path/query/fragment запрещены.
Проверяются доверенная CA, hostname и срок сертификата через стандартный
ssl.create_default_context. Redirect и прокси окружения отключены. Есть timeout
и ограничения ответов. Для частной CA допустим отдельный доверенный PEM-файл;
режима insecure нет. Неверный/самоподписанный/чужой сертификат блокирует отправку.

## Переключение без потери очереди

1. Дождаться завершения текущей задачи, выполнить старый `flush`, затем `status`.
   Сохранить старый DPAPI-файл/outbox; не удалять и не редактировать SQLite очереди.
2. Если pending>0 и старый сервер недоступен, не переадресовывать очередь
   автоматически. Она сохраняется для восстановления старого endpoint;
   новый сервер получает отдельную очередь. Не доставлять старые события под
   новой идентичностью. Outbox проверяет привязку agent_id И base_url.
3. После готовности HTTPS сервера войти в него и выдать новый agent key.
   После restore БД все прежние ключи отозваны. Ключ вводить только локально скрыто.
4. Новые файлы, например (заменить домен/UUID своим):

```powershell
.\.venv\Scripts\python.exe codex-monitor/local_monitor.py --key-file secrets/storm-server.dpapi init-key --agent-id AGENT_UUID --url https://storm.example
.\.venv\Scripts\python.exe codex-monitor/local_monitor.py --key-file secrets/storm-server.dpapi --outbox data/codex-monitor/server.sqlite3 flush
```

Для собственной CA добавить `init-key --ca-file C:\trusted\storm-ca.pem`.
Файл сертификата публичный, но его подлинность проверить отдельно; доверять
произвольной CA из сообщения нельзя. Путь сохранён внутри DPAPI; существующие
файлы прежней версии1 читаются как раньше. Init-key не перезаписывает файл.

5. `flush` не запускает Codex, проверяет identity и heartbeat. Для настоящей
   задачи использовать те же `--key-file`/`--outbox` перед `run-task`; задача
   запускается только явно. Delivered не означает успешность самой задачи —
   смотреть exec отдельно. При остановке Monitor сервер со временем STALE/OFFLINE.

Из текущего окружения реальный сервер не подключался: адрес/сертификат ещё
не настроены отдельно. Автотест создаёт временную CA/серверный сертификат,
поднимает настоящий TLS-сервер и проверяет успех, неизвестную CA, неверный
hostname и redirect. Никаких настоящих agent tokens/модельных задач в тесте.
