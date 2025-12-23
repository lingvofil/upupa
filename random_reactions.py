import re
import os
import random
import logging
import asyncio
import json
from aiogram.types import FSInputFile, Message, ReactionTypeEmoji
from aiogram import Bot

# Используем тот же экстрактор сообщений, что и в других модулях
from lexicon_settings import extract_chat_messages
from config import model # Модель передается как аргумент

# Полный список доступных реакций Telegram
TELEGRAM_REACTIONS = [
    "❤️", "🥰", "😁", "❤️‍🔥", "💔", "🤨", "👀", "🫡"
]

# --- ИЗМЕНЕНО: Случайные эмодзи-реакции (БЕЗ AI) ---
async def set_random_emoji_reaction(message: Message):
    """
    Ставит случайный эмодзи из списка без анализа контекста.
    Быстро, бесплатно, не грузит API.
    """
    try:
        # Выбираем случайный эмодзи
        chosen_emoji = random.choice(TELEGRAM_REACTIONS)
        
        # Ставим реакцию
        await message.react(reactions=[ReactionTypeEmoji(emoji=chosen_emoji)])
        logging.info(f"Бот поставил случайную реакцию: {chosen_emoji}")
        return True

    except Exception as e:
        logging.error(f"Ошибка при проставлении случайной эмодзи-реакции: {e}")
        return False

# --- СТАРЫЙ ФУНКЦИОНАЛ (Остался с AI, так как вызывается редко) ---

async def generate_situational_reaction(chat_id: int, model_instance):
    """
    Генерирует ироничную кинематографичную ремарку на основе истории чата.
    """
    logging.info(f"Запуск генерации ситуативной реакции для чата {chat_id}.")
    
    all_messages = await extract_chat_messages(chat_id)
    
    if not all_messages:
        logging.warning(f"Для чата {chat_id} не найдено сообщений в логе. Реакция отменена.")
        return None

    last_messages = all_messages[-15:]
    chat_history = "\n".join(last_messages)
    
    if not chat_history.strip():
        return None
        
    logging.info(f"Взято последних {len(last_messages)} сообщений для генерации реакции.")

    prompt = f"""
    Проанализируй диалог из чата. Придумай короткую, кинематографичную ремарку или звуковой эффект, который бы дополнил этот эффект. 
    Ремарка должна быть креативной, возможно даже грубоватой, но четко подходить под ситуацию.
    Ответь ТОЛЬКО ОДНОЙ фразой, курсивом, заключенной в звездочки (*).

    Примеры ремарок:
    - *слышен звук сверчков*
    - *закадровый смех дегенератов*
    - *повисла неловкая тишина*
    - *где-то вдалеке наебнулся со стула ребенок*
    - *послышался звук падающей на пол челюсти*
    - *в воздухе запахло тотальным кринжем*

    Вот диалог для анализа:
    ---
    {chat_history}
    ---

    Твоя ремарка (короткая, атмосферная, курсивом):
    """
    
    try:
        def sync_llm_call():
            try:
                response = model_instance.generate_content(
                    prompt,
                    chat_id=chat_id,
                    generation_config={
                        'temperature': 1.0,
                        'max_output_tokens': 60,
                        'top_p': 1.0,
                    }
                )
                if response and response.candidates and response.candidates[0].content.parts:
                    return response.text.strip()
                return ""
            except Exception as e:
                 logging.warning(f"Ошибка внутри sync_llm_call (situational): {e}")
                 return ""

        reaction_text = await asyncio.to_thread(sync_llm_call)
        
        logging.info(f"Ответ от Gemini для ситуативной реакции: '{reaction_text}'")

        if reaction_text and reaction_text.startswith('*') and reaction_text.endswith('*'):
            return reaction_text
        else:
            return None

    except Exception as e:
        logging.error(f"Ошибка при генерации ситуативной реакции: {e}", exc_info=True)
        return None

