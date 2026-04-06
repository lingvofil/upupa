import re
import os
import random
import logging
import asyncio
import json
from aiogram.types import FSInputFile, Message, ReactionTypeEmoji
from aiogram import Bot

from config import model, groq_ai, gigachat_model, chat_settings, conversation_history

# Полный список доступных реакций Telegram
TELEGRAM_REACTIONS = [
    "❤️", "🥰", "😁", "❤️‍🔥", "💔", "🤨", "👀", "🫡"
]

# Кэш последних использованных слов для избежания повторов
_recent_word_reactions = {}

# --- Универсальная функция выбора активной модели ---

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
    Универсальная генерация текста с автоматическим выбором модели.
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

# --- Кинематографичные ремарки с выбором активной модели ---

async def generate_situational_reaction(chat_id: int) -> str | None:
    """
    Генерирует одну короткую циничную ремарку строго по теме последних реплик.
    Источник данных: актуальная conversation_history.
    """
    logging.info(f"[situational] Запуск генерации ситуативной реакции для чата {chat_id}.")

    chat_key = str(chat_id)
    history = conversation_history.get(chat_key, [])
    if not history:
        logging.warning(f"[situational] Для чата {chat_id} нет актуальной истории диалога.")
        return None

    usable_messages = [msg for msg in history if msg.get("content", "").strip()]
    if len(usable_messages) < 3:
        logging.info(
            f"[situational] Недостаточно контекста для чата {chat_id}: "
            f"{len(usable_messages)} валидных сообщений."
        )
        return None

    focus_count = min(5, len(usable_messages))
    focus_messages = usable_messages[-focus_count:]
    older_messages = usable_messages[:-focus_count]

    meme_phrases = {
        "паприкаш", "гойда", "база", "кринж", "имба", "разъеб", "sigma", "сигма"
    }

    older_meme_hits = []
    for msg in older_messages[-10:]:
        content = msg.get("content", "").lower()
        hit = [phrase for phrase in meme_phrases if phrase in content]
        if hit:
            older_meme_hits.extend(hit)
    older_meme_hits = sorted(set(older_meme_hits))

    formatted_focus = []
    for idx, msg in enumerate(focus_messages, start=1):
        author = msg.get("name") or ("Бот" if msg.get("role") == "assistant" else "Пользователь")
        role = msg.get("role", "user")
        text = msg.get("content", "").strip()

        reply_to = msg.get("reply_to_name") or msg.get("reply_to")
        reply_hint = f" -> ответ на: {reply_to}" if reply_to else ""

        formatted_focus.append(f"{idx}. [{author} | {role}]{reply_hint}: {text}")

    focus_block = "\n".join(formatted_focus)
    logging.info(
        f"[situational] Подготовлено {len(focus_messages)} последних сообщений "
        f"для чата {chat_id}."
    )
    logging.debug(f"[situational] Фокус-контекст:\n{focus_block}")
    if older_meme_hits:
        logging.info(
            f"[situational] В старом контексте найдены мемные слова "
            f"(будут проигнорированы): {', '.join(older_meme_hits)}"
        )

    prompt = f"""
Ты — циничный закадровый голос грязного артхауса.
Твоя задача: дать ровно одну короткую ремарку по теме последних сообщений.

Жёсткие правила:
- Учитывай авторов, последовательность и возможные ответы друг другу (reply-chain).
- Работай СТРОГО по последним {len(focus_messages)} сообщениям ниже.
- Игнорируй старые мемы/шаблоны и случайные слова, если их нет в последних репликах.
- Тон: цинично, с двусмысленностью, органично в контексте.
- Никаких объяснений, вариантов, вопросов.
- Формат: только одна фраза в звёздочках (*...*).

Последние сообщения:
---
{focus_block}
---

Ремарка:
""".strip()

    try:
        reaction_text = await generate_with_model(
            prompt,
            chat_id,
            temperature=0.72,
            max_tokens=45
        )
        logging.info(f"[situational] Ответ модели для чата {chat_id}: '{reaction_text}'")

        if reaction_text and reaction_text.startswith('*') and reaction_text.endswith('*'):
            logging.info(f"[situational] Валидная ремарка сгенерирована для чата {chat_id}.")
            return reaction_text

        logging.warning(
            f"[situational] Невалидный формат ответа для чата {chat_id}. "
            "Ожидался формат *...*; возвращаем None."
        )
        return None
    except Exception as e:
        logging.error(f"[situational] Ошибка генерации реакции для чата {chat_id}: {e}", exc_info=True)
        return None

# --- НОВОЕ: Алгоритмическая реакция "я %слово%" БЕЗ AI ---

