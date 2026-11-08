# Структура проекта

```
upupa/
├── main.py            # минимальная точка входа: asyncio.run(main())
├── app/               # composition root и lifecycle приложения
│   ├── bootstrap.py   # сборка router'ов, startup и запуск polling
│   └── lifecycle.py   # владелец фоновых asyncio-задач
├── prompts/           # промпты и текстовые данные
├── core/              # shared settings/state/paths/storage/loader
├── infrastructure/    # адаптеры внешних систем
│   ├── ai/            # Gemini/Groq/GigaChat/OpenAI-compatible providers и lazy resources
│   └── persistence/   # SQLite и другие durable-storage adapters
├── features/          # функциональные блоки бота, включая явный dialog pipeline
├── services/          # внешние сервисы и обработка медиа (поиск, погода, ytp, мемы)
├── games/             # игры (крокодил, егра)
├── AI/                # AI-функции
│   └── dialog/        # settings/generation/commands/serious-mode/style диалога
└── tests/             # regression, architecture guardrails и smoke-тесты
```

## Направление зависимостей

- `main.py` делегирует запуск в `app/` и не содержит бизнес-логики.
- На R13 `Bot` и `Dispatcher` создаются только в `app.bootstrap.create_application`; импорт `core.loader` оставляет лишь стабильные compatibility-proxy без токена, сессии и сетевых ресурсов.
- `app/` — composition root: ему разрешено знать про handlers/features/services/games/AI/core/infrastructure.
- `core/` не должен зависеть от `AI/`, `features/`, `services/`, `games/` или `handlers/`; это контролируется тестом архитектуры.
- Provider-реализации находятся в `infrastructure.ai` и не зависят от `AI/` или других прикладных слоёв.
- Durable-storage adapters находятся в `infrastructure.persistence`; feature-модули не должны содержать SQL.
- На R9 удалены промежуточные import-path фасады `core.ai_clients`, `AI.wrapper`, `AI.gigachat_client` и `AI.talking`. Provider-зависимости идут через `infrastructure.ai`, dialogue-зависимости — через `AI.dialog.*`.
- На R10 удалён общий compatibility-фасад `config.py`; production-код импортирует canonical modules напрямую.
- Repository-wide architecture guard запрещает импорт `config` в production-коде и повторное появление файла `config.py`.
- Architecture guard также запрещает возвращать удалённые import paths в production-код.

## AI providers

- Gemini SDK adapter/fallback находится в `infrastructure.ai.gemini`.
- GigaChat conversation/fallback adapters находятся в `infrastructure.ai.gigachat`.
- Groq adapter находится в `infrastructure.ai.groq`.
- OpenRouter/SiliconFlow совместимый HTTP adapter находится в `infrastructure.ai.openai_compatible`.
- Настроенные объекты `model`, `groq_ai`, `gigachat_model`, `gemini_client`, `openrouter_ai` и `siliconflow_ai` экспортируются через `infrastructure.ai.clients` как `LazyResource`.
- Импорт `infrastructure.ai.clients` не создаёт SDK-клиенты: реальный объект создаётся потокобезопасно при первом обращении и затем переиспользуется.
- Старые provider-фасады `AI.wrapper`, `AI.gigachat_client` и `core.ai_clients` удалены на R9; их публичные типы/ресурсы импортируются из соответствующих `infrastructure.ai.*` модулей.
- Provider wrappers пока синхронные. Async call-sites должны продолжать выносить их в `asyncio.to_thread`; переход на native async transport в этот этап не входит.

## Dialog pipeline

