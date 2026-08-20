import asyncio

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def test_chotam_result_is_routed_through_current_chat_prompt(monkeypatch):
    from AI import whatisthere_prompt as wp

    captured = {}

    def fake_build(chat_id, task_prompt, task_name=""):
        captured["chat_id"] = chat_id
        captured["task_prompt"] = task_prompt
        captured["task_name"] = task_name
        return "CURRENT CHAT PERSONA\n" + task_prompt

    async def fake_generate(prompt, chat_id):
        captured["generated_prompt"] = prompt
        captured["generated_chat_id"] = chat_id
        return "извините пожалуйста, на фото голубь"

    monkeypatch.setattr(wp, "build_prompt_with_current_chat_prompt", fake_build)
    monkeypatch.setattr(wp, "generate_with_active_model", fake_generate)

    result = asyncio.run(
        wp.apply_current_prompt_to_whatisthere("На фотографии изображён голубь.", 12345)
    )

    assert result == "извините пожалуйста, на фото голубь"
    assert captured["chat_id"] == "12345"
    assert captured["generated_chat_id"] == 12345
    assert captured["task_name"] == "финальный ответ команды «чотам»"
    assert "На фотографии изображён голубь." in captured["task_prompt"]
    assert "CURRENT CHAT PERSONA" in captured["generated_prompt"]
    assert "Сохрани факты" in captured["task_prompt"]


def test_chotam_prompt_pass_falls_back_to_raw_result(monkeypatch):
    from AI import whatisthere_prompt as wp

    monkeypatch.setattr(
        wp,
        "build_prompt_with_current_chat_prompt",
        lambda *args, **kwargs: "styled prompt",
    )

    async def fail_generate(prompt, chat_id):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(wp, "generate_with_active_model", fail_generate)

    raw = "На фотографии изображён голубь."
    result = asyncio.run(wp.apply_current_prompt_to_whatisthere(raw, 12345))

    assert result == raw


def test_empty_chotam_result_does_not_call_model(monkeypatch):
    from AI import whatisthere_prompt as wp

    async def must_not_run(*args, **kwargs):
        raise AssertionError("model must not be called for an empty result")

    monkeypatch.setattr(wp, "generate_with_active_model", must_not_run)

    assert asyncio.run(wp.apply_current_prompt_to_whatisthere("", 12345)) == ""
