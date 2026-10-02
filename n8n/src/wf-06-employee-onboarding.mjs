// WF-06 Employee Onboarding: employee record exactly once, simulated account, welcome + orientation, HR/manager
// notice, completion notice, and an hourly sweep that reminds owners of overdue onboarding tasks.
import {
  Workflow, x, ESC, LAYOUT, FMT, sticky, execTrigger, schedule, pg, set, email, when, route, splitOut,
  handlerContext, addHandlerTail,
} from './lib.mjs';

export default function build() {
  const w = new Workflow('WF-06',
    'WF-06: creates the employee exactly once from the accepted offer (NT-YYYY-NNN), simulated account provisioning, welcome + orientation, HR/manager notices, completion notice and hourly overdue-task reminders.');
  const CTX = '$("Build Context").first().json.ctx';
  const S = '$("Start Log & Load State").first().json';
  const H = `${ESC} ${LAYOUT} ${FMT}`;
  const GCAL = `const gcal = (title, start, end, details) => "https://calendar.google.com/calendar/render?action=TEMPLATE&text=" + encodeURIComponent(title) + "&dates=" + start.toUTC().toFormat("yyyyLLdd'T'HHmmss'Z'") + "/" + end.toUTC().toFormat("yyyyLLdd'T'HHmmss'Z'") + "&details=" + encodeURIComponent(details);`;

  w.add(execTrigger('When Called by Dispatcher', [['action', 'object']]));
  w.add(handlerContext('WF-06'));
  w.add(pg('Start Log & Load State', `SELECT api.start_workflow_execution($1::jsonb, 'SUB_WORKFLOW', 'APPLICATION', $2) AS run_id,
       api.application_snapshot($2::uuid) AS app,
       (SELECT o.id FROM hiring.offers o
         WHERE o.application_id = $2::uuid AND o.status = 'ACCEPTED' ORDER BY o.revision DESC LIMIT 1) AS accepted_offer_id,
       (SELECT api.employee_snapshot(e.id) FROM hiring.employees e WHERE e.application_id = $2::uuid) AS employee,
       (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.timezone') AS timezone,
       (SELECT string_agg(s.email, ',' ORDER BY s.email) FROM hiring.staff_members s
         WHERE s.is_active AND 'HR_ADMIN' = ANY (s.roles)) AS hr_emails`,
  ['JSON.stringify($json.ctx)', '$json.application_id || $json.entity_id']));
  w.add(route('Route by Action Type', '$("Build Context").first().json.action.action_type', ['START_ONBOARDING', 'NOTIFY_ONBOARDING_COMPLETE']));
  w.chain('When Called by Dispatcher', 'Build Context', 'Start Log & Load State', 'Route by Action Type');

  const EMAILS = ['Welcome Employee (SWF-02)', 'Notify HR & Manager (SWF-02)', 'Announce Completion (SWF-02)'];
  const tail = addHandlerTail(w, 'WF-06', EMAILS);
  const dbFail = (node) => w.connect(node, tail.failed, 1);

  // ---- 0: START_ONBOARDING (application entered ACCEPTED) -------------------------------------------------
  w.add(when('Ready to Onboard?', `["ACCEPTED", "ONBOARDING"].includes(${S}.app?.status) && ${S}.accepted_offer_id`));
  w.connect('Route by Action Type', 'Ready to Onboard?', 0);
  w.connect('Ready to Onboard?', tail.skip, 1);
  w.add(pg('Create Employee', 'SELECT * FROM api.create_employee_from_offer($1::uuid, $2::jsonb)',
    [`${S}.accepted_offer_id`, `JSON.stringify(${CTX})`], { onError: 'continueErrorOutput' }));
  w.connect('Ready to Onboard?', 'Create Employee');
  dbFail('Create Employee');
  // Simulated identity provider: replace with Google Workspace / Entra ID provisioning when available.
  w.add(pg('Provision Account (Simulated)',
    'SELECT api.mark_account_provisioned($1::uuid, $2::jsonb) AS provisioned_at, api.employee_snapshot($1::uuid) AS employee',
    ['$json.employee_id', `JSON.stringify(${CTX})`], { onError: 'continueErrorOutput' }));
  w.connect('Create Employee', 'Provision Account (Simulated)');
  dbFail('Provision Account (Simulated)');
  const E = '$("Provision Account (Simulated)").first().json.employee';
  w.add(email('Welcome Employee (SWF-02)', {
    ctx: CTX, template: 'onboarding.welcome', entityType: 'EMPLOYEE',
    dedupe: `"onboarding.welcome:" + ${E}.employee_id`,
    recipient: `${E}.personal_email || ${S}.app.candidate.email`,
    subject: `"Welcome to " + ${S}.app.company.name + ", " + ${E}.full_name.split(" ")[0] + "!"`,
    html: `(() => { ${H} ${GCAL} const s = ${S}; const e = ${E}; const tz = s.timezone; const start = DateTime.fromISO(e.joining_date, { zone: tz }).set({ hour: 9, minute: 30 }); const yours = (e.tasks || []).filter(t => t.owner_role === "EMPLOYEE"); return layout("<p>Dear " + esc(e.full_name) + ",</p><p>Welcome to <b>" + esc(s.app.company.name) + "</b>! We are excited to have you join us as <b>" + esc(e.position) + "</b> in " + esc(e.department) + ".</p><table cellpadding='4'><tr><td style='color:#667085'>Employee ID</td><td><b>" + esc(e.employee_code) + "</b></td></tr><tr><td style='color:#667085'>Company email</td><td>" + esc(e.company_email) + " (login details follow from IT on your first day)</td></tr><tr><td style='color:#667085'>First day</td><td><b>" + esc(fmtDate(e.joining_date)) + "</b></td></tr><tr><td style='color:#667085'>Manager</td><td>" + esc(e.manager?.full_name || "-") + "</td></tr></table><p><b>Orientation:</b> " + esc(start.toFormat("cccc d LLLL yyyy, h:mm a")) + " with HR. <a href='" + esc(gcal("Orientation at " + s.app.company.name, start, start.plus({ hours: 2 }), "Welcome session for " + e.full_name + " (" + e.employee_code + ")")) + "'>Add to calendar</a></p>" + (yours.length ? "<p>Before or on your first day, please:</p><ul>" + yours.map(t => "<li>" + esc(t.title) + " (by " + esc(fmtDate(t.due_date)) + ")</li>").join("") + "</ul>" : "") + "<p>See you soon!<br/>" + esc(s.app.company.name) + " Human Resources</p>", "Employee " + esc(e.employee_code)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${E}.employee_id`,
  }));
  w.connect('Provision Account (Simulated)', 'Welcome Employee (SWF-02)');
  w.add(email('Notify HR & Manager (SWF-02)', {
    ctx: CTX, template: 'onboarding.started', entityType: 'EMPLOYEE',
    dedupe: `"onboarding.started:" + ${E}.employee_id`,
    recipient: `[${S}.hr_emails, ${E}.manager?.email, ${E}.it_email].filter(Boolean).join(",")`,
    subject: `"Onboarding started: " + ${E}.full_name + " (" + ${E}.employee_code + "), joining " + DateTime.fromISO(${E}.joining_date).toFormat("d LLL")`,
    html: `(() => { ${H} const s = ${S}; const e = ${E}; return layout("<p><b>" + esc(e.full_name) + "</b> accepted the offer and joins as <b>" + esc(e.position) + "</b> on <b>" + esc(fmtDate(e.joining_date)) + "</b>. Employee record " + esc(e.employee_code) + " was created and the company account " + esc(e.company_email) + " was provisioned (simulated).</p><table cellpadding='6' style='border-collapse:collapse;border:1px solid #d0d5dd'><tr style='background:#f2f4f7'><th align='left'>Task</th><th align='left'>Owner</th><th align='left'>Due</th><th align='left'>Status</th></tr>" + (e.tasks || []).map(t => "<tr><td>" + esc(t.title) + "</td><td>" + esc(t.owner_role) + "</td><td>" + esc(fmtDate(t.due_date)) + "</td><td>" + esc(t.status) + "</td></tr>").join("") + "</table><p>Owners get a reminder when a task becomes overdue. Mark tasks done as you complete them; onboarding closes automatically when all are done.</p>", "Correlation id " + esc(e.correlation_id)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${E}.employee_id`,
  }));
  w.chain('Welcome Employee (SWF-02)', 'Notify HR & Manager (SWF-02)', tail.notified);

  // ---- 1: NOTIFY_ONBOARDING_COMPLETE (application entered ONBOARDED) ------------------------------------------
  w.add(when('Onboarded?', `${S}.app?.status === "ONBOARDED" && ${S}.employee`));
  w.connect('Route by Action Type', 'Onboarded?', 1);
  w.connect('Onboarded?', tail.skip, 1);
  w.add(email('Announce Completion (SWF-02)', {
    ctx: CTX, template: 'onboarding.complete', entityType: 'EMPLOYEE',
    dedupe: `"onboarding.complete:" + ${S}.employee.employee_id`,
    recipient: `[${S}.hr_emails, ${S}.employee.manager?.email].filter(Boolean).join(",")`,
    subject: `"Onboarding complete: " + ${S}.employee.full_name + " (" + ${S}.employee.employee_code + ")"`,
    html: `(() => { ${H} const e = ${S}.employee; return layout("<p>All onboarding tasks for <b>" + esc(e.full_name) + "</b> (" + esc(e.employee_code) + ", " + esc(e.position) + ") are complete. The hiring journey for this person is closed.</p>", "Correlation id " + esc(e.correlation_id)); })()`,
    applicationId: `${S}.app.application_id`, entityId: `${S}.employee.employee_id`,
  }));
  w.connect('Onboarded?', 'Announce Completion (SWF-02)');
  w.connect('Announce Completion (SWF-02)', tail.notified);

  w.add(set('Result: Unknown Action', [
    ['action_outcome', 'FAILED'],
    ['action_reason', x('"WF-06 has no handler for " + $("Build Context").first().json.action.action_type')],
  ]));
  w.connect('Route by Action Type', 'Result: Unknown Action', 2);
  w.connect('Result: Unknown Action', tail.finish);

  // ---- hourly sweep: overdue onboarding tasks ------------------------------------------------------------------
  w.add(schedule('Every Hour', { field: 'hours', hoursInterval: 1, triggerAtMinute: 15 }));
  w.add(set('Sweep Context', [
    ['ctx', x('({ actor_type: "SYSTEM", actor_id: "n8n", workflow_name: "WF-06", workflow_version: "1.0.0", execution_id: $execution.id })'), 'object'],
  ]));
  // Claims atomically: each task is reminded at most once per onboarding.overdue_reminder_every.
  w.add(pg('Claim Overdue Tasks', 'SELECT * FROM api.claim_overdue_onboarding_tasks($1::jsonb, 50)', ['JSON.stringify($json.ctx)']));
  w.add(pg('Start Sweep Log', `SELECT api.start_workflow_execution($1::jsonb, 'SCHEDULE', 'ONBOARDING_TASK', $2) AS run_id,
       (SELECT string_agg(s.email, ',' ORDER BY s.email) FROM hiring.staff_members s
         WHERE s.is_active AND 'HR_ADMIN' = ANY (s.roles)) AS hr_emails`,
  ['JSON.stringify($("Sweep Context").first().json.ctx)', '$input.all().length + " overdue task(s)"'], { executeOnce: true }));
  w.add(set('Overdue Tasks', [['tasks', x('$("Claim Overdue Tasks").all().map(i => i.json)'), 'array']], { executeOnce: true }));
  w.add(splitOut('One Item per Task', 'tasks'));
  w.add(email('Remind Task Owner (SWF-02)', {
    ctx: '({ ...$("Sweep Context").first().json.ctx, correlation_id: $json.correlation_id })',
    template: 'onboarding.task_overdue', entityType: 'ONBOARDING_TASK', mode: 'each',
    dedupe: '"onboarding.overdue:" + $json.task_id + ":" + $json.reminder_count',
    recipient: '$json.assignee_email || $("Start Sweep Log").first().json.hr_emails',
    subject: '"[Overdue] " + $json.task_title + " for " + $json.employee_name + " (" + $json.employee_code + ")"',
    html: `(() => { ${H} const t = $json; return layout("<p>Hi " + esc(t.assignee_name || "team") + ",</p><p>The onboarding task <b>" + esc(t.task_title) + "</b> for " + esc(t.employee_name) + " (" + esc(t.employee_code) + ") was due on " + esc(fmtDate(t.due_date)) + " and is <b>" + t.days_overdue + " day(s) overdue</b>.</p><p>Please complete it and mark it done so the new employee's onboarding can close.</p>", "Reminder " + t.reminder_count + " &middot; owner " + esc(t.owner_role)); })()`,
    applicationId: '$json.application_id', entityId: '$json.task_id',
  }));
  w.add(pg('Finish Sweep', `SELECT api.finish_workflow_execution($1::jsonb, $2) AS duration_ms, $3::int AS reminders`,
    ['JSON.stringify($("Sweep Context").first().json.ctx)', '$input.all().some(i => i.json.status === "FAILED") ? "FAILED" : "SUCCEEDED"', '$input.all().length'],
    { executeOnce: true }));
  w.chain('Every Hour', 'Sweep Context', 'Claim Overdue Tasks', 'Start Sweep Log', 'Overdue Tasks', 'One Item per Task',
    'Remind Task Owner (SWF-02)', 'Finish Sweep');

  w.add(sticky('Overview', '## WF-06 Employee Onboarding\n- **START_ONBOARDING** (offer accepted): `api.create_employee_from_offer` creates the employee **exactly once per offer** (unique offer_id), generates `NT-YYYY-NNN` and the company email, and creates the tasks from the templates. Account provisioning is simulated (`api.mark_account_provisioned`). Then the welcome email (with orientation) and the HR/manager/IT notice.\n- **Hourly sweep**: `api.claim_overdue_onboarding_tasks` claims overdue tasks not reminded within `onboarding.overdue_reminder_every`, and each owner is reminded once per claim.\n- **NOTIFY_ONBOARDING_COMPLETE** when the last task is done.', { width: 620, height: 300 }));
  return w.toJSON();
}
