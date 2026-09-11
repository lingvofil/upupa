from prompts import HELP_DICT, HELP_TEXT


def test_dnd_help_lists_start_alias_and_state_commands():
    section = HELP_DICT["creative"]

    assert "<code>упупа днд</code>" in section
    assert "<code>упупа начни историю</code>" in section
    assert "<code>мой герой</code>" in section
    assert "<code>инвентарь</code>" in section
    assert "<code>наши знакомые</code>" in section
    assert "<code>что происходит?</code>" in section
    assert "работают и между партиями" in section


def test_exported_full_help_contains_dnd_state_commands():
    assert "<code>мой герой</code>" in HELP_TEXT
    assert "<code>упупа днд</code>" in HELP_TEXT
