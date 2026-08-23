from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_imperfect_language_mode_is_rare_and_requests_one_natural_error():
    from prompts.channel import POST_CONTENT_MODES

    weights = {mode["name"]: mode["weight"] for mode in POST_CONTENT_MODES}
    assert sum(weights.values()) == 100
    assert weights["absurd"] == 38
    assert weights["imperfect"] == 5

    imperfect = next(mode for mode in POST_CONTENT_MODES if mode["name"] == "imperfect")
    instruction = imperfect["instruction"].casefold()

    assert "ровно одну" in instruction
    assert "опечат" in instruction
    assert "орфограф" in instruction
    assert "синтаксис" in instruction
    assert "нечитаем" in instruction


def test_error_instruction_is_not_present_in_normal_content_modes():
    from prompts.channel import POST_CONTENT_MODES

    normal_instructions = "\n".join(
        mode["instruction"]
        for mode in POST_CONTENT_MODES
        if mode["name"] != "imperfect"
    ).casefold()

    assert "опечат" not in normal_instructions
    assert "орфограф" not in normal_instructions
    assert "синтаксис" not in normal_instructions


def test_imperfect_instruction_reaches_prompt_only_when_mode_is_selected():
    from features.channel.service import _build_prompt
    from prompts.channel import POST_CONTENT_MODES, POST_LENGTH_MODES

    micro = next(mode for mode in POST_LENGTH_MODES if mode["name"] == "micro")
    absurd = next(mode for mode in POST_CONTENT_MODES if mode["name"] == "absurd")
    imperfect = next(mode for mode in POST_CONTENT_MODES if mode["name"] == "imperfect")

    ordinary_prompt = _build_prompt([], None, micro, absurd, False).casefold()
    imperfect_prompt = _build_prompt([], None, micro, imperfect, False).casefold()

    assert "опечат" not in ordinary_prompt
    assert "ровно одну" not in ordinary_prompt
    assert "опечат" in imperfect_prompt
    assert "ровно одну" in imperfect_prompt
