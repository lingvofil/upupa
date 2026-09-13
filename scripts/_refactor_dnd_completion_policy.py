from pathlib import Path


def replace_one(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file.write_text(text.replace(old, new), encoding="utf-8")


replace_one(
    "AI/dnd_completion.py",
    '''class DndParticipantCompletionMiddleware(BaseMiddleware):
    """Collect participant replies robustly and finish complete group decisions early."""

    async def __call__(self, handler, event, data):''',
    '''class DndCompletionPolicy:
    """Explicit extension points for campaign/combat completion behavior."""

    def __init__(self):
        self.after_participant_joined = None
        self.filter_expected_ids = None


class DndParticipantCompletionMiddleware(BaseMiddleware):
    """Collect participant replies robustly and finish complete group decisions early."""

    def __init__(self, policy=None):
        self.policy = policy or DndCompletionPolicy()

    async def __call__(self, handler, event, data):''',
)

replace_one(
    "AI/dnd_completion.py",
    '''    @staticmethod
    def _expected_ids(dnd, session, target_user_ids) -> set[int]:
        if not dnd._is_participant_mode(session):
            return set()
        participants = dnd._participant_ids(session)
        targets = {int(value) for value in (target_user_ids or [])}
        return participants & targets if targets else participants
''',
    '''    def _expected_ids(self, dnd, session, target_user_ids) -> set[int]:
        if not dnd._is_participant_mode(session):
            return set()
        participants = dnd._participant_ids(session)
        targets = {int(value) for value in (target_user_ids or [])}
        expected = participants & targets if targets else participants
        if self.policy.filter_expected_ids is not None:
            expected = set(self.policy.filter_expected_ids(dnd, session, expected))
        return expected
''',
)

replace_one(
    "AI/dnd_completion.py",
    '''        user_name = getattr(user, "first_name", None) or f"егрок {user_id}"

        if user_id not in participants:''',
    '''        user_name = getattr(user, "first_name", None) or f"егрок {user_id}"
        joined = False

        if user_id not in participants:''',
)

replace_one(
    "AI/dnd_completion.py",
    '''                bool(targets),
            )

            if targets:''',
    '''                bool(targets),
            )
            joined = True

            if targets:''',
)

replace_one(
    "AI/dnd_completion.py",
    '''                await bot.send_message(
                    chat_id,
                    f"{user_name}, ты влез в егру. Но щас ход {names or 'других егроков'}: "
                    "твой ответ не учтён — жди следующей движухи.",
                )
                return
''',
    '''                await bot.send_message(
                    chat_id,
                    f"{user_name}, ты влез в егру. Но щас ход {names or 'других егроков'}: "
                    "твой ответ не учтён — жди следующей движухи.",
                )
                await self._after_participant_joined(
                    dnd, bot, event, session, user_id, user_name
                )
                return
''',
)

replace_one(
    "AI/dnd_completion.py",
    '''        logging.info(
            "DnD participant action precollected chat_id=%s user_id=%s prompt_message_id=%s",
            chat_id,
            user_id,
            prompt_message_id,
        )

    async def _maybe_finalize(self, event, bot, dnd) -> None:''',
    '''        logging.info(
            "DnD participant action precollected chat_id=%s user_id=%s prompt_message_id=%s",
            chat_id,
            user_id,
            prompt_message_id,
        )
        if joined:
            await self._after_participant_joined(
                dnd, bot, event, session, user_id, user_name
            )

    async def _after_participant_joined(
        self, dnd, bot, event, session, user_id: int, user_name: str
    ) -> None:
        callback = self.policy.after_participant_joined
        if callback is not None:
            await callback(dnd, bot, event, session, user_id, user_name)

    async def _maybe_finalize(self, event, bot, dnd) -> None:''',
)

replace_one(
    "AI/dnd_completion.py",
    "def configure_dnd_completion(dnd_router, *, middleware_class=None) -> None:",
    "def configure_dnd_completion(dnd_router, *, policy=None) -> None:",
)
replace_one(
    "AI/dnd_completion.py",
    '''    middleware_type = middleware_class or DndParticipantCompletionMiddleware
    middleware = middleware_type()
''',
    '''    middleware = DndParticipantCompletionMiddleware(policy=policy)
''',
)

replace_one(
    "AI/dnd_campaign.py",
    "def configure_dnd_campaign(dnd, router):",
    "def configure_dnd_campaign(dnd, router, *, completion_policy=None):",
)
replace_one(
    "AI/dnd_campaign.py",
    '''    from AI.dnd_completion import DndParticipantCompletionMiddleware
    old_precollect = DndParticipantCompletionMiddleware._precollect_action_reply

    async def precollect(self, dnd_module, bot, event):
        chat = getattr(event, "chat", None); user = getattr(event, "from_user", None); session = dnd_module.dnd_sessions.get(int(chat.id)) if chat else None
        uid = int(user.id) if user else None; was_new = bool(session and uid is not None and str(uid) not in session.participants)
        result = await old_precollect(self, dnd_module, bot, event); session = dnd_module.dnd_sessions.get(int(chat.id)) if chat else None
        if was_new and session and str(uid) in session.participants:
            profile = await _auto_profile(dnd_module, session, uid)
            dnd_module.persist_dnd_sessions()
            await bot.send_message(session.chat_id, f"🎭 {user.first_name} врывается сразу. Профиль выдан автоматически: {_profile_text(profile)}.")
        return result

    DndParticipantCompletionMiddleware._precollect_action_reply = precollect
''',
    '''    async def after_participant_joined(
        dnd_module, bot, event, session, user_id, user_name
    ):
        profile = await _auto_profile(dnd_module, session, user_id)
        dnd_module.persist_dnd_sessions()
        await bot.send_message(
            session.chat_id,
            f"🎭 {user_name} врывается сразу. Профиль выдан автоматически: {_profile_text(profile)}.",
        )

    if completion_policy is not None:
        completion_policy.after_participant_joined = after_participant_joined
''',
)

replace_one(
    "AI/dnd_combat.py",
    "def install_dnd_combat(dnd_router) -> None:",
    "def install_dnd_combat(dnd_router, *, completion_policy=None) -> None:",
)
replace_one(
    "AI/dnd_combat.py",
    "    from AI.dnd_completion import DndParticipantCompletionMiddleware\n",
    "",
)
replace_one(
    "AI/dnd_combat.py",
    '''    original_expected = DndParticipantCompletionMiddleware._expected_ids

    def expected_ids(dnd_module, session, target_user_ids):
        expected = original_expected(dnd_module, session, target_user_ids)
        living = _living_ids(session)
        return {user_id for user_id in expected if user_id in living}

    DndParticipantCompletionMiddleware._expected_ids = staticmethod(expected_ids)
''',
    '''    if completion_policy is not None:
        def filter_expected_ids(dnd_module, session, expected):
            living = _living_ids(session)
            return {user_id for user_id in expected if user_id in living}

        completion_policy.filter_expected_ids = filter_expected_ids
''',
)
