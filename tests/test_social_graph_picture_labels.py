import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw

from tests import test_smoke_imports  # noqa: F401


def _portrait_sheet() -> bytes:
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    for index, color in enumerate(("red", "green", "blue", "yellow", "purple", "orange")):
        col = index % 3
        row = index // 3
        x0 = col * 200
        y0 = row * 200
        draw.ellipse((x0 + 45, y0 + 45, x0 + 155, y0 + 155), fill=color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_picture_label_nfkc_normalizes_styled_unicode():
    from features.social_graph.rendering import _load_font, _sanitize_label_for_font

    assert _sanitize_label_for_font("𝐒𝐎𝐍𝐍𝐄", _load_font(26)) == "SONNE"


def test_picture_label_drops_unsupported_glyphs_without_tofu_square():
    from features.social_graph.rendering import _load_font, _sanitize_label_for_font

    label = _sanitize_label_for_font("Чудо В Стране Алис 🍀", _load_font(26))

    assert label == "Чудо В Стране Алис"
    assert "🍀" not in label
    assert "□" not in label


def test_picture_label_keeps_plain_cyrillic_latin_digits_and_ampersand():
    from features.social_graph.rendering import _load_font, _sanitize_label_for_font

    label = "Детектор SONNE М&M 123"
    assert _sanitize_label_for_font(label, _load_font(26)) == label


def test_edge_keyword_uses_real_interaction_type_breakdown():
    from features.social_graph.analysis import aggregate_edges
    from features.social_graph.interaction_analysis import build_edge_interaction_profiles

    cases = (
        ([(1, 2, "mention", 4.0)], "пинги"),
        ([(1, 2, "reaction", 4.0)], "реакты"),
        ([(1, 2, "reply", 3.0), (1, 2, "mention", 3.0)], "винегрет"),
    )
    for interactions, expected in cases:
        edge = aggregate_edges(interactions)[0]
        profile = build_edge_interaction_profiles(interactions, (edge,))[(1, 2)]
        assert profile.keyword == expected


def test_reply_keyword_distinguishes_mutual_from_asymmetric():
    from features.social_graph.analysis import aggregate_edges
    from features.social_graph.interaction_analysis import build_edge_interaction_profiles

    mutual_interactions = [(1, 2, "reply", 3.0), (2, 1, "reply", 3.0)]
    asymmetric_interactions = [(1, 2, "reply", 9.0), (2, 1, "reply", 3.0)]
    mutual_edge = aggregate_edges(mutual_interactions)[0]
    asymmetric_edge = aggregate_edges(asymmetric_interactions)[0]
    mutual = build_edge_interaction_profiles(mutual_interactions, (mutual_edge,))[(1, 2)]
    asymmetric = build_edge_interaction_profiles(asymmetric_interactions, (asymmetric_edge,))[(1, 2)]

    assert mutual.keyword == "срач"
    assert asymmetric.keyword == "доёб"


def test_cringe_renderer_draws_exactly_one_keyword_per_visible_edge(monkeypatch):
    import features.social_graph.rendering as rendering
    from features.social_graph.analysis import aggregate_edges, select_render_graph
    from features.social_graph.interaction_analysis import build_edge_interaction_profiles, edge_keywords

    interactions = [
        (1, 2, "reply", 3.0),
        (2, 1, "reply", 3.0),
        (1, 3, "mention", 4.0),
        (3, 1, "reaction", 1.0),
    ]
    view = select_render_graph(
        aggregate_edges(interactions),
        {1: "Детектор", 2: "𝐒𝐎𝐍𝐍𝐄", 3: "Чудо 🍀"},
        max_nodes=6,
        max_edges=7,
    )
    keywords = []

    def capture(_draw, _center, keyword):
        keywords.append(keyword)

    profiles = build_edge_interaction_profiles(interactions, view.edges)
    monkeypatch.setattr(rendering, "_draw_cringe_edge_keyword", capture)
    rendering.render_cringe_graph_png(view, _portrait_sheet(), edge_keywords(profiles))

    assert len(keywords) == len(view.edges)
    assert set(keywords) == {"срач", "пинги"}


def test_cringe_explanation_is_interpretive_instead_of_percentage_dump():
    from features.social_graph.analysis import aggregate_edges, select_render_graph
    from features.social_graph.interaction_analysis import (
        build_cringe_graph_explanation,
        build_edge_interaction_profiles,
    )

    interactions = [
        (1, 2, "reply", 9.0),
        (2, 1, "reply", 3.0),
        (1, 3, "reaction", 4.0),
    ]
    names = {1: "Детектор", 2: "М&M", 3: "Alina"}
    view = select_render_graph(aggregate_edges(interactions), names, max_nodes=6, max_edges=7)

    profiles = build_edge_interaction_profiles(interactions, view.edges)
    text = build_cringe_graph_explanation(view, profiles, names)

    assert text.startswith("Если перевести этот позор с языка статистики:")
    assert "Детектор → М&M" in text
    assert "доёб" in text
    assert "персональную подписку" in text
    assert "Детектор → Alina" in text
    assert "реакты" in text
    assert "кнопками реакций" in text
    assert "%" not in text
    assert "реплаи 100" not in text
    assert "реакты 100" not in text


def test_mixed_explanation_describes_the_mix_without_numbers():
    from features.social_graph.analysis import aggregate_edges, select_render_graph
    from features.social_graph.interaction_analysis import (
        build_cringe_graph_explanation,
        build_edge_interaction_profiles,
    )

    interactions = [
        (1, 2, "reply", 3.0),
        (1, 2, "mention", 3.0),
        (2, 1, "reaction", 2.5),
    ]
    names = {1: "Детектор", 2: "SONNE"}
    view = select_render_graph(aggregate_edges(interactions), names, max_nodes=6, max_edges=7)
    profiles = build_edge_interaction_profiles(interactions, view.edges)

    text = build_cringe_graph_explanation(view, profiles, names)

    assert "винегрет" in text
    assert "реплаи" in text
    assert "пинги" in text
    assert "реакции" in text
    assert "%" not in text


def test_cringe_handler_sends_explanation_after_picture(monkeypatch):
    import handlers.social_graph as handler
    from features.social_graph.service import SocialGraphData

    async def fake_data(_chat_id):
        return SocialGraphData(
            interactions=(
                (1, 2, "reply", 3.0),
                (2, 1, "reply", 3.0),
            ),
            names={1: "Детектор", 2: "М&M"},
            period_days=30,
        )

    async def fake_generate(_prompt):
        return b"portrait-sheet", "fake"

    async def fake_render(_view, _portrait_sheet, _edge_keywords):
        return b"png"

    calls = []

    async def reply(text):
        calls.append(("reply", text))

    async def answer_photo(_photo, caption=None):
        calls.append(("photo", caption))

    async def answer(text):
        calls.append(("text", text))

    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="supergroup"),
        from_user=SimpleNamespace(id=1),
        reply=reply,
        answer_photo=answer_photo,
        answer=answer,
    )
    monkeypatch.setattr(handler, "is_social_graph_enabled", lambda _chat_id: True)
    monkeypatch.setattr(handler, "get_graph_data", fake_data)
    monkeypatch.setattr(handler, "generate_social_graph_image", fake_generate)
    monkeypatch.setattr(handler, "render_cringe_graph_png_async", fake_render)

    asyncio.run(handler.handle_cringe_social_graph(message))

    assert [kind for kind, _value in calls][-2:] == ["photo", "text"]
    assert "срач" in calls[-1][1]
    assert "взаимный абонемент" in calls[-1][1]
    assert "%" not in calls[-1][1]
