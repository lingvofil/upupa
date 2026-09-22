import asyncio
from types import SimpleNamespace

from AI import dnd_result_recovery as recovery
from AI.dnd_group_action_resilience import _resolution_budget, install_dnd_group_action_resilience
from AI.dnd_group_progress import (
    _minimum_resolution_words,
    group_resolution_was_noop,
    inspection_was_deferred,
    install_dnd_group_progress,
    progress_correction_reason,
)


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(
            message_id=100 + len(self.messages),
            chat=SimpleNamespace(id=chat_id),
        )


def _session(chat_id=-1001):
    return SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ACTION",
        action_prompt_message_id=77,
        pending_actions={
            "1": {"user_id": 1, "name": "Алиса", "action": "ломаю дверь"},
            "2": {"user_id": 2, "name": "Боря", "action": "ищу ловушку"},
        },
        action_target_user_ids=[1, 2],
        action_deadline=123.0,
        conversation=[],
        pending_generated_result={},
        pending_generation_request={},
        generated_result_seq=0,
        scene_count=1,
    )


def _fake_dnd(session, generate):
    persisted = []

    async def parse_and_execute_turn(_bot, _chat_id, _text):
        return None

    return SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        persist_dnd_sessions=lambda: persisted.append(True),
        _format_group_actions=lambda actions: "\n".join(
            f"- {item['name']}: {item['action']}" for item in actions
        ),
        with_scene_direction=lambda _session, prompt: prompt,
        generate_session_response=generate,
        parse_and_execute_turn=parse_and_execute_turn,
        DND_SYSTEM_PROMPT="BASE",
    ), persisted


def test_group_turn_is_durable_before_provider_call(monkeypatch):
    session = _session()
    seen = []

    async def generate(_session, _prompt):
        raise AssertionError("group wrapper delegates generation to durable recovery")

    async def fake_continue(dnd, bot, current):
        del dnd, bot
        request = current.pending_generation_request
        seen.append(
            {
                "state": current.state,
                "actions": dict(current.pending_actions),
                "prompt_message_id": current.action_prompt_message_id,
                "kind": request.get("kind"),
                "prompt": request.get("prompt"),
                "effects": list(request.get("telegram_effects") or []),
            }
        )
        return True

    monkeypatch.setattr(recovery, "continue_pending_generation", fake_continue)

    dnd, persisted = _fake_dnd(session, generate)
    install_dnd_group_action_resilience(dnd)
    bot = FakeBot()

    asyncio.run(dnd.finalize_group_actions(bot, session.chat_id, 77))

    assert seen[0]["state"] == "RESOLVING"
    assert seen[0]["actions"] == {}
    assert seen[0]["prompt_message_id"] is None
    assert seen[0]["kind"] == "GROUP_ACTION_CONTINUATION"
    assert "Алиса: ломаю дверь (id=1)" in seen[0]["prompt"]
    assert "Боря: ищу ловушку (id=2)" in seen[0]["prompt"]
    assert "Сначала РАЗРЕШИ КАЖДУЮ заявку" in seen[0]["prompt"]
    assert "ЭТО ПЕРВЫЙ ОБЩИЙ КРУГ" in seen[0]["prompt"]
    assert "не сжимай четыре разных действия" in seen[0]["prompt"].casefold()
    assert "допустим ещё один общий ACTION:INPUT" in seen[0]["prompt"]
    assert seen[0]["effects"][0]["method"] == "send_message"
    assert seen[0]["effects"][0]["text"].startswith("🎭 Ход партии:")
    assert session.action_target_user_ids == []
    assert session.action_deadline is None
    assert persisted
    assert bot.messages == []


def test_failed_group_generation_keeps_exact_request_for_next_command(monkeypatch):
    session = _session()

    async def generate(_session, _prompt):
        raise AssertionError("not called directly")

    async def fail_continue(_dnd, _bot, _session):
        return False

    monkeypatch.setattr(recovery, "continue_pending_generation", fail_continue)

    dnd, _persisted = _fake_dnd(session, generate)
    install_dnd_group_action_resilience(dnd)
    bot = FakeBot()

    asyncio.run(dnd.finalize_group_actions(bot, session.chat_id, 77))

    assert session.state == "RESOLVING"
    assert session.pending_actions == {}
    assert session.pending_generation_request["kind"] == "GROUP_ACTION_CONTINUATION"
    assert "Алиса: ломаю дверь (id=1)" in session.pending_generation_request["prompt"]
    assert len(bot.messages) == 1
    assert "коллективный ход сохранён" in bot.messages[0][1]
    assert "дальше" in bot.messages[0][1]


