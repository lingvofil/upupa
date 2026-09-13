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
        "configure_crocodile_party_controls()",
        "configure_crocodile_duo_opt_in()",
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
