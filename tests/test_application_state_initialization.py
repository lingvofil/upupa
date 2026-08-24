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
    import features.social_graph as social_graph_feature
    import features.stat_rank_settings as stat_rank_feature
    import features.statistics as statistics_feature
    import features.world.service as world_service_feature
    import infrastructure.persistence as persistence

    calls = []
    monkeypatch.setattr(chat_settings_feature, "load_chat_state", lambda: calls.append("chat-state"))
    monkeypatch.setattr(content_filter_feature, "load_antispam_settings", lambda: calls.append("antispam"))
    monkeypatch.setattr(sms_settings_feature, "load_sms_disabled_chats", lambda: calls.append("sms"))
    monkeypatch.setattr(stat_rank_feature, "load_stat_rank_state", lambda: calls.append("rank-state"))
    monkeypatch.setattr(statistics_feature, "init_db", lambda: calls.append("statistics"))
    monkeypatch.setattr(social_graph_feature, "init_db", lambda: calls.append("social-graph"))

    class FakeWorldRepository:
        def __init__(self, path):
            self.path = path

        def init_schema(self):
            calls.append("world-schema")

    monkeypatch.setattr(persistence, "SQLiteWorldRepository", FakeWorldRepository)
    monkeypatch.setattr(
        world_service_feature,
        "configure_world_service",
        lambda service: calls.append("world-config"),
    )

    application = UpupaApplication(
        bot=object(),
        dispatcher=object(),
        supervisor=NoopSupervisor(),
    )

    application.initialize_state()

    assert calls == [
        "chat-state",
        "antispam",
        "sms",
        "rank-state",
        "statistics",
        "social-graph",
        "world-schema",
        "world-config",
    ]
