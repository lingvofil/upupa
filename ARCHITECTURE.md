# Структура проекта

```
upupa/
├── main.py            # минимальная точка входа: asyncio.run(main())
├── app/               # composition root и lifecycle приложения
│   ├── bootstrap.py   # сборка router'ов, startup и запуск polling
│   └── lifecycle.py   # владелец фоновых asyncio-задач
├── config.py          # legacy-фасад обратной совместимости
├── prompts/           # промпты и текстовые данные
├── core/              # shared settings/state/paths/storage/loader + compatibility exports
├── infrastructure/    # адаптеры внешних систем
│   ├── ai/            # Gemini/Groq/GigaChat/OpenAI-compatible providers и lazy resources
│   └── persistence/   # SQLite и другие durable-storage adapters
├── features/          # функциональные блоки бота, включая явный dialog pipeline
├── services/          # внешние сервисы и обработка медиа (поиск, погода, ytp, мемы)
├── games/             # игры (крокодил, егра)
├── AI/                # AI-функции и compatibility-фасады
│   └── dialog/        # settings/generation/commands/serious-mode/style диалога
└── tests/             # regression, architecture guardrails и smoke-тесты
```

## Направление зависимостей

- `main.py` делегирует запуск в `app/` и не содержит бизнес-логики.
- `app/` — composition root: ему разрешено знать про handlers/features/services/games/AI/core/infrastructure.
- `core/` не должен зависеть от `config.py`, `AI/`, `features/`, `services/`, `games/` или `handlers/`; это контролируется тестом архитектуры.
- Provider-реализации находятся в `infrastructure.ai` и не зависят от `AI/` или других прикладных слоёв.
- Durable-storage adapters находятся в `infrastructure.persistence`; feature-модули не должны содержать SQL.
- `core.ai_clients` временно остаётся compatibility-фасадом, но импортирует только `infrastructure.ai.clients`; обратная зависимость `core.ai_clients -> AI.*` устранена на R4.
- `AI.wrapper` и `AI.gigachat_client` — compatibility-фасады для старых import paths. Новые provider-зависимости должны идти через `infrastructure.ai`.
- `AI.talking` после R7 — compatibility-фасад для старых dialogue import paths; production pipeline и dialogue handlers должны импортировать `AI.dialog.*` напрямую.
- `config.py` — только compatibility-фасад; новые зависимости на него добавлять не следует.

## AI providers

- Gemini SDK adapter/fallback находится в `infrastructure.ai.gemini`.
- GigaChat conversation/fallback adapters находятся в `infrastructure.ai.gigachat`.
- Groq adapter находится в `infrastructure.ai.groq`.
- OpenRouter/SiliconFlow совместимый HTTP adapter находится в `infrastructure.ai.openai_compatible`.
- Настроенные объекты `model`, `groq_ai`, `gigachat_model`, `gemini_client` и legacy provider-объекты экспортируются через `infrastructure.ai.clients` как `LazyResource`.
- Импорт `core.ai_clients`/`config.py` больше не создаёт эти SDK-клиенты: реальный объект создаётся потокобезопасно при первом обращении и затем переиспользуется.
- Публичные имена `model`, `groq_ai`, `gigachat_model`, `gemini_client`, `openrouter_ai`, `siliconflow_ai` сохранены, поэтому миграция потребителей может идти постепенно.
- Provider wrappers пока синхронные. Async call-sites должны продолжать выносить их в `asyncio.to_thread`; переход на native async transport не входит в R4.

## Dialog pipeline

- Production catch-all handler делегирует реакции и прямой диалог в `features.dialog_pipeline`.
- Канонический порядок внутри pipeline: один проход random reactions/accounting, затем serious/direct dialogue.
- `handlers.dialog` больше не вызывает `AI.situational_summary.install_into_random_reactions` и не присваивает функции в `AI.talking` во время импорта.
- Живой буфер ситуативной реакции и защита от повторного `message_id` используются явно из `AI.situational_summary`; ситуативный текст генерируется через `generate_absurd_situational_reaction` без замены функций другого модуля.
- На R7 dialogue feature разделён на `AI.dialog.settings`, `AI.dialog.generation`, `AI.dialog.model_commands`, `AI.dialog.prompt_commands`, `AI.dialog.serious_mode` и `AI.dialog.style`.
- `features.dialog_pipeline` напрямую зависит от focused dialogue modules и больше не импортирует `AI.talking`.
- `handlers.ai_modes` и `handlers.ai_prompts` также используют `AI.dialog.*` напрямую; model/prompt/serious-mode команды больше не живут в god-module.
- `AI.talking` сохранён как compatibility-фасад для старых потребителей и legacy `process_general_message`; новые production-зависимости на него запрещены architecture guardrail'ами. Удаление фасада относится к R9.
- `AI.random_reactions.process_random_reactions` также пока сохранён для совместимости, но production handler его не компонует.
- Случайная реакция по-прежнему прерывает catch-all handler, а serious/direct dialogue не пропускает финальную запись `features.statistics.log_message`.
- Compatibility wrapper `AI.talking.handle_bot_conversation` передаёт тестируемые зависимости в focused generation module явно, вместо runtime mutation другого модуля.
- Architecture regression-тесты запрещают возвращать runtime monkeypatch в `handlers.dialog`, импортировать `AI.talking` из canonical pipeline/dialog handlers и вызывать legacy composed entrypoints из production pipeline.

