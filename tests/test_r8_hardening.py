from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_crocodile_socket_requires_signed_telegram_init_data():
    source = _source("games/crocodile.py")
    html = _source("index.html")

    assert 'cors_allowed_origins="*"' not in source
    assert "validate_telegram_init_data(init_data, API_TOKEN)" in source
    assert "authorize_crocodile_drawer(" in source
    assert "telegram_user_id" in source
    assert "socket attempted to switch rooms" in source
    assert 'getHashParam("tgWebAppData")' in html
    assert "(tg && tg.initData)" in html
    assert "auth: { initData: telegramInitData }" in html


def test_mutating_crocodile_socket_events_are_authorized():
    source = _source("games/crocodile.py")

    for function_name in ("draw_step", "snapshot", "skip_turn", "final_frame"):
        marker = f"async def {function_name}"
        start = source.index(marker)
        next_event = source.find("\n@sio.event", start + len(marker))
        body = source[start : next_event if next_event != -1 else len(source)]
        assert "_authorize_socket_room" in body, function_name


def test_incoming_message_logs_do_not_dump_message_content_by_default():
    source = _source("core/middlewares.py")

    assert "LOG_MESSAGE_CONTENT" in source
    assert "event.from_user.full_name" not in source
    assert "model_dump(" not in source
    assert "UNKNOWN message payload" not in source
    assert "пользователь_id=%s" in source
    assert "длина_текста=%s" in source


def test_deploy_targets_exact_sha_and_has_backup_healthcheck_and_rollback():
    source = _source(".github/workflows/deploy.yml")

    assert "DEPLOY_SHA: ${{ github.sha }}" in source
    assert "cancel-in-progress: false" in source
    assert 'PREVIOUS_SHA="$(git rev-parse HEAD)"' in source
    assert 'git reset --hard "$TARGET_SHA"' in source
    assert 'git reset --hard "$PREVIOUS_SHA"' in source
    assert '"$VENV_PYTHON" -m pip install' in source
    assert 'service_ctl is-active --quiet "$SERVICE"' in source
    assert "production_healthcheck.py" in source
    assert "backup_runtime_state.py" in source
    assert '[[ -f "$BACKUP_DIR/manifest.json" ]]' in source
    assert source.index('BACKUP_DIR="$(') < source.index('git reset --hard "$TARGET_SHA"')
    assert "StrictHostKeyChecking=yes" in source
    assert "SSH_KNOWN_HOSTS is not configured" in source
    assert "trap rollback ERR" in source
