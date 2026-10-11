"""Tag schemas outrank pacing text in the compact system block of the real composition.

Regression: in the production composition the compact block sat 83 characters
under its 10k cap. Any growth elsewhere silently dropped <DND_ENEMIES> (the only
place the model learns the simple-enemy format), because the duplicated pacing
text was appended first.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = textwrap.dedent(r'''
    import json, pathlib, sys
    from types import SimpleNamespace
    from tests import test_smoke_imports  # noqa: F401
    from AI import dnd
    from AI.dnd_runtime import configure_dnd_runtime
    from AI.dnd_style import configure_dnd_style

    dnd.DND_STATE_PATH = pathlib.Path(sys.argv[1]) / "state.json"
    configure_dnd_runtime()
    configure_dnd_style()
    from AI.dnd_local_runtime import configure_dnd_local_runtime
    configure_dnd_local_runtime()

    import AI.dnd_settings as settings
    settings.settings_for = lambda session=None: dict(settings.DEFAULTS, monsters=3, poll=4)
    from AI.dnd_generation_resilience import DND_COMPACT_SYSTEM_MAX_CHARS, build_compact_system
    from tests.test_dnd_ai_budget import _session

    session = _session(enemy_combatants={"огр": {"hp": 0, "status": "dead"}, "тролль": {"hp": 20}},
                       inventories={"1": [{}]}, conditions={"1": [{}]}, scene_clocks={"alarm": {}},
                       scene_objects={"cart": {"available": True}}, scene_count=3, spotlight_decisions_since_poll=9)
    system = build_compact_system(session, "бой предмет слабость шкала")
    print(json.dumps({"len": len(system), "limit": DND_COMPACT_SYSTEM_MAX_CHARS,
                      "enemies": "<DND_ENEMIES>" in system, "rules": "<DND_RULES>" in system,
                      "wait": "DND_WAIT" in system, "notes": "зарегистрировано противников" in system}))
''')


def test_tag_schemas_survive_full_composition_and_turn_notes_remain(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, "-c", _SCRIPT, str(tmp_path)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-3000:]
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["len"] <= data["limit"]
    assert data["enemies"] and data["rules"] and data["wait"], data
    assert data["notes"], data
