# DnD Memory v2 — план работ

## Цель

Убрать зависимость игрового состояния от того, что модель "помнит" из диалога, и сделать продолжение партии воспроизводимым после ошибок провайдера, рестартов и длинных кампаний.

Базовый принцип:

> conversation отвечает за литературную связность; код и persisted state отвечают за факты мира.

## Ограничения

- Не переписывать DnD одним большим PR.
- Не ломать уже работающий durable provider recovery.
- Каждая стадия должна сохранять обратную совместимость со старыми сохранёнными партиями.
- Новые механики кампании добавлять только после стабилизации памяти.
- Каждый этап закрывается regression-тестами на реальные классы прежних ошибок.

## Этап 1. Один реестр persisted state

Статус: **готово в PR #724**.

- Все расширения живой DnD-сессии регистрируют persisted-поля через `DndCampaignStatePolicy`.
- Убрать прямые обёртки `campaign._state/_ensure/_restore_state` там, где они используются только для persistence.
- Сделать восстановление зарегистрированных полей явной обязанностью policy, а не побочным эффектом динамических monkey-patch цепочек.
- Добавить тест, что extension field восстанавливается даже если downstream restore ничего о нём не знает.

Первый перенос: combat state (`character_sheets`, `healing_charge`) и post-restore синхронизация artifact stats.

## Этап 2. Инвентаризация и инварианты состояния

Статус: **готово в PR #725**.


- Составить полный список canonical/runtime/derived полей.
- Для каждого поля зафиксировать: владелец, источник истины, persist/restore, reset rules, archive rules.
- Добавить валидатор инвариантов после restore и перед persist.
- Первые инварианты:
  - один герой — одна физическая позиция;
  - HP/status не противоречат друг другу;
  - pending roll/poll/action согласованы с `session.state`;
  - TARGETS принадлежат текущим участникам;
  - уникальный артефакт не принадлежит двум героям;
  - pending generation/result относятся к текущей сессии.

## Этап 3. State revision + event journal

Статус: **готово в PR #727**.

- Ввести устойчивый `campaign_id` и монотонный `state_revision`.
- Фиксировать структурированные изменения мира как компактные события с `event_id`, `campaign_id`, `revision`, `sequence`.
- Первая версия автоматически журналирует изменения позиций, инвентаря/передач, HP/status героев и врагов, NPC memory, conditions, reputation, threat и scene clocks.
- Один durable persist с несколькими изменениями создаёт одну новую revision и несколько событий внутри неё.
- Journal bounded: хранится последние 200 событий; canonical state остаётся источником истины.
- Quest/thread события появятся вместе с самой структурированной сущностью quest/thread, а не раньше неё.
- События нужны для диагностики и будущего replay, но не должны дублировать художественную историю.

## Этап 4. Context builder

Статус: **готово в PR #729**.

- Перестать полагаться на длинную `conversation` как на память мира.
- На каждый основной AI-вызов собирать bounded context из:
  - текущей сцены;
  - canonical party state;
  - релевантных NPC/обязательств;
  - активных нитей;
  - последних структурированных событий;
  - последних 2–3 художественных сцен.
- Structured state в текущем запросе помечается как авторитетный относительно старого narrative history.
- Gemini и Groq на каждом основном ходе получают только system contract + короткое окно последних реплик + текущий запрос с Memory v2.
- Старая история остаётся durable архивом, но больше не отправляется провайдеру целиком даже когда формально помещается в контекст.

## Этап 5. Транзакционный turn pipeline

Статус: **готово в PR #731**.

Целевая схема:

```
player actions
  -> generation request
  -> model result
  -> parsed structured events
  -> validate
  -> apply
  -> persist revision N+1
  -> Telegram effects
```

- Сохранить нынешний durable provider/result recovery.
- Каждый `pending_generation_request` и `pending_generated_result` привязать к `source_campaign_id/source_revision`.
- До provider call, после provider call и перед apply проверять identity; поздний ответ старой revision/campaign отбрасывать без parse.
- Во время durable parse промежуточные persist не создают отдельные event-journal revisions; успешный логический ход открывает один commit boundary.
- Crash в APPLYING восстанавливает `pre_apply_snapshot` и replay-ит тот же exact result с идемпотентными Telegram effects.
- Явный successor (`transition_to_generation_request`) получает prospective revision родительского canonical commit.

## Этап 6. Regression suite на реальные поломки

Статус: **готово в PR #732**.

Обязательные сценарии:

- герой остаётся в ранее зафиксированной позиции через много ходов;
- все действия группового хода учитываются;
- предмет не возвращается после использования/передачи;
- пересборка героя не теряет допустимый инвентарь;
- HP и художественная смерть не расходятся;
- NPC/обещание переживает рестарт;
- restart в INPUT / ROLL / POLL / RESOLVING;
- обе модели недоступны -> ход сохраняется -> `дальше` продолжает его без повторного броска/действия;
- поздний ответ предыдущей кампании ничего не меняет.

## После Memory v2

Memory v2 закрыта. Новые функции из офлайн-DnD можно наслаивать поверх structured state:

- **SESSION CANON — готово в PR #733:** детерминированный снимок только структурированно подтверждённых фактов, команда `днд канон`, сохранение снимка в архив завершённой партии и legacy fallback для старых архивов;
- **DM adjudication discipline — в работе в текущем stacked PR:** не бросать на очевидный успех/невозможность/безрисковый retry, разделять CHECK/SAVE/ATTACK, сначала выбирать характеристику по подходу и только потом навык;
- player/character knowledge и секреты;
- квесты/threads/milestones;
- партийная казна;
- RAW/RULING/HOUSE RULE journal;
- более богатые PC↔PC relationships;
- DM companion / настольный режим.

### Карта regression suite PR #732

- `test_fixed_position_survives_many_unrelated_turns_and_stays_authoritative` — позиция через длинную серию ходов;
- `test_group_turn_keeps_every_action_across_provider_outage_and_retry` — все заявки группового хода + exact retry;
- `test_transferred_item_does_not_return_after_many_later_turns` — предмет после передачи не возвращается из старого narrative;
- `test_lobby_character_rebuild_keeps_existing_inventory_and_heritage` — пересборка не стирает допустимые данные героя;
- `test_hp_and_death_state_remain_authoritative_over_old_narrative` — HP/status против старого художественного текста;
- `test_structured_npc_promise_survives_state_roundtrip_and_returns_to_context` — NPC/обещание после restore;
- `test_restart_roundtrip_keeps_input_roll_poll_and_resolving_payloads` — INPUT / ROLL / POLL / RESOLVING;
- `test_both_providers_down_keeps_exact_turn_and_retry_does_not_repeat_effect` — оба провайдера недоступны, затем безопасный retry;
- `test_late_result_from_previous_campaign_cannot_mutate_new_campaign` — поздний ответ прошлой кампании.
