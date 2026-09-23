"""Exercise the production wrapper order in an isolated interpreter."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_composed_personal_turn_keeps_actor_inventory_and_complete_narrative(tmp_path):
    script = textwrap.dedent(r'''
        import asyncio
        from pathlib import Path
        from types import SimpleNamespace
        from tests import test_smoke_imports
        from AI import dnd, dnd_campaign as campaign, dnd_generation_resilience as generation
        from AI.dnd_runtime import configure_dnd_runtime
        from AI.dnd_style import configure_dnd_style
        from AI.dnd_result_recovery import configure_dnd_result_recovery

        dnd.DND_STATE_PATH = Path(__import__('sys').argv[1]) / 'state.json'
        dnd.get_active_model = lambda _: 'groq'
        dnd._start_background_task = lambda coro, **kwargs: coro.close()
        configure_dnd_runtime()
        configure_dnd_style()
        configure_dnd_result_recovery()

        prompts = []
        narrative = ('Алина обыскивает стол и находит тайник. ' +
                     'В комнате тихо, за стеной слышны голоса стражников. ' * 28 +
                     'В тайнике лежит ключ; Алина отмечает его положение и оставляет на месте. ' +
                     'Детектор, что ты делаешь? [ACTION:INPUT;TARGETS:2]')
        async def provider(session, prompt):
            prompts.append(prompt)
            return narrative
        async def auxiliary(*args, **kwargs):
            return None
        generation._run_groq_fallback = provider
        generation.generate_auxiliary_text = auxiliary
        value = dnd.GameSession(-9001, 'Ведущий', starter_user_id=1)
        value.mode = 'participants'
        value.state = 'WAITING_ACTION'
        value.participants = {'1': {'user_id': 1, 'name': 'Алина'}, '2': {'user_id': 2, 'name': 'Детектор'}}
        campaign._ensure(value)
        value.adventure_length = 'long'
        value.mission_goal = 'Найти пропавшего кузнеца'
        value.action_prompt_message_id = 91
        value.action_target_user_ids = [1]
        value.pending_actions = {'1': {'user_id': 1, 'name': 'Алина', 'action': 'Обыскать стол'}}
        value.next_illustration_at = 99
        dnd.dnd_sessions[value.chat_id] = value
        sent = []
        class Bot:
            async def send_message(self, chat_id, text, **kwargs):
                sent.append(text)
                return SimpleNamespace(message_id=100 + len(sent))
        asyncio.run(dnd.finalize_group_actions(Bot(), value.chat_id, 91))
        assert value.state == 'WAITING_ACTION', value.state
        assert value.action_target_user_ids == [2], value.action_target_user_ids
        assert any('Личный ход' in text and 'Обыскать стол' in text for text in sent), sent
        assert any('В тайнике лежит ключ' in text for text in sent), sent
        assert any('Алина: Обыскать стол (id=1)' in prompt for prompt in prompts), prompts
        assert any('Найти пропавшего кузнеца' in prompt and '24–36' in prompt for prompt in prompts)
        restored = dnd.GameSession.from_record(value.to_record())
        assert restored.mission_goal == value.mission_goal
        assert restored.adventure_length == 'long'
        assert not getattr(value, 'pending_generation_request', {})
        value.pending_generated_result = {'id': 'restart-test', 'text': narrative, 'phase': 'READY'}
        dnd.persist_dnd_sessions()
        scheduled = []
        def schedule(coro, **kwargs):
            scheduled.append(kwargs['name'])
            coro.close()
        dnd._start_background_task = schedule
        assert dnd.restore_dnd_sessions(Bot()) == 1
        assert any(name.startswith('dnd-result-replay:-9001:') for name in scheduled), scheduled
        assert not any(name.startswith('dnd-poll:') for name in scheduled), scheduled
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
