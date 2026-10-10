import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

from infrastructure.ai import gemini as gemini_mod
from infrastructure.ai import gemini_quota
from AI import dnd_generation_resilience as resilience

DAILY_TEXT = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': [{'violations': [{"
              "'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}]}]}}")
MINUTE_TEXT = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': [{'violations': [{"
               "'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}]}]}}")


class QuotaError(Exception):
    def __init__(self, text):
        super().__init__(text)
        self.code = 429


@pytest.fixture(autouse=True)
def clean_registry():
    gemini_quota.reset()
    yield
    gemini_quota.reset()


def test_daily_and_minute_429_are_distinguished():
    assert gemini_quota.is_daily_quota_error(QuotaError(DAILY_TEXT))
    assert not gemini_quota.is_daily_quota_error(QuotaError(MINUTE_TEXT))
    assert not gemini_quota.is_daily_quota_error(RuntimeError("503 UNAVAILABLE"))


def _utc(*parts):
    return dt.datetime(*parts, tzinfo=dt.timezone.utc).timestamp()


def test_reset_is_next_pacific_midnight_across_dst():
    # PDT (UTC-7): 2026-10-10 20:54 UTC = 13:54 PDT -> reset 2026-10-11 07:00 UTC
    assert gemini_quota.next_daily_reset(_utc(2026, 10, 10, 20, 54)) == _utc(2026, 10, 11, 7, 0)
    # PST (UTC-8) in winter
    assert gemini_quota.next_daily_reset(_utc(2026, 12, 1, 12, 0)) == _utc(2026, 12, 2, 8, 0)
    # DST ends 2026-11-01: reset at midnight PST of Nov 2
    assert gemini_quota.next_daily_reset(_utc(2026, 11, 1, 12, 0)) == _utc(2026, 11, 2, 8, 0)
    # DST starts 2026-03-08: reset at midnight PDT of Mar 9
    assert gemini_quota.next_daily_reset(_utc(2026, 3, 8, 20, 0)) == _utc(2026, 3, 9, 7, 0)
    # Just before midnight PDT -> the reset is minutes away, not a day
    assert gemini_quota.next_daily_reset(_utc(2026, 10, 11, 6, 59)) == _utc(2026, 10, 11, 7, 0)


def test_daily_quota_is_pair_scoped():
    now = _utc(2026, 10, 10, 20, 54)

    assert gemini_quota.mark_if_daily_quota("k1", "models/gemini-2.5-flash", QuotaError(DAILY_TEXT), now=now)
    assert gemini_quota.is_exhausted("k1", "gemini-2.5-flash", now=now + 3600)
    assert not gemini_quota.is_exhausted("k1", "gemini-2.5-flash-lite", now=now + 3600)
    assert not gemini_quota.is_exhausted("k2", "gemini-2.5-flash", now=now + 3600)
    assert not gemini_quota.is_exhausted("k1", "gemini-2.5-flash", now=gemini_quota.next_daily_reset(now) + 120)
    assert not gemini_quota.mark_if_daily_quota("k1", "gemini-2.5-flash", QuotaError(MINUTE_TEXT))


def test_dnd_pairs_use_whole_queue_and_skip_parked_pairs(monkeypatch):
    monkeypatch.setattr(resilience, "GEMINI_KEYS_POOL", ["k0", "k1", "k2"])
    monkeypatch.setattr(resilience, "_model_queue", lambda _chat: ["m-flash", "m-lite", "m-preview", "m-31lite"])
    monkeypatch.setattr(resilience, "_key_cursor", 0)
    pairs = resilience._attempt_pairs(1, 2)
    assert [model for _key, model in pairs] == ["m-flash", "m-lite"]
    assert pairs[0][0] != pairs[1][0]

    for key in ("k0", "k1", "k2"):
        gemini_quota.mark_if_daily_quota(key, "m-flash", QuotaError(DAILY_TEXT))
    gemini_quota.mark_if_daily_quota("k0", "m-lite", QuotaError(DAILY_TEXT))
    gemini_quota.mark_if_daily_quota("k2", "m-lite", QuotaError(DAILY_TEXT))
    monkeypatch.setattr(resilience, "_key_cursor", 0)
    pairs = resilience._attempt_pairs(1, 2)
    assert pairs[0] == ("k1", "m-lite")
    assert pairs[1][1] == "m-preview"
    assert not any(gemini_quota.is_exhausted(key, model) for key, model in pairs)

    for key in ("k0", "k1", "k2"):
        for model in ("m-lite", "m-preview", "m-31lite"):
            gemini_quota.mark_if_daily_quota(key, model, QuotaError(DAILY_TEXT))
    assert resilience._attempt_pairs(1, 2) == []


def test_dnd_thinking_config_per_family():
    assert resilience._thinking_config("gemini-2.5-flash").thinking_budget == 0
    assert resilience._thinking_config("gemini-3-flash-preview").thinking_level is not None
    assert resilience._thinking_config("gemini-3.1-flash-lite").thinking_level is not None


def test_general_wrapper_parks_daily_key_and_skips_it_next_time(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, key, model):
            self.key, self.model = key, model

        def generate_content(self, prompt, **_kwargs):
            calls.append((self.key, self.model))
            if self.model == "flash" and self.key in {"a", "b"}:
                raise QuotaError(DAILY_TEXT)
            return SimpleNamespace(text="ok", candidates=[])

    monkeypatch.setattr(gemini_mod, "_throttle_key", lambda _key: None)
    wrapper = gemini_mod.ModelFallbackWrapper(["flash", "lite"], ["flash", "lite"], keys_pool=["a", "b", "c"])
    monkeypatch.setattr(wrapper, "_build_model", lambda key, model: FakeModel(key, model))

    wrapper.generate_content("hi", require_text=False)
    assert ("c", "flash") in calls
    assert gemini_quota.is_exhausted("a", "flash") and gemini_quota.is_exhausted("b", "flash")
    assert wrapper._model_rate_limit_circuit_remaining("flash") == 0

    calls.clear()
    wrapper.generate_content("hi", require_text=False)
    assert calls == [("c", "flash")]


def test_general_wrapper_moves_to_next_model_when_all_keys_parked(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, key, model):
            self.key, self.model = key, model

        def generate_content(self, prompt, **_kwargs):
            calls.append((self.key, self.model))
            return SimpleNamespace(text="ok", candidates=[])

    for key in ("a", "b"):
        gemini_quota.mark_if_daily_quota(key, "flash", QuotaError(DAILY_TEXT))
    monkeypatch.setattr(gemini_mod, "_throttle_key", lambda _key: None)
    wrapper = gemini_mod.ModelFallbackWrapper(["flash", "lite"], ["flash", "lite"], keys_pool=["a", "b"])
    monkeypatch.setattr(wrapper, "_build_model", lambda key, model: FakeModel(key, model))
    wrapper.generate_content("hi", require_text=False)
    assert [model for _key, model in calls] == ["lite"]
