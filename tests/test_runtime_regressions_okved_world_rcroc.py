import asyncio

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_okved_csv_accepts_utf8_and_legacy_cp1251():
    from AI.profession import _extract_okved_descriptions

    utf8 = '01.11;Выращивание зерновых\n01.12;Выращивание риса\n'.encode('utf-8-sig')
    cp1251 = '01.11;Выращивание зерновых\n01.12;Выращивание риса\n'.encode('cp1251')

    assert _extract_okved_descriptions(utf8) == [
        'Выращивание зерновых',
        'Выращивание риса',
    ]
    assert _extract_okved_descriptions(cp1251) == [
        'Выращивание зерновых',
        'Выращивание риса',
    ]


def test_okved_csv_uses_real_csv_parser_for_quoted_semicolons():
    from AI.profession import _extract_okved_descriptions

    payload = '01.11;"Выращивание кофе; чая"\n'.encode('utf-8')
    assert _extract_okved_descriptions(payload) == ['Выращивание кофе; чая']


def test_every_world_main_menu_exposes_sanctions_and_court():
    import handlers
    from features.world.hub_ui import build_world_main_markup
    from handlers import world_expansion, world_hub, world_interactions

    # handlers.__init__ intentionally installs one canonical menu into all
    # historical hub routers because world_interactions matches first.
    assert world_interactions._main_markup is build_world_main_markup
    assert world_expansion._main_markup is build_world_main_markup
    assert world_hub._main_markup is build_world_main_markup

    markup = world_interactions._main_markup()
    texts = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert '🚫 Санкции' in texts
    assert '⚖️ Международный суд' in texts
    assert '🎩 Назначить посла' in texts
    assert 'worldhub:sanctions' in callbacks
    assert 'worldhub:court' in callbacks
    assert handlers.ROUTERS.index(world_interactions.router) < handlers.ROUTERS.index(world_expansion.router)


def test_reverse_crocodile_retries_visual_clue_that_leaks_answer(monkeypatch):
    import AI.summarize as summarize
    import games.reverse_crocodile as reverse

    responses = iter([
        'Нарисовать большого кота рядом с миской.',
        'Пушистое домашнее животное с усами гоняется за клубком на полу.',
    ])

    async def fake_generate(_prompt, _chat_id, **_kwargs):
        return next(responses)

    monkeypatch.setattr(summarize, '_generate_with_active_model', fake_generate)
    clue = asyncio.run(reverse._build_visual_clue('кот', '-100'))

    assert clue == 'Пушистое домашнее животное с усами гоняется за клубком на полу.'
    assert not reverse._clue_leaks_secret(clue, 'кот')


def test_reverse_crocodile_image_provider_never_sees_literal_answer(monkeypatch):
    import AI.gigachat_image as gigachat_image
    import games.reverse_crocodile as reverse

    captured = {}

    async def fake_clue(_word, _chat_id):
        return 'Пушистое животное с усами пытается поймать клубок на полу.'

    async def fake_image(prompt):
        captured['prompt'] = prompt
        return b'image'

    monkeypatch.setattr(reverse, '_build_visual_clue', fake_clue)
    monkeypatch.setattr(gigachat_image, 'generate_gigachat_image', fake_image)

    result = asyncio.run(reverse._generate_word_image('кот', '-100'))

    assert result == b'image'
    assert 'кот' not in captured['prompt'].casefold()
    assert 'никакого читаемого текста' in captured['prompt'].casefold()
