# Структура проекта

```
upupa/
├── main.py            # минимальная точка входа: asyncio.run(main())
├── app/               # composition root и lifecycle приложения
│   ├── bootstrap.py   # сборка router'ов, startup и запуск polling
│   └── lifecycle.py   # владелец фоновых asyncio-задач
├── config.py          # legacy-фасад обратной совместимости
├── prompts/           # промпты и текстовые данные
├── core/              # нижний инфраструктурный слой: settings/state/paths/storage/loader
├── features/          # функциональные блоки бота (настройки, статистика, фильтры)
├── services/          # внешние сервисы и обработка медиа (поиск, погода, ytp, мемы)
├── games/             # игры (крокодил, егра)
├── AI/                # AI-функции и текущие provider wrappers
└── tests/             # regression, architecture guardrails и smoke-тесты
```

## Направление зависимостей

- `main.py` делегирует запуск в `app/` и не содержит бизнес-логики.
- `app/` — composition root: ему разрешено знать про handlers/features/services/games/AI/core.
- `core/` не должен зависеть от `config.py`, `features/`, `services/`, `games/` или `handlers/`; это контролируется тестом архитектуры.
- `core.ai_clients -> AI.*` пока остаётся известным legacy-долгом и будет устранён на этапе разделения AI providers.
- `config.py` — только compatibility-фасад; новые зависимости на него добавлять не следует.

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
- Синхронные Groq/GigaChat/Gemini/Robotics wrappers в `AI.whatisthere` временно offload'ятся через
  `asyncio.to_thread`; их собственное разделение на provider adapters остаётся задачей R4.
- Медиа-пайплайн `чотам` больше не создаёт общие файлы `photo_<file_id>`, `video_<file_id>` и т. п.:
  скачанные байты передаются в анализ напрямую. `download_file()` сохранён как compatibility API для distortion.

## Прочее

- Секреты: `config_private.py` (локально, в .gitignore) или env-переменные.
- Каждый рефакторинговый цикл оформляется отдельным PR и должен проходить CI.
