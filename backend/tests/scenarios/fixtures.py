"""Fictional applications (42) with the screening outcome each one must reach.

Expected outcomes follow from the seeded scoring rules (db/seed/010_novatech_config.sql) and the screening policy:
  strong      rule score >= shortlist threshold, CV attached        -> SHORTLISTED
  review band rule score between review and shortlist thresholds    -> SCREENING_REVIEW
  weak        rule score below the review threshold, AI agrees      -> REJECTED
  incomplete  no CV / missing experience or salary                  -> SCREENING_REVIEW (no scoring)
  invalid     unknown position or no usable contact                 -> INVALID (no application)
Inputs deliberately use the messy formats real forms produce: lakh/k salaries, local phone formats, dd/mm/yyyy
dates, skill aliases ("py", "postgres", "drf"), extra whitespace.
"""

from datetime import date, timedelta
from typing import Any

SOON = (date.today() + timedelta(days=30)).strftime("%d/%m/%Y")
LATER = (date.today() + timedelta(days=45)).isoformat()

PY_STRONG_CV = (
    "Backend engineer, {years} years. Built payment APIs for a fintech startup with Python, FastAPI and PostgreSQL.\n"
    "Designed REST APIs, containerised services with Docker, deployed on AWS (ECS, RDS). Git, code review, CI.\n"
    "Mentored two junior developers and cut p95 latency by 40% through query tuning."
)
PY_REVIEW_CV = "Python developer, {years} years. Django and SQL for internal tools; REST APIs; some Docker."
PY_WEAK_CV = "Web designer, {years} year(s). HTML, CSS and WordPress themes for small businesses. Photoshop."
BDE_STRONG_CV = (
    "Business development executive, {years} years selling SaaS and IT services to mid-size companies.\n"
    "Owned B2B sales pipeline in HubSpot CRM, lead generation through prospecting and cold calling,\n"
    "negotiation of annual contracts, market research for new verticals and client presentations."
)
BDE_REVIEW_CV = "Sales associate, {years} year(s). Prospecting, CRM updates, presentations and market research."
BDE_WEAK_CV = "Customer service representative, {years} year(s). Answered phone queries at a retail chain."
QA_STRONG_CV = (
    "QA engineer, {years} years in agile SaaS teams. Manual testing, test cases and test planning,\n"
    "Selenium and Cypress automation, API testing with Postman, SQL checks, Jira bug tracking, Jenkins CI/CD."
)
QA_REVIEW_CV = "Software tester, {years} years. Manual testing, test cases, Cypress basics, Jira, Postman."
QA_WEAK_CV = "Office assistant, {years} year(s). MS Office, data entry and filing."

PROFILES: dict[str, dict[str, Any]] = {
    "PY_DEV/strong": {
        "skills": ["Python", "FastAPI", "postgres", "REST APIs", "Git", "Docker", "AWS"],
        "cv": PY_STRONG_CV,
        "years": 4,
        "salary": "220k",
        "expect": "SHORTLISTED",
    },
    "PY_DEV/strong-alias": {
        "skills": "py, drf, mysql, rest apis, docker, gcp, github",
        "cv": PY_STRONG_CV,
        "years": "5 years",
        "salary": "2.5 lakh",
        "expect": "SHORTLISTED",
    },
    "PY_DEV/review": {
        "skills": ["Python", "Django", "SQL", "REST APIs", "Docker"],
        "cv": PY_REVIEW_CV,
        "years": 2,
        "salary": "160,000",
        "expect": "SCREENING_REVIEW",
    },
    "PY_DEV/weak": {
        "skills": ["HTML", "CSS", "WordPress"],
        "cv": PY_WEAK_CV,
        "years": 1,
        "salary": "90k",
        "expect": "REJECTED",
    },
    "BDE/strong": {
        "skills": ["B2B Sales", "Lead Generation", "Negotiation", "HubSpot", "Communication", "Market Research"],
        "cv": BDE_STRONG_CV,
        "years": 3,
        "salary": "1.2 lakh",
        "expect": "SHORTLISTED",
    },
    "BDE/review": {
        "skills": ["Sales", "Prospecting", "CRM", "Presentation", "Market Research"],
        "cv": BDE_REVIEW_CV,
        "years": 1,
        "salary": "85000",
        "expect": "SCREENING_REVIEW",
    },
    "BDE/weak": {
        "skills": ["Customer Service"],
        "cv": BDE_WEAK_CV,
        "years": 1,
        "salary": "60k",
        "expect": "REJECTED",
    },
    "QA_ENG/strong": {
        "skills": ["Manual Testing", "Selenium", "Test Cases", "Postman", "SQL", "Jira", "Jenkins"],
        "cv": QA_STRONG_CV,
        "years": 3,
        "salary": "1.6 lakh",
        "expect": "SHORTLISTED",
    },
    "QA_ENG/review": {
        "skills": ["Manual Testing", "Cypress", "Test Cases", "Jira", "Postman"],
        "cv": QA_REVIEW_CV,
        "years": 2,
        "salary": "120k",
        "expect": "SCREENING_REVIEW",
    },
    "QA_ENG/weak": {
        "skills": ["MS Office", "Data Entry"],
        "cv": QA_WEAK_CV,
        "years": 1,
        "salary": "70k",
        "expect": "REJECTED",
    },
}

