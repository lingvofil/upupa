"""Every tag the DnD code parses must be taught by the contract the provider really receives.

Regression: the GROWTH tag existed only in the long ``DND_SYSTEM_PROMPT``,
which the live generation path never sends, so hero growth never triggered.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]

# Display scrubbers may mention a tag head without the code acting on it.
SCRUB_ONLY_HEADS = {"META"}

_SCRIPT = textwrap.dedent(r'''
    import json, pathlib, re, sys
    from tests import test_smoke_imports  # noqa: F401  (stubs heavy optional deps)
    from AI import dnd
    from AI.dnd_runtime import configure_dnd_runtime
    from AI.dnd_style import configure_dnd_style

    dnd.ALLOWED_CHAT_ID = -9001
    dnd.DND_STATE_PATH = pathlib.Path(sys.argv[1]) / "state.json"
    configure_dnd_runtime()
    configure_dnd_style()

    from AI import dnd_scene_rules as rules
    from AI.dnd_generation_resilience import DND_GEMINI_SYSTEM_MAX_CHARS, build_compact_system
    from AI.dnd_prompt_registry import all_sections
    from AI.dnd_turn_contract import TURN_CONTRACT

    corpus = "\n".join([TURN_CONTRACT, rules.RULES_PROTOCOL, rules.ENEMY_RULES_PROTOCOL,
                        rules.WAIT_RULES_PROTOCOL] + [row.text for row in all_sections()])
    heads, subs, blocks = set(), set(), set()
    for path in sorted(pathlib.Path("AI").glob("dnd*.py")):
        source = path.read_text("utf-8")
        for literal in re.findall(r'r"([^"\n]*)"', source):
            for group in re.findall(r'\\\[\(?(?:\?:)?([A-Z_|]{3,})\)?:', literal):
                heads.update(group.split("|"))
            for group in re.findall(r'ACTION:\(?(?:\?:)?([A-Z_|]{3,})', literal):
                subs.update(group.split("|"))
            blocks.update(re.findall(r'<(DND_[A-Z_]+)>', literal))

    session = dnd.GameSession(-9001, "Ведущий", starter_user_id=1)
    session.mode = "participants"
    session.participants = {"1": {"user_id": 1, "name": "Алина", "active": True}}
    opening = build_compact_system(session, "")
    worst = dnd.GameSession(-9001, "Ведущий", starter_user_id=1)
    worst.mode = "participants"
    worst.participants = session.participants
    worst.enemy_combatants = {"огр": {"name": "Огр", "hp": 20, "status": "alive"}}
    worst.inventories = {"1": [{"name": "ключ"}]}
    worst.scene_clocks = {"alarm": {"value": 1, "max": 4}}
    worst.conditions = {"1": [{"name": "ранен"}]}
    worst.scene_objects = {"cart": {"name": "телега", "available": True}}
    worst.achievement_world_facts = [{"kind": "PARLEY", "text": "переговоры"}]
    worst.mission_goal = "Ц" * 350
    mandatory = build_compact_system(worst, "бой предмет слабость шкала", include_optional=False)
    inactive = [row.name for row in all_sections() if not row.optional and not row.active(worst, "бой предмет слабость шкала")]
    print(json.dumps({
        "worst_mandatory_len": len(mandatory),
        "gemini_ceiling": DND_GEMINI_SYSTEM_MAX_CHARS,
        "worst_inactive": inactive,
        "heads": sorted(heads), "subs": sorted(subs), "blocks": sorted(blocks),
        "missing_heads": sorted(h for h in heads if f"[{h}:" not in corpus),
        "missing_subs": sorted(s for s in subs if f"ACTION:{s}" not in corpus),
        "missing_blocks": sorted(b for b in blocks if f"<{b}>" not in corpus),
        "opening_has_growth": "[GROWTH:ADD" in opening,
        "opening_len": len(opening),
    }, ensure_ascii=False))
''')


def _probe(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_every_parsed_tag_is_taught_by_the_live_contract(tmp_path):
    data = _probe(tmp_path)
    # Guard against the scanner silently finding nothing.
    assert {"ACTION", "ITEM", "GROWTH", "CLOCK", "POSITION"} <= set(data["heads"])
    assert {"ROLL", "POLL", "INPUT", "PLAYER_ATTACK"} <= set(data["subs"])
    assert set(data["missing_heads"]) - SCRUB_ONLY_HEADS == set()
    assert data["missing_subs"] == []
    assert data["missing_blocks"] == []


def test_worst_case_mandatory_contract_fits_gemini_system_ceiling(tmp_path):
    data = _probe(tmp_path)
    # Every mandatory section is active at once in the probe.
    assert data["worst_inactive"] == []
    # Ceiling minus the up-to-256-char opening reply, plus 500 chars kept free
    # so the next mandatory section cannot silently start refusing turns.
    assert data["worst_mandatory_len"] <= data["gemini_ceiling"] - 256 - 500


def test_growth_tag_reaches_participant_opening_within_gemini_budget(tmp_path):
    data = _probe(tmp_path)
    assert data["opening_has_growth"]
    assert data["opening_len"] <= data["gemini_ceiling"]
