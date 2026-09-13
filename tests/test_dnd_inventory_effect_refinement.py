from types import SimpleNamespace

from AI.dnd_inventory_effect_refinement import (
    OLD_PLACEHOLDER_EFFECTS,
    concrete_effect,
    refine_archive_data,
    refine_inventory_item,
    refine_session_inventory,
)


def test_old_artifact_placeholder_becomes_concrete_property():
    item = {
        "name": "Корона сантехника",
        "kind": "artifact",
        "effect": "явно хранит больше истории, чем объясняет",
    }

    assert refine_inventory_item(item) is True
    assert item["effect"] == (
        "делает владельца убедительнее, когда тот изображает важную персону"
    )
    assert item["effect"] not in OLD_PLACEHOLDER_EFFECTS


def test_old_generic_artifacts_get_stable_but_varied_properties():
    names = [
        "Сфера А",
        "Сфера Б",
        "Сфера В",
        "Сфера Г",
        "Сфера Д",
        "Сфера Е",
        "Сфера Ж",
        "Сфера З",
        "Сфера И",
        "Сфера К",
        "Сфера Л",
        "Сфера М",
    ]
    generated = [concrete_effect({"name": name, "kind": "artifact"}) for name in names]

    assert len(set(generated)) >= 8
    assert all(effect not in OLD_PLACEHOLDER_EFFECTS for effect in generated)
    assert generated[0] == concrete_effect({"name": names[0], "kind": "artifact"})


def test_authored_effect_is_never_rewritten():
    item = {
        "name": "Перстень мокрого барона",
        "kind": "artifact",
        "effect": "звенит рядом с болотной нечистью",
    }

    assert refine_inventory_item(item) is False
    assert item["effect"] == "звенит рядом с болотной нечистью"


def test_archive_refinement_rewrites_old_placeholders_everywhere():
    archive = {
        "chats": {
            "-100": {
                "players": {
                    "1": {
                        "inventory": [
                            {
                                "name": "Кусок стекла",
                                "kind": "item",
                                "effect": "пригодится в самый неподходящий момент",
                            }
                        ],
                        "artifacts": [
                            {
                                "name": "Череп бухгалтера",
                                "kind": "artifact",
                                "effect": "слишком важен, чтобы просто валяться в кармане",
                            }
                        ],
                    }
                },
                "campaigns": [
                    {
                        "inventories": {
                            "1": [
                                {
                                    "name": "Карта канализации",
                                    "kind": "artifact",
                                    "effect": "ведёт себя так, будто у него есть собственный план",
                                }
                            ]
                        }
                    }
                ],
            }
        }
    }

    assert refine_archive_data(archive) is True

    player = archive["chats"]["-100"]["players"]["1"]
    assert player["inventory"][0]["effect"] not in OLD_PLACEHOLDER_EFFECTS
    assert player["artifacts"][0]["effect"] == (
        "дёргается рядом с существами, которые явно что-то недоговаривают"
    )
    campaign_item = archive["chats"]["-100"]["campaigns"][0]["inventories"]["1"][0]
    assert campaign_item["effect"] == (
        "уверенно указывает направление, не обещая, что туда стоило идти"
    )
    assert refine_archive_data(archive) is False


def test_active_session_refinement_rewrites_all_placeholders_and_preserves_authored():
    first_placeholder = {
        "name": "Непонятная хрень",
        "kind": "artifact",
        "effect": "подозрительно реагирует на серьёзные неприятности",
    }
    second_placeholder = {
        "name": "Ещё более непонятная хрень",
        "kind": "artifact",
        "effect": "явно хранит больше истории, чем объясняет",
    }
    authored = {
        "name": "Рабочий фонарь",
        "kind": "item",
        "effect": "слепит охранника на близкой дистанции",
    }
    session = SimpleNamespace(
        inventories={"1": [first_placeholder, second_placeholder, authored]}
    )

    assert refine_session_inventory(session) is True
    assert first_placeholder["effect"] not in OLD_PLACEHOLDER_EFFECTS
    assert second_placeholder["effect"] not in OLD_PLACEHOLDER_EFFECTS
    assert authored["effect"] == "слепит охранника на близкой дистанции"
    assert refine_session_inventory(session) is False
