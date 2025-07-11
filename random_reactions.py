import re
import os
import random
import logging
import asyncio
from aiogram.types import FSInputFile
from common_settings import *
from chat_settings import *

# Рифма
async def generate_rhyme_reaction(message, model):
    """Генерирует рифмованную реакцию на последнее слово сообщения"""
    tries = 0
    max_tries = 3
    
    while tries < max_tries:
        try:
            if not message or not message.text:
                return None
                
            words = message.text.split()
            if not words:
                return None
                
            last_word = words[-1].strip('.,!?;:()[]{}"\'-')
            if len(last_word) <= 2:
                return None
                
            # Создаем промпт для генерации рифмы
            rhyme_prompt = f"""Найди простую рифму к слову "{last_word}". 
            Ответь только одним словом - рифмой, без объяснений и дополнительного текста.
            Рифма должна быть на русском языке и звучать естественно."""
            
            def sync_rhyme_call():
                try:
                    # Специфично для Google Gemini API
                    response = model.generate_content(
                        rhyme_prompt,
                        generation_config={
                            'temperature': 0.7,
                            'max_output_tokens': 10,  # Ограничиваем до одного слова
                            'top_p': 0.8,
                        }
                    )
                    
                    # Для Gemini API правильная структура ответа
                    if hasattr(response, 'text') and response.text:
                        return response.text.strip()
                    elif hasattr(response, 'candidates') and response.candidates:
                        # Альтернативный способ получения текста
                        candidate = response.candidates[0]
                        if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                            return candidate.content.parts[0].text.strip()
                    
                    # Если ничего не получилось
                    logging.warning(f"Gemini API returned empty response for rhyme generation")
                    return None
                        
                except Exception as e:
                    # Логируем конкретные ошибки API
                    if "quota" in str(e).lower():
                        logging.error(f"Gemini API quota exceeded: {e}")
                    elif "timeout" in str(e).lower():
                        logging.error(f"Gemini API timeout: {e}")
                    elif "api_key" in str(e).lower():
                        logging.error(f"Gemini API key error: {e}")
                    else:
                        logging.error(f"Gemini API error in sync_rhyme_call: {e}")
                    return None
            
            rhyme_word = await asyncio.to_thread(sync_rhyme_call)
            
            if not rhyme_word:
                tries += 1
                if tries < max_tries:
                    # Небольшая задержка перед повторной попыткой
                    await asyncio.sleep(0.5)
                    continue
                else:
                    return None
                    
            # Очищаем ответ от лишних символов и берем только первое слово
            rhyme_words = rhyme_word.split()
            if rhyme_words:
                rhyme_word = rhyme_words[0]
            else:
                tries += 1
                continue
                
            rhyme_word = rhyme_word.strip('.,!?;:()[]{}"\'-')
            
            # Проверяем, что получили адекватную рифму
            if len(rhyme_word) > 0 and rhyme_word != last_word and rhyme_word.isalpha():
                return f"пидора {rhyme_word}".lower()
            else:
                tries += 1
                continue
                
        except Exception as e:
            logging.error(f"Ошибка при генерации рифмы (попытка {tries + 1}): {e}")
            tries += 1
            if tries < max_tries:
                await asyncio.sleep(1)
    
    # Если все попытки неудачны, возвращаем None
    logging.warning(f"Не удалось сгенерировать рифму после {max_tries} попыток")
    return None

def is_laughter(text):
    """Проверяет, является ли текст выражением смеха"""
    if not text:
        return False
    
    # Приводим к нижнему регистру и убираем знаки препинания
    text = text.lower().strip('.,!?;:()[]{}"\'-')
    
    # Паттерны смеха
    laughter_patterns = ['ха', 'ах', 'хх']
    
    # Проверяем, содержит ли текст повторяющиеся паттерны смеха
    return any(pattern * 2 in text for pattern in laughter_patterns)

