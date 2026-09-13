from types import SimpleNamespace

from AI import dnd_campaign
from AI.dnd_epilogue_image import build_final_scene_prompt


def test_final_image_prompt_is_one_scene_not_a_comic_page():
    session = SimpleNamespace(
        scene_log=[
            "Алиса выбила дверь трактиром.",
            "Боря уронил проклятый кубок в колодец.",
            "Герои выбрались на площадь с трофеем.",
        ],
        participants={"1": {"user_id": 1, "name": "Алиса"}},
        character_profiles={
            "1": {
                "style": "язвительная искательница",
                "strength": "не теряется в бардаке",
                "weakness": "лезет проверять запретное",
                "special": "аварийный план из кармана",
                "gender": "женский",
            }
        },
    )

    prompt = build_final_scene_prompt(
        dnd_campaign,
        session,
        "Алиса унесла трофей, а площадь пришлось чинить ещё неделю.",
        style="cinematic adventure illustration",
    )

    lowered = prompt.casefold()
    assert "one cinematic full-frame" in lowered
    assert "one continuous scene" in lowered
    assert "no comic panels" in lowered
    assert "split screen" in lowered
    assert "speech bubbles" in lowered
    assert "four-panel" not in lowered
    assert "panel-to-panel" not in lowered
    assert "алиса унесла трофей" in lowered
