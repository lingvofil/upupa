import json
from pathlib import Path

from scripts import recover_crocodile_scores as recovery


CHAT_ID = "-1001707530786"
EXPECTED = {
    "Чудо в Стране Алис 🍀": 20,
    "Alina": 16,
    "Детектор": 9,
}


def _table(detector_points=9):
    return {
        "101": {"pts": 20, "name": "Чудо в Стране Алис 🍀"},
        "102": {"pts": 16, "name": "Alina"},
        "103": {"pts": detector_points, "name": "Детектор"},
        "999": {"pts": 1, "name": "Другой игрок"},
    }


def _write_scores(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_find_matching_backup_uses_newest_exact_signature(tmp_path):
    backup_root = tmp_path / "backups"
    older = backup_root / "20260905T120000Z-old" / "crocodile_scores.json"
    newer_bad = backup_root / "20260905T130000Z-bad" / "crocodile_scores.json"
    newest = backup_root / "20260905T140000Z-good" / "crocodile_scores.json"

    _write_scores(older, {CHAT_ID: _table()})
    _write_scores(newer_bad, {CHAT_ID: _table(detector_points=1)})
    _write_scores(newest, {CHAT_ID: _table()})

    match = recovery.find_matching_backup(backup_root, CHAT_ID, EXPECTED)

    assert match is not None
    path, table = match
    assert path == newest
    assert table == _table()


def test_recovery_replaces_only_target_chat_and_keeps_preimage(tmp_path):
    backup_root = tmp_path / "backups"
    candidate = backup_root / "20260905T120000Z-good" / "crocodile_scores.json"
    recovered_table = _table()
    _write_scores(candidate, {CHAT_ID: recovered_table})

    scores_file = tmp_path / "app" / "crocodile_scores.json"
    current_other_chat = {"201": {"pts": 7, "name": "Сосед"}}
    _write_scores(
        scores_file,
        {
            CHAT_ID: {"777": {"pts": 1, "name": "Сброшенный рейтинг"}},
            "-100999": current_other_chat,
        },
    )
    marker = backup_root / ".recovery.done"

    selected = recovery.recover_scoreboard(
        backup_root=backup_root,
        scores_file=scores_file,
        chat_id=CHAT_ID,
        expected=EXPECTED,
        marker_file=marker,
        apply=True,
    )

    assert selected == candidate
    restored = json.loads(scores_file.read_text(encoding="utf-8"))
    assert restored[CHAT_ID] == recovered_table
    assert restored["-100999"] == current_other_chat
    assert marker.is_file()

    preimages = list(backup_root.glob("manual-crocodile-recovery-*-before.json"))
    assert len(preimages) == 1
    preimage = json.loads(preimages[0].read_text(encoding="utf-8"))
    assert preimage[CHAT_ID]["777"]["pts"] == 1


def test_recovery_refuses_to_change_scores_without_matching_backup(tmp_path):
    backup_root = tmp_path / "backups"
    candidate = backup_root / "20260905T120000Z-bad" / "crocodile_scores.json"
    _write_scores(candidate, {CHAT_ID: _table(detector_points=8)})

    scores_file = tmp_path / "app" / "crocodile_scores.json"
    original = {CHAT_ID: {"777": {"pts": 1, "name": "Текущее состояние"}}}
    _write_scores(scores_file, original)
    marker = backup_root / ".recovery.done"

    try:
        recovery.recover_scoreboard(
            backup_root=backup_root,
            scores_file=scores_file,
            chat_id=CHAT_ID,
            expected=EXPECTED,
            marker_file=marker,
            apply=True,
        )
    except RuntimeError as exc:
        assert "no backup matches" in str(exc)
    else:
        raise AssertionError("recovery must fail without an exact backup match")

    assert json.loads(scores_file.read_text(encoding="utf-8")) == original
    assert not marker.exists()
