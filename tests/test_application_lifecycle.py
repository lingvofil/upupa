import asyncio
import logging

from app.lifecycle import TaskSupervisor


def test_task_supervisor_cancels_running_tasks_on_stop():
    async def scenario():
        started = asyncio.Event()
        finalized = asyncio.Event()

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalized.set()

        supervisor = TaskSupervisor()
        task = supervisor.start(worker(), name="worker")
        await started.wait()

        assert supervisor.task_count == 1
        assert supervisor.task_names == ("worker",)

        await supervisor.stop()

        assert task.cancelled()
        assert finalized.is_set()
        assert supervisor.task_count == 0

    asyncio.run(scenario())


def test_task_supervisor_logs_failed_task(caplog):
    async def scenario():
        test_logger = logging.getLogger("tests.task-supervisor")
        supervisor = TaskSupervisor(test_logger)

        async def crash():
            raise RuntimeError("boom")

        task = supervisor.start(crash(), name="broken-worker")
        try:
            await task
        except RuntimeError:
            pass

        # done-callback выполняется следующим тиком event loop.
        await asyncio.sleep(0)
        assert supervisor.task_count == 0

    with caplog.at_level(logging.ERROR, logger="tests.task-supervisor"):
        asyncio.run(scenario())

    assert "Background task broken-worker crashed" in caplog.text
