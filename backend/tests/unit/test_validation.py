from app.domain.contracts import ApplicationSubmission, ValidationOutcome
from app.domain.validation import CvInfo, validate_submission
from tests.conftest import validation_context

COMPLETE = {
    "full_name": "Ali Ahmed",
    "email": "Ali@Example.com",
    "phone": "0300-1234567",
    "position": "Python Developer",
    "experience_years": "4 years",
    "skills": "Python, FastAPI, Postgres",
    "expected_salary": "1,80,000",
    "available_from": "01/11/2026",
    "cv_ref": "cv/2026/09/" + "a" * 32 + ".pdf",
    "consent": True,
}
CV = CvInfo(exists=True, text="Senior backend engineer. Python, FastAPI.")


def codes(result: object) -> set[str]:
    return {i.code for i in result.issues}  # type: ignore[attr-defined]


def test_complete_application_is_valid_and_normalised() -> None:
    result = validate_submission(ApplicationSubmission(**COMPLETE), validation_context(), CV)
    assert result.outcome is ValidationOutcome.VALID, result.issues
    app = result.application
    assert app.email == "ali@example.com"
    assert app.phone == "+923001234567"
    assert app.position_code == "PY_DEV"
    assert app.skills == ["python", "fastapi", "postgresql"]
    assert app.expected_salary == 180000
    assert app.experience_years == 4.0
    assert str(app.available_from) == "2026-11-01"
    assert app.cv_text and app.cv_storage_key


def test_incomplete_application_goes_to_review_with_reasons() -> None:
    data = {**COMPLETE, "experience_years": None, "expected_salary": "", "cv_ref": None, "available_from": None}
    result = validate_submission(ApplicationSubmission(**data), validation_context(), None)
    assert result.outcome is ValidationOutcome.NEEDS_REVIEW
    assert {"EXPERIENCE_MISSING", "SALARY_MISSING", "CV_MISSING", "JOINING_DATE_MISSING"} <= codes(result)


def test_invalid_email_with_valid_phone_is_review_not_invalid() -> None:
    result = validate_submission(
        ApplicationSubmission(**{**COMPLETE, "email": "ali@@example"}), validation_context(), CV
    )
    assert result.outcome is ValidationOutcome.NEEDS_REVIEW
    assert "EMAIL_INVALID" in codes(result)
    assert result.application.email is None


def test_no_valid_contact_is_invalid() -> None:
    data = {**COMPLETE, "email": "not-an-email", "phone": "123"}
    result = validate_submission(ApplicationSubmission(**data), validation_context(), CV)
    assert result.outcome is ValidationOutcome.INVALID
    assert "NO_VALID_CONTACT" in codes(result)


def test_unknown_and_closed_positions_are_invalid() -> None:
    unknown = validate_submission(
        ApplicationSubmission(**{**COMPLETE, "position": "Astronaut"}), validation_context(), CV
    )
    closed = validate_submission(
        ApplicationSubmission(**{**COMPLETE, "position": "OLD_ROLE"}), validation_context(), CV
    )
    assert unknown.outcome is ValidationOutcome.INVALID and "POSITION_UNKNOWN" in codes(unknown)
    assert closed.outcome is ValidationOutcome.INVALID and "POSITION_CLOSED" in codes(closed)


def test_past_joining_date_and_unreadable_cv_need_review() -> None:
    data = {**COMPLETE, "available_from": "2020-01-01"}
    result = validate_submission(ApplicationSubmission(**data), validation_context(), CvInfo(exists=True, text=""))
    assert result.outcome is ValidationOutcome.NEEDS_REVIEW
    assert {"JOINING_DATE_PAST", "CV_TEXT_UNREADABLE"} <= codes(result)


def test_missing_consent_needs_review() -> None:
    result = validate_submission(ApplicationSubmission(**{**COMPLETE, "consent": None}), validation_context(), CV)
    assert "CONSENT_MISSING" in codes(result)


def test_high_salary_is_only_a_warning() -> None:
    result = validate_submission(
        ApplicationSubmission(**{**COMPLETE, "expected_salary": "900k"}), validation_context(), CV
    )
    assert result.outcome is ValidationOutcome.VALID
    assert "SALARY_ABOVE_BAND" in codes(result)
