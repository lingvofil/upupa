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
from config import model, groq_ai, gigachat_model, chat_settings # ИСПРАВЛЕНО: добавлен chat_settings

# Полный список доступных реакций Telegram
TELEGRAM_REACTIONS = [
    "❤️", "🥰", "😁", "❤️‍🔥", "💔", "🤨", "👀", "🫡"
]

# --- ИСПРАВЛЕНО: Универсальная функция выбора активной модели ---

async def get_active_model_for_chat(chat_id: int):
    """
    Возвращает активную модель для чата на основе настроек.
    ВАЖНО: Эта функция используется вместо прямого обращения к model.
    """
    chat_key = str(chat_id)
    current_settings = chat_settings.get(chat_key, {})
    active_model_name = current_settings.get("active_model", "gemini")
    
    # Режим истории не подходит для реакций
    if active_model_name == "history":
        active_model_name = "gemini"
    
    logging.info(f"Активная модель для чата {chat_id}: {active_model_name}")
    
    if active_model_name == "gigachat":
        return gigachat_model, "gigachat"
    elif active_model_name == "groq":
        return groq_ai, "groq"
    else:  # gemini
        return model, "gemini"

async def generate_with_model(prompt: str, chat_id: int, temperature: float = 0.7, max_tokens: int = 60) -> str:
    """
    НОВАЯ ФУНКЦИЯ: Универсальная генерация текста с автоматическим выбором модели.
    Используется во всех AI-реакциях.
    """
    model_instance, model_name = await get_active_model_for_chat(chat_id)
    
    def sync_generate():
        try:
            if model_name == "groq":
                return groq_ai.generate_text(prompt, max_tokens=max_tokens)
            elif model_name == "gigachat":
                response = gigachat_model.generate_content(prompt, chat_id=chat_id)
                return response.text
            else:  # gemini
                response = model.generate_content(
                    prompt, 
                    chat_id=chat_id,
                    generation_config={
                        'temperature': temperature,
                        'max_output_tokens': max_tokens,
                        'top_p': 1.0,
                    }
                )
                if response and response.candidates and response.candidates[0].content.parts:
                    return response.text.strip()
                return ""
        except Exception as e:
            logging.error(f"Ошибка генерации с моделью {model_name}: {e}")
            return ""
    
    return await asyncio.to_thread(sync_generate)

# --- Случайные эмодзи-реакции (БЕЗ AI) ---
async def set_random_emoji_reaction(message: Message):
    """
    Ставит случайный эмодзи из списка без анализа контекста.
    Быстро, бесплатно, не грузит API.
    """
    try:
        chosen_emoji = random.choice(TELEGRAM_REACTIONS)
        await message.react(reaction=[ReactionTypeEmoji(emoji=chosen_emoji)])
        logging.info(f"Бот поставил случайную реакцию: {chosen_emoji}")
        return True
    except Exception as e:
        logging.error(f"Ошибка при проставлении случайной эмодзи-реакции: {e}")
        return False

# --- ИСПРАВЛЕНО: Кинематографичные ремарки с выбором активной модели ---

