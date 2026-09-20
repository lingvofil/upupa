from types import SimpleNamespace

from AI import dnd_scene_clocks as clocks


def _session():
    return SimpleNamespace(scene_count=5)


def _apply(session, text):
    return clocks.apply_clock_metadata(session, text, text, [])


def test_scene_clocks_allow_two_independent_scales():
    session = _session()
    _apply(
        session,
        "[CLOCK:SET;ID:access;NAME:Доступ;KIND:PROGRESS;VALUE:0;MAX:6;WHEN_FULL:хранилище открыто]"
        "[CLOCK:SET;ID:alarm;NAME:Тревога;KIND:DANGER;VALUE:0;MAX:6;WHEN_FULL:прибывает стража]",
    )
    assert set(session.scene_clocks) == {"access", "alarm"}

    _apply(session, "[CLOCK:SET;ID:third;NAME:Третья;KIND:NEUTRAL;VALUE:0;MAX:4;WHEN_FULL:что-то происходит]")
    assert set(session.scene_clocks) == {"access", "alarm"}


def test_progress_and_danger_can_grow_in_same_response():
    session = _session()
    _apply(
        session,
        "[CLOCK:SET;ID:access;NAME:Доступ;KIND:PROGRESS;VALUE:0;MAX:6;WHEN_FULL:хранилище открыто]"
        "[CLOCK:SET;ID:alarm;NAME:Тревога;KIND:DANGER;VALUE:0;MAX:6;WHEN_FULL:прибывает стража]",
    )
    cleaned, notices = _apply(
        session,
        "[CLOCK:DELTA;ID:access;DELTA:2;CAUSE:вскрыли внешний замок]"
        "[CLOCK:DELTA;ID:alarm;DELTA:1;CAUSE:подняли шум]",
    )
    assert cleaned == ""
    assert session.scene_clocks["access"]["value"] == 2
    assert session.scene_clocks["alarm"]["value"] == 1
    assert len(notices) == 2


def test_filling_clock_establishes_event_without_ending_campaign():
    session = _session()
    _apply(
        session,
        "[CLOCK:SET;ID:alarm;NAME:Тревога;KIND:DANGER;VALUE:5;MAX:6;WHEN_FULL:прибывает стража]",
    )
    _, notices = _apply(session, "[CLOCK:DELTA;ID:alarm;DELTA:1;CAUSE:разбили окно]")
    row = session.scene_clocks["alarm"]
    assert row["value"] == 6
    assert row["full"] is True
    assert row["when_full"] == "прибывает стража"
    assert "Событие: прибывает стража" in notices[0]


def test_complete_bypasses_remaining_segments():
    session = _session()
    _apply(
        session,
        "[CLOCK:SET;ID:access;NAME:Доступ;KIND:PROGRESS;VALUE:1;MAX:6;WHEN_FULL:хранилище открыто]",
    )
    _, notices = _apply(session, "[CLOCK:COMPLETE;ID:access;CAUSE:нашли настоящий ключ]")
    row = session.scene_clocks["access"]
    assert row["value"] == 6
    assert row["full"] is True
    assert row["history"][-1]["complete"] is True
    assert "обход: нашли настоящий ключ" in notices[0]


def test_completed_clock_is_frozen_until_explicitly_cleared():
    session = _session()
    _apply(
        session,
        "[CLOCK:SET;ID:alarm;NAME:Тревога;KIND:DANGER;VALUE:6;MAX:6;WHEN_FULL:прибывает стража]",
    )
    _apply(session, "[CLOCK:DELTA;ID:alarm;DELTA:-2;CAUSE:замели следы]")
    row = session.scene_clocks["alarm"]
    assert row["value"] == 6
    assert row["full"] is True


def test_clear_frees_slot_for_new_scene_clock():
    session = _session()
    _apply(
        session,
        "[CLOCK:SET;ID:access;NAME:Доступ;KIND:PROGRESS;VALUE:0;MAX:6;WHEN_FULL:дверь открыта]"
        "[CLOCK:SET;ID:alarm;NAME:Тревога;KIND:DANGER;VALUE:0;MAX:6;WHEN_FULL:стража приходит]",
    )
    _apply(session, "[CLOCK:CLEAR;ID:access]")
    _apply(
        session,
        "[CLOCK:SET;ID:escape;NAME:Побег;KIND:PROGRESS;VALUE:1;MAX:4;WHEN_FULL:партия уходит от погони]",
    )
    assert set(session.scene_clocks) == {"alarm", "escape"}


def test_render_clocks_is_compact_and_bounded():
    session = _session()
    _apply(
        session,
        "[CLOCK:SET;ID:access;NAME:Доступ;KIND:PROGRESS;VALUE:3;MAX:6;WHEN_FULL:хранилище открыто]"
        "[CLOCK:SET;ID:alarm;NAME:Тревога;KIND:DANGER;VALUE:2;MAX:4;WHEN_FULL:прибывает стража]",
    )
    lines = clocks.render_clocks(session.scene_clocks)
    assert lines == [
        "🎯 Доступ: ■■■□□□ 3/6",
        "🚨 Тревога: ■■□□ 2/4",
    ]
