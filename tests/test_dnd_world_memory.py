from types import SimpleNamespace

import pytest

from AI import dnd_campaign as campaign
from AI import dnd_world_memory as memory


def _session():
    return SimpleNamespace(
        chat_id=100,
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        npc_memory={},
        scene_count=3,
        world_callback_candidate={},
        world_inherited_npc_keys=[],
        world_callback_used=False,
    )


def test_structured_npc_memory_separates_attitude_obligation_wants_and_thread():
    session = _session()
    text = (
        "[NPC:Капитан Ржа;EVENT:герои спасли корабль;AFFECTED:1,2;"
        "ATTITUDE:благодарен, но насторожен;"
        "OBLIGATION_KIND:NPC_OWES_PLAYERS;OBLIGATION:обещал безопасный проход;"
        "WANTS:вернуть украденную карту;UNRESOLVED:Алиса обещала найти матроса]"
    )

    cleaned, notices = memory.apply_world_memory_metadata(
        campaign, session, text, "", []
    )

    assert cleaned == ""
    assert notices == []
    row = session.npc_memory["капитан ржа"]
    assert row["event"] == "герои спасли корабль"
    assert row["attitude"] == "благодарен, но насторожен"
    assert row["obligation_kind"] == "NPC_OWES_PLAYERS"
    assert row["obligation"] == "обещал безопасный проход"
    assert row["wants"] == "вернуть украденную карту"
    assert row["unresolved"] == "Алиса обещала найти матроса"
    assert row["affected_player_ids"] == ["1", "2"]


def test_npc_obligation_can_be_resolved_without_erasing_attitude():
    session = _session()
    session.npc_memory["капитан ржа"] = memory.normalize_npc(
        "Капитан Ржа",
        {
            "name": "Капитан Ржа",
            "attitude": "тепло относится к партии",
            "obligation_kind": "NPC_OWES_PLAYERS",
            "obligation": "обещал лодку",
            "unresolved": "должен передать лодку",
        },
    )
    text = (
        "[NPC:Капитан Ржа;EVENT:передал лодку;"
        "OBLIGATION_KIND:NONE;OBLIGATION:NONE;UNRESOLVED:NONE]"
    )
    memory.apply_world_memory_metadata(campaign, session, text, "", [])
    row = session.npc_memory["капитан ржа"]
    assert row["attitude"] == "тепло относится к партии"
    assert row["obligation_kind"] == "NONE"
    assert row["obligation"] is None
    assert row["unresolved"] is None


def test_callback_candidate_is_marked_used_only_when_same_npc_returns():
    session = _session()
    session.world_callback_candidate = memory.normalize_npc(
        "Капитан Ржа",
        {"name": "Капитан Ржа", "unresolved": "обещал вернуться"},
    )
    memory.apply_world_memory_metadata(
        campaign,
        session,
        "[NPC:Доктор Мох;EVENT:встретили в аптеке]",
        "",
        [],
    )
    assert session.world_callback_used is False

    memory.apply_world_memory_metadata(
        campaign,
        session,
        "[NPC:Капитан Ржа;EVENT:пришёл требовать обещанное]",
        "",
        [],
    )
    assert session.world_callback_used is True


def test_inherited_old_npcs_are_hidden_except_single_callback_candidate():
    session = _session()
    session.npc_memory = {
        "капитан ржа": memory.normalize_npc(
            "Капитан Ржа", {"name": "Капитан Ржа", "unresolved": "ищет карту"}
        ),
        "доктор мох": memory.normalize_npc(
            "Доктор Мох", {"name": "Доктор Мох", "event": "лечил партию"}
        ),
        "новый торговец": memory.normalize_npc(
            "Новый торговец", {"name": "Новый торговец", "event": "встретили сегодня"}
        ),
    }
    session.world_inherited_npc_keys = ["капитан ржа", "доктор мох"]
    session.world_callback_candidate = memory.normalize_npc(
        "Капитан Ржа", {"name": "Капитан Ржа", "unresolved": "ищет карту"}
    )

    context = memory._filtered_npc_context(session)

    assert "Капитан Ржа" in context
    assert "Новый торговец" in context
    assert "Доктор Мох" not in context


def test_legacy_backfill_does_not_clear_existing_structured_debt():
    archive = {
        "chats": {
            "100": {
                "world_npcs": {
                    "капитан ржа": memory.normalize_npc(
                        "Капитан Ржа",
                        {
                            "name": "Капитан Ржа",
                            "attitude": "благодарен",
                            "obligation_kind": "NPC_OWES_PLAYERS",
                            "obligation": "обещал проводника",
                        },
                    )
                },
                "campaigns": [
                    {
                        "completed_at": "2026-01-01T00:00:00+00:00",
                        "npc_memory": {
                            "капитан ржа": {
                                "name": "Капитан Ржа",
                                "event": "помог у причала",
                                "notes": ["знает героев"],
                            }
                        },
                    }
                ],
            }
        }
    }

    memory.backfill_world_npcs(archive)

    row = archive["chats"]["100"]["world_npcs"]["капитан ржа"]
    assert row["obligation_kind"] == "NPC_OWES_PLAYERS"
    assert row["obligation"] == "обещал проводника"
    assert row["attitude"] == "благодарен"
    assert row["event"] == "помог у причала"


