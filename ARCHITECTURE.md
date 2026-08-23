# Структура проекта

```
upupa/
├── main.py            # минимальная точка входа: asyncio.run(main())
├── app/               # composition root и lifecycle приложения
│   ├── bootstrap.py   # сборка router'ов, startup и запуск polling
│   └── lifecycle.py   # владелец фоновых asyncio-задач
├── config.py          # legacy-фасад обратной совместимости
├── prompts/           # промпты и текстовые данные
├── core/              # нижний инфраструктурный слой: settings/state/loader/utils
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

## Прочее

- Файлы данных (json/txt/ogg) пока читаются относительно рабочей директории (корень репы).
- Секреты: `config_private.py` (локально, в .gitignore) или env-переменные.
- Каждый рефакторинговый цикл оформляется отдельным PR и должен проходить CI.