# Рифма
async def generate_rhyme_reaction(message, model_instance):
    """Генерирует рифмованную реакцию на последнее слово сообщения"""
    tries = 0
    max_tries = 3
    chat_id = message.chat.id
    
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
                
            rhyme_prompt = f"""Найди простую рифму к слову "{last_word}". 
            Ответь только одним словом - рифмой, без объяснений и дополнительного текста.
            Рифма должна быть на русском языке и звучать естественно."""
            
            def sync_rhyme_call():
                try:
                    response = model_instance.generate_content(
                        rhyme_prompt,
                        chat_id=chat_id,
                        generation_config={
                            'temperature': 0.7,
                            'max_output_tokens': 10,
                            'top_p': 0.8,
                        }
                    )
                    if hasattr(response, 'text') and response.text:
                        return response.text.strip()
                    elif hasattr(response, 'candidates') and response.candidates:
                        candidate = response.candidates[0]
                        if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                            return candidate.content.parts[0].text.strip()
                    return None
                        
                except Exception as e:
                    logging.error(f"Gemini API error in sync_rhyme_call: {e}")
                    return None
            
            rhyme_word = await asyncio.to_thread(sync_rhyme_call)
            
            if not rhyme_word:
                tries += 1
                if tries < max_tries:
                    await asyncio.sleep(0.5)
                    continue
                else:
                    return None
                    
            rhyme_words = rhyme_word.split()
            if rhyme_words:
                rhyme_word = rhyme_words[0]
            else:
                tries += 1
                continue
                
            rhyme_word = rhyme_word.strip('.,!?;:()[]{}"\'-')
            
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
    
    logging.warning(f"Не удалось сгенерировать рифму после {max_tries} попыток")
    return None

def is_laughter(text):
    if not text: return False
    text = text.lower().strip('.,!?;:()[]{}"\'-')
    laughter_patterns = ['ха', 'ах', 'хх']
    return any(pattern * 2 in text for pattern in laughter_patterns)

