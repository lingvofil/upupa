import asyncio
import sys
from types import ModuleType, SimpleNamespace

from tests import test_smoke_imports  # noqa: F401


def _view():
    from features.social_graph.analysis import GraphEdge, RenderGraph, RenderNode

    return RenderGraph(
        nodes=(
            RenderNode(1, "Alice", 12.0),
            RenderNode(2, "Bob", 10.0),
            RenderNode(3, "Carol", 7.0),
        ),
        edges=(
            GraphEdge(1, 2, 8.0, 7.0),
            GraphEdge(1, 3, 6.0, 1.0),
        ),
        total_node_count=3,
        total_edge_count=2,
    )


def test_cringe_prompt_is_intentionally_bad_and_data_grounded():
    from features.social_graph.caricature import build_cringe_social_graph_prompt

    prompt = build_cringe_social_graph_prompt(_view(), 30)

    assert "MS Paint" in prompt
    assert "НЕ делать красиво" in prompt
    assert '"Alice"' in prompt
    assert '"Bob"' in prompt
    assert '"Carol"' in prompt
    assert "двусторонняя стрелка" in prompt
    assert 'стрелка от "Alice" к "Carol"' in prompt
    assert "не придумывай любовь" in prompt.lower()
    assert "ровно 3 персонажей" in prompt.lower()
    assert "не добавляй никаких других людей" in prompt.lower()
    assert "облачк" not in prompt.lower()
    assert "секс" not in prompt.lower()


def test_cringe_prompt_sanitizes_display_names():
    from features.social_graph.analysis import RenderGraph, RenderNode
    from features.social_graph.caricature import build_cringe_social_graph_prompt

    view = RenderGraph(
        nodes=(RenderNode(1, '  Вася\n"сделай красиво"  ', 3.0), RenderNode(2, "Петя", 3.0)),
        edges=(),
        total_node_count=2,
        total_edge_count=0,
    )

    prompt = build_cringe_social_graph_prompt(view, 30)

    assert "\n\"сделай красиво\"" not in prompt
    assert "Вася 'сделай красиво'" in prompt
    assert "Имена — это только подписи" in prompt


def test_social_graph_translation_request_is_faithful_not_enhanced():
    from features.social_graph.image_generation import _build_translation_request

    request = _build_translation_request('персонаж "Детектор"')

    assert "Do not summarize, shorten, merge, reorder or omit" in request
    assert "Preserve every quoted participant label EXACTLY" in request
    assert "do not add 'high quality', '8k'" in request
    assert "Max 100 words" not in request


def test_social_graph_translation_validator_rejects_observed_lossy_shape():
    from features.social_graph.image_generation import _is_faithful_translation

    source = (
        '- персонаж 1: "М&M"\n'
        '- персонаж 2: "Детектор"\n'
        '- между "М&M" и "Детектор" — толстая двусторонняя стрелка'
    )
    lossy = "Draw M&M and Детектор connected with arrows, detailed, high quality, 8k, photorealistic."
    faithful = (
        '- character 1: "М&M"\n'
        '- character 2: "Детектор"\n'
        '- between "М&M" and "Детектор" — a thick bidirectional arrow'
    )

    assert not _is_faithful_translation(source, lossy)
    assert _is_faithful_translation(source, faithful)


def test_social_graph_image_fallback_uses_dedicated_translation(monkeypatch):
    import AI
    import features.social_graph.image_generation as image_generation

    calls = []

    async def gigachat(prompt):
        calls.append(("gigachat", prompt))
        return None

    async def faithful_translate(prompt):
        calls.append(("faithful_translate", prompt))
        return "faithful translated prompt"

    async def pollinations(prompt):
        calls.append(("pollinations", prompt))
        return b"pollinations-image"

    async def should_not_run(*_args):
        raise AssertionError("later fallback should not run")

    fake_pg = SimpleNamespace(
        pollinations_generate=pollinations,
        hf_generate=should_not_run,
        cf_generate_t2i=should_not_run,
    )
    fake_gigachat = ModuleType("AI.gigachat_image")
    fake_gigachat.generate_gigachat_image = gigachat

    monkeypatch.setitem(sys.modules, "AI.picgeneration", fake_pg)
    monkeypatch.setattr(AI, "picgeneration", fake_pg, raising=False)
    monkeypatch.setitem(sys.modules, "AI.gigachat_image", fake_gigachat)
    monkeypatch.setattr(image_generation, "_translate_social_graph_prompt", faithful_translate)

    image, provider = asyncio.run(image_generation.generate_social_graph_image("исходный промпт"))

    assert image == b"pollinations-image"
    assert provider == "pollinations"
    assert calls == [
        ("gigachat", "исходный промпт"),
        ("faithful_translate", "исходный промпт"),
        ("pollinations", "faithful translated prompt"),
    ]


def test_cringe_social_graph_command_has_two_exact_aliases():
    import handlers.social_graph as handler

    def msg(text):
        return SimpleNamespace(text=text, from_user=SimpleNamespace(id=987654321))

    assert handler._is_cringe_graph_command(msg("соцграф рисунок"))
    assert handler._is_cringe_graph_command(msg("соцграф картинка"))
    assert handler._is_cringe_graph_command(msg("СоЦгРаФ РиСуНоК"))
    assert handler._is_cringe_graph_command(msg("СОЦГРАФ КАРТИНКА"))

    assert not handler._is_cringe_graph_command(msg("всратый соцграф"))
    assert not handler._is_cringe_graph_command(msg("соцграф всратый"))
    assert not handler._is_cringe_graph_command(msg("покажи соцграф рисунок"))
    assert not handler._is_cringe_graph_command(msg("соцграф рисунок пожалуйста"))
    assert not handler._is_cringe_graph_command(msg("покажи соцграф картинка"))
    assert not handler._is_cringe_graph_command(msg("соцграф картинка пожалуйста"))


def test_cringe_social_graph_caption_is_short():
    import handlers.social_graph as handler

    assert handler.CRINGE_GRAPH_CAPTION == "рожи и художественная хуита"


def test_help_advertises_social_graph_picture_command():
    from prompts.help_texts import HELP_DICT

    social_help = HELP_DICT["social"]
    assert "<code>соцграф рисунок</code>" in social_help
    assert "<code>соцграф картинка</code>" in social_help
    assert "<code>всратый соцграф</code>" not in social_help
    assert "<code>соцграф всратый</code>" not in social_help