- Production catch-all handler делегирует реакции и прямой диалог в `features.dialog_pipeline`.
- Канонический порядок внутри pipeline: один проход random reactions/accounting, затем serious/direct dialogue.
- `handlers.dialog` больше не вызывает `AI.situational_summary.install_into_random_reactions` и не присваивает функции другим AI-модулям во время импорта.
- Живой буфер ситуативной реакции и защита от повторного `message_id` используются явно из `AI.situational_summary`; ситуативный текст генерируется через `generate_absurd_situational_reaction` без замены функций другого модуля.
- Dialogue feature разделён на `AI.dialog.settings`, `AI.dialog.generation`, `AI.dialog.model_commands`, `AI.dialog.prompt_commands`, `AI.dialog.serious_mode` и `AI.dialog.style`.
- `features.dialog_pipeline`, `handlers.ai_modes`, `handlers.ai_prompts` и остальные потребители dialogue helpers используют focused `AI.dialog.*` модули напрямую.
- `AI.talking` удалён на R9 после миграции оставшихся потребителей; legacy `process_general_message` больше не является доступным production API.
- `AI.random_reactions.process_random_reactions` пока сохранён для совместимости, но production handler его не компонует.
- Случайная реакция по-прежнему прерывает catch-all handler, а serious/direct dialogue не пропускает финальную запись `features.statistics.log_message`.
- Сонник (`AI.dream_book`) — триггерная реакция на рассказ о собственном сне («мне снилось», «приснился», «видел во сне», «у меня был сон»). Он стоит в pipeline первым после проверки `reactions_enabled`, до случайных реакций, и управляется per-chat шансом `dream_prob` (по умолчанию 100%, меню «Настроить шансы»). Чужие сны («тебе снилось»), отрицания/идиомы («и не снилось») и пожелания («пусть приснится») отсеиваются; бот-сообщения, пересланные сообщения ботов и автопересылки постов канала в группу обсуждения игнорируются. Пересланный сон человека толкуется от имени автора оригинала (пользователь, скрытый пользователь, подпись или название чата/канала), а переславшему может достаться мимоходом. Толкование короткое (40–110 слов): 2–4 образа «▪️ образ — фраза» и одна строка «Итог»; строки, где толкуются местоимения, сам сновидец или время, и образы сверх четырёх вырезаются кодом. Анонс без содержания («мне такая дичь приснилась») ждёт до 3 минут продолжения от того же автора и толкуется вместе с ним. Кулдаун — 3 минуты на пользователя в чате; при сбое генерации кулдаун не расходуется, а сообщение идёт дальше по pipeline. Состояние анонсов и кулдаунов — in-memory и сбрасывается при рестарте.
- Architecture regression-тесты запрещают возвращать runtime monkeypatch в `handlers.dialog`, импортировать удалённые compatibility-фасады и вызывать legacy composed entrypoints из production pipeline.

## Radio Upupa и speech

- `handlers.radio` — тонкий transport-слой для команд `радио упупы` / `упупа радио`; router зарегистрирован до catch-all `handlers.dialog`.
- `features.radio.service` отвечает за сбор материала текущего чата и orchestration выпуска, а `features.radio.script` — за отдельный разговорный prompt и hard limit сценария.
- Радио использует общий history API сводок: в работающем приложении это индексированные выборки из `history.db` за 24/72/168 часов. Для явно переданного стороннего log-файла сохранён старый parser.
- При большом объёме сообщений сначала строится фактическая редакторская выжимка, а уже из неё и свежего контекста — финальный сценарий.
- Помимо пар реплаев за окно выпуска (`summary_context`), радио получает «политическую карту» из `features.social_graph.radio_structure`: лагеря (`detect_factions` + кэшированные AI-названия), их лидеров и связного, роли участников и ярлыки/тренды отношений пар. Структура берётся за 30 дней, но в эфир попадают только люди, активные в окне выпуска; при наличии карты добавляется рубрика «политическая хроника». Любой сбой этого слоя не ломает выпуск.
- `services.speech` — канонический reusable слой `text -> clean TTS -> merged MP3`: синхронные SDK и pydub/ffmpeg export offload'ятся через `asyncio.to_thread`, длинный текст режется на безопасные чанки.
- `services.speech` принципиально не импортирует `services.distortion`. `AI.voice` (`упупа скажи`) использует clean speech backend, а затем отдельно применяет legacy `apply_ffmpeg_audio_distortion`; Radio Upupa получает результат speech backend напрямую, без distortion.
- Радио не запускает фоновые задачи и ничего не делает пассивно: per-chat настройка `radio_enabled` только разрешает или запрещает команду.

## Автономный канал

