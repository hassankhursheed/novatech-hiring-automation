"""Offer letter PDF, rendered from the approved offer (api.offer_snapshot).

Rendering is deterministic (ReportLab invariant mode): the same offer always produces the same bytes, so
re-generating on a retry or replay does not change the document or its checksum.
"""

import hashlib
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@dataclass(frozen=True)
class OfferDocument:
    content: bytes
    sha256: str


def _fmt_date(value: Any) -> str:
    if not value:
        return "-"
    if isinstance(value, str):
        value = datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)
    return value.strftime("%d %B %Y")


def _money(amount: Any, currency: str) -> str:
    return f"{currency} {int(amount):,}"


def render_offer_letter(offer: dict[str, Any], *, company: str, careers_email: str, issued_on: date) -> OfferDocument:
    app = offer["application"]
    candidate = app["candidate"]
    position = app["position"]
    manager = offer.get("reporting_manager") or {}
    currency = str(offer["currency"]).strip()

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10.5, leading=15)
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=18, spaceAfter=4)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, textColor=colors.grey)

    def p(text: str, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(text, style)

    e = escape
    terms = [
        ["Position", e(position["title"])],
        ["Department", e(offer["department"])],
        ["Monthly gross salary", e(_money(offer["monthly_salary"], currency))],
        ["Joining date", e(_fmt_date(offer["joining_date"]))],
        ["Probation period", f"{offer['probation_months']} month(s)"],
        [
            "Reporting to",
            e(manager.get("full_name", "-") + (f", {manager['job_title']}" if manager.get("job_title") else "")),
        ],
    ]
    validity = (
        f"This offer is valid until <b>{e(_fmt_date(offer['expires_at']))}</b>."
        if offer.get("expires_at")
        else "This offer is valid for the period stated in the email that accompanies it."
    )
    table = Table(terms, colWidths=[55 * mm, 105 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10.5),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0D5DD")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    story = [
        p(e(company), title),
        p(f"Offer of employment &middot; Reference {e(offer['offer_code'])} (revision {offer['revision']})", small),
        Spacer(1, 8 * mm),
        p(f"Date: {e(_fmt_date(issued_on))}"),
        Spacer(1, 4 * mm),
        p(f"Dear {e(candidate['full_name'])},"),
        p(
            f"We are pleased to offer you the position of <b>{e(position['title'])}</b> at {e(company)}, "
            "on the terms below."
        ),
        Spacer(1, 3 * mm),
        table,
        Spacer(1, 5 * mm),
        p(validity),
        p(
            "To accept, decline or discuss this offer, please use the secure link in the email we sent you. "
            f"If you have any questions, contact us at {e(careers_email)}."
        ),
        p(
            "This offer is subject to the verification of your documents and to the company's standard terms of "
            "employment, which will be shared with you on your first day."
        ),
        Spacer(1, 10 * mm),
        p("Yours sincerely,"),
        p(f"Human Resources<br/>{e(company)}"),
        Spacer(1, 12 * mm),
        p(f"Application {e(app['application_code'])} &middot; Candidate {e(candidate['candidate_code'])}", small),
    ]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"Offer {offer['offer_code']}",
        author=company,
        invariant=1,  # fixed document id and timestamps: identical input gives identical bytes
    )
    doc.build(story)
    content = buffer.getvalue()
    return OfferDocument(content=content, sha256=hashlib.sha256(content).hexdigest())
