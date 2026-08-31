from features.content_filter import SPAM_PATTERNS


def _matches_spam_pattern(text: str) -> bool:
    return any(pattern.search(text) for pattern in SPAM_PATTERNS)


def test_plain_bot_mention_is_not_spam() -> None:
    assert not _matches_spam_pattern("❤️ @allsaverbot")
    assert not _matches_spam_pattern("спасибо @somehelperbot")


def test_explicit_spam_bot_rule_is_preserved() -> None:
    assert _matches_spam_pattern("@Amofitlifebot")


def test_existing_spam_patterns_still_match() -> None:
    assert _matches_spam_pattern("доход 100$")
    assert _matches_spam_pattern("пиши в лс")
