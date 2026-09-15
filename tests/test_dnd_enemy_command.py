from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_enemy_command as enemy_command
from AI import dnd_state_commands as state_commands


def _session(*, enemies=None):
    return SimpleNamespace(
        mode="participants",
        enemy_combatants=enemies or {},
    )


def test_enemy_command_registers_exact_aliases():
    enemy_command.install_dnd_enemy_command(SimpleNamespace())

    assert state_commands.command_kind("враги") == "enemies"
    assert state_commands.command_kind("ДНД ВРАГИ!!!") == "enemies"
    assert state_commands.command_kind("враги огр") is None


def test_enemy_command_shows_remaining_hp_ac_and_defeated():
    session = _session(
        enemies={
            "огр": {
                "name": "Огр",
                "power": "HIGH",
                "hp": 11,
                "max_hp": 28,
                "ac": 14,
                "status": "alive",
            },
            "гоблин 1": {
                "name": "Гоблин 1",
                "power": "LOW",
                "hp": 0,
                "max_hp": 8,
                "ac": 10,
                "status": "dead",
            },
            "шаман": {
                "name": "Шаман",
                "power": "MEDIUM",
                "hp": 9,
                "max_hp": 16,
                "ac": 12,
                "status": "alive",
            },
        }
    )
    fake_dnd = SimpleNamespace(dnd_sessions={-100: session})

    text = enemy_command.render_enemies(fake_dnd, -100)

    assert "Осталось: 2 из 3" in text
    assert "👹 Огр — ❤️ 11/28 · 🛡 КБ 14" in text
    assert "👹 Шаман — ❤️ 9/16 · 🛡 КБ 12" in text
    assert "Побеждены:" in text
    assert "☠️ Гоблин 1 — ❤️ 0/8 · 🛡 КБ 10" in text


def test_enemy_command_reports_when_fight_has_not_started():
    fake_dnd = SimpleNamespace(dnd_sessions={-100: _session()})

    assert enemy_command.render_enemies(fake_dnd, -100) == "⚔️ Сейчас боя с зарегистрированными противниками нет."


def test_enemy_command_does_not_fall_back_to_archive_without_active_game():
    fake_dnd = SimpleNamespace(dnd_sessions={})

    text = enemy_command.render_enemies(fake_dnd, -100)

    assert "только во время боя" in text


def test_enemy_command_reports_zero_when_all_registered_enemies_are_dead():
    session = _session(
        enemies={
            "огр": {
                "name": "Огр",
                "power": "HIGH",
                "hp": 0,
                "max_hp": 28,
                "ac": 14,
                "status": "dead",
            }
        }
    )
    fake_dnd = SimpleNamespace(dnd_sessions={-100: session})

    text = enemy_command.render_enemies(fake_dnd, -100)

    assert "Осталось: 0 из 1" in text
    assert "Живых противников не осталось" in text
    assert "☠️ Огр" in text
