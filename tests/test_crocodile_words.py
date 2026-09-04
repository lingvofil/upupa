from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORDS_FILE = ROOT / "games" / "crocowords.txt"


def _words() -> list[str]:
    return [
        line.strip()
        for line in WORDS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_crocodile_dictionary_has_large_complex_phrase_pool():
    words = _words()
    normalized = {word.lower().replace("ё", "е") for word in words}
    multiword = [word for word in words if len(word.split()) >= 2]

    assert len(normalized) >= 400
    assert len(multiword) >= 90

    expected_hard_words = {
        "бермудский треугольник",
        "квантовый компьютер",
        "портал в другое измерение",
        "слон в посудной лавке",
        "битва с ветряными мельницами",
        "экспедиция на марс",
    }
    assert expected_hard_words <= normalized
