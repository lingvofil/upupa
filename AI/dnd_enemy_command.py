"""Player-facing enemy roster for active participant-mode DnD combat."""
from __future__ import annotations


COMMAND_ALIASES = {"враги", "днд враги"}


def _combatants(session) -> tuple[list[dict], list[dict]]:
    from AI import dnd_player_combat as player_combat

    player_combat._ensure_enemy_store(session)
    alive: list[dict] = []
    dead: list[dict] = []
    for key, row in (getattr(session, "enemy_combatants", {}) or {}).items():
        clean = player_combat._clean_enemy_row(str(key), row)
        if clean is None:
            continue
        if clean.get("status") == "dead" or int(clean.get("hp", 0)) <= 0:
            dead.append(clean)
        else:
            alive.append(clean)
    return alive, dead


def _enemy_line(enemy: dict, *, dead: bool = False) -> str:
    marker = "☠️" if dead else "👹"
    return (
        f"{marker} {enemy.get('name') or 'Враг'} — "
        f"❤️ {int(enemy.get('hp', 0))}/{int(enemy.get('max_hp', 0))} · "
        f"🛡 КБ {int(enemy.get('ac', 0))}"
    )


def render_enemies(dnd, chat_id: int) -> str:
    """Render only live-session combatants; never fall back to campaign archive."""
    session = (getattr(dnd, "dnd_sessions", {}) or {}).get(int(chat_id))
    if session is None or getattr(session, "mode", None) != "participants":
        return "⚔️ «днд враги» работает только во время боя в активной игре с участниками."

    alive, dead = _combatants(session)
    if not alive and not dead:
        return "⚔️ Сейчас боя с зарегистрированными противниками нет."

    total = len(alive) + len(dead)
    lines = ["⚔️ Противники", f"Осталось: {len(alive)} из {total}."]
    if alive:
        lines.extend(_enemy_line(enemy) for enemy in alive)
    else:
        lines.append("✅ Живых противников не осталось.")

    if dead:
        lines.extend(("", "Побеждены:"))
        lines.extend(_enemy_line(enemy, dead=True) for enemy in dead)
    return "\n".join(lines)


def install_dnd_enemy_command(dnd) -> None:
    """Expose the persistent enemy store through exact combat-state commands."""
    from AI import dnd_state_commands as state_commands

    if getattr(state_commands, "_upupa_dnd_enemy_command_installed", False):
        return

    state_commands._STATE_ALIASES["enemies"] = set(COMMAND_ALIASES)
    original_render_state_command = state_commands.render_state_command

    def render_state_command(
        kind: str,
        dnd_module,
        chat_id: int,
        user_id: int,
        user_name: str | None = None,
        *,
        view_policy=None,
    ) -> str:
        if kind == "enemies":
            return render_enemies(dnd_module, chat_id)
        return original_render_state_command(
            kind,
            dnd_module,
            chat_id,
            user_id,
            user_name,
            view_policy=view_policy,
        )

    state_commands.render_state_command = render_state_command
    state_commands._upupa_dnd_enemy_command_installed = True
