import asyncio
from types import SimpleNamespace

from features.world.permissions import is_chat_admin
from features.world.service import WorldService
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository


def _service(tmp_path):
    repo = SQLiteWorldRepository(tmp_path / "world.db")
    repo.init_schema()
    return repo, WorldService(repo)


def _run(coro):
    return asyncio.run(coro)


def test_world_id_is_stable_and_chat_id_is_unique(tmp_path):
    repo, service = _service(tmp_path)

    first = _run(service.enable_state(-1001, "Alpha"))
    again = _run(service.enable_state(-1001, "Alpha renamed"))
    states = repo.list_enabled_states()

    assert first.world_id == again.world_id
    assert again.title == "Alpha renamed"
    assert len(states) == 1
    assert states[0].chat_id == -1001


def test_reenable_reuses_state_and_preserves_alliance(tmp_path):
    repo, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    accepted = _run(
        service.resolve_request(proposal.request.request_id, -1002, "accepted")
    )
    assert accepted.status == "accepted"

    disabled = _run(service.disable_state(-1002))
    assert disabled is not None and not disabled.enabled
    assert repo.has_alliance(alpha.world_id, beta.world_id)
    assert beta.world_id not in {state.world_id for state in repo.list_enabled_states()}

    reenabled = _run(service.enable_state(-1002, "Beta again"))
    assert reenabled.world_id == beta.world_id
    assert repo.has_alliance(alpha.world_id, beta.world_id)


def test_create_accept_and_callback_idempotency(tmp_path):
    repo, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    assert proposal.status == "created"
    assert proposal.request is not None

    accepted = _run(
        service.resolve_request(proposal.request.request_id, -1002, "accepted")
    )
    assert accepted.status == "accepted"
    assert repo.has_alliance(alpha.world_id, beta.world_id)

    repeated = _run(
        service.resolve_request(proposal.request.request_id, -1002, "accepted")
    )
    assert repeated.status == "already_resolved"
    assert repeated.request is not None
    assert repeated.request.status == "accepted"


def test_reject_does_not_create_alliance(tmp_path):
    repo, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    rejected = _run(
        service.resolve_request(proposal.request.request_id, -1002, "rejected")
    )

    assert rejected.status == "rejected"
    assert not repo.has_alliance(alpha.world_id, beta.world_id)


def test_break_alliance_is_symmetric(tmp_path):
    repo, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))
    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    _run(service.resolve_request(proposal.request.request_id, -1002, "accepted"))

    result = _run(service.break_alliance(-1002, "Beta", alpha.world_id))

    assert result.status == "broken"
    assert not repo.has_alliance(alpha.world_id, beta.world_id)


def test_self_alliance_is_rejected(tmp_path):
    _, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))

    result = _run(service.propose_alliance(-1001, "Alpha", alpha.world_id))

    assert result.status == "self"


def test_duplicate_and_crossed_requests_share_one_pending_request(tmp_path):
    _, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    async def race():
        return await asyncio.gather(
            service.propose_alliance(-1001, "Alpha", beta.world_id),
            service.propose_alliance(-1002, "Beta", alpha.world_id),
        )

    first, second = _run(race())
    assert {first.status, second.status} == {"created", "duplicate"}
    assert first.request is not None and second.request is not None
    assert first.request.request_id == second.request.request_id

    duplicate = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    assert duplicate.status == "duplicate"
    assert duplicate.request.request_id == first.request.request_id


def test_disabled_state_cannot_receive_new_requests_and_cancels_pending(tmp_path):
    _, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    _run(service.disable_state(-1002))

    stale = _run(
        service.resolve_request(proposal.request.request_id, -1002, "accepted")
    )
    assert stale.status == "already_resolved"
    assert stale.request.status == "cancelled"

    disabled_target = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    assert disabled_target.status == "target_disabled"

    disabled_source = _run(service.disable_state(-1001))
    assert disabled_source.world_id == alpha.world_id
    source_result = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    assert source_result.status == "source_disabled"


def test_repository_rechecks_disabled_state_when_creating_request(tmp_path):
    repo, service = _service(tmp_path)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))

    repo.disable_state(-1002)
    status, request = repo.create_request(alpha.world_id, beta.world_id)

    assert status == "target_disabled"
    assert request is None
    assert repo.get_pending_request_between(alpha.world_id, beta.world_id) is None


def test_world_persistence_survives_repository_recreation(tmp_path):
    db_path = tmp_path / "world.db"
    repo = SQLiteWorldRepository(db_path)
    repo.init_schema()
    service = WorldService(repo)
    alpha = _run(service.enable_state(-1001, "Alpha"))
    beta = _run(service.enable_state(-1002, "Beta"))
    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))
    _run(service.resolve_request(proposal.request.request_id, -1002, "accepted"))

    reopened = SQLiteWorldRepository(db_path)
    reopened.init_schema()

    assert reopened.get_state_by_chat_id(-1001).world_id == alpha.world_id
    assert reopened.get_state_by_chat_id(-1002).world_id == beta.world_id
    assert reopened.has_alliance(alpha.world_id, beta.world_id)


def test_request_must_be_resolved_in_target_chat(tmp_path):
    _, service = _service(tmp_path)
    beta = _run(service.enable_state(-1002, "Beta"))
    _run(service.enable_state(-1001, "Alpha"))
    proposal = _run(service.propose_alliance(-1001, "Alpha", beta.world_id))

    wrong_chat = _run(
        service.resolve_request(proposal.request.request_id, -9999, "accepted")
    )

    assert wrong_chat.status == "wrong_target"
    assert wrong_chat.request.status == "pending"


def test_world_permissions_require_telegram_admin_status():
    class FakeBot:
        def __init__(self, status):
            self.status = status

        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status=self.status)

    assert _run(is_chat_admin(FakeBot("administrator"), -1001, 1))
    assert _run(is_chat_admin(FakeBot("creator"), -1001, 1))
    assert not _run(is_chat_admin(FakeBot("member"), -1001, 1))


def test_world_setting_is_off_by_default_and_separate_from_sms(monkeypatch):
    import features.interactive_settings as interactive_settings
    import features.world.service as world_service_module

    monkeypatch.setattr(world_service_module, "_world_service", None)
    monkeypatch.setattr(interactive_settings, "sms_disabled_chats", set())
    monkeypatch.setattr(interactive_settings, "chat_settings", {"-1001": {}})

    text, markup = _run(interactive_settings.get_main_settings_markup("-1001"))
    buttons = [button.text for row in markup.inline_keyboard for button in row]

    assert "💬 *СМС/ММС:* Вкл. ✅" in text
    assert "🌍 *Мир Упупы:* Выкл. ❌" in text
    assert "Вкл. Мир" in buttons
