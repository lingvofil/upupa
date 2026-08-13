import re


_CONFIDENCE_LINE_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?"
    r"(?:моя\s+)?(?:уверенность|степень уверенности|оценка уверенности|confidence|certainty)"
    r"\s*[:\-–—]?\s*(?:примерно|около)?\s*\d{1,3}\s*(?:%|процентов?)\.?\s*$"
)
_CONFIDENCE_PREFIX_RE = re.compile(
    r"(?i)\b(?:моя\s+)?(?:уверенность|степень уверенности|оценка уверенности|confidence|certainty)"
    r"\s*[:\-–—]?\s*(?:примерно|около)?\s*\d{1,3}\s*(?:%|процентов?)\.?\s*"
)
_CONFIDENT_ON_PERCENT_RE = re.compile(
    r"(?i)\b(?:я\s+)?уверен(?:а)?\s+на\s+\d{1,3}\s*(?:%|процентов?),?\s+что\s+"
)
_PAREN_CONFIDENCE_RE = re.compile(
    r"(?i)\s*\((?:моя\s+)?(?:уверенность|степень уверенности|оценка уверенности|confidence|certainty)"
    r"\s*[:\-–—]?\s*(?:примерно|около)?\s*\d{1,3}\s*(?:%|процентов?)\)"
)


def strip_confidence_percentages(text: str) -> str:
    """Удаляет самооценки уверенности в процентах из AI-ответов."""
    if not text:
        return text

    cleaned = _CONFIDENCE_LINE_RE.sub("", text)
    cleaned = _PAREN_CONFIDENCE_RE.sub("", cleaned)
    cleaned = _CONFIDENT_ON_PERCENT_RE.sub("", cleaned)
    cleaned = _CONFIDENCE_PREFIX_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
