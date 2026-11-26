"""Persistent shared Sasulka scores, keyed by Telegram user ID."""

from contextlib import closing, contextmanager
import sqlite3


class SasulkaRanking:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def _transaction(self, identity, username):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    user_id INTEGER PRIMARY KEY, username TEXT NOT NULL,
                    auth_date INTEGER NOT NULL, record INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS wins (
                    user_id INTEGER NOT NULL, level TEXT NOT NULL, seed INTEGER NOT NULL,
                    points INTEGER NOT NULL, PRIMARY KEY (user_id, level, seed)
                )
            """)
            db.execute("""
                INSERT INTO players (user_id, username, auth_date) VALUES (?, ?, ?)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = excluded.username, auth_date = excluded.auth_date
                WHERE excluded.auth_date >= players.auth_date
            """, (identity.user_id, username, identity.auth_date))
            yield db

    def ranking(self, identity, username, sort):
        order = {"record": "record", "solved": "solved"}.get(sort, "points")
        with self._transaction(identity, username) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(f"""
                SELECT '@' || username AS name, record, COUNT(wins.level) AS solved,
                       record * 2 + COALESCE(SUM(wins.points), 0) AS points
                FROM players LEFT JOIN wins USING (user_id)
                WHERE username <> ''
                GROUP BY players.user_id
                HAVING points > 0
                ORDER BY {order} DESC, points DESC, record DESC, solved DESC, players.user_id
                LIMIT 30
            """).fetchall()
        return [dict(row) for row in rows]

    def submit(self, identity, username, level, seed, length, points):
        with self._transaction(identity, username) as db:
            if level == "record":
                current, = db.execute("SELECT record FROM players WHERE user_id = ?",
                                      (identity.user_id,)).fetchone()
                if length > current:
                    db.execute("UPDATE players SET record = ? WHERE user_id = ?",
                               (length, identity.user_id))
                return 2 * max(0, length - current)

            key = (identity.user_id, level, seed)
            previous = db.execute("SELECT points FROM wins WHERE user_id = ? AND level = ? AND seed = ?",
                                  key).fetchone()
            current = previous[0] if previous else 0
            if points > current:
                db.execute("""
                    INSERT INTO wins (user_id, level, seed, points) VALUES (?, ?, ?, ?)
                    ON CONFLICT (user_id, level, seed) DO UPDATE SET points = excluded.points
                """, (*key, points))
            return max(0, points - current)
