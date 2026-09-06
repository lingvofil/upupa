import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_prompt_context_is_task_local_and_resettable():
    from AI.dialog.settings import build_prompt_with_current_chat_prompt
    from core.prompt_context import reset_prompt_context, set_prompt_context

    token = set_prompt_context("СОЦГРАФ: Вася ↔ Петя: сила 6")
    try:
        prompt = build_prompt_with_current_chat_prompt("-1001", "Сделай сводку")
        assert "Дополнительный фактический контекст" in prompt
        assert "Вася ↔ Петя" in prompt
    finally:
        reset_prompt_context(token)

    clean = build_prompt_with_current_chat_prompt("-1001", "Другая задача")
    assert "Вася ↔ Петя" not in clean


def test_radio_rubrics_and_two_speaker_parser():
    from features.radio.script import _choose_rubrics
    from features.radio.voices import parse_speaker_turns, strip_speaker_labels

    class Rng:
        def randint(self, _a, _b):
            return 2

        def sample(self, values, k):
            return list(values)[:k]

    rubrics = _choose_rubrics(
        world_context="есть мир",
        social_context="Вася и Петя часто взаимодействовали",
        rng=Rng(),
    )
    assert len(rubrics) == 4
    assert any(name == "кто с кем" for name, _instruction in rubrics)
    assert rubrics[-1][0] == "международная панорама"

    script = "ВЕДУЩИЙ: Начинаем эфир. ЭКСПЕРТ: Я изучил ровно эти факты. ВЕДУЩИЙ: Спасибо, ужасно полезно."
    turns = parse_speaker_turns(script)
    assert [turn.speaker for turn in turns] == ["host", "expert", "host"]
    assert "ВЕДУЩИЙ:" not in strip_speaker_labels(script)
    assert "ЭКСПЕРТ:" not in strip_speaker_labels(script)


def test_summary_status_hides_message_counters():
    from handlers.ai_summary import _NaturalSummaryMessage

    reply = AsyncMock(return_value="sent")
    raw = SimpleNamespace(reply=reply)
    proxy = _NaturalSummaryMessage(raw)
    asyncio.run(
        proxy.reply(
            "Сводка за последние 12 часов. Сообщений: 843; в выборке: 500. Щас всех вас сдам..."
        )
    )
    text = reply.await_args.args[0]
    assert "Сообщений:" not in text
    assert "в выборке" not in text
    assert "Щас всех вас сдам" in text


def test_crocodile_has_no_speed_bonus_and_keeps_artist_stats():
    import features.crocodile_scoring as scoring
    import games.reverse_crocodile as reverse

    assert "fast_guess_bonus" not in inspect.getsource(scoring)
    assert "бонусных очк" not in inspect.getsource(reverse)
    assert "pick_reverse_crocodile_word" in inspect.getsource(reverse)


def test_crocodile_artist_statistics_are_separate(tmp_path, monkeypatch):
    import features.crocodile_scoring as scoring
    from core.json_repository import JsonFileRepository

    monkeypatch.setattr(
        scoring,
        "_artist_repository",
        JsonFileRepository(tmp_path / "artists.json"),
    )
    asyncio.run(scoring.record_artist_success("-100", 10, "Вася"))
    asyncio.run(scoring.record_artist_success("-100", 10, "Вася"))
    asyncio.run(scoring.record_artist_success("-100", 20, "Петя"))

    board = scoring.format_artist_leaderboard("-100")
    assert "Вася" in board and "<b>2</b>" in board
    assert "Петя" in board and "<b>1</b>" in board


def test_world_expansion_repository_persists_sanctions_and_court(tmp_path):
    import sqlite3

    from infrastructure.persistence.sqlite_world_expansion import SQLiteWorldExpansionRepository

    path = tmp_path / "world.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE world_states (world_id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO world_states(world_id) VALUES (?)", [(1,), (2,)])

    repo = SQLiteWorldExpansionRepository(path)
    repo.init_schema()
    created, sanction = repo.impose_sanction(1, 2, "за плохую дипломатию")
    assert created and sanction is not None
    assert sanction.reason == "за плохую дипломатию"
    assert len(repo.list_active_sanctions(2)) == 1

    duplicate, _ = repo.impose_sanction(1, 2, "ещё раз")
    assert duplicate is False
    assert repo.lift_sanction(1, 2) is True
    assert repo.list_active_sanctions(2) == []

    case = repo.record_court_case(1, 2, "верните табуретку", "иск удовлетворить частично")
    assert case.case_id == 1
    assert repo.list_court_cases(limit=5)[0].claim == "верните табуретку"


def test_world_hub_exposes_sanctions_and_court_buttons():
    from handlers.world_expansion import _main_markup

    markup = _main_markup()
    texts = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "🚫 Санкции" in texts
    assert "⚖️ Международный суд" in texts
    assert "worldhub:sanctions" in callbacks
    assert "worldhub:court" in callbacks


def test_world_news_knows_sanctions_and_international_court():
    from datetime import datetime

    from features.world.ledger import WorldEvent
    from features.world.models import WorldState
    from features.world.news import SIGNIFICANT_EVENT_TYPES, format_event_fact

    now = datetime.now()
    states = {
        1: WorldState(1, -1, "Альфа", now, True),
        2: WorldState(2, -2, "Бета", now, True),
    }
    sanction = WorldEvent(
        event_id=1,
        event_type="sanctions_imposed",
        actor_state=1,
        target_state=2,
        payload={"reason": "за табуретку"},
        created_at=now,
    )
    assert "sanctions_imposed" in SIGNIFICANT_EVENT_TYPES
    assert "санкции" in format_event_fact(sanction, states).lower()


def test_channel_continuity_has_history_gate():
    from features.channel.continuity import should_try_continuity

    class YesRng:
        @staticmethod
        def random():
            return 0.0

    assert not should_try_continuity([{}] * 3, rng=YesRng())
    assert should_try_continuity([{}] * 4, rng=YesRng())


def test_new_router_order_precedes_legacy_handlers():
    import handlers

    names = [router.name for router in handlers.ROUTERS]
    assert names.index("world_expansion") < names.index("world_hub")
    assert names.index("court") < names.index("ai_summary")
    assert names.index("radio") < names.index("dialog")
    assert names[-1] == "dialog"
