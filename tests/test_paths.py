from pathlib import Path

from core import paths, state


def test_persistent_paths_are_absolute_and_stay_in_project_root():
    persistent_paths = [
        paths.CHAT_SETTINGS_PATH,
        paths.USER_MESSAGES_LOG_PATH,
        paths.MESSAGE_STATS_PATH,
        paths.CHAT_LIST_PATH,
        paths.SMS_DISABLED_CHATS_PATH,
        paths.STATISTICS_DB_PATH,
        paths.ANTISPAM_SETTINGS_PATH,
    ]

    assert paths.PROJECT_ROOT == Path(paths.__file__).resolve().parents[1]
    assert paths.DATA_DIR == paths.PROJECT_ROOT
    assert all(path.is_absolute() for path in persistent_paths)
    assert all(path.parent == paths.DATA_DIR for path in persistent_paths)


def test_legacy_state_file_names_delegate_to_core_paths():
    assert state.CHAT_SETTINGS_FILE == str(paths.CHAT_SETTINGS_PATH)
    assert state.LOG_FILE == str(paths.USER_MESSAGES_LOG_PATH)
    assert state.STATS_FILE == str(paths.MESSAGE_STATS_PATH)
    assert state.CHAT_LIST_FILE == str(paths.CHAT_LIST_PATH)
    assert state.SMS_DISABLED_CHATS_FILE == str(paths.SMS_DISABLED_CHATS_PATH)
    assert state.DB_FILE == str(paths.STATISTICS_DB_PATH)
