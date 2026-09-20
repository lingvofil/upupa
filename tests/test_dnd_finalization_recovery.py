import asyncio
import copy
from types import SimpleNamespace

import pytest

from AI import dnd_campaign as campaign
from AI import dnd_finalization_recovery as finalization
from AI import dnd_result_recovery as recovery


def _history_accessor(campaign_obj):
    def chat_history(chat_id, create=False):
        chats = campaign_obj._archive.setdefault("chats", {})
        key = str(int(chat_id))
        if create:
            return chats.setdefault(key, {"players": {}, "campaigns": []})
        return chats.get(key, {"players": {}, "campaigns": []})
    return chat_history


def test_archive_transaction_commits_once_and_replay_skips_all_inner_wrappers():
    campaign_obj = SimpleNamespace(_archive={"chats": {}}, _save_archive=None)
    campaign_obj._chat_history = _history_accessor(campaign_obj)
    deferred_saves = []
    archive_saves = []
    inner_calls = []
    final = {"completion_id": "-100:result-7"}

    def initial_save(_dnd):
        deferred_saves.append(True)
        return True

    campaign_obj._save_archive = initial_save

    def original_archive(_dnd, session, finale, epilogue):
        inner_calls.append((finale, epilogue))
        chat = campaign_obj._chat_history(session.chat_id, True)
        chat["campaigns"].append({"finale": finale, "epilogue": epilogue})
        session.growth_created_offer_tokens = [(1, "offer-token")]
        # Simulate several archive wrappers saving along the chain.
        campaign_obj._save_archive(_dnd)
        campaign_obj._save_archive(_dnd)

    def archive_save(_dnd):
        archive_saves.append(copy.deepcopy(campaign_obj._archive))
        return True

    dnd = SimpleNamespace(persist_dnd_sessions=lambda: None)
    session = SimpleNamespace(chat_id=-100, growth_created_offer_tokens=[])
    state = lambda _session, create=False: final

    first = finalization._archive_once(
        campaign_obj,
        dnd,
        session,
        "финал",
        "эпилог",
        original_archive=original_archive,
        archive_save=archive_save,
        finalization_state=state,
    )

    assert len(inner_calls) == 1
    assert deferred_saves == []
    assert len(archive_saves) == 1
    assert first["completion_id"] == "-100:result-7"
    assert first["growth_created_offer_tokens"] == [[1, "offer-token"]]
    assert final["archive_done"] is True

    # Simulate the pre-parse session snapshot being restored on replay.
    session.growth_created_offer_tokens = []
    second = finalization._archive_once(
        campaign_obj,
        dnd,
        session,
        "финал",
        "эпилог",
        original_archive=original_archive,
        archive_save=archive_save,
        finalization_state=state,
    )

    assert second is first
    assert len(inner_calls) == 1
    assert len(archive_saves) == 1
    assert len(campaign_obj._chat_history(-100)["campaigns"]) == 1
    assert session.growth_created_offer_tokens == [(1, "offer-token")]


def test_archive_transaction_rolls_back_memory_when_atomic_save_fails():
    original_archive_data = {"chats": {}}
    campaign_obj = SimpleNamespace(
        _archive=copy.deepcopy(original_archive_data),
        _save_archive=lambda _dnd: True,
    )
    campaign_obj._chat_history = _history_accessor(campaign_obj)
    final = {"completion_id": "-101:result-8"}

    def original_archive(_dnd, session, finale, epilogue):
        chat = campaign_obj._chat_history(session.chat_id, True)
        chat["campaigns"].append({"finale": finale, "epilogue": epilogue})

    with pytest.raises(RuntimeError, match="atomic save failed"):
        finalization._archive_once(
            campaign_obj,
            SimpleNamespace(persist_dnd_sessions=lambda: None),
            SimpleNamespace(chat_id=-101, growth_created_offer_tokens=[]),
            "финал",
            "эпилог",
            original_archive=original_archive,
            archive_save=lambda _dnd: False,
            finalization_state=lambda _session, create=False: final,
        )

    assert campaign_obj._archive == original_archive_data