- `features.channel.scheduler` распределяет mood-зависимое число публикаций по всей московской календарной дате без quiet hours; production-диапазон — `00:00:00–23:59:59`.
- Базовый ориентир активности — около 10 постов в сутки, но фактический диапазон зависит от текущего mood: сонный режим пишет заметно реже, chaotic/social — чаще.
- Время суток не блокирует публикацию, а передаётся в mood prompt как мягкий контекст: ночь/утро/день/вечер могут влиять на тон, не заставляя Упупу механически называть время.
- Mood по-прежнему управляет длиной, типом контента, частотой картинок/внешних комментариев и вероятностью burst-публикаций.
- Базовые текстовые импульсы разделяют бытовой режим (25%) и деятельный `mischief` (20%), чтобы сохранять абсурд без постоянной пассивности.
- Валидатор вводит независимые cooldown-окна для повторного зачина «хочу/хочется» и пассивно-унылых мотивов; отклонённая генерация повторяется с объяснением причины.
- Редкий формат «сон» (`features.channel.dreams`): Упупа рассказывает, что ему снилось, в манере чатового сонника (`AI.dream_book`) — саркастично, с самоиздёвкой и матом, но без толкования: блок сонника, буллеты образов, «Итог:» и упоминания сонника отклоняются валидатором. Базовый шанс 4% слота, чаще утром и в sleepy-настроении, реже вечером; кулдаун 15 постов. У формата свой лимит 600 символов вместо общих 280, но сохранены проверки на дубль, бытовой реквизит и повтор сюжета прошлого сна; три неудачные генерации — откат к остальным форматам.

## Игровая лаборатория

