from pathlib import Path

from tests import test_smoke_imports  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


def test_telephone_finish_button_is_distinct_from_regular_crocodile():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    # The ordinary Crocodile keeps the compact red flag button in the source.
    assert 'aria-label="Завершить рисунок">🏁 Закончить</button>' in source

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


def test_finish_requires_confirmation():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "tg.showConfirm(message, (ok) => { if (ok) onConfirm(); });" in source
    assert "if (window.confirm(message)) onConfirm();" in source
    assert "Точно закончить игру? Слово покажут всем, раунд завершится." in source
    assert "Завершить ход и передать рисунок дальше?" in source
    finish_body = source.split("window.finish = () => {", 1)[1].split("\n  };", 1)[0]
    emit_pos = finish_body.index('socket.emit("final_frame"')
    assert finish_body.index("confirmAction(question") < emit_pos
