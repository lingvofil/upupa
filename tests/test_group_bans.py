from features import group_bans


class MemoryRepository:
    def __init__(self, data=None):
        self.data = data

    def load(self):
        if self.data is None:
            raise FileNotFoundError
        return self.data

    def save(self, data):
        self.data = {key: dict(value) for key, value in data.items()}


def setup_function():
    group_bans._group_bans.clear()


def test_group_ban_persists_and_matches_title_username_and_link(monkeypatch):
    repo = MemoryRepository({})
    monkeypatch.setattr(group_bans, "_repository", lambda: repo)

    group_bans.ban_group(-100123, "Brawlhalla", "brawlhallaz")

    assert group_bans.is_group_banned(-100123)
    assert group_bans.find_group_ban("Brawlhalla")["id"] == -100123
    assert group_bans.find_group_ban("@brawlhallaz")["id"] == -100123
    assert group_bans.find_group_ban("https://t.me/brawlhallaz")["id"] == -100123
    assert repo.data["-100123"]["username"] == "brawlhallaz"


def test_only_explicit_unban_removes_group(monkeypatch):
    repo = MemoryRepository({})
    monkeypatch.setattr(group_bans, "_repository", lambda: repo)
    group_bans.ban_group(-100123, "Brawlhalla", "brawlhallaz")

    removed = group_bans.unban_group("@brawlhallaz")

    assert removed["id"] == -100123
    assert not group_bans.is_group_banned(-100123)
    assert repo.data == {}