- `features.lab` — инфраструктура экспериментов с новыми играми: реестр (`registry`), флаги по чатам (`flags`), журнал событий (`events`), разбор команды без aiogram (`commands`) и фильтр `ExperimentEnabled` (`filters`). Игровая логика каждого эксперимента живёт в своём модуле, отдельно от Telegram.
- По умолчанию все эксперименты выключены. Админ (`ADMIN_ID`) включает их в текущем чате командой `лаба вкл <имя>` и выключает командой `лаба выкл <имя>` или `лаба выкл все`. `лаба` без аргументов показывает состояние. Чужое «лаба» уходит дальше по роутерам.
- Флаги хранятся в `lab_experiments.json` (`{"<chat_id>": ["oracle", ...]}`), загружаются в `UpupaApplication.initialize_state()` и сохраняются атомарно через `JsonFileRepository` вне event loop.
- Хэндлеры эксперимента подключаются с фильтром `ExperimentEnabled("<ключ>")`: в чате, где эксперимент выключен, update проходит дальше, как будто эксперимента нет.
- События пишутся в `lab_events.jsonl`, по одной JSON-строке: `v`, `ts` (UTC, мс), `exp`, `event`, `chat_id`, `user_id`, `session`, `data`. Базовые события — `start`, `action`, `finish`, `abandon`. Итог раунда обязан попасть в журнал. Включение и выключение эксперимента пишется как `lab_enabled`/`lab_disabled`. `log_event` пишет вне event loop и никогда не бросает исключений. Текст сообщений сам в журнал не попадает.
- Оба файла лежат в корне рядом с остальным runtime-состоянием, попадают в deploy-backup и исключены из git.
- Салон «Гуру» (эксперимент `guru`, правила v5): комедийный симулятор гадалки: одиночная смена и живой приём, где один игрок гадает другому. Подробные правила, метрики и бэклог — `docs/lab/guru.md`.
  - Ядро `games.guru` не знает про Telegram: `content` (словарь слотов, методы гадания, архетипы), `stories` (12 историй с уточнениями, больше половины — про отношения), `model` (история, клиент, партия со списком участников), `rules` (единственное место изменения состояния: `new_session`, `available_actions`, `act`, `build_view`, `SessionRegistry`), `contract` (`View`, `Action`, `DisplayEvent`, `ActResult`; `View.to_dict()` — задел для Mini App/PC), `textui` (события и View → сообщения и кнопки без aiogram). Случайность приходит как `random.Random`, время — числом секунд: партия воспроизводится по seed.
  - Приём клиента — автомат фаз: рассказ → до 2 уточнений → до 2 гаданий с выбором толкования → конструктор → объявление → развязка; следующий клиент приходит по кнопке. Выводы из рассказа игрок делает сам: что сказал клиент, хранится скрыто и нужно только для правила эха.
  - Живой приём (`games.guru.live`, `LiveSession`, `LiveView`) — отдельный автомат фаз `waiting → divine → announce → verdict`. Скрытой правды нет: люди говорят в чате, ядро сообщения не читает, даёт реквизит и принимает ставку уверенности; исход выбирает клиент. Гонорар мадам платит касса салона, а не клиент, чтобы оценка оставалась честной. Партия и живой приём делят `SessionRegistry`: один на чат.
  - Касса игрока (`features.guru_wallets`, `guru_wallets.json`, запись через `JsonFileRepository` вне event loop) своя в каждом чате и может уйти в минус. Ядро о хранении не знает: получает стартовый баланс в `new_session` и отдаёт сумму в `ClientReport.fee`, transport зачисляет её сразу после приёма, поэтому брошенная смена заработанного не теряет.
  - Рисунки Таро лежат в `assets/guru_tarot/`. Ядро отдаёт их имена в `DisplayEvent.data["cards"]`, transport показывает альбомом и запоминает `file_id`; если альбом не отправился, партия продолжается по тексту.
  - У клиента три шкалы: доверие к гадалке, удовлетворённость сеансом и склонность верить в мистику; разоблачённый клиент может уйти довольным.
  - `DisplayEvent.pause`, `spread` и `replace` описывают драматические паузы, не привязываясь к интерфейсу. `handlers.guru_salon` показывает их как «печатает…» и `asyncio.sleep` внутри хэндлера: aiogram обрабатывает update отдельными задачами, поэтому ожидание не задерживает других пользователей. Если партию закрыли во время паузы, показ прерывается.
  - `handlers.guru_salon` — тонкий transport за `ExperimentEnabled("guru")`, router до `dialog`. Команды: `гуру лаб` — открыть, `гуру лаб приём` — живой приём (только в группе), `гуру лаб касса` — кассы чата, `гуру лаб стоп` — бросить (участник или админ). В личке с ботом эксперимент включается той же командой `лаба вкл гуру`. Одна партия на чат, кнопки жмёт только игрок; нажатия зрителей пишутся как `spectator`.
  - Callback `guru:<сессия>:<шаг>:<действие>`: шаг сессии растёт на каждое принятое действие, ход применяется синхронно до первого `await`, поэтому двойное нажатие и старые кнопки не играют. Отдельно: `guru:again:yes|no:<сессия>` (финал), `guru:rep:<клиент>:<сессия>` (подробный разбор, текст держится в памяти до нажатия) и `guru:livenew:<сессия>` (нажавший открывает следующий живой приём).
  - Партия живёт в памяти. Без ходов `IDLE_TIMEOUT_S` (15 минут; открытый живой приём без клиента — `WAIT_TIMEOUT_S`, 3 минуты) её бросает `guru_salon_timeout_loop` через `TaskSupervisor` (`guru-salon-timeouts`); при остановке бота незаконченные партии пишутся как `abandon` с `reason=restart`.
  - Метрики формирует ядро как данные (`ActResult.metrics`), transport только пишет их в журнал: `start`, `action` (`kind=question|reading|prediction`), `client_reveal`, `legend_lost`, `streak_updated`, `mastery_bonus`, `finish`, `abandon`, `replay_answer`, `start_rejected`, `spectator`. `rules` — версия баланса `rules.RULES_VERSION`; её поднимают при любом изменении чисел.
  - Прогрессии гадалки в MVP нет. Точки расширения для будущих инструментов (другая колода, дополнительный вопрос, новые способы хиромантии, особые толкования): наборы толкований в `content` — данные, лимиты `CLARIFICATIONS` и `READINGS` — константы правил. Когда прогрессия появится, инструменты игрока подаются параметром в `new_session`; ядро останется без знания о хранении.

## Lifecycle