def test_retry_does_not_duplicate_group_turn_announcement():
    session = _session()
    attempts = []

    async def fail_generate(_session, prompt):
        attempts.append(prompt)
        raise RuntimeError("providers unavailable")

    dnd, _persisted = _fake_dnd(session, fail_generate)
    install_dnd_group_action_resilience(dnd)
    bot = FakeBot()

    asyncio.run(dnd.finalize_group_actions(bot, session.chat_id, 77))

    action_announcements = [
        text for _chat_id, text, _kwargs in bot.messages
        if text.startswith("🎭 Ход партии:")
    ]
    assert len(action_announcements) == 1
    assert session.pending_generation_request["telegram_effects"][0]["status"] == recovery.EFFECT_DONE

    # This is what the host's «дальше» command does for a RESOLVING session.
    asyncio.run(recovery.continue_pending_generation(dnd, bot, session))

    action_announcements = [
        text for _chat_id, text, _kwargs in bot.messages
        if text.startswith("🎭 Ход партии:")
    ]
    assert len(action_announcements) == 1
    assert len(attempts) == 2


def test_later_group_turn_still_preserves_each_action_without_opening_lock(monkeypatch):
    session = _session()
    session.scene_count = 5
    seen = []

    async def generate(_session, _prompt):
        raise AssertionError("group wrapper delegates generation to durable recovery")

    async def fake_continue(_dnd, _bot, current):
        seen.append(current.pending_generation_request["prompt"])
        return True

    monkeypatch.setattr(recovery, "continue_pending_generation", fake_continue)

    dnd, _persisted = _fake_dnd(session, generate)
    install_dnd_group_action_resilience(dnd)

    asyncio.run(dnd.finalize_group_actions(FakeBot(), session.chat_id, 77))

    assert "Сначала РАЗРЕШИ КАЖДУЮ заявку" in seen[0]
    assert "ЭТО ПЕРВЫЙ ОБЩИЙ КРУГ" not in seen[0]
    assert "не обязано запускать новый экшен" in seen[0]


def test_group_prompt_requires_concrete_feedback_for_inspection(monkeypatch):
    session = _session()
    session.scene_count = 5
    session.pending_actions = {
        "1": {"user_id": 1, "name": "Алиса", "action": "осматриваюсь вокруг"},
        "2": {"user_id": 2, "name": "Боря", "action": "ищу следы у ворот"},
    }
    seen = []

    async def generate(_session, _prompt):
        raise AssertionError("group wrapper delegates generation to durable recovery")

    async def fake_continue(_dnd, _bot, current):
        seen.append(current.pending_generation_request["prompt"])
        return True

    monkeypatch.setattr(recovery, "continue_pending_generation", fake_continue)
    dnd, _persisted = _fake_dnd(session, generate)
    install_dnd_group_action_resilience(dnd)

    asyncio.run(dnd.finalize_group_actions(FakeBot(), session.chat_id, 77))

    prompt = seen[0].casefold()
    assert "нельзя ответить «да-да, осматривайтесь/думайте»" in prompt
    assert "дай конкретный результат" in prompt
    assert "если бросок нужен, назначь его конкретному" in prompt


def test_deferred_inspection_response_is_detected():
    source_prompt = (
        "Игроки заявили действия одновременно:\n"
        "- Алиса: осматриваюсь вокруг (id=1)\n"
        "- Боря: ищу следы у ворот (id=2)\n"
        "Сначала явно учти КАЖДУЮ заявку"
    )

    assert inspection_was_deferred(
        source_prompt,
        "Да-да, осматривайтесь и думайте, что делать дальше. [ACTION:INPUT]",
    ) is True

    assert inspection_was_deferred(
        source_prompt,
        "Алиса замечает свежие следы у стены, Боря видит приоткрытую дверь. [ACTION:INPUT]",
    ) is False

    assert inspection_was_deferred(
        source_prompt,
        "Следы смазаны дождём — нужна проверка. "
        "[ACTION:ROLL;TYPE:CHECK;SKILL:Внимательность;TARGETS:2;DC:10;MODE:NORMAL]",
    ) is False


def test_third_consecutive_group_input_requires_progress_correction():
    session = SimpleNamespace(group_input_streak=2)
    pending = {
        "source_request_kind": "GROUP_ACTION_CONTINUATION",
        "source_prompt": (
            "Игроки заявили действия одновременно:\n"
            "- Алиса: иду к воротам (id=1)\n"
            "Сначала явно учти КАЖДУЮ заявку"
        ),
    }

    assert progress_correction_reason(
        session,
        pending,
        "Вы подходите к воротам. [ACTION:INPUT]",
    ) == "third-consecutive-group-input"


