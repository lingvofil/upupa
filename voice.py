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
    Использует Gemini TTS для генерации аудио.
    Если модель перегружена (429), ждет и пробует снова (Retry).
    """
    try:
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

        # Функция для попытки генерации с ожиданием
        def sync_tts_call_with_retry():
            max_retries = 3
            # Начальная задержка больше, так как Google просит ~27 сек
            base_delay = 15 
            
            # Пробегаем по моделям в очереди (в нашем случае там одна)
            for model_name in TTS_MODELS_QUEUE:
                tts_model = genai.GenerativeModel(model_name)
                
                # Внутренний цикл повторов для одной модели
                for attempt in range(max_retries):
                    try:
                        logging.info(f"🎤 Trying TTS model: {model_name} with voice {voice_name} (Attempt {attempt+1})")
                        response = tts_model.generate_content(
                            text,
                            generation_config=generation_config
                        )
                        return response
                        
                    except exceptions.ResourceExhausted as e:
                        if attempt < max_retries - 1:
                            # Увеличиваем задержку экспоненциально + рандом
                            delay = base_delay * (attempt + 1) + random.uniform(1, 5)
                            logging.warning(f"⚠️ Quota exceeded for {model_name}. Sleeping for {delay:.1f}s...")
                            time.sleep(delay)
                        else:
                            logging.error(f"❌ Max retries reached for {model_name}.")
                            # Если есть другие модели в очереди - попробуем их
                            break 
                            
                    except Exception as e:
                        # Если 404 (Not Found), сразу переходим к следующей модели (break inner loop)
                        if "404" in str(e):
                             logging.error(f"❌ Model {model_name} not found (404). Skipping.")
                             break
                        
                        logging.error(f"Error with model {model_name}: {e}")
                        # Для других ошибок можно попробовать еще раз или выйти
                        if attempt < max_retries - 1:
                            time.sleep(5)
                        else:
                            break
            
            return None

        # Запускаем в отдельном потоке
        response = await asyncio.to_thread(sync_tts_call_with_retry)
        
        # Обработка ответа
        if not response or not response.candidates:
            logging.error("Gemini TTS returned no candidates")
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
            await processing_msg.edit_text("🤐 Голос сорвал (все модели перегружены, я подождал, но не вышло).")
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
