"""The response-guard pipeline: explicit order, one wrapper, same semantics as the old onion."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401

from AI import dnd_turn_pipeline as pipeline

ROOT = Path(__file__).resolve().parents[1]


def test_steps_run_outer_to_inner_and_unwind_in_reverse():
    calls = []

    async def inner(bot, chat_id, response):
        calls.append(("inner", response))
        return "done"

    fake = SimpleNamespace(parse_and_execute_turn=inner)

    def step(name):
        async def run(bot, chat_id, response, call_next):
            calls.append(("in", name))
            result = await call_next(bot, chat_id, response + name)
            calls.append(("out", name))
            return result
        return run

    pipeline.register_turn_step(fake, "inner_guard", step("b"), order=10)
    pipeline.register_turn_step(fake, "outer_guard", step("a"), order=20)
    assert asyncio.run(fake.parse_and_execute_turn(None, 1, "x")) == "done"
    assert calls == [("in", "a"), ("in", "b"), ("inner", "xab"), ("out", "b"), ("out", "a")]
    assert pipeline.describe(fake) == ["outer_guard", "inner_guard"]


def test_reregistering_a_step_replaces_it_and_wrapper_is_installed_once():
    async def inner(bot, chat_id, response):
        return response

    fake = SimpleNamespace(parse_and_execute_turn=inner)

    async def first(bot, chat_id, response, call_next):
        return await call_next(bot, chat_id, response + "1")

    async def second(bot, chat_id, response, call_next):
        return await call_next(bot, chat_id, response + "2")

    pipeline.register_turn_step(fake, "same", first, order=5)
    wrapper = fake.parse_and_execute_turn
    pipeline.register_turn_step(fake, "same", second, order=5)
    assert fake.parse_and_execute_turn is wrapper
    assert asyncio.run(fake.parse_and_execute_turn(None, 1, "")) == "2"


_SCRIPT = textwrap.dedent(r'''
    import json, pathlib, sys
    from tests import test_smoke_imports  # noqa: F401
    from AI import dnd
    dnd.DND_STATE_PATH = pathlib.Path(sys.argv[1]) / "state.json"
    from AI.dnd_runtime import configure_dnd_runtime; configure_dnd_runtime()
    from AI.dnd_style import configure_dnd_style; configure_dnd_style()
    from AI.dnd_player_agency import configure_dnd_player_agency; configure_dnd_player_agency()
    from AI.dnd_target_mentions import configure_dnd_target_mentions; configure_dnd_target_mentions()
    from AI.dnd_result_recovery import configure_dnd_result_recovery; configure_dnd_result_recovery()
    from AI.dnd_local_runtime import configure_dnd_local_runtime; configure_dnd_local_runtime()
    from AI import dnd_turn_pipeline as pipeline

    # Walk the remaining wrapper chain from the outside in via closures.
    chain, fn, seen = [], dnd.parse_and_execute_turn, set()
    while fn is not None and id(fn) not in seen:
        seen.add(id(fn))
        module = getattr(fn, "__module__", "?").rsplit(".", 1)[-1]
        chain.append("PIPELINE" if getattr(fn, "_upupa_turn_pipeline", False) else module)
        names = getattr(getattr(fn, "__code__", None), "co_freevars", ())
        candidates = list(zip(names, [cell.cell_contents for cell in (getattr(fn, "__closure__", None) or ())]))
        candidates += list((getattr(fn, "__kwdefaults__", None) or {}).items())  # dnd_style: __original=...
        fn = next((value for name, value in candidates
                   if name.lstrip("_").startswith(("original", "inner")) and callable(value)
                   and getattr(value, "__module__", "").startswith("AI.")), None)
    print(json.dumps({"steps": pipeline.describe(dnd), "chain": chain}))
''')


def test_production_composition_pins_the_pipeline_order_and_position(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, "-c", _SCRIPT, str(tmp_path)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-3000:]
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["steps"] == ["poll_agency", "spotlight", "target_mentions", "growth", "item_actions",
                             "roll_ability_display", "weakness_luck", "special_moves", "scene_tactics", "conditions"]
    chain = data["chain"]
    assert chain.count("PIPELINE") == 1
    # The guards left the onion; the pipeline sits right outside the
    # interceptors that may answer the turn themselves.
    for module in ("dnd_poll_agency", "dnd_spotlight", "dnd_target_mentions", "dnd_growth", "dnd_item_actions",
                   "dnd_roll_ability_display", "dnd_weakness_luck", "dnd_special_moves", "dnd_scene_tactics",
                   "dnd_conditions", "dnd_two_heals", "dnd_healing_choice", "dnd_manual_healing"):
        assert module not in chain, chain
    position = chain.index("PIPELINE")
    assert chain[position - 1] == "dnd_pacing" and chain[position + 1] == "dnd_cinematic_combat", chain
    # The whole remaining onion, outer → inner. A new parser wrapper must be
    # added here deliberately (or, better, become a pipeline step).
    assert chain == ["dnd_local_runtime", "dnd_result_recovery", "dnd_style", "dnd_settings", "dnd_group_progress",
                     "dnd_pacing", "PIPELINE", "dnd_cinematic_combat", "dnd_player_combat",
                     "dnd_unknown_action_recovery", "dnd_combat", "dnd_profile_gender_grounding", "dnd_campaign", "dnd"]
