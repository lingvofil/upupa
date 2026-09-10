from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_party_commands_are_added_to_help():
    from games import crocodile_party_controls as controls
    from prompts import help_texts

    controls.install_crocodile_help()

    creative = help_texts.HELP_DICT["creative"]
    assert "<code>кракадил</code> - единое меню" in creative
    assert "<code>кракадил дуэль</code>" in creative
    assert "<code>кракадил телефон</code>" in creative
    assert "<code>кракадил галерея</code>" in creative
