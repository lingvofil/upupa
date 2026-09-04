from games import crocodile_likes


def test_register_like_allows_only_one_like_per_user_and_message():
    registry = {}
    key = crocodile_likes._message_key(-100123, 456)

    added, count = crocodile_likes._register_like(
        registry,
        key=key,
        user_id=10,
        current_count=0,
    )
    assert added is True
    assert count == 1

    added, count = crocodile_likes._register_like(
        registry,
        key=key,
        user_id=10,
        current_count=1,
    )
    assert added is False
    assert count == 1

    added, count = crocodile_likes._register_like(
        registry,
        key=key,
        user_id=11,
        current_count=1,
    )
    assert added is True
    assert count == 2


def test_register_like_preserves_existing_counter_for_predeployment_messages():
    registry = {}
    key = crocodile_likes._message_key(-100123, 789)

    added, count = crocodile_likes._register_like(
        registry,
        key=key,
        user_id=20,
        current_count=7,
    )

    assert added is True
    assert count == 8
    assert registry[key]["base_count"] == 7


def test_like_registry_round_trip_preserves_user_ids(tmp_path, monkeypatch):
    likes_file = tmp_path / "crocodile_likes.json"
    monkeypatch.setattr(crocodile_likes, "LIKES_FILE", likes_file)

    registry = {
        "-100123:456": {
            "base_count": 2,
            "users": [10, 11],
        }
    }
    crocodile_likes._save_registry(registry)

    assert crocodile_likes._load_registry() == registry
