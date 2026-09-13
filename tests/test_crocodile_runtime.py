import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_crocodile_runtime_owns_extension_composition_order():
    source = _source("games/crocodile_runtime.py")
    calls = [
        "persistence.configure_crocodile_runtime()",
        "configure_crocodile_controls()",
        "configure_crocodile_single_words()",
        "configure_crocodile_modes()",
        "persistence.configure_crocodile_persistence_dependencies(",
        "party_controls.configure_crocodile_party_controls()",
        "party_controls.menu_keyboard = _compose_party_menu_keyboard(",
        "crocodile.get_game_keyboard = _compose_game_keyboard(",
        "crocodile.handle_callback = _compose_callback_handler(",
        "duo_optin.configure_crocodile_duo_opt_in(",
        "configure_crocodile_ui_enhancements()",
        "configure_crocodile_admin_controls()",
        "configure_crocodile_telephone_mentions()",
        "configure_crocodile_telephone_skip_permissions()",
        "configure_crocodile_telephone_roles()",
        "configure_crocodile_telephone_role_announcements()",
        "configure_crocodile_canvas_restore()",
    ]
    positions = [source.index(call) for call in calls]
    assert positions == sorted(positions)


def test_crocodile_installers_do_not_compose_other_installers():
    controls = _source("games/crocodile_controls.py")
    canvas = _source("games/crocodile_canvas_restore.py")
    duo = _source("games/crocodile_duo_optin.py")
    admin = _source("games/crocodile_admin_controls.py")
    mentions = _source("games/crocodile_telephone_mentions.py")
    skip = _source("games/crocodile_telephone_skip_permissions.py")

    assert "persistence.configure_crocodile_runtime()" not in controls
    for call in (
        "configure_crocodile_modes()",
        "configure_crocodile_party_state()",
        "configure_crocodile_party_controls()",
        "configure_crocodile_duo_opt_in()",
        "configure_crocodile_admin_controls()",
    ):
        assert call not in canvas
    assert "configure_crocodile_ui_enhancements()" not in duo
    assert "configure_crocodile_telephone_mentions()" not in admin
    assert "configure_crocodile_telephone_skip_permissions()" not in mentions
    assert "configure_crocodile_telephone_roles()" not in skip
    assert "configure_crocodile_telephone_role_announcements()" not in skip


def test_party_state_does_not_assign_into_runtime_modules():
    state_source = _source("games/crocodile_party_state.py")
    tree = ast.parse(state_source)
    assigned = []

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in {"persistence", "crocodile_modes"}
            ):
                assigned.append((target.value.id, target.attr))

    assert assigned == []
    assert "configure_crocodile_persistence_dependencies(" not in state_source
    assert "configure_crocodile_party_state" not in state_source
    assert "_original_start_duel_vote" not in state_source
    assert "_start_duel_vote_with_deadline" not in state_source

    runtime_source = _source("games/crocodile_runtime.py")
    assert runtime_source.count(
        "persistence.configure_crocodile_persistence_dependencies("
    ) == 1
    assert "party_state.crocodile_persistence_dependencies()" in runtime_source
    assert "configure_crocodile_party_state" not in runtime_source


def test_duo_opt_in_does_not_replace_persistence_serializers():
    duo_source = _source("games/crocodile_duo_optin.py")
    tree = ast.parse(duo_source)
    assigned = []

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "crocodile_persistence"
            ):
                assigned.append(target.attr)

    assert assigned == []
    assert "_original_session_to_record" not in duo_source
    assert "_original_session_from_record" not in duo_source
    assert "_session_to_record_with_duo_opt_in" not in duo_source
    assert "_session_from_record_with_duo_opt_in" not in duo_source

    runtime_source = _source("games/crocodile_runtime.py")
    record_duo = runtime_source.index(
        "duo_optin.enrich_session_record_with_duo_opt_in"
    )
    record_party = runtime_source.index("party_dependencies.enrich_session_record")
    restore_duo = runtime_source.index(
        "duo_optin.enrich_restored_session_with_duo_opt_in"
    )
    restore_party = runtime_source.index("party_dependencies.enrich_restored_session")
    assert record_duo < record_party
    assert restore_duo < restore_party


def test_duo_opt_in_does_not_replace_party_menu():
    duo_source = _source("games/crocodile_duo_optin.py")
    tree = ast.parse(duo_source)
    assigned = []

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "crocodile_party_controls"
            ):
                assigned.append(target.attr)

    assert assigned == []
    assert "_original_party_menu_keyboard" not in duo_source
    assert "unified_menu_keyboard_without_default_duo" not in duo_source
    assert "decorate_party_menu_without_default_duo" in duo_source

    runtime_source = _source("games/crocodile_runtime.py")
    assert runtime_source.count(
        "party_controls.menu_keyboard = _compose_party_menu_keyboard("
    ) == 1
    assert "duo_optin.decorate_party_menu_without_default_duo" in runtime_source


def test_duo_opt_in_does_not_replace_game_keyboard():
    duo_source = _source("games/crocodile_duo_optin.py")
    tree = ast.parse(duo_source)
    assigned = []

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "crocodile"
            ):
                assigned.append(target.attr)

    assert "get_game_keyboard" not in assigned
    assert "_original_get_game_keyboard" not in duo_source
    assert "get_game_keyboard_with_duo_opt_in" not in duo_source
    assert "decorate_game_keyboard_with_duo_opt_in" in duo_source
    assert "_base_game_keyboard" in duo_source

    runtime_source = _source("games/crocodile_runtime.py")
    assert runtime_source.count(
        "crocodile.get_game_keyboard = _compose_game_keyboard("
    ) == 1
    assert "base_game_keyboard = crocodile.get_game_keyboard" in runtime_source
    assert "duo_optin.decorate_game_keyboard_with_duo_opt_in" in runtime_source
    assert "base_game_keyboard=base_game_keyboard" in runtime_source


def test_duo_opt_in_does_not_replace_callback_handler():
    duo_source = _source("games/crocodile_duo_optin.py")
    tree = ast.parse(duo_source)
    assigned = []

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "crocodile"
            ):
                assigned.append(target.attr)

    assert "handle_callback" not in assigned
    assert "_original_handle_callback" not in duo_source
    assert "next_handler" in duo_source

    runtime_source = _source("games/crocodile_runtime.py")
    assert runtime_source.count(
        "crocodile.handle_callback = _compose_callback_handler("
    ) == 1
    assert "base_callback_handler = crocodile.handle_callback" in runtime_source
    assert "duo_optin.handle_duo_opt_in_callback" in runtime_source
    callback_wiring = runtime_source.index(
        "crocodile.handle_callback = _compose_callback_handler("
    )
    ui_install = runtime_source.index("configure_crocodile_ui_enhancements()")
    assert callback_wiring < ui_install


def test_duel_vote_deadline_is_owned_by_modes():
    modes_source = _source("games/crocodile_modes.py")
    assert modes_source.count(
        'duel["vote_deadline"] = time.time() + DUEL_VOTE_SECONDS'
    ) == 1


def test_bootstrap_uses_single_crocodile_composition_entrypoint():
    source = _source("app/bootstrap.py")
    assert "from games.crocodile_runtime import configure_crocodile_runtime" in source
    assert source.count("configure_crocodile_runtime()") == 1
    assert "configure_crocodile_canvas_restore" not in source
    assert "configure_crocodile_controls" not in source
    assert "configure_crocodile_single_words" not in source
