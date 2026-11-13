"""Юнит-тесты логики видеогенерации (без обращений к API)."""
from tests import test_smoke_imports  # noqa: F401  (env + моки)

from AI import videogeneration as vg


def test_expert_quota_is_two_and_other_chats_share_one():
    vg._usage.clear()
    expert = vg.SPECIAL_CHAT_ID
    other_a, other_b = expert - 1, expert - 2

    assert vg._check_and_count_limit(expert)
    assert vg._check_and_count_limit(expert)
    assert not vg._check_and_count_limit(expert)
    assert vg._check_and_count_limit(other_a)
    assert not vg._check_and_count_limit(other_b)
    assert not vg._check_and_count_limit(other_a)
    assert sum(vg._usage.values()) == 3
    vg._usage.clear()


def test_other_chats_do_not_consume_expert_reservation():
    vg._usage.clear()
    assert vg._check_and_count_limit(vg.SPECIAL_CHAT_ID - 1)
    assert not vg._check_and_count_limit(vg.SPECIAL_CHAT_ID - 2)
    assert vg._check_and_count_limit(vg.SPECIAL_CHAT_ID)
    assert vg._check_and_count_limit(vg.SPECIAL_CHAT_ID)
    assert not vg._check_and_count_limit(vg.SPECIAL_CHAT_ID)
    vg._usage.clear()


def test_quota_resets_on_next_day(monkeypatch):
    vg._usage.clear()
    monkeypatch.setattr(vg, "_quota_day", lambda: "2026-11-13")
    assert vg._check_and_count_limit(vg.SPECIAL_CHAT_ID - 1)
    assert not vg._check_and_count_limit(vg.SPECIAL_CHAT_ID - 2)

    monkeypatch.setattr(vg, "_quota_day", lambda: "2026-11-14")
    assert vg._check_and_count_limit(vg.SPECIAL_CHAT_ID - 2)
    assert len(vg._usage) == 1
    vg._usage.clear()


def test_limit_exceeded_messages_identify_shared_pool():
    assert "2/день" in vg._limit_exceeded_text(vg.SPECIAL_CHAT_ID)
    outside = vg._limit_exceeded_text(vg.SPECIAL_CHAT_ID - 1)
    assert "1/день" in outside
    assert "все остальные чаты" in outside


def test_extract_prompt():
    assert vg._extract_prompt("упупа сними кота в космосе", "упупа сними") == "кота в космосе"
    assert vg._extract_prompt("Упупа сними ЗАКАТ", "упупа сними") == "ЗАКАТ"
    assert vg._extract_prompt("оживи", "оживи") == ""
    assert vg._extract_prompt("оживи как в кино", "оживи") == "как в кино"
    assert vg._extract_prompt("Упупа оживи улыбку", "упупа оживи", "оживи") == "улыбку"
    assert vg._extract_prompt("Упупа, оживи улыбку", "упупа, оживи", "упупа оживи", "оживи") == "улыбку"


def test_animate_handler_accepts_optional_upupa_prefix():
    from handlers.video import _is_animate_command

    assert _is_animate_command("оживи")
    assert _is_animate_command("Упупа оживи")
    assert _is_animate_command("Упупа, оживи плавно")
    assert not _is_animate_command("упупа сними")


def test_order_models_preference():
    names = ["veo", "p-video-720p", "neizvestnaya-model", "wan-fast", "ltx-2"]
    out = vg._order_models(names)
    assert out[0] == "ltx-2", "самая дешёвая модель должна идти первой"
    assert out[1] == "wan-fast"
    assert out[-1] == "neizvestnaya-model"


def test_extract_video_models_tolerates_shapes():
    catalog = [
        {"name": "p-video-720p", "video_capabilities": {"end_frame": False}},
        {"name": "flux", "output_modalities": ["image"]},
        {"id": "veo", "output_modalities": ["video"]},
        {"name": "seedance", "type": "video"},
        "мусор",
    ]
    assert vg._extract_video_models(catalog) == ["p-video-720p", "veo", "seedance"]
    assert vg._extract_video_models({"models": catalog}) == ["p-video-720p", "veo", "seedance"]
    assert vg._extract_video_models("совсем мусор") == []