async def generate_situational_reaction(chat_id: int):
    """
    Генерирует ироничную кинематографичную ремарку на основе истории чата.
    ИСПРАВЛЕНО: Автоматически выбирает активную модель для чата.
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
        reaction_text = await generate_with_model(prompt, chat_id, temperature=1.0, max_tokens=60)
        logging.info(f"Ответ от модели для ситуативной реакции: '{reaction_text}'")

        if reaction_text and reaction_text.startswith('*') and reaction_text.endswith('*'):
            return reaction_text
        else:
            return None

    except Exception as e:
        logging.error(f"Ошибка при генерации ситуативной реакции: {e}", exc_info=True)
        return None

# --- ИСПРАВЛЕНО: Реакция "я %слово%" с выбором активной модели ---

async def generate_random_word_reaction(chat_id: int):
    """
    ИСПРАВЛЕНО: Выбирает случайное слово/словосочетание из последних 20 сообщений
    и генерирует реакцию в формате "я %это слово/словосочетание%".
    Автоматически использует активную модель для чата.
    """
    logging.info(f"Запуск генерации реакции 'я %слово%' для чата {chat_id}.")
    
    all_messages = await extract_chat_messages(chat_id)
    
    if not all_messages:
        logging.warning(f"Для чата {chat_id} не найдено сообщений в логе. Реакция отменена.")
        return None

    last_messages = all_messages[-20:]
    chat_history = "\n".join(last_messages)
    
    if not chat_history.strip():
        return None
        
    logging.info(f"Взято последних {len(last_messages)} сообщений для генерации реакции 'я %слово%'.")
    logging.debug(f"История для анализа: {chat_history[:200]}...")  # Первые 200 символов для отладки

    prompt = f"""
    ЗАДАЧА: Выбери одно слово или короткое словосочетание (максимум 2-3 слова) СТРОГО из диалога ниже.
    Затем составь фразу в формате: "я [выбранное слово/словосочетание]"
    
    КРИТИЧЕСКИ ВАЖНО:
    - Используй ТОЛЬКО слова, которые ЕСТЬ в диалоге ниже
    - НЕ выдумывай новые слова
    - НЕ используй слова, которых НЕТ в диалоге
    - Выбирай интересные, смешные или абсурдные слова/фразы
    
    Примеры правильного формата:
    - "я реактивный самолет" (если в диалоге есть "реактивный самолет")
    - "я пидорас" (если в диалоге есть "пидорас")
    - "я твоя мама" (если в диалоге есть "твоя мама")
    - "я философ" (если в диалоге есть "философ")
    
    Диалог для анализа:
    ---
    {chat_history}
    ---

    Твоя фраза (ТОЛЬКО "я [слово из диалога выше]"):
    """
    
    try:
        reaction_text = await generate_with_model(prompt, chat_id, temperature=0.8, max_tokens=30)
        logging.info(f"Ответ от модели для реакции 'я %слово%': '{reaction_text}'")

        # Проверяем, что ответ начинается с "я " (регистронезависимо)
        if reaction_text and reaction_text.lower().startswith('я '):
            # НОВОЕ: Проверяем, что слово из ответа действительно есть в диалоге
            word_part = reaction_text[2:].strip().lower()  # Убираем "я "
            
            # Проверка: есть ли это слово в диалоге?
            if word_part and word_part in chat_history.lower():
                return reaction_text
            else:
                logging.warning(f"Модель выдумала слово '{word_part}', которого нет в диалоге. Используем fallback.")
                # FALLBACK: Простой Python-выбор случайного слова
                return await generate_simple_random_word_reaction(chat_history)
        else:
            return None

    except Exception as e:
        logging.error(f"Ошибка при генерации реакции 'я %слово%': {e}", exc_info=True)
        # FALLBACK при ошибке
        try:
            return await generate_simple_random_word_reaction(chat_history)
        except:
            return None

async def generate_simple_random_word_reaction(chat_history: str):
    """
    FALLBACK-функция: Простой выбор случайного слова из диалога без AI.
    Используется, когда модель выдумывает несуществующие слова.
    """
    try:
        # Разбиваем на слова
        import re
        words = re.findall(r'\b[а-яёА-ЯЁa-zA-Z]{3,}\b', chat_history)
        
        if not words:
            return None
        
        # Фильтруем стоп-слова
        stop_words = {'это', 'был', 'была', 'были', 'что', 'как', 'где', 'когда', 'кто', 'чтобы', 'если', 'или', 'для', 'при', 'под', 'над'}
        filtered_words = [w for w in words if w.lower() not in stop_words and len(w) > 3]
        
        if not filtered_words:
            filtered_words = words
        
        # Выбираем случайное слово
        chosen_word = random.choice(filtered_words)
        
        # Иногда выбираем словосочетание (2 слова)
        if len(filtered_words) > 1 and random.random() < 0.3:
            idx = filtered_words.index(chosen_word)
            if idx < len(filtered_words) - 1:
                chosen_word = f"{chosen_word} {filtered_words[idx + 1]}"
        
        result = f"я {chosen_word.lower()}"
        logging.info(f"FALLBACK: Сгенерирована простая реакция без AI: '{result}'")
        return result
        
    except Exception as e:
        logging.error(f"Ошибка в fallback-генерации: {e}")
        return None

# --- ИСПРАВЛЕНО: Рифма с выбором активной модели ---

async def generate_rhyme_reaction(message):
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
            
            rhyme_word = await generate_with_model(rhyme_prompt, chat_id, temperature=0.7, max_tokens=10)
            
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

# ИСПРАВЛЕНО: Персональные реакции с выбором активной модели

async def generate_insult_for_lis(message):
    """Генерирует реакцию для пользователя 1399269377."""
    chat_id = message.chat.id
    try:
        if random.random() < 0.9:
            logging.info("Генерация МИКСА фразы для 1399269377...")
            
            prompt = (
                "Ты — микшер фраз. Твоя задача — взять 2-3 фразы из списка ниже и смешать их, чтобы получилась новая, но в том же стиле. "
                "ВАЖНО: Используй ТОЛЬКО слова и короткие обороты из предложенных примеров. Не добавляй НИЧЕГО от себя. "
                "Твой ответ — только результат микса (5-15 слов), без пояснений.\n\n"
                "Примеры для микширования:\n" + "\n".join(INSULT_WORDS_FOR_1399269377) +
                "\n\nТвой микс (ТОЛЬКО из слов выше):"
            )
            
            new_phrase = await generate_with_model(prompt, chat_id, temperature=0.6, max_tokens=60)
            
            if new_phrase:
                await message.reply(new_phrase)
                return True
            else:
                logging.warning("Не удалось сгенерировать МИКС для 1399269377, используется случайная из списка (фолбэк).")
                selected_phrase = random.choice(INSULT_WORDS_FOR_1399269377)
                await message.reply(selected_phrase)
                return True
        else:
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

async def generate_reaction_for_113086922(message: Message):
    """Генерирует реакцию для пользователя 113086922."""
    chat_id = message.chat.id
    try:
        if random.random() < 0.9:
            logging.info("Генерация МИКСА фразы для 113086922...")
            
            prompt = (
                "Ты — микшер фраз. Твоя задача — взять 2-3 фразы из списка ниже и смешать их, чтобы получилась новая, но в том же стиле. "
                "ВАЖНО: Используй ТОЛЬКО слова и короткие обороты из предложенных примеров. Не добавляй НИЧЕГО от себя. "
                "Твой ответ — только результат микса (5-15 слов), без пояснений.\n\n"
                "Примеры для микширования:\n" + "\n".join(PHRASES_FOR_113086922) +
                "\n\nТвой микс (ТОЛЬКО из слов выше):"
            )
            
            new_phrase = await generate_with_model(prompt, chat_id, temperature=0.6, max_tokens=60)
            
            if new_phrase:
                await message.reply(new_phrase)
                return True
            else:
                logging.warning("Не удалось сгенерировать МИКС для 113086922, используется случайная из списка (фолбэк).")
                selected_phrase = random.choice(PHRASES_FOR_113086922)
                await message.reply(selected_phrase)
                return True
        else:
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
    model_placeholder,  # ИЗМЕНЕНО: теперь не используется напрямую
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
    # 3. EMOJI-РЕАКЦИИ (Random, без AI) - key: emoji_prob
    # ------------------------------------------------------------------
    if chat_cfg.get("emoji_enabled", True):
        emoji_prob = chat_cfg.get("emoji_prob", 0.01)
        if random.random() < emoji_prob:
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
    # 5. Ситуативная текстовая реакция (AI/Remarks) - key: ai_prob
    # ------------------------------------------------------------------
    ai_prob = chat_cfg.get("ai_prob", 0.01)
    if random.random() < ai_prob:
        situational = await generate_situational_reaction(message.chat.id)  # ИСПРАВЛЕНО: убран model_instance
        if situational:
            await message.bot.send_message(
                message.chat.id,
                situational,
                parse_mode="Markdown",
            )
            return True

    # ------------------------------------------------------------------
    # 5.1. НОВОЕ: Реакция "я %слово%" - key: random_word_prob
    # ------------------------------------------------------------------
    random_word_prob = chat_cfg.get("random_word_prob", 0.005)
    if random.random() < random_word_prob:
        random_word_reaction = await generate_random_word_reaction(message.chat.id)  # ИСПРАВЛЕНО: убран model_instance
        if random_word_reaction:
            await message.bot.send_message(
                message.chat.id,
                random_word_reaction,
            )
            return True

    # ------------------------------------------------------------------
    # 6. Персональные реакции (Easter Eggs)
    # ------------------------------------------------------------------
    if message.from_user.id == 1399269377 and message.text and random.random() < 0.3:
        if await generate_insult_for_lis(message):  # ИСПРАВЛЕНО: убран model_instance
            return True

    if message.from_user.id == 113086922 and random.random() < 0.005:
        if await generate_reaction_for_113086922(message):  # ИСПРАВЛЕНО: убран model_instance
            return True

    # ------------------------------------------------------------------
    # 7. Голосовые реакции - key: voice_prob
    # ------------------------------------------------------------------
    voice_prob = chat_cfg.get("voice_prob", 0.0001)
    
    if message.voice and random.random() < 0.001: 
        if await send_random_voice_reaction(message):
            return True

    if random.random() < voice_prob:
        if await send_random_common_voice_reaction(message):
            return True

    if message.text and "пара дня" in message.text.lower() and random.random() < 0.05:
        if await send_para_voice_reaction(message):
            return True

    # ------------------------------------------------------------------
    # 8. Рифма - key: rhyme_prob
    # ------------------------------------------------------------------
    rhyme_prob = chat_cfg.get("rhyme_prob", 0.008)
    if message.text and random.random() < rhyme_prob:
        rhyme = await generate_rhyme_reaction(message)  # ИСПРАВЛЕНО: убран model_instance
        if rhyme:
            await message.reply(rhyme)
            return True

    # ------------------------------------------------------------------
    # 9. Обычная текстовая реакция (Штаны) - key: regular_prob
    # ------------------------------------------------------------------
    regular_prob = chat_cfg.get("regular_prob", 0.008)
    if message.text and random.random() < regular_prob:
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
