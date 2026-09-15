from AI import dnd_campaign as campaign
from AI import dnd_plot_resilience as resilience


def test_parser_accepts_json_object_with_options():
    raw = '{"options":["порт", "лес", "поезд", "ярмарка", "станция"]}'

    assert resilience.robust_extract_list_payload(campaign, raw) == [
        "порт",
        "лес",
        "поезд",
        "ярмарка",
        "станция",
    ]


def test_parser_accepts_fenced_json_array():
    raw = "```json\n[\"а\", \"б\", \"в\", \"г\", \"д\"]\n```"

    assert resilience.robust_extract_list_payload(campaign, raw) == ["а", "б", "в", "г", "д"]


def test_parser_accepts_inline_numbered_options():
    raw = "1) порт 2) лес 3) поезд 4) ярмарка 5) станция"

    assert resilience.robust_extract_list_payload(campaign, raw) == [
        "порт",
        "лес",
        "поезд",
        "ярмарка",
        "станция",
    ]


def test_configure_reduces_plot_retries_and_replaces_shared_list_parser(monkeypatch):
    fake = type("Campaign", (), {})()
    fake.ACTION_RE = campaign.ACTION_RE
    fake.META_RE = campaign.META_RE
    fake._clean_generated_value = campaign._clean_generated_value
    fake.PLOT_GENERATION_RETRIES = 3

    resilience.configure_dnd_plot_resilience(fake)

    assert fake.PLOT_GENERATION_RETRIES == 2
    assert fake._extract_list_payload("1. a\n2. b\n3. c\n4. d\n5. e") == [
        "a",
        "b",
        "c",
        "d",
        "e",
    ]
