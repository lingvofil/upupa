from types import SimpleNamespace

from AI import dnd_context_builder as context_builder


def _session():
    return SimpleNamespace(
        campaign_id="campaign-test",
        state_revision=7,
        selected_plot="Украсть реестр до рассвета",
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        character_profiles={
            "1": {
                "style": "мрачный курьер",
                "strength": "лезет напролом",
                "weakness": "не умеет молчать",
                "special": "договориться с нечистью",
            },
            "2": {
                "style": "нервный архивист",
                "strength": "видит детали",
                "weakness": "боится воды",
                "special": "помнит чужие долги",
            },
        },
        player_positions={
            "1": {"location": "банка с пивом", "detail": "сидит внутри"},
            "2": {"location": "крыша склада", "detail": "держит верёвку"},
        },
        character_sheets={
            "1": {"hp": 4, "max_hp": 10, "status": "alive"},
            "2": {"hp": 0, "max_hp": 8, "status": "dead"},
        },
        conditions={
            "1": [{"name": "оглушена", "effect": "CHECK_DISADVANTAGE"}],
        },
        inventories={
            "1": [
                {"name": "Медный ключ", "kind": "artifact"},
                {"name": "яблоко", "kind": "item", "quantity": 2},
            ],
            "2": [],
        },
        reputations={"1": ["поджигатель пристани"]},
        learned_achievements={
            "1": [
                {
                    "title": "Адвокат нечисти",
                    "charges_remaining": 1,
                }
            ]
        },
        enemy_combatants={
            "страж": {
                "name": "Страж",
                "hp": 3,
                "max_hp": 9,
                "ac": 12,
                "status": "alive",
            }
        },
        scene_clocks={
            "alarm": {
                "name": "Тревога",
                "kind": "DANGER",
                "value": 2,
                "max": 4,
                "full": False,
                "full_cause": "",
            }
        },
        threat={"name": "Шум", "level": 1, "max": 6},
        npc_memory={
            "капитан ржа": {
                "name": "Капитан Ржа",
                "event": "обманули",
                "notes": ["обещали вернуть груз"],
                "obligation": "ждёт груз",
                "unresolved": "груз не возвращён",
            },
            "доктор мох": {
                "name": "Доктор Мох",
                "event": "лечил героя",
                "notes": ["взял плату заранее"],
            },
            "трактирщик": {
                "name": "Трактирщик",
                "event": "дал ночлег",
                "notes": ["любит тишину"],
            },
        },
        world_inherited_npc_keys=[],
        world_callback_candidate={},
        social_relationships=[
            "Алиса постоянно подкалывает Борю",
            "Боря доверяет Алисе в опасных сценах",
        ],
        event_journal=[
            {
                "event_id": "campaign-test:6:1",
                "campaign_id": "campaign-test",
                "revision": 6,
                "sequence": 1,
                "type": "ITEM_REMOVED",
                "data": {
                    "player_id": "1",
                    "name": "старое яблоко",
                    "quantity": 1,
                },
            },
            {
                "event_id": "campaign-test:7:1",
                "campaign_id": "campaign-test",
                "revision": 7,
                "sequence": 1,
                "type": "PLAYER_POSITION_CHANGED",
                "data": {
                    "player_id": "1",
                    "before": {"location": "двор"},
                    "after": {"location": "банка с пивом"},
                },
            },
        ],
        scene_log=[
            "Очень старая сцена, которую уже не надо тащить.",
            "Алиса полезла к складу.",
            "Боря забрался на крышу склада.",
            "Капитан Ржа заметил движение у ворот.",
        ],
    )


class _Campaign:
    @staticmethod
    def _campaign_context(_dnd, _session):
        return "LEGACY_HEAD " + ("x" * 4_000) + " SPOTLIGHT_TAIL"


def test_context_is_bounded_and_contains_authoritative_state():
    session = _session()

    context = context_builder.build_memory_context(
        SimpleNamespace(),
        _Campaign,
        session,
        prompt="Алиса спрашивает Капитана Ржу про груз",
    )

    assert len(context) <= context_builder.CONTEXT_MAX_CHARS
    assert "АВТОРИТЕТНЫЙ СНИМОК" in context
    assert "банка с пивом" in context
    assert "HP 4/10, alive" in context
    assert "Боря" in context and "HP 0/8, dead" in context
    assert "Медный ключ" in context
    assert "Страж: HP 3/9" in context
    assert "Тревога: 2/4" in context
    assert "PLAYER_POSITION_CHANGED" in context
    assert "Капитан Ржа" in context
    assert "SPOTLIGHT_TAIL" in context


def test_context_keeps_only_recent_narrative_scenes():
    session = _session()

    context = context_builder.build_memory_context(
        SimpleNamespace(),
        _Campaign,
        session,
        prompt="продолжай",
    )

    assert "Очень старая сцена" not in context
    assert "Алиса полезла к складу" in context
    assert "Боря забрался на крышу" in context
    assert "Капитан Ржа заметил движение" in context


def test_npc_relevance_prefers_named_and_open_thread():
    session = _session()
    # Inflate unrelated recent NPCs so relevance, not insertion order, decides.
    for index in range(12):
        session.npc_memory[f"прохожий-{index}"] = {
            "name": f"Прохожий {index}",
            "event": "прошёл мимо",
            "notes": [],
        }

    context = context_builder.build_memory_context(
        SimpleNamespace(),
        _Campaign,
        session,
        prompt="Надо договориться с Капитаном Ржой",
    )

    assert "Капитан Ржа" in context
    assert "груз не возвращён" in context


def test_context_does_not_mutate_durable_history_or_state():
    session = _session()
    before_events = [dict(row) for row in session.event_journal]
    before_scenes = list(session.scene_log)

    context_builder.build_memory_context(
        SimpleNamespace(),
        _Campaign,
        session,
        prompt="дальше",
    )

    assert session.event_journal == before_events
    assert session.scene_log == before_scenes