## Lifecycle

Фоновые планировщики запускаются через `TaskSupervisor`, который хранит ссылки на задачи,
логирует необработанные исключения и отменяет оставшиеся задачи при завершении polling.
Создание глобальных `bot`/`dp` в `core.loader` пока сохранено для обратной совместимости:
многие существующие модули всё ещё импортируют `bot` через `config.py`.

Постоянное состояние, которое необходимо приложению на старте, загружается из
`UpupaApplication.initialize_state()`, а не как побочный эффект импорта feature-модуля.
На R6 это правило распространяется на chat settings/list, антиспам, SMS-disable,
message/rank counters и rank-notification settings; SQLite schema также инициализируется
явно из startup.

## Пути и persistence

- Канонические пути к рабочим данным определяются в `core.paths` через абсолютный `PROJECT_ROOT`.
- Физически JSON/DB/log-файлы пока остаются в корне репозитория; R6 не требует миграции production-данных и не меняет их форматы.
- `core.state` временно переэкспортирует старые `*_FILE` имена как строки для `config.py` и legacy-кода.
- Для JSON используется граница `JsonRepository` и файловая реализация `JsonFileRepository`.
- `JsonFileRepository` пишет через временный файл и атомарный `os.replace`, чтобы авария записи не оставляла частично перезаписанный JSON.
- `features.chat_settings`, `features.stat_rank_settings`, `features.sms_settings` и `features.content_filter` не используют `json.load/json.dump` для своего durable state; загрузка принимает repository и обновляет shared `dict/set/list` на месте, сохраняя identity.
- `message_stats.json`, `rank_notifications_settings.json`, `sms_disabled_chats.json` и `antispam_enabled.json` сохраняют прежний JSON-контракт.
- SQL для `statistics.db` сосредоточен в `infrastructure.persistence.sqlite_statistics.SQLiteStatisticsRepository`; `features.statistics` является facade/application API и не открывает SQLite-соединения самостоятельно.
- Схемы таблиц `message_stats` и `model_stats` на R6 не меняются.

## Async I/O

- Сетевые вызовы внутри `async def` не должны использовать синхронные HTTP-клиенты.
- На R3 `services.search` переведён с `requests` на `httpx.AsyncClient`.
- Синхронные SDK без async API (Google Custom Search и legacy `model.generate_content`) вызываются через `asyncio.to_thread`, чтобы не останавливать Telegram event loop.
- Найденные изображения и GIF передаются в aiogram через `BufferedInputFile` прямо из памяти; общие временные файлы для параллельных запросов не используются.
- CPU-bound обработка изображения в `handle_add_text_command` также вынесена в worker thread.
- Regression-тест запрещает возвращать известные блокирующие вызовы непосредственно в async-функции `services.search`.
- На R3.2 `AI.whatisthere` переведён на `httpx.AsyncClient` для URL/Telegram downloads; ответы по URL читаются потоково и ограничены 50 МБ.
- Синхронные Groq/GigaChat/Gemini/Robotics wrappers в `AI.whatisthere` offload'ятся через `asyncio.to_thread`.
- Медиа-пайплайн `чотам` больше не создаёт общие файлы `photo_<file_id>`, `video_<file_id>` и т. п.: скачанные байты передаются в анализ напрямую. `download_file()` сохранён как compatibility API для distortion.
- На R6 запись operational statistics в SQLite и async-чтение статистических отчётов offload'ятся через `asyncio.to_thread`; JSON-сохранение message/rank counters и SMS-disable из async handlers также не блокирует event loop.
- На R7 синхронный provider routing в `AI.dialog.generation` сохраняет прежний `asyncio.to_thread` boundary для Gemini/GigaChat/Groq/OpenRouter/SiliconFlow.

## Прочее

- Секреты: `config_private.py` (локально, в .gitignore) или env-переменные.
- Каждый рефакторинговый цикл оформляется отдельным PR и должен проходить CI.
