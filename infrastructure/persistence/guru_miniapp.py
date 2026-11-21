"""Transactional, user-scoped solo visits. The separate share table holds only a public card."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import json
import random
import secrets
import sqlite3

from games.guru import miniapp, salon, seance


class StaleTurn(ValueError):
    pass


def _history(db, owner):
    return [dict(row) for row in db.execute("""
        SELECT id, story, json_extract(record, '$.client') AS client, json_extract(record, '$.title') AS title,
               json_extract(record, '$.keepsake') AS keepsake, json_extract(record, '$.type') AS archetype,
               json_extract(record, '$.fee') AS fee, json_type(record, '$.reply')='null' AS waiting
        FROM visits WHERE owner=? ORDER BY rowid DESC
    """, (owner,))]


def _interior(history, purchases):
    items = salon.owned_items(purchases)
    earned = {row["archetype"] for row in history if row["keepsake"]}
    return {"balance": sum(row["fee"] for row in history) - sum(purchases.values()),
            "items": [item.key for item in items], "rank": salon.rank(len(items)),
            "gifts": [{"id": gift.key, "emoji": gift.emoji, "name": gift.name, "giver": gift.giver,
                       "story": gift.story if owned else None} for gift, owned in salon.shelf(earned)]}


class GuruVisits:
    def __init__(self, path):
        self.path = path

    def request(self, user_id, command):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE IF NOT EXISTS salons (owner INTEGER PRIMARY KEY, rev INTEGER, active TEXT)")
            db.execute("""CREATE TABLE IF NOT EXISTS visits (
                id TEXT PRIMARY KEY, owner INTEGER NOT NULL, story TEXT NOT NULL, record TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS visit_owner ON visits(owner)")
            db.execute("""CREATE TABLE IF NOT EXISTS shared (
                token TEXT PRIMARY KEY, visit TEXT, slot INTEGER, card TEXT NOT NULL, UNIQUE(visit, slot))""")
            db.execute("""CREATE TABLE IF NOT EXISTS journey (
                id TEXT PRIMARY KEY, owner INTEGER, story TEXT, started INTEGER, first_reading INTEGER,
                finished INTEGER, returned INTEGER, shared INTEGER, abandoned INTEGER)""")
            db.execute("""CREATE TABLE IF NOT EXISTS purchases (
                owner INTEGER NOT NULL, item TEXT NOT NULL, price INTEGER NOT NULL, PRIMARY KEY(owner, item))""")
            op = command.get("op", "home")
            db.execute("BEGIN" if op in ("home", "catalog", "card", "record", "interior", "shop") else "BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM salons WHERE owner=?", (user_id,)).fetchone()
            rev, active = (row["rev"], json.loads(row["active"])) if row else (0, None)
            if op == "catalog":
                completed = {entry[0] for entry in db.execute("SELECT DISTINCT story FROM visits WHERE owner=?", (user_id,))}
                return {"catalog": miniapp.catalog(completed)}
            if op == "shop":
                return {"shop": miniapp.shop()}
            if op == "card":
                shared = db.execute("SELECT card FROM shared WHERE token=?", (command.get("token"),)).fetchone()
                if not shared:
                    raise ValueError("Карточка не найдена.")
                return {"card": json.loads(shared["card"])}
            if op in ("record", "reply", "share"):
                found = db.execute("SELECT record FROM visits WHERE id=? AND owner=?",
                                   (command.get("id"), user_id)).fetchone()
                if not found:
                    raise ValueError("Приём не найден.")
                record = json.loads(found["record"])
                if op == "record":
                    return {"record": record}
                if op == "share":
                    slot = command.get("slot")
                    if type(slot) is not int or not 0 <= slot < len(record["cards"]):
                        raise ValueError("Выберите карту из своего расклада.")
                    card = record["cards"][slot]
                    db.execute("INSERT OR IGNORE INTO shared VALUES (?, ?, ?, ?)",
                               (secrets.token_hex(12), record["id"], slot, json.dumps(card, ensure_ascii=False)))
                    token = db.execute("SELECT token FROM shared WHERE visit=? AND slot=?", (record["id"], slot)).fetchone()[0]
                    db.execute("UPDATE journey SET shared=coalesce(shared,strftime('%s','now')) WHERE id=?", (record["id"],))
                    return {"token": token, "card": card}
            changed = op in ("start", "move", "reply", "leave", "buy")
            if changed and command.get("rev") != rev:
                raise StaleTurn("Салон открыт ещё в одном окне. Показан последний ход.")
            history = _history(db, user_id)
            purchases = dict(db.execute("SELECT item, price FROM purchases WHERE owner=?", (user_id,)))
            if op == "interior":
                return {"salon": _interior(history, purchases)}
            current = None
            if op == "start":
                if active:
                    raise ValueError("Сначала завершите текущий приём.")
                story = seance.pick_least_visited(Counter(row["story"] for row in history), random.Random())
                active = miniapp.new_visit(story, (item.key for item in salon.owned_items(purchases)))
                db.execute("INSERT INTO journey(id,owner,story,started) VALUES (?,?,?,strftime('%s','now'))",
                           (active["id"], user_id, story))
            elif op == "leave":
                if not active:
                    raise ValueError("Сеанс уже завершён.")
                db.execute("UPDATE journey SET abandoned=strftime('%s','now') WHERE id=?", (active["id"],))
                active = None
            elif op == "move":
                if not active:
                    raise ValueError("Сеанс уже завершён.")
                session, _ = miniapp.replay(active)
                action = command.get("action")
                if action == "heard":
                    raise ValueError("Журнал открыт отдельно.")
                result = seance.act(session, 1, action, now=session.step + 1)
                active["moves"].append(action)
                if action.startswith(("tone:", "wd:")):
                    db.execute("UPDATE journey SET first_reading=strftime('%s','now') WHERE id=? AND first_reading IS NULL",
                               (active["id"],))
                if session.finished:
                    record = miniapp.close_visit(session, result.events)
                    db.execute("INSERT INTO visits VALUES (?, ?, ?, ?)",
                               (record["id"], user_id, record["story"], json.dumps(record, ensure_ascii=False)))
                    db.execute("UPDATE journey SET finished=strftime('%s','now') WHERE id=?", (active["id"],))
                    active = None
                else:
                    current = miniapp.scene(session, result.events)
            elif op == "reply":
                choice = command.get("choice")
                if choice not in miniapp.REPLIES or record["reply"] is not None:
                    raise ValueError("Это письмо уже закрыто или ответ недоступен.")
                record["reply"] = miniapp.second_visit(record, choice)
                db.execute("UPDATE visits SET record=? WHERE id=? AND owner=?",
                           (json.dumps(record, ensure_ascii=False), record["id"], user_id))
                db.execute("UPDATE journey SET returned=strftime('%s','now') WHERE id=?", (record["id"],))
            elif op == "buy":
                key = command.get("item")
                balance = sum(row["fee"] for row in history) - sum(purchases.values())
                refusal = salon.refusal(key, balance, purchases)
                if refusal:
                    raise ValueError(refusal)
                item = salon.ITEMS[key]
                db.execute("INSERT INTO purchases VALUES (?, ?, ?)", (user_id, key, item.price))
                purchases[key] = item.price
            elif op != "home":
                raise ValueError("Неизвестное действие.")
            if changed:
                rev += 1
                db.execute("INSERT OR REPLACE INTO salons VALUES (?, ?, ?)", (user_id, rev, json.dumps(active)))
            if active and current is None:
                current = miniapp.scene(*miniapp.replay(active))
            if op == "reply" or (op == "move" and active is None):
                history = _history(db, user_id)
            result = {"rev": rev, "current": current, "history": history, "count": len(seance.STORY_IDS),
                      "salon": _interior(history, purchases)}
            if op == "reply" or (op == "move" and active is None):
                result["record"] = record
            return result
