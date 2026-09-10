import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_campaign as campaign


def _session(chat_id=-1009001):
    return SimpleNamespace(
        chat_id=chat_id,
        mode="participants",
        state="LOBBY",
        starter_name="Ведущий",
        lobby_message_id=777,
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        conversation=[],
    )


class FakeDnd:
    def __init__(self, session, responses):
        self.dnd_sessions = {session.chat_id: session}
        self.responses = list(responses)
        self.prompts = []
        self.persist_calls = 0

    async def generate_session_response(self, session, prompt):
        self.prompts.append(prompt)
        session.conversation.append({"role": "user", "content": prompt})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        session.conversation.append({"role": "assistant", "content": response})
        return response

    def persist_dnd_sessions(self):
        self.persist_calls += 1


VALID_PROFILE_OPTIONS = (
    "1. архивный фокусник без лицензии\n"
    "2. курьер сомнительных пророчеств\n"
    "3. светский охотник на проклятия\n"
    "4. инженер бытовых катастроф\n"
    "5. коллекционер чужих неприятностей"
)

VALID_PLOTS = (
    "1. В музее запахов экспонаты сбежали и требуют вернуть украденные воспоминания.\n"
    "2. Плавучий рынок несёт штормом к военной гавани, пока торговцы делят единственный руль.\n"
    "3. Горный санаторий теряет этажи по ночам, а постояльцы саботируют эвакуацию ради завтрака.\n"
    "4. Воздушный цирк рухнул на банк, и артисты пытаются спасти зверей раньше золота.\n"
    "5. Подземный почтамт бастует, а неотправленные письма материализуют разгневанных адресатов."
)


def test_random_profile_contains_all_lightweight_fields_without_legacy_profile_options():
    assert "PROFILE_OPTIONS" not in campaign.__dict__
    profile = campaign._random_profile()
    assert campaign._profile_complete(profile)
    assert set(profile) == set(campaign.PROFILE_STEPS)


def test_profile_option_parser_accepts_numbered_ai_output():
    options = campaign._parse_profile_options(VALID_PROFILE_OPTIONS)
    assert len(options) == campaign.PROFILE_OPTION_COUNT == 5
    assert len(set(options)) == 5
    assert options[0] == "архивный фокусник без лицензии"


def test_profile_option_parser_accepts_json_array():
    raw = '["тихий громила", "нервный эстет", "уличный пророк", "морской бухгалтер", "вежливый мародёр"]'
    options = campaign._parse_profile_options(raw)
    assert campaign._options_are_valid(options)


def test_profile_generation_retries_until_exact_unique_batch():
    session = _session()
    bad = "1. одинаковый тип\n2. одинаковый тип\n3. третий"
    dnd = FakeDnd(session, [bad, VALID_PROFILE_OPTIONS])

    options = asyncio.run(campaign._generate_profile_options(dnd, session, 1, "style"))

    assert len(dnd.prompts) == 2
    assert options == campaign._parse_profile_options(VALID_PROFILE_OPTIONS)
    assert session.profile_options["1"]["style"] == options
    assert session.conversation == []


def test_profile_regeneration_excludes_previous_batch():
    session = _session()
    first = VALID_PROFILE_OPTIONS
    second = (
        "1. придворный вредитель с манерами\n"
        "2. ночной инспектор чудес\n"
        "3. речной контрабандист легенд\n"
        "4. уставший дуэлянт-натуралист\n"
        "5. парадный специалист по побегам"
    )
    dnd = FakeDnd(session, [first, second])
    first_options = asyncio.run(campaign._generate_profile_options(dnd, session, 1, "style"))
    second_options = asyncio.run(campaign._generate_profile_options(dnd, session, 1, "style", exclude=first_options))

    assert first_options != second_options
    assert all(item in dnd.prompts[-1] for item in first_options)
    assert session.profile_options["1"]["style"] == second_options


def test_generated_complete_profile_parser_and_late_join_ai_profile(monkeypatch):
    session = _session()
    campaign._ensure(session)
    payload = (
        '{"style":"курьер запретных реликвий","strength":"видит чужой блеф",'
        '"weakness":"лезет проверять запретное","special":"аварийный план из кармана"}'
    )
    dnd = FakeDnd(session, [payload])
    monkeypatch.setattr(campaign, "_player_history", lambda *_args: None)

    profile = asyncio.run(campaign._auto_profile(dnd, session, 1))

    assert profile == {
        "style": "курьер запретных реликвий",
        "strength": "видит чужой блеф",
        "weakness": "лезет проверять запретное",
        "special": "аварийный план из кармана",
    }
    assert session.character_profiles["1"] == profile
    assert dnd.prompts and "JSON-объект" in dnd.prompts[0]


