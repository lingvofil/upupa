import json

from tests import test_smoke_imports  # noqa: F401
from features import crocodile_archive
from games import crocodile
from games import crocodile_likes
from games import crocodile_persistence as persistence


def test_runtime_score_save_uses_configured_crash_safe_path(monkeypatch, tmp_path):
    scores_path = tmp_path / "crocodile_scores.json"
    monkeypatch.setattr(persistence, "CROCODILE_SCORES_PATH", scores_path)
    monkeypatch.setattr(
        crocodile,
        "_scores",
        {"-42": {"7": {"pts": 3, "name": "Игрок"}}},
    )

    persistence.configure_crocodile_runtime()
    crocodile._scores_save()

    assert json.loads(scores_path.read_text(encoding="utf-8")) == crocodile._scores
    assert not list(tmp_path.glob(".crocodile_scores.json.*.tmp"))


def test_like_registry_save_uses_crash_safe_repository(monkeypatch, tmp_path):
    path = tmp_path / "crocodile_likes.json"
    monkeypatch.setattr(crocodile_likes, "LIKES_FILE", path)
    registry = {"-42:10": {"base_count": 1, "users": [7, 8]}}

    crocodile_likes._save_registry(registry)

    assert json.loads(path.read_text(encoding="utf-8")) == registry
    assert not list(tmp_path.glob(".crocodile_likes.json.*.tmp"))


def test_gallery_manifest_save_uses_crash_safe_repository(monkeypatch, tmp_path):
    path = tmp_path / "gallery" / "manifest.json"
    monkeypatch.setattr(crocodile_archive, "MANIFEST_PATH", path)
    rows = [{"chat_id": "-42", "file": "drawing.jpg", "word": "ёж"}]

    crocodile_archive._save(rows)

    assert json.loads(path.read_text(encoding="utf-8")) == rows
    assert not list(path.parent.glob(".manifest.json.*.tmp"))
