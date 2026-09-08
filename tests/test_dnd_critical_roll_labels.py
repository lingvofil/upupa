from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from app import bootstrap


def test_critical_dnd_roll_note_labels_extremes():
    assert bootstrap._critical_dnd_roll_note(1) == "КРИТИЧЕСКАЯ НЕУДАЧА"
    assert bootstrap._critical_dnd_roll_note(20) == "КРИТИЧЕСКАЯ УДАЧА"
    assert bootstrap._critical_dnd_roll_note(2) is None
    assert bootstrap._critical_dnd_roll_note(19) is None


def test_dnd_roll_labels_are_configured_on_runtime(monkeypatch):
    original = dnd._natural_roll_note
    monkeypatch.setattr(bootstrap, "_dnd_roll_labels_configured", False)
    try:
        bootstrap._configure_dnd_roll_labels()
        assert dnd._natural_roll_note(1) == "КРИТИЧЕСКАЯ НЕУДАЧА"
        assert dnd._natural_roll_note(20) == "КРИТИЧЕСКАЯ УДАЧА"
    finally:
        dnd._natural_roll_note = original
