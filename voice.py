import os
import random
import asyncio
import logging
import base64
import wave
import time
from aiogram import types, Bot
from aiogram.types import FSInputFile
from config import model, chat_settings, conversation_history, MAX_HISTORY_LENGTH, TTS_MODELS_QUEUE, TEXT_GENERATION_MODEL_LIGHT
from talking import update_chat_settings, get_current_chat_prompt, update_conversation_history, format_chat_history
from distortion import apply_ffmpeg_audio_distortion
import google.generativeai as genai
from google.api_core import exceptions

# Fallbacks
if not 'TTS_MODELS_QUEUE' in locals() and not 'TTS_MODELS_QUEUE' in globals():
    try:
        from config import TTS_MODELS_QUEUE
    except ImportError:
        TTS_MODELS_QUEUE = ["gemini-2.5-flash-preview-tts"]

if not 'TEXT_GENERATION_MODEL_LIGHT' in locals() and not 'TEXT_GENERATION_MODEL_LIGHT' in globals():
    try:
        from config import TEXT_GENERATION_MODEL_LIGHT
    except ImportError:
        TEXT_GENERATION_MODEL_LIGHT = 'gemini-2.0-flash-lite-preview-02-05'

# Полный список доступных голосов
AVAILABLE_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", 
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba", 
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar", 
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi", 
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"
]

DEFAULT_DISTORTION_INTENSITY = 60 

async def generate_text_response_for_voice(chat_id: str, user_query: str) -> str:
    """
    Генерирует текстовый ответ от имени персонажа.
    ИСПОЛЬЗУЕТ ЛЕГКУЮ МОДЕЛЬ, чтобы не тратить лимиты основной очереди.
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
            # ВМЕСТО model.generate_content (который берет тяжелую модель)
            # Мы используем легкую модель специально для этого таска
            light_model = genai.GenerativeModel(TEXT_GENERATION_MODEL_LIGHT)
            response = light_model.generate_content(full_prompt)
            return response.text
            
        text_response = await asyncio.to_thread(sync_gemini_call)
        
        # Историю сохраняем как обычно
        update_conversation_history(chat_id, "User (Voice)", user_query, role="user")
        update_conversation_history(chat_id, prompt_name, text_response, role="assistant")
        
        return text_response
    except Exception as e:
        logging.error(f"Voice Text Gen Error ({TEXT_GENERATION_MODEL_LIGHT}): {e}")
        # Если легкая упала, можно попробовать через основную очередь как fallback
        try:
             logging.info("Fallback to main model queue for voice text...")
             def sync_fallback_call():
                 return model.generate_content(full_prompt, chat_id=chat_id).text
             return await asyncio.to_thread(sync_fallback_call)
        except Exception as e2:
             logging.error(f"Fallback Voice Text Gen Error: {e2}")
             return "Кхе-кхе... Что-то горло першит, не могу говорить."

async def generate_audio_from_text(text: str, output_path: str) -> bool:
    """
    Использует Gemini TTS для генерации аудио с Retry.
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

        def sync_tts_call_with_retry():
            max_retries = 3
            base_delay = 15 
            
            for model_name in TTS_MODELS_QUEUE:
                tts_model = genai.GenerativeModel(model_name)
                
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
                            delay = base_delay * (attempt + 1) + random.uniform(1, 5)
                            logging.warning(f"⚠️ Quota exceeded for {model_name}. Sleeping for {delay:.1f}s...")
                            time.sleep(delay)
                        else:
                            logging.error(f"❌ Max retries reached for {model_name}.")
                            break 
                            
                    except Exception as e:
                        if "404" in str(e):
                             logging.error(f"❌ Model {model_name} not found (404). Skipping.")
                             break
                        logging.error(f"Error with model {model_name}: {e}")
                        if attempt < max_retries - 1:
                            time.sleep(5)
                        else:
                            break
            return None

        response = await asyncio.to_thread(sync_tts_call_with_retry)
        
        if not response or not response.candidates:
            logging.error("Gemini TTS returned no candidates")
            return False
            
        part = response.candidates[0].content.parts[0]
        if not part.inline_data:
            logging.error("Gemini TTS returned no inline_data")
            return False

        audio_data = part.inline_data.data
        sample_rate = 24000 
        
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1) 
            wav_file.setsampwidth(2) 
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_data)
            
        return True

    except Exception as e:
        logging.error(f"Gemini TTS Final Error: {e}")
        return False

async def handle_voice_command(message: types.Message, bot: Bot):
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
            #caption=f"🗣 Ответ на: {user_query[:20]}...",
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
