"""Transactional, user-scoped solo visits. The separate share table holds only a public card."""
from __future__ import annotations

from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import replace
import json
import random
import secrets
import sqlite3

from games.guru import miniapp, salon, seance


class StaleTurn(ValueError):
    pass


def _set_profile(db, owner, name, auth_date):
    if name:
        db.execute("""INSERT INTO salon_profiles VALUES (?, ?, ?) ON CONFLICT(owner) DO UPDATE SET
            name=excluded.name, auth_date=excluded.auth_date
            WHERE excluded.auth_date > salon_profiles.auth_date
               OR (excluded.auth_date = salon_profiles.auth_date AND excluded.name != salon_profiles.name)""",
                   (owner, name, auth_date))


def _history(db, owner):
    return [dict(row) for row in db.execute("""
        SELECT id, story, json_extract(record, '$.client') AS client, json_extract(record, '$.title') AS title,
               json_extract(record, '$.keepsake') AS keepsake, json_extract(record, '$.type') AS archetype,
               json_extract(record, '$.fee') AS fee, json_type(record, '$.reply')='null' AS waiting
        FROM visits WHERE owner=? ORDER BY rowid DESC
    """, (owner,))]


def _purchases(db, owner):
    return dict(db.execute("SELECT item, price FROM purchases WHERE owner=?", (owner,)))


def _money(db, owner, history, purchases):
    chat = db.execute("""SELECT coalesce(sum(balance), 0), coalesce(sum(earned), 0),
        coalesce(sum(lost), 0), coalesce(sum(spent), 0) FROM chat_balances WHERE owner=?""", (owner,)).fetchone()
    fees = [row["fee"] for row in history]
    cost = sum(purchases.values())
    return {"balance": chat[0] + sum(fees) - cost,
            "earned": chat[1] + sum(fee for fee in fees if fee > 0),
            "lost": chat[2] - sum(fee for fee in fees if fee < 0),
            "spent": chat[3] + cost, "items": list(purchases)}


def _interior(history, purchases, money):
    items = salon.owned_items(purchases, catalog=miniapp.ITEMS)
    earned = {row["archetype"] for row in history if row["keepsake"]}
    balance, spent = money["balance"], money["spent"]
    return {"balance": balance, "fees": balance + spent, "spent": spent,
            "items": [item.key for item in items], "rank": salon.room_rank(purchases),
            "picture": miniapp.room_picture(purchases),
            "collection": [{"id": item.key, "name": item.name, "picture": miniapp.shop_picture(item.key)} for item in items],
            "gifts": [{"id": gift.key, "emoji": gift.emoji, "name": gift.name, "giver": gift.giver,
                       "story": gift.story if owned else None} for gift, owned in salon.shelf(earned)]}


def _pick_guest(db, owner, history, rng):
    counts = Counter(row["story"] for row in history)
    since_bonus = next((i for i, row in enumerate(history) if row["story"] in miniapp.GUESTS), len(history))
    ordinary = sum(row["story"] in seance.STORY_IDS for row in history[:since_bonus])
    if ordinary >= miniapp.BONUS_INTERVAL:
        last = db.execute("SELECT story, abandoned FROM journey WHERE owner=? ORDER BY rowid DESC LIMIT 1",
                          (owner,)).fetchone()
        if not last or not (last["abandoned"] and last["story"] in miniapp.GUESTS):
            fewest = min(counts[key] for key in miniapp.GUESTS)
            return rng.choice([key for key in miniapp.GUESTS if counts[key] == fewest])
    return seance.pick_least_visited(counts, rng)


def _recent_tarot_art(db, owner):
    arts = [row[0] for row in db.execute("""
        SELECT art FROM tarot_seen WHERE owner=? GROUP BY art ORDER BY max(rowid) DESC
    """, (owner,))]
    if len(arts) < len(miniapp.ludmila_deck.TITLES):
        # Before we tracked turned cards, only published readings were saved in visit records.
        legacy_arts = db.execute("""
            SELECT json_extract(card.value, '$.art') AS art
            FROM visits v JOIN json_each(v.record, '$.cards') card
            WHERE v.owner=? AND NOT EXISTS (SELECT 1 FROM tarot_seen t WHERE t.owner=v.owner AND t.visit=v.id)
            GROUP BY art ORDER BY max(v.rowid) DESC
        """, (owner,))
        arts.extend(row[0] for row in legacy_arts if row[0] not in arts)
    return arts