async def send_random_laughter_voice(message):
    try:
        laughter_files = ["smeh_bomzha.ogg", "smeh_pydorskii.ogg", "smeh_nikity.ogg"]
        selected_file = random.choice(laughter_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        if os.path.exists(voice_path):
            await message.reply_voice(FSInputFile(voice_path))
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_random_common_voice_reaction(message):
    try:
        voice_files = ["cho_derzysh.ogg", "poidu_primu_vannu.ogg", "razbei_vitrinu.ogg", "sidi_ne_otsvechivai.ogg", "so_slezami_lutogo_ugara.ogg", "ty_cho_komediyu.ogg"]
        selected_file = random.choice(voice_files)
        voice_path = f"/root/upupa/voice/{selected_file}"
        if os.path.exists(voice_path):
            await message.reply_voice(FSInputFile(voice_path))
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_yaytsa_voice_reaction(message):
    try:
        voice_path = "/root/upupa/voice/yaytsa_prishemili.ogg"
        if os.path.exists(voice_path):
            await message.reply_voice(FSInputFile(voice_path))
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False
        
async def send_para_voice_reaction(message):
    try:
        voice_path = "/root/upupa/voice/muzhik_molodetc.ogg"
        if os.path.exists(voice_path):
            await message.reply_voice(FSInputFile(voice_path))
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

async def send_random_voice_reaction(message: Message):
    """Отправляет случайное голосовое сообщение в ответ на голосовое"""
    try:
        voice_reactions = ["sexy_golos.ogg", "istorii_doebali.ogg", "normik_golos.ogg", "la_golosochek.ogg"]
        selected_file = random.choice(voice_reactions)
        voice_path = f"/root/upupa/voice/{selected_file}"
        if os.path.exists(voice_path):
            await message.reply_voice(FSInputFile(voice_path))
            return True
        else:
            logging.error(f"Файл {voice_path} не найден")
            return False
    except Exception as e:
        logging.error(f"Ошибка при отправке голосового сообщения: {e}")
        return False

# <<<--- СПИСОК ФРАЗ ДЛЯ 1399269377 --->>>
INSULT_WORDS_FOR_1399269377 = [
    "норм", "найс", "горит", "тряска", "матрас", "подматрасный", "ебать", "фултайм", "юрист", "порвало",
    "петух", "карлан", "кривозубый", "гном", "куколд", "сталкер", "лысик", "лисичка", "порвало пердак", 
    "подгорел пердак", "чат шиз", "чат колхозниц", "скинь фотку", "рейт", "лоу айкью",
    "чекай", "ебало к осмотру", "за щеку тебе", "манька",
    "рост 165", "рост 162", "рост 170", "лысый", "кривозубик", "карлик", "кофемания", "шоколадница", "помои",
    "селюк", "ахахах", "быдло", "кринж", "кринжанул", "маньку порвало",
    "чат шиз-колхозниц", "помои в кофемании", "екатеринбург", "додик", "додикс", "чмо",
    "закукарекал", "лохобес", "заерзал", "мразота", "ущерб", "тварь пиздливая", "байт", "байтить", "завилял",
    "А че ребят не нравится то?",
    "блядина с шаблонным мышлением лопнула ахах",
    "почему все время все уточняешь? ты тупой или боишься ошибиться?",
    "я с пациками таких чертей с бородками норм разваливал в нулевых в барах китай города бггг",
    "ебало закрой и предков не трогай хуйло воняющее",
    "Правильно сиди в этом загоне для додиксов",
    "этот еблан тоже эмоции ставит ботам ну сука кретин блять)))",
    "Хули ты проецируешь свои боли на людей ммм",
    "твой отец бомж с вокзала, твоя мать спидозная питерская шалава",
    "с ублюдками как ты по-ублюдски",
    "ну когда там уже по твоему мнению предел твоих лоховских ожиданий",
    "ну так это о тебе и не делай ебало удивленное",
    "че гогочим, сена хочим",
    "Пиздец ты можешь нормально фразы строить уебище таежное?",
    "СНГ долбаеб чек",
    "Ты про предмет разговора пиши долбоеб",
    "Че приполз сюда кста",
    "Ебнутый годами тут сидящий лузер",
    ">не знаешь чем аргументировать лепи стикер и переходи на личности",
    "Откисай ты не в приоритете",
    "ты четко называй, хули ты заерзал с вопросами опять мразота",
    "щас своего другалька уже зовет",
    "ахахахаха тварь пиздливая",
    "я самодостаточен, это такой мрази как ты стая нужна",
    "Нахуй тут время прожигать",
    "дебил обнуленный",
    "хуя панчи из детского сада",
    "Кофе надо дома пить а не по ресторанам шастать",
    "ну ты сидишь тут подбайчиваешь, я то прямо тебе в ебало",
    "Жепой не виляй прямо отвечай",
    "тя сломать что ли дядя",
    "Давай побольше эмоджиков навали чтобы точно было видно"
]

async def generate_insult_for_lis(message, model_instance):
    """
    Генерирует реакцию для пользователя 1399269377.
    """
    chat_id = message.chat.id
    try:
        if random.random() < 0.9: # 90% шанс сгенерировать новую фразу (микс)
            logging.info("Генерация МИКСА фразы для 1399269377...")
            
            prompt = (
                "Ты — микшер фраз. Твоя задача — взять 2-3 фразы из списка ниже и смешать их, чтобы получилась новая, но в том же стиле. "
                "ВАЖНО: Используй ТОЛЬКО слова и короткие обороты из предложенных примеров. Не добавляй НИЧЕГО от себя. "
                "Твой ответ — только результат микса (5-15 слов), без пояснений.\n\n"
                "Примеры для микширования:\n" + "\n".join(INSULT_WORDS_FOR_1399269377) +
                "\n\nТвой микс (ТОЛЬКО из слов выше):"
            )

            def call_llm():
                try:
                    response = model_instance.generate_content(
                        prompt,
                        chat_id=chat_id,
                        generation_config={'temperature': 0.6, 'max_output_tokens': 60, 'top_p': 1.0}
                    )
                    if response and response.candidates and response.candidates[0].content.parts:
                        return response.text.strip()
                    return ""
                except Exception as e:
                    logging.error(f"Ошибка генерации реакции для 1399269377 (LLM call): {e}")
                    return None
            
            new_phrase = await asyncio.to_thread(call_llm)
            
            if new_phrase:
                await message.reply(new_phrase)
                return True
            else:
                logging.warning("Не удалось сгенерировать МИКС для 1399269377, используется случайная из списка (фолбэк).")
                selected_phrase = random.choice(INSULT_WORDS_FOR_1399269377)
                await message.reply(selected_phrase)
                return True
        
        else: # 10% шанс использовать фразу из списка
            logging.info("Использование случайной фразы из списка для 1399269377...")
            selected_phrase = random.choice(INSULT_WORDS_FOR_1399269377)
            await message.reply(selected_phrase)
            return True

    except Exception as e:
        logging.error(f"Критическая ошибка при отправке реакции для 1399269377: {e}")
        return False

# Список фраз для пользователя 113086922
PHRASES_FOR_113086922 = [
    "Твой, сука, юношеский максимализм плюс гормональная нестабильность заставляют тебя думать, что если ты будешь приходить и говном всех поливать, то тебе полегчает\nА если тебе не отвечать, так ты ещё больше говном плюёшься\nТак пиздуй-ка ты проспись лучше",
    "хуёв сто",
    "спи иди нахуй",
    "Слыш ты, гари, блядь\nНе доводи до предела, Залиночка\nНе стоит оно того",
    "Ты тупая там штоле совсем? Иди-ка перечитай свои охуительные сообщения\nТебя никто, убогую, не трогает\nА ты сходу тут говна полный рот наберёшь и плюёшься\nНи здрасте, ни пошли нахуй\nКакого хуя тебя так разрывает-то",
    "С хуёнией, Женя\nШтоб первый день её знать, так ладно бы\nА то, блядь, каждый раз с говном в ладошках",
    "Всё, сука, растёт, меняется\nА эта как была с мозгами из пипетки, так, блядь, и осталась",
    "Тупая, в первую очередь",
    "По пизде мешалкой себе постучи, ишь",
    "ИДИ БЛЯДЬ НА УЛИЦЕ ПОГУЛЯЙ НАХУЙ",
    "А поплачешь, так меньше ссать будешь",
    "пукиня, поцелуй меня",
    "пуколка, давай няшиться под пледиком",
    "пукиня, почему ты меня так ненавидишь",
    "мелкая, ты почему такая стерва",
    "залина, я спать пошел"
]

async def generate_reaction_for_113086922(message: Message, model_instance):
    """
    Генерирует реакцию для пользователя 113086922.
    """
    chat_id = message.chat.id
    try:
        if random.random() < 0.9: # 90% шанс сгенерировать новую фразу (микс)
            logging.info("Генерация МИКСА фразы для 113086922...")
            
            prompt = (
                "Ты — микшер фраз. Твоя задача — взять 2-3 фразы из списка ниже и смешать их, чтобы получилась новая, но в том же стиле. "
                "ВАЖНО: Используй ТОЛЬКО слова и короткие обороты из предложенных примеров. Не добавляй НИЧЕГО от себя. "
                "Твой ответ — только результат микса (5-15 слов), без пояснений.\n\n"
                "Примеры для микширования:\n" + "\n".join(PHRASES_FOR_113086922) +
                "\n\nТвой микс (ТОЛЬКО из слов выше):"
            )

            def call_llm():
                try:
                    response = model_instance.generate_content(
                        prompt,
                        chat_id=chat_id,
                        generation_config={'temperature': 0.6, 'max_output_tokens': 60, 'top_p': 1.0}
                    )
                    if response and response.candidates and response.candidates[0].content.parts:
                        return response.text.strip()
                    return ""
                except Exception as e:
                    logging.error(f"Ошибка генерации реакции для 113086922 (LLM call): {e}")
                    return None
            
            new_phrase = await asyncio.to_thread(call_llm)
            
            if new_phrase:
                await message.reply(new_phrase)
                return True
            else:
                logging.warning("Не удалось сгенерировать МИКС для 113086922, используется случайная из списка (фолбэк).")
                selected_phrase = random.choice(PHRASES_FOR_113086922)
                await message.reply(selected_phrase)
                return True
        
        else: # 10% шанс использовать старую фразу
            logging.info("Использование случайной фразы из списка для 113086922...")
            selected_phrase = random.choice(PHRASES_FOR_113086922)
            await message.reply(selected_phrase)
            return True

    except Exception as e:
        logging.error(f"Критическая ошибка при отправке реакции для 113086922: {e}")
        return False

async def generate_regular_reaction(message):
    try:
        if not message.text: return None
        words = message.text.split()
        valid_words = [word for word in words if len(word) > 2]        
        if not valid_words: return None
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

async def process_random_reactions(
    message: Message,
    model,
    save_user_message,
    track_message_statistics,
    add_chat,
    chat_settings,
    save_chat_settings,
):

    # --- 0. Защита от реакции на сообщения бота ---
    if not message.from_user or message.from_user.is_bot:
        return False

    # --- 1. Базовые операции учета ---
    await save_user_message(message)
    await track_message_statistics(message)
    add_chat(message.chat.id, message.chat.title, message.chat.username)

    chat_id = str(message.chat.id)

    # --- 2. Инициализация настроек чата ---
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {
            "dialog_enabled": True,
            "reactions_enabled": True,
            "emoji_enabled": True,
        }
        save_chat_settings()

    chat_cfg = chat_settings.get(chat_id, {})

    # ------------------------------------------------------------------
    # 3. EMOJI-РЕАКЦИИ (Random, без AI)
    # ------------------------------------------------------------------
    if chat_cfg.get("emoji_enabled", True):
        # Шанс можно немного поднять, раз это бесплатно (например, 7-10%)
        # Оставил 5% как было
        if random.random() < 0.5:
            try:
                await set_random_emoji_reaction(message)
            except Exception as e:
                logging.error(f"Emoji reaction failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # 4. Если реакции полностью отключены — выходим
    # ------------------------------------------------------------------
    if not chat_cfg.get("reactions_enabled", True):
        return False

    # ------------------------------------------------------------------
    # 5. Ситуативная текстовая реакция (в чат, не reply)
    # ------------------------------------------------------------------
    if random.random() < 0.01:
        situational = await generate_situational_reaction(message.chat.id, model)
        if situational:
            await message.bot.send_message(
                message.chat.id,
                situational,
                parse_mode="Markdown",
            )
            return True

    # ------------------------------------------------------------------
    # 6. Персональные реакции
    # ------------------------------------------------------------------
    if message.from_user.id == 1399269377 and message.text and random.random() < 0.3:
        if await generate_insult_for_lis(message, model):
            return True

    if message.from_user.id == 113086922 and random.random() < 0.005:
        if await generate_reaction_for_113086922(message, model):
            return True

    # ------------------------------------------------------------------
    # 7. Голосовые реакции
    # ------------------------------------------------------------------
    if message.voice and random.random() < 0.001:
        if await send_random_voice_reaction(message):
            return True

    if random.random() < 0.0001:
        if await send_random_common_voice_reaction(message):
            return True

    if message.text and "пара дня" in message.text.lower() and random.random() < 0.05:
        if await send_para_voice_reaction(message):
            return True

    # ------------------------------------------------------------------
    # 8. Рифма
    # ------------------------------------------------------------------
    if message.text and random.random() < 0.008:
        rhyme = await generate_rhyme_reaction(message, model)
        if rhyme:
            await message.reply(rhyme)
            return True

    # ------------------------------------------------------------------
    # 9. Обычная текстовая реакция
    # ------------------------------------------------------------------
    if message.text and random.random() < 0.008:
        regular = await generate_regular_reaction(message)
        if regular:
            await message.reply(regular)
            return True

    # ------------------------------------------------------------------
    # 10. Диалог выключен
    # ------------------------------------------------------------------
    if not chat_cfg.get("dialog_enabled", True):
        return False

    return False
