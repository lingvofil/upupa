import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from AI import dnd_state_commands as commands


def test_npc_repair_replays_only_npc_tags_from_history(monkeypatch):
    chat_id = -100952
    session = SimpleNamespace(
        chat_id=chat_id,
        participants={"7": {"user_id": 7, "name": "Семён"}},
        npc_memory={},
        scene_log=["Капитан Ржа провёл партию через ворота."],
        conversation=[
            {
                "role": "assistant",
                "content": (
                    "Стража злится. "
                    "[THREAT:Подозрение стражи;DELTA:2;CAUSE:шум] "
                    "[NPC:Капитан Ржа;EVENT:помог пройти;NOTE:провёл через ворота]"
                ),
            }
        ],
    )
    applied = []
    persisted = []

    def apply_metadata(target, text):
        applied.append(text)
        assert "THREAT:" not in text
        if "[NPC:Капитан Ржа" in text:
            target.npc_memory["капитан ржа"] = {
                "name": "Капитан Ржа",
                "event": "помог пройти",
                "notes": ["провёл через ворота"],
            }
        return text, []

    async def must_not_generate(*_args, **_kwargs):
        raise AssertionError("existing NPC tag should be enough; scene extraction is unnecessary")

    fake_campaign = SimpleNamespace(
        _ensure=lambda _session: None,
        _apply_metadata=apply_metadata,
        _ephemeral_generate=must_not_generate,
    )
    monkeypatch.setattr(dnd, "dnd_sessions", {chat_id: session})
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: persisted.append(True))
    monkeypatch.setattr(commands, "_campaign_module", lambda _dnd: fake_campaign)

    asyncio.run(commands._repair_active_npc_memory(dnd, chat_id))

    assert applied == ["[NPC:Капитан Ржа;EVENT:помог пройти;NOTE:провёл через ворота]"]
    assert session.npc_memory["капитан ржа"]["event"] == "помог пройти"
    assert persisted == [True]