Фоновые планировщики запускаются через `TaskSupervisor`, который хранит ссылки на задачи,
логирует необработанные исключения и отменяет оставшиеся задачи при завершении polling.
Динамические задачи DnD-опросов и Crocodile bump-loop также регистрируются в том же supervisor через явную конфигурацию из composition root; прямой `asyncio.create_task` в этих модулях запрещён regression-тестом.
На R13 реальные `Bot` и `Dispatcher` стали ресурсами `UpupaApplication` и создаются только в `app.bootstrap.create_application`. Стабильные compatibility-proxy из `core.loader` делегируют им обращения после связывания и сами не создают токен, сессию или сетевые ресурсы при импорте.

Постоянное состояние, которое необходимо приложению на старте, загружается из
`UpupaApplication.initialize_state()`, а не как побочный эффект импорта feature-модуля.
На R6 это правило распространяется на chat settings/list, антиспам, SMS-disable,
message/rank counters и rank-notification settings; SQLite schema также инициализируется
явно из startup.

## Пути и persistence

- Канонические пути к рабочим данным определяются в `core.paths` через абсолютный `PROJECT_ROOT`.
- Физически JSON/DB/log-файлы пока остаются в корне репозитория; R6 не требует миграции production-данных и не меняет их форматы.
- `core.state` временно сохраняет старые `*_FILE` имена как строки для прямых legacy-потребителей; источник истины для путей — `core.paths`.
- Для JSON используется граница `JsonRepository` и файловая реализация `JsonFileRepository`.
- `JsonFileRepository` пишет через временный файл и атомарный `os.replace`, чтобы авария записи не оставляла частично записанный JSON.
- JSON-настройки в `features.chat_settings`, `features.stat_rank_settings`, `features.sms_settings` и `features.content_filter` загружаются через repository с сохранением identity shared `dict/set/list`. Счётчики сообщений читаются непосредственно из SQLite через отдельный repository.
- `rank_notifications_settings.json`, `sms_disabled_chats.json` и `antispam_enabled.json` сохраняют прежний JSON-контракт. Счётчики рангов теперь находятся в таблице `rank_counters` в `statistics.db`: startup однократно импортирует `message_stats.json` в транзакции с маркером миграции; исходный JSON сохраняется. Повреждённый импорт останавливает startup без частичного переноса данных.
- SQL для `statistics.db` сосредоточен в `infrastructure.persistence.sqlite_statistics.SQLiteStatisticsRepository`; `features.statistics` является facade/application API и не открывает SQLite-соединения самостоятельно. На R14 это правило распространено на `features.proactive`: выборка последней активности чатов также проходит через repository.
- Схемы таблиц `message_stats` и `model_stats` не меняются. R14 добавляет только служебную таблицу `persistence_migrations` и идемпотентные индексы для выборок по приватности, времени, чату и пользователю.
- SQLite adapters используют WAL и `busy_timeout=30s`; синхронные методы по-прежнему вызываются из async-кода через `asyncio.to_thread`. Версия миграции фиксируется как `statistics:001-query-indexes`, поэтому повторный startup безопасен.

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
- На R6 запись operational statistics в SQLite и async-чтение статистических отчётов offload'ятся через `asyncio.to_thread`; JSON-сохранение настроек и SMS-disable из async handlers также не блокирует event loop.
- Синхронный provider routing в `AI.dialog.generation` сохраняет `asyncio.to_thread` boundary для Gemini/GigaChat/Groq/OpenRouter/SiliconFlow.
- На R12 создание DnD-сессии и все её синхронные provider-вызовы вынесены через `asyncio.to_thread` и ограничены timeout; Telegram event loop больше не ждёт SDK синхронно.
- Генерация картинок и видео (`AI.media_execution`, `AI.aihorde_image`, `AI.gigachat_image`) идёт через отдельную media-полосу `AIExecutionGovernor`: свои слоты (`UPUPA_AI_MEDIA_MAX_CONCURRENCY`, по умолчанию 2) и свой executor, текстовые слоты (`UPUPA_AI_MAX_CONCURRENCY`) она не занимает. Async-вызовы (`run_ai_media_call`) ждут слот и результат без потока default executor; метрики полосы видны в диагностике как `ai.media_*`.
- Default executor asyncio задаётся явно при старте (`app.bootstrap.configure_default_executor`, `UPUPA_DEFAULT_EXECUTOR_MAX_WORKERS`, по умолчанию 32): через него идут записи SQLite и синхронные AI SDK, а неявный `min(32, cpu + 4)` на 1–2 vCPU даёт лишь 5–6 потоков.
- Внешние процессы (ffmpeg, ffprobe, lottie_convert, sherlock) запускаются только через `infrastructure.subprocess_runner.run_subprocess`: таймаут обязателен, при таймауте и отмене задачи процесс (на POSIX — его группа) убивается с ожиданием выхода, захват stdout/stderr ограничен. Regression-тест запрещает `asyncio.create_subprocess_*` в остальном production-коде. Скрипты venv вроде `lottie_convert.py` ищутся рядом с `sys.executable` или в `PATH`, а не по пути деплоя.

