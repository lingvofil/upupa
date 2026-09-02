from tests import test_smoke_imports  # noqa: F401
from games import crocodile


def test_leaderboard_uses_plain_non_mentioning_names():
    chat_id = "-1001"
    original_scores = crocodile._scores
    try:
        crocodile._scores = {
            chat_id: {
                "42": {"pts": 3, "name": "@alice <Boss>"},
            }
        }

        rendered = crocodile.format_leaderboard(chat_id)

        assert "tg://user" not in rendered
        assert "<a " not in rendered
        assert "@alice" not in rendered
        assert "@\u200balice &lt;Boss&gt; — <b>3</b>" in rendered
    finally:
        crocodile._scores = original_scores
