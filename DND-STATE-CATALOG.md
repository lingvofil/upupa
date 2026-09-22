# DnD state catalog

Этот файл фиксирует текущее распределение памяти DnD перед дальнейшей работой над Memory v2.

Главное правило классификации:

- **canonical** — игровой факт; потеря или расхождение меняет смысл мира;
- **runtime** — координация незавершённого Telegram/AI-хода;
- **derived** — кэш, представление или данные, которые можно пересчитать из canonical state;
- **narrative** — текстовая история для связности; не должна быть единственным источником игрового факта.

## Core GameSession

| Поля | Класс | Владелец | Правило |
|---|---|---|---|
| `chat_id`, `mode`, `participants`, `starter_*` | canonical/runtime identity | `AI/dnd.py` | сохраняются в основном record |
| `state` | runtime | `AI/dnd.py` | определяет текущую фазу автомата |
| `conversation` | narrative | `AI/dnd.py` | durable transcript; не источник истины мира |
| `mode_prompt_message_id`, `lobby_message_id`, `backstory_prompt_message_id` | runtime | `AI/dnd.py` | Telegram recovery |
| `pending_roll`, `last_roll_stat` | runtime | `AI/dnd.py` + combat extensions | должны быть согласованы с `WAITING_ROLL` |
| `current_poll_id`, `pending_poll`, `last_resolved_poll` | runtime | `AI/dnd.py` | active poll / durable resolved choice |
| `action_prompt_message_id`, `pending_actions`, `action_deadline`, `action_target_user_ids` | runtime | `AI/dnd.py` | group/targeted action collection |
| `recent_scene_types` | derived/narrative pacing | `AI/dnd.py` | bounded anti-repeat helper |

## Base campaign state

Владелец: `AI/dnd_campaign.py`.

| Поля | Класс |
|---|---|
| `character_profiles` | canonical |
| `heritage` | canonical |
| `inventories` | canonical |
| `npc_memory` | canonical |
| `reputations` | canonical |
| `threat` | canonical |
| `selected_plot`, `continuation_mode` | canonical campaign identity |
| `scene_count` | canonical counter |
| `scene_log` | narrative/diagnostic |
| `social_relationships` | imported context, not hard truth |
| `profile_options`, `plot_options` | runtime UI choices |
| `next_illustration_at` | derived scheduling |
| `action_opened_at` | runtime |
| `campaign_started_at` | canonical metadata |

## Registered extension state

Все поля ниже должны проходить через один `DndCampaignStatePolicy`.

| Модуль | Поля | Класс |
|---|---|---|
| `dnd_combat` | `character_sheets`, `healing_charge` | canonical |
| `dnd_healing_choice` | `pending_heal_decision` | runtime |
| `dnd_two_heals` | `healing_charges` | canonical/runtime resource state |
| `dnd_inventory_fun` | `artifact_awards` | canonical bookkeeping |
| `dnd_inventory_effects` | `inventory_effects_version` | derived migration marker |
| `dnd_player_combat` | `enemy_combatants` | canonical |
| `dnd_player_positions` | `player_positions` | canonical |
| `dnd_conditions` | `conditions` | canonical |
| `dnd_scene_clocks` | `scene_clocks` | canonical |
| `dnd_special_moves` | `special_move_charges` | canonical |
| `dnd_special_moves` | `special_move_pending_user_id`, `special_move_offer_user_id` | runtime |
| `dnd_weakness_luck` | `luck_tokens`, `weakness_luck_earned`, `weakness_luck_spent` | canonical resources |
| `dnd_weakness_luck` | `pending_weakness_invocations` | runtime |
| `dnd_growth` | `growth_counts`, `growth_evidence`, `growth_seen_scene_keys` | canonical progression evidence |
| `dnd_growth` | `learned_achievements` | canonical |
| `dnd_growth` | `pending_achievement_uses`, `achievement_boosts`, `growth_expected_actor_ids` | runtime |
| `dnd_growth` | `achievement_world_facts` | canonical temporary world facts |
| `dnd_world_memory` | `world_callback_candidate`, `world_inherited_npc_keys`, `world_callback_used` | canonical/runtime cross-campaign memory |
| `dnd_spotlight` | `spotlight_order`, `spotlight_cursor`, `spotlight_individual_streak`, `spotlight_decisions_since_poll`, `spotlight_last_player` | runtime pacing |
| `dnd_result_recovery` | `pending_generation_request`, `pending_generated_result`, `generated_result_seq` | runtime durable outbox |
| `dnd_event_journal` | `campaign_id`, `state_revision` | canonical identity/version metadata |
| `dnd_event_journal` | `event_journal` | bounded diagnostic/replay journal derived from canonical state changes |

