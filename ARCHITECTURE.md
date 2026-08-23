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
│   └── ai/            # Gemini/Groq/GigaChat/OpenAI-compatible providers и lazy resources
├── features/          # функциональные блоки бота (настройки, статистика, фильтры)
├── services/          # внешние сервисы и обработка медиа (поиск, погода, ytp, мемы)
├── games/             # игры (крокодил, егра)
├── AI/                # AI-функции; старые provider import paths оставлены фасадами
└── tests/             # regression, architecture guardrails и smoke-тесты
```

## Направление зависимостей

- `main.py` делегирует запуск в `app/` и не содержит бизнес-логики.
- `app/` — composition root: ему разрешено знать про handlers/features/services/games/AI/core/infrastructure.
- `core/` не должен зависеть от `config.py`, `AI/`, `features/`, `services/`, `games/` или `handlers/`; это контролируется тестом архитектуры.
- Provider-реализации находятся в `infrastructure.ai` и не зависят от `AI/` или других прикладных слоёв.
- `core.ai_clients` временно остаётся compatibility-фасадом, но импортирует только `infrastructure.ai.clients`; обратная зависимость `core.ai_clients -> AI.*` устранена на R4.
- `AI.wrapper` и `AI.gigachat_client` — compatibility-фасады для старых import paths. Новые provider-зависимости должны идти через `infrastructure.ai`.
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

## Lifecycle

Фоновые планировщики запускаются через `TaskSupervisor`, который хранит ссылки на задачи,
логирует необработанные исключения и отменяет оставшиеся задачи при завершении polling.
Создание глобальных `bot`/`dp` в `core.loader` пока сохранено для обратной совместимости:
многие существующие модули всё ещё импортируют `bot` через `config.py`.

Постоянное состояние, которое необходимо приложению на старте, должно загружаться из
`UpupaApplication.initialize_state()`, а не как побочный эффект импорта feature-модуля.
На R2 это правило применено к `features.chat_settings`; остальные legacy import-time loaders
будут выноситься поэтапно.

## Пути и persistence

- Канонические пути к рабочим данным определяются в `core.paths` через абсолютный `PROJECT_ROOT`.
- Физически json/db/log-файлы пока остаются в корне репозитория: R2 не требует миграции данных.
- `core.state` временно переэкспортирует старые `*_FILE` имена как строки для `config.py` и legacy-кода.
- Для JSON добавлена граница `JsonRepository` и файловая реализация `JsonFileRepository`.
- `JsonFileRepository` пишет через временный файл и атомарный `os.replace`, чтобы авария записи
  не оставляла частично перезаписанный JSON.
- `features.chat_settings` больше не открывает JSON напрямую и сохраняет identity общих
  `chat_settings`/`chat_list`, на которые уже ссылаются другие модули.

## Async I/O

- Сетевые вызовы внутри `async def` не должны использовать синхронные HTTP-клиенты.
- На R3 `services.search` переведён с `requests` на `httpx.AsyncClient`.
- Синхронные SDK без async API (Google Custom Search и legacy `model.generate_content`) вызываются
  через `asyncio.to_thread`, чтобы не останавливать Telegram event loop.
- Найденные изображения и GIF передаются в aiogram через `BufferedInputFile` прямо из памяти;
  общие временные файлы для параллельных запросов не используются.
- CPU-bound обработка изображения в `handle_add_text_command` также вынесена в worker thread.
- Regression-тест запрещает возвращать известные блокирующие вызовы непосредственно в async-функции `services.search`.
- На R3.2 `AI.whatisthere` переведён на `httpx.AsyncClient` для URL/Telegram downloads; ответы по URL
  читаются потоково и ограничены 50 МБ.
- Синхронные Groq/GigaChat/Gemini/Robotics wrappers в `AI.whatisthere` offload'ятся через
  `asyncio.to_thread`.
- Медиа-пайплайн `чотам` больше не создаёт общие файлы `photo_<file_id>`, `video_<file_id>` и т. п.:
  скачанные байты передаются в анализ напрямую. `download_file()` сохранён как compatibility API для distortion.

## Прочее

- Секреты: `config_private.py` (локально, в .gitignore) или env-переменные.
- Каждый рефакторинговый цикл оформляется отдельным PR и должен проходить CI.
