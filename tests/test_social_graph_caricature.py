import asyncio
from io import BytesIO
import sys
from types import ModuleType, SimpleNamespace

from PIL import Image, ImageDraw

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


def _portrait_sheet() -> bytes:
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    colors = ["red", "green", "blue", "yellow", "purple", "orange"]
    for index, color in enumerate(colors):
        col = index % 3
        row = index // 3
        x0 = col * 200
        y0 = row * 200
        draw.ellipse((x0 + 45, y0 + 45, x0 + 155, y0 + 155), fill=color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_cringe_prompt_requests_only_anonymous_portrait_sheet():
    from features.social_graph.caricature import build_cringe_social_graph_prompt

    prompt = build_cringe_social_graph_prompt(_view(), 30)

    assert "EXACTLY SIX" in prompt
    assert "3 columns x 2 rows" in prompt
    assert "ABSOLUTELY NO TEXT" in prompt
    assert "no arrows" in prompt
    assert "do not connect" in prompt
    assert "bitmap-paint-program" in prompt
    assert "Alice" not in prompt
    assert "Bob" not in prompt
    assert "Carol" not in prompt
    assert "2007" not in prompt
    assert "Telegram" not in prompt


def test_social_graph_image_fallback_reuses_same_english_prompt(monkeypatch):
    import AI
    from features.social_graph.image_generation import generate_social_graph_image

    calls = []

    async def gigachat(prompt):
        calls.append(("gigachat", prompt))
        return None

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

    prompt = "already English, no text"
    image, provider = asyncio.run(generate_social_graph_image(prompt))

    assert image == b"pollinations-image"
    assert provider == "pollinations"
    assert calls == [
        ("gigachat", prompt),
        ("pollinations", prompt),
    ]


def test_cringe_renderer_composes_exact_graph_from_portrait_sheet():
    from features.social_graph.rendering import (
        CANVAS_HEIGHT,
        CANVAS_WIDTH,
        _cringe_positions,
        _extract_cringe_portraits,
        render_cringe_graph_png,
    )

    sheet = _portrait_sheet()
    portraits = _extract_cringe_portraits(sheet)
    assert len(portraits) == 6
    assert all(portrait.size == (170, 170) for portrait in portraits)

    positions, central_id = _cringe_positions(_view())
    assert set(positions) == {1, 2, 3}
    assert central_id == 1
    assert positions[central_id] == (600, 440)

    result = render_cringe_graph_png(_view(), sheet)
    rendered = Image.open(BytesIO(result))
    assert rendered.size == (CANVAS_WIDTH, CANVAS_HEIGHT)
    assert rendered.format == "PNG"


def test_cringe_renderer_passes_real_labels_to_pillow(monkeypatch):
    import features.social_graph.rendering as rendering
    from features.social_graph.analysis import GraphEdge, RenderGraph, RenderNode

    graph = RenderGraph(
        nodes=(
            RenderNode(1, "М&M", 12.0),
            RenderNode(2, "Детектор", 10.0),
            RenderNode(3, "Чудо В Стране Алис 🍀", 7.0),
        ),
        edges=(
            GraphEdge(1, 2, 8.0, 7.0),
            GraphEdge(1, 3, 6.0, 1.0),
        ),
        total_node_count=3,
        total_edge_count=2,
    )
    labels = []
    real_draw_label = rendering._draw_cringe_label

    def capture(draw, center, label):
        labels.append(label)
        return real_draw_label(draw, center, label)

    monkeypatch.setattr(rendering, "_draw_cringe_label", capture)
    rendering.render_cringe_graph_png(graph, _portrait_sheet())

    assert set(labels) == {"М&M", "Детектор", "Чудо В Стране Алис 🍀"}


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
