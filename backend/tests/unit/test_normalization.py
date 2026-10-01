from datetime import date

import pytest

from app.domain import normalization as norm


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("150000", 150000),
        ("1,50,000", 150000),
        ("150k", 150000),
        ("PKR 1.5 lakh", 150000),
        ("Rs. 180,000/month", 180000),
        ("150k - 200k", 150000),
        ("2 lacs", 200000),
        (175000, 175000),
        ("negotiable", None),
        ("", None),
        (None, None),
        (-5, None),
    ],
)
def test_parse_salary(raw: object, expected: int | None) -> None:
    assert norm.parse_salary(raw)[0] == expected  # type: ignore[arg-type]


def test_parse_salary_detects_currency() -> None:
    assert norm.parse_salary("USD 2,000") == (2000, "USD")
    assert norm.parse_salary("Rs 90,000") == (90000, "PKR")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3", 3.0),
        ("3.5 years", 3.5),
        ("2+ yrs", 2.0),
        ("Fresher", 0.0),
        ("18 months", 1.5),
        (4, 4.0),
        ("a lot", None),
        (None, None),
    ],
)
def test_parse_experience(raw: object, expected: float | None) -> None:
    assert norm.parse_experience_years(raw) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0300-1234567", "+923001234567"),
        ("+92 300 1234567", "+923001234567"),
        ("03001234567", "+923001234567"),
        ("12345", None),
        ("not a phone", None),
        (None, None),
    ],
)
def test_normalize_phone(raw: str | None, expected: str | None) -> None:
    assert norm.normalize_phone(raw, "PK") == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ali.Ahmed@Example.COM", "ali.ahmed@example.com"),
        ("  sara@example.com ", "sara@example.com"),
        ("ali@", None),
        ("no-at-sign.com", None),
        ("", None),
    ],
)
def test_normalize_email(raw: str, expected: str | None) -> None:
    assert norm.normalize_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-01", date(2026, 11, 1)),
        ("01/11/2026", date(2026, 11, 1)),
        ("1 Nov 2026", date(2026, 11, 1)),
        ("November 1, 2026", date(2026, 11, 1)),
        ("next month", None),
    ],
)
def test_parse_date_is_day_first(raw: str, expected: date | None) -> None:
    assert norm.parse_date(raw) == expected


def test_normalize_skills_maps_aliases_and_dedupes() -> None:
    aliases = {"py": "python", "postgres": "postgresql"}
    assert norm.normalize_skills("Py, Python; Postgres |  FastAPI.", aliases) == ["python", "postgresql", "fastapi"]
    assert norm.normalize_skills(["Docker", "docker ", "CI/CD"], {}) == ["docker", "ci/cd"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ali Ahmed", "Ali Ahmed"),
        ("  Muhammad   Usman  ", "Muhammad Usman"),
        ("O'Brien-Smith", "O'Brien-Smith"),
        ("علی احمد", "علی احمد"),
        ("A", None),
        ("R2D2", None),
        ("", None),
    ],
)
def test_normalize_name(raw: str, expected: str | None) -> None:
    assert norm.normalize_name(raw) == expected


def test_find_terms_is_whole_word() -> None:
    text = "Built CI/CD with GitHub Actions; used Postgres and Java."
    assert norm.find_terms(text, ["ci/cd", "github actions", "java", "javascript", "git"]) == [
        "ci/cd",
        "github actions",
        "java",
    ]
