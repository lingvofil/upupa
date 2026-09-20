from types import SimpleNamespace

from AI import dnd_growth as growth


def _achievement(pattern="NEGOTIATE_MONSTER", mechanic="ADVANTAGE_SOCIAL", title="Дипломат с клыками", charge=1):
    return {
        "id": f"{pattern.lower()}:{mechanic.lower()}",
        "title": title,
        "description": "раз за приключение сделать полезную штуку",
        "pattern": pattern,
        "mechanic": mechanic,
        "charges_max": 1,
        "charges_remaining": charge,
    }


def _session():
    return SimpleNamespace(
        chat_id=100,
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        growth_counts={},
        growth_evidence={},
        growth_expected_actor_ids=[],
        growth_seen_scene_keys=[],
        learned_achievements={},
        pending_achievement_uses={},
        achievement_boosts={},
        achievement_world_facts=[],
        growth_created_offer_tokens=[],
        scene_count=2,
        scene_clocks={},
        threat={"name": None, "level": 0, "max": 6, "history": []},
    )


def test_growth_counts_only_resolved_expected_actor_and_once_per_scene():
    session = _session()
    session.growth_expected_actor_ids = [1]
    tag = "[GROWTH:ADD;PLAYER:1;PATTERN:NEGOTIATE_MONSTER;EVIDENCE:уговорил огра пройти мимо]"
    cleaned, notices = growth.apply_growth_metadata(session, tag, tag, [])
    assert cleaned == ""
    assert session.growth_counts["1"]["NEGOTIATE_MONSTER"] == 1
    assert len(notices) == 1

    second = "[GROWTH:ADD;PLAYER:1;PATTERN:DECEIVE;EVIDENCE:соврал тому же огру]"
    growth.apply_growth_metadata(session, second, second, [])
    assert "DECEIVE" not in session.growth_counts["1"]

    wrong_actor = "[GROWTH:ADD;PLAYER:2;PATTERN:ESCAPE;EVIDENCE:сбежал через окно]"
    growth.apply_growth_metadata(session, wrong_actor, wrong_actor, [])
    assert "2" not in session.growth_counts

    session.scene_count += 1
    growth.apply_growth_metadata(session, second, second, [])
    assert session.growth_counts["1"]["DECEIVE"] == 1


def test_achievements_are_scoped_per_hero_and_reset_charge_on_heritage():
    session = _session()
    growth._load_achievements_from_history(
        session,
        1,
        {"achievements": [_achievement(charge=0)]},
    )
    growth._load_achievements_from_history(
        session,
        2,
        {"achievements": [_achievement("ESCAPE", "ADVANTAGE_MOVE", "Мастер заднего хода", 0)]},
    )
    assert session.learned_achievements["1"][0]["title"] == "Дипломат с клыками"
    assert session.learned_achievements["2"][0]["title"] == "Мастер заднего хода"
    assert session.learned_achievements["1"][0]["charges_remaining"] == 1
    assert session.learned_achievements["2"][0]["charges_remaining"] == 1


def test_dead_hero_does_not_inherit_achievements():
    session = _session()
    growth._load_achievements_from_history(
        session,
        1,
        {"dead": True, "achievements": [_achievement()]},
    )
    assert session.learned_achievements["1"] == []


class _FakeCampaign:
    archive = {"chats": {}}

    @classmethod
    def _chat_history(cls, chat_id, create=False):
        key = str(int(chat_id))
        if create:
            return cls.archive["chats"].setdefault(key, {"players": {}, "campaigns": []})
        return cls.archive["chats"].get(key, {"players": {}, "campaigns": []})

    @classmethod
    def _player_history(cls, chat_id, user_id):
        return (cls._chat_history(chat_id).get("players") or {}).get(str(int(user_id)))

    @classmethod
    def _save_archive(cls, dnd):
        dnd.saved += 1


def test_third_repetition_creates_two_option_offer_after_archive():
    _FakeCampaign.archive = {
        "chats": {
            "100": {
                "players": {
                    "1": {
                        "name": "Алиса",
                        "growth_progress": {"NEGOTIATE_MONSTER": 2},
                        "growth_evidence": {
                            "NEGOTIATE_MONSTER": ["договорилась с троллем", "договорилась с гарпией"]
                        },
                        "achievements": [],
                        "mastered_growth_patterns": [],
                    }
                },
                "campaigns": [],
            }
        }
    }
    session = _session()
    session.growth_counts = {"1": {"NEGOTIATE_MONSTER": 1}}
    session.growth_evidence = {"1": {"NEGOTIATE_MONSTER": ["уговорила огра"]}}

    def original_archive(dnd, session, finale, epilogue):
        chat = _FakeCampaign._chat_history(session.chat_id, True)
        old = chat["players"].get("1") or {}
        chat["players"]["1"] = {"name": old.get("name"), "dead": False}
        chat["players"]["2"] = {"name": "Боря", "dead": False}
        chat["campaigns"].append({"finale": finale, "epilogue": epilogue})

    dnd = SimpleNamespace(saved=0)
    growth._archive_growth(_FakeCampaign, dnd, session, original_archive, "финал", "эпилог")

    history = _FakeCampaign._player_history(100, 1)
    assert history["growth_progress"]["NEGOTIATE_MONSTER"] == 3
    assert len(history["pending_achievement"]["options"]) == 2
    assert history["pending_achievement"]["options"][0]["title"] == "Адвокат нечисти"
    assert history["pending_achievement"]["options"][1]["title"] == "Дипломат с клыками"
    assert len(session.growth_created_offer_tokens) == 1


