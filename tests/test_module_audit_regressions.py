"""Regression checks for the module audit of 2026-10-08; no external APIs."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import random
import runpy
import sqlite3
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tests import test_smoke_imports

del test_smoke_imports


async def _wait(event):
    await asyncio.wait_for(event.wait(), timeout=5)


def test_partial_private_config_retains_token():
    private = types.ModuleType('config_private')
    private.API_TOKEN = '123456789:AAFakeTokenForAuditOnly_abcdefgh'
    with patch.dict(sys.modules, {'config_private': private}), patch.dict(os.environ, {}, clear=True):
        settings = runpy.run_path('core/settings.py')
    assert settings['API_TOKEN'] == private.API_TOKEN, 'Partial config_private discards the provided API_TOKEN'


def test_quiz_stop_during_generation_prevents_publication(monkeypatch):
    from AI import quiz
    async def scenario():
        monkeypatch.setattr(quiz, 'quiz_states', {})
        monkeypatch.setattr(quiz, 'quiz_questions', {})
        monkeypatch.setattr(quiz, '_quiz_generations', {})
        entered, release = asyncio.Event(), asyncio.Event()
        async def generate(*args):
            entered.set()
            await release.wait()
            return [{'text': 'Q', 'options': ['A','B'], 'correct_answer': 'A'}]
        monkeypatch.setattr(quiz, 'extract_messages', AsyncMock(return_value=[{'text':'Hello'}]))
        monkeypatch.setattr(quiz, 'generate_quiz_with_gemini', generate)
        bot = SimpleNamespace(send_poll=AsyncMock(return_value=SimpleNamespace(message_id=1, poll=SimpleNamespace(id='new'))), stop_poll=AsyncMock())
        task=asyncio.create_task(quiz.process_quiz_start(SimpleNamespace(chat=SimpleNamespace(id=-321)), bot))
        await _wait(entered)
        assert await quiz.stop_quiz(bot,-321)
        release.set()
        await task
        assert bot.send_poll.await_count == 0, 'Quiz published after stop during generation'
    asyncio.run(scenario())


@pytest.mark.parametrize("reflection", ["Reflection", None])
def test_channel_poll_registration_survives_reflection(monkeypatch,tmp_path,reflection):
    from features.channel import polls
    from features.channel import storage
    monkeypatch.setattr(polls,'POLL_STATE_FILE',tmp_path/'polls.json')
    monkeypatch.setattr(storage,'POSTS_FILE',tmp_path/'posts.json')
    now=datetime.now(timezone.utc)
    polls._write_state({'polls':[{'status':'awaiting_reflection','poll_id':'old','message_id':1,'reflection_due_at':(now-timedelta(minutes=1)).isoformat(),'question':'Q','options':['A','B']}], 'next_eligible_post_count':0})
    async def scenario():
        monkeypatch.setattr(polls,'_state_lock',asyncio.Lock())
        entered, release=asyncio.Event(),asyncio.Event()
        async def reflect(record):
            entered.set(); await release.wait(); return reflection
        monkeypatch.setattr(polls,'_generate_reflection',reflect)
        bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=3)))
        task=asyncio.create_task(polls.process_due_polls(bot,channel_target='fake',now=now))
        await _wait(entered)
        await polls.register_published_poll(SimpleNamespace(message_id=2,poll=SimpleNamespace(id='new')),plan={'question':'New','options':['A','B']},source='manual',published_count_before=100)
        assert 'new' in [x['poll_id'] for x in polls._read_state()['polls']]
        cooldown = polls._read_state()['next_eligible_post_count']
        release.set(); await task
        assert polls._read_state()['next_eligible_post_count'] == cooldown
        assert 'new' in [x['poll_id'] for x in polls._read_state()['polls']], 'Reflection overwrote registered poll'
    asyncio.run(scenario())


def test_world_visit_feedback_prompt_is_retried(tmp_path):
    from features.world import interactions, visit_feedback
    from features.world.service import WorldService
    from infrastructure.persistence.sqlite_world import SQLiteWorldRepository
    repo=SQLiteWorldRepository(tmp_path/'world.db'); repo.init_schema()
    service=WorldService(repo)
    async def scenario():
        host=await service.enable_state(-1001,'Host'); guest=await service.enable_state(-1002,'Guest')
        service.ledger.record_event('state_visit_accepted',actor_state=guest.world_id,target_state=host.world_id,payload={},created_at=datetime.now(timezone.utc)-timedelta(hours=25))
        class Bot:
            count=0
            async def send_message(self,*args,**kwargs):
                self.count+=1
                if self.count==3: raise RuntimeError('temporary Telegram failure')
                return SimpleNamespace(message_id=self.count)
        bot=Bot()
        first=await interactions.expire_due_visits(bot,service)
        restarted = WorldService(SQLiteWorldRepository(tmp_path/'world.db'))
        second=await interactions.expire_due_visits(bot,restarted)
        assert bot.count == 4
        assert service.ledger.pending_visit_deliveries() == []
        await interactions.expire_due_visits(bot,restarted)
        assert bot.count == 4
        assert first==1
        windows=await visit_feedback.list_feedback_windows(service)
        assert len(windows)==1, f'Feedback lost after single transient failure: first={first}, next={second}, windows={len(windows)}'
    asyncio.run(scenario())


@pytest.mark.parametrize("reader", ["windows", "feedback", "showcases"])
@pytest.mark.parametrize("missing_table", [False, True])
def test_visit_sqlite_connections_are_closed(tmp_path, monkeypatch, reader, missing_table):
    from features.world import visit_feedback, visit_report
    path = tmp_path / 'world.db'
    conn = sqlite3.connect(path)
    if not missing_table:
        conn.execute('CREATE TABLE world_events(id INTEGER, event_type TEXT, actor_state INTEGER, target_state INTEGER, payload_json TEXT, created_at TEXT)')
        conn.commit()
    conn.close()
    opened = []
    class Tracked(sqlite3.Connection):
        closed = False
        def close(self):
            self.closed = True
            super().close()
    original = sqlite3.connect
    def connect(*args, **kwargs):
        result = original(*args, **kwargs, factory=Tracked)
        opened.append(result)
        return result
    monkeypatch.setattr(sqlite3, 'connect', connect)
    now = datetime.now(timezone.utc)
    readers = {
        'windows': lambda: visit_feedback._load_windows_sync(path),
        'feedback': lambda: visit_feedback._load_feedback_sync(path, host_state=1, guest_state=2, accepted_event_id=3),
        'showcases': lambda: visit_report._load_showcases_sync(path, 1, 2, now-timedelta(days=1), now),
    }
    try:
        if missing_table:
            with pytest.raises(sqlite3.OperationalError):
                readers[reader]()
        else:
            readers[reader]()
        assert opened and all(c.closed for c in opened)
    finally:
        for conn in opened:
            conn.close()


def test_paused_dnd_does_not_consume_unrelated_reply(monkeypatch):
    from AI import dnd_local_runtime as runtime
    async def scenario():
        monkeypatch.setattr(runtime, '_LOCKS', {})
        monkeypatch.setattr(runtime, '_OWNERS', {})
        session=SimpleNamespace(paused=True)
        dnd=SimpleNamespace(dnd_sessions={-1001707530786:session},poll_map={})
        guard=runtime.GameUpdateMiddleware(SimpleNamespace(dnd=dnd))
        event=SimpleNamespace(chat=SimpleNamespace(id=-1001707530786),text='Agreed',data=None,
            reply_to_message=SimpleNamespace(message_id=123,from_user=SimpleNamespace(id=9,is_bot=False)),
            from_user=SimpleNamespace(id=8),answer=AsyncMock())
        handler=AsyncMock(return_value='handled outside DnD')
        await guard(handler,event,{})
        assert handler.await_count==1, 'Paused DnD consumed an ordinary reply to another participant'
    asyncio.run(scenario())


def test_egra_simultaneous_votes_do_not_corrupt_options(monkeypatch):
    from games import egra
    game={'is_active':True,'options':['A','B','C'],'poll_id':'poll','poll_message_id':1,'final_button_message_id':None}
    monkeypatch.setattr(egra,'game_states',{-321:game})
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delete(*args):
            entered.set()
            await release.wait()
        bot = SimpleNamespace(
            delete_message=delete,
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=3)),
            send_poll=AsyncMock(return_value=SimpleNamespace(message_id=2, poll=SimpleNamespace(id='next'))),
        )
        def answer(user_id):
            return SimpleNamespace(poll_id='poll', option_ids=[2], user=SimpleNamespace(id=user_id, full_name=f'Player {user_id}'))
        first = asyncio.create_task(egra.handle_egra_answer(answer(1), bot))
        await _wait(entered)
        second = await egra.handle_egra_answer(answer(2), bot)
        release.set()
        results = [await first, second]
        assert not any(isinstance(x,Exception) for x in results), f'Simultaneous valid votes raised: {results!r}'
        assert len(game['options'])==2
        assert bot.send_poll.await_count == 1
    asyncio.run(scenario())


def test_distortion_requests_keep_separate_input_files(monkeypatch,tmp_path):
    from services import distortion_formats as formats
    monkeypatch.chdir(tmp_path)
    # Deterministically exercise a permitted random suffix collision.
    monkeypatch.setattr(random,'randint',lambda *args:1234)
    async def scenario():
        first_worker=asyncio.Event(); second_downloaded=asyncio.Event()
        seen={}
        async def download(file_id,local_path):
            if file_id=='B':await first_worker.wait()
            Path(local_path).write_bytes(file_id.encode())
            if file_id=='B':second_downloaded.set()
            return True
        async def worker(token,chat_id,media_info,intensity):
            if chat_id==1:
                first_worker.set();await second_downloaded.wait()
            seen[chat_id]=Path(media_info['local_path']).read_bytes()
        module=SimpleNamespace(parse_intensity_from_text=lambda text:45,download_file=download,
            distortion_worker_async=worker,main_bot_instance=SimpleNamespace(token='fake'))
        def message(chat_id,file_id):
            target=SimpleNamespace(photo=[SimpleNamespace(file_id=file_id)])
            return SimpleNamespace(chat=SimpleNamespace(id=chat_id),reply_to_message=target,
                text='distort',caption=None,answer=AsyncMock())
        await asyncio.gather(formats.handle_format_preserving_distortion_request(message(1,'A'),distortion_module=module),
            formats.handle_format_preserving_distortion_request(message(2,'B'),distortion_module=module))
        assert seen=={1:b'A',2:b'B'},f'Input from another chat substituted: {seen!r}'
    asyncio.run(scenario())


def test_media_process_stops_when_request_is_cancelled(monkeypatch):
    from services import media_change
    async def scenario():
        created=asyncio.Event(); processes=[]
        original=asyncio.create_subprocess_exec
        async def spawn(*args,**kwargs):
            proc=await original(*args,**kwargs)
            processes.append(proc);created.set();return proc
        monkeypatch.setattr(media_change.asyncio,'create_subprocess_exec',spawn)
        # A harmless local Python process stands in for ffmpeg.
        task=asyncio.create_task(media_change._run_command([sys.executable,'-c','import time; time.sleep(5)']))
        await _wait(created)
        await asyncio.sleep(0)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):await task
            try:await asyncio.wait_for(processes[0].wait(),timeout=0.2)
            except asyncio.TimeoutError:pass
            assert processes[0].returncode is not None,'Cancelled request left its child process running'
        finally:
            for proc in processes:
                if proc.returncode is None:
                    proc.kill()
                await proc.communicate()
    asyncio.run(scenario())


@pytest.mark.parametrize("starter", ["regular", "participants", "daily"])
@pytest.mark.parametrize("phase", ["history", "generation"])
def test_all_quiz_starters_can_be_stopped_during_preparation(monkeypatch, starter, phase):
    from AI import quiz

    async def scenario():
        for name in ("quiz_states", "quiz_questions", "_quiz_generations", "_quiz_preparing"):
            monkeypatch.setattr(quiz, name, {})
        entered, release = asyncio.Event(), asyncio.Event()
        questions = [{"text": "Q", "options": ["A", "B"], "correct_answer": "A"}]

        async def history(*args, **kwargs):
            if phase == "history":
                entered.set()
                await release.wait()
            return [{"text": "hello"}] * 20

        async def generate(*args, **kwargs):
            if phase == "generation":
                entered.set()
                await release.wait()
            return questions

        monkeypatch.setattr(quiz, "extract_messages", history)
        monkeypatch.setattr(quiz, "generate_quiz_with_gemini", generate)
        monkeypatch.setattr(quiz, "generate_participant_quiz", generate)
        bot = SimpleNamespace(send_poll=AsyncMock(), send_message=AsyncMock(), stop_poll=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=-322))
        starts = {
            "regular": lambda: quiz.process_quiz_start(message, bot),
            "participants": lambda: quiz.process_participant_quiz_start(message, bot),
            "daily": lambda: quiz.send_daily_quiz(bot, -322),
        }
        task = asyncio.create_task(starts[starter]())
        await _wait(entered)
        assert await quiz.stop_quiz(bot, -322)
        release.set()
        await task
        bot.send_poll.assert_not_awaited()
        assert not quiz.quiz_states and not quiz.quiz_questions
        assert not quiz._quiz_preparing

    asyncio.run(scenario())


def test_cancelled_quiz_cannot_release_new_preparation(monkeypatch):
    from AI import quiz

    async def scenario():
        for name in ("quiz_states", "quiz_questions", "_quiz_generations", "_quiz_preparing"):
            monkeypatch.setattr(quiz, name, {})
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        count = 0

        async def generate(*args, **kwargs):
            nonlocal count
            index = count
            count += 1
            entered[index].set()
            await release[index].wait()
            return [{"text": "Q", "options": ["A", "B"], "correct_answer": "A"}]

        monkeypatch.setattr(quiz, "extract_messages", AsyncMock(return_value=[{"text": "hello"}]))
        monkeypatch.setattr(quiz, "generate_quiz_with_gemini", generate)
        bot = SimpleNamespace(send_poll=AsyncMock(return_value=SimpleNamespace(message_id=1, poll=SimpleNamespace(id="new"))), stop_poll=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=-323))
        old = asyncio.create_task(quiz.process_quiz_start(message, bot))
        await _wait(entered[0])
        assert await quiz.stop_quiz(bot, -323)
        new = asyncio.create_task(quiz.process_quiz_start(message, bot))
        await _wait(entered[1])
        release[0].set()
        assert not (await old)[0]
        assert not (await quiz.process_quiz_start(message, bot))[0]
        assert count == 2
        release[1].set()
        assert (await new)[0]
        bot.send_poll.assert_awaited_once()

    asyncio.run(scenario())


def test_partial_config_keeps_legacy_precedence_and_env_fallback():
    private = types.ModuleType("config_private")
    private.API_TOKEN = "file-token"
    private.GENERIC_API_KEY = "file-key"
    private.OPENWEATHER_API_KEY = "file-weather"
    with patch.dict(sys.modules, {"config_private": private}), patch.dict(os.environ, {
        "API_TOKEN": "env-token", "GENERIC_API_KEY": "env-key", "GROQ_API_KEY": "env-groq",
        "OPENWEATHER_API_KEY": "env-weather",
    }, clear=True):
        settings = runpy.run_path("core/settings.py")
    assert settings["API_TOKEN"] == "file-token"
    assert settings["GENERIC_API_KEY"] == "file-key"
    assert settings["GROQ_API_KEY"] == "env-groq"
    assert settings["OPENWEATHER_API_KEY"] == "env-weather"


@pytest.mark.parametrize("failed_send", [1, 2])
def test_visit_retry_keeps_successful_deliveries_and_cached_report(tmp_path, monkeypatch, failed_send):
    from features.world import interactions
    from features.world.service import WorldService
    from features.world.visit_report import VisitReport
    from infrastructure.persistence.sqlite_world import SQLiteWorldRepository

    async def scenario():
        path = tmp_path / "world.db"
        repo = SQLiteWorldRepository(path)
        repo.init_schema()
        service = WorldService(repo)
        host = await service.enable_state(-1001, "Host")
        guest = await service.enable_state(-1002, "Guest")
        service.ledger.record_event("state_visit_accepted", actor_state=guest.world_id,
            target_state=host.world_id, created_at=datetime.now(timezone.utc)-timedelta(hours=25))
        report = AsyncMock(return_value=VisitReport("Fixed report", 2, 1))
        monkeypatch.setattr(interactions, "build_visit_report", report)
        delivered = []
        attempts = 0

        async def send(chat_id, text):
            nonlocal attempts
            attempts += 1
            if attempts == failed_send:
                raise RuntimeError("transient transport failure")
            delivered.append((chat_id, text))
            return SimpleNamespace(message_id=attempts)

        bot = SimpleNamespace(send_message=send)
        assert await interactions.expire_due_visits(bot, service) == 1
        assert len(service.ledger.pending_visit_deliveries()) == 1
        restarted = WorldService(SQLiteWorldRepository(path))
        assert await interactions.expire_due_visits(bot, restarted) == 0
        assert len(delivered) == 3
        assert len(set(delivered)) == 3
        assert attempts == 4
        assert restarted.ledger.pending_visit_deliveries() == []
        public_events = await restarted.list_events(limit=200)
        assert not any(event.event_type in {
            "state_visit_notification_delivered", "state_visit_delivery_completed"
        } for event in public_events)
        report.assert_awaited_once()
        assert report.await_args.kwargs["finished_at"] is not None
        await interactions.expire_due_visits(bot, restarted)
        assert attempts == 4

    asyncio.run(scenario())


def test_manual_visit_finish_recovers_after_restart_before_delivery(tmp_path):
    from features.world import interactions
    from features.world.service import WorldService
    from infrastructure.persistence.sqlite_world import SQLiteWorldRepository

    async def scenario():
        path = tmp_path / "world.db"
        repo = SQLiteWorldRepository(path)
        repo.init_schema()
        service = WorldService(repo)
        host = await service.enable_state(-1001, "Host")
        guest = await service.enable_state(-1002, "Guest")
        service.ledger.record_event("state_visit_accepted", actor_state=guest.world_id, target_state=host.world_id)
        assert await interactions.finish_visit(service, host.world_id, guest.world_id, reason="manual")
        restarted = WorldService(SQLiteWorldRepository(path))
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)))
        assert await interactions.expire_due_visits(bot, restarted) == 0
        assert bot.send_message.await_count == 3
        assert restarted.ledger.pending_visit_deliveries() == []
        # Pre-upgrade finishes must never be replayed as new notifications.
        service.ledger.record_event("state_visit_finished", actor_state=host.world_id,
            target_state=guest.world_id, payload={"accepted_event_id": 999, "reason": "manual"})
        await interactions.expire_due_visits(bot, restarted)
        assert bot.send_message.await_count == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("stale_fails", [False, True])
def test_egra_poll_from_old_game_cannot_replace_or_erase_restart(monkeypatch, stale_fails):
    from games import egra

    async def scenario():
        old = {"is_active": True, "options": ["A", "B"], "poll_id": None}
        new = {"is_active": True, "options": ["1", "2", "3"], "poll_id": "new"}
        monkeypatch.setattr(egra, "game_states", {-324: old})
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(**kwargs):
            entered.set()
            await release.wait()
            if stale_fails:
                raise RuntimeError("transport failure")
            return SimpleNamespace(message_id=10, poll=SimpleNamespace(id="old"))

        bot = SimpleNamespace(send_poll=send, delete_message=AsyncMock())
        task = asyncio.create_task(egra.send_game_poll(-324, bot))
        await _wait(entered)
        egra.game_states[-324] = new
        release.set()
        await task
        assert egra.game_states[-324] is new
        assert new["poll_id"] == "new"
        if not stale_fails:
            bot.delete_message.assert_awaited_once_with(-324, 10)

    asyncio.run(scenario())


def test_egra_final_button_awards_only_one_winner(monkeypatch):
    from games import egra

    async def scenario():
        monkeypatch.setattr(egra, "game_states", {-325: {"is_active": True, "final_button_message_id": 10}})
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(*args, **kwargs):
            entered.set()
            await release.wait()

        bot = SimpleNamespace(send_message=AsyncMock(side_effect=send), delete_message=AsyncMock())
        callback = SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=-325), message_id=10, delete=AsyncMock()),
            from_user=SimpleNamespace(full_name="Player"), answer=AsyncMock())
        first = asyncio.create_task(egra.handle_final_button_press(callback, bot))
        await _wait(entered)
        await egra.handle_final_button_press(callback, bot)
        release.set()
        await first
        bot.send_message.assert_awaited_once()
        assert -325 not in egra.game_states

    asyncio.run(scenario())


def test_paused_dnd_still_blocks_reply_to_game_prompt(monkeypatch):
    from AI import dnd_local_runtime as runtime

    async def scenario():
        monkeypatch.setattr(runtime, "_LOCKS", {})
        monkeypatch.setattr(runtime, "_OWNERS", {})
        session = SimpleNamespace(paused=True, action_prompt_message_id=123)
        dnd = SimpleNamespace(dnd_sessions={-326: session}, poll_map={})
        guard = runtime.GameUpdateMiddleware(SimpleNamespace(dnd=dnd))
        event = SimpleNamespace(chat=SimpleNamespace(id=-326), text="Attack", data=None,
            reply_to_message=SimpleNamespace(message_id=123), from_user=SimpleNamespace(id=8), answer=AsyncMock())
        handler = AsyncMock()
        await guard(handler, event, {})
        handler.assert_not_awaited()
        event.answer.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "download_failure", "cancelled"])
def test_distortion_removes_its_directory_on_exit(outcome):
    from services import distortion_formats as formats

    async def scenario():
        paths = []

        async def download(file_id, path):
            paths.append(Path(path))
            Path(path).write_bytes(b"test")
            return outcome != "download_failure"

        async def worker(*args):
            if outcome == "cancelled":
                raise asyncio.CancelledError

        module = SimpleNamespace(parse_intensity_from_text=lambda text: 45, download_file=download,
            distortion_worker_async=worker, main_bot_instance=SimpleNamespace(token="fake"))
        message = SimpleNamespace(chat=SimpleNamespace(id=1), reply_to_message=SimpleNamespace(photo=[SimpleNamespace(file_id="A")]),
            text="distort", caption=None, answer=AsyncMock())
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await formats.handle_format_preserving_distortion_request(message, distortion_module=module)
        else:
            await formats.handle_format_preserving_distortion_request(message, distortion_module=module)
        assert paths and all(not path.parent.exists() for path in paths)

    asyncio.run(scenario())


def test_concurrent_channel_sweeps_send_one_reflection(monkeypatch, tmp_path):
    from features.channel import polls

    async def scenario():
        monkeypatch.setattr(polls, "POLL_STATE_FILE", tmp_path / "polls.json")
        monkeypatch.setattr(polls, "_state_lock", asyncio.Lock())
        monkeypatch.setattr(polls, "_processing_lock", asyncio.Lock())
        monkeypatch.setattr(polls, "append_post", lambda record: None)
        now = datetime.now(timezone.utc)
        polls._write_state({"polls": [{"status": "awaiting_reflection", "poll_id": "old",
            "message_id": 1, "reflection_due_at": (now-timedelta(minutes=1)).isoformat()}]})
        entered, release = asyncio.Event(), asyncio.Event()

        async def generate(record):
            entered.set()
            await release.wait()
            return "Reflection"

        monkeypatch.setattr(polls, "_generate_reflection", generate)
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=2)))
        first = asyncio.create_task(polls.process_due_polls(bot, channel_target="fake", now=now))
        await _wait(entered)
        second = asyncio.create_task(polls.process_due_polls(bot, channel_target="fake", now=now))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        bot.send_message.assert_awaited_once()
        assert polls._read_state()["polls"][0]["status"] == "reflected"

    asyncio.run(scenario())


def test_concurrent_visit_notifications_are_delivered_once(tmp_path, monkeypatch):
    from features.world import interactions
    from features.world.service import WorldService
    from infrastructure.persistence.sqlite_world import SQLiteWorldRepository

    async def scenario():
        monkeypatch.setattr(interactions, "_visit_delivery_lock", asyncio.Lock())
        repo = SQLiteWorldRepository(tmp_path / "world.db")
        repo.init_schema()
        service = WorldService(repo)
        host = await service.enable_state(-1001, "Host")
        guest = await service.enable_state(-1002, "Guest")
        service.ledger.record_event("state_visit_accepted", actor_state=guest.world_id, target_state=host.world_id)
        visit = await interactions.finish_visit(service, host.world_id, guest.world_id, reason="manual")
        entered, release = asyncio.Event(), asyncio.Event()
        sends = 0

        async def send(*args, **kwargs):
            nonlocal sends
            sends += 1
            if sends == 1:
                entered.set()
                await release.wait()
            return SimpleNamespace(message_id=sends)

        bot = SimpleNamespace(send_message=send)
        first = asyncio.create_task(interactions.notify_visit_finished(bot, service, visit, reason="manual"))
        await _wait(entered)
        second = asyncio.create_task(interactions.notify_visit_finished(bot, service, visit, reason="manual"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        assert sends == 3
        assert not service.ledger.pending_visit_deliveries()

    asyncio.run(scenario())
