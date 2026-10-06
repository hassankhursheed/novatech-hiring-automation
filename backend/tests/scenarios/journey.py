"""End-to-end check of the whole hiring journey on the running stack, step by step, with the candidate's inbox.

    docker compose --profile test run --rm backend-tests python -m tests.scenarios.journey

One applicant (on the reserved test domain example.com) goes through all 7 steps, through the same public
interfaces people use: the careers intake, the emailed links (slot choice, offer approval, offer response) and the
HR portal API (shortlist, scorecard, decision, onboarding tasks). Where the automation asks a person to decide
(for example while the AI key is not set yet), the script acts as that person, exactly like HR would in the portal.

It prints one line per step and every email the candidate received, in order, and exits non-zero on a failure.
"""

import sys
import time
from typing import Any

from tests.scenarios.harness import CheckFailed, Harness

CV = """Ali Raza - Backend Developer
Python developer with 4 years of experience building REST APIs with FastAPI and Django.
Designed PostgreSQL schemas, wrote SQL migrations and tuned queries; Docker and Git daily.
Built an order-management API for a Lahore software house; mentored two junior developers."""

SCORECARD = {
    "technical_skills": 4,
    "communication": 4,
    "problem_solving": 4,
    "experience": 4,
    "team_fit": 5,
    "recommendation": "HIRE",
    "comments": "Explained API design and indexing trade-offs clearly; solved the SQL exercise with a clean join.",
}


