"""Tolerant parsing and bounded retries for DnD generated option lists."""
from __future__ import annotations

import json
import re


_NUMBERED_LINE_RE = re.compile(r"^\s*(?:\d+\s*[.)\]:-]|[-•])\s*(.+?)\s*$")
_INLINE_NUMBER_RE = re.compile(r"(?:^|\s)(\d+)\s*[.)\]:-]\s*")
_CONTAINER_KEYS = ("options", "plots", "variants", "ideas", "items")


def _clean(campaign, value):
    cleaner = getattr(campaign, "_clean_generated_value", None)
    if cleaner is None:
        text = " ".join(str(value or "").split()).strip(" \t\r\n\"'`*-—–")
        return text or None
    return cleaner(value)


def _json_candidates(campaign, text: str) -> list[str | None]:
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    payloads = [fenced]
    bracket = re.search(r"\[[\s\S]*\]", fenced)
    if bracket and bracket.group(0) != fenced:
        payloads.append(bracket.group(0))
    brace = re.search(r"\{[\s\S]*\}", fenced)
    if brace and brace.group(0) != fenced:
        payloads.append(brace.group(0))

    for payload in payloads:
        try:
            data = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            return [_clean(campaign, item) for item in data]
        if isinstance(data, dict):
            for key in _CONTAINER_KEYS:
                value = data.get(key)
                if isinstance(value, list):
                    return [_clean(campaign, item) for item in value]
            numeric = []
            for key, value in data.items():
                if str(key).strip().isdigit():
                    numeric.append((int(str(key).strip()), value))
            if numeric:
                numeric.sort(key=lambda item: item[0])
                return [_clean(campaign, value) for _, value in numeric]
    return []


def _numbered_candidates(campaign, text: str) -> list[str | None]:
    values = []
    for line in text.splitlines():
        match = _NUMBERED_LINE_RE.match(line)
        if match:
            values.append(_clean(campaign, match.group(1)))
    if values:
        return values

    matches = list(_INLINE_NUMBER_RE.finditer(text))
    if len(matches) >= 2:
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            values.append(_clean(campaign, text[start:end]))
    return values


def robust_extract_list_payload(campaign, raw):
    text = campaign.ACTION_RE.sub("", campaign.META_RE.sub("", str(raw or ""))).strip()
    if not text:
        return []

    values = _json_candidates(campaign, text)
    if values:
        return values

    values = _numbered_candidates(campaign, text)
    if values:
        return values

    plain = []
    for line in text.splitlines():
        cleaned = _clean(campaign, line)
        if not cleaned:
            continue
        if cleaned.casefold().rstrip(":") in {
            "варианты",
            "варианты сюжета",
            "сюжеты",
            "идеи",
        }:
            continue
        plain.append(cleaned)
    return plain if len(plain) == 5 else []


def configure_dnd_plot_resilience(campaign) -> None:
    """Accept common model formats and avoid three expensive retries."""
    if getattr(campaign, "_upupa_dnd_plot_resilience_configured", False):
        return

    def extract(raw):
        return robust_extract_list_payload(campaign, raw)

    campaign._extract_list_payload = extract
    campaign.PLOT_GENERATION_RETRIES = min(
        int(getattr(campaign, "PLOT_GENERATION_RETRIES", 3) or 3),
        2,
    )
    campaign._upupa_dnd_plot_resilience_configured = True


__all__ = ["configure_dnd_plot_resilience", "robust_extract_list_payload"]
