import re
import os
import random
import logging
import asyncio
import json
from aiogram.types import FSInputFile, Message
from aiogram import Bot

# Используем тот же экстрактор сообщений, что и в других модулях
from lexicon_settings import extract_chat_messages
from config import model # Модель передается как аргумент, импорт не нужен

# Новая, исправленная функция для генерации ситуативных реакций
async def generate_situational_reaction(chat_id: int, model_instance):
    """
    Генерирует ироничную кинематографичную ремарку на основе истории чата.
    Использует `extract_chat_messages` для надежности.
    """
    logging.info(f"Запуск генерации ситуативной реакции для чата {chat_id}.")
    
    # 1. Получаем все сообщения из лога для данного чата
    all_messages = await extract_chat_messages(chat_id)
    
    if not all_messages:
        logging.warning(f"Для чата {chat_id} не найдено сообщений в логе. Реакция отменена.")
        return None

    # 2. Берем последние 15 сообщений для анализа
    last_messages = all_messages[-15:]
    chat_history = "\n".join(last_messages)
    
    if not chat_history.strip():
        logging.warning("История чата пуста после обработки. Реакция отменена.")
        return None
        
    logging.info(f"Взято последних {len(last_messages)} сообщений для генерации реакции.")

    # 3. Формируем промпт (с добавлением обсценной лексики)
    prompt = f"""
    Проанализируй этот ебаный диалог из чата. Придумай короткую, язвительную и циничную кинематографичную ремарку или звуковой эффект, который бы дополнил этот пиздец. 
    Используй обсценную лексику (мат), будь грубым и саркастичным. Ремарка должна быть смешной и немного абсурдной.
    Ответь ТОЛЬКО ОДНОЙ фразой, курсивом, заключенной в звездочки (*).

    Примеры ремарок:
    - *слышен звук сверчков и чей-то пердеж*
    - *закадровый смех дегенератов*
    - *повисла неловкая, сука, тишина*
    - *где-то вдалеке наебнулся со стула ребенок*
    - *послышался звук падающего на пол ебала*
    - *в воздухе запахло тотальным кринжем*

    Вот диалог для анализа:
    ---
    {chat_history}
    ---

    Твоя ремарка (короткая, грубая, матерная, курсивом):
    """
    
    logging.info(f"Промпт для ситуативной реакции готов. Длина: {len(prompt)}")

    # 4. Отправляем запрос к модели
    try:
        def sync_llm_call():
            response = model_instance.generate_content(
                prompt,
                generation_config={
                    'temperature': 1.0,
                    'max_output_tokens': 60,
                    'top_p': 1.0,
                }
            )
            return getattr(response, 'text', '').strip()

        reaction_text = await asyncio.to_thread(sync_llm_call)
        
        logging.info(f"Ответ от Gemini для ситуативной реакции: '{reaction_text}'")

        # 5. Проверяем и возвращаем результат
        if reaction_text and reaction_text.startswith('*') and reaction_text.endswith('*'):
            return reaction_text
        else:
            logging.warning(f"Ситуативная реакция от модели не соответствует формату: {reaction_text}")
            return None

    except Exception as e:
        # Логируем ошибку с полной трассировкой
        logging.error(f"Ошибка при генерации ситуативной реакции: {e}", exc_info=True)
        return None

# --- Остальные функции остаются без изменений ---

# Рифма
async def generate_rhyme_reaction(message, model_instance):
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
                
            rhyme_prompt = f"""Найди простую рифму к слову "{last_word}". 
            Ответь только одним словом - рифмой, без объяснений и дополнительного текста.
            Рифма должна быть на русском языке и звучать естественно."""
            
            def sync_rhyme_call():
                try:
                    response = model_instance.generate_content(
                        rhyme_prompt,
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
                    logging.warning(f"Gemini API returned empty response for rhyme generation")
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

# ... (остальные функции send_..._voice_reaction без изменений) ...

async def generate_insult_for_lis(message, model_instance):
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
            response = model_instance.generate_content(
                prompt,
                generation_config={'temperature': 1.0, 'max_output_tokens': 50, 'top_p': 0.9}
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

async def process_random_reactions(message: Message, model, save_user_message, track_message_statistics, add_chat, chat_settings, save_chat_settings):
    """Основная функция обработки случайных реакций"""
    await save_user_message(message)
    await track_message_statistics(message)
    add_chat(message.chat.id, message.chat.title, message.chat.username)    
    
    chat_id = str(message.chat.id)
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {"dialog_enabled": True, "prompt": None}
        save_chat_settings()

    # >>> ИЗМЕНЕННЫЙ БЛОК: СИТУАТИВНАЯ РЕМАРКА (ВЕРОЯТНОСТЬ 1%) <<<
    # Ставим эту проверку в самое начало, чтобы она имела приоритет
    if random.random() < 0.01: # Вероятность 1%
        # Передаем chat.id (int) и объект модели
        situational_reaction = await generate_situational_reaction(message.chat.id, model)
        if situational_reaction:
            # Отправляем сообщение в чат, а не реплаем
            await message.bot.send_message(message.chat.id, situational_reaction, parse_mode="Markdown")
            return True  # Реакция отправлена, выходим

    # --- Далее идут остальные реакции без изменений ---

    if message.from_user.id == 1399269377 and random.random() < 0.3 and message.text:
        success = await generate_insult_for_lis(message, model)
        if success:
            return True

    if random.random() < 0.0001:
        success = await send_random_common_voice_reaction(message)
        if success:
            return True
            
    if message.text and "пара дня" in message.text.lower() and random.random() < 0.05:
        success = await send_para_voice_reaction(message)
        if success:
            return True

    if message.voice and random.random() < 0.001:
        success = await send_random_voice_reaction(message)
        if success:
            return True

    if random.random() < 0.0008 and message.text:
        rhyme_reaction = await generate_rhyme_reaction(message, model)
        if rhyme_reaction:
            await message.reply(rhyme_reaction)
            return True

    if random.random() < 0.0008 and message.text:
        regular_reaction = await generate_regular_reaction(message)
        if regular_reaction:
            await message.reply(regular_reaction)
            return True

    # Проверяем включен ли диалог
    if not chat_settings[chat_id]["dialog_enabled"]:
        return False
        
    return False
