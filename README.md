# Upupa

[![Tests](https://github.com/lingvofil/upupa/actions/workflows/tests.yml/badge.svg)](https://github.com/lingvofil/upupa/actions/workflows/tests.yml)
[![Deploy](https://github.com/lingvofil/upupa/actions/workflows/deploy.yml/badge.svg)](https://github.com/lingvofil/upupa/actions/workflows/deploy.yml)

Многофункциональный Telegram-бот на aiogram 3: AI-диалог с персонами (Gemini / Groq / GigaChat / OpenRouter с фоллбэками), описание и генерация картинок, голосовые, викторины, игры (кракадил, егра, DnD), статистика чатов, мемогенератор, обработка видео (YTP), погода, поиск и многое другое.

## Структура

Кратко (подробнее — в [ARCHITECTURE.md](ARCHITECTURE.md)):

```
main.py       точка входа: middleware, сборка роутеров, запуск polling
config.py     фасад обратной совместимости над core/*
core/         settings, loader (bot/dp), ai_clients, state, логирование
handlers/     14 роутеров-хэндлеров; порядок подключения критичен (catch-all — последний)
features/     функциональные блоки: настройки, статистика, фильтры, рассылки
services/     поиск, погода, ytp, медиа, мемы
games/        кракадил, егра
AI/           AI-функции: talking, summarize, картинки, голос, викторины
prompts/      промпты, персоны, справка, данные чатов
tests/        smoke-импорты, контракты фасадов, регистрация хэндлеров, pyflakes
```

## Запуск

```bash
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
playwright install               # для модулей с браузером
# секреты: config_private.py в корне ИЛИ env-переменные (см. core/settings.py)
python main.py
```

Системные зависимости: `ffmpeg` (pydub/moviepy).

## Тесты

```bash
pip install -r requirements-test.txt
python -m pytest tests/ -q
```

Тесты гоняются в CI на каждый push в `main`/`refactor`. Они проверяют:
импортируемость всех модулей, полноту фасадов `config` и `prompts`,
количество и порядок регистрации хэндлеров (87), отсутствие undefined names (pyflakes).

## Деплой

Push в `main` → GitHub Actions по SSH делает `git reset --hard origin/main`
и перезапускает `upupa_bot.service` на сервере. Зависимости на сервере
обновляются вручную (`venv` не пересобирается деплоем) — версии зафиксированы
в `requirements.txt` по продовому окружению.

## Правила разработки

- Ветка `refactor` → PR → merge в `main` (merge = деплой).
- Один коммит — одно изменение; после каждого коммита тесты зелёные.
- Новый код импортирует из `core.*` / `prompts.*` напрямую; фасады `config.py`
  и `prompts/__init__.py` — только для старого кода.
- Хэндлер — тонкая обёртка в `handlers/`, логика — в профильном модуле.
