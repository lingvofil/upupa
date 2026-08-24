"""Text response + legacy distorted voice command."""

import asyncio
import logging
import tempfile
from pathlib import Path

from aiogram import Bot, types
from aiogram.types import BufferedInputFile

from AI.dialog.generation import format_chat_history, update_conversation_history
from AI.dialog.settings import get_current_chat_prompt, update_chat_settings
from core.settings import TEXT_GENERATION_MODEL_LIGHT
from core.state import chat_settings
from infrastructure.ai.clients import gemini_client, gigachat_model, groq_ai, model
from infrastructure.ai.gemini import GeminiModel
from services.distortion import apply_ffmpeg_audio_distortion
from services.speech import SpeechSynthesisError, synthesize_speech


DEFAULT_DISTORTION_INTENSITY = 60


async def generate_text_response_for_voice(chat_id: str, user_query: str) -> str:
    """Generate the short spoken answer using the chat's active model/persona."""
    update_chat_settings(chat_id)
    current_settings = chat_settings.get(chat_id, {})
    active_model = current_settings.get("active_model", "gemini")

    selected_prompt, prompt_name = get_current_chat_prompt(chat_id)
    chat_history_formatted = format_chat_history(chat_id)

    full_prompt = (
        f"{selected_prompt}\n\n"
        f"Это голосовой ответ в чате. Твоя задача — ответить пользователю '{prompt_name}' голосом.\n"
        f"Отвечай коротко, емко, не используй сложные списки и markdown разметку.\n"
        f"Вопрос пользователя: {user_query}\n"
        f"{prompt_name}:"
    )

    try:
        if active_model == "history":
            logging.info("Voice: режим 'history' не поддерживается, переключаюсь на gemini")
            active_model = "gemini"

        logging.info("Voice text generation: используется модель %s", active_model)

        def sync_model_call():
            if active_model == "gigachat":
                response = gigachat_model.generate_content(full_prompt, chat_id=int(chat_id))
                return response.text
            if active_model == "groq":
                return groq_ai.generate_text(full_prompt, max_tokens=500)

            light_model = GeminiModel(gemini_client, TEXT_GENERATION_MODEL_LIGHT)
            response = light_model.generate_content(full_prompt)
            return response.text

        text_response = await asyncio.to_thread(sync_model_call)

        update_conversation_history(chat_id, "User (Voice)", user_query, role="user")
        update_conversation_history(chat_id, prompt_name, text_response, role="assistant")
        return text_response
    except Exception as exc:
        logging.error("Voice Text Gen Error (%s): %s", active_model, exc)
        try:
            logging.info("Fallback to main Gemini model for voice text...")

            def sync_fallback_call():
                return model.generate_content(full_prompt, chat_id=int(chat_id)).text

            return await asyncio.to_thread(sync_fallback_call)
        except Exception as fallback_exc:
            logging.error("Fallback Voice Text Gen Error: %s", fallback_exc)
            return "Кхе-кхе... Что-то горло першит, не могу говорить."


async def handle_voice_command(message: types.Message, bot: Bot):
    """Legacy voice command: clean shared TTS followed by distortion."""
    chat_id = str(message.chat.id)
    normalized_text = message.text or ""
    command_prefix = "упупа скажи"
    user_query = normalized_text[len(command_prefix):].strip()

    if not user_query:
        await message.reply("А что сказать-то, епта?")
        return

    update_chat_settings(chat_id)
    current_settings = chat_settings.get(chat_id, {})
    active_model = current_settings.get("active_model", "gemini")

    await bot.send_chat_action(chat_id=message.chat.id, action="record_voice")
    processing_msg = await message.reply("🎤 Записываю голосовое...")

    try:
        text_response = await generate_text_response_for_voice(chat_id, user_query)
        if len(text_response) > 500:
            text_response = text_response[:500] + "..."

        provider_order = ("groq",) if active_model == "groq" else ("gemini",)
        try:
            clean_audio = await synthesize_speech(
                text_response,
                provider_order=provider_order,
                allow_groq_for_cyrillic=active_model == "groq",
            )
        except SpeechSynthesisError:
            logging.exception("Voice clean TTS failed")
            await processing_msg.edit_text("🤐 Голос сорвал (все модели перегружены).")
            return

        with tempfile.TemporaryDirectory(prefix="upupa_voice_") as temp_dir:
            clean_path = Path(temp_dir) / "clean.mp3"
            distorted_path = Path(temp_dir) / "distorted.mp3"
            await asyncio.to_thread(clean_path.write_bytes, clean_audio.data)

            distort_success = await apply_ffmpeg_audio_distortion(
                str(clean_path),
                str(distorted_path),
                DEFAULT_DISTORTION_INTENSITY,
            )
            if not distort_success:
                await processing_msg.edit_text("🤐 Микрофон зафонил (ошибка обработки).")
                return

            distorted_bytes = await asyncio.to_thread(distorted_path.read_bytes)
            await bot.send_voice(
                chat_id=message.chat.id,
                voice=BufferedInputFile(distorted_bytes, filename="upupa-voice.mp3"),
                reply_to_message_id=message.message_id,
            )

        await processing_msg.delete()
    except Exception as exc:
        logging.error("Global Voice Handler Error: %s", exc, exc_info=True)
        await processing_msg.edit_text("Внутренняя ошибка голосового модуля.")
