# Настраивает fake env и моки тяжёлых библиотек до импорта приложения/features.
from tests import test_smoke_imports  # noqa: F401

from app.bootstrap import UpupaApplication


class NoopSupervisor:
    async def stop(self):
        return None


def test_application_initializes_file_state_explicitly(monkeypatch):
    import features.chat_settings as chat_settings_feature
    import features.content_filter as content_filter_feature
    import features.sms_settings as sms_settings_feature
    import features.stat_rank_settings as stat_rank_feature
    import features.statistics as statistics_feature

    calls = []
    monkeypatch.setattr(chat_settings_feature, "load_chat_state", lambda: calls.append("chat-state"))
    monkeypatch.setattr(content_filter_feature, "load_antispam_settings", lambda: calls.append("antispam"))
    monkeypatch.setattr(sms_settings_feature, "load_sms_disabled_chats", lambda: calls.append("sms"))
    monkeypatch.setattr(stat_rank_feature, "load_stat_rank_state", lambda: calls.append("rank-state"))
    monkeypatch.setattr(statistics_feature, "init_db", lambda: calls.append("statistics"))

    application = UpupaApplication(
        bot=object(),
        dispatcher=object(),
        supervisor=NoopSupervisor(),
    )

    application.initialize_state()

    assert calls == ["chat-state", "antispam", "sms", "rank-state", "statistics"]