def test_legacy_profile_button_after_restart_migrates_to_generated_batch(monkeypatch):
    session = _session()
    campaign._ensure(session)
    generated = campaign._parse_profile_options(VALID_PROFILE_OPTIONS)

    async def fake_generate(_dnd, target_session, user_id, step, exclude=None):
        assert target_session is session
        assert user_id == 1
        assert step == "style"
        target_session.profile_options.setdefault("1", {})["style"] = generated
        return generated

    monkeypatch.setattr(campaign, "_generate_profile_options", fake_generate)

    class Message:
        chat = SimpleNamespace(id=session.chat_id)

        def __init__(self):
            self.text = None
            self.markup = None

        async def edit_text(self, text, reply_markup=None):
            self.text = text
            self.markup = reply_markup

    class Callback:
        data = "dnd:prof:1:style:1"
        from_user = SimpleNamespace(id=1, first_name="Алиса")
        bot = SimpleNamespace()

        def __init__(self):
            self.message = Message()
            self.answers = []

        async def answer(self, text=None, **kwargs):
            self.answers.append((text, kwargs))

    callback = Callback()
    dnd = SimpleNamespace(dnd_sessions={session.chat_id: session}, persist_dnd_sessions=lambda: None)

    asyncio.run(campaign._profile_callback(callback, dnd))

    assert session.character_profiles.get("1", {}) == {}
    assert session.profile_options["1"]["style"] == generated
    assert callback.message.markup.inline_keyboard[-1][0].text == "🎲 Ещё варианты"
    assert "Старая кнопка" in callback.answers[0][0]


def test_archive_campaign_saves_selected_profile(monkeypatch):
    session = _session()
    campaign._ensure(session)
    profile = {
        "style": "курьер запретных реликвий",
        "strength": "видит чужой блеф",
        "weakness": "лезет проверять запретное",
        "special": "аварийный план из кармана",
    }
    session.character_profiles["1"] = profile
    session.selected_plot = "Проверочный сюжет"
    campaign._archive = {"version": 1, "chats": {}}
    monkeypatch.setattr(campaign, "_save_archive", lambda _dnd: None)

    campaign._archive_campaign(SimpleNamespace(), session, "финал", "эпилог")

    saved = campaign._archive["chats"][str(session.chat_id)]["players"]["1"]["profile"]
    assert saved == profile


def test_plot_parser_returns_only_model_candidates_without_padding():
    options = campaign._parse_plot_options("1. Первый сюжет\n2. Второй сюжет\n")
    assert options == ["Первый сюжет", "Второй сюжет"]


def test_plot_validation_rejects_duplicates_close_variants_and_forbidden_examples():
    duplicates = campaign._parse_plot_options(
        "1. Побег из библиотеки\n2. Побег из библиотеки\n3. Порт\n4. Горы\n5. Цирк"
    )
    assert campaign._plot_options_are_valid(duplicates) is False

    close = campaign._parse_plot_options(
        "1. Огромный поезд везёт героев через пустыню к катастрофе\n"
        "2. Огромный поезд везёт партию через пустыню прямо к катастрофе\n"
        "3. Рыбацкий порт объявил войну чайкам\n4. Вулканический отель теряет гостей\n5. Летающий театр ищет сцену"
    )
    assert campaign._plot_options_are_valid(close) is False

    forbidden = campaign._parse_plot_options(
        "1. Временная петля заперла пассажиров в трамвае\n"
        "2. Порт спорит с маяком\n3. Горы требуют налог\n4. Цирк сбежал\n5. Архив объявил забастовку"
    )
    assert campaign._plot_options_are_valid(forbidden) is False


def test_plot_generation_retries_bad_batch_before_emergency_fallback():
    session = _session()
    bad = (
        "1. Временная петля в метро\n2. Временная петля в автобусе\n3. Временная петля в порту\n"
        "4. Временная петля в цирке\n5. Временная петля в лесу"
    )
    dnd = FakeDnd(session, [bad, VALID_PLOTS])

    options = asyncio.run(campaign._plot_choices(dnd, session))

    assert len(dnd.prompts) == 2
    assert options == campaign._parse_plot_options(VALID_PLOTS)
    assert session.conversation == []