def _leaderboard(db, owner, period):
    rows = db.execute("""
        WITH totals AS (
            SELECT v.owner, sum(json_extract(v.record, '$.fee')) AS earned, count(*) AS visits
            FROM visits v LEFT JOIN journey j ON j.id=v.id
            WHERE ?='all' OR j.finished >= CAST(strftime('%s','now','-7 days') AS INTEGER)
            GROUP BY v.owner
        ), ranked AS (
            SELECT *, rank() OVER (ORDER BY earned DESC) AS place FROM totals
        ), top AS (
            SELECT owner FROM ranked ORDER BY place, owner LIMIT 20
        )
        SELECT r.*, coalesce(nullif(n.name,''), p.name, 'Игрок') AS name, t.owner IS NOT NULL AS in_top
        FROM ranked r LEFT JOIN salon_names n ON n.owner=r.owner LEFT JOIN top t ON t.owner=r.owner
        LEFT JOIN salon_profiles p ON p.owner=r.owner
        WHERE t.owner IS NOT NULL OR r.owner=?
        ORDER BY r.place, r.owner
    """, (period, owner))
    leaders, own = [], None
    for row in rows:
        entry = {"name": row["name"], "place": row["place"], "earned": row["earned"], "visits": row["visits"],
                 "self": row["owner"] == owner}
        if row["in_top"]:
            leaders.append(entry)
        if entry["self"]:
            own = entry
    alias = db.execute("SELECT name FROM salon_names WHERE owner=?", (owner,)).fetchone()
    profile = db.execute("SELECT name FROM salon_profiles WHERE owner=?", (owner,)).fetchone()
    return {"leaders": leaders, "own": own, "alias": alias[0] if alias else "",
            "profileName": profile[0] if profile else ""}


def _seen_bonus(db, owner):
    return {row[0] for row in db.execute("SELECT DISTINCT story FROM journey WHERE owner=?", (owner,))
            if row[0] in miniapp.GUESTS}


