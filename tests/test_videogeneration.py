"""Юнит-тесты логики видеогенерации (без обращений к API)."""
from tests import test_smoke_imports  # noqa: F401  (env + моки)

from AI import videogeneration as vg


def test_daily_limit_per_chat():
    vg._usage.clear()
    chat = 111
    for _ in range(vg.DAILY_LIMIT_PER_CHAT):
        assert vg._check_and_count_limit(chat)
    assert not vg._check_and_count_limit(chat), "лимит должен сработать"
    assert vg._check_and_count_limit(222), "другой чат не должен зависеть от первого"
    vg._usage.clear()


def test_extract_prompt():
    assert vg._extract_prompt("упупа сними кота в космосе", "упупа сними") == "кота в космосе"
    assert vg._extract_prompt("Упупа сними ЗАКАТ", "упупа сними") == "ЗАКАТ"
    assert vg._extract_prompt("оживи", "оживи") == ""
    assert vg._extract_prompt("оживи как в кино", "оживи") == "как в кино"