def test_emergency_plots_do_not_contain_old_examples():
    joined = " ".join(campaign.EMERGENCY_PLOTS).casefold()
    for old_fragment in ("марианск", "обувного магазина", "временная петля", "выборы мэра"):
        assert old_fragment not in joined
    assert len(campaign.EMERGENCY_PLOTS) > 5
    assert all(not campaign._plot_is_forbidden(plot) for plot in campaign.EMERGENCY_PLOTS)


def test_plot_text_and_buttons_use_same_final_array(monkeypatch):
    session = _session()
    session.mode = "participants"

    async def choices(_dnd, _session):
        return campaign._parse_plot_options(VALID_PLOTS)

    monkeypatch.setattr(campaign, "_plot_choices", choices)

    class Message:
        def __init__(self):
            self.text = None
            self.markup = None

        async def edit_text(self, text, reply_markup=None):
            self.text = text
            self.markup = reply_markup

        async def answer(self, *_args, **_kwargs):
            raise AssertionError("edit_text should succeed")

    message = Message()
    callback = SimpleNamespace(message=message)
    dnd = SimpleNamespace(persist_dnd_sessions=lambda: None)

    asyncio.run(campaign._choose_plots(dnd, callback, session))

    assert all(option in message.text for option in session.plot_options)
    button_labels = [row[0].text for row in message.markup.inline_keyboard[:5]]
    assert button_labels == [f"{i + 1}. {option[:44]}" for i, option in enumerate(session.plot_options)]


def test_risk_levels_have_monotonic_difficulty():
    assert [campaign.RISK_DC[x] for x in ("LOW", "MEDIUM", "HIGH", "EXTREME")] == [8, 11, 14, 17]
    assert campaign._extract_risk("ROLL;TYPE:CHECK;RISK:HIGH;MODE:NORMAL") == "HIGH"
    assert campaign._risk_from_response("текст [ACTION:ROLL;TYPE:CHECK;RISK:EXTREME;MODE:NORMAL]") == "EXTREME"


def test_roll_grade_has_four_distinct_story_branches():
    assert campaign._roll_grade_from_prompt("итог: 18. Сложность: 12").startswith("сильный успех")
    assert campaign._roll_grade_from_prompt("итог: 12. Сложность: 12").startswith("успех")
    assert campaign._roll_grade_from_prompt("итог: 10. Сложность: 12").startswith("провал с ценой")
    assert campaign._roll_grade_from_prompt("итог: 5. Сложность: 12").startswith("тяжёлый провал")


def test_metadata_tracks_threat_items_npc_memory_and_reputation():
    session = _session()
    text = (
        "Стража рядом.\n"
        "[THREAT:Подозрение стражи;DELTA:2;CAUSE:разбили витрину]\n"
        "[ITEM:ADD;PLAYER:1;NAME:бронзовый ключ;KIND:artifact]\n"
        "[NPC:Капитан Ржа;EVENT:обманули;NOTE:пообещали груз и сбежали]\n"
        "[REP:ADD;PLAYER:1;TEXT:известна как грабительница порта]\n"
        "[ACTION:INPUT]"
    )
    clean, notices = campaign._apply_metadata(session, text)
    assert "THREAT:" not in clean and "ITEM:" not in clean and "NPC:" not in clean and "REP:" not in clean
    assert "[ACTION:INPUT]" in clean
    assert session.threat["level"] == 2
    assert session.inventories["1"] == [{"name": "бронзовый ключ", "kind": "artifact"}]
    assert session.npc_memory["капитан ржа"]["event"] == "обманули"
    assert session.reputations["1"] == ["известна как грабительница порта"]
    assert any("■■□□□□ 2/6" in notice for notice in notices)


