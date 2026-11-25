from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_party_commands_are_added_to_help():
    from games import crocodile_party_controls as controls
    from prompts import help_texts

    controls.install_crocodile_help()

    section = help_texts.HELP_DICT["crocodile"]
    assert "<code>кракадил</code> - единое меню" in section
    assert "<code>кракадил дуэль</code>" in section
    assert "<code>кракадил телефон</code>" in section
    assert "<code>кракадил галерея</code>" in section
