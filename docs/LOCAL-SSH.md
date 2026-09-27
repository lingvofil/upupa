# Диагностика production через локальный SSH

Проверено 27 сентября 2026: в рабочем окружении владельца доступен `ssh server`
без запроса пароля, в том числе из PowerShell. Alias задан в
`C:\Users\detector\.ssh\config`. Не выводить содержимое приватных ключей.

Репозиторий на сервере: `/root/upupa`. Systemd-сервис: `upupa_bot.service`.

Примеры диагностики:

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=10 server "cd /root/upupa && git log -3 --oneline"
ssh server "systemctl status upupa_bot.service --no-pager"
ssh server "cd /root/upupa && tail -100 bot_log.txt"
```

Журналы: `bot_log.txt` и ротации `bot_log.txt.1` и т. д.; сообщения игроков:
`user_messages.log`; состояние DnD: `dnd_sessions.json` и архив
`dnd_sessions_campaigns.json`. Для анализа выбирать нужный чат и период.
Уточнять часовой пояс источника: `campaign_started_at` записан с UTC offset,
а человек может назвать московское время.

Скачанные журналы содержат переписку: хранить их в `.local-audit-dnd/`, который
исключён из Git. Возможность SSH не означает автоматического разрешения на
перезапуск, редактирование production-состояния или ручной деплой.