async def send_random_laughter_voice(message):
    """Отправляет случайное голосовое сообщение смеха"""
    try:
        # Список файлов со смехом
        laughter_files = [
            "smeh_bomzha.ogg",
            "smeh_pydorskii.ogg",
            "smeh_nikity.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(laughter_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

def count_letter_a(text):
    """Подсчитывает количество букв 'а' в тексте (включая заглавные)"""
    if not text:
        return 0
    return text.lower().count('а')

async def send_random_common_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из общего набора реакций"""
    try:
        # Список всех возможных голосовых сообщений для случайной реакции
        voice_files = [
            "cho_derzysh.ogg",
            "poidu_primu_vannu.ogg",
            "razbei_vitrinu.ogg",
            "sidi_ne_otsvechivai.ogg",
            "so_slezami_lutogo_ugara.ogg",
            "ty_cho_komediyu.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_yaytsa_voice_reaction(message):
    """Отправляет голосовое сообщение yaytsa_prishemili.ogg"""
    try:
        voice_path = "/root/upupa/voice/yaytsa_prishemili.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_para_voice_reaction(message):
    """Отправляет голосовое сообщение muzhik_molodetc.ogg"""
    try:
        voice_path = "/root/upupa/voice/muzhik_molodetc.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_vstretimsya_voice_reaction(message):
    """Отправляет голосовое сообщение usloviya_po_vstreche.ogg"""
    try:
        voice_path = "/root/upupa/voice/usloviya_po_vstreche.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_sobesedovan_voice_reaction(message):
    """Отправляет голосовое сообщение sobesedovanie.ogg"""
    try:
        voice_path = "/root/upupa/voice/sobesedovanie.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение в ответ на голосовое"""
    try:
        # Список возможных голосовых реакций
        voice_reactions = [
            "sexy_golos.ogg",
            "istorii_doebali.ogg",
            "normik_golos.ogg",
            "la_golosochek.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_reactions)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_narisuy_voice_reaction(message):
    """Отправляет голосовое сообщение narisuy.ogg"""
    try:
        voice_path = "/root/upupa/voice/narisuy.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

def contains_imax_mention(text):
    """Проверяет, содержит ли текст упоминание IMAX или пользователя"""
    if not text:
        return False
    text = text.lower()
    return any(keyword in text for keyword in ['имакс', 'imax', '@trtrtrtrtrtrtr'])

async def send_tupovatyi_voice_reaction(message):
    """Отправляет голосовое сообщение on_tupovatyi.ogg"""
    try:
        voice_path = "/root/upupa/voice/on_tupovatyi.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_krinzhanul_voice_reaction(message):
    """Отправляет голосовое сообщение krinzhanul_spesenki.ogg"""
    try:
        voice_path = "/root/upupa/voice/krinzhanul_spesenki.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_ola_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Ola"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "ola_boobs.ogg",
            "ola_pasta_shlamidii.ogg",
            "olga_ty_ne_mogla_by.ogg",
            "vzad_vernula.ogg",
            "dudidadiduda.ogg",
            "dudidadiduda2.ogg",
            "dudidadiduda3.ogg",
            "izuchat_organizm_georgesa.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

def is_ya_message(text):
    """Проверяет, является ли сообщение словом 'я' или 'я?'"""
    if not text:
        return False
    text = text.lower().strip()
    return text in ['я', 'я?']

async def send_ya_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора реакций на 'я'"""
    try:
        # Список возможных голосовых реакций
        voice_files = [
            "huynya.ogg",
            "vnavoze.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_muhtar_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Мухтар"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "muhtar_durachok.ogg",
            "muhtar_huesos_siniy.ogg",
            "ryzhaya_pizdenka.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_nikita_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Никита"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "vedro_obsuzhdat.ogg",
            "ebanulsya.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_elena_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Елена"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "elenu_bombit.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_detector_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Детектор"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "detector_yobany_vrot.ogg",
            "detector_prosypaysya.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_anna_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Anna"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "prosto_pms.ogg",
            "zalas_kupatsya.ogg",
            "zay_tykni_umor.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_imax_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Имакс"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "chel_ty_buhoy.ogg",
            "rozhay_huesos.ogg",
            "slysh_kozel.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_matsuk_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Мацюк"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "suka_shiz.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_neo_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Neo"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "o_vi_shto_gey.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_serhio_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Serhio"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "serhio_nauchi.ogg",
            "serhio_ty_shutish.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_lis_random_voice_reaction(message):
    """Отправляет случайное голосовое сообщение из набора для пользователя Lis"""
    try:
        # Список всех возможных голосовых сообщений для данного пользователя
        voice_files = [
            "davay_ne_dodumyvai.ogg"
        ]
        
        # Выбираем случайный файл
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_sticker_reaction_voice(message):
    """Отправляет голосовое сообщение slysh_eblo_stickerpack.ogg"""
    try:
        voice_path = "/root/upupa/voice/slysh_eblo_stickerpack.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_odintsovo_voice_reaction(message):
    """Отправляет голосовое сообщение v_odintsovo.ogg"""
    try:
        voice_path = "/root/upupa/voice/v_odintsovo.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_dedy_voice_reaction(message):
    """Отправляет голосовое сообщение dedy_vrot.ogg"""
    try:
        voice_path = "/root/upupa/voice/dedy_vrot.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_vuz_voice_reaction(message):
    """Отправляет голосовое сообщение vuz_colledge.ogg"""
    try:
        voice_path = "/root/upupa/voice/vuz_colledge.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_skin_voice_reaction(message):
    """Отправляет голосовое сообщение skinul_za_sheku.ogg"""
    try:
        voice_path = "/root/upupa/voice/skinul_za_sheku.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_vmeste_voice_reaction(message):
    """Отправляет голосовое сообщение hui_pososite.ogg"""
    try:
        voice_path = "/root/upupa/voice/hui_pososite.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_nerabotaet_voice_reaction(message):
    """Отправляет голосовое сообщение mozg_ne_rabotaet.ogg"""
    try:
        voice_path = "/root/upupa/voice/mozg_ne_rabotaet.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
    
async def send_davay_voice_reaction(message):
    """Отправляет голосовое сообщение psina.ogg"""
    try:
        voice_path = "/root/upupa/voice/psina.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_chego_voice_reaction(message):
    """Отправляет голосовое сообщение ebat_ne_dolzhno.ogg"""
    try:
        voice_path = "/root/upupa/voice/ebat_ne_dolzhno.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

def is_single_question_word(text):
    """Проверяет, является ли сообщение одиночным вопросительным словом"""
    if not text:
        return False
    
    # Список вопросительных слов
    question_words = ['чего', 'что', 'как', 'каво', 'кого', 'почему', 'што', 'где', 'кто', 'когда', 'куда', 'мнение']
    
    # Приводим к нижнему регистру
    text = text.lower()
    
    # Проверяем наличие вопросительного знака
    has_question_mark = '?' in text
    
    # Убираем знаки препинания для проверки слова
    clean_text = text.strip('.,!?;:()[]{}"\'-')
    
    # Проверяем, состоит ли сообщение только из одного вопросительного слова
    # или является ли оно вопросительным словом со знаком вопроса
    return clean_text in question_words or (has_question_mark and clean_text in question_words)

async def send_ebat_voice_reaction(message):
    """Отправляет голосовое сообщение ebat_ne_dolzhno2.ogg"""
    try:
        voice_path = "/root/upupa/voice/ebat_ne_dolzhno2.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

def contains_pledik_words(text):
    """Проверяет, содержит ли текст слова братан/педик/пледик"""
    if not text:
        return False
    text = text.lower()
    return any(word in text for word in ['братан', 'педик', 'пледик'])

async def send_pledik_voice_reaction(message):
    """Отправляет голосовое сообщение pledik.ogg"""
    try:
        voice_path = "/root/upupa/voice/pledik.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_sebi_voice_reaction(message):
    """Отправляет голосовое сообщение sebi.ogg"""
    try:
        voice_path = "/root/upupa/voice/sebi.ogg"
        if os.path.exists(voice_path):
            voice_file = FSInputFile(voice_path)
            await message.reply_voice(voice_file)
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def generate_insult_for_lis(message, model):
    """
    Генерирует оскорбительную текстовую реакцию для пользователя 1399269377
    на основе заданного набора фраз и слов.
    """
    insult_words = [
        "норм", "найс", "горит", "тряска", "матрас", "одинцовский", "стекломой", "одинцово",
        "удод", "удод с горящим хохолком", "ебать", "фултайм", "юрист", "порвало",
        "два петуха", "порвало пердаки", "подгорели пердаки", "шакалы", "удодик", "мухтар", "никита",
        "лада гранта", "корыто", "ведро", "ржавое корыто", "ржавая гранта", "омск", "омский",
        "солевой", "на любителя", "чат шиз", "чат колхозниц", "скинь фотку", "рейт",
        "чекай", "ебало к осмотру", "за щеку тебе", "пизда над губой", "рыжая манька", "манька",
        "рост 185", "рост 182", "рост 180", "лысый", "кривозубый", "карлик", "кофемания", "шоколадница", "помои",
        "селюк", "ахахах", "быдло", "нью бэлэнс", "диор", "кринж", "кринжанул", "маньку порвало",
        "чат шиз-колхозниц", "помои в кофемании", "екатеринбург", "додик", "додикс", "чмо",
        "закукарекал", "лохобес", "заерзал", "мразота", "тварь пиздливая", "алконафт", "байт", "байтить", "завилял",
        # Полные фразы
        "почему все время все уточняешь? ты тупой или боишься ошибиться?",
        "Там нет авторитетов просто общение шиз",
        "Синяя истеричка на связи",
        "я с пациками таких чертей с бородками норм разваливал в нулевых в барах китай города бггг",
        "ебало закрой и предков не трогай хуйло воняющее",
        "Правильно сиди в этом загоне для додиксов",
        "Вся твоя жизнь это ржавая гранта с текущим маслом",
        "этот еблан тоже эмоции ставит ботам ну сука кретин блять)))",
        "Хули ты проецируешь свои боли на людей ммм",
        "Слюдянское чмо закукарекало",
        "твой отец бомж с вокзала, твоя мать спидозная питерская шалава",
        "ну когда там уже по твоему мнению предел твоих лоховских ожиданий",
        "лохобесы всегда кучкуются против норм пацанов)",
        "ну так это о тебе и не делай ебало удивленное",
        "че гогочим, сена хочим",
        "Че приполз сюда кста",
        ">не знаешь чем аргументировать лепи стикер и переходи на личности",
        "Откисай ты не в приоритете",
        "Птица хоть и бухает но поумнее",
        "ты четко называй, хули ты заерзал с вопросами опять мразота",
        "щас своего другалька уже зовет",
        "ахахахаха тварь пиздливая",
        "я самодостаточен, это такой мрази как ты стая нужна",
        "ты просто алкаш, не способный в жизнерадостность и оптимизм",
        "Не думаю что вы тут ресурсные супермены которые успевают в мультитаск",
        "Нахуй тут время прожигать",
        "дебил обнуленный",
        "хуя панчи из детского сада",
        "Кофе надо дома пить а не по ресторанам шастать",
        "ну ты сидишь тут подбайчиваешь, я то прямо тебе в ебало",
        "у меня процессуальное бездействие",
        "Жепой не виляй прямо отвечай",
    ]

    prompt = (
        "Составь грубую, колкую, матерную фразу в ответ, используя или склоняя, иногда вставляй закрывающую скобку, "
        "сочетая любые из этих слов и фраз, меняя их падежи и формы, добавляя ругательства, "
        "чтобы звучало как реальное оскорбление в чате, писать нужно коротко и емко, 5-10 слов. "
        "Список слов и фраз:\n\n" + "\n".join(insult_words) +
        "\n\nОтветь только текстом оскорбления без пояснений."
    )

    def call_llm():
        try:
            response = model.generate_content(
                prompt,
                generation_config={
                    'temperature': 1.0,
                    'max_output_tokens': 50,
                    'top_p': 0.9
                }
            )
            return getattr(response, 'text', '').strip()
        except Exception as e:
            logging.error(f"Ошибка генерации оскорбления: {e}")
            return None

    insult_text = await asyncio.to_thread(call_llm)
    if insult_text:
        await message.reply(insult_text)
        return True

    return False


async def generate_regular_reaction(message):
    """Генерирует обычную реакцию 'у тебя в штанах'"""
    try:
        if not message.text:
            return None
            
        words = message.text.split()
        valid_words = [word for word in words if len(word) > 2]        
        if not valid_words:
            return None
            
        random_word = random.choice(valid_words)            
        if len(valid_words) > 1 and random.random() < 0.008:
            word_index = words.index(random_word)
            if word_index < len(words) - 1 and len(words[word_index + 1]) > 2:
                random_word = f"{random_word} {words[word_index + 1]}"
            elif word_index > 0 and len(words[word_index - 1]) > 2:
                random_word = f"{words[word_index - 1]} {random_word}"
                
        return f"{random_word} у тебя в штанах"
        
    except Exception as e:
        logging.error(f"Ошибка при генерации обычной реакции: {e}")
        return None

async def process_random_reactions(message, model, save_user_message, track_message_statistics, add_chat, chat_settings, save_chat_settings):
    """Основная функция обработки случайных реакций"""
    await save_user_message(message)
    await track_message_statistics(message)
    add_chat(message.chat.id, message.chat.title, message.chat.username)    
    
    chat_id = str(message.chat.id)
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {"dialog_enabled": True, "prompt": None}
        save_chat_settings()

    # Случайная реакция с вероятностью 0.001
    if random.random() < 0.001:
        success = await send_random_common_voice_reaction(message)
        if success:
            return True

    # Проверяем реакцию на сообщения от олги
    if message.from_user.id == 183058014 and random.random() < 0.008:
        success = await send_ola_random_voice_reaction(message)
        if success:
            return True
        
    # Проверяем реакцию на сообщения от Мухтара
    if message.from_user.id == 298213704 and random.random() < 0.003:
        success = await send_muhtar_random_voice_reaction(message)
        if success:
            return True
        
    # Проверяем реакцию на сообщения от Никиты
    if message.from_user.id == 278971274 and random.random() < 0.003:
        success = await send_nikita_random_voice_reaction(message)
        if success:
            return True
        
    # Проверяем реакцию на сообщения от Елены
    if message.from_user.id == 315162029 and random.random() < 0.008:
        success = await send_elena_random_voice_reaction(message)
        if success:
            return True
        
    # Проверяем реакцию на сообщения от Детектора
    if message.from_user.id == 126386976 and random.random() < 0.001:
        success = await send_detector_random_voice_reaction(message)
        if success:
            return True
        
    # Проверяем реакцию на сообщения от Анны
    if message.from_user.id == 324820202 and random.random() < 0.003:
        success = await send_anna_random_voice_reaction(message)
        if success:
            return True
        
    # Проверяем реакцию на сообщения от Имакса
    if message.from_user.id == 196920885 and random.random() < 0.008:
        success = await send_imax_random_voice_reaction(message)
        if success:
            return True

    # Проверяем реакцию на сообщения от Мацюка
    if message.from_user.id == 5121280692 and random.random() < 0.008:
        success = await send_matsuk_random_voice_reaction(message)
        if success:
            return True

    # Проверяем реакцию на сообщения от Neo
    if message.from_user.id == 486375703 and random.random() < 0.008:
        success = await send_neo_random_voice_reaction(message)
        if success:
            return True
        
    # Проверяем реакцию на сообщения от Serhio
    if message.from_user.id == 356940644 and random.random() < 0.008:
        success = await send_serhio_random_voice_reaction(message)
        if success:
            return True

    # Проверяем реакцию на сообщения от Lis
    if message.from_user.id == 1399269377 and random.random() < 0.008:
        success = await send_lis_random_voice_reaction(message)
        if success:
            return True
            
    if message.from_user.id == 1399269377 and random.random() < 0.1 and message.text:
        success = await generate_insult_for_lis(message, model)
        if success:
            return True   

    # Проверяем упоминание IMAX с вероятностью 0.008
    if message.text and contains_imax_mention(message.text) and random.random() < 0.008:
        success = await send_tupovatyi_voice_reaction(message)
        if success:
            return True

    # Проверяем реакцию на стикеры
    if message.sticker and random.random() < 0.01:
        success = await send_sticker_reaction_voice(message)
        if success:
            return True
        
    # Проверяем реакцию на "нарисуй" с вероятностью 0.01
    if message.text and "нарисуй" in message.text.lower() and random.random() < 0.01:
        success = await send_narisuy_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку

    # Проверяем реакцию на "скинь" с вероятностью 0.1
    if message.text and "скинь" in message.text.lower() and random.random() < 0.1:
        success = await send_skin_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку

    # Проверяем реакцию на "все вместе" с вероятностью 0.4
    if message.text and "все вместе" in message.text.lower() and random.random() < 0.4:
        success = await send_vmeste_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку

    # Проверяем реакцию на "не работает" с вероятностью 0.1
    if message.text and "не работает" in message.text.lower() and random.random() < 0.1:
        success = await send_nerabotaet_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку

    # Проверяем реакцию на "москв" с вероятностью 0.01
    if message.text and "москв" in message.text.lower() and random.random() < 0.02:
        success = await send_odintsovo_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку
        
    # Проверяем реакцию на "деды" с вероятностью 0.5
    if message.text and "деды" in message.text.lower() and random.random() < 0.5:
        success = await send_dedy_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку

    # Проверяем реакцию на "вуз" с вероятностью 0.3
    if message.text and "вуз" in message.text.lower() and random.random() < 0.5:
        success = await send_vuz_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку

    # Проверяем реакцию на "давай" с вероятностью 0.01
    if message.text and "давай" in message.text.lower() and random.random() < 0.01:
        success = await send_davay_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку
        
    # Проверяем реакцию на "чего" с вероятностью 0.01
    if message.text and "чего" in message.text.lower() and random.random() < 0.01:
        success = await send_chego_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку
        
    # Проверяем реакцию на "пара дня" с вероятностью 0.01
    if message.text and "пара дня" in message.text.lower() and random.random() < 0.05:
        success = await send_para_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку
        
    # Проверяем реакцию на "встретимся" с вероятностью 0.01
    if message.text and "встретимся" in message.text.lower() and random.random() < 0.1:
        success = await send_vstretimsya_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку
        
    # Проверяем реакцию на "собеседован" с вероятностью 0.01
    if message.text and "собеседован" in message.text.lower() and random.random() < 0.1:
        success = await send_sobesedovan_voice_reaction(message)
        if success:
            return True  # Реакция отправлена, прекращаем дальнейшую обработку

    # Проверяем вопросительное слово с вероятностью 0.001
    if message.text and is_single_question_word(message.text) and random.random() < 0.001:
        success = await send_ebat_voice_reaction(message)
        if success:
            return True

    # Проверяем слова братан/педик/пледик с вероятностью 0.01
    if message.text and contains_pledik_words(message.text) and random.random() < 0.1:
        success = await send_pledik_voice_reaction(message)
        if success:
            return True

    # Проверяем сообщение "я" с вероятностью 0.01
    if message.text and is_ya_message(message.text) and random.random() < 0.01:
        success = await send_ya_voice_reaction(message)
        if success:
            return True

    # Проверяем рифмованную реакцию с вероятностью 0.01
    if random.random() < 0.008 and message.text:
        rhyme_reaction = await generate_rhyme_reaction(message, model)
        if rhyme_reaction:
            await message.reply(rhyme_reaction)
            return True  # Реакция отправлена

    # Проверяем реакцию на голосовое сообщение с вероятностью 0.01
    if message.voice and random.random() < 0.01:
        success = await send_random_voice_reaction(message)
        if success:
            return True

    # Проверяем реакцию на аудиофайл с вероятностью 0.01
    if message.audio and random.random() < 0.01:
        success = await send_krinzhanul_voice_reaction(message)
        if success:
            return True

    def has_consecutive_a(text: str) -> bool:
        """Проверяет наличие 3 и более букв 'а' подряд в тексте"""
        # Учитываем как русские, так и английские 'a'
        text = text.lower()
        return bool(re.search(r'[aа]{3,}', text))
    
    # В основной функции:
    if message.text and has_consecutive_a(message.text) and random.random() < 0.1:
        success = await send_yaytsa_voice_reaction(message)
        if success:
            return True

    # Проверяем реакцию на смех с вероятностью 0.01
    if message.text and is_laughter(message.text) and random.random() < 0.01:
        success = await send_random_laughter_voice(message)
        if success:
            return True

    # Обычная реакция "у тебя в штанах" с вероятностью 0.02
    if random.random() < 0.008 and message.text:
        regular_reaction = await generate_regular_reaction(message)
        if regular_reaction:
            await message.reply(regular_reaction)
            return True  # Реакция отправлена
        
    # Проверяем новых участников
    if message.new_chat_members and random.random() < 0.1:
        success = await send_sebi_voice_reaction(message)
        if success:
            return True

    # Проверяем включен ли диалог
    if not chat_settings[chat_id]["dialog_enabled"]:
        return False  # Диалог выключен, дальнейшая обработка не нужна
        
    return False  # Никакая реакция не сработала, можно продолжать обработку