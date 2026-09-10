from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_campaign as campaign


def _session(chat_id=-1009001):
    return SimpleNamespace(
        chat_id=chat_id,
        mode="participants",
        starter_name="Ведущий",
        lobby_message_id=777,
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
    )


def test_random_profile_contains_all_lightweight_fields():
    profile = campaign._random_profile()
    assert campaign._profile_complete(profile)
    assert set(profile) == set(campaign.PROFILE_STEPS)


def test_plot_parser_always_returns_five_options():
    options = campaign._parse_plot_options("1. Петля во времени\n2. Ограбление обувного\n")
    assert len(options) == 5
    assert options[:2] == ["Петля во времени", "Ограбление обувного"]
    assert len(set(options)) == 5


def test_risk_levels_have_monotonic_difficulty():
    assert [campaign.RISK_DC[x] for x in ("LOW", "MEDIUM", "HIGH", "EXTREME")] == [8, 11, 14, 17]
    assert campaign._extract_risk("ROLL;TYPE:CHECK;RISK:HIGH;MODE:NORMAL") == "HIGH"
    assert campaign._risk_from_response("текст [ACTION:ROLL;TYPE:CHECK;RISK:EXTREME;MODE:NORMAL]") == "EXTREME"


def test_roll_grade_has_four_distinct_story_branches():
    assert campaign._roll_grade_from_prompt("итог: 18. Сложность: 12").startswith("сильный успех")
    assert campaign._roll_grade_from_prompt("итог: 12. Сложность: 12").startswith("успех")
    assert campaign._roll_grade_from_prompt("итог: 10. Сложность: 12").startswith("провал с ценой")
    assert campaign._roll_grade_from_prompt("итог: 5. Сложность: 12").startswith("тяжёлый провал")


def test_metadata_tracks_threat_items_npc_memory_and_reputation():
    session = _session()
    text = (
        "Стража рядом.\n"
        "[THREAT:Подозрение стражи;DELTA:2;CAUSE:разбили витрину]\n"
        "[ITEM:ADD;PLAYER:1;NAME:бронзовый ключ;KIND:artifact]\n"
        "[NPC:Капитан Ржа;EVENT:обманули;NOTE:пообещали груз и сбежали]\n"
        "[REP:ADD;PLAYER:1;TEXT:известна как грабительница порта]\n"
        "[ACTION:INPUT]"
    )
    clean, notices = campaign._apply_metadata(session, text)
    assert "THREAT:" not in clean and "ITEM:" not in clean and "NPC:" not in clean and "REP:" not in clean
    assert "[ACTION:INPUT]" in clean
    assert session.threat["level"] == 2
    assert session.inventories["1"] == [{"name": "бронзовый ключ", "kind": "artifact"}]
    assert session.npc_memory["капитан ржа"]["event"] == "обманули"
    assert session.reputations["1"] == ["известна как грабительница порта"]
    assert any("■■□□□□ 2/6" in notice for notice in notices)


def test_campaign_state_roundtrip_keeps_persistent_state():
    source = _session()
    campaign._ensure(source)
    source.character_profiles["1"] = campaign._random_profile()
    source.inventories["1"] = [{"name": "ключ", "kind": "item"}]
    source.npc_memory["сторож"] = {"name": "Сторож", "event": "помогли", "notes": ["вытащили из ямы"]}
    source.reputations["1"] = ["спаситель двора"]
    source.threat = {"name": "Буря", "level": 3, "max": 6, "history": []}
    source.selected_plot = "Заплыв в Марианскую впадину"
    payload = campaign._state(source)
    restored = _session()
    campaign._restore_state(restored, payload)
    assert restored.character_profiles == source.character_profiles
    assert restored.inventories == source.inventories
    assert restored.npc_memory == source.npc_memory
    assert restored.reputations == source.reputations
    assert restored.threat["level"] == 3
    assert restored.selected_plot == "Заплыв в Марианскую впадину"


def test_delay_advances_existing_threat_after_150_seconds(monkeypatch):
    session = _session()
    campaign._ensure(session)
    session.threat = {"name": "Шум", "level": 1, "max": 6, "history": []}
    session.action_opened_at = 100.0
    monkeypatch.setattr(campaign.time, "time", lambda: 251.0)
    notice = campaign._delay_threat(session)
    assert session.threat["level"] == 2
    assert "2/6" in notice
