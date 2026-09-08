from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORDS_FILE = ROOT / "games" / "crocowords.txt"


def _single_words() -> set[str]:
    return {
        line.strip().lower().replace("ё", "е")
        for line in WORDS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and len(line.strip().split()) == 1
    }


def test_crocodile_single_word_pool_is_substantially_expanded():
    words = _single_words()

    assert len(words) >= 950
    assert {
        "альбатрос",
        "капибара",
        "каракал",
        "круассан",
        "ленивец",
        "нарвал",
        "росомаха",
        "сурикат",
        "тукан",
        "фламинго",
        "шиншилла",
        "штангенциркуль",
    } <= words
