from pathlib import Path

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_runtime


ROOT = Path(__file__).resolve().parents[1]


def test_critical_dnd_roll_note_labels_extremes():
    assert dnd_runtime._critical_dnd_roll_note(1) == "КРИТИЧЕСКАЯ НЕУДАЧА"
    assert dnd_runtime._critical_dnd_roll_note(20) == "КРИТИЧЕСКАЯ УДАЧА"
    assert dnd_runtime._critical_dnd_roll_note(2) is None
    assert dnd_runtime._critical_dnd_roll_note(19) is None


def test_dnd_roll_label_composition_stays_out_of_application_bootstrap():
    bootstrap_source = (ROOT / "app" / "bootstrap.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "_natural_roll_note" not in bootstrap_source
    assert "dnd._natural_roll_note = _critical_dnd_roll_note" in runtime_source
