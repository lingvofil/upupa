"""Касса игроков салона «Гуру»: копится от партии к партии и может уйти в минус.

Формат файла: ``{"<chat_id>": {"<user_id>": {"name": ..., "balance": ..., "earned": ..., "lost": ..., "spent": ..., "items": [...],
"gifts": [...], "endings": [...], "shifts": ..., "card_day": ..., "card_streak": ...}}}``.
Касса своя в каждом чате: тренировки в личке с ботом не влияют на таблицу группы.
Здесь же лежит то, что игрок копит помимо денег: счётчик смен и серия «карт дня».
Файл читается на startup (``load_guru_wallets``), а не при импорте.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field

from core.json_repository import JsonFileRepository, JsonRepository
from core.paths import GURU_WALLETS_PATH

logger = logging.getLogger(__name__)

# В кассах, заведённых до счётчика смен, его нет. Если в такой кассе уже были деньги,
# игрок не новичок: ставим заведомо больше любого порога новичка.
SEASONED_SHIFTS = 99


@dataclass
class Wallet:
    name: str = ""
    balance: int = 0
    earned: int = 0
    lost: int = 0
    spent: int = 0  # потрачено в лавке: это вложение, а не потеря
    items: list[str] = field(default_factory=list)  # купленное в лавке
    gifts: list[str] = field(default_factory=list)  # подарки клиентов: по одному от каждого типа
    endings: list[str] = field(default_factory=list)  # какие финалы сеанса игрок уже видел
    shifts: int = 0  # смены, в которых принят хотя бы один клиент
    card_day: str = ""  # день последней «карты дня» (ISO)
    card_streak: int = 0  # сколько дней подряд игрок тянет карту


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
        earned, lost = int(entry.get("earned", 0)), int(entry.get("lost", 0))
        return Wallet(
            name=str(entry.get("name", "")),
            balance=int(entry.get("balance", 0)),
            earned=earned,
            lost=lost,
            spent=int(entry.get("spent", 0)),
            items=[str(item) for item in entry.get("items", []) if isinstance(item, str)],
            gifts=[str(gift) for gift in entry.get("gifts", []) if isinstance(gift, str)],
            endings=[str(ending) for ending in entry.get("endings", []) if isinstance(ending, str)],
            shifts=int(entry.get("shifts", SEASONED_SHIFTS if earned or lost else 0)),
            card_day=str(entry.get("card_day", "")),
            card_streak=int(entry.get("card_streak", 0)),
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

    def add_gift(self, chat_id: int | str, user_id: int | str, name: str, gift: str) -> bool:
        """Поставить подарок клиента на полку; ``False``, если такой уже стоит."""
        wallet = self._chats.setdefault(str(chat_id), {}).setdefault(str(user_id), Wallet())
        if gift in wallet.gifts:
            return False
        wallet.name = name
        wallet.gifts.append(gift)
        return True

    def see_ending(self, chat_id: int | str, user_id: int | str, name: str, ending: str) -> int:
        """Запомнить увиденный финал сеанса; вернуть, сколько разных финалов игрок уже видел."""
        wallet = self._chats.setdefault(str(chat_id), {}).setdefault(str(user_id), Wallet())
        wallet.name = name
        if ending not in wallet.endings:
            wallet.endings.append(ending)
        return len(wallet.endings)

    def count_shift(self, chat_id: int | str, user_id: int | str, name: str) -> int:
        """Засчитать смену; вернуть, сколько их теперь."""
        wallet = self._chats.setdefault(str(chat_id), {}).setdefault(str(user_id), Wallet())
        wallet.name = name
        wallet.shifts += 1
        return wallet.shifts

    def claim_card(self, chat_id: int | str, user_id: int | str, name: str, day: str, previous_day: str) -> int | None:
        """Отметить «карту дня». Вернуть серию дней подряд или ``None``, если карта на ``day`` уже вытянута.

        Без await: проверка и отметка атомарны, двойное нажатие не даст чаевые дважды.
        """
        wallet = self._chats.setdefault(str(chat_id), {}).setdefault(str(user_id), Wallet())
        if wallet.card_day == day:
            return None
        wallet.name = name
        wallet.card_streak = wallet.card_streak + 1 if wallet.card_day == previous_day else 1
        wallet.card_day = day
        return wallet.card_streak

    def top(self, chat_id: int | str, limit: int = 10) -> list[Wallet]:
        """Самые богатые кассы чата. Кассы без оборота (только счётчики) в таблицу не идут."""
        wallets = [wallet for wallet in self._chats.get(str(chat_id), {}).values()
                   if wallet.balance or wallet.earned or wallet.lost or wallet.items]
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