def test_campaign_state_roundtrip_keeps_persistent_state_and_profile_choices():
    source = _session()
    campaign._ensure(source)
    source.character_profiles["1"] = campaign._random_profile()
    source.profile_options["2"] = {"style": campaign._parse_profile_options(VALID_PROFILE_OPTIONS)}
    source.inventories["1"] = [{"name": "ключ", "kind": "item"}]
    source.npc_memory["сторож"] = {"name": "Сторож", "event": "помогли", "notes": ["вытащили из ямы"]}
    source.reputations["1"] = ["спаситель двора"]
    source.threat = {"name": "Буря", "level": 3, "max": 6, "history": []}
    source.selected_plot = "Плавучий рынок"
    payload = campaign._state(source)
    restored = _session()
    campaign._restore_state(restored, payload)
    assert restored.character_profiles == source.character_profiles
    assert restored.profile_options == source.profile_options
    assert restored.inventories == source.inventories
    assert restored.npc_memory == source.npc_memory
    assert restored.reputations == source.reputations
    assert restored.threat["level"] == 3
    assert restored.selected_plot == "Плавучий рынок"


def test_delay_advances_existing_threat_after_150_seconds(monkeypatch):
    session = _session()
    campaign._ensure(session)
    session.threat = {"name": "Шум", "level": 1, "max": 6, "history": []}
    session.action_opened_at = 100.0
    monkeypatch.setattr(campaign.time, "time", lambda: 251.0)
    notice = campaign._delay_threat(session)
    assert session.threat["level"] == 2
    assert "2/6" in notice


def test_scene_image_prompt_contains_scene_profiles_and_visual_quality_guards():
    session = _session()
    campaign._ensure(session)
    session.character_profiles["1"] = {
        "style": "курьер запретных реликвий",
        "strength": "видит чужой блеф",
        "weakness": "лезет проверять запретное",
        "special": "аварийный план из кармана",
    }
    prompt = campaign._scene_image_prompt(
        session,
        "Алиса прыгает с люстры на движущийся сейф, пока Боря отбивается зонтом от гусей.",
        style="dark comedy graphic novel",
    )
    assert "Алиса прыгает с люстры" in prompt
    assert "курьер запретных реликвий" in prompt
    assert "dark comedy graphic novel" in prompt
    assert "pale grey-beige pencil sketch" in prompt
    assert "No text" in prompt


def test_dnd_image_uses_shared_gigachat_first_backend(monkeypatch):
    from features import image_generation

    calls = []

    async def fake_generate(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return b"image", "gigachat"

    monkeypatch.setattr(image_generation, "generate_image_bytes", fake_generate)

    class Bot:
        def __init__(self):
            self.photos = []

        async def send_photo(self, chat_id, photo, caption=None):
            self.photos.append((chat_id, photo, caption))

    bot = Bot()
    asyncio.run(campaign._image(bot, 123, "scene prompt", "scene.png", "caption"))

    assert calls == [("scene prompt", {"log_context": "dnd"})]
    assert len(bot.photos) == 1


def test_image_provider_failure_does_not_escape_into_game(monkeypatch):
    from features import image_generation

    async def fail(*_args, **_kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(image_generation, "generate_image_bytes", fail)

    class Bot:
        async def send_photo(self, *_args, **_kwargs):
            raise AssertionError("no photo should be sent")

    asyncio.run(campaign._image(Bot(), 123, "scene prompt", "scene.png", "caption"))


def test_finish_archives_and_cleans_up_before_background_comic(monkeypatch):
    session = _session()
    campaign._ensure(session)
    session.state = "RESOLVING"
    session.selected_plot = "Плавучий рынок"
    session.scene_log = ["Первая сцена", "Вторая сцена"]
    events = []

    async def generate(_session, _prompt):
        return "Все выжили, но теперь им запрещено приближаться к рынкам."

    def cleanup(chat_id):
        events.append(("cleanup", chat_id))

    def start_background(coro, *, name):
        events.append(("background", name))
        coro.close()
        return None

    dnd = SimpleNamespace(
        generate_session_response=generate,
        cleanup_session=cleanup,
        _start_background_task=start_background,
    )
    monkeypatch.setattr(campaign, "_archive_campaign", lambda *_args: events.append(("archive", session.chat_id)))

    class Bot:
        async def send_message(self, chat_id, text):
            events.append(("message", chat_id, text))

    asyncio.run(campaign._finish(dnd, Bot(), session, "Финальная сцена. [ACTION:END]"))

    archive_index = next(i for i, event in enumerate(events) if event[0] == "archive")
    cleanup_index = next(i for i, event in enumerate(events) if event[0] == "cleanup")
    background_index = next(i for i, event in enumerate(events) if event[0] == "background")
    end_message_index = next(i for i, event in enumerate(events) if event[0] == "message" and "Егра окончена" in event[2])
    assert archive_index < cleanup_index < end_message_index < background_index