def test_parse_replaces_noop_inspection_with_one_correction(monkeypatch):
    session = _session()
    session.state = "RESOLVING"
    session.pending_generated_result = {
        "id": "1:noop",
        "text": "Да-да, осматривайтесь и думайте. [ACTION:INPUT]",
        "source_request_kind": "GROUP_ACTION_CONTINUATION",
        "source_prompt": (
            "Игроки заявили действия одновременно:\n"
            "- Алиса: осматриваюсь вокруг (id=1)\n"
            "- Боря: ищу следы (id=2)\n"
            "Сначала явно учти КАЖДУЮ заявку"
        ),
    }
    session.group_input_streak = 0
    calls = {"parse": 0, "transition": 0, "continue": 0}

    async def generate(_session, _prompt):
        return "unused"

    dnd, _persisted = _fake_dnd(session, generate)

    async def original_parse(_bot, _chat_id, _response):
        calls["parse"] += 1

    dnd.parse_and_execute_turn = original_parse
    install_dnd_group_progress(dnd)

    def fake_transition(current, prompt, *, kind, effects=None):
        del effects
        calls["transition"] += 1
        assert kind == "GROUP_PROGRESS_CORRECTION"
        assert "ИСХОДНЫЕ ЗАЯВКИ" in prompt
        current.pending_generation_request = {
            "prompt": prompt,
            "kind": kind,
        }
        current.pending_generated_result = {}
        return True

    async def fake_continue(_dnd, _bot, _session):
        calls["continue"] += 1
        return True

    monkeypatch.setattr(recovery, "transition_to_generation_request", fake_transition)
    monkeypatch.setattr(recovery, "continue_pending_generation", fake_continue)

    asyncio.run(
        dnd.parse_and_execute_turn(
            FakeBot(),
            session.chat_id,
            "Да-да, осматривайтесь и думайте. [ACTION:INPUT]",
        )
    )

    assert calls == {"parse": 0, "transition": 1, "continue": 1}


def test_group_resolution_budget_scales_with_participant_actions():
    assert _resolution_budget(1) == (90, 120)
    assert _resolution_budget(2) == (111, 141)
    assert _resolution_budget(4) == (167, 197)
    assert _resolution_budget(9) == (170, 200)


def test_generic_acknowledgement_only_group_turn_is_noop():
    source_prompt = (
        "Игроки заявили действия одновременно:\n"
        "- Алиса: пытаюсь уговорить стражника (id=1)\n"
        "- Боря: ем яблоко (id=2)\n"
        "Сначала явно учти КАЖДУЮ заявку"
    )

    assert group_resolution_was_noop(
        source_prompt,
        "Алиса пытается говорить со стражником, Боря продолжает есть яблоко. "
        "Думайте, что делать дальше. [ACTION:INPUT]",
    ) is True

    assert group_resolution_was_noop(
        source_prompt,
        "Стражник отказывается пропускать Алису, но называет цену в три серебряных; "
        "Боря доедает яблоко и замечает на кожуре чужую печать. [ACTION:INPUT]",
    ) is False


def test_generic_noop_reason_is_used_before_streak_limit():
    session = SimpleNamespace(group_input_streak=0)
    pending = {
        "source_request_kind": "GROUP_ACTION_CONTINUATION",
        "source_prompt": (
            "Игроки заявили действия одновременно:\n"
            "- Алиса: говорю со стражником (id=1)\n"
            "Сначала явно учти КАЖДУЮ заявку"
        ),
    }

    assert progress_correction_reason(
        session,
        pending,
        "Алиса продолжает разговор. Решайте, что дальше. [ACTION:INPUT]",
    ) == "group-action-without-consequence"


def test_live_short_group_resolution_from_party_is_rejected():
    source_prompt = (
        "Игроки заявили действия одновременно:\n"
        "- Детектор: бегу в соседнюю комнату, смотрю что там (id=1)\n"
        "- Чудо: бегу следом, шарю по углам в поисках блестящих находок (id=2)\n"
        "- М&M: завожу пиратский корабль и валю нахуй (id=3)\n"
        "Сначала РАЗРЕШИ КАЖДУЮ заявку"
    )
    response = (
        "Ладно, давайте, блядь, по одному! Детектор рвёт когти в соседнюю комнату, "
        "Чудо за ним, блестяшки. Не тормозите, долбоёбы. [ACTION:INPUT]"
    )

    assert _minimum_resolution_words(source_prompt) == 78
    assert group_resolution_was_noop(source_prompt, response) is True


def test_short_but_concrete_roll_is_not_rejected_as_group_noop():
    source_prompt = (
        "Игроки заявили действия одновременно:\n"
        "- Детектор: осматриваю люк (id=1)\n"
        "- Чудо: ищу следы (id=2)\n"
        "Сначала РАЗРЕШИ КАЖДУЮ заявку"
    )
    response = (
        "На люке следы свежей копоти; чтобы понять, открывали ли его недавно, нужна проверка. "
        "[ACTION:ROLL;TYPE:CHECK;SKILL:Расследование;TARGETS:1;DC:10;MODE:NORMAL]"
    )

    assert group_resolution_was_noop(source_prompt, response) is False


def test_action_extraction_does_not_treat_instructions_as_player_intent():
    from AI.dnd_group_progress import _group_action_block

    prompt = (
        "Игроки заявили действия одновременно:\n- Алиса: ем яблоко (id=1)\n"
        "Сначала РАЗРЕШИ КАЖДУЮ заявку. ОСОБЕННО для осмотра, поиска, прислушивания."
    )
    assert _group_action_block(prompt) == "- Алиса: ем яблоко (id=1)"
    assert inspection_was_deferred(prompt, "Думайте. [ACTION:INPUT]") is False


def test_individual_continuation_resets_group_streak():
    from AI.dnd_group_progress import _update_streak

    session = SimpleNamespace(group_input_streak=2)
    _update_streak(session, {"source_request_kind": "ROLL_CONTINUATION"}, "[ACTION:INPUT;TARGETS:2]")
    assert session.group_input_streak == 0
