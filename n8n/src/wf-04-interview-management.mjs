// WF-04 Interview Management: invitation, reminders, expiry, booking finalisation, feedback chasing, evaluation.
import {
  Workflow, x, ESC, LAYOUT, FMT, sticky, execTrigger, pg, set, email, when, route,
  handlerContext, addHandlerTail, addBackendStep,
} from './lib.mjs';

export default function build() {
  const w = new Workflow('WF-04',
    'WF-04: interview invitations (signed slot links), reminders, expiry, calendar + confirmation, interviewer scorecard links, feedback reminders/escalation and post-interview evaluation (30/70 weighted, configurable).');
  const CTX = '$("Build Context").first().json.ctx';
  const S = '$("Start Log & Load State").first().json';
  const H = `${ESC} ${LAYOUT} ${FMT}`;

  w.add(execTrigger('When Called by Dispatcher', [['action', 'object']]));
  w.add(handlerContext('WF-04'));
  w.add(pg('Start Log & Load State', `SELECT api.start_workflow_execution($1::jsonb, 'SUB_WORKFLOW', $2, $3) AS run_id,
       api.application_snapshot(coalesce(NULLIF($4, '')::uuid, iv.application_id)) AS app,
       iv.snapshot AS interview,
       (SELECT sa.run_at FROM ops.scheduled_actions sa
         WHERE sa.entity_id = iv.id AND sa.action_type = 'INTERVIEW_INVITE_EXPIRY' AND sa.status IN ('PENDING', 'RUNNING')
         ORDER BY sa.run_at LIMIT 1) AS invite_expires_at,
       api.test_directive($5) AS fault_inject,
       (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.timezone') AS timezone,
       (SELECT string_agg(s.email, ',' ORDER BY s.email) FROM hiring.staff_members s
         WHERE s.is_active AND 'HR_ADMIN' = ANY (s.roles)) AS hr_emails
  FROM (SELECT 1) AS one
  LEFT JOIN LATERAL (
    SELECT i.id, i.application_id, api.interview_snapshot(i.id) AS snapshot
      FROM hiring.interviews i
     WHERE CASE WHEN $2 = 'INTERVIEW' THEN i.id = NULLIF($3, '')::uuid ELSE i.application_id = NULLIF($4, '')::uuid END
     ORDER BY i.round DESC LIMIT 1) AS iv ON true`,
  [`JSON.stringify($json.ctx)`, '$json.entity_type', '$json.entity_id', '$json.application_id', '$json.ctx.correlation_id']));
  w.add(route('Route by Action Type', '$("Build Context").first().json.action.action_type', [
    'INVITE_TO_INTERVIEW', 'INTERVIEW_INVITE_REMINDER', 'INTERVIEW_INVITE_EXPIRY', 'FINALIZE_INTERVIEW_BOOKING',
    'FEEDBACK_REMINDER', 'FEEDBACK_ESCALATION', 'EVALUATE_INTERVIEW',
  ]));
  w.chain('When Called by Dispatcher', 'Build Context', 'Start Log & Load State', 'Route by Action Type');

  const EMAILS = ['Send Invitation (SWF-02)', 'Send Invitation Reminder (SWF-02)', 'Confirm to Candidate (SWF-02)',
    'Brief Interviewer (SWF-02)', 'Remind Interviewer (SWF-02)', 'Escalate to HR (SWF-02)'];
  const tail = addHandlerTail(w, 'WF-04', EMAILS);
  const dbFail = (node) => w.connect(node, tail.failed, 1);

  // ---- 0: INVITE_TO_INTERVIEW (application entered SHORTLISTED) ----------------------------------------
  w.add(when('Shortlisted?', `${S}.app?.status === "SHORTLISTED"`));
  w.connect('Route by Action Type', 'Shortlisted?', 0);
  w.connect('Shortlisted?', tail.skip, 1);
  w.add(pg('Create Invitation', 'SELECT * FROM api.create_interview_invitation($1::uuid, $2::jsonb)',
    [`${S}.app.application_id`, `JSON.stringify(${CTX})`], { onError: 'continueErrorOutput' }));
  w.connect('Shortlisted?', 'Create Invitation');
  dbFail('Create Invitation');
  const inviteLinkOk = addBackendStep(w, tail, 'Invitation Link (SWF-01)', {
    ctx: CTX, path: '/v1/links', entityType: 'INTERVIEW', entityId: '$json.interview_id',
    body: `({ purpose: "INTERVIEW_SLOT", subject_id: ${S}.app.candidate.candidate_id, entity_id: $json.interview_id, expires_at: $json.invite_expires_at })`,
  });
  w.connect('Create Invitation', 'Invitation Link (SWF-01)');
  w.add(email('Send Invitation (SWF-02)', {
    ctx: CTX, template: 'interview.invitation', entityType: 'INTERVIEW',
    dedupe: '"interview.invite:" + $("Create Invitation").first().json.interview_id',
    recipient: `${S}.app.candidate.email`,
    subject: `"Interview invitation: " + ${S}.app.position.title + " at " + ${S}.app.company.name`,
    html: `(() => { ${H} const s = ${S}; const inv = $("Create Invitation").first().json; const url = $json.body.url; return layout("<p>Dear " + esc(s.app.candidate.full_name) + ",</p><p>Thank you for your application for <b>" + esc(s.app.position.title) + "</b> (reference " + esc(s.app.application_code) + "). We would like to invite you to an interview with " + esc(inv.interviewer_name) + ".</p><p><a href='" + esc(url) + "' style='background:#1570ef;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none'>Choose your interview slot</a></p><p>Please pick a time before <b>" + esc(fmt(inv.invite_expires_at, s.timezone)) + "</b>. This link is personal; please do not forward it.</p><p>Kind regards,<br/>" + esc(s.app.company.name) + " Talent Team</p>", "Interview " + esc(inv.interview_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: '$("Create Invitation").first().json.interview_id',
  }));
  w.connect(inviteLinkOk, 'Send Invitation (SWF-02)');
  w.connect('Send Invitation (SWF-02)', tail.notified);

  // ---- 1: INTERVIEW_INVITE_REMINDER ----------------------------------------------------------------------
  w.add(when('Still Invited?', `${S}.interview?.status === "INVITED"`));
  w.connect('Route by Action Type', 'Still Invited?', 1);
  w.connect('Still Invited?', tail.skip, 1);
  const reminderLinkOk = addBackendStep(w, tail, 'Reminder Link (SWF-01)', {
    ctx: CTX, path: '/v1/links', entityType: 'INTERVIEW', entityId: `${S}.interview.interview_id`,
    body: `({ purpose: "INTERVIEW_SLOT", subject_id: ${S}.app.candidate.candidate_id, entity_id: ${S}.interview.interview_id, expires_at: ${S}.invite_expires_at })`,
  });
  w.connect('Still Invited?', 'Reminder Link (SWF-01)');
  w.add(email('Send Invitation Reminder (SWF-02)', {
    ctx: CTX, template: 'interview.invitation_reminder', entityType: 'INTERVIEW',
    dedupe: `"interview.invite_reminder:" + ${S}.interview.interview_id`,
    recipient: `${S}.app.candidate.email`,
    subject: `"Reminder: please choose your interview slot for " + ${S}.app.position.title`,
    html: `(() => { ${H} const s = ${S}; return layout("<p>Dear " + esc(s.app.candidate.full_name) + ",</p><p>We have not yet received your interview slot choice for <b>" + esc(s.app.position.title) + "</b>.</p><p><a href='" + esc($json.body.url) + "'>Choose your interview slot</a></p><p>The invitation expires on <b>" + esc(fmt(s.invite_expires_at, s.timezone)) + "</b>. If you are no longer interested, you can ignore this email.</p><p>Kind regards,<br/>" + esc(s.app.company.name) + " Talent Team</p>", "Interview " + esc(s.interview.interview_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.interview.interview_id`,
  }));
  w.connect(reminderLinkOk, 'Send Invitation Reminder (SWF-02)');
  w.connect('Send Invitation Reminder (SWF-02)', tail.notified);

  // ---- 2: INTERVIEW_INVITE_EXPIRY -----------------------------------------------------------------------
  w.add(pg('Expire Invitation', 'SELECT * FROM api.expire_interview_invitation($1::uuid, $2::jsonb)',
    [`$("Build Context").first().json.entity_id`, `JSON.stringify(${CTX})`], { onError: 'continueErrorOutput' }));
  w.connect('Route by Action Type', 'Expire Invitation', 2);
  dbFail('Expire Invitation');
  w.add(set('Result: Expiry', [
    ['action_outcome', x('$json.changed ? "DONE" : "SKIPPED"')],
    ['action_reason', x('$json.changed ? "invitation expired; application returned to screening review" : "invitation was already " + $json.status')],
  ]));
  w.chain('Expire Invitation', 'Result: Expiry', tail.finish);

  // ---- 3: FINALIZE_INTERVIEW_BOOKING (application entered INTERVIEW_SCHEDULED) -------------------------------
  w.add(when('Interview Confirmed?', `${S}.interview?.status === "CONFIRMED"`));
  w.connect('Route by Action Type', 'Interview Confirmed?', 3);
  w.connect('Interview Confirmed?', tail.skip, 1);
  // Calendar provider: the built-in provider issues a stable event id and an "add to calendar" link in the
  // emails. To use Google Calendar/Outlook, replace this node with the provider node and keep the event id call.
  w.add(pg('Record Calendar Event', 'SELECT api.record_calendar_event($1::uuid, $2, $3::jsonb) AS calendar_event_id',
    [`${S}.interview.interview_id`, `${S}.interview.calendar_event_id || ("novatech-" + ${S}.interview.interview_code)`, `JSON.stringify(${CTX})`],
    { onError: 'continueErrorOutput' }));
  w.connect('Interview Confirmed?', 'Record Calendar Event');
  dbFail('Record Calendar Event');
  const feedbackLinkOk = addBackendStep(w, tail, 'Scorecard Link (SWF-01)', {
    ctx: CTX, path: '/v1/links', entityType: 'INTERVIEW', entityId: `${S}.interview.interview_id`,
    body: `({ purpose: "INTERVIEW_FEEDBACK", subject_id: ${S}.interview.interviewer.id, entity_id: ${S}.interview.interview_id, expires_at: DateTime.fromISO(${S}.interview.scheduled_end).plus({ days: 14 }).toISO() })`,
  });
  w.connect('Record Calendar Event', 'Scorecard Link (SWF-01)');
  const GCAL = `const gcal = (title, start, end, details) => "https://calendar.google.com/calendar/render?action=TEMPLATE&text=" + encodeURIComponent(title) + "&dates=" + DateTime.fromISO(start).toUTC().toFormat("yyyyLLdd'T'HHmmss'Z'") + "/" + DateTime.fromISO(end).toUTC().toFormat("yyyyLLdd'T'HHmmss'Z'") + "&details=" + encodeURIComponent(details);`;
  w.add(email('Confirm to Candidate (SWF-02)', {
    ctx: CTX, template: 'interview.confirmed', entityType: 'INTERVIEW',
    dedupe: `"interview.confirmed:" + ${S}.interview.interview_id`,
    recipient: `${S}.app.candidate.email`,
    subject: `"Interview confirmed: " + ${S}.app.position.title + ", " + DateTime.fromISO(${S}.interview.scheduled_start).setZone(${S}.timezone).toFormat("d LLL, h:mm a")`,
    html: `(() => { ${H} ${GCAL} const s = ${S}; const iv = s.interview; const where = iv.mode === "ONLINE" ? (iv.meeting_url ? "Online: <a href='" + esc(iv.meeting_url) + "'>" + esc(iv.meeting_url) + "</a>" : "Online (the meeting link will follow)") : "On site at " + esc(s.app.company.name); return layout("<p>Dear " + esc(s.app.candidate.full_name) + ",</p><p>Your interview for <b>" + esc(s.app.position.title) + "</b> is confirmed.</p><table cellpadding='4'><tr><td style='color:#667085'>When</td><td><b>" + esc(fmt(iv.scheduled_start, s.timezone)) + "</b> (" + esc(s.timezone) + ")</td></tr><tr><td style='color:#667085'>Where</td><td>" + where + "</td></tr><tr><td style='color:#667085'>Interviewer</td><td>" + esc(iv.interviewer.full_name) + "</td></tr></table><p><a href='" + esc(gcal("Interview: " + s.app.position.title + " (" + s.app.company.name + ")", iv.scheduled_start, iv.scheduled_end, "Reference " + iv.interview_code)) + "'>Add to Google Calendar</a></p><p>If you need to cancel, please use the link in your invitation email so the slot can be offered to someone else.</p><p>Good luck!<br/>" + esc(s.app.company.name) + " Talent Team</p>", "Interview " + esc(iv.interview_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.interview.interview_id`,
  }));
  w.connect(feedbackLinkOk, 'Confirm to Candidate (SWF-02)');
  w.add(email('Brief Interviewer (SWF-02)', {
    ctx: CTX, template: 'interview.interviewer_brief', entityType: 'INTERVIEW',
    dedupe: `"interview.interviewer_brief:" + ${S}.interview.interview_id`,
    recipient: `${S}.interview.interviewer.email`,
    subject: `"Interview booked: " + ${S}.app.candidate.full_name + " (" + ${S}.app.position.title + ")"`,
    html: `(() => { ${H} ${GCAL} const s = ${S}; const iv = s.interview; const url = $("Scorecard Link (SWF-01)").first().json.body.url; return layout("<p>Hi " + esc(iv.interviewer.full_name) + ",</p><p>An interview has been booked with you.</p><table cellpadding='4'><tr><td style='color:#667085'>Candidate</td><td><b>" + esc(s.app.candidate.full_name) + "</b> (" + esc(s.app.application_code) + ")</td></tr><tr><td style='color:#667085'>Position</td><td>" + esc(s.app.position.title) + "</td></tr><tr><td style='color:#667085'>When</td><td><b>" + esc(fmt(iv.scheduled_start, s.timezone)) + "</b></td></tr><tr><td style='color:#667085'>Screening score</td><td>" + esc(s.app.application_score ?? "-") + "</td></tr></table><p><a href='" + esc(gcal("Interview: " + s.app.candidate.full_name, iv.scheduled_start, iv.scheduled_end, "Scorecard: " + url)) + "'>Add to Google Calendar</a></p><p>After the interview, please submit the scorecard (5 criteria, 1 to 5, plus your recommendation):<br/><a href='" + esc(url) + "' style='background:#1570ef;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none;display:inline-block;margin-top:8px'>Open scorecard</a></p>", "Interview " + esc(iv.interview_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.interview.interview_id`,
  }));
  w.chain('Confirm to Candidate (SWF-02)', 'Brief Interviewer (SWF-02)', tail.notified);

  // ---- 4: FEEDBACK_REMINDER -----------------------------------------------------------------------------
  const FEEDBACK_MISSING = `${S}.interview?.status === "CONFIRMED" && !${S}.interview?.has_feedback`;
  w.add(when('Feedback Still Missing?', FEEDBACK_MISSING));
  w.connect('Route by Action Type', 'Feedback Still Missing?', 4);
  w.connect('Feedback Still Missing?', tail.skip, 1);
  const reminderScorecardOk = addBackendStep(w, tail, 'Reminder Scorecard Link (SWF-01)', {
    ctx: CTX, path: '/v1/links', entityType: 'INTERVIEW', entityId: `${S}.interview.interview_id`,
    body: `({ purpose: "INTERVIEW_FEEDBACK", subject_id: ${S}.interview.interviewer.id, entity_id: ${S}.interview.interview_id, expires_at: DateTime.now().plus({ days: 14 }).toISO() })`,
  });
  w.connect('Feedback Still Missing?', 'Reminder Scorecard Link (SWF-01)');
  w.add(email('Remind Interviewer (SWF-02)', {
    ctx: CTX, template: 'interview.feedback_reminder', entityType: 'INTERVIEW',
    dedupe: `"interview.feedback_reminder:" + ${S}.interview.interview_id`,
    recipient: `${S}.interview.interviewer.email`,
    subject: `"Reminder: scorecard for " + ${S}.app.candidate.full_name + " is due"`,
    html: `(() => { ${H} const s = ${S}; const iv = s.interview; return layout("<p>Hi " + esc(iv.interviewer.full_name) + ",</p><p>We have not received your scorecard for <b>" + esc(s.app.candidate.full_name) + "</b> (" + esc(s.app.position.title) + "), interviewed on " + esc(fmt(iv.scheduled_start, s.timezone)) + ". The candidate cannot move forward until it is submitted.</p><p><a href='" + esc($json.body.url) + "'>Submit the scorecard</a></p>", "Interview " + esc(iv.interview_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.interview.interview_id`,
  }));
  w.connect(reminderScorecardOk, 'Remind Interviewer (SWF-02)');
  w.connect('Remind Interviewer (SWF-02)', tail.notified);

  // ---- 5: FEEDBACK_ESCALATION ---------------------------------------------------------------------------
  w.add(when('Feedback Still Missing at Escalation?', FEEDBACK_MISSING));
  w.connect('Route by Action Type', 'Feedback Still Missing at Escalation?', 5);
  w.connect('Feedback Still Missing at Escalation?', tail.skip, 1);
  w.add(email('Escalate to HR (SWF-02)', {
    ctx: CTX, template: 'interview.feedback_escalation', entityType: 'INTERVIEW',
    dedupe: `"interview.feedback_escalation:" + ${S}.interview.interview_id`,
    recipient: `${S}.hr_emails`,
    subject: `"[Escalation] Interview feedback overdue: " + ${S}.interview.interview_code`,
    html: `(() => { ${H} const s = ${S}; const iv = s.interview; return layout("<p>Interview feedback is overdue and a reminder did not help.</p><table cellpadding='4'><tr><td style='color:#667085'>Interview</td><td><b>" + esc(iv.interview_code) + "</b> on " + esc(fmt(iv.scheduled_start, s.timezone)) + "</td></tr><tr><td style='color:#667085'>Interviewer</td><td>" + esc(iv.interviewer.full_name) + " (" + esc(iv.interviewer.email) + ")</td></tr><tr><td style='color:#667085'>Candidate</td><td>" + esc(s.app.candidate.full_name) + " (" + esc(s.app.application_code) + ")</td></tr></table><p>Please follow up with the interviewer, or submit the feedback on their behalf as HR admin.</p>", "Correlation id " + esc(s.app.correlation_id)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.interview.interview_id`,
  }));
  w.connect('Feedback Still Missing at Escalation?', 'Escalate to HR (SWF-02)');
  w.connect('Escalate to HR (SWF-02)', tail.notified);

  // ---- 6: EVALUATE_INTERVIEW (application entered INTERVIEWED) --------------------------------------------
  w.add(when('Interviewed?', `${S}.app?.status === "INTERVIEWED"`));
  w.connect('Route by Action Type', 'Interviewed?', 6);
  w.connect('Interviewed?', tail.skip, 1);
  const evaluateOk = addBackendStep(w, tail, 'Evaluate Interview (SWF-01)', {
    ctx: CTX, path: '/v1/interviews/evaluate', entityType: 'APPLICATION', entityId: `${S}.app.application_id`,
    body: `({ application_id: ${S}.app.application_id })`, fault: `${S}.fault_inject || ""`,
  });
  w.connect('Interviewed?', 'Evaluate Interview (SWF-01)');
  w.add(pg('Apply Interview Decision', 'SELECT * FROM api.apply_interview_decision($1::uuid, $2::jsonb, $3::jsonb)',
    [`${S}.app.application_id`, 'JSON.stringify({ decision: $json.body.decision, reason: $json.body.reason, final_score: $json.body.final_score, policy_version: $json.body.policy_version })', `JSON.stringify(${CTX})`],
    { onError: 'continueErrorOutput' }));
  w.connect(evaluateOk, 'Apply Interview Decision');
  dbFail('Apply Interview Decision');
  w.add(set('Result: Decision Applied', [
    ['action_outcome', 'DONE'],
    ['action_reason', x('"interview decision: " + $json.to_status + ($json.changed ? "" : " (already applied)")')],
  ]));
  w.chain('Apply Interview Decision', 'Result: Decision Applied', tail.finish);

  // ---- fallback --------------------------------------------------------------------------------------------
  w.add(set('Result: Unknown Action', [
    ['action_outcome', 'FAILED'],
    ['action_reason', x('"WF-04 has no handler for " + $("Build Context").first().json.action.action_type')],
  ]));
  w.connect('Route by Action Type', 'Result: Unknown Action', 7);
  w.connect('Result: Unknown Action', tail.finish);

  w.add(sticky('Overview', '## WF-04 Interview Management\nCalled by **WF-00** for interview actions. Every branch first re-checks the current state (a stale timer is **SKIPPED**).\n- **Invite**: `api.create_interview_invitation` (idempotent per round, schedules reminder + expiry) → signed slot link → email.\n- **Booking**: the candidate confirms in the portal (`api.confirm_interview_slot`), then this workflow records the calendar event, confirms to the candidate and sends the interviewer a scorecard link.\n- **Feedback**: reminder, then escalation to HR. Submitting the scorecard cancels both timers.\n- **Evaluation**: backend combined score (weights and thresholds are settings) → `api.apply_interview_decision`.\nRetryable failures return FAILED (dispatcher backoff); permanent ones go to the error queue (SWF-03). Emails are sent at most once per dedupe key.', { width: 620, height: 340 }));
  return w.toJSON();
}
