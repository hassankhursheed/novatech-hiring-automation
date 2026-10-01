"""PII minimisation before text is sent to an external LLM.

The model gets job-relevant content only. Contact details and personal identifiers are removed, which
reduces privacy exposure and removes signals (name, photo, age, gender, marital status) that could bias
an assessment. The candidate's name is never sent.
"""

import re

_PHONE_CANDIDATE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE), "[link]"),
    (re.compile(r"\b\d{5}-\d{7}-\d\b"), "[national-id]"),  # Pakistani CNIC
    (re.compile(r"\b(?:date of birth|dob|born on)\b[^\n]{0,40}", re.IGNORECASE), "[date-of-birth]"),
    (
        re.compile(
            r"\b(?:marital status|religion|gender|sex|nationality|father'?s name|age)\s*[:\-][^\n]{0,40}", re.IGNORECASE
        ),
        "[personal-detail]",
    ),
]


def _redact_phone(match: re.Match[str]) -> str:
    # Phone numbers have 9-15 digits; year ranges such as "2019 - 2023" (8 digits) are kept.
    digits = sum(ch.isdigit() for ch in match.group())
    return "[phone]" if 9 <= digits <= 15 else match.group()


def redact(text: str | None, *, extra_terms: list[str] | None = None, max_chars: int = 12000) -> str:
    """Remove contact details and personal identifiers; also blank out explicit terms (e.g. the name)."""
    if not text:
        return ""
    result = text
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    result = _PHONE_CANDIDATE.sub(_redact_phone, result)
    for term in extra_terms or []:
        for token in sorted({term, *term.split()}, key=len, reverse=True):
            if len(token) >= 3:
                result = re.sub(rf"\b{re.escape(token)}\b", "[candidate]", result, flags=re.IGNORECASE)
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result[:max_chars]
