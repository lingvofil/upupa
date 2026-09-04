from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GAMES_HANDLER = ROOT / "handlers" / "games.py"


def test_crocodile_final_buttons_are_routed_to_game_handler():
    source = GAMES_HANDLER.read_text(encoding="utf-8")

    assert 'F.data.startswith("cr_")' in source
    assert '"btn_like"' in source
    assert '"btn_want_draw"' in source
    assert 'if callback.data == "btn_like":' in source
    assert "await crocodile_likes.handle_like_callback(callback)" in source
    assert "await crocodile.handle_callback(callback)" in source