def test_callback_ranking_prefers_open_threads_and_penalizes_recent_repeat():
    class FakeCampaign:
        @staticmethod
        def _chat_history(chat_id, create=False):
            return {
                "campaigns": [{}, {}, {}],
                "world_npcs": {
                    "a": memory.normalize_npc(
                        "А",
                        {
                            "name": "А",
                            "unresolved": "обещание не закрыто",
                            "last_seen_campaign": 3,
                            "callback_count": 0,
                        },
                    ),
                    "b": memory.normalize_npc(
                        "Б",
                        {
                            "name": "Б",
                            "unresolved": "старый долг",
                            "last_seen_campaign": 3,
                            "last_callback_campaign": 3,
                            "callback_count": 2,
                        },
                    ),
                    "c": memory.normalize_npc(
                        "В",
                        {
                            "name": "В",
                            "event": "однажды встретились",
                            "last_seen_campaign": 2,
                        },
                    ),
                },
            }

    rows = memory._callback_candidates(FakeCampaign, 100)
    assert rows[0]["name"] == "А"
    assert rows[-1]["name"] != "Б"


@pytest.mark.asyncio
async def test_ai_selector_can_decline_irrelevant_callbacks():
    class FakeCampaign:
        @staticmethod
        def _chat_history(chat_id, create=False):
            return {
                "campaigns": [{}],
                "world_npcs": {
                    "капитан": memory.normalize_npc(
                        "Капитан",
                        {
                            "name": "Капитан",
                            "unresolved": "ждёт возврата морской карты",
                            "last_seen_campaign": 1,
                        },
                    )
                },
            }

        @staticmethod
        def _latest_campaign(chat_id):
            return {"finale": "ушли из порта", "epilogue": "капитан остался ждать"}

        @staticmethod
        async def _ephemeral_generate(dnd, session, prompt):
            assert "КАНДИДАТЫ" in prompt
            return "NONE"

    result = await memory.select_callback_candidate(
        FakeCampaign,
        SimpleNamespace(),
        _session(),
        "приключение на лунной сыроварне",
        continuation=False,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_ai_selector_returns_exactly_one_relevant_callback():
    class FakeCampaign:
        @staticmethod
        def _chat_history(chat_id, create=False):
            return {
                "campaigns": [{}, {}],
                "world_npcs": {
                    "капитан": memory.normalize_npc(
                        "Капитан",
                        {
                            "name": "Капитан",
                            "obligation_kind": "NPC_OWES_PLAYERS",
                            "obligation": "обещал провести через порт",
                            "last_seen_campaign": 2,
                        },
                    ),
                    "аптекарь": memory.normalize_npc(
                        "Аптекарь",
                        {
                            "name": "Аптекарь",
                            "event": "продал мазь",
                            "last_seen_campaign": 1,
                        },
                    ),
                },
            }

        @staticmethod
        def _latest_campaign(chat_id):
            return None

        @staticmethod
        async def _ephemeral_generate(dnd, session, prompt):
            return "1"

    result = await memory.select_callback_candidate(
        FakeCampaign,
        SimpleNamespace(),
        _session(),
        "контрабанда через тот же порт",
        continuation=False,
    )
    assert result["name"] == "Капитан"


def test_archive_updates_world_memory_and_callback_frequency():
    store = {
        "players": {},
        "campaigns": [],
        "world_npcs": {},
    }

    class FakeCampaign:
        @staticmethod
        def _chat_history(chat_id, create=False):
            return store

        @staticmethod
        def _save_archive(dnd):
            dnd.saved += 1

    session = _session()
    session.npc_memory["капитан ржа"] = memory.normalize_npc(
        "Капитан Ржа",
        {
            "name": "Капитан Ржа",
            "attitude": "благодарен",
            "obligation_kind": "NPC_OWES_PLAYERS",
            "obligation": "обещал помощь",
        },
    )
    session.world_callback_candidate = memory.normalize_npc(
        "Капитан Ржа", {"name": "Капитан Ржа", "obligation": "обещал помощь"}
    )
    session.world_callback_used = True

    def original_archive(dnd, session, finale, epilogue):
        store["campaigns"].append(
            {"completed_at": "2026-09-20T00:00:00+00:00", "npc_memory": session.npc_memory}
        )

    dnd = SimpleNamespace(saved=0)
    memory._archive_world_memory(
        FakeCampaign,
        dnd,
        session,
        original_archive,
        "финал",
        "эпилог",
    )

    row = store["world_npcs"]["капитан ржа"]
    assert row["attitude"] == "благодарен"
    assert row["obligation"] == "обещал помощь"
    assert row["callback_count"] == 1
    assert row["last_callback_campaign"] == 1
    assert store["campaigns"][-1]["world_callback_used"] is True


def test_render_npc_lines_exposes_structured_memory_without_mixing_fields():
    lines = memory.render_npc_lines(
        {
            "капитан": memory.normalize_npc(
                "Капитан",
                {
                    "name": "Капитан",
                    "event": "спасён героями",
                    "attitude": "искренне благодарен",
                    "obligation_kind": "NPC_OWES_PLAYERS",
                    "obligation": "должен услугу",
                    "wants": "вернуть судно",
                    "unresolved": "пропал матрос",
                    "affected_player_ids": ["1"],
                },
            )
        }
    )
    text = "\n".join(lines)
    assert "отношение: искренне благодарен" in text
    assert "должен героям: должен услугу" in text
    assert "хочет: вернуть судно" in text
    assert "не закрыто: пропал матрос" in text
    assert "касается ID 1" in text
