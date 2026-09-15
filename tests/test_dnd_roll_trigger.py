from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd


def _message(text):
    return SimpleNamespace(text=text)


def test_roll_command_only_matches_standalone_kidayu():
    assert dnd._is_roll_command(_message("кидаю")) is True
    assert dnd._is_roll_command(_message(" КИДАЮ ")) is True
    assert dnd._is_roll_command(_message("кидаю идею")) is False
    assert dnd._is_roll_command(_message("я кидаю")) is False
    assert dnd._is_roll_command(_message("покидаю")) is False
    assert dnd._is_roll_command(_message(None)) is False