## Security и deploy

- Crocodile Mini App отправляет на Socket.IO сервер raw `Telegram.WebApp.initData`; сервер проверяет Telegram HMAC и свежесть `auth_date` до принятия соединения.
- Client-controlled `room` не является авторизацией: после проверки Telegram user id сервер связывает socket с активной игровой сессией и разрешает `draw_step`, `snapshot`, `skip_turn` и `final_frame` только текущему `drawer_id`.
- Wildcard Socket.IO CORS на R8 удалён; используется same-origin policy библиотеки.
- `IncomingMessageLogMiddleware` по умолчанию пишет только идентификаторы/тип/длину, без имени пользователя, текста сообщения и полного UNKNOWN payload. Короткий текстовый preview допускается только через явный `LOG_MESSAGE_CONTENT=true`.
- Production deploy сериализован через GitHub Actions concurrency и разворачивает точный `${{ github.sha }}`, а не плавающий `origin/main`.
- До переключения кода R15 создаёт append-only backup корневых `*.db`, `*.json` и `user_messages.log`: SQLite копируется online backup API, а manifest фиксирует размер и SHA-256 каждого файла.
- После установки dependencies и рестарта workflow проверяет systemd, локальный `/ready` и Telegram `getMe` с 12 попытками. `/ready` обслуживается event loop бота на `127.0.0.1:8766`: требует успешного `getUpdates` за последние 90 секунд без последующей ошибки, наличия обязательных фоновых циклов и доступности SQLite для чтения и получения write-lock. PID ответа сверяется с systemd `MainPID`. Это readiness инфраструктуры, а не end-to-end проверка каждой пользовательской команды. При откате запускается healthcheck восстановленного коммита.
- SSH host key проверяется строго, когда задан secret `SSH_KNOWN_HOSTS`. `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_APP_DIR` и `DEPLOY_SERVICE` также вынесены в optional secrets.
- Legacy fallback на текущий root-host и `StrictHostKeyChecking=no` временно сохранён, чтобы R15 не сломал production до ручного provisioning deploy-user и доверенного host key. Порядок перехода описан в `docs/DEPLOY_HARDENING.md`.
- GigaChat wrappers пока сохраняют `verify_ssl_certs=False`: включение проверки требует сначала установить на VPS доверенный российский CA bundle и прокинуть его в SDK. Отключать текущий рабочий путь без этого prerequisite нельзя.

## Прочее

- Секреты: `config_private.py` (локально, в `.gitignore`) или env-переменные. На startup обязателен только `API_TOKEN`; ключи AI-провайдеров опциональны и проверяются лениво при первом обращении к конкретному провайдеру.
- Каждый рефакторинговый цикл оформляется отдельным PR и должен проходить CI.
## Счётчики рангов и откат миграции

- `SQLiteRankCountersRepository` владеет SQL; `features.stat_rank_settings` вызывает его через `asyncio.to_thread`. Общего in-memory словаря и полной перезаписи JSON на сообщение больше нет.
- `BEGIN IMMEDIATE` сериализует транзакцию чтения/увеличения одной строки. Поздравление отправляется после commit; ошибка Telegram не теряет счётчик.
- Дневной и недельный периоды сохраняют прежнюю семантику (локальная дата сервера и семь дней с предыдущего недельного сброса). При чтении просроченный период также отображается нулём.
- Перед автоматическим откатом на JSON-версию workflow останавливает сервис и запускает `scripts/export_rank_counters.py`. Экспорт атомарно сохраняет JSON и ставит SQLite-маркер `rank-counters:legacy-active`. Следующий upgrade транзакционно импортирует актуальный JSON, включая сообщения старой версии. Повторный экспорт до импорта не перетирает JSON; отсутствующий/невалидный файл после handoff останавливает миграцию.
- При ошибке экспорта workflow останавливает откат до переключения кода: сервис остаётся остановлен, SQLite и backup сохранены для восстановления.

