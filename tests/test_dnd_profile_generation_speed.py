import asyncio
import json
from types import SimpleNamespace

from AI import dnd_campaign as campaign
from AI import dnd_profile_gender_grounding as feature


def _session(chat_id=-1009911):
    session = SimpleNamespace(
        chat_id=chat_id,
        participants={"1": {"user_id": 1, "name": "Алиса"}},
        character_profiles={"1": {feature.GENDER_STEP: "женский"}},
        profile_options={},
        conversation=[],
    )
    campaign._ensure(session)
    return session


def _bundle_payload():
    return json.dumps(
        {
            "style": [
                "уличная охотница на проклятия",
                "нервная архивистка чудес",
                "светская контрабандистка реликвий",
                "дворовая алхимичка-самоучка",
                "театральная мошенница-идеалистка",
            ],
            "strength": [
                "видит чужой блеф",
                "не теряется в бардаке",
                "замечает мелкие несостыковки",
                "чинит всё из мусора",
                "выкручивается на ходу",
            ],
            "weakness": [
                "лезет проверять запретное",
                "не умеет вовремя замолчать",
                "боится выглядеть трусихой",
                "залипает на загадках",
                "любит эффектные выходы",
            ],
            "special": [
                "аварийный план из кармана",
                "наглая убедительная легенда",
                "нелепый отвлекающий манёвр",
                "интуиция на одну катастрофу",
                "грязный трюк без инструкции",
            ],
        },
        ensure_ascii=False,
    )


class FakeDnd:
    def __init__(self, session, response):
        self.dnd_sessions = {session.chat_id: session}
        self.response = response
        self.prompts = []
        self.persist_calls = 0

    async def generate_session_response(self, session, prompt):
        self.prompts.append(prompt)
        session.conversation.append({"role": "user", "content": prompt})
        session.conversation.append({"role": "assistant", "content": self.response})
        return self.response

    def persist_dnd_sessions(self):
        self.persist_calls += 1


def test_profile_bundle_parser_requires_five_valid_options_per_step():
    bundle = feature._parse_profile_option_bundle(_bundle_payload(), campaign)

    assert set(bundle) == set(campaign.PROFILE_STEPS)
    assert all(len(bundle[step]) == campaign.PROFILE_OPTION_COUNT for step in campaign.PROFILE_STEPS)

    broken = json.loads(_bundle_payload())
    broken["strength"] = broken["strength"][:4]
    assert feature._parse_profile_option_bundle(json.dumps(broken, ensure_ascii=False), campaign) is None


def test_profile_bundle_generates_all_character_steps_in_one_model_call():
    session = _session()
    dnd = FakeDnd(session, _bundle_payload())

    bundle = asyncio.run(feature._generate_profile_option_bundle(campaign, dnd, session, 1))

    assert len(dnd.prompts) == 1
    assert "Пол персонажа: женский" in dnd.prompts[0]
    assert all(campaign._options_are_valid(bundle[step]) for step in campaign.PROFILE_STEPS)
    assert session.profile_options["1"] == bundle
    assert session.conversation == []
