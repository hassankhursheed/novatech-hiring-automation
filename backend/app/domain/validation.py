"""Application validation: turns a raw submission into the normalized contract plus explicit issues.

Nothing is silently dropped. Every problem becomes an issue with a severity:
  ERROR  -> INVALID       no usable identity/position; recorded with the reason, no application created
  REVIEW -> NEEDS_REVIEW  application created and routed to a recruiter with the reason
  WARNING                 informational
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.domain import normalization as norm
from app.domain.contracts import (
    ApplicationSubmission,
    NormalizedApplication,
    Severity,
    ValidationIssue,
    ValidationOutcome,
    ValidationResult,
)

VALIDATOR_VERSION = "intake-validator/1.0.0"
MAX_EXPERIENCE_YEARS = 50
MAX_MONTHLY_SALARY = 10_000_000
MAX_JOINING_HORIZON_DAYS = 365
MAX_COVER_LETTER_CHARS = 5000


@dataclass(frozen=True)
class PositionInfo:
    code: str
    title: str
    currency: str
    is_open: bool
    salary_min: int | None = None
    salary_max: int | None = None


@dataclass(frozen=True)
class CvInfo:
    exists: bool
    text: str | None


@dataclass(frozen=True)
class ValidationContext:
    positions: list[PositionInfo]
    skill_aliases: dict[str, str]
    default_phone_region: str
    default_currency: str
    today: date


def _resolve_position(raw: str | None, positions: list[PositionInfo]) -> PositionInfo | None:
    key = (norm.collapse_whitespace(raw) or "").lower()
    if not key:
        return None
    for position in positions:
        if key in (position.code.lower(), position.title.lower()):
            return position
    return None


def validate_submission(
    sub: ApplicationSubmission, ctx: ValidationContext, cv: CvInfo | None, correlation_id: str | None = None
) -> ValidationResult:
    issues: list[ValidationIssue] = []

    def issue(field: str, code: str, severity: Severity, message: str) -> None:
        issues.append(ValidationIssue(field=field, code=code, severity=severity, message=message))

    # ---- identity -----------------------------------------------------------------------------
    full_name = norm.normalize_name(sub.full_name)
    if full_name is None:
        issue(
            "full_name",
            "NAME_MISSING" if not sub.full_name else "NAME_INVALID",
            Severity.ERROR,
            "full name is missing" if not sub.full_name else "full name contains invalid characters",
        )

    email = norm.normalize_email(sub.email)
    phone = norm.normalize_phone(sub.phone, ctx.default_phone_region)
    email_problem = None if email else ("EMAIL_MISSING" if not sub.email else "EMAIL_INVALID")
    phone_problem = None if phone else ("PHONE_MISSING" if not sub.phone else "PHONE_INVALID")

    if not email and not phone:
        issue(
            "contact", "NO_VALID_CONTACT", Severity.ERROR, "neither a valid email nor a valid phone number was provided"
        )
    else:
        if email_problem:
            issue(
                "email",
                email_problem,
                Severity.REVIEW,
                "email is missing" if email_problem == "EMAIL_MISSING" else f"email '{sub.email}' is not valid",
            )
        if phone_problem:
            issue(
                "phone",
                phone_problem,
                Severity.REVIEW,
                "phone number is missing"
                if phone_problem == "PHONE_MISSING"
                else f"phone number '{sub.phone}' is not valid",
            )

    # ---- position -------------------------------------------------------------------------------
    position = _resolve_position(sub.position, ctx.positions)
    if position is None:
        issue(
            "position",
            "POSITION_MISSING" if not sub.position else "POSITION_UNKNOWN",
            Severity.ERROR,
            "position is missing" if not sub.position else f"position '{sub.position}' is not recognised",
        )
    elif not position.is_open:
        issue("position", "POSITION_CLOSED", Severity.ERROR, f"position '{position.title}' is not open")

    # ---- experience -----------------------------------------------------------------------------
    experience = norm.parse_experience_years(sub.experience_years)
    if sub.experience_years in (None, ""):
        issue("experience_years", "EXPERIENCE_MISSING", Severity.REVIEW, "years of experience not provided")
    elif experience is None or not (0 <= experience <= MAX_EXPERIENCE_YEARS):
        issue(
            "experience_years",
            "EXPERIENCE_INVALID",
            Severity.REVIEW,
            f"years of experience '{sub.experience_years}' is not a plausible value",
        )
        experience = None

    # ---- skills ---------------------------------------------------------------------------------
    skills = norm.normalize_skills(sub.skills, ctx.skill_aliases)
    if not skills:
        issue("skills", "SKILLS_MISSING", Severity.REVIEW, "no skills were listed")

    # ---- salary ---------------------------------------------------------------------------------
    salary, detected_currency = norm.parse_salary(sub.expected_salary)
    currency = (
        sub.salary_currency or detected_currency or (position.currency if position else None) or ctx.default_currency
    ).upper()[:3]
    if sub.expected_salary in (None, ""):
        issue("expected_salary", "SALARY_MISSING", Severity.REVIEW, "expected salary not provided")
    elif salary is None or salary > MAX_MONTHLY_SALARY:
        issue(
            "expected_salary",
            "SALARY_INVALID",
            Severity.REVIEW,
            f"expected salary '{sub.expected_salary}' could not be understood as a monthly amount",
        )
        salary = None
    elif position and currency == position.currency and position.salary_max and salary > position.salary_max * 1.5:
        issue(
            "expected_salary",
            "SALARY_ABOVE_BAND",
            Severity.WARNING,
            f"expected salary {salary:,} {currency} is well above the band for {position.title}",
        )
    if position and currency != position.currency:
        issue(
            "salary_currency",
            "CURRENCY_MISMATCH",
            Severity.REVIEW,
            f"salary currency {currency} differs from the position currency {position.currency}",
        )

    # ---- joining date ---------------------------------------------------------------------------
    available_from = norm.parse_date(sub.available_from)
    if sub.available_from in (None, ""):
        issue("available_from", "JOINING_DATE_MISSING", Severity.REVIEW, "earliest joining date not provided")
    elif available_from is None:
        issue(
            "available_from",
            "JOINING_DATE_INVALID",
            Severity.REVIEW,
            f"joining date '{sub.available_from}' is not a recognised date (use YYYY-MM-DD or DD/MM/YYYY)",
        )
    elif available_from < ctx.today:
        issue("available_from", "JOINING_DATE_PAST", Severity.REVIEW, f"joining date {available_from} is in the past")
        available_from = None
    elif available_from > ctx.today + timedelta(days=MAX_JOINING_HORIZON_DAYS):
        issue(
            "available_from",
            "JOINING_DATE_TOO_FAR",
            Severity.REVIEW,
            f"joining date {available_from} is more than a year away",
        )

    notice_days = norm.parse_int(sub.notice_period_days)
    if notice_days is not None and not (0 <= notice_days <= 365):
        issue("notice_period_days", "NOTICE_PERIOD_INVALID", Severity.WARNING, "notice period ignored (not 0-365 days)")
        notice_days = None

    # ---- CV -------------------------------------------------------------------------------------
    cv_text: str | None = None
    cv_key: str | None = None
    if not sub.cv_ref:
        issue("cv", "CV_MISSING", Severity.REVIEW, "no CV was attached")
    elif cv is None or not cv.exists:
        issue("cv", "CV_NOT_FOUND", Severity.REVIEW, "the referenced CV file was not found")
    else:
        cv_key = sub.cv_ref
        cv_text = (cv.text or "").strip() or None
        if cv_text is None:
            issue(
                "cv", "CV_TEXT_UNREADABLE", Severity.REVIEW, "no text could be extracted from the CV (scanned image?)"
            )

    # ---- other ------------------------------------------------------------------------------------
    consent = norm.parse_bool(sub.consent)
    if not consent:
        issue("consent", "CONSENT_MISSING", Severity.REVIEW, "data-processing consent was not given")

    linkedin = norm.normalize_url(sub.linkedin_url)
    if sub.linkedin_url and linkedin is None:
        issue("linkedin_url", "URL_INVALID", Severity.WARNING, "profile URL ignored (not a valid URL)")

    cover_letter = sub.cover_letter
    if cover_letter and len(cover_letter) > MAX_COVER_LETTER_CHARS:
        issue(
            "cover_letter",
            "COVER_LETTER_TRUNCATED",
            Severity.WARNING,
            f"cover letter truncated to {MAX_COVER_LETTER_CHARS} characters",
        )
        cover_letter = cover_letter[:MAX_COVER_LETTER_CHARS]

    severities = {i.severity for i in issues}
    if Severity.ERROR in severities:
        outcome = ValidationOutcome.INVALID
    elif Severity.REVIEW in severities:
        outcome = ValidationOutcome.NEEDS_REVIEW
    else:
        outcome = ValidationOutcome.VALID

    application = NormalizedApplication(
        full_name=full_name,
        email=email,
        phone=phone,
        position_code=position.code if position else None,
        position_title=position.title if position else None,
        experience_years=experience,
        skills=skills,
        expected_salary=salary,
        salary_currency=currency,
        available_from=available_from,
        notice_period_days=notice_days,
        current_company=norm.collapse_whitespace(sub.current_company),
        current_title=norm.collapse_whitespace(sub.current_title),
        city=norm.collapse_whitespace(sub.city),
        linkedin_url=linkedin,
        cover_letter=cover_letter,
        cv_storage_key=cv_key,
        cv_text=cv_text,
        consent=consent,
    )
    return ValidationResult(
        outcome=outcome,
        issues=issues,
        application=application,
        validator_version=VALIDATOR_VERSION,
        correlation_id=correlation_id,
    )
