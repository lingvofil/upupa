import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd, dnd_campaign
from AI.dnd_completion import DndParticipantCompletionMiddleware
from AI.dnd_state_commands import (
    DndStateCommandMiddleware,
    command_kind,
    configure_dnd_state_commands,
    render_hero,
    render_inventory,
    render_npcs,
    render_status,
)


class FakeMessage:
    def __init__(self, text, *, chat_id=-100950, user_id=7, first_name="Семён"):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, first_name=first_name)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=999)


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))


def _set_archive(monkeypatch, *, chat_id=-100950):
    archive = {
        "version": 1,
        "chats": {
            str(chat_id): {
                "players": {
                    "7": {
                        "name": "Архивный Семён",
                        "profile": {
                            "style": "бухгалтер некромантии",
                            "strength": "видит чужой блеф",
                            "weakness": "лезет проверять запретное",
                            "special": "аварийный план из кармана",
                        },
                        "inventory": [
                            {"name": "ржавая ложка", "kind": "item"},
                            {"name": "Корона Подъезда", "kind": "artifact"},
                        ],
                        "artifacts": [{"name": "Корона Подъезда", "kind": "artifact"}],
                        "reputation": ["разбудил налоговую нежить"],
                        "adventures": [{"plot": "Старый сюжет", "summary": "Выжил зачем-то"}],
                    }
                },
                "campaigns": [
                    {
                        "selected_plot": "Почтамт объявил войну адресатам",
                        "finale": "Почтамт сгорел, письма победили.",
                        "epilogue": "Герои получили пожизненную подписку на спам.",
                        "npc_memory": {
                            "капитан ржа": {
                                "name": "Капитан Ржа",
                                "event": "их запомнил",
                                "notes": ["обещал счёт", "теперь не доверяет Семёну"],
                            }
                        },
                        "threat": {"name": "Почтовый бунт", "level": 4, "max": 6},
                        "scenes": ["Последняя архивная сцена"],
                    }
                ],
            }
        },
    }
    monkeypatch.setattr(dnd_campaign, "_archive", archive)
    monkeypatch.setattr(dnd_campaign, "_archive_loaded", True)
    return archive


def _active_session(chat_id=-100950):
    return SimpleNamespace(
        chat_id=chat_id,
        mode="participants",
        state="WAITING_ROLL",
        participants={"7": {"user_id": 7, "name": "Текущий Семён"}},
        character_profiles={
            "7": {
                "style": "курьер запретных реликвий",
                "strength": "не теряется в бардаке",
                "weakness": "не умеет вовремя замолчать",
                "special": "грязный трюк без инструкции",
            }
        },
        reputations={"7": ["поссорился с часовней"]},
        inventories={
            "7": [
                {"name": "мокрый ключ", "kind": "item"},
                {"name": "Зуб Канцлера", "kind": "artifact"},
            ]
        },
        npc_memory={
            "марфа": {
                "name": "Марфа Без Паспорта",
                "event": "помогла сбежать",
                "notes": ["просит вернуть тележку"],
            }
        },
        threat={"name": "Налоговая кавалерия", "level": 3, "max": 6, "history": []},
        selected_plot="Санаторий теряет этажи",
        scene_log=["Лифт выплюнул героев прямо на крышу столовой."],
        pending_roll={
            "reason": "перепрыгнуть кассовый аппарат",
            "dc": 11,
            "mode": "DISADVANTAGE",
            "target_user_ids": [7],
        },
        pending_poll=None,
        pending_actions={},
        action_target_user_ids=[],
        plot_options=[],
    )


def test_command_aliases_accept_requested_phrases_and_punctuation():
    assert command_kind("Мой герой") == "hero"
    assert command_kind("инвентарь!!!") == "inventory"
    assert command_kind("Наши знакомые") == "npcs"
    assert command_kind("Что происходит?") == "status"
    assert command_kind("УПУПА ДНД") == "start"
    assert command_kind("упупа днд что-нибудь") is None


def test_active_hero_and_inventory_use_live_state_not_archive(monkeypatch):
    _set_archive(monkeypatch)
    session = _active_session()
    fake_dnd = SimpleNamespace(dnd_sessions={session.chat_id: session})

    hero = render_hero(fake_dnd, session.chat_id, 7, "Семён")
    inventory = render_inventory(fake_dnd, session.chat_id, 7)

    assert "Источник: текущая егра" in hero
    assert "курьер запретных реликвий" in hero
    assert "поссорился с часовней" in hero
    assert "бухгалтер некромантии" not in hero
    assert "Источник: текущая егра" in inventory
    assert "мокрый ключ" in inventory
    assert "✨ Зуб Канцлера" in inventory
    assert "ржавая ложка" not in inventory