class Journey:
    def __init__(self) -> None:
        self.h = Harness()
        self.results: list[tuple[str, bool, str]] = []

    def step(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)
        if not ok:
            raise CheckFailed(f"{name}: {detail}")

    def staff(self, path: str, body: dict[str, Any] | None = None, who: str = "sana") -> dict[str, Any]:
        response = self.h.staff(path, body, who=who)
        if response.status_code >= 400:
            raise CheckFailed(f"staff {path} -> HTTP {response.status_code}: {response.text[:300]}")
        return dict(response.json()) if response.content else {}

    def wait_status(self, app_id: str, wanted: set[str], timeout: float = 240) -> str:
        deadline = time.monotonic() + timeout
        status = ""
        while time.monotonic() < deadline:
            self.h.settle(timeout=max(30, deadline - time.monotonic()))
            status = self.h.one("SELECT status FROM hiring.applications WHERE id = %s", app_id)["status"]
            if status in wanted:
                return status
            time.sleep(2)
        return status

    def inbox(self, to: str, subject: str) -> list[dict[str, Any]]:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            found = self.h.emails(to, subject)
            if found:
                return found
            time.sleep(2)
        return []

    def run(self) -> int:
        h = self.h
        payload = h.make(
            "journey",
            "Ali",
            "Raza",
            "PY_DEV",
            experience_years="4",
            expected_salary="200000",
            skills=["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs"],
            current_title="Backend Developer",
            cover_letter="I build reliable APIs with FastAPI and PostgreSQL and would like to grow with your team.",
            cv_ref=h.upload_cv(CV),
        )
        email = payload["email"]
        position = h.one("SELECT title FROM hiring.job_positions WHERE code = 'PY_DEV'")["title"]

        # 1. Apply ------------------------------------------------------------------------------------------
        c = h.submit(payload)
        event = h.wait_intake(c)
        app_id = str(event["result"]["application_id"])
        received = self.inbox(email, "We received your application")
        self.step("1 Apply", bool(received), f"{event['result']['application_code']} stored; confirmation email sent")

        # 2. Screening: rules + advisory AI -------------------------------------------------------------------
        status = self.wait_status(app_id, {"SHORTLISTED", "SCREENING_REVIEW", "REJECTED"})
        score = h.one("SELECT application_score FROM hiring.applications WHERE id = %s", app_id)["application_score"]
        ai = h.q(
            "SELECT status, recommendation, fallback_reason FROM hiring.ai_analyses WHERE application_id = %s", app_id
        )
        ai_text = (
            f"AI {ai[0]['status']}" + (f" ({ai[0]['recommendation']})" if ai[0]["recommendation"] else "")
            if ai
            else "AI not run"
        )
        self.step(
            "2 Screening", status in {"SHORTLISTED", "SCREENING_REVIEW"}, f"rule score {score}, {ai_text} -> {status}"
        )
        if status == "SCREENING_REVIEW":
            under_review = self.inbox(email, "is being reviewed")
            self.step("2 Screening email", bool(under_review), "candidate told the application is being reviewed")
            self.staff(
                f"applications/{app_id}/transition", {"to_status": "SHORTLISTED", "reason": "Strong backend profile"}
            )
            status = self.wait_status(app_id, {"SHORTLISTED"})

        # 3. Interview: invitation, slot choice, confirmation -------------------------------------------------
        h.settle()
        token = h.link_token(email, "Interview invitation")
        view = h.portal("GET", "interview", token).json()
        slot = view["slots"][0]
        confirmed = h.portal("POST", "interview/confirm", token, {"slot_id": slot["slot_id"]})
        status = self.wait_status(app_id, {"INTERVIEW_SCHEDULED"})
        confirmation = self.inbox(email, "Interview confirmed")
        self.step(
            "3 Interview booked",
            confirmed.status_code == 200 and status == "INTERVIEW_SCHEDULED" and bool(confirmation),
            f"invitation email -> slot {slot['starts_at']} booked -> confirmation email",
        )

        # 4. Scorecard (entered by HR in the portal) -> AI assessment + weighted evaluation --------------------
        interview = h.one("SELECT id FROM hiring.interviews WHERE application_id = %s ORDER BY round DESC", app_id)
        self.staff(f"interviews/{interview['id']}/feedback", SCORECARD)
        status = self.wait_status(app_id, {"SELECTED", "INTERVIEW_REVIEW", "REJECTED", "OFFER_PENDING_APPROVAL"})
        assessed = h.q(
            "SELECT status, recommendation, evidence_alignment, fallback_reason FROM hiring.interview_assessments "
            "WHERE application_id = %s",
            app_id,
        )
        final = h.one("SELECT final_score FROM hiring.applications WHERE id = %s", app_id)["final_score"]
        thanks = self.inbox(email, "Thank you for interviewing")
        self.step(
            "4 Scorecard + evaluation",
            bool(assessed) and final is not None and bool(thanks),
            f"interview score 84, AI assessment {assessed[0]['status'] if assessed else '-'}, "
            f"final {final} -> {status}",
        )

        # 5. Decision (30/70 score; a person decides when the case is in review) ----------------------------
        if status == "INTERVIEW_REVIEW":
            self.staff(
                f"applications/{app_id}/transition",
                {"to_status": "SELECTED", "reason": "Hiring manager decision after the interview"},
                who="ayesha",
            )
            status = self.wait_status(app_id, {"SELECTED", "OFFER_PENDING_APPROVAL", "OFFERED"})
        selected = self.inbox(email, "Good news about your application")
        self.step("5 Decision", bool(selected), f"selected; candidate told the offer is being prepared ({status})")

        # 6. Offer: approval, letter, candidate accepts ------------------------------------------------------
        for _ in range(2):  # one or two approval levels, depending on the salary
            status = self.wait_status(app_id, {"OFFER_PENDING_APPROVAL", "OFFERED"})
            if status == "OFFERED":
                break
            offer = h.one(
                "SELECT o.offer_code, o.reporting_manager_id FROM hiring.offers o WHERE o.application_id = %s "
                "ORDER BY revision DESC LIMIT 1",
                app_id,
            )
            pending = h.q(
                "SELECT n.recipient FROM ops.notifications n WHERE n.template_key = 'offer.approval_request' "
                "AND n.correlation_id = %s ORDER BY n.created_at DESC LIMIT 1",
                c.correlation_id,
            )
            if not pending:
                raise CheckFailed("no approval request was emailed")
            approver = pending[0]["recipient"]
            token = h.link_token(approver, offer["offer_code"])
            decided = h.portal("POST", "approval", token, {"decision": "APPROVED"})
            if decided.status_code != 200:
                raise CheckFailed(f"approval -> HTTP {decided.status_code}: {decided.text[:200]}")
        status = self.wait_status(app_id, {"OFFERED"})
        offer_mail = self.inbox(email, "Your offer from")
        token = h.link_token(email, "Your offer from")
        pdf = h.portal("GET", "offer/document", token)
        accepted = h.portal("POST", "offer/respond", token, {"response": "ACCEPT", "message": "Happy to join"})
        self.step(
            "6 Offer",
            bool(offer_mail) and pdf.status_code == 200 and accepted.status_code == 200,
            f"approved -> offer email with letter ({len(pdf.content)} byte PDF) -> accepted",
        )

        # 7. Onboarding: employee record, welcome, tasks ----------------------------------------------------
        status = self.wait_status(app_id, {"ONBOARDING"})
        employee = h.one(
            "SELECT id, employee_code, company_email FROM hiring.employees WHERE application_id = %s", app_id
        )
        tasks = h.q(
            "SELECT id FROM hiring.onboarding_tasks WHERE employee_id = %s AND status <> 'COMPLETED'", employee["id"]
        )
        welcome = self.inbox(email, "Welcome")
        for task in tasks:
            self.staff(f"onboarding-tasks/{task['id']}/complete", {})
        status = self.wait_status(app_id, {"ONBOARDED"})
        finished = self.inbox(email, "onboarding at")
        self.step(
            "7 Onboarding",
            status == "ONBOARDED" and bool(welcome) and bool(finished),
            f"employee {employee['employee_code']} ({employee['company_email']}), welcome email, "
            f"{len(tasks)} tasks completed -> {status}, closing email",
        )

        print(f"\nThe candidate's inbox ({email}, {position}), oldest first:")
        for message in reversed(h.emails(email)):
            print(f"  {message['Created'][:19].replace('T', ' ')}  {message['Subject']}")
        return 0


def main() -> int:
    journey = Journey()
    try:
        return journey.run()
    except CheckFailed as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
