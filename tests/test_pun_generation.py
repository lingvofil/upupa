import os

# `AI.pun_generation` imports the normal project settings through the GigaChat
# image provider. CI has no private config, so provide harmless placeholders.
os.environ.setdefault("API_TOKEN", "123456789:AAFakeTokenForTestsOnly_abcdef")
os.environ.setdefault("GENERIC_API_KEY", "fake")
os.environ.setdefault("GOOGLE_API_KEY", "fake")
os.environ.setdefault("GIGACHAT_API_KEY", "fake")
os.environ.setdefault("GROQ_API_KEY", "fake")
os.environ.setdefault("POLLINATIONS_API_KEY", "fake")

from AI import pun_generation as puns


def test_parse_and_validate_real_overlap():
    candidate = puns.parse_pun_candidates("кабан+банан = кабанан")[0]
    assert candidate.first == "кабан"
    assert candidate.second == "банан"
    assert candidate.result == "кабанан"
    assert puns.is_candidate_acceptable(candidate)


def test_reject_plain_concatenation_without_overlap():
    candidate = puns.parse_pun_candidates("кот+лампа = котлампа")[0]
    assert not puns.is_candidate_acceptable(candidate)


def test_reject_known_cliches():
    hippo = puns.parse_pun_candidates("бегемот+мотоцикл = бегемотоцикл")[0]
    penguin = puns.parse_pun_candidates("пингвин+виноград = пингвиноград")[0]
    assert not puns.is_candidate_acceptable(hippo)
    assert not puns.is_candidate_acceptable(penguin)


def test_reject_recent_result_and_pair():
    recent = ["кабан+банан = кабанан"]
    same = puns.parse_pun_candidates("кабан+банан = кабанан")[0]
    assert not puns.is_candidate_acceptable(same, recent)


def test_parser_accepts_numbered_model_output():
    parsed = puns.parse_pun_candidates(
        "1. кабан+банан = кабанан\n"
        "2) нарвал+валик = нарвалик\n"
    )
    assert [candidate.result for candidate in parsed] == ["кабанан", "нарвалик"]


def test_history_survives_reload(tmp_path, monkeypatch):
    history_file = tmp_path / "pun_history.json"
    monkeypatch.setattr(puns, "_HISTORY_FILE", history_file)

    puns._remember("-100", "кабан+банан = кабанан")
    puns._remember("-100", "нарвал+валик = нарвалик")

    assert puns._get_recent("-100") == [
        "кабан+банан = кабанан",
        "нарвал+валик = нарвалик",
    ]