## Derived state that must not become a second truth

- artifact-adjusted effective stats are derived from base character stats + owned artifacts;
- human-readable inventory descriptions/effects are presentation around inventory records;
- prompt context is built from state and must never be written back as an independent truth;
- `conversation` and `scene_log` may describe facts, but mechanics must read structured fields when such a field exists.

## Restore/reset rules

1. `GameSession.from_record` restores core runtime state.
2. `dnd_campaign` restores campaign state through `DndCampaignStatePolicy`.
3. Registered extension fields are restored by the policy before restore hooks run.
4. Restore hooks may normalize/migrate derived representations, but should not invent new canonical events.
5. A new campaign must explicitly decide which canonical fields inherit; absence of a reset rule is considered a memory bug.
6. Provider/result recovery state must survive restart until the exact pending turn is either applied or deliberately superseded.

## Runtime invariants introduced in stage 2

`AI/dnd_state_invariants.py` checks the following at restore and persist boundaries without mutating the campaign:

- state name is known;
- `WAITING_ROLL/POLL/HEAL` has the matching pending payload;
- incompatible pending payloads do not silently survive in another state;
- TARGETS and pending action actors belong to current participants;
- player positions and conditions do not silently belong to absent players;
- HP/max HP/status are mutually consistent;
- a unique artifact is not owned by multiple heroes or stacked;
- durable generation request and durable generated result are not active simultaneously;
- an APPLYING generated result has a pre-apply snapshot;
- campaign/event identity is consistent: event IDs are unique, revisions are ordered and no event belongs to another campaign or a future revision.

На этом этапе нарушение логируется, но не чинится автоматически. Это намеренно: сначала собираем реальные нарушения на существующих партиях, затем для каждого класса выбираем безопасную recovery-policy вместо скрытого удаления данных.


## Stage 3: revision/event journal boundaries

`AI/dnd_event_journal.py` observes canonical state only at the durable persist boundary.

- The first persist after installation establishes a baseline and does **not** invent history for older changes.
- Any later canonical delta increments `state_revision` exactly once for that persisted snapshot.
- Multiple changes in one snapshot share the same revision and receive ordered `sequence` values.
- The journal currently records positions, inventory add/remove/transfer, player/enemy HP and status, NPC memory, conditions, reputations, threat and scene clocks.
- `event_journal` stores only the latest 200 events. Dropping old journal entries never changes canonical game state.
- Narrative text, prompt context and Telegram runtime fields are deliberately excluded from revision changes.


## Stage 4: bounded provider context

`AI/dnd_context_builder.py` rebuilds the current model memory from structured state on every main generation.

Priority order:

1. authoritative current hero state: positions, HP/status, conditions, inventory and compact progression/resources;
2. active enemies and scene clocks/threat;
3. relevant NPC memory and open obligations;
4. latest structured `event_journal` entries;
5. only the latest three narrative scenes for literary continuity;
6. a small tail of legacy dynamic mechanics context for compatibility.

The resulting Memory v2 block is capped at 6500 characters. Durable `conversation` remains stored as an audit/recovery transcript, but provider calls are always windowed: system contract + a few latest exchanges + the current request. Old narrative history is therefore no longer a second implicit source of world truth.
