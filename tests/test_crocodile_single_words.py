from pathlib import Path

from tests import test_smoke_imports  # noqa: F401
from games import crocodile
from games import crocodile_persistence as persistence
from games import crocodile_single_words as single_words


def test_picker_filters_out_multiword_entries(monkeypatch):
    monkeypatch.setattr(
        crocodile,
        "_load_words",
        lambda: ["квантовый компьютер", "капибара", "слон в лавке", "самолёт"],
    )
    monkeypatch.setattr(persistence, "_load_word_history", lambda: [])
    written = []
    monkeypatch.setattr(persistence, "_write_word_history", lambda used: written.append(list(used)))
    monkeypatch.setattr(single_words.random, "choice", lambda available: available[0])

    picked = single_words.pick_single_crocodile_word()

    assert picked == "капибара"
    assert len(picked.split()) == 1
    assert written == [["капибара"]]


def test_old_phrase_history_is_discarded_from_single_word_cycle(monkeypatch):
    monkeypatch.setattr(
        crocodile,
        "_load_words",
        lambda: ["кот", "квантовый компьютер", "дом"],
    )
    monkeypatch.setattr(
        persistence,
        "_load_word_history",
        lambda: ["квантовый компьютер", "кот"],
    )
    written = []
    monkeypatch.setattr(persistence, "_write_word_history", lambda used: written.append(list(used)))
    monkeypatch.setattr(single_words.random, "choice", lambda available: available[0])

    assert single_words.pick_single_crocodile_word() == "дом"
    assert written == [["кот", "дом"]]


def test_runtime_installs_single_word_picker_before_crocodile_restore():
    root = Path(__file__).resolve().parents[1]
    runtime_source = (root / "games" / "crocodile_runtime.py").read_text(encoding="utf-8")
    bootstrap_source = (root / "app" / "bootstrap.py").read_text(encoding="utf-8")

    assert "configure_crocodile_single_words()" in runtime_source
    configure_pos = bootstrap_source.index("configure_crocodile_runtime()")
    restore_pos = bootstrap_source.index("restore_crocodile_sessions()")
    assert configure_pos < restore_pos
