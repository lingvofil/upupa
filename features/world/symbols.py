"""Persistent state symbols and prompt construction for World of Upupa."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from features.world.ledger import WorldDetails
from features.world.models import WorldState


SYMBOL_KINDS = {"flag", "emblem"}


@dataclass(frozen=True)
class WorldSymbol:
    world_id: int
    kind: str
    telegram_file_id: str
    prompt: str
    updated_at: datetime


class WorldSymbolStore:
    """SQLite storage for one currently approved symbol per state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_state_symbols (
                    world_id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    telegram_file_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(world_id) REFERENCES world_states(world_id) ON DELETE CASCADE
                )
                """
            )

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> WorldSymbol | None:
        if row is None:
            return None
        return WorldSymbol(
            world_id=int(row["world_id"]),
            kind=str(row["kind"]),
            telegram_file_id=str(row["telegram_file_id"]),
            prompt=str(row["prompt"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def get_symbol(self, world_id: int) -> WorldSymbol | None:
        self.init_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_state_symbols WHERE world_id = ?",
                (int(world_id),),
            ).fetchone()
        return self._from_row(row)

    def set_symbol(
        self,
        world_id: int,
        kind: str,
        telegram_file_id: str,
        prompt: str,
    ) -> WorldSymbol:
        if kind not in SYMBOL_KINDS:
            raise ValueError(f"Unsupported world symbol kind: {kind}")
        file_id = telegram_file_id.strip()
        if not file_id:
            raise ValueError("Telegram file_id cannot be empty")
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Symbol prompt cannot be empty")
        self.init_schema()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO world_state_symbols(world_id, kind, telegram_file_id, prompt, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(world_id) DO UPDATE SET
                    kind = excluded.kind,
                    telegram_file_id = excluded.telegram_file_id,
                    prompt = excluded.prompt,
                    updated_at = excluded.updated_at
                """,
                (int(world_id), kind, file_id, clean_prompt, now),
            )
            row = conn.execute(
                "SELECT * FROM world_state_symbols WHERE world_id = ?",
                (int(world_id),),
            ).fetchone()
        symbol = self._from_row(row)
        if symbol is None:  # pragma: no cover - defensive guard
            raise RuntimeError("Failed to read saved state symbol")
        return symbol


def symbol_kind_label(kind: str) -> str:
    return "флаг" if kind == "flag" else "герб"


def build_state_symbol_prompt(
    state: WorldState,
    details: WorldDetails | None,
    kind: str,
    *,
    idea: str | None = None,
) -> str:
    """Build a strict visual prompt so the model draws heraldry, not a random scene."""
    if kind not in SYMBOL_KINDS:
        raise ValueError(f"Unsupported world symbol kind: {kind}")

    traits: list[str] = [f"Название государства: {state.title}."]
    if details is not None:
        traits.extend(
            [
                f"Государственный строй: {details.government_form}.",
                f"Климат: {details.climate}.",
                f"Главная угроза: {details.main_threat}.",
            ]
        )
    if idea and idea.strip():
        traits.append(f"Дополнительная идея граждан: {idea.strip()[:500]}.")

    if kind == "flag":
        format_rules = (
            "Нарисуй ИМЕННО ГОСУДАРСТВЕННЫЙ ФЛАГ. Это должна быть плоская векторная "
            "композиция прямоугольного флага примерно 3:2, целиком заполняющая кадр. "
            "Не рисуй древко, ветер, складки ткани, комнату, пейзаж, людей, мокап или фотографию флага. "
            "Используй 2–4 основных цвета и один ясный центральный символ или простую систему полос/полей."
        )
    else:
        format_rules = (
            "Нарисуй ИМЕННО ГОСУДАРСТВЕННЫЙ ГЕРБ. Это должна быть чистая плоская геральдическая "
            "эмблема: щит и/или лаконичные геральдические элементы, фронтально, по центру, без окружения. "
            "Не рисуй здание, пейзаж, людей, флаг на древке, мокап, печать на бумаге или фотографию предмета."
        )

    return "\n".join(
        [
            format_rules,
            "Стиль: убедительная официальная государственная символика, но с одним заметным абсурдным или сатирическим визуальным приколом, характерным именно для этого государства.",
            "Прикол должен читаться через изображение, а не через надпись. Не добавляй текст, буквы, цифры, девизы, подписи, логотипы и водяные знаки.",
            "Не превращай результат в мем-картинку: сначала это должен быть правдоподобный флаг/герб, и только потом — смешная деталь.",
            "Композиция должна оставаться узнаваемой в маленьком размере и выглядеть как единый официальный символ.",
            *traits,
        ]
    )
