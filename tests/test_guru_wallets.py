"""Касса игроков салона: накопление, потери, хранение."""

import asyncio
from types import SimpleNamespace

from features.guru_wallets import Wallet, WalletBook
from games.guru import textui

CHAT = -100
OTHER_CHAT = -200


class MemoryRepository:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error
        self.saved = []

    def load(self):
        if self.error:
            raise self.error
        if self.data is None:
            raise FileNotFoundError
        return self.data

    def save(self, value):
        if isinstance(self.error, OSError):
            raise self.error
        self.saved.append(value)


def book(data=None):
    instance = WalletBook(MemoryRepository(data))
    instance.load()
    return instance


def test_money_accumulates_and_can_be_lost():
    wallets = book()
    assert wallets.apply(CHAT, 1, "Зоя", 1500) == 1500
    assert wallets.apply(CHAT, 1, "Зоя", -2000) == -500
    wallet = wallets.get(CHAT, 1)
    assert (wallet.balance, wallet.earned, wallet.lost) == (-500, 1500, 2000)


def test_each_chat_has_its_own_wallet():
    wallets = book()
    wallets.apply(CHAT, 1, "Зоя", 1500)
    assert wallets.balance(OTHER_CHAT, 1) == 0 and wallets.balance(CHAT, 2) == 0


def test_top_is_sorted_and_limited():
    wallets = book()
    for user, amount in enumerate((100, -50, 900, 300)):
        wallets.apply(CHAT, user, f"u{user}", amount)
    assert [w.balance for w in wallets.top(CHAT, limit=3)] == [900, 300, 100]


def test_round_trip_through_repository():
    repository = MemoryRepository()
    wallets = WalletBook(repository)
    wallets.load()
    wallets.apply(CHAT, 1, "Зоя", 700)
    asyncio.run(wallets.persist())

    restored = WalletBook(MemoryRepository(repository.saved[-1]))
    restored.load()
    assert restored.get(CHAT, 1) == Wallet("Зоя", 700, 700, 0)


def test_broken_file_means_empty_wallets():
    for repository in (MemoryRepository(error=ValueError("bad")), MemoryRepository(["not", "a", "dict"])):
        wallets = WalletBook(repository)
        wallets.load()
        assert wallets.top(CHAT) == []


def test_failed_save_does_not_stop_the_game():
    wallets = WalletBook(MemoryRepository(error=OSError("disk full")))
    wallets._chats = {}
    wallets.apply(CHAT, 1, "Зоя", 100)
    asyncio.run(wallets.persist())
    assert wallets.balance(CHAT, 1) == 100


def test_table_text():
    assert "пусто" in textui.render_wallets([], Wallet())
    rows = [SimpleNamespace(name="Зоя", balance=12300, earned=15000, lost=2700),
            SimpleNamespace(name="Гоша", balance=-1500, earned=0, lost=1500, spent=0, items=[])]
    text = textui.render_wallets(rows, rows[1])
    assert "1. Зоя — 12 300 ₽" in text and "2. Гоша — −1 500 ₽" in text and "потеряно 1 500 ₽" in text
