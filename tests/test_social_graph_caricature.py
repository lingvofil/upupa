from types import SimpleNamespace

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


def test_cringe_social_graph_command_has_two_exact_aliases():
    import handlers.social_graph as handler

    def msg(text):
        return SimpleNamespace(text=text, from_user=SimpleNamespace(id=987654321))

    assert handler._is_cringe_graph_command(msg("всратый соцграф"))
    assert handler._is_cringe_graph_command(msg("Соцграф всратый"))
    assert not handler._is_cringe_graph_command(msg("покажи всратый соцграф"))
    assert not handler._is_cringe_graph_command(msg("всратый соцграф пожалуйста"))


def test_help_advertises_cringe_social_graph():
    from prompts.help_texts import HELP_DICT

    assert "<code>всратый соцграф</code>" in HELP_DICT["social"]
