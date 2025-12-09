import os
import random
import asyncio
import logging
import base64
import wave
import time
from aiogram import types, Bot
from aiogram.types import FSInputFile
from config import model, chat_settings, conversation_history, MAX_HISTORY_LENGTH, TTS_MODELS_QUEUE
from talking import update_chat_settings, get_current_chat_prompt, update_conversation_history, format_chat_history
from distortion import apply_ffmpeg_audio_distortion
import google.generativeai as genai
from google.api_core import exceptions

# Если в конфиге вдруг нет очереди, делаем fallback
if not 'TTS_MODELS_QUEUE' in locals() and not 'TTS_MODELS_QUEUE' in globals():
    try:
        from config import TTS_MODELS_QUEUE
    except ImportError:
        TTS_MODELS_QUEUE = ["gemini-2.5-flash-preview-tts"]

# Полный список доступных голосов
AVAILABLE_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", 
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba", 
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar", 
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi", 
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"
]

# Параметры для дисторшна по умолчанию
DEFAULT_DISTORTION_INTENSITY = 60 

async def generate_text_response_for_voice(chat_id: str, user_query: str) -> str:
    """
    Генерирует текстовый ответ от имени персонажа.
    """
    update_chat_settings(chat_id)
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
        def sync_gemini_call():
            response = model.generate_content(full_prompt, chat_id=chat_id)
            return response.text
            
        text_response = await asyncio.to_thread(sync_gemini_call)
        
        update_conversation_history(chat_id, "User (Voice)", user_query, role="user")
        update_conversation_history(chat_id, prompt_name, text_response, role="assistant")
        
        return text_response
    except Exception as e:
        logging.error(f"Voice Text Gen Error: {e}")
        return "Кхе-кхе... Что-то горло першит, не могу говорить."

async def generate_audio_from_text(text: str, output_path: str) -> bool:
    """
    Использует Gemini TTS (с ротацией моделей) для генерации аудио.
    """
    try:
        # Выбираем случайный голос из полного списка
        voice_name = random.choice(AVAILABLE_VOICES)
        
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

        # Функция для попытки генерации через разные модели
        def sync_tts_call_with_fallback():
            last_error = None
            
            # Пробегаем по всем доступным моделям TTS
            for model_name in TTS_MODELS_QUEUE:
                try:
                    logging.info(f"🎤 Trying TTS model: {model_name} with voice {voice_name}")
                    tts_model = genai.GenerativeModel(model_name)
                    
                    response = tts_model.generate_content(
                        text,
                        generation_config=generation_config
                    )
                    return response
                    
                except exceptions.ResourceExhausted:
                    logging.warning(f"⚠️ Quota exceeded for {model_name}. Switching to next model...")
                    continue # Пробуем следующую модель в списке
                    
                except Exception as e:
                    logging.error(f"Error with model {model_name}: {e}")
                    last_error = e
                    continue # Пробуем следующую модель даже при других ошибках
            
            # Если вышли из цикла и ничего не вернули
            if last_error:
                raise last_error
            return None

        # Запускаем в отдельном потоке
        response = await asyncio.to_thread(sync_tts_call_with_fallback)
        
        # Обработка ответа
        if not response or not response.candidates:
            logging.error("Gemini TTS returned no candidates (all models failed or empty response)")
            return False
            
        part = response.candidates[0].content.parts[0]
        if not part.inline_data:
            logging.error("Gemini TTS returned no inline_data")
            return False

        audio_data = part.inline_data.data
        
        # Параметры PCM от Gemini
        sample_rate = 24000 
        
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_data)
            
        return True

    except Exception as e:
        logging.error(f"Gemini TTS Final Error: {e}")
        return False

async def handle_voice_command(message: types.Message, bot: Bot):
    """
    Обработчик команды 'упупа скажи ...'
    """
    chat_id = str(message.chat.id)
    command_prefix = "упупа скажи"
    user_query = message.text[len(command_prefix):].strip()
    
    if not user_query:
        await message.reply("А что сказать-то, епта?")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="record_voice")
    processing_msg = await message.reply("🎤 Записываю голосовое...")

    rand_id = random.randint(10000, 99999)
    temp_wav = f"temp_voice_{rand_id}.wav"
    temp_mp3 = f"voice_out_{rand_id}.mp3" 

    try:
        text_response = await generate_text_response_for_voice(chat_id, user_query)
        if len(text_response) > 500:
            text_response = text_response[:500] + "..."

        tts_success = await generate_audio_from_text(text_response, temp_wav)
        
        if not tts_success:
            await processing_msg.edit_text("🤐 Голос сорвал (все модели перегружены).")
            return

        distort_success = await apply_ffmpeg_audio_distortion(temp_wav, temp_mp3, DEFAULT_DISTORTION_INTENSITY)
        
        if not distort_success:
            await processing_msg.edit_text("🤐 Микрофон зафонил (ошибка обработки).")
            return

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
        if os.path.exists(temp_wav):
            os.remove(temp_wav)
        if os.path.exists(temp_mp3):
            os.remove(temp_mp3)
