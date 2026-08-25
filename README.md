# Упупа

[![Tests](https://github.com/lingvofil/upupa/actions/workflows/tests.yml/badge.svg)](https://github.com/lingvofil/upupa/actions/workflows/tests.yml)
[![Deploy](https://github.com/lingvofil/upupa/actions/workflows/deploy.yml/badge.svg)](https://github.com/lingvofil/upupa/actions/workflows/deploy.yml)

Telegram-бот на **aiogram 3**, который когда-то был просто большим комком функций, а теперь является большим комком функций с архитектурными границами.

Упупа разговаривает через несколько LLM-провайдеров, генерирует и разбирает картинки, слушает голосовые, ковыряет видео и стикеры, играет в игры, считает статистику, ищет всякое в интернете, следит за настройками чатов и отдельно ведёт собственный Telegram-канал. Иногда даже намеренно пишет хуйню. Это feature.

## Что здесь вообще есть

- **AI-диалог** с персонами, историей, serious mode и переключением моделей.
- **Gemini, Groq, GigaChat, OpenRouter и SiliconFlow** с fallback-очередями и lazy initialization клиентов.
- **Картинки**: генерация, `перерисуй`, `магшот`, мемы, анализ изображений и fallback между провайдерами.
- **Видео и аудио**: `пуп`, `дисторшн`, `быстрее`, `медленнее`, `наоборот`, генерация и анимация изображений.
- **Радио Упупы**: по запросу собирает недавнюю жизнь текущего чата в короткий сценарий и присылает чистую голосовую сводку без distortion.
- **Чотам**: анализ изображений, видео, аудио, файлов и URL.
- **Игры**: кракадил с Telegram Mini App, егра, DnD и прочие способы потратить серверное время с достоинством.
- **Чаты**: статистика, ранги, антиспам, SMS/MMS между чатами, праздники, профили участников и другие социальные эксперименты.
- **Автономный канал Упупы**: собственные посты, картинки, комментарии к внешним каналам, память публикаций, настроение и плавающая круглосуточная частота постинга.

## Архитектура

После рефакторинга **R0–R15** проект больше не строится вокруг `main.py`, `config.py` и надежды на то, что порядок импортов сегодня совпадёт со вчерашним.

```text
upupa/
├── main.py            минимальная точка входа
├── app/               composition root, startup/shutdown, фоновые задачи
├── core/              settings, state, paths, storage, bot/dispatcher
├── infrastructure/    внешние адаптеры
│   ├── ai/            Gemini/Groq/GigaChat/OpenAI-compatible providers
│   └── persistence/   SQLite и durable storage adapters
├── handlers/          Telegram handlers и routing
├── features/          прикладные сценарии и dialog pipeline
├── services/          поиск, погода, медиа, YTP, мемы и внешние сервисы
├── games/             игры
├── AI/                AI-функции
│   └── dialog/        генерация, настройки, команды, serious mode, style
├── prompts/           промпты, персоны и тексты
└── tests/             regression, smoke и architecture guardrails
```

Главное правило теперь простое: **импортировать из канонического модуля, а не через историческую магию**. `config.py`, `AI.talking`, `AI.wrapper`, `AI.gigachat_client` и `core.ai_clients` удалены; тесты не дают им воскреснуть ночью.

Подробно направление зависимостей, persistence, async I/O и ограничения описаны в [ARCHITECTURE.md](ARCHITECTURE.md).

## Что дали R0–R15

| Этап | Что изменилось | Зачем |
| --- | --- | --- |
| **R0** | architecture guardrails, coverage floor, починка словаря кракадила | сначала научиться замечать, когда рефакторинг что-то ломает |
| **R1** | `app.bootstrap`, явный lifecycle, `TaskSupervisor` | убрать запуск приложения и фоновые задачи из каши импортов |
| **R2** | `core.paths`, JSON repository, явная загрузка state | отделить пути и хранение данных от бизнес-логики |
| **R3–R3.2** | async-safe HTTP и media I/O | перестать блокировать event loop сетевыми вызовами и общими temp-файлами |
| **R4** | `infrastructure.ai`, lazy provider clients | отделить SDK провайдеров от прикладного AI-кода и import-time side effects |
| **R5** | явный `features.dialog_pipeline` | убрать runtime monkeypatch и сделать порядок реакций/диалога читаемым |
| **R6** | persistence boundaries для JSON/SQLite | перестать размазывать файловый I/O и SQL по feature-модулям |
| **R7** | распилен огромный `AI.talking` | заменить god-module на сфокусированные `AI.dialog.*` модули |
| **R8** | Mini App security + надёжный deploy с rollback | уменьшить поверхность атаки и шанс оставить прод в сломанном состоянии |
| **R9** | удалены AI compatibility facades | перестать поддерживать старые import paths вечно |
| **R10** | удалён последний `config.py` compatibility facade | довести миграцию до конца и оставить один источник истины для зависимостей |
| **R11** | динамические фоновые задачи переданы `TaskSupervisor` | единообразно владеть задачами и корректно завершать их |
| **R12** | синхронные DnD provider-вызовы получили thread/timeout boundary | не блокировать Telegram event loop |
| **R13** | реальные Bot/Dispatcher перенесены в composition root | убрать import-time токены, сессии и сетевые ресурсы |
| **R14** | версионированные SQLite-индексы и repository-query для proactive | ускорить горячие запросы и централизовать SQL |
| **R15** | backup перед переключением кода, functional health-check и staged SSH hardening | снизить риск production deploy и подготовить безопасный уход от root |

Итог: пользовательские команды в основном остались теми же, зато изменение одной части проекта теперь заметно реже вызывает мистический пожар в другой.

## Локальный запуск

```bash
python -m venv venv
. venv/bin/activate
pip install -r requirements.txt
playwright install
python main.py
```

Секреты берутся из `config_private.py` (локально, файл игнорируется git) или из переменных окружения — актуальный список см. в `core/settings.py`. Для запуска обязателен только `API_TOKEN`; ключи AI-провайдеров проверяются при первом использовании соответствующей функции.

Из системных зависимостей нужен как минимум **ffmpeg**; часть медиа-функций также зависит от утилит/библиотек, перечисленных в `requirements.txt`.

## Тесты

```bash
pip install -r requirements-test.txt
python -m pytest tests/ -q
```

CI запускается для pull request и push в `main`/`refactor`, собирает coverage для основных пакетов и запускает `pyflakes` для архитектурного ядра (`main.py`, `app`, `core`, `infrastructure`) и держит минимальный coverage floor **30%**.

Кроме обычных regression-тестов есть architecture guardrails. Они, среди прочего, проверяют направление зависимостей и запрещают возвращать удалённые compatibility imports. То есть `config.py` теперь нельзя просто тихо положить обратно и сделать вид, что так и было.

## Деплой

Push/merge в `main` запускает GitHub Actions:

1. берётся **точный SHA** коммита, а deploy на VPS выполняется последовательно;
2. до переключения кода создаётся append-only backup SQLite/JSON/log state с manifest и SHA-256;
3. сервер переключается на целевой SHA и обновляет зависимости из `requirements.txt`;
4. перезапускается systemd-сервис;
5. health-check с тремя попытками проверяет и `systemctl is-active`, и Telegram `getMe`;
6. при ошибке workflow откатывает код и зависимости на предыдущий commit, а pre-deploy backup оставляет для ручного восстановления данных.

Параметры VPS можно задать secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_APP_DIR` и `DEPLOY_SERVICE`. Если настроен `SSH_KNOWN_HOSTS`, host key проверяется строго. До ручного provisioning сохраняются совместимые legacy defaults; безопасный переход на отдельного deploy-user описан в [docs/DEPLOY_HARDENING.md](docs/DEPLOY_HARDENING.md).

## Как вносить изменения

- отдельная ветка под изменение;
- pull request в `main`;
- изменение должно быть ограниченным по scope;
- сначала regression/architecture tests, потом героизм;
- новые зависимости берутся из канонических модулей (`core`, `infrastructure`, `features`, `AI.dialog` и т. д.);
- `handlers/` по возможности остаются тонким транспортным слоем;
- merge в `main` означает запуск production deploy.

Если хочется понять, почему конкретный импорт считается архитектурным преступлением, см. [ARCHITECTURE.md](ARCHITECTURE.md). Там всё без шуток. Почти.