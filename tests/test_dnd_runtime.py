import pytest

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_completion
from AI.dnd_runtime import isolated_completion_middleware_class


def test_runtime_completion_class_is_private_and_restored():
    base_class = dnd_completion.DndParticipantCompletionMiddleware
    base_expected_ids = base_class._expected_ids

    with isolated_completion_middleware_class() as runtime_class:
        assert runtime_class is dnd_completion.DndParticipantCompletionMiddleware
        assert runtime_class is not base_class
        assert issubclass(runtime_class, base_class)

        runtime_class._expected_ids = staticmethod(lambda *_args: {999})

        assert runtime_class._expected_ids(None, None, None) == {999}
        assert base_class._expected_ids is base_expected_ids

    assert dnd_completion.DndParticipantCompletionMiddleware is base_class
    assert base_class._expected_ids is base_expected_ids


def test_runtime_completion_class_is_restored_after_installer_failure():
    base_class = dnd_completion.DndParticipantCompletionMiddleware

    with pytest.raises(RuntimeError, match="installer failed"):
        with isolated_completion_middleware_class() as runtime_class:
            assert dnd_completion.DndParticipantCompletionMiddleware is runtime_class
            raise RuntimeError("installer failed")

    assert dnd_completion.DndParticipantCompletionMiddleware is base_class