## Индексированная история

- `SQLiteHistoryRepository` хранит сообщения в `history.db`: индексы по чату/времени, чату/пользователю, username, имени и порядку сообщений. `history_fts` (FTS5) ускоряет поиск слов; точный `chat_id` всегда проверяется SQL-условием. SQLite на сервере должен поддерживать FTS5.
- Composition root инициализирует индекс до polling и регистрирует его через `core.history_store`. Сводки, радио, комиксы, профили/лексикон, память диалога, контекст канала, викторины, поздравления, мемы, SMS-просмотр и активность мира используют индекс для основного журнала. Явные сторонние пути и standalone-вызовы без composition root сохраняют совместимость с прежним файловым API.
- Первый импорт `user_messages.log` потоковый, с commit каждых 1000 записей. Сообщения и byte-offset checkpoint фиксируются одной транзакцией. Ошибка откатывает текущую порцию; повторный startup продолжает с последней завершённой порции. Многострочные сообщения сохраняются; нераспознанные/служебные записи сохраняются в `history_rejected` с исходными байтами. Незавершённый хвост без newline ждёт продолжения. При деплое `scripts/index_history.py` подготавливает индекс до переключения кода с `finalize_tail=False`: последняя запись остаётся открытой для работающего старого бота. Startup завершает импорт после остановки старого процесса.
- Перед чтением индекс дочитывает только новый хвост; для уже обработанного префикса проверяется 256-байтный контрольный участок. Это проверка целостности checkpoint, не криптографическая проверка всего архива. Усечение или несовпадение контрольного участка останавливает обработку без удаления индекса. Журнал не следует вручную менять/ротировать; retention требует согласованной миграции.
- Новое сообщение сначала записывается в durable `history_pending`, затем в совместимый журнал и индекс. После сбоя дописывается только недостающий хвост; Telegram message_id сохраняется и предотвращает повторную запись того же сообщения. Старые записи журнала не содержат message_id, восстановить ссылки на них из текста невозможно.
- `history.db` и журнал включены в существующий backup. При восстановлении допускается смена каталога/inode при совпадающем контрольном участке; более свежий хвост журнала доиндексируется. Перед откатом на версию без индекса `prepare_history_rollback.py` завершает pending-запись при остановленном боте. Старый бот продолжает писать обычный журнал; повторное обновление импортирует этот хвост. При ошибке подготовки откат прекращается до переключения кода.
- Лексический поиск обсуждений ищет по всей индексированной истории. При отсутствии лексических кандидатов приблизительный поиск с опечатками ограничен последними 2000 сообщениями текущего чата. Семантический поиск по смыслу пока не реализован.
- Семантика времени сводок/истории остаётся локальной датой сервера. Викторина сохраняет своё прежнее UTC-толкование naive timestamp; унификация timezone — отдельное изменение.
- `чобыло` и `что я пропустил(а)` используют общий `_summarize_messages` с прежним промптом и цепочкой fallback. `history_summary_cursors` хранит `(chat_id, user_id) → through_id, requested_at`. Перед выборкой фиксируется максимальный ID чата; все запросы ограничены этим ID, поэтому сообщения, поступившие во время генерации, не будут пропущены. Первый персональный запрос берёт 12 часов, последующие — ID после отметки без ограничения давности. Оба режима подтверждают отметку после доставки всех частей ответа (либо ответа об отсутствии новых сообщений). Ошибка/отмена не подтверждает её; UPSERT не допускает движения назад. Одновременные запросы одного пользователя в одном чате блокируются в рамках процесса; отмена освобождает блокировку. Анонимные отправители не получают общий персональный курсор. Размер выборки остаётся ограниченным, как у обычной сводки; команды сводки исключаются из входных данных.
