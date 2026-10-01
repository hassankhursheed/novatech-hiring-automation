"""Pure normalisation helpers. No I/O: easy to unit test and safe to reuse anywhere."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import phonenumbers
from email_validator import EmailNotValidError, validate_email

_WS = re.compile(r"\s+")
_NAME = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ .'\-])*$", re.UNICODE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_URL = re.compile(r"^https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/?#][^\s]*)?$")
_SKILL_SPLIT = re.compile(r"[,;|\n]+")
_EN_DASH = chr(0x2013)
_RANGE_SEPARATOR = re.compile(rf"\s*(?:-|{_EN_DASH}|to)\s*(?=\d)")  # hyphen, en dash or "to"
_ZERO_EXPERIENCE = {"fresher", "fresh", "fresh graduate", "none", "no experience", "nil", "0"}

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
)

_CURRENCY_TOKENS = {
    "pkr": "PKR",
    "rs": "PKR",
    "rs.": "PKR",
    "rupees": "PKR",
    "₨": "PKR",
    "usd": "USD",
    "$": "USD",
    "aed": "AED",
    "sar": "SAR",
    "gbp": "GBP",
    "£": "GBP",
    "eur": "EUR",
    "€": "EUR",
}
_MULTIPLIERS = {
    "k": 1_000,
    "thousand": 1_000,
    "lac": 100_000,
    "lacs": 100_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "m": 1_000_000,
    "mn": 1_000_000,
    "million": 1_000_000,
}


def collapse_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WS.sub(" ", value).strip()
    return cleaned or None


def normalize_name(raw: str | None) -> str | None:
    """Collapse whitespace; accept letters (any script), spaces and . ' - . Casing is preserved."""
    name = collapse_whitespace(raw)
    if not name or not (2 <= len(name) <= 120) or not _NAME.match(name):
        return None
    return name


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        result = validate_email(raw.strip(), check_deliverability=False)
    except EmailNotValidError:
        return None
    return result.normalized.lower()


def normalize_phone(raw: str | None, default_region: str) -> str | None:
    """Return E.164 (e.g. +923001234567) or None when the number is not valid."""
    if not raw:
        return None
    try:
        number = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def parse_experience_years(raw: str | float | int | None) -> float | None:
    """'3', '3.5', '3 years', '2+ yrs', 'fresher' -> number of years."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw)
    text = raw.strip().lower()
    if not text:
        return None
    if text in _ZERO_EXPERIENCE:
        return 0.0
    match = _NUMBER.search(text)
    if not match:
        return None
    value = float(match.group())
    if "month" in text and "year" not in text:
        value = round(value / 12, 1)
    return value


def parse_salary(raw: str | float | int | None) -> tuple[int | None, str | None]:
    """Parse a monthly salary: '150000', '1,50,000', '150k', 'PKR 1.5 lakh', 'Rs. 180,000/month'.

    Ranges ('150k-200k') use the lower bound. Returns (amount, detected ISO currency or None).
    """
    if raw is None or isinstance(raw, bool):
        return None, None
    if isinstance(raw, int | float):
        return (int(raw), None) if raw > 0 else (None, None)

    text = raw.strip().lower()
    if not text:
        return None, None

    currency = None
    for token, code in _CURRENCY_TOKENS.items():
        if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", text):
            currency = code
            break

    text = text.replace(",", "")
    text = _RANGE_SEPARATOR.split(text, maxsplit=1)[0]  # keep lower bound of a range
    match = re.search(r"(\d+(?:\.\d+)?)\s*([a-z]+)?", text)
    if not match:
        return None, currency
    try:
        amount = Decimal(match.group(1))
    except InvalidOperation:
        return None, currency
    suffix = match.group(2) or ""
    amount *= _MULTIPLIERS.get(suffix, 1)
    value = int(amount)
    return (value if value > 0 else None), currency


def parse_date(raw: str | date | None) -> date | None:
    """ISO and day-first formats (Pakistan convention: 15/10/2026 is 15 October)."""
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    text = collapse_whitespace(raw)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_int(raw: str | int | None) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    match = _NUMBER.search(raw)
    return int(float(match.group())) if match else None


def parse_bool(raw: bool | str | None) -> bool:
    if isinstance(raw, bool):
        return raw
    return (raw or "").strip().lower() in {"true", "yes", "y", "on", "1", "accepted"}


def normalize_url(raw: str | None) -> str | None:
    url = (raw or "").strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url if len(url) <= 300 and _URL.match(url) else None


def canonical_skill(raw: str, aliases: dict[str, str]) -> str | None:
    skill = _WS.sub(" ", raw.strip().lower()).strip(" .")
    if not skill or len(skill) > 60:
        return None
    return aliases.get(skill, skill)


def normalize_skills(raw: list[str] | str | None, aliases: dict[str, str], limit: int = 50) -> list[str]:
    """Split, lower-case, map aliases to canonical names, de-duplicate (order preserved)."""
    if raw is None:
        return []
    parts = _SKILL_SPLIT.split(raw) if isinstance(raw, str) else [p for item in raw for p in _SKILL_SPLIT.split(item)]
    seen: dict[str, None] = {}
    for part in parts:
        skill = canonical_skill(part, aliases)
        if skill:
            seen.setdefault(skill, None)
    return list(seen)[:limit]


def term_pattern(term: str) -> re.Pattern[str]:
    """Whole-term, case-insensitive match that also works for terms like 'ci/cd' or 'c++'."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", re.IGNORECASE)


def find_terms(text: str | None, terms: list[str]) -> list[str]:
    if not text:
        return []
    return [t for t in terms if term_pattern(t).search(text)]
