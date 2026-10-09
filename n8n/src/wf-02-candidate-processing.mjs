// WF-02 Candidate Processing: stores a submission and keeps the candidate informed by email.
//   intake            (called by WF-01)  persist the application, acknowledge it
//   SEND_REJECTION_NOTICE (WF-00)        the application was rejected
//   NOTIFY_CANDIDATE      (WF-00)        a status update (under review, interview done, selected, ...)
import { Workflow, x, ESC, LAYOUT, addNotes, execTrigger, pg, set, exec, email, when, route } from './lib.mjs';

// Status updates by template key (hiring.application_statuses.candidate_notice). Each returns { subject, body }.
// `s` is the application snapshot, `from` the previous status. Wording is plain and promises nothing the process
// does not do.
const UPDATES = `const UPDATES = {
  "application.under_review": (s, from) => from === "SHORTLISTED" ? ({
    subject: "Your interview invitation for " + s.app.position.title + " has expired",
    body: "<p>The interview invitation for <b>" + esc(s.app.position.title) + "</b> expired before a time was chosen, so your application (reference " + esc(s.app.application_code) + ") is back with our recruitment team.</p><p>A recruiter will look at it again and email you about the next step.</p>",
  }) : ({
    subject: "Your application for " + s.app.position.title + " is being reviewed",
    body: "<p>Thank you for applying for <b>" + esc(s.app.position.title) + "</b> (reference " + esc(s.app.application_code) + ").</p><p>Your application has passed our first checks and is now with a recruiter for a closer review. We will email you as soon as there is a decision on the next step.</p>",
  }),
  "interview.completed": (s) => ({
    subject: "Thank you for interviewing for " + s.app.position.title,
    body: "<p>Thank you for taking the time to interview for <b>" + esc(s.app.position.title) + "</b>.</p><p>Your interviewer has shared their feedback and we are now making a decision. We will email you with the outcome as soon as it is ready.</p>",
  }),
  "application.selected": (s) => ({
    subject: "Good news about your application for " + s.app.position.title,
    body: "<p>We are pleased to tell you that you have been <b>selected</b> for the <b>" + esc(s.app.position.title) + "</b> position.</p><p>We are now preparing your written offer. Once it has been approved internally, you will receive it by email with a personal link where you can read the terms, download the offer letter and respond.</p>",
  }),
  "onboarding.finished": (s) => ({
    subject: "Your onboarding at " + s.app.company.name + " is complete",
    body: "<p>All of your onboarding steps for the <b>" + esc(s.app.position.title) + "</b> role are complete. Welcome to the team!</p><p>Your manager and the People team are your contacts from here on.</p>",
  }),
  "application.withdrawn": (s) => ({
    subject: "Your application for " + s.app.position.title + " has been withdrawn",
    body: "<p>This is to confirm that your application for <b>" + esc(s.app.position.title) + "</b> (reference " + esc(s.app.application_code) + ") has been withdrawn, and any interview or offer that was open has been cancelled.</p><p>You are welcome to apply again for this or any future opening.</p>",
  }),
};`;

