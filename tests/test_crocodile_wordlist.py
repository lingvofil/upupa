from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORDS_FILE = ROOT / "games" / "crocowords.txt"


def _words() -> list[str]:
    return [
        line.strip()
        for line in WORDS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_crocodile_dictionary_is_colocated_with_game():
    assert WORDS_FILE.is_file()
    assert not (ROOT / "crocowords.txt").exists()


def test_crocodile_dictionary_is_full_not_fallback_subset():
    words = _words()
    lowered = {word.lower() for word in words}

    assert len(words) > 250
    assert {"упупа", "альпинист", "якорь"} <= lowered