def test_dead_hero_gets_no_post_campaign_offer():
    _FakeCampaign.archive = {
        "chats": {
            "100": {
                "players": {
                    "1": {
                        "name": "Алиса",
                        "growth_progress": {"NEGOTIATE_MONSTER": 2},
                        "achievements": [],
                    }
                },
                "campaigns": [],
            }
        }
    }
    session = _session()
    session.growth_counts = {"1": {"NEGOTIATE_MONSTER": 1}}

    def original_archive(dnd, session, finale, epilogue):
        chat = _FakeCampaign._chat_history(session.chat_id, True)
        chat["players"]["1"] = {"name": "Алиса", "dead": True}
        chat["players"]["2"] = {"name": "Боря", "dead": False}
        chat["campaigns"].append({})

    dnd = SimpleNamespace(saved=0)
    growth._archive_growth(_FakeCampaign, dnd, session, original_archive, "", "")
    assert "pending_achievement" not in _FakeCampaign._player_history(100, 1)


def test_selecting_offer_marks_pattern_mastered_and_updates_live_hero():
    offer = growth._make_offer("NEGOTIATE_MONSTER", ["уговорила огра"])
    offer["token"] = "abcd1234"
    _FakeCampaign.archive = {
        "chats": {
            "100": {
                "players": {
                    "1": {
                        "name": "Алиса",
                        "achievements": [],
                        "mastered_growth_patterns": [],
                        "pending_achievement": offer,
                    }
                },
                "campaigns": [],
            }
        }
    }
    session = _session()
    dnd = SimpleNamespace(
        saved=0,
        dnd_sessions={100: session},
        persist_dnd_sessions=lambda: setattr(dnd, "persisted", getattr(dnd, "persisted", 0) + 1),
    )

    selected, error = growth.select_achievement(_FakeCampaign, dnd, 100, 1, "abcd1234", 0)

    assert error is None
    assert selected["title"] == "Адвокат нечисти"
    history = _FakeCampaign._player_history(100, 1)
    assert history["pending_achievement"] if False else True
    assert "pending_achievement" not in history
    assert history["mastered_growth_patterns"] == ["NEGOTIATE_MONSTER"]
    assert session.learned_achievements["1"][0]["title"] == "Адвокат нечисти"
    assert session.learned_achievements["1"][0]["charges_remaining"] == 1


def test_achievement_advantage_cancels_disadvantage_and_is_consumed():
    session = _session()
    session.achievement_boosts = {"1": {"domain": "SOCIAL", "source": "Дипломат с клыками"}}
    response = (
        "[ACTION:ROLL;TYPE:CHECK;DOMAIN:SOCIAL;REASON:уговорить стражника;"
        "DC:12;MODE:DISADVANTAGE;TARGETS:1]"
    )
    guarded, source = growth.apply_achievement_boost(session, response)
    assert "MODE:NORMAL" in guarded
    assert source == "Дипломат с клыками"
    assert session.achievement_boosts == {}


def test_parley_achievement_spends_charge_and_blocks_first_enemy_attack():
    session = _session()
    session.learned_achievements = {
        "1": [_achievement("NEGOTIATE_MONSTER", "PARLEY", "Адвокат нечисти", 1)]
    }
    session.pending_achievement_uses = {
        "1": {
            "index": 0,
            "id": "negotiate_monster:parley",
            "title": "Адвокат нечисти",
            "mechanic": "PARLEY",
        }
    }
    notices = growth.commit_pending_achievement_uses(session)
    assert session.learned_achievements["1"][0]["charges_remaining"] == 0
    assert growth._has_parley(session) is True
    assert any("окно переговоров" in notice for notice in notices)

    response = "[ACTION:ENEMY_ATTACK;TARGETS:1;POWER:HIGH;REASON:огр бьёт первым]"
    guarded, blocked = growth._block_attack_for_parley(response)
    assert blocked is True
    assert "[ACTION:INPUT]" in guarded
    growth._consume_parley(session)
    assert growth._has_parley(session) is False


def test_clock_achievement_requires_relevant_clock_and_pushes_by_two():
    session = _session()
    session.learned_achievements = {
        "1": [_achievement("INVESTIGATE", "CLOCK_PUSH", "Следователь на минималках", 1)]
    }
    plan = growth._parse_achievement_use(
        session,
        1,
        "использую достижение Следователь на минималках и ищу улику",
    )
    ok, error, _ = growth._prevalidate_achievement_use(session, 1, plan)
    assert ok is False
    assert "шкалы прогресса" in error

    from AI import dnd_scene_clocks as clocks

    clocks._set_clock(
        session,
        {
            "ID": "clue",
            "NAME": "Зацепки",
            "KIND": "PROGRESS",
            "VALUE": "1",
            "MAX": "6",
            "WHEN_FULL": "тайник найден",
        },
    )
    ok, error, validated = growth._prevalidate_achievement_use(session, 1, plan)
    assert ok is True
    assert error is None
    session.pending_achievement_uses = {"1": validated}
    growth.commit_pending_achievement_uses(session)
    assert session.scene_clocks["clue"]["value"] == 3


def test_growth_context_does_not_emit_growth_tag_rules_outside_resolution():
    session = _session()
    session.learned_achievements = {"1": [_achievement()]}
    text = growth._growth_context(session)
    assert "ДОСТИЖЕНИЯ ГЕРОЕВ" in text
    assert growth.GROWTH_MARKER not in text
