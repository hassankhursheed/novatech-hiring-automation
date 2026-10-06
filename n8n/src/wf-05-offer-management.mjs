// WF-05 Offer Management: draft, approval requests, offer letter + send, reminders, expiry, negotiation and closure.
import {
  Workflow, x, ESC, LAYOUT, FMT, addNotes, execTrigger, pg, set, email, when, route,
  handlerContext, addHandlerTail, addBackendStep,
} from './lib.mjs';

export default function build() {
  const w = new Workflow('WF-05',
    'WF-05: offer drafting, L1/L2 approval requests (signed links), offer letter PDF + sending, reminders, expiry, negotiation and closure notices.');
  const CTX = '$("Build Context").first().json.ctx';
  const S = '$("Start Log & Load State").first().json';
  const H = `${ESC} ${LAYOUT} ${FMT}`;
  const MONEY = 'const money = (v, c) => (c || "PKR") + " " + Number(v).toLocaleString("en-US");';

  w.add(execTrigger('When Called by Dispatcher', [['action', 'object']]));
  w.add(handlerContext('WF-05'));
  w.add(pg('Start Log & Load State', `SELECT api.start_workflow_execution($1::jsonb, 'SUB_WORKFLOW', $2, $3) AS run_id,
       api.application_snapshot(coalesce(NULLIF($4, '')::uuid, o.application_id)) AS app,
       CASE WHEN o.id IS NOT NULL THEN api.offer_snapshot(o.id) END AS offer,
       api.test_directive($5) AS fault_inject,
       (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.timezone') AS timezone,
       (SELECT string_agg(s.email, ',' ORDER BY s.email) FROM hiring.staff_members s
         WHERE s.is_active AND 'HR_ADMIN' = ANY (s.roles)) AS hr_emails
  FROM (SELECT 1) AS one
  LEFT JOIN LATERAL (
    SELECT o.id, o.application_id FROM hiring.offers o
     WHERE CASE WHEN $2 = 'OFFER' THEN o.id = NULLIF($3, '')::uuid ELSE o.application_id = NULLIF($4, '')::uuid END
     ORDER BY o.revision DESC LIMIT 1) AS o ON true`,
  ['JSON.stringify($json.ctx)', '$json.entity_type', '$json.entity_id', '$json.application_id', '$json.ctx.correlation_id']));
  w.add(route('Route by Action Type', '$("Build Context").first().json.action.action_type', [
    'PREPARE_OFFER', 'REQUEST_OFFER_APPROVAL', 'SEND_OFFER', 'OFFER_REMINDER', 'OFFER_FINAL_REMINDER', 'OFFER_EXPIRY',
    'NOTIFY_NEGOTIATION', 'NOTIFY_OFFER_CLOSED',
  ]));
  w.chain('When Called by Dispatcher', 'Build Context', 'Start Log & Load State', 'Route by Action Type');

  const EMAILS = ['Ask HR to Revise (SWF-02)', 'Request Approval (SWF-02)', 'Send Offer (SWF-02)', 'Send Offer Reminder (SWF-02)',
    'Notify HR of Negotiation (SWF-02)', 'Acknowledge Negotiation (SWF-02)', 'Notify HR of Closure (SWF-02)', 'Close With Candidate (SWF-02)'];
  const tail = addHandlerTail(w, 'WF-05', EMAILS);
  const dbFail = (node) => w.connect(node, tail.failed, 1);

  // ---- 0: PREPARE_OFFER (application entered SELECTED) -------------------------------------------------
  w.add(when('Selected?', `${S}.app?.status === "SELECTED"`));
  w.connect('Route by Action Type', 'Selected?', 0);
  w.connect('Selected?', tail.skip, 1);
  w.add(pg('Draft Offer', `SELECT * FROM api.create_offer($1::uuid, '{}'::jsonb, $2::jsonb)`,
    [`${S}.app.application_id`, `JSON.stringify(${CTX})`], { onError: 'continueErrorOutput' }));
  w.connect('Selected?', 'Draft Offer');
  dbFail('Draft Offer');
  w.add(when('Revision Required?', '$json.status === "REVISION_REQUIRED"'));
  w.connect('Draft Offer', 'Revision Required?');
  w.add(email('Ask HR to Revise (SWF-02)', {
    ctx: CTX, template: 'offer.revision_required', entityType: 'OFFER',
    dedupe: '"offer.revision_required:" + $("Draft Offer").first().json.offer_id',
    recipient: `${S}.hr_emails`,
    subject: `"[Action needed] Revise the offer for " + ${S}.app.candidate.full_name`,
    html: `(() => { ${H} const s = ${S}; const o = s.offer || {}; const rejected = (o.approvals || []).find(a => a.decision === "REJECTED"); return layout("<p>The last offer for <b>" + esc(s.app.candidate.full_name) + "</b> (" + esc(s.app.position.title) + ", " + esc(s.app.application_code) + ") was rejected by an approver" + (rejected ? " at level " + rejected.level : "") + ".</p><p>The system will not re-draft it automatically. Please revise the terms (for example the salary) and submit a new revision; it will go through approval again.</p>", "Offer " + esc(o.offer_code || "")); })()`,
    applicationId: `${S}.app.application_id`, entityId: '$("Draft Offer").first().json.offer_id',
  }));
  w.connect('Revision Required?', 'Ask HR to Revise (SWF-02)', 0);
  w.connect('Ask HR to Revise (SWF-02)', tail.notified);
  w.add(set('Result: Offer Drafted', [
    ['action_outcome', 'DONE'],
    ['action_reason', x('"offer " + $("Draft Offer").first().json.offer_code + " " + ($("Draft Offer").first().json.created ? "drafted" : "already open") + " (" + $("Draft Offer").first().json.required_approval_levels + " approval level(s))"')],
  ]));
  w.connect('Revision Required?', 'Result: Offer Drafted', 1);
  w.connect('Result: Offer Drafted', tail.finish);

  // ---- 1: REQUEST_OFFER_APPROVAL -------------------------------------------------------------------------
  // Routed to the reporting manager when eligible at this level, otherwise to the first eligible approver.
  // Any eligible approver can still decide in the portal.
  const APPROVER = `(${S}.offer.next_approvers || []).find(a => a.id === ${S}.offer.reporting_manager?.id) || (${S}.offer.next_approvers || [])[0]`;
  w.add(when('Awaiting Approval?', `${S}.offer?.status === "PENDING_APPROVAL" && ${S}.offer?.next_level`));
  w.connect('Route by Action Type', 'Awaiting Approval?', 1);
  w.connect('Awaiting Approval?', tail.skip, 1);
  w.add(when('Approver Available?', `${APPROVER}`));
  w.connect('Awaiting Approval?', 'Approver Available?');
  w.add(set('No Approver Configured', [
    ['error_class', 'NON_RETRYABLE'],
    ['error_code', 'NO_APPROVER_AVAILABLE'],
    ['error_message', x(`"no active staff member can approve level " + ${S}.offer.next_level + " of offer " + ${S}.offer.offer_code + " (role APPROVER_L" + ${S}.offer.next_level + ", not the creator, not a previous approver)"`)],
  ]));
  w.connect('Approver Available?', 'No Approver Configured', 1);
  w.connect('No Approver Configured', tail.failed);
  const approvalLinkOk = addBackendStep(w, tail, 'Approval Link (SWF-01)', {
    ctx: CTX, path: '/v1/links', entityType: 'OFFER', entityId: `${S}.offer.offer_id`,
    body: `({ purpose: "OFFER_APPROVAL", subject_id: (${APPROVER}).id, entity_id: ${S}.offer.offer_id, expires_at: DateTime.now().plus({ days: 7 }).toISO() })`,
  });
  w.connect('Approver Available?', 'Approval Link (SWF-01)', 0);
  w.add(email('Request Approval (SWF-02)', {
    ctx: CTX, template: 'offer.approval_request', entityType: 'OFFER',
    dedupe: `"offer.approval_request:" + ${S}.offer.offer_id + ":L" + ${S}.offer.next_level`,
    recipient: `(${APPROVER}).email`,
    subject: `"[Approval L" + ${S}.offer.next_level + "] Offer " + ${S}.offer.offer_code + " for " + ${S}.app.candidate.full_name`,
    html: `(() => { ${H} ${MONEY} const s = ${S}; const o = s.offer; const a = ${APPROVER}; return layout("<p>Hi " + esc(a.full_name) + ",</p><p>An offer needs your <b>level " + o.next_level + "</b> approval" + (o.required_approval_levels === 2 ? " (two levels are required because the salary is above " + esc(money(o.approval_threshold_applied, o.currency)) + ")" : "") + ".</p><table cellpadding='4'><tr><td style='color:#667085'>Candidate</td><td><b>" + esc(s.app.candidate.full_name) + "</b> (" + esc(s.app.application_code) + ")</td></tr><tr><td style='color:#667085'>Position</td><td>" + esc(s.app.position.title) + ", " + esc(o.department) + "</td></tr><tr><td style='color:#667085'>Monthly salary</td><td><b>" + esc(money(o.monthly_salary, o.currency)) + "</b> (expected " + esc(s.app.expected_salary ? money(s.app.expected_salary, o.currency) : "-") + ")</td></tr><tr><td style='color:#667085'>Joining date</td><td>" + esc(fmtDate(o.joining_date)) + "</td></tr><tr><td style='color:#667085'>Scores</td><td>screening " + esc(s.app.application_score ?? "-") + ", interview " + esc(s.app.interview_score ?? "-") + ", final " + esc(s.app.final_score ?? "-") + "</td></tr></table><p><a href='" + esc($json.body.url) + "' style='background:#1570ef;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none'>Review and decide</a></p><p style='color:#667085'>A rejection needs a reason and sends the offer back to HR for revision.</p>", "Offer " + esc(o.offer_code) + " revision " + o.revision); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.offer.offer_id`,
  }));
  w.connect(approvalLinkOk, 'Request Approval (SWF-02)');
  w.connect('Request Approval (SWF-02)', tail.notified);

  // ---- 2: SEND_OFFER (all approvals granted) --------------------------------------------------------------
  w.add(when('Approved or Sent?', `["APPROVED", "SENT"].includes(${S}.offer?.status)`));
  w.connect('Route by Action Type', 'Approved or Sent?', 2);
  w.connect('Approved or Sent?', tail.skip, 1);
  const letterOk = addBackendStep(w, tail, 'Offer Letter (SWF-01)', {
    ctx: CTX, path: '/v1/offers/document', entityType: 'OFFER', entityId: `${S}.offer.offer_id`,
    body: `({ offer_id: ${S}.offer.offer_id })`, fault: `${S}.fault_inject || ""`,
  });
  w.connect('Approved or Sent?', 'Offer Letter (SWF-01)', 0);
  w.add(pg('Mark Offer Sent', 'SELECT * FROM api.mark_offer_sent($1::uuid, $2, $3::jsonb)',
    [`${S}.offer.offer_id`, '$json.body.document_key', `JSON.stringify(${CTX})`], { onError: 'continueErrorOutput' }));
  w.connect(letterOk, 'Mark Offer Sent');
  dbFail('Mark Offer Sent');
  const offerLinkOk = addBackendStep(w, tail, 'Offer Link (SWF-01)', {
    ctx: CTX, path: '/v1/links', entityType: 'OFFER', entityId: `${S}.offer.offer_id`,
    body: `({ purpose: "OFFER_RESPONSE", subject_id: ${S}.app.candidate.candidate_id, entity_id: ${S}.offer.offer_id, expires_at: $json.expires_at })`,
  });
  w.connect('Mark Offer Sent', 'Offer Link (SWF-01)');
  w.add(email('Send Offer (SWF-02)', {
    ctx: CTX, template: 'offer.sent', entityType: 'OFFER',
    dedupe: `"offer.sent:" + ${S}.offer.offer_id`,
    recipient: `${S}.app.candidate.email`,
    subject: `"Your offer from " + ${S}.app.company.name + ": " + ${S}.app.position.title`,
    html: `(() => { ${H} ${MONEY} const s = ${S}; const o = s.offer; const sent = $("Mark Offer Sent").first().json; return layout("<p>Dear " + esc(s.app.candidate.full_name) + ",</p><p>Congratulations! We are delighted to offer you the position of <b>" + esc(s.app.position.title) + "</b>.</p><table cellpadding='4'><tr><td style='color:#667085'>Monthly gross salary</td><td><b>" + esc(money(o.monthly_salary, o.currency)) + "</b></td></tr><tr><td style='color:#667085'>Joining date</td><td>" + esc(fmtDate(o.joining_date)) + "</td></tr><tr><td style='color:#667085'>Probation</td><td>" + o.probation_months + " month(s)</td></tr><tr><td style='color:#667085'>Reporting to</td><td>" + esc(o.reporting_manager?.full_name || "-") + "</td></tr></table><p><a href='" + esc($json.body.url) + "' style='background:#1570ef;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none'>View your offer letter and respond</a></p><p>You can accept, decline or ask to discuss the terms. This offer is valid until <b>" + esc(fmt(sent.expires_at, s.timezone)) + "</b>. The link is personal; please do not forward it.</p><p>Kind regards,<br/>" + esc(s.app.company.name) + " Human Resources</p>", "Offer " + esc(o.offer_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.offer.offer_id`,
  }));
  w.connect(offerLinkOk, 'Send Offer (SWF-02)');
  w.connect('Send Offer (SWF-02)', tail.notified);

  // ---- 3/4: OFFER_REMINDER / OFFER_FINAL_REMINDER --------------------------------------------------------
  w.add(when('Offer Still Open?', `${S}.offer?.status === "SENT"`));
  w.connect('Route by Action Type', 'Offer Still Open?', 3);
  w.connect('Route by Action Type', 'Offer Still Open?', 4);
  w.connect('Offer Still Open?', tail.skip, 1);
  const reminderLinkOk = addBackendStep(w, tail, 'Reminder Offer Link (SWF-01)', {
    ctx: CTX, path: '/v1/links', entityType: 'OFFER', entityId: `${S}.offer.offer_id`,
    body: `({ purpose: "OFFER_RESPONSE", subject_id: ${S}.app.candidate.candidate_id, entity_id: ${S}.offer.offer_id, expires_at: ${S}.offer.expires_at })`,
  });
  w.connect('Offer Still Open?', 'Reminder Offer Link (SWF-01)', 0);
  const FINAL = '$("Build Context").first().json.action.action_type === "OFFER_FINAL_REMINDER"';
  w.add(email('Send Offer Reminder (SWF-02)', {
    ctx: CTX, template: 'offer.reminder', entityType: 'OFFER',
    dedupe: `"offer." + (${FINAL} ? "final_reminder" : "reminder") + ":" + ${S}.offer.offer_id`,
    recipient: `${S}.app.candidate.email`,
    subject: `(${FINAL} ? "Final reminder: " : "Reminder: ") + "your offer for " + ${S}.app.position.title + " is waiting"`,
    html: `(() => { ${H} const s = ${S}; const o = s.offer; const final = ${FINAL}; return layout("<p>Dear " + esc(s.app.candidate.full_name) + ",</p><p>" + (final ? "This is a final reminder: your" : "Your") + " offer for <b>" + esc(s.app.position.title) + "</b> is still waiting for your answer. It expires on <b>" + esc(fmt(o.expires_at, s.timezone)) + "</b>.</p><p><a href='" + esc($json.body.url) + "'>View the offer and respond</a></p><p>If you have questions about the terms, choose \\"discuss terms\\" and we will get back to you.</p><p>Kind regards,<br/>" + esc(s.app.company.name) + " Human Resources</p>", "Offer " + esc(o.offer_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.offer.offer_id`,
  }));
  w.connect(reminderLinkOk, 'Send Offer Reminder (SWF-02)');
  w.connect('Send Offer Reminder (SWF-02)', tail.notified);

  // ---- 5: OFFER_EXPIRY ------------------------------------------------------------------------------------
  w.add(pg('Expire Offer', 'SELECT * FROM api.expire_offer($1::uuid, $2::jsonb)',
    ['$("Build Context").first().json.entity_id', `JSON.stringify(${CTX})`], { onError: 'continueErrorOutput' }));
  w.connect('Route by Action Type', 'Expire Offer', 5);
  dbFail('Expire Offer');
  w.add(set('Result: Expiry', [
    ['action_outcome', x('$json.changed ? "DONE" : "SKIPPED"')],
    ['action_reason', x('$json.changed ? "offer expired without a response" : "offer is " + $json.status + "; nothing to expire"')],
  ]));
  w.chain('Expire Offer', 'Result: Expiry', tail.finish);

  // ---- 6: NOTIFY_NEGOTIATION (application entered NEGOTIATION) ---------------------------------------------
  w.add(when('Negotiating?', `${S}.app?.status === "NEGOTIATION"`));
  w.connect('Route by Action Type', 'Negotiating?', 6);
  w.connect('Negotiating?', tail.skip, 1);
  w.add(email('Notify HR of Negotiation (SWF-02)', {
    ctx: CTX, template: 'offer.negotiation_hr', entityType: 'OFFER',
    dedupe: `"offer.negotiation_hr:" + ${S}.offer.offer_id`,
    recipient: `[${S}.hr_emails, ${S}.offer.reporting_manager?.email].filter(Boolean).join(",")`,
    subject: `"[Action needed] " + ${S}.app.candidate.full_name + " wants to discuss offer " + ${S}.offer.offer_code`,
    html: `(() => { ${H} ${MONEY} const s = ${S}; const o = s.offer; return layout("<p><b>" + esc(s.app.candidate.full_name) + "</b> asked to discuss the offer for " + esc(s.app.position.title) + " (" + esc(money(o.monthly_salary, o.currency)) + "/month).</p><blockquote style='border-left:3px solid #d0d5dd;margin:0;padding-left:12px;color:#344054'>" + esc(o.candidate_message || "(no message)") + "</blockquote><p>Next step: either draft a revised offer (it goes through approval again) or close the negotiation with a reason.</p>", "Offer " + esc(o.offer_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.offer.offer_id`,
  }));
  w.add(email('Acknowledge Negotiation (SWF-02)', {
    ctx: CTX, template: 'offer.negotiation_ack', entityType: 'OFFER',
    dedupe: `"offer.negotiation_ack:" + ${S}.offer.offer_id`,
    recipient: `${S}.app.candidate.email`,
    subject: `"We received your message about your offer"`,
    html: `(() => { ${H} const s = ${S}; return layout("<p>Dear " + esc(s.app.candidate.full_name) + ",</p><p>Thank you for your message about the offer for <b>" + esc(s.app.position.title) + "</b>. Our HR team will review it and get back to you shortly.</p><p>Kind regards,<br/>" + esc(s.app.company.name) + " Human Resources</p>", "Offer " + esc(s.offer.offer_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.offer.offer_id`,
  }));
  w.chain('Negotiating?', 'Notify HR of Negotiation (SWF-02)', 'Acknowledge Negotiation (SWF-02)', tail.notified);

  // ---- 7: NOTIFY_OFFER_CLOSED (application entered DECLINED or OFFER_EXPIRED) -------------------------------
  w.add(when('Offer Closed?', `["DECLINED", "OFFER_EXPIRED"].includes(${S}.app?.status)`));
  w.connect('Route by Action Type', 'Offer Closed?', 7);
  w.connect('Offer Closed?', tail.skip, 1);
  w.add(email('Notify HR of Closure (SWF-02)', {
    ctx: CTX, template: 'offer.closed_hr', entityType: 'APPLICATION',
    dedupe: `"offer.closed_hr:" + ${S}.app.application_id + ":" + ${S}.app.status`,
    recipient: `${S}.hr_emails`,
    subject: `"Offer " + (${S}.app.status === "DECLINED" ? "declined" : "expired") + ": " + ${S}.app.candidate.full_name + " (" + ${S}.app.position.title + ")"`,
    html: `(() => { ${H} const s = ${S}; const o = s.offer || {}; return layout("<p>The offer " + esc(o.offer_code || "") + " for <b>" + esc(s.app.candidate.full_name) + "</b> (" + esc(s.app.position.title) + ") " + (s.app.status === "DECLINED" ? "was declined" : "expired without a response") + ".</p>" + (o.candidate_message ? "<p>Candidate message: <i>" + esc(o.candidate_message) + "</i></p>" : "") + "<p>The application is closed. Consider the next candidate in the pipeline for this position.</p>", "Application " + esc(s.app.application_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.app.application_id`,
  }));
  w.add(email('Close With Candidate (SWF-02)', {
    ctx: CTX, template: 'offer.closed', entityType: 'APPLICATION',
    dedupe: `"offer.closed:" + ${S}.app.application_id + ":" + ${S}.app.status`,
    recipient: `${S}.app.candidate.email`,
    subject: `${S}.app.status === "DECLINED" ? "Thank you for letting us know" : "Your offer has expired"`,
    html: `(() => { ${H} const s = ${S}; const body = s.app.status === "DECLINED" ? "<p>Thank you for letting us know your decision about the <b>" + esc(s.app.position.title) + "</b> offer. We respect it and wish you every success. We would be glad to hear from you again in the future.</p>" : "<p>The offer for <b>" + esc(s.app.position.title) + "</b> has expired because we did not receive a response in time. If you are still interested, please reply to this email and our team will get in touch.</p>"; return layout("<p>Dear " + esc(s.app.candidate.full_name) + ",</p>" + body + "<p>Kind regards,<br/>" + esc(s.app.company.name) + " Human Resources</p>", "Application " + esc(s.app.application_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.app.application_id`,
  }));
  w.chain('Offer Closed?', 'Notify HR of Closure (SWF-02)', 'Close With Candidate (SWF-02)', tail.notified);

  // ---- fallback --------------------------------------------------------------------------------------------
  w.add(set('Result: Unknown Action', [
    ['action_outcome', 'FAILED'],
    ['action_reason', x('"WF-05 has no handler for " + $("Build Context").first().json.action.action_type')],
  ]));
  w.connect('Route by Action Type', 'Result: Unknown Action', 8);
  w.connect('Result: Unknown Action', tail.finish);

  addNotes(w, 'WF-05');
  return w.toJSON();
}