def test_commands_fall_back_to_saved_state_without_active_game(monkeypatch):
    _set_archive(monkeypatch)
    fake_dnd = SimpleNamespace(dnd_sessions={})

    hero = render_hero(fake_dnd, -100950, 7, "Семён")
    inventory = render_inventory(fake_dnd, -100950, 7)
    npcs = render_npcs(fake_dnd, -100950)
    status = render_status(fake_dnd, -100950)

    assert "последнее сохранённое состояние" in hero
    assert "бухгалтер некромантии" in hero
    assert "Завершённых приключений в памяти: 1" in hero
    assert "ржавая ложка" in inventory
    assert "✨ Корона Подъезда" in inventory
    assert "Капитан Ржа" in npcs
    assert "теперь не доверяет Семёну" in npcs
    assert "Активной егры сейчас нет" in status
    assert "Почтамт объявил войну адресатам" in status
    assert "Почтамт сгорел, письма победили" in status
    assert "Почтовый бунт" in status


def test_active_status_exposes_real_pending_roll_scene_and_threat(monkeypatch):
    _set_archive(monkeypatch)
    session = _active_session()
    fake_dnd = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        _target_names=lambda _session, ids: ["Текущий Семён" for _ in ids],
    )

    status = render_status(fake_dnd, session.chat_id)

    assert "Егра сейчас активна" in status
    assert "Санаторий теряет этажи" in status
    assert "ждём бросок" in status
    assert "перепрыгнуть кассовый аппарат" in status
    assert "сложность 11" in status
    assert "с помехой" in status
    assert "Лифт выплюнул героев" in status
    assert "Налоговая кавалерия" in status
    assert "Почтамт сгорел" not in status


def test_active_npcs_do_not_mix_in_previous_campaign(monkeypatch):
    _set_archive(monkeypatch)
    session = _active_session()
    fake_dnd = SimpleNamespace(dnd_sessions={session.chat_id: session})

    text = render_npcs(fake_dnd, session.chat_id)

    assert "Источник: текущая егра" in text
    assert "Марфа Без Паспорта" in text
    assert "Капитан Ржа" not in text


def test_state_middleware_intercepts_command_without_calling_downstream(monkeypatch):
    message = FakeMessage("Что происходит?")
    called = []
    monkeypatch.setattr(
        "AI.dnd_state_commands.render_state_command",
        lambda *args, **kwargs: "живое состояние",
    )

    async def handler(_event, _data):
        called.append(True)
        return "handled"

    result = asyncio.run(DndStateCommandMiddleware()(handler, message, {"bot": FakeBot()}))

    assert result is None
    assert called == []
    assert message.answers == [("живое состояние", {})]


def test_short_dnd_alias_delegates_to_existing_start_handler(monkeypatch):
    message = FakeMessage("упупа днд")
    starts = []

    async def fake_start(event):
        starts.append(event)

    monkeypatch.setattr(dnd, "cmd_start_dnd", fake_start)

    async def handler(_event, _data):
        raise AssertionError("short DnD alias must not fall through")

    result = asyncio.run(DndStateCommandMiddleware()(handler, message, {"bot": FakeBot()}))

    assert result is None
    assert starts == [message]


def test_state_query_reply_is_not_precollected_as_player_action(monkeypatch):
    chat_id = -100951
    session = SimpleNamespace(
        chat_id=chat_id,
        mode="participants",
        state="WAITING_ACTION",
        participants={"7": {"user_id": 7, "name": "Семён"}},
        action_target_user_ids=[],
        action_prompt_message_id=777,
        pending_actions={"7": {"user_id": 7, "name": "Семён", "action": "старое действие"}},
    )
    fake_dnd = SimpleNamespace(
        dnd_sessions={chat_id: session},
        _is_participant_mode=lambda _session: True,
        _participant_ids=lambda _session: {7},
        persist_dnd_sessions=lambda: None,
    )
    event = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=7, first_name="Семён"),
        reply_to_message=SimpleNamespace(message_id=777),
        text="Мой герой",
        caption=None,
    )

    asyncio.run(
        DndParticipantCompletionMiddleware()._precollect_action_reply(
            fake_dnd,
            FakeBot(),
            event,
        )
    )

    assert session.pending_actions["7"]["action"] == "старое действие"


def test_state_command_registration_is_idempotent():
    registered = []
    router = SimpleNamespace(
        message=SimpleNamespace(outer_middleware=lambda middleware: registered.append(middleware))
    )

    configure_dnd_state_commands(router)
    configure_dnd_state_commands(router)

    assert len(registered) == 1
    assert isinstance(registered[0], DndStateCommandMiddleware)
