from types import SimpleNamespace

from AI import dnd
from AI import dnd_adjudication as adjudication
from AI.dnd_roll_ability_display import annotate_roll_ability


def _session(mode="participants"):
    return SimpleNamespace(mode=mode)


def test_adjudication_rules_require_possible_uncertain_and_meaningful_failure():
    rules = adjudication.ADJUDICATION_RULES

    assert "действие вообще возможно" in rules
    assert "исход действительно неочевиден" in rules
    assert "провал или цена провала заметно меняют сцену" in rules
    assert "обычная незапертая дверь" in rules.casefold()
    assert "очевидно невозможно" in rules
    assert "безнаказанно повторять" in rules


def test_adjudication_rules_separate_check_save_and_attack():
    rules = adjudication.ADJUDICATION_RULES

    assert "CHECK — герой активно пытается" in rules
    assert "SAVE — герой реактивно сопротивляется" in rules
    assert "ATTACK — попытка попасть атакой" in rules
    assert "PLAYER_ATTACK/CINEMATIC_ATTACK" in rules
    assert "ENEMY_ATTACK" in rules
    assert "Никогда не подменяй атаку обычным CHECK или SAVE" in rules


def test_adjudication_rules_choose_ability_before_skill_and_allow_unusual_pair():
    rules = adjudication.ADJUDICATION_RULES

    assert "сначала выбери ХАРАКТЕРИСТИКУ" in rules
    assert "только потом подходящий SKILL" in rules
    assert "ABILITY:STR + SKILL:Запугивание" in rules
    assert "ABILITY:CHA + SKILL:Запугивание" in rules
    assert "не подгоняй характеристику под лучший стат героя" in rules.casefold()


def test_explicit_strength_intimidation_survives_roll_annotation():
    response = (
        "Алдарн сгибает железный прут. "
        "[ACTION:ROLL;TYPE:CHECK;SKILL:Запугивание;ABILITY:STR;"
        "REASON:сломить волю пленника демонстрацией силы;DC:11;MODE:NORMAL;TARGETS:1]"
    )

    guarded, ability = annotate_roll_ability(response)

    assert ability == "STR"
    assert "ABILITY:STR" in guarded
    assert "ABILITY:CHA" not in guarded
    assert "Запугивание" in guarded


def test_installation_adds_rules_to_system_and_live_context_once():
    fake_dnd = SimpleNamespace(
        DND_SYSTEM_PROMPT="BASE",
    )
    fake_campaign = SimpleNamespace(
        _campaign_context=lambda _dnd, _session: "STATE",
    )

    adjudication.install_dnd_adjudication(fake_dnd, fake_campaign)
    adjudication.install_dnd_adjudication(fake_dnd, fake_campaign)

    assert fake_dnd.DND_SYSTEM_PROMPT.count(adjudication.ADJUDICATION_MARKER) == 1

    participant_context = fake_campaign._campaign_context(fake_dnd, _session())
    assert participant_context.count(adjudication.ADJUDICATION_MARKER) == 1
    assert "ABILITY в ACTION:ROLL обязателен" in participant_context

    abstract_context = fake_campaign._campaign_context(
        fake_dnd,
        _session(mode="abstract"),
    )
    assert adjudication.ADJUDICATION_MARKER in abstract_context
    assert "добровольный подход и решение принадлежат игроку" not in abstract_context


def test_base_prompt_no_longer_conflicts_with_participant_ability_modifiers():
    assert "d20 БЕЗ модификаторов" not in dnd.DND_SYSTEM_PROMPT
    assert "Не используй характеристики, модификаторы" not in dnd.DND_SYSTEM_PROMPT
    assert "Сначала выбирай характеристику по способу действия" in dnd.DND_SYSTEM_PROMPT
    assert "Прямую атаку не превращай ни в CHECK, ни в SAVE" in dnd.DND_SYSTEM_PROMPT