def test_finish_cleanup_happens_after_notice_and_final_image():
    order = []
    final = {"final_image_prompt": "нарисуй финал"}
    session = SimpleNamespace(chat_id=-102)
    dnd = SimpleNamespace(
        dnd_sessions={-102: session},
        persist_dnd_sessions=lambda: order.append("persist"),
    )

    def cleanup(chat_id):
        order.append("cleanup")
        dnd.dnd_sessions.pop(chat_id, None)

    dnd.cleanup_session = cleanup

    class Bot:
        async def send_message(self, chat_id, text, **kwargs):
            del chat_id, text, kwargs
            order.append("final_notice")
            return SimpleNamespace(message_id=1, chat=SimpleNamespace(id=-102))

    async def original_finish(_dnd, _bot, _session, _response):
        order.append("original_finish")

    async def image(_bot, _chat_id, _prompt, _filename, _caption, *, deliver_if=None):
        assert deliver_if is not None and deliver_if()
        order.append("final_image")
        return SimpleNamespace(message_id=2, chat=SimpleNamespace(id=-102))

    campaign_obj = SimpleNamespace(_image=image)

    asyncio.run(
        finalization._finish_and_cleanup(
            campaign_obj,
            dnd,
            Bot(),
            session,
            "END",
            original_finish=original_finish,
            finalization_state=lambda _session, create=False: final,
        )
    )

    assert order.index("original_finish") < order.index("final_notice")
    assert order.index("final_notice") < order.index("final_image")
    assert order.index("final_image") < order.index("cleanup")
    assert final["final_notice_done"] is True
    assert final["final_image_done"] is True
    assert final["cleanup_ready"] is True
    assert -102 not in dnd.dnd_sessions


def test_base_finish_reuses_saved_epilogue_on_replay(monkeypatch):
    generated = []
    archived = []
    session = SimpleNamespace(
        chat_id=-103,
        conversation=[],
        pending_generated_result={
            "id": "3:end",
            "text": "финальная сцена [ACTION:END]",
            "phase": recovery.RESULT_PHASE_APPLYING,
            "telegram_effects": [],
        },
        pending_generation_request={},
        generated_result_seq=3,
    )
    dnd = SimpleNamespace(
        persist_dnd_sessions=lambda: None,
        _rewind_session_conversation=lambda _session, _size: False,
    )

    class Bot:
        async def send_message(self, chat_id, text, **kwargs):
            del chat_id, text, kwargs
            return SimpleNamespace(message_id=1)

    monkeypatch.setattr(campaign, "_apply_metadata", lambda _session, response: (response, []))
    monkeypatch.setattr(campaign, "_record_scene", lambda _session, _text: None)

    async def ephemeral(_dnd, _session, _prompt):
        generated.append(True)
        return "Один и тот же эпилог"

    monkeypatch.setattr(campaign, "_ephemeral_generate", ephemeral)
    monkeypatch.setattr(
        campaign,
        "_archive_campaign",
        lambda _dnd, _session, finale, epilogue: archived.append((finale, epilogue)),
    )
    monkeypatch.setattr(campaign, "_final_comic_prompt", lambda _session, ep: "IMAGE:" + ep)

    asyncio.run(campaign._finish(dnd, Bot(), session, "финальная сцена [ACTION:END]"))
    asyncio.run(campaign._finish(dnd, Bot(), session, "финальная сцена [ACTION:END]"))

    state = session.pending_generated_result["finalization"]
    assert generated == [True]
    assert state["epilogue"] == "Один и тот же эпилог"
    assert state["final_image_prompt"] == "IMAGE:Один и тот же эпилог"
    assert archived == [
        ("финальная сцена", "Один и тот же эпилог"),
        ("финальная сцена", "Один и тот же эпилог"),
    ]


def test_durable_photo_effect_is_suppressed_on_replay():
    sent = []
    persisted = []
    session = SimpleNamespace(
        chat_id=-104,
        pending_generated_result={
            "id": "4:end",
            "text": "END",
            "phase": recovery.RESULT_PHASE_APPLYING,
            "telegram_effects": [],
        },
        pending_generation_request={},
        generated_result_seq=4,
    )
    dnd = SimpleNamespace(persist_dnd_sessions=lambda: persisted.append(True))

    class Bot:
        async def send_photo(self, chat_id, photo, **kwargs):
            sent.append((chat_id, photo, kwargs))
            return SimpleNamespace(message_id=77, chat=SimpleNamespace(id=chat_id))

    first = recovery._DurableBotProxy(Bot(), dnd, session)
    first_result = asyncio.run(first.send_photo(-104, object(), caption="финал"))

    second = recovery._DurableBotProxy(Bot(), dnd, session)
    second_result = asyncio.run(second.send_photo(-104, object(), caption="финал"))

    assert first_result.message_id == 77
    assert second_result.message_id == 77
    assert len(sent) == 1
    assert session.pending_generated_result["telegram_effects"][0]["status"] == recovery.EFFECT_DONE