class GuruVisits:
    def __init__(self, path, *, free_shop=False):
        self.path = path
        self.free_shop = free_shop
        self.shop_items = ({key: replace(item, price=0) for key, item in miniapp.ITEMS.items()}
                           if free_shop else miniapp.ITEMS)

    @contextmanager
    def _database(self, *, write=False):
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
            db.execute("CREATE INDEX IF NOT EXISTS journey_owner ON journey(owner)")
            db.execute("""CREATE TABLE IF NOT EXISTS tarot_seen (
                owner INTEGER NOT NULL, visit TEXT NOT NULL, art TEXT NOT NULL, PRIMARY KEY(owner, visit, art))""")
            db.execute("""CREATE TABLE IF NOT EXISTS purchases (
                owner INTEGER NOT NULL, item TEXT NOT NULL, price INTEGER NOT NULL, PRIMARY KEY(owner, item))""")
            db.execute("CREATE TABLE IF NOT EXISTS salon_names (owner INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS salon_profiles (
                owner INTEGER PRIMARY KEY, name TEXT NOT NULL, auth_date INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS chat_balances (
                owner INTEGER NOT NULL, chat TEXT NOT NULL, balance INTEGER NOT NULL,
                earned INTEGER NOT NULL, lost INTEGER NOT NULL, spent INTEGER NOT NULL, PRIMARY KEY(owner, chat))""")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db

    def import_chat_wallets(self, chats):
        """Import the legacy JSON ledger once per chat wallet, preserving its actual closing balance."""
        with self._database(write=True) as db:
            for chat, owners in chats.items():
                for owner, wallet in owners.items():
                    if not owner.isdecimal():
                        continue
                    result = db.execute("INSERT OR IGNORE INTO chat_balances VALUES (?, ?, ?, ?, ?, ?)",
                                        (owner, chat, wallet.balance, wallet.earned, wallet.lost, wallet.spent))
                    if result.rowcount:
                        db.executemany("INSERT OR IGNORE INTO purchases VALUES (?, ?, 0)",
                                       ((owner, item) for item in wallet.items))
                        if wallet.name:
                            db.execute("INSERT OR IGNORE INTO salon_profiles VALUES (?, ?, 0)", (owner, wallet.name))

    def wallet(self, owner):
        with self._database() as db:
            return _money(db, owner, _history(db, owner), _purchases(db, owner))

    def credit_chat(self, chat, owner, name, amount):
        with self._database(write=True) as db:
            db.execute("""INSERT INTO chat_balances VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT(owner, chat) DO UPDATE SET
                balance=balance+excluded.balance, earned=earned+excluded.earned, lost=lost+excluded.lost""",
                       (owner, chat, amount, max(amount, 0), max(-amount, 0)))
            if name:
                db.execute("INSERT OR IGNORE INTO salon_profiles VALUES (?, ?, 0)", (owner, name))
            return _money(db, owner, _history(db, owner), _purchases(db, owner))["balance"]

    def buy_from_chat(self, owner, name, item, price):
        with self._database(write=True) as db:
            purchases = _purchases(db, owner)
            if item in purchases or _money(db, owner, _history(db, owner), purchases)["balance"] < price:
                return False
            db.execute("INSERT INTO purchases VALUES (?, ?, ?)", (owner, item, price))
            if name:
                db.execute("INSERT OR IGNORE INTO salon_profiles VALUES (?, ?, 0)", (owner, name))
            return True

    def request(self, user_id, command, *, profile=None):
        op = command.get("op", "home")
        update_profile = profile and op in ("home", "leaderboard", "rename")
        read_only = op in ("home", "catalog", "card", "record", "interior", "shop", "leaderboard")
        with self._database(write=update_profile or not read_only) as db:
            if update_profile:
                _set_profile(db, user_id, *profile)
            if op in ("leaderboard", "rename"):
                period = command.get("period", "week")
                if period not in ("week", "all"):
                    raise ValueError("Неизвестный период рейтинга.")
                if op == "rename":
                    raw = command.get("name")
                    if not isinstance(raw, str):
                        raise ValueError("Введите имя для рейтинга.")
                    name = " ".join(raw.split())
                    if name and (len(name) > 28 or not name.isprintable()):
                        raise ValueError("Имя должно содержать до 28 печатных символов.")
                    db.execute("INSERT OR REPLACE INTO salon_names VALUES (?, ?)", (user_id, name))
                return _leaderboard(db, user_id, period)
            row = db.execute("SELECT * FROM salons WHERE owner=?", (user_id,)).fetchone()
            rev, active = (row["rev"], json.loads(row["active"])) if row else (0, None)
            if op == "catalog":
                completed = {entry[0] for entry in db.execute("SELECT DISTINCT story FROM visits WHERE owner=?", (user_id,))}
                return miniapp.catalog(completed | _seen_bonus(db, user_id))
            if op == "shop":
                return {"shop": miniapp.shop(self.shop_items), "freeShop": self.free_shop}
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
            changed = op in ("start", "greet", "move", "reply", "leave", "buy")
            if changed and command.get("rev") != rev:
                raise StaleTurn("Салон открыт ещё в одном окне. Показан последний ход.")
            history = _history(db, user_id)
            purchases = _purchases(db, user_id)
            if op == "interior":
                return {"salon": _interior(history, purchases, _money(db, user_id, history, purchases))}
            current = None
            if op == "start":
                if active:
                    raise ValueError("Сначала завершите текущий приём.")
                story = _pick_guest(db, user_id, history, random.Random())
                items = salon.owned_items(purchases, catalog=miniapp.ITEMS)
                active = miniapp.new_visit(story, (item.key for item in items), recently_seen=_recent_tarot_art(db, user_id))
                db.execute("INSERT INTO journey(id,owner,story,started) VALUES (?,?,?,strftime('%s','now'))",
                           (active["id"], user_id, story))
            elif op == "greet":
                if not active:
                    raise ValueError("Сеанс уже завершён.")
                active.pop("greeting_pending", None)
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
                active.pop("greeting_pending", None)
                if action.startswith("turn:"):
                    db.execute("INSERT OR IGNORE INTO tarot_seen VALUES (?, ?, ?)",
                               (user_id, active["id"], session.content.cards[session.card].art))
                if action.startswith(("tone:", "wd:", "nr:")):
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
                balance = _money(db, user_id, history, purchases)["balance"]
                refusal = salon.refusal(key, balance, purchases, catalog=self.shop_items)
                if refusal:
                    raise ValueError(refusal)
                item = self.shop_items[key]
                db.execute("INSERT INTO purchases VALUES (?, ?, ?)", (user_id, key, item.price))
                purchases[key] = item.price
            elif op != "home":
                raise ValueError("Неизвестное действие.")
            if changed:
                rev += 1
                db.execute("INSERT OR REPLACE INTO salons VALUES (?, ?, ?)", (user_id, rev, json.dumps(active)))
            if active and current is None:
                current = miniapp.scene(*miniapp.replay(active), greeting_pending=active.get("greeting_pending", False))
            if op == "reply" or (op == "move" and active is None):
                history = _history(db, user_id)
            opened = {row["story"] for row in history} | _seen_bonus(db, user_id)
            result = {"rev": rev, "current": current, "history": history, "count": len(miniapp.STORIES),
                      "openedCount": len(opened), "salon": _interior(history, purchases, _money(db, user_id, history, purchases))}
            if op == "reply" or (op == "move" and active is None):
                result["record"] = record
            return result
