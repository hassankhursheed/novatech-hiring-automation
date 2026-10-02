"""The 23 mandatory scenarios of the brief, each proven against the running stack."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tests.scenarios.fixtures import PY_REVIEW_CV, PY_STRONG_CV, PY_WEAK_CV, QA_STRONG_CV
from tests.scenarios.harness import BACKEND, N8N, STAFF, Candidate, CheckFailed, Harness

HR_EMAIL = "sana.malik@novatech.example"
AYESHA = "ayesha.khan@novatech.example"
OMAR = "omar.farooq@novatech.example"


@dataclass
class Scenario:
    number: int
    title: str
    run: Callable[["Proof"], None]


@dataclass
class Proof:
    h: Harness
    evidence: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.evidence.append(text)

    def check(self, condition: Any, claim: str) -> None:
        self.evidence.append(("PASS " if condition else "FAIL ") + claim)
        if not condition:
            raise CheckFailed(claim)


SCENARIOS: list[Scenario] = []
SHARED: dict[str, Candidate] = {}  # candidates handed from one scenario to a later one


def scenario(number: int, title: str) -> Callable[[Callable[[Proof], None]], Callable[[Proof], None]]:
    def register(fn: Callable[[Proof], None]) -> Callable[[Proof], None]:
        SCENARIOS.append(Scenario(number, title, fn))
        return fn

    return register


# ---- building blocks ---------------------------------------------------------------------------------------
def python_dev(
    h: Harness, label: str, first: str, last: str, *, strength: str = "strong", **over: Any
) -> dict[str, Any]:
    cv = {"strong": PY_STRONG_CV, "review": PY_REVIEW_CV, "weak": PY_WEAK_CV}[strength].format(years=4)
    skills = {
        "strong": ["Python", "FastAPI", "PostgreSQL", "REST APIs", "Git", "Docker", "AWS"],
        "review": ["Python", "Django", "SQL", "REST APIs", "Docker"],
        "weak": ["HTML", "CSS", "WordPress"],
    }[strength]
    fields: dict[str, Any] = {
        "skills": skills,
        "experience_years": 4 if strength == "strong" else 1,
        "expected_salary": "220k",
        "current_title": "Backend Engineer" if strength != "weak" else "Web Designer",
        "cover_letter": "I build reliable backend services and would like to grow with a product-focused team. " * 2,
        "cv_ref": h.upload_cv(cv),
    }
    fields.update(over)
    return h.make(label, first, last, "PY_DEV", **fields)


def apply(h: Harness, payload: dict[str, Any], *, fault: str | None = None) -> Candidate:
    c = h.submit(payload, fault=fault)
    h.wait_intake(c)
    h.settle()
    return c


def path_text(h: Harness, c: Candidate) -> str:
    return " -> ".join(h.path(c))


def count(h: Harness, c: Candidate, template: str) -> int:
    return len([n for n in h.notifications(c, template) if n["status"] == "SENT"])


def interview(h: Harness, c: Candidate) -> dict[str, Any]:
    row = h.one(
        "SELECT api.interview_snapshot(i.id) AS s FROM hiring.interviews i WHERE i.application_id = %s "
        "ORDER BY i.round DESC LIMIT 1",
        c.application_id,
    )
    return row.get("s") or {}


def offer(h: Harness, c: Candidate) -> dict[str, Any]:
    row = h.one(
        "SELECT api.offer_snapshot(o.id) AS s FROM hiring.offers o WHERE o.application_id = %s "
        "ORDER BY o.revision DESC LIMIT 1",
        c.application_id,
    )
    return row.get("s") or {}


def book(p: Proof, c: Candidate, choice: int = 0) -> dict[str, Any]:
    h = p.h
    token = h.link_token(c.email, "Interview invitation")
    view = h.portal("GET", "interview", token).json()
    p.check(view.get("slots"), f"candidate sees {len(view.get('slots', []))} open slots through the signed link")
    slot = view["slots"][choice]
    confirmed = h.portal("POST", "interview/confirm", token, {"slot_id": slot["slot_id"]})
    p.check(
        confirmed.status_code == 200 and confirmed.json()["status"] == "CONFIRMED", "slot confirmed by the candidate"
    )
    h.settle()
    return {"token": token, "slot_id": slot["slot_id"], **confirmed.json()}


def scorecard(p: Proof, c: Candidate, ratings: tuple[int, int, int, int, int], recommendation: str) -> dict[str, Any]:
    h = p.h
    interviewer = interview(h, c)["interviewer"]["email"]
    token = h.link_token(interviewer, f"Interview booked: {c.full_name}")
    keys = ("technical_skills", "communication", "problem_solving", "experience", "team_fit")
    body = dict(zip(keys, ratings, strict=True)) | {
        "recommendation": recommendation,
        "comments": "Structured interview.",
    }
    response = h.portal("POST", "feedback", token, body)
    p.check(
        response.status_code == 200,
        f"interviewer submitted the scorecard ({recommendation}, score {response.json().get('interview_score')})",
    )
    h.settle()
    return response.json()


def approve(
    p: Proof, c: Candidate, level: int, approver: str, decision: str = "APPROVED", reason: str | None = None
) -> dict[str, Any]:
    h = p.h
    code = offer(h, c)["offer_code"]
    token = h.link_token(approver, f"[Approval L{level}] Offer {code}")
    response = h.portal("POST", "approval", token, {"decision": decision, "reason": reason})
    p.check(
        response.status_code == 200, f"L{level} {decision.lower()} by {approver.split('.')[0].title()} via signed link"
    )
    h.settle()
    return response.json()


def to_selected(p: Proof, label: str, first: str, last: str, **over: Any) -> Candidate:
    h = p.h
    c = apply(h, python_dev(h, label, first, last, **over))
    p.check(h.status(c) == "SHORTLISTED", "screened and shortlisted")
    book(p, c)
    scorecard(p, c, (5, 4, 4, 4, 4), "HIRE")
    p.check(h.status(c) == "OFFER_PENDING_APPROVAL", "evaluation selected the candidate and drafted the offer")
    return c


def to_offered(p: Proof, label: str, first: str, last: str, **over: Any) -> Candidate:
    c = to_selected(p, label, first, last, **over)
    o = offer(p.h, c)
    approve(p, c, 1, AYESHA)
    if o["required_approval_levels"] == 2:
        approve(p, c, 2, OMAR)
    p.check(p.h.status(c) == "OFFERED" and offer(p.h, c)["status"] == "SENT", "offer approved and sent")
    return c


# ---- 1-6: intake and screening ------------------------------------------------------------------------------
@scenario(1, "Strong candidate becomes an employee")
def s01(p: Proof) -> None:
    h = p.h
    c = to_offered(p, "s01", "Areeba", "Siddiqui")
    token = h.link_token(c.email, "Your offer from")
    view = h.portal("GET", "offer", token).json()
    p.check(
        view["monthly_salary"] == 220000 and "application_score" not in view,
        "candidate sees the terms, not internal scores",
    )
    pdf = h.http.get(f"{BACKEND}/v1/portal/offer/document", headers={"Authorization": f"Bearer {token}"})
    p.check(
        pdf.status_code == 200 and pdf.content[:4] == b"%PDF",
        f"offer letter PDF downloadable ({len(pdf.content)} bytes)",
    )
    answer = h.portal("POST", "offer/respond", token, {"response": "ACCEPT", "message": "Delighted to join."})
    p.check(answer.status_code == 200, "candidate accepted")
    h.settle()
    employee = h.one("SELECT * FROM hiring.employees WHERE application_id = %s", c.application_id)
    p.check(
        employee.get("employee_code", "").startswith("NT-"),
        f"employee {employee.get('employee_code')} created, account {employee.get('company_email')}",
    )
    tasks = h.q("SELECT id FROM hiring.onboarding_tasks WHERE employee_id = %s AND status = 'PENDING'", employee["id"])
    p.check(count(h, c, "onboarding.welcome") == 1, "one welcome email with orientation details")
    for task in tasks:
        done = h.staff(f"onboarding-tasks/{task['id']}/complete", {})
        if done.status_code != 200:
            raise CheckFailed(f"completing a task failed: {done.text[:200]}")
    h.settle()
    p.check(h.status(c) == "ONBOARDED", f"all {len(tasks)} remaining onboarding tasks done -> ONBOARDED")
    p.check(count(h, c, "onboarding.complete") == 1, "HR and manager told onboarding is complete")
    p.note(f"status path: {path_text(h, c)}")


@scenario(2, "Incomplete application goes to manual review with a reason")
def s02(p: Proof) -> None:
    h = p.h
    c = apply(h, h.make("s02", "Danyal", "Hayat", "PY_DEV", skills=["Python", "Django"]))
    event = h.event(c)
    p.check(event["outcome"] == "NEEDS_REVIEW", "validator outcome NEEDS_REVIEW (no CV, no experience, no salary)")
    snap = h.snapshot(c)
    p.check(snap["status"] == "SCREENING_REVIEW", "application is in SCREENING_REVIEW")
    p.check("cv" in (snap.get("review_reason") or "").lower(), f"reason recorded: {snap.get('review_reason')}")
    p.check(
        not h.q("SELECT 1 FROM hiring.application_scores WHERE application_id = %s", c.application_id),
        "not scored automatically",
    )
    p.check(count(h, c, "staff.review_needed") == 1, "recruiters alerted once")


@scenario(3, "Duplicate candidate submission is detected")
def s03(p: Proof) -> None:
    h = p.h
    payload = python_dev(h, "s03", "Komal", "Jafri")
    first = apply(h, payload)
    second = h.submit(payload)  # same person and position, new idempotency key (submitted the form twice)
    h.wait_intake(second)
    h.settle()
    p.check(h.event(second)["outcome"] == "DUPLICATE", "second submission classified DUPLICATE")
    apps = h.q(
        "SELECT a.id FROM hiring.applications a JOIN hiring.candidates c ON c.id = a.candidate_id WHERE c.email = %s",
        payload["email"],
    )
    p.check(len(apps) == 1, "still exactly one application for the candidate")
    p.check(count(h, second, "application.duplicate") == 1, "candidate told the application already exists")
    p.note(f"original {h.snapshot(first)['application_code']} status {h.status(first)}")


@scenario(4, "Same webhook replayed creates no duplicates")
def s04(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s04", "Usama", "Wali"))
    replay = h.resubmit(c)
    p.check(
        replay.status_code == 200 and replay.json().get("correlation_id") == c.correlation_id,
        f"replay answered 200 with the original correlation id {c.correlation_id}",
    )
    h.settle()
    p.check(h.event(c)["receive_count"] == 2, "event receive_count = 2 (replay recorded, not processed)")
    p.check(h.logs(c, "EVENT_REPLAY_DETECTED"), "EVENT_REPLAY_DETECTED logged")
    p.check(count(h, c, "application.received") == 1, "one acknowledgement email")
    reused = h.http.post(
        f"{N8N}/webhook/applications",
        json={**c.payload, "expected_salary": "999k"},
        headers={"Idempotency-Key": c.idempotency_key},
    )
    p.check(reused.status_code == 409, "same key with a different body is refused (409 IDEMPOTENCY_KEY_REUSED)")
    missing = h.http.post(f"{N8N}/webhook/applications", json=c.payload)
    p.check(missing.status_code == 400, "missing Idempotency-Key is refused (400)")


@scenario(5, "Weak candidate is rejected by the rules")
def s05(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s05", "Bushra", "Anjum", strength="weak"))
    score = h.one("SELECT score, route FROM hiring.application_scores WHERE application_id = %s", c.application_id)
    p.check(score["route"] == "REJECT", f"rule score {score['score']} routes to REJECT")
    p.check(h.status(c) == "REJECTED", "application REJECTED (AI only advised)")
    p.check(count(h, c, "application.rejected") == 1, "polite rejection notice sent once")


@scenario(6, "A rule changed in the database is used without editing workflows")
def s06(p: Proof) -> None:
    h = p.h
    profile = {
        "skills": ["Manual Testing", "Test Cases", "Postman", "SQL", "Jira", "Jenkins"],
        "experience_years": 3,
        "expected_salary": "1.4 lakh",
        "current_title": "QA Engineer",
        "cover_letter": "Agile QA engineer focused on release quality and API testing for SaaS products.",
    }
    before_version = h.scoring_version("QA_ENG")
    before = apply(
        h,
        h.make(
            "s06-before",
            "Rida",
            "Mansoor",
            "QA_ENG",
            cv_ref=h.upload_cv(QA_STRONG_CV.format(years=3).replace("Selenium and Cypress automation, ", "")),
            **profile,
        ),
    )
    s1 = h.one(
        "SELECT score, route, scoring_version FROM hiring.application_scores WHERE application_id = %s",
        before.application_id,
    )
    try:
        h.set_rule_points("QA_ENG", "automation", 30)  # HR makes test automation weigh more
        after_version = h.scoring_version("QA_ENG")
        p.check(
            after_version == before_version + 1,
            f"scoring version bumped automatically {before_version} -> {after_version}",
        )
        after = apply(
            h,
            h.make(
                "s06-after",
                "Ali",
                "Mustafa",
                "QA_ENG",
                cv_ref=h.upload_cv(QA_STRONG_CV.format(years=3).replace("Selenium and Cypress automation, ", "")),
                **profile,
            ),
        )
        s2 = h.one(
            "SELECT score, route, scoring_version FROM hiring.application_scores WHERE application_id = %s",
            after.application_id,
        )
    finally:
        h.set_rule_points("QA_ENG", "automation", 20)
    p.check(
        s1["route"] == "SHORTLIST" and float(s1["score"]) == 80,
        f"before: score {s1['score']} ({s1['route']}, v{s1['scoring_version']})",
    )
    p.check(
        s2["route"] == "REVIEW" and int(s2["scoring_version"]) == after_version,
        f"after: same profile scores {s2['score']} ({s2['route']}, v{s2['scoring_version']}) with no workflow change",
    )
    p.check(h.status(after) == "SCREENING_REVIEW", "outcome follows the new rule")


# ---- 7-11: failures, retries, error queue, replay ---------------------------------------------------------------
@scenario(7, "Malformed AI output is handled (one retry, then fallback to review)")
def s07(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s07", "Sameer", "Lodhi"), fault="ai:malformed")
    ai = h.one(
        "SELECT * FROM hiring.ai_analyses WHERE application_id = %s ORDER BY created_at DESC LIMIT 1", c.application_id
    )
    p.check(
        ai["status"] == "FALLBACK" and ai["attempts"] == 2,
        f"AI output rejected twice -> FALLBACK ({ai['fallback_reason']})",
    )
    p.check(
        h.one("SELECT route FROM hiring.application_scores WHERE application_id = %s", c.application_id)["route"]
        == "SHORTLIST",
        "rule score unaffected (SHORTLIST)",
    )
    snap = h.snapshot(c)
    p.check(
        snap["status"] == "SCREENING_REVIEW" and "AI analysis unavailable" in snap["review_reason"],
        "routed to manual review instead of trusting bad output",
    )


@scenario(8, "A retryable failure succeeds after retries")
def s08(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s08", "Nida", "Karim"), fault="score:http_503@2")
    attempts = h.logs(c, "RETRY_ATTEMPT")
    p.check(len(attempts) == 2, "two RETRY_ATTEMPT entries (HTTP 503, backoff 2 s then 4 s)")
    p.check(len(h.logs(c, "RETRY_RECOVERED")) == 1, "RETRY_RECOVERED on attempt 3")
    p.check(h.status(c) == "SHORTLISTED" and not h.errors(c), "screening completed; nothing in the error queue")


@scenario(9, "A non-retryable failure goes to the error queue")
def s09(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s09", "Tahir", "Naqvi"), fault="score:http_400")
    errors = h.errors(c)
    p.check(
        len(errors) == 1 and errors[0]["error_class"] == "NON_RETRYABLE",
        f"one NON_RETRYABLE error queued ({errors[0]['error_code'] if errors else '-'})",
    )
    p.check(
        errors[0]["replay_workflow"] == "WF-03" and errors[0]["payload"].get("action"),
        "original action stored for replay",
    )
    p.check(not h.logs(c, "RETRY_ATTEMPT"), "no retries for a permanent error")
    p.check(h.status(c) == "VALIDATED", "application waits in VALIDATED (nothing half-done)")
    SHARED["non_retryable"] = c


@scenario(10, "Retries exhausted -> dead queue")
def s10(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s10", "Saima", "Rauf"), fault="score:http_503")
    for _ in range(6):
        action = h.actions(c, "SCREEN_APPLICATION")[0]
        if action["status"] == "FAILED":
            break
        h.fast_forward(c, "SCREEN_APPLICATION")  # skip the backoff wait (30 s, 1 m, 2 m, 4 m)
        h.settle()
    action = h.actions(c, "SCREEN_APPLICATION")[0]
    p.check(
        action["status"] == "FAILED" and action["attempts"] == 5,
        f"dispatcher gave up after {action['attempts']} attempts with backoff",
    )
    p.check(len(h.logs(c, "RETRY_ATTEMPT")) >= 10, f"{len(h.logs(c, 'RETRY_ATTEMPT'))} backend retries logged in total")
    dead = [e for e in h.errors(c) if e["error_code"] == "ACTION_ATTEMPTS_EXHAUSTED"]
    p.check(dead and dead[0]["status"] == "OPEN", "ACTION_ATTEMPTS_EXHAUSTED in the error queue (replayable via WF-00)")
    SHARED["exhausted"] = c


@scenario(11, "A corrected item is replayed without duplicates")
def s11(p: Proof) -> None:
    h = p.h
    for key in ("non_retryable", "exhausted"):
        c = SHARED.get(key)
        if c is None:
            raise CheckFailed(f"scenario providing {key} did not run")
        h.clear_test_directive(c)  # the cause is fixed
        error = next(e for e in h.errors(c) if e["status"] == "OPEN")
        replay = h.staff(f"errors/{error['id']}/replay", {})
        p.check(
            replay.status_code == 200 and replay.json()["status"] == "RESOLVED",
            f"{error['error_code']} replayed by HR: {replay.json().get('detail')}",
        )
        h.settle()
        scores = h.q("SELECT id FROM hiring.application_scores WHERE application_id = %s", c.application_id)
        p.check(h.status(c) == "SHORTLISTED" and len(scores) == 1, f"{c.label}: shortlisted with exactly one score row")
        p.check(count(h, c, "application.received") == 1, f"{c.label}: still one acknowledgement email")
        resolved = h.one("SELECT status, resolved_by FROM ops.automation_errors WHERE id = %s", error["id"])
        p.check(
            resolved["status"] == "RESOLVED" and resolved["resolved_by"] == STAFF["sana"],
            "error RESOLVED, replay attributed to the staff member",
        )


# ---- 12-15: interviews ----------------------------------------------------------------------------------------
@scenario(12, "Interview invitation reminder, then expiry")
def s12(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s12", "Mahira", "Sultan"))
    p.check(h.fast_forward(c, "INTERVIEW_INVITE_REMINDER"), "two days pass without a slot choice")
    h.settle()
    p.check(count(h, c, "interview.invitation_reminder") == 1, "candidate reminded once")
    h.fast_forward(c, "INTERVIEW_INVITE_EXPIRY")
    h.settle()
    p.check(interview(h, c)["status"] == "EXPIRED", "invitation expired")
    p.check(h.status(c) == "SCREENING_REVIEW", "application back to SCREENING_REVIEW for the recruiter")
    p.check(count(h, c, "staff.review_needed") == 1, "recruiters alerted")


@scenario(13, "Interview cancellation releases the slot and re-invites")
def s13(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s13", "Faisal", "Durrani"))
    booking = book(p, c)
    cancelled = h.portal("POST", "interview/cancel", booking["token"], {"reason": "family emergency"})
    p.check(
        cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED", "candidate cancelled via the link"
    )
    h.settle()
    slot = h.one("SELECT status FROM hiring.interview_slots WHERE id = %s", booking["slot_id"])
    p.check(slot["status"] == "OPEN", "slot released for other candidates")
    p.check(all(a["status"] == "CANCELLED" for a in h.actions(c, "FEEDBACK_REMINDER")), "feedback timers cancelled")
    p.check(interview(h, c)["round"] == 2 and count(h, c, "interview.invitation") == 2, "new invitation (round 2) sent")


@scenario(14, "Missing interview feedback: reminder, then escalation")
def s14(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s14", "Ammar", "Jilani"))
    book(p, c)
    h.fast_forward(c, "FEEDBACK_REMINDER")
    h.settle()
    p.check(count(h, c, "interview.feedback_reminder") == 1, "interviewer reminded")
    h.fast_forward(c, "FEEDBACK_ESCALATION")
    h.settle()
    p.check(count(h, c, "interview.feedback_escalation") == 1, "escalated to HR")
    p.check(
        h.emails(HR_EMAIL, f"[Escalation] Interview feedback overdue: {interview(h, c)['interview_code']}"),
        "HR inbox has the escalation",
    )


@scenario(15, "Candidate fails the interview")
def s15(p: Proof) -> None:
    h = p.h
    c = apply(h, python_dev(h, "s15", "Rameez", "Afridi"))
    book(p, c)
    scorecard(p, c, (2, 2, 1, 2, 2), "NO_HIRE")
    snap = h.snapshot(c)
    p.check(snap["status"] == "REJECTED", f"final score {snap['final_score']} below the review threshold -> REJECTED")
    p.check(count(h, c, "application.rejected") == 1, "rejection notice sent")


# ---- 16-21: offers -------------------------------------------------------------------------------------------
@scenario(16, "Standard offer: one approval level")
def s16(p: Proof) -> None:
    h = p.h
    c = to_offered(p, "s16", "Zara", "Hamdani", expected_salary="200k")
    o = offer(h, c)
    p.check(
        o["required_approval_levels"] == 1 and len(o["approvals"]) == 1, f"PKR {o['monthly_salary']:,} needs one level"
    )
    p.check(count(h, c, "offer.sent") == 1 and o["document_storage_key"], "offer letter generated and emailed once")


@scenario(17, "High salary: two approval levels by different people")
def s17(p: Proof) -> None:
    h = p.h
    c = to_selected(p, "s17", "Shahzaib", "Lashari", expected_salary="320k")
    o = offer(h, c)
    p.check(o["required_approval_levels"] == 2, f"PKR {o['monthly_salary']:,} > threshold -> two levels")
    approve(p, c, 1, AYESHA)
    p.check(offer(h, c)["status"] == "PENDING_APPROVAL", "after L1 the offer still waits")
    same_person = h.staff(f"offers/{o['offer_id']}/approval", {"decision": "APPROVED", "level": 2}, who="ayesha")
    p.check(
        same_person.status_code in (403, 409),
        f"the L1 approver cannot also approve L2 ({same_person.json().get('code')})",
    )
    approve(p, c, 2, OMAR)
    p.check(offer(h, c)["status"] == "SENT", "sent after both levels")


@scenario(18, "Approver rejects the offer")
def s18(p: Proof) -> None:
    h = p.h
    c = to_selected(p, "s18", "Hareem", "Qadri")
    approve(p, c, 1, AYESHA, "REJECTED", "Above the team's budget for this level")
    o = offer(h, c)
    p.check(o["status"] == "REJECTED_BY_APPROVER", "offer stopped with the approver's reason")
    p.check(h.status(c) == "SELECTED" and count(h, c, "offer.sent") == 0, "nothing sent to the candidate")
    p.check(count(h, c, "offer.revision_required") == 1, "HR asked to revise (no automatic re-draft)")
    revised = h.staff(f"applications/{c.application_id}/offers", {"monthly_salary": 190000})
    p.check(revised.status_code == 200 and revised.json()["revision"] == 2, "HR drafted revision 2 for approval")


@scenario(19, "Candidate declines the offer")
def s19(p: Proof) -> None:
    h = p.h
    c = to_offered(p, "s19", "Nabeel", "Sarwar")
    token = h.link_token(c.email, "Your offer from")
    h.portal("POST", "offer/respond", token, {"response": "DECLINE", "message": "Accepted another offer."})
    h.settle()
    p.check(h.status(c) == "DECLINED", "application DECLINED")
    p.check(
        all(a["status"] == "CANCELLED" for a in h.actions(c) if a["action_type"].startswith("OFFER_")),
        "offer reminders and expiry cancelled",
    )
    p.check(count(h, c, "offer.closed_hr") == 1 and count(h, c, "offer.closed") == 1, "HR informed; candidate thanked")


@scenario(20, "Candidate negotiates; HR sends a revised offer")
def s20(p: Proof) -> None:
    h = p.h
    c = to_offered(p, "s20", "Sundas", "Ejaz")
    token = h.link_token(c.email, "Your offer from")
    h.portal("POST", "offer/respond", token, {"response": "NEGOTIATE", "message": "Could we discuss 240k?"})
    h.settle()
    p.check(h.status(c) == "NEGOTIATION", "application in NEGOTIATION")
    p.check(
        count(h, c, "offer.negotiation_hr") == 1 and count(h, c, "offer.negotiation_ack") == 1,
        "HR notified with the message; candidate acknowledged",
    )
    revised = h.staff(f"applications/{c.application_id}/offers", {"monthly_salary": 240000})
    p.check(revised.status_code == 200 and revised.json()["revision"] == 2, "HR drafted revision 2")
    h.settle()
    approve(p, c, 1, AYESHA)
    o = offer(h, c)
    p.check(
        o["revision"] == 2 and o["status"] == "SENT" and o["monthly_salary"] == 240000,
        "revised offer approved and sent",
    )
    p.check(
        h.one("SELECT status FROM hiring.offers WHERE application_id = %s AND revision = 1", c.application_id)["status"]
        == "SUPERSEDED",
        "revision 1 superseded",
    )


@scenario(21, "Unanswered offer: reminder, then expiry")
def s21(p: Proof) -> None:
    h = p.h
    c = to_offered(p, "s21", "Taimoor", "Niazi")
    h.fast_forward(c, "OFFER_REMINDER")
    h.settle()
    p.check(count(h, c, "offer.reminder") == 1, "candidate reminded")
    h.expire_offer_deadline(offer(h, c)["offer_id"])
    h.fast_forward(c, "OFFER_EXPIRY")
    h.settle()
    p.check(h.status(c) == "OFFER_EXPIRED", "offer expired")
    late = h.portal("POST", "offer/respond", h.link_token(c.email, "Your offer from"), {"response": "ACCEPT"})
    p.check(
        late.status_code in (409, 410), f"a late acceptance is refused ({late.status_code} {late.json().get('code')})"
    )
    p.check(count(h, c, "offer.closed") == 1, "candidate told the offer expired")


# ---- 22-23: onboarding -----------------------------------------------------------------------------------------
@scenario(22, "Onboarding runs exactly once, even if the step is delivered twice")
def s22(p: Proof) -> None:
    h = p.h
    c = to_offered(p, "s22", "Kashif", "Rasheed")
    h.portal("POST", "offer/respond", h.link_token(c.email, "Your offer from"), {"response": "ACCEPT"})
    h.settle()
    p.check(h.status(c) == "ONBOARDING", "offer accepted; onboarding started")
    redelivered = h.q(
        """UPDATE ops.scheduled_actions SET status = 'PENDING', run_at = now(), locked_until = NULL, locked_by = NULL
            WHERE application_id = %s AND action_type = 'START_ONBOARDING' RETURNING id""",
        c.application_id,
    )
    p.check(redelivered, "START_ONBOARDING delivered a second time (simulated at-least-once redelivery)")
    h.settle()
    employees = h.q("SELECT employee_code FROM hiring.employees WHERE application_id = %s", c.application_id)
    p.check(len(employees) == 1, f"still exactly one employee ({employees[0]['employee_code']})")
    p.check(count(h, c, "onboarding.welcome") == 1, "still one welcome email")
    p.check(h.logs(c, "DUPLICATE_NOTIFICATION_SUPPRESSED"), "the second welcome was suppressed by its dedupe key")
    SHARED["onboarding_candidate"] = c


@scenario(23, "Overdue onboarding task is visible and its owner reminded")
def s23(p: Proof) -> None:
    h = p.h
    c = SHARED.get("onboarding_candidate")
    if c is None:
        raise CheckFailed("scenario 22 did not provide an onboarding employee")
    employee = h.one("SELECT id, employee_code FROM hiring.employees WHERE application_id = %s", c.application_id)
    h.overdue_task(employee["id"], "collect_documents", 3)
    visible = h.q(
        "SELECT * FROM reporting.v_overdue_onboarding_tasks WHERE employee_code = %s", employee["employee_code"]
    )
    p.check(
        visible and visible[0]["days_overdue"] >= 3,
        f"{employee['employee_code']} task visible in v_overdue_onboarding_tasks",
    )
    subject = f"[Overdue] Collect signed contract and documents for {c.full_name}"
    h.ops_webhook("onboarding-sweep")
    for _ in range(30):
        if h.emails(HR_EMAIL, subject):
            break
        time.sleep(1)
    p.check(h.emails(HR_EMAIL, subject), "owner (HR) reminded by the onboarding sweep")
    h.ops_webhook("onboarding-sweep")
    time.sleep(5)
    p.check(len(h.emails(HR_EMAIL, subject)) == 1, "a second sweep does not remind again within the reminder interval")
    metrics = h.one("SELECT reporting.daily_metrics(current_date) AS m")["m"]
    p.check(
        metrics["overdue_onboarding_tasks"] >= 1,
        f"daily metrics count {metrics['overdue_onboarding_tasks']} overdue task(s)",
    )
