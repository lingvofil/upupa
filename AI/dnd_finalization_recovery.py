"""Durable, idempotent completion of a finished DnD campaign."""
from __future__ import annotations

import copy
import logging


def _completion_row(campaign, chat_id, completion_id):
    chat = campaign._chat_history(chat_id, True)
    for row in reversed(chat.get("campaigns") or []):
        if isinstance(row, dict) and str(row.get("completion_id") or "") == str(completion_id):
            return row
    return None


def _restore_growth_offer_tokens(session, row) -> None:
    raw = (row or {}).get("growth_created_offer_tokens") or []
    restored = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                restored.append((int(item[0]), str(item[1])))
            except (TypeError, ValueError):
                continue
    session.growth_created_offer_tokens = restored


def install_dnd_finalization_recovery(dnd) -> None:
    from AI import dnd_campaign as campaign
    from AI.dnd_result_recovery import finalization_state

    if getattr(campaign, "_upupa_dnd_finalization_recovery_installed", False):
        return

    original_archive = campaign._archive_campaign
    archive_save = campaign._save_archive

    def archive_campaign(dnd_module, session, finale, epilogue):
        finalization = finalization_state(session, create=True)
        completion_id = str(finalization.get("completion_id") or "") if finalization else ""

        existing = _completion_row(campaign, session.chat_id, completion_id) if completion_id else None
        if existing is not None:
            _restore_growth_offer_tokens(session, existing)
            if finalization is not None:
                finalization["archive_done"] = True
                dnd_module.persist_dnd_sessions()
            return existing

        snapshot = copy.deepcopy(campaign._archive)
        active_save = campaign._save_archive

        def defer_save(_dnd):
            return True

        campaign._save_archive = defer_save
        try:
            original_archive(dnd_module, session, finale, epilogue)
            chat = campaign._chat_history(session.chat_id, True)
            rows = chat.get("campaigns") or []
            if not rows:
                raise RuntimeError("DnD archive chain produced no campaign row")
            row = rows[-1]
            if completion_id:
                row["completion_id"] = completion_id
            growth_tokens = list(getattr(session, "growth_created_offer_tokens", []) or [])
            row["growth_created_offer_tokens"] = [
                [int(user_id), str(token)]
                for user_id, token in growth_tokens
            ]
        except Exception:
            campaign._archive = snapshot
            raise
        finally:
            campaign._save_archive = active_save

        if archive_save(dnd_module) is False:
            campaign._archive = snapshot
            raise RuntimeError("DnD campaign archive atomic save failed")

        if finalization is not None:
            finalization["archive_done"] = True
            dnd_module.persist_dnd_sessions()
        return row

    campaign._archive_campaign = archive_campaign

    original_finish = campaign._finish

    async def finish(dnd_module, bot, session, response):
        await original_finish(dnd_module, bot, session, response)

        finalization = finalization_state(session, create=True)
        if not finalization:
            # Defensive legacy path: no durable parent outbox exists.
            await bot.send_message(
                session.chat_id,
                "☠️ Егра окончена. Наследие этой катастрофы сохранено.",
            )
            dnd_module.cleanup_session(session.chat_id)
            return

        await bot.send_message(
            session.chat_id,
            "☠️ Егра окончена. Наследие этой катастрофы сохранено.",
        )
        finalization["final_notice_done"] = True
        dnd_module.persist_dnd_sessions()

        if not bool(finalization.get("final_image_done")):
            prompt = str(finalization.get("final_image_prompt") or "")
            if prompt:
                result = await campaign._image(
                    bot,
                    session.chat_id,
                    prompt,
                    "dnd_final_comic.png",
                    "📚 Финальный комикс. Вот до чего вы доигрались.",
                    deliver_if=lambda: dnd_module.dnd_sessions.get(session.chat_id) is session,
                )
                if result is not None:
                    finalization["final_image_done"] = True
                else:
                    finalization["final_image_failed"] = True
                dnd_module.persist_dnd_sessions()

        finalization["cleanup_ready"] = True
        dnd_module.persist_dnd_sessions()
        dnd_module.cleanup_session(session.chat_id)

    campaign._finish = finish
    campaign._upupa_dnd_finalization_recovery_installed = True
    logging.info("DnD durable finalization recovery installed")


__all__ = ["install_dnd_finalization_recovery"]
