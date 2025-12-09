import os
import random
import asyncio
import logging
import base64
import wave
from aiogram import types, Bot
from aiogram.types import FSInputFile
from config import model, chat_settings, conversation_history, MAX_HISTORY_LENGTH, TTS_MODEL_NAME
from talking import update_chat_settings, get_current_chat_prompt, update_conversation_history, format_chat_history
from distortion import apply_ffmpeg_audio_distortion
import google.generativeai as genai

# Пытаемся импортировать настройки
try:
    from config import TTS_MODEL_NAME
except ImportError:
    TTS_MODEL_NAME = "gemini-2.5-flash-preview-tts" # Fallback

# Параметры для дисторшна по умолчанию
DEFAULT_DISTORTION_INTENSITY = 60 

async def generate_text_response_for_voice(chat_id: str, user_query: str) -> str:
    """
    Генерирует текстовый ответ от имени персонажа, используя логику talking.py,
    но возвращает чистый текст, не отправляя сообщение.
    """
    # 1. Получаем настройки чата
    update_chat_settings(chat_id)
    selected_prompt, prompt_name = get_current_chat_prompt(chat_id)
    
    # 2. Формируем историю (короткую)
    chat_history_formatted = format_chat_history(chat_id)
    
    # 3. Создаем промпт
    # Мы явно просим ответ покороче, так как это аудио
    full_prompt = (
        f"{selected_prompt}\n\n"
        f"Это голосовой ответ в чате. Твоя задача — ответить пользователю '{prompt_name}' голосом.\n"
        f"Отвечай коротко, емко, не используй сложные списки и markdown разметку.\n"
        f"Вопрос пользователя: {user_query}\n"
        f"{prompt_name}:"
    )

    try:
        def sync_gemini_call():
            # Используем обертку model из config
            response = model.generate_content(full_prompt, chat_id=chat_id)
            return response.text
            
        text_response = await asyncio.to_thread(sync_gemini_call)
        
        # Сохраняем в историю диалога
        update_conversation_history(chat_id, "User (Voice)", user_query, role="user")
        update_conversation_history(chat_id, prompt_name, text_response, role="assistant")
        
        return text_response
    except Exception as e:
        logging.error(f"Voice Text Gen Error: {e}")
        return "Кхе-кхе... Что-то горло першит, не могу говорить."

async def generate_audio_from_text(text: str, output_path: str) -> bool:
    """
    Использует Gemini TTS для генерации аудио.
    """
    try:
        # Прямой вызов Gemini API для TTS
        # ВАЖНО: Используем специальный конфиг для аудио
        
        # Выбираем случайный голос из доступных для разнообразия
        # (На данный момент Gemini поддерживает несколько голосов, возьмем 'Kore' или 'Charon' как дефолт)
        voice_name = random.choice(["Kore", "Fenrir", "Puck", "Charon"])
        
        generation_config = {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {
                        "voice_name": voice_name
                    }
                }
            }
        }

        # Вызываем модель напрямую через genai, так как TTS специфичен
        tts_model = genai.GenerativeModel(TTS_MODEL_NAME)
        
        def sync_tts_call():
            response = tts_model.generate_content(
                text,
                generation_config=generation_config
            )
            return response

        response = await asyncio.to_thread(sync_tts_call)
        
        # Обработка ответа (Gemini возвращает PCM audio)
        # Нам нужно достать аудио данные
        if not response.candidates:
            logging.error("Gemini TTS returned no candidates")
            return False
            
        part = response.candidates[0].content.parts[0]
        if not part.inline_data:
            logging.error("Gemini TTS returned no inline_data")
            return False

        # Данные приходят в base64 (обычно) или байтах внутри объекта
        # В Python SDK это обычно .data (bytes)
        audio_data = part.inline_data.data
        
        # Gemini возвращает Raw PCM (обычно 24kHz, mono, s16le)
        # Нам нужно завернуть это в WAV, чтобы ffmpeg понял
        
        # Параметры PCM от Gemini (стандартные для текущей preview)
        # Частота может меняться, но обычно 24000
        sample_rate = 24000 
        
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_data)
            
        return True

    except Exception as e:
        logging.error(f"Gemini TTS Error: {e}", exc_info=True)
        return False

async def handle_voice_command(message: types.Message, bot: Bot):
    """
    Обработчик команды 'упупа скажи ...'
    """
    chat_id = str(message.chat.id)
    
    # 1. Парсинг текста
    command_prefix = "упупа скажи"
    user_query = message.text[len(command_prefix):].strip()
    
    if not user_query:
        await message.reply("А что сказать-то, епта?")
        return

    # Отправляем экшен "запись голосового"
    await bot.send_chat_action(chat_id=message.chat.id, action="record_voice")
    
    processing_msg = await message.reply("🎤 Записываю голосовое...")

    # Временные файлы
    rand_id = random.randint(10000, 99999)
    temp_wav = f"temp_voice_{rand_id}.wav"
    temp_mp3 = f"voice_out_{rand_id}.mp3" # Финальный файл после дисторшна

    try:
        # 2. Генерируем текст ответа (в стиле персонажа)
        text_response = await generate_text_response_for_voice(chat_id, user_query)
        
        # Если ответ слишком длинный, обрезаем, иначе TTS может отвалиться или быть дорогим
        if len(text_response) > 500:
            text_response = text_response[:500] + "..."

        # 3. Генерируем аудио (WAV)
        tts_success = await generate_audio_from_text(text_response, temp_wav)
        
        if not tts_success:
            await processing_msg.edit_text("🤐 Голос пропал (ошибка генерации).")
            return

        # 4. Применяем дисторшн (WAV -> MP3)
        # Используем intensity 60 (средне-сильное искажение)
        distort_success = await apply_ffmpeg_audio_distortion(temp_wav, temp_mp3, DEFAULT_DISTORTION_INTENSITY)
        
        if not distort_success:
            # Если дисторшн не сработал, попробуем отправить оригинал (конвертировав, если нужно, но пока просто ошибку)
            await processing_msg.edit_text("🤐 Микрофон зафонил (ошибка обработки).")
            return

        # 5. Отправляем голосовое
        audio_file = FSInputFile(temp_mp3)
        await bot.send_voice(
            chat_id=message.chat.id,
            voice=audio_file,
            caption=f"🗣 Ответ на: {user_query[:20]}...",
            reply_to_message_id=message.message_id
        )
        
        await processing_msg.delete()

    except Exception as e:
        logging.error(f"Global Voice Handler Error: {e}", exc_info=True)
        await processing_msg.edit_text("Внутренняя ошибка голосового модуля.")
        
    finally:
        # Чистка
        if os.path.exists(temp_wav):
            os.remove(temp_wav)
        if os.path.exists(temp_mp3):
            os.remove(temp_mp3)