export default function build() {
  const w = new Workflow('WF-02',
    'WF-02: persists submissions (one transaction: candidate, duplicate check, application) and keeps the candidate informed by email: acknowledgement, rejection notice and a status update at every candidate-facing step.');
  const BC = '$("Build Context").first().json';

  w.add(execTrigger('When Called by Workflow', [['mode'], ['event_id'], ['correlation_id'], ['validation', 'object'], ['action', 'object']]));
  w.add(set('Build Context', [
    ['ctx', x('({ actor_type: "SYSTEM", actor_id: "n8n", workflow_name: "WF-02", workflow_version: "1.1.0", execution_id: $execution.id, correlation_id: $json.correlation_id || $json.action?.correlation_id || null })'), 'object'],
  ]));
  w.nodes.at(-1).parameters.includeOtherFields = true;
  w.add(route('Intake or Action?', '$json.mode === "intake" ? "intake" : ($json.action?.action_type || "")',
    ['intake', 'SEND_REJECTION_NOTICE', 'NOTIFY_CANDIDATE'], 'unknown'));
  w.chain('When Called by Workflow', 'Build Context', 'Intake or Action?');

  // ---- intake (WF-01) ---------------------------------------------------------------------------------
  w.add(pg('Start Execution Log', `SELECT api.start_workflow_execution($1::jsonb, 'SUB_WORKFLOW', 'EVENT', $2) AS run_id`,
    ['JSON.stringify($json.ctx)', '$json.event_id']));
  w.add(pg('Persist Application', 'SELECT * FROM api.submit_application($1::uuid, $2::jsonb, $3::jsonb)',
    [`${BC}.event_id`, `JSON.stringify(${BC}.validation)`, `JSON.stringify(${BC}.ctx)`], { onError: 'continueErrorOutput' }));
  w.add(pg('Load Company', `SELECT (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.name') AS company_name`,
    [], { alwaysOutputData: true }));
  w.add(set('Compose Candidate Message', [
    ['recipient', x(`${BC}.validation?.application?.email || ""`)],
    ['template_key', x('({ ACCEPTED: "application.received", NEEDS_REVIEW: "application.received", DUPLICATE: "application.duplicate", INVALID: "application.invalid" })[$("Persist Application").first().json.outcome] || "application.received"')],
    ['dedupe_key', x(`"candidate.ack:" + ${BC}.event_id`)],
    ['subject', x(`(() => { const p = ${BC}.validation?.application?.position_title || "your application"; const o = $("Persist Application").first().json.outcome; if (o === "DUPLICATE") return "We already have your application for " + p; if (o === "INVALID") return "We could not process your application"; return "We received your application for " + p; })()`)],
    ['html', x(`(() => { ${ESC} ${LAYOUT} const r = $("Persist Application").first().json; const company = $json.company_name || "NovaTech Solutions"; const a = ${BC}.validation?.application || {}; const pos = esc(a.position_title || "the position"); const code = esc(r.application_code || ""); let body; if (r.outcome === "DUPLICATE") { body = "<p>We already have an active application from you for <b>" + pos + "</b> (reference " + code + "). There is no need to apply again; we will keep you updated on that application.</p>"; } else if (r.outcome === "INVALID") { body = "<p>Unfortunately we could not process your application: " + esc(r.reason || "some required information was missing") + ".</p><p>Please submit the form again with the missing details.</p>"; } else { body = "<p>Thank you for applying for <b>" + pos + "</b> at " + esc(company) + ". Your reference number is <b>" + code + "</b>.</p><p>Here is what happens next: we check your application, a recruiter reviews it, and you will receive an email from us at every step, whether that is an interview invitation or another update.</p>"; } return layout("<p>Dear " + esc(a.full_name || "applicant") + ",</p>" + body + "<p>Kind regards,<br/>" + esc(company) + " Talent Team</p>", "Reference " + code); })()`)],
    ['application_id', x('$("Persist Application").first().json.application_id || ""')],
  ]));
  w.add(when('Has Valid Email?', '$json.recipient'));
  w.add(exec('Send Acknowledgement (SWF-02)', 'SWF-02', {
    ctx: x(`${BC}.ctx`), dedupe_key: x('$json.dedupe_key'), channel: 'EMAIL', template_key: x('$json.template_key'),
    recipient: x('$json.recipient'), subject: x('$json.subject'), html: x('$json.html'),
    application_id: x('$json.application_id'), entity_type: 'APPLICATION', entity_id: x('$json.application_id'),
  }));
  w.add(pg('Finish Execution Log', `SELECT api.finish_workflow_execution($1::jsonb, 'SUCCEEDED') AS duration_ms`,
    [`JSON.stringify(${BC}.ctx)`]));
  w.add(set('Return Intake Result', [
    ['outcome', x('$("Persist Application").first().json.outcome')],
    ['status', x('$("Persist Application").first().json.status')],
    ['application_id', x('$("Persist Application").first().json.application_id')],
    ['application_code', x('$("Persist Application").first().json.application_code')],
    ['correlation_id', x('$("Persist Application").first().json.correlation_id')],
    ['replayed', x('$("Persist Application").first().json.replayed'), 'boolean'],
  ]));
  w.add(exec('Record Failure (SWF-03)', 'SWF-03', {
    ctx: x(`${BC}.ctx`),
    error: x(`(() => { const msg = String($json.message ?? $json.error?.message ?? (typeof $json.error === "string" ? $json.error : $json.error?.description) ?? "persist failed"); const m = msg.match(/^([A-Z][A-Z0-9_]+): /); const b = ${BC}; return { workflow_name: "WF-02", node_name: "Persist Application", error_class: m ? "NON_RETRYABLE" : "UNKNOWN", error_code: m ? m[1] : "DB_ERROR", error_message: msg, entity_type: "EVENT", entity_id: b.event_id, replay_workflow: "WF-02", payload: { mode: "intake", event_id: b.event_id, correlation_id: b.correlation_id, validation: b.validation } }; })()`),
  }));
  w.connect('Intake or Action?', 'Start Execution Log', 0);
  w.chain('Start Execution Log', 'Persist Application', 'Load Company', 'Compose Candidate Message', 'Has Valid Email?', 'Send Acknowledgement (SWF-02)', 'Finish Execution Log', 'Return Intake Result');
  w.connect('Persist Application', 'Record Failure (SWF-03)', 1);
  w.connect('Has Valid Email?', 'Finish Execution Log', 1);

  // ---- SEND_REJECTION_NOTICE (WF-00) ----------------------------------------------------------------------
  // stage: SCREENING (never invited), INVITED (invited or booked, not interviewed) or INTERVIEWED.
  w.add(pg('Load Application State', `SELECT api.application_snapshot($1::uuid) AS app,
       CASE WHEN EXISTS (SELECT 1 FROM hiring.interviews i WHERE i.application_id = $1::uuid AND i.status = 'COMPLETED') THEN 'INTERVIEWED'
            WHEN EXISTS (SELECT 1 FROM hiring.interviews i WHERE i.application_id = $1::uuid) THEN 'INVITED'
            ELSE 'SCREENING' END AS stage`, ['$json.action.application_id']));
  w.add(when('Still Rejected With Email?', '$json.app?.status === "REJECTED" && $json.app?.candidate?.email'));
  w.add(email('Send Rejection Notice (SWF-02)', {
    ctx: `${BC}.ctx`, template: 'application.rejected', entityType: 'APPLICATION',
    dedupe: '"candidate.rejection:" + $json.app.application_id',
    recipient: '$json.app.candidate.email',
    subject: '"Your application for " + $json.app.position.title + " at " + $json.app.company.name',
    html: `(() => { ${ESC} ${LAYOUT} const a = $json.app; const role = "<b>" + esc(a.position.title) + "</b> role (reference " + esc(a.application_code) + ")"; const opening = { INTERVIEWED: "<p>Thank you for taking the time to interview with us for the " + role + ". We enjoyed learning about your experience.</p><p>After careful consideration of all the interviews, we have decided not to move forward with your application at this time.", INVITED: "<p>Thank you for your interest in the " + role + ".</p><p>After a further review we have decided not to move forward with your application at this time, so the interview invitation we sent you is no longer valid.", SCREENING: "<p>Thank you for your interest in the " + role + " and for the time you put into your application.</p><p>After careful review we have decided not to move forward with your application at this time." }[$json.stage] || ""; return layout("<p>Dear " + esc(a.candidate.full_name) + ",</p>" + opening + " This was not an easy decision, and we encourage you to apply for future openings that match your experience.</p><p>Kind regards,<br/>" + esc(a.company.name) + " Talent Team</p>", "Reference " + esc(a.application_code)); })()`,
    applicationId: '$json.app.application_id', entityId: '$json.app.application_id',
  }));
  w.add(set('Return Action Result', [
    ['action_outcome', x('$json.status === "FAILED" ? "FAILED" : "DONE"')],
    ['action_reason', x('$json.status === "FAILED" ? "rejection notice could not be sent (will retry)" : ($json.duplicate_suppressed ? "already sent" : "rejection notice sent")')],
  ]));
  w.add(set('Skip: State Changed', [
    ['action_outcome', 'SKIPPED'],
    ['action_reason', x('"application is " + ($json.app?.status || "missing") + " or has no email"')],
  ]));
  w.connect('Intake or Action?', 'Load Application State', 1);
  w.chain('Load Application State', 'Still Rejected With Email?', 'Send Rejection Notice (SWF-02)', 'Return Action Result');
  w.connect('Still Rejected With Email?', 'Skip: State Changed', 1);

  // ---- NOTIFY_CANDIDATE (WF-00): status update ---------------------------------------------------------------
  // Skipped when the application has since reached another step that emails the candidate (candidate_informed),
  // so a slow queue never sends "under review" after the interview invitation.
  w.add(pg('Load Update State', `SELECT api.application_snapshot($1::uuid) AS app,
       NOT EXISTS (SELECT 1 FROM hiring.candidate_status_history h
                     JOIN hiring.application_statuses st ON st.code = h.to_status
                    WHERE h.application_id = $1::uuid AND h.id > $2::bigint AND st.candidate_informed) AS still_relevant,
       $3::text AS template_key, $4::text AS from_status`,
  ['$json.action.application_id', '$json.action.payload?.history_id || 0', '$json.action.payload?.template_key || ""', '$json.action.payload?.from_status || ""']));
  w.add(when('Update Still Relevant?', '$json.still_relevant && $json.app?.candidate?.email && $json.template_key'));
  w.add(email('Send Status Update (SWF-02)', {
    ctx: `${BC}.ctx`, template: 'candidate.status_update', entityType: 'APPLICATION',
    dedupe: '"candidate.update:" + $json.app.application_id + ":" + $("Build Context").first().json.action.payload.history_id',
    recipient: '$json.app.candidate.email',
    subject: `(() => { ${ESC} ${UPDATES} const t = UPDATES[$json.template_key]; return t ? t({ app: $json.app }, $json.from_status).subject : "An update on your application"; })()`,
    html: `(() => { ${ESC} ${LAYOUT} ${UPDATES} const a = $json.app; const t = UPDATES[$json.template_key]; const body = t ? t({ app: a }, $json.from_status).body : "<p>There is an update on your application for <b>" + esc(a.position.title) + "</b>. We will contact you shortly.</p>"; return layout("<p>Dear " + esc(a.candidate.full_name) + ",</p>" + body + "<p>Kind regards,<br/>" + esc(a.company.name) + " Talent Team</p>", "Reference " + esc(a.application_code)); })()`,
    applicationId: '$json.app.application_id', entityId: '$json.app.application_id',
  }));
  // The template key in the log is the specific update, not the generic channel template.
  w.nodes.at(-1).parameters.workflowInputs.value.template_key = x('$json.template_key');
  w.add(set('Return Update Result', [
    ['action_outcome', x('$json.status === "FAILED" ? "FAILED" : "DONE"')],
    ['action_reason', x('$json.status === "FAILED" ? "status update could not be sent (will retry)" : ($json.duplicate_suppressed ? "status update already sent" : "status update sent: " + $("Load Update State").first().json.template_key)')],
  ]));
  w.add(set('Skip: Update Superseded', [
    ['action_outcome', 'SKIPPED'],
    ['action_reason', x('!$json.app ? "application missing" : (!$json.app.candidate?.email ? "candidate has no email" : (!$json.template_key ? "no template in the action" : "a later step already emailed the candidate (now " + $json.app.status + ")"))')],
  ]));
  w.connect('Intake or Action?', 'Load Update State', 2);
  w.chain('Load Update State', 'Update Still Relevant?', 'Send Status Update (SWF-02)', 'Return Update Result');
  w.connect('Update Still Relevant?', 'Skip: Update Superseded', 1);

  // ---- unknown action ----------------------------------------------------------------------------------------
  w.add(set('Result: Unknown Action', [
    ['action_outcome', 'FAILED'],
    ['action_reason', x('"WF-02 has no handler for " + ($json.action?.action_type || $json.mode || "this call")')],
  ]));
  w.connect('Intake or Action?', 'Result: Unknown Action', 3);

  addNotes(w, 'WF-02');
  return w.toJSON();
}
