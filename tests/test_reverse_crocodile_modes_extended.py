import asyncio
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_themed_history_has_no_repeats_until_exhaustion_and_survives_disk(tmp_path, monkeypatch):
    from games import reverse_crocodile_history as history

    monkeypatch.setattr(history, "HISTORY_PATH", tmp_path / "reverse_history.json")
    monkeypatch.setattr(history, "HISTORY_CARRYOVER", 1)
    monkeypatch.setattr(history.random, "choice", lambda seq: seq[0])
    pool = ("Один", "Два", "Три")

    first_cycle = [history.pick_from_pool("movie", pool) for _ in range(len(pool))]
    assert len(set(first_cycle)) == len(pool)

    payload = json.loads(history.HISTORY_PATH.read_text(encoding="utf-8"))
    assert payload["version"] == history.HISTORY_VERSION
    assert len(payload["modes"]["movie"]) == len(pool)
    assert history._load_history()["movie"] == payload["modes"]["movie"]

    next_cycle = history.pick_from_pool("movie", pool)
    assert next_cycle in pool
    assert next_cycle != first_cycle[-1]


def test_themed_history_is_isolated_between_modes(tmp_path, monkeypatch):
    from games import reverse_crocodile_history as history

    monkeypatch.setattr(history, "HISTORY_PATH", tmp_path / "reverse_history.json")
    monkeypatch.setattr(history.random, "choice", lambda seq: seq[0])
    pool = ("Общий вариант", "Другой вариант")

    assert history.pick_from_pool("movie", pool) == "Общий вариант"
    assert history.pick_from_pool("cartoon", pool) == "Общий вариант"
    assert history.pick_from_pool("movie", pool) == "Другой вариант"


def test_proverb_and_saying_callbacks_map_to_one_internal_mode():
    from games.reverse_crocodile_phrases import mode_label, normalize_mode

    assert normalize_mode("proverb") == "proverbs"
    assert normalize_mode("saying") == "proverbs"
    assert normalize_mode("proverbs") == "proverbs"
    assert mode_label("proverb") == "Пословицы/поговорки"
    assert mode_label("saying") == "Пословицы/поговорки"


def test_pun_mode_uses_common_reverse_image_prompt_without_answer_text(monkeypatch):
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_modes as modes

    candidate = SimpleNamespace(first="слон", second="носорог", result="слоносорог")
    clue = "Огромное животное с нелепым рогом и хоботом срослось в один объект."
    prompt = modes._mode_image_prompt(clue)

    assert reverse.IMAGE_STYLE in prompt
    assert candidate.result.casefold() not in prompt.casefold()
    assert "читаемого текста" in prompt.casefold()

    build_clue = AsyncMock(return_value=clue)
    common_pipeline = AsyncMock(return_value=b"image")
    monkeypatch.setattr(modes, "_build_pun_clue", build_clue)
    monkeypatch.setattr(modes, "_generate_image_from_clue", common_pipeline)

    assert asyncio.run(modes._generate_pun_image(candidate, "-42")) == b"image"
    build_clue.assert_awaited_once_with(candidate, "-42")
    common_pipeline.assert_awaited_once_with(
        clue,
        "-42",
        "pun",
        forbidden_secret=candidate.result,
    )


def test_progressive_reveal_and_minute_bump_use_same_current_frame(monkeypatch):
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_modes as modes

    source = Image.new("RGB", (100, 120), (12, 34, 56))
    buf = BytesIO()
    source.save(buf, format="JPEG")
    chat_id = "-77"
    session = {
        "word": "лабиринт",
        "mode": "reveal",
        "image": buf.getvalue(),
        "revealed_tiles": 3,
        "reveal_order": list(range(modes.REVEAL_COLS * modes.REVEAL_ROWS)),
        "hints": 0,
        "revealed_positions": set(),
        "last_hint_at": None,
    }
    reverse.games[chat_id] = session
    send_hint = AsyncMock(return_value=True)
    send_round = AsyncMock(return_value=True)
    monkeypatch.setattr(reverse, "_send_next_hint", send_hint)
    monkeypatch.setattr(modes, "_send_round", send_round)

    try:
        early = asyncio.run(modes._visible_image(session))
        session["revealed_tiles"] = 9
        later = asyncio.run(modes._visible_image(session))
        assert early != later
        assert asyncio.run(modes._run_round_tick(chat_id, session)) is True
    finally:
        reverse.games.pop(chat_id, None)

    send_hint.assert_awaited_once_with(chat_id, session)
    send_round.assert_awaited_once_with(chat_id, session, replace=True)
