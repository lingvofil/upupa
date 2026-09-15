from pathlib import Path

from tests import test_smoke_imports  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


def test_telephone_finish_button_is_distinct_from_regular_crocodile():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    # The ordinary Crocodile keeps the compact red flag button in the source.
    assert 'aria-label="Завершить рисунок">🏁</button>' in source

    # Telephone rooms are identified by their established t{step} room suffix
    # and get an explicit green turn-completion action instead of the red flag.
    assert 'return /_t\\d+$/.test(String(roomId || ""));' in source
    assert 'finishButton.textContent = "✅ Завершить ход";' in source
    assert 'finishButton.setAttribute("aria-label", "Завершить свой ход")' in source
    assert 'finishButton.classList.remove("btn-red")' in source
    assert 'finishButton.style.background = "#34c759"' in source
    assert "configureTurnFinishButton();" in source


def test_telephone_text_step_uses_same_completion_wording():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    assert 'submit.textContent = "✅ Завершить ход";' in source
    assert 'submit.textContent = "Готово ✓";' not in source
