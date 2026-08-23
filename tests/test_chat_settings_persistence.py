from copy import deepcopy

import pytest

# Настраивает fake env и моки тяжёлых библиотек до импорта feature.
from tests import test_smoke_imports  # noqa: F401

import features.chat_settings as chat_settings_feature
from core.state import chat_list, chat_settings


class MemoryRepository:
    def __init__(self, value=None):
        self.value = value
        self.saved = []

    def load(self):
        return deepcopy(self.value)

    def save(self, value):
        self.saved.append(deepcopy(value))


@pytest.fixture(autouse=True)
def restore_shared_state():
    saved_settings = deepcopy(chat_settings)
    saved_chats = deepcopy(chat_list)
    yield
    chat_settings.clear()
    chat_settings.update(saved_settings)
    chat_list.clear()
    chat_list.extend(saved_chats)


def test_load_chat_settings_preserves_shared_dict_identity():
    original = chat_settings
    repository = MemoryRepository({"-1001": {"dialog": True}})

    chat_settings_feature.load_chat_settings(repository)

    assert chat_settings is original
    assert chat_settings == {"-1001": {"dialog": True}}


def test_load_chats_preserves_shared_list_identity():
    original = chat_list
    repository = MemoryRepository([{"id": -1001, "title": "test"}])

    chat_settings_feature.load_chats(repository)

    assert chat_list is original
    assert chat_list == [{"id": -1001, "title": "test"}]


def test_invalid_repository_payload_resets_state():
    chat_settings["old"] = True
    chat_list.append({"id": 1})

    chat_settings_feature.load_chat_settings(MemoryRepository([]))
    chat_settings_feature.load_chats(MemoryRepository({}))

    assert chat_settings == {}
    assert chat_list == []


def test_save_functions_delegate_to_repository():
    chat_settings.clear()
    chat_settings.update({"-1001": {"dialog": False}})
    chat_list.clear()
    chat_list.append({"id": -1001, "title": "test"})
    settings_repository = MemoryRepository()
    chats_repository = MemoryRepository()

    chat_settings_feature.save_chat_settings(settings_repository)
    chat_settings_feature.save_chats(chats_repository)

    assert settings_repository.saved == [{"-1001": {"dialog": False}}]
    assert chats_repository.saved == [[{"id": -1001, "title": "test"}]]


def test_load_chat_state_has_explicit_startup_order(monkeypatch):
    calls = []
    monkeypatch.setattr(chat_settings_feature, "load_chat_settings", lambda: calls.append("settings"))
    monkeypatch.setattr(chat_settings_feature, "load_chats", lambda: calls.append("chats"))

    chat_settings_feature.load_chat_state()

    assert calls == ["settings", "chats"]
