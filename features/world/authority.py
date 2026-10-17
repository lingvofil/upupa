"""International authority for World of Upupa — the single source of truth.

Authority = diplomatic base + modifiers.  Every screen (hub, state card, state
list, rating, map) shows :pyattr:`AuthorityBreakdown.total`, so the same state
never has two different numbers.

Base (pure function, kept for the diplomatic core):
    50 + 8 per active ally − 5 per active war, floor 0, no upper cap.

Modifiers:
    🕰 alliance age      +1 per full week of each alliance, max +4 per alliance
    🧱 bloc              +2 per other member of the state's bloc, max +10
    🚫 sanctions         −3 per incoming active package, max −18
    🗡 betrayal          −10 per alliance broken (or ally attacked) in 7 days, max −30
    🏳 capitulation      −10 per capitulation in 14 days
    ⚔️ battles           +5 per battle won (max +15), −3 per battle lost (max −9) in 30 days
    🧳 tourism           (average guest rating − 3) × 3, ±6, needs 2+ votes in 30 days
    🎁 gifts             +1 per received gift in 30 days, max +5
    ⚖️ court             −5 lost claim, −2 partially lost, −3 frivolous claim; max −10 in 14 days
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from features.world.models import WorldProfile


BASE_AUTHORITY = 50
ALLY_BONUS = 8
WAR_PENALTY = 5
ALLIANCE_WEEK_BONUS = 1
ALLIANCE_AGE_CAP = 4
BLOC_MEMBER_BONUS = 2
BLOC_CAP = 10
SANCTION_PENALTY = 3
SANCTION_CAP = 18
BETRAYAL_PENALTY = 10
BETRAYAL_DAYS = 7
BETRAYAL_CAP = 30
CAPITULATION_PENALTY = 10
CAPITULATION_DAYS = 14
BATTLE_WIN_BONUS = 5
BATTLE_WIN_CAP = 15
BATTLE_LOSS_PENALTY = 3
BATTLE_LOSS_CAP = 9
BATTLE_DAYS = 30
TOURISM_FACTOR = 3
TOURISM_CAP = 6
TOURISM_MIN_VOTES = 2
TOURISM_DAYS = 30
GIFT_BONUS = 1
GIFT_CAP = 5
GIFT_DAYS = 30
COURT_LOST = 5
COURT_PARTIAL = 2
COURT_FRIVOLOUS = 3
COURT_CAP = 10
COURT_DAYS = 14


def authority_from_counts(allies: int, wars: int) -> int:
    """Diplomatic base without an upper cap; zero remains the natural floor."""
    return max(0, BASE_AUTHORITY + ALLY_BONUS * int(allies) - WAR_PENALTY * int(wars))


def calculate_authority(profile: WorldProfile) -> int:
    """Diplomatic base of a profile (without modifiers)."""
    return authority_from_counts(len(profile.allies), len(profile.wars))


@dataclass(frozen=True)
class AuthorityBreakdown:
    base: int
    alliance_age: int = 0
    bloc: int = 0
    sanctions: int = 0
    betrayal: int = 0
    capitulation: int = 0
    battles: int = 0
    tourism: int = 0
    gifts: int = 0
    court: int = 0

    @property
    def modifiers(self) -> int:
        return (
            self.alliance_age
            + self.bloc
            + self.sanctions
            + self.betrayal
            + self.capitulation
            + self.battles
            + self.tourism
            + self.gifts
            + self.court
        )

    @property
    def total(self) -> int:
        return max(0, self.base + self.modifiers)

    def explain(self) -> list[str]:
        """Human-readable non-zero modifiers for the state card."""
        labels = (
            ("🕰 давность союзов", self.alliance_age),
            ("🧱 блок", self.bloc),
            ("🚫 санкции", self.sanctions),
            ("🗡 предательства", self.betrayal),
            ("🏳 капитуляции", self.capitulation),
            ("⚔️ битвы", self.battles),
            ("🧳 туризм", self.tourism),
            ("🎁 дары", self.gifts),
            ("⚖️ суд", self.court),
        )
        return [f"{label}: {value:+d}" for label, value in labels if value]


@dataclass
class AuthorityInputs:
    incoming_sanctions: dict[int, int] = field(default_factory=dict)
    betrayals: dict[int, int] = field(default_factory=dict)
    capitulations: dict[int, int] = field(default_factory=dict)
    battle_wins: dict[int, int] = field(default_factory=dict)
    battle_losses: dict[int, int] = field(default_factory=dict)
    tourism: dict[int, tuple[float, int]] = field(default_factory=dict)
    gifts: dict[int, int] = field(default_factory=dict)
    court_penalty: dict[int, int] = field(default_factory=dict)
    bloc_sizes: dict[int, int] = field(default_factory=dict)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _since(now: datetime, days: int) -> str:
    return (now - timedelta(days=days)).isoformat()


def load_authority_inputs(path: str | Path, *, now: datetime | None = None) -> AuthorityInputs:
    """Aggregate every modifier with a handful of indexed queries (one connection)."""
    current = now or datetime.now(timezone.utc)
    inputs = AuthorityInputs()
    with closing(sqlite3.connect(Path(path), timeout=30)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        if _table_exists(conn, "world_sanctions"):
            for row in conn.execute(
                """
                SELECT target_state, COUNT(*) AS n FROM world_sanctions
                WHERE lifted_at IS NULL AND (expires_at IS NULL OR expires_at > ?)
                GROUP BY target_state
                """,
                (current.isoformat(),),
            ):
                inputs.incoming_sanctions[int(row["target_state"])] = int(row["n"])
        if _table_exists(conn, "world_events"):
            for row in conn.execute(
                """
                SELECT actor_state, COUNT(*) AS n FROM world_events
                WHERE created_at >= ? AND actor_state IS NOT NULL AND (
                    event_type = 'alliance_broken'
                    OR (event_type = 'war_declared'
                        AND json_extract(payload_json, '$.previous_relation') = 'allied')
                )
                GROUP BY actor_state
                """,
                (_since(current, BETRAYAL_DAYS),),
            ):
                inputs.betrayals[int(row["actor_state"])] = int(row["n"])
            for row in conn.execute(
                """
                SELECT actor_state, COUNT(*) AS n FROM world_events
                WHERE event_type = 'war_capitulated' AND created_at >= ? AND actor_state IS NOT NULL
                GROUP BY actor_state
                """,
                (_since(current, CAPITULATION_DAYS),),
            ):
                inputs.capitulations[int(row["actor_state"])] = int(row["n"])
            for row in conn.execute(
                """
                SELECT target_state, COUNT(*) AS n FROM world_events
                WHERE event_type = 'state_gift' AND created_at >= ? AND target_state IS NOT NULL
                GROUP BY target_state
                """,
                (_since(current, GIFT_DAYS),),
            ):
                inputs.gifts[int(row["target_state"])] = int(row["n"])
            court = defaultdict(int)
            for row in conn.execute(
                """
                SELECT actor_state, target_state, json_extract(payload_json, '$.outcome') AS outcome
                FROM world_events
                WHERE event_type = 'international_court_verdict' AND created_at >= ?
                """,
                (_since(current, COURT_DAYS),),
            ):
                outcome = str(row["outcome"] or "")
                if outcome == "satisfied" and row["target_state"] is not None:
                    court[int(row["target_state"])] += COURT_LOST
                elif outcome == "partial" and row["target_state"] is not None:
                    court[int(row["target_state"])] += COURT_PARTIAL
                elif outcome == "rejected" and row["actor_state"] is not None:
                    court[int(row["actor_state"])] += COURT_FRIVOLOUS
            inputs.court_penalty = dict(court)
        if _table_exists(conn, "world_battles"):
            for row in conn.execute(
                """
                SELECT state_a, state_b, winner FROM world_battles
                WHERE finished_at IS NOT NULL AND finished_at >= ? AND winner IS NOT NULL
                """,
                (_since(current, BATTLE_DAYS),),
            ):
                winner = int(row["winner"])
                loser = int(row["state_b"]) if winner == int(row["state_a"]) else int(row["state_a"])
                inputs.battle_wins[winner] = inputs.battle_wins.get(winner, 0) + 1
                inputs.battle_losses[loser] = inputs.battle_losses.get(loser, 0) + 1
        if _table_exists(conn, "world_visit_ratings"):
            for row in conn.execute(
                """
                SELECT host_state, AVG(rating) AS avg_rating, COUNT(*) AS votes
                FROM world_visit_ratings WHERE created_at >= ?
                GROUP BY host_state
                """,
                (_since(current, TOURISM_DAYS),),
            ):
                inputs.tourism[int(row["host_state"])] = (float(row["avg_rating"]), int(row["votes"]))
        if _table_exists(conn, "world_bloc_members") and _table_exists(conn, "world_blocs"):
            for row in conn.execute(
                """
                SELECT m.world_id, (
                    SELECT COUNT(*) FROM world_bloc_members other WHERE other.bloc_id = m.bloc_id
                ) AS size
                FROM world_bloc_members m JOIN world_blocs b ON b.id = m.bloc_id
                WHERE b.dissolved_at IS NULL
                """
            ):
                inputs.bloc_sizes[int(row["world_id"])] = int(row["size"])
    return inputs


def tourism_modifier(avg_rating: float | None, votes: int) -> int:
    if avg_rating is None or votes < TOURISM_MIN_VOTES:
        return 0
    return max(-TOURISM_CAP, min(TOURISM_CAP, round((avg_rating - 3) * TOURISM_FACTOR)))


def build_breakdown(
    world_id: int,
    *,
    allies: int,
    wars: int,
    alliance_since: list[datetime | None],
    inputs: AuthorityInputs,
    now: datetime | None = None,
) -> AuthorityBreakdown:
    current = now or datetime.now(timezone.utc)
    age = 0
    for since in alliance_since:
        if since is None:
            continue
        weeks = max(0, (current - since).days // 7)
        age += min(ALLIANCE_AGE_CAP, weeks * ALLIANCE_WEEK_BONUS)
    bloc_size = inputs.bloc_sizes.get(world_id, 0)
    avg, votes = inputs.tourism.get(world_id, (None, 0))
    return AuthorityBreakdown(
        base=authority_from_counts(allies, wars),
        alliance_age=age,
        bloc=min(BLOC_CAP, BLOC_MEMBER_BONUS * max(0, bloc_size - 1)),
        sanctions=-min(SANCTION_CAP, SANCTION_PENALTY * inputs.incoming_sanctions.get(world_id, 0)),
        betrayal=-min(BETRAYAL_CAP, BETRAYAL_PENALTY * inputs.betrayals.get(world_id, 0)),
        capitulation=-CAPITULATION_PENALTY * inputs.capitulations.get(world_id, 0),
        battles=(
            min(BATTLE_WIN_CAP, BATTLE_WIN_BONUS * inputs.battle_wins.get(world_id, 0))
            - min(BATTLE_LOSS_CAP, BATTLE_LOSS_PENALTY * inputs.battle_losses.get(world_id, 0))
        ),
        tourism=tourism_modifier(avg, votes),
        gifts=min(GIFT_CAP, GIFT_BONUS * inputs.gifts.get(world_id, 0)),
        court=-min(COURT_CAP, inputs.court_penalty.get(world_id, 0)),
    )

