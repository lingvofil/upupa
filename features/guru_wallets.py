"""Касса игроков салона «Гуру»: копится от партии к партии и может уйти в минус.

Формат файла: ``{"<chat_id>": {"<user_id>": {"name": ..., "balance": ..., "earned": ..., "lost": ..., "spent": ..., "items": [...]}}}``.
Касса своя в каждом чате: тренировки в личке с ботом не влияют на таблицу группы.
Файл читается на startup (``load_guru_wallets``), а не при импорте.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field

from core.json_repository import JsonFileRepository, JsonRepository
from core.paths import GURU_WALLETS_PATH

logger = logging.getLogger(__name__)


@dataclass
class Wallet:
    name: str = ""
    balance: int = 0
    earned: int = 0
    lost: int = 0
    spent: int = 0  # потрачено в лавке: это вложение, а не потеря
    items: list[str] = field(default_factory=list)  # купленное в лавке


class WalletBook:
    def __init__(self, repository: JsonRepository) -> None:
        self._repository = repository
        self._chats: dict[str, dict[str, Wallet]] = {}
        self._save_lock: asyncio.Lock | None = None

    def load(self) -> None:
        """Прочитать кассы; отсутствующий или битый файл — все кассы пусты."""
        try:
            data = self._repository.load()
        except FileNotFoundError:
            self._chats = {}
            return
        except Exception as exc:
            logger.error("[guru] не удалось прочитать кассы игроков: %s", exc)
            self._chats = {}
            return
        if not isinstance(data, dict):
            logger.warning("[guru] файл касс повреждён, кассы пусты")
            self._chats = {}
            return
        self._chats = {
            str(chat): {str(user): self._parse(entry) for user, entry in users.items() if isinstance(entry, dict)}
            for chat, users in data.items()
            if isinstance(users, dict)
        }

    @staticmethod
    def _parse(entry: dict) -> Wallet:
        return Wallet(
            name=str(entry.get("name", "")),
            balance=int(entry.get("balance", 0)),
            earned=int(entry.get("earned", 0)),
            lost=int(entry.get("lost", 0)),
            spent=int(entry.get("spent", 0)),
            items=[str(item) for item in entry.get("items", []) if isinstance(item, str)],
        )

    def get(self, chat_id: int | str, user_id: int | str) -> Wallet:
        return self._chats.get(str(chat_id), {}).get(str(user_id), Wallet())

    def balance(self, chat_id: int | str, user_id: int | str) -> int:
        return self.get(chat_id, user_id).balance

    def apply(self, chat_id: int | str, user_id: int | str, name: str, delta: int) -> int:
        """Зачислить или списать; вернуть новый баланс."""
        wallet = self._chats.setdefault(str(chat_id), {}).setdefault(str(user_id), Wallet())
        wallet.name = name
        wallet.balance += delta
        if delta > 0:
            wallet.earned += delta
        else:
            wallet.lost -= delta
        return wallet.balance

    def buy(self, chat_id: int | str, user_id: int | str, name: str, item: str, price: int) -> bool:
        """Купить вещь. Без await: проверка и списание атомарны, двойное нажатие не спишет дважды."""
        wallet = self._chats.setdefault(str(chat_id), {}).setdefault(str(user_id), Wallet())
        if item in wallet.items or wallet.balance < price:
            return False
        wallet.name = name
        wallet.balance -= price
        wallet.spent += price
        wallet.items.append(item)
        return True

    def top(self, chat_id: int | str, limit: int = 10) -> list[Wallet]:
        wallets = self._chats.get(str(chat_id), {}).values()
        return sorted(wallets, key=lambda wallet: wallet.balance, reverse=True)[:limit]

    def snapshot(self) -> dict[str, dict[str, dict]]:
        return {
            chat: {user: asdict(wallet) for user, wallet in users.items()}
            for chat, users in self._chats.items()
        }

    async def persist(self) -> None:
        """Сохранить на диск вне event loop. Ошибка записи не должна останавливать игру."""
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()
        async with self._save_lock:
            snapshot = self.snapshot()
            try:
                await asyncio.to_thread(self._repository.save, snapshot)
            except Exception:
                logger.exception("[guru] не удалось сохранить кассы игроков")


_wallets = WalletBook(JsonFileRepository(GURU_WALLETS_PATH))


def get_guru_wallets() -> WalletBook:
    return _wallets


def load_guru_wallets() -> None:
    """Явная загрузка на startup приложения."""
    _wallets.load()