NAMES = [
    ("Ahmed", "Raza"),
    ("Sara", "Iqbal"),
    ("Bilal", "Shah"),
    ("Mahnoor", "Aslam"),
    ("Hassan", "Mirza"),
    ("Iqra", "Nadeem"),
    ("Danish", "Kamal"),
    ("Fiza", "Rehman"),
    ("Talha", "Javed"),
    ("Noor", "Fatima"),
    ("Saad", "Anwar"),
    ("Hina", "Butt"),
    ("Zeeshan", "Haider"),
    ("Maryam", "Yousaf"),
    ("Faraz", "Akhtar"),
    ("Kiran", "Abbasi"),
    ("Umer", "Farooqi"),
    ("Laiba", "Sheikh"),
    ("Junaid", "Malik"),
    ("Ayesha", "Riaz"),
    ("Hamid", "Chaudhry"),
    ("Sana", "Qamar"),
    ("Waqas", "Gill"),
    ("Rabia", "Hashmi"),
    ("Kamran", "Baig"),
    ("Sobia", "Latif"),
    ("Imran", "Sadiq"),
    ("Amna", "Zubair"),
    ("Asad", "Mehmood"),
    ("Zoya", "Khalid"),
    ("Naveed", "Akram"),
    ("Mehwish", "Tariq"),
    ("Shoaib", "Ghani"),
    ("Anam", "Saleem"),
    ("Rizwan", "Arif"),
    ("Hafsa", "Noman"),
    ("Adeel", "Kazmi"),
    ("Sidra", "Waheed"),
    ("Fahad", "Rana"),
    ("Huma", "Pervaiz"),
    ("Arslan", "Bhatti"),
    ("Ifrah", "Sohail"),
]

PHONE_FORMATS = ["0300-{n}", "+92 321 {n}", "92-333-{n}", "0345{n}", "(0312) {n}"]


def bulk_fixtures() -> list[dict[str, Any]]:
    """42 applications: per position 4 strong, 3 review band, 3 weak, 2 incomplete; plus 6 invalid."""
    plan: list[tuple[str, int]] = []
    for position in ("PY_DEV", "BDE", "QA_ENG"):
        strong_variants = ["strong", "strong-alias"] if position == "PY_DEV" else ["strong"]
        for i in range(4):
            plan.append((f"{position}/{strong_variants[i % len(strong_variants)]}", 1))
        plan += [(f"{position}/review", 1)] * 3 + [(f"{position}/weak", 1)] * 3 + [(f"{position}/incomplete", 1)] * 2
    plan += [("invalid/unknown-position", 1)] * 3 + [("invalid/no-contact", 1)] * 3

    fixtures: list[dict[str, Any]] = []
    for index, (kind, _) in enumerate(plan):
        first, last = NAMES[index % len(NAMES)]
        position = kind.split("/")[0]
        if kind.startswith("invalid/unknown-position"):
            fixtures.append(
                {
                    "kind": kind,
                    "first": first,
                    "last": last,
                    "position": "Blockchain Wizard",
                    "fields": {"experience_years": 3, "skills": ["Solidity"], "expected_salary": "300k"},
                    "cv": None,
                    "expect": "INVALID",
                }
            )
            continue
        if kind.startswith("invalid/no-contact"):
            fixtures.append(
                {
                    "kind": kind,
                    "first": first,
                    "last": last,
                    "position": "PY_DEV",
                    "fields": {"email": "not-an-email", "phone": "12", "experience_years": 2},
                    "cv": None,
                    "expect": "INVALID",
                }
            )
            continue
        if kind.endswith("/incomplete"):
            base = PROFILES[f"{position}/review"]
            fixtures.append(
                {
                    "kind": kind,
                    "first": first,
                    "last": last,
                    "position": position,
                    "fields": {"skills": base["skills"]},  # no CV, no experience, no salary
                    "cv": None,
                    "expect": "SCREENING_REVIEW",
                }
            )
            continue
        profile = PROFILES[kind]
        fields = {
            "skills": profile["skills"],
            "experience_years": profile["years"],
            "expected_salary": profile["salary"],
            "available_from": SOON if index % 2 else LATER,
            "current_title": profile["cv"].split(",")[0],
            "city": ["Lahore", "Karachi", "Islamabad", "Faisalabad"][index % 4],
            "cover_letter": (
                f"I am applying for the role because it matches my {profile['years']} years of experience. "
                "I enjoy owning outcomes end to end and working closely with product and engineering teams."
            ),
        }
        years = str(profile["years"]).split()[0]
        fixtures.append(
            {
                "kind": kind,
                "first": first,
                "last": last,
                "position": position,
                "fields": fields,
                "cv": profile["cv"].format(years=years),
                "expect": profile["expect"],
                "phone_format": PHONE_FORMATS[index % len(PHONE_FORMATS)],
            }
        )
    return fixtures
