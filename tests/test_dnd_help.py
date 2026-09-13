from prompts import HELP_DICT, HELP_TEXT


def test_dnd_help_lists_current_commands():
    section = HELP_DICT["creative"]

    assert "<code>упупа днд</code>" in section
    assert "<code>днд</code>" in section
    assert "<code>днд старт</code>" in section
    assert "<code>герой</code>" in section
    assert "<code>днд герой</code>" in section
    assert "<code>инвентарь</code>" in section
    assert "<code>днд инвентарь</code>" in section
    assert "<code>днд связи</code>" in section
    assert "<code>днд сюжет</code>" in section
    assert "<code>днд конец</code>" in section
    assert "работают и между партиями" in section


def test_dnd_help_stops_advertising_replaced_triggers():
    section = HELP_DICT["creative"]

    assert "<code>мой герой</code>" not in section
    assert "<code>наши знакомые</code>" not in section
    assert "<code>что происходит?</code>" not in section
    assert "<code>упупа заверши историю</code>" not in section
    assert "<code>упупа закончи историю</code>" not in section


def test_exported_full_help_contains_dnd_state_commands():
    assert "<code>днд герой</code>" in HELP_TEXT
    assert "<code>днд связи</code>" in HELP_TEXT
    assert "<code>днд сюжет</code>" in HELP_TEXT