async def generate_random_word_reaction(chat_id: int):
    """
    Алгоритмический выбор слова/фразы из последних сообщений для реакции "я %слово%".
    Использует контекстный анализ для естественного попадания в тему.
    Берет данные из актуальной истории диалога (conversation_history).
    """
    from config import conversation_history
    
    logging.info(f"Запуск генерации реакции 'я %слово%' для чата {chat_id}.")
    
    chat_key = str(chat_id)
    
    # Берем из актуальной истории диалога (как в talking.py)
    if chat_key not in conversation_history or not conversation_history[chat_key]:
        logging.warning(f"Для чата {chat_id} нет актуальной истории диалога. Реакция отменена.")
        return None
    
    # Берем последние 5-7 сообщений из conversation_history
    recent_history = conversation_history[chat_key][-7:]
    
    # Формируем текст для анализа из актуальных сообщений
    chat_history = "\n".join([
        f"{msg.get('name', 'Пользователь')}: {msg.get('content', '')}"
        for msg in recent_history
    ])
    
    if not chat_history.strip():
        logging.warning(f"История диалога пуста для чата {chat_id}. Реакция отменена.")
        return None
        
    logging.info(f"Взято последних {len(recent_history)} сообщений из актуальной истории диалога.")
    
    try:
        # Стоп-слова (самые частые и неинтересные)
        stop_words = {
            'это', 'был', 'была', 'были', 'что', 'как', 'где', 'когда', 'кто', 
            'чтобы', 'если', 'или', 'для', 'при', 'под', 'над', 'про', 'без',
            'так', 'вот', 'уже', 'еще', 'ещё', 'тут', 'там', 'тебе', 'мне',
            'его', 'её', 'их', 'эти', 'тот', 'эта', 'весь', 'всё', 'мой',
            'твой', 'наш', 'ваш', 'себя', 'быть', 'есть', 'нет', 'да'
        }
        
        # Интересные категории слов (приоритет)
        interesting_patterns = {
            'существительные': r'\b([а-яё]+(?:ость|ние|ание|ение|тель|щик|ник|ка|ец|ист|лог|граф|фил))\b',
            'профессии': r'\b(врач|юрист|программист|дизайнер|менеджер|директор|художник|музыкант|писатель|блогер|стример|геймер)\b',
            'оскорбления': r'\b(пидор|ебал|хуй|блядь|сука|мудак|долбоёб|дебил|идиот|даун|уебок|говно|шлюх|пидар|еблан)\w*',
            'животные': r'\b(собак|кот|кошк|птиц|рыб|змей|медвед|волк|лис|заяц|мыш|крыс|обезьян)\w*',
            'эмоции': r'\b(любовь|ненависть|радость|грусть|страх|злость|счастье|тоска|боль|кайф)\w*',
            'абстракции': r'\b(философ|идея|мысль|концепция|теория|принцип|метод|система|процесс|явление)\w*',
            'еда': r'\b(пиво|водка|виск|вино|торт|пицц|бургер|шаурм|суши|паста|мясо|сыр|хлеб)\w*',
            'техника': r'\b(компьютер|телефон|ноутбук|планшет|консоль|приставк|робот|дрон|гаджет)\w*'
        }
        
        # --- 1. АНАЛИЗ: Извлекаем все слова (минимум 4 символа) ---
        all_words = re.findall(r'\b[а-яёА-ЯЁ]{4,}\b', chat_history.lower())  # ИЗМЕНЕНО: 4+ символов вместо 3+
        
        if not all_words:
            return None
        
        # --- 2. ФИЛЬТРАЦИЯ: Убираем стоп-слова ---
        filtered_words = [w for w in all_words if w not in stop_words]
        
        if not filtered_words:
            filtered_words = all_words  # Если все слова стоп-слова, берем хоть что-то
        
        # --- 3. ПРИОРИТИЗАЦИЯ: Ищем интересные слова по паттернам ---
        priority_words = []
        
        for category, pattern in interesting_patterns.items():
            matches = re.findall(pattern, chat_history.lower())
            if matches:
                # Разворачиваем вложенные списки (если есть группы в regex)
                flat_matches = []
                for match in matches:
                    if isinstance(match, tuple):
                        flat_matches.extend([m for m in match if m])
                    else:
                        flat_matches.append(match)
                
                priority_words.extend(flat_matches)
                logging.info(f"Найдены слова категории '{category}': {flat_matches}")
        
        # --- 4. ВЫБОР СЛОВА С УЛУЧШЕННЫМИ ПРИОРИТЕТАМИ ---
        chosen_word = None
        
        # ПРИОРИТЕТ 1: Слова из ПОСЛЕДНЕГО сообщения (60% шанс - самое актуальное!)
        if recent_history and random.random() < 0.6:
            last_msg_content = recent_history[-1].get('content', '')
            last_msg_words = re.findall(r'\b[а-яёА-ЯЁ]{4,}\b', last_msg_content.lower())  # 4+ символов
            last_msg_filtered = [w for w in last_msg_words if w not in stop_words]
            
            if last_msg_filtered:
                chosen_word = random.choice(last_msg_filtered)
                logging.info(f"[ПРИОРИТЕТ 1] Выбрано слово из последнего сообщения: '{chosen_word}'")
        
        # ПРИОРИТЕТ 2: Интересные слова из всей истории (30% шанс)
        if not chosen_word and priority_words and random.random() < 0.8:  # 80% шанс при условии что Приоритет 1 не сработал
            chosen_word = random.choice(priority_words)
            logging.info(f"[ПРИОРИТЕТ 2] Выбрано приоритетное слово: '{chosen_word}'")
        
        # ПРИОРИТЕТ 3: Любое отфильтрованное слово (fallback)
        if not chosen_word and filtered_words:
            chosen_word = random.choice(filtered_words)
            logging.info(f"[ПРИОРИТЕТ 3] Выбрано случайное отфильтрованное слово: '{chosen_word}'")
        
        if not chosen_word:
            return None
        
        # --- ЗАЩИТА ОТ ПОВТОРОВ ---
        # Проверяем, не использовали ли мы это слово недавно в этом чате
        chat_key = str(chat_id)
        if chat_key not in _recent_word_reactions:
            _recent_word_reactions[chat_key] = []
        
        # Если это слово было в последних 5 реакциях, пробуем выбрать другое
        recent_words = _recent_word_reactions[chat_key]
        if chosen_word in recent_words and len(filtered_words) > 5:
            # Пробуем найти альтернативу
            alternative_words = [w for w in filtered_words if w not in recent_words]
            if alternative_words:
                chosen_word = random.choice(alternative_words)
                logging.info(f"[ANTI-REPEAT] Заменили повторяющееся слово на: '{chosen_word}'")
        
        # Сохраняем использованное слово (храним последние 5)
        recent_words.append(chosen_word)
        if len(recent_words) > 5:
            recent_words.pop(0)
        
        # --- 5. СЛОВОСОЧЕТАНИЯ: Иногда берем 2 слова подряд ---
        final_phrase = chosen_word
        
        if random.random() < 0.15:  # СНИЖЕНО с 30% до 15% - реже делаем словосочетания
            # Ищем это слово в оригинальном тексте (с учетом регистра)
            words_in_context = re.findall(r'\b[а-яёА-ЯЁ]+\b', chat_history)
            
            try:
                # Находим индекс выбранного слова
                idx = next(i for i, w in enumerate(words_in_context) if w.lower() == chosen_word)
                
                # Пробуем взять следующее слово
                if idx < len(words_in_context) - 1:
                    next_word = words_in_context[idx + 1].lower()
                    # Более строгая проверка для второго слова
                    if (next_word not in stop_words and 
                        len(next_word) > 3 and  # УВЕЛИЧЕНО с 2 до 3 символов
                        next_word.isalpha()):   # Только буквы, без цифр
                        final_phrase = f"{chosen_word} {next_word}"
                        logging.info(f"Создано словосочетание: '{final_phrase}'")
            except (StopIteration, IndexError):
                pass  # Оставляем одно слово
        
        result = f"я {final_phrase}"
        logging.info(f"Итоговая реакция: '{result}'")
        return result
        
    except Exception as e:
        logging.error(f"Ошибка в алгоритмической генерации 'я %слово%': {e}", exc_info=True)
        return None

# --- Рифма с выбором активной модели ---

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
    model_placeholder,
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
        situational = await generate_situational_reaction(message.chat.id)
        if situational:
            await message.bot.send_message(
                message.chat.id,
                situational,
                parse_mode="Markdown",
            )
            return True

    # ------------------------------------------------------------------
    # 5.1. НОВОЕ: Алгоритмическая реакция "я %слово%" - key: random_word_prob
    # ------------------------------------------------------------------
    random_word_prob = chat_cfg.get("random_word_prob", 0.005)
    if random.random() < random_word_prob:
        random_word_reaction = await generate_random_word_reaction(message.chat.id)
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
        if await generate_insult_for_lis(message):
            return True

    if message.from_user.id == 113086922 and random.random() < 0.005:
        if await generate_reaction_for_113086922(message):
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
        rhyme = await generate_rhyme_reaction(message)
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
