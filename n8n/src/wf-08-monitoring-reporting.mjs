// WF-08 Monitoring & Reporting: daily management report (numbers from SQL only) and review-queue alerts.
import {
  Workflow, x, ESC, LAYOUT, addNotes, execTrigger, schedule, webhook, pg, set, exec, email, iff, cond, when, route,
} from './lib.mjs';

export default function build() {
  const w = new Workflow('WF-08',
    'WF-08: daily management report at 09:00 (all figures from reporting.daily_metrics, AI prose behind a numeric guardrail, one report per date, emailed once) and review-queue alerts for recruiters / hiring managers.');
  const H = `${ESC} ${LAYOUT}`;

  // ---- daily report ------------------------------------------------------------------------------------
  w.add(schedule('Daily 09:00', { field: 'days', daysInterval: 1, triggerAtHour: 9, triggerAtMinute: 0 }));
  w.add(webhook('Run Report On Demand', 'ops/daily-report', { responseMode: 'lastNode' }));
  w.nodes.at(-1).parameters.responseData = 'firstEntryJson';
  w.add(execTrigger('When Called by Workflow', [['action', 'object']]));
  w.add(route('Route by Action Type', '$json.action?.action_type', ['NOTIFY_REVIEW_QUEUE', 'DAILY_REPORT']));

  const RC = '$("Report Context").first().json';
  const LM = '$("Load Metrics").first().json';
  w.add(set('Report Context', [
    ['ctx', x('({ actor_type: "SYSTEM", actor_id: "n8n", workflow_name: "WF-08", workflow_version: "1.0.0", execution_id: $execution.id })'), 'object'],
    ['report_date', x('$json.body?.report_date ?? $json.action?.payload?.report_date ?? ""')],
    ['force', x('$json.body?.force === true'), 'boolean'],
    ['trigger_type', x('$json.body !== undefined ? "WEBHOOK" : ($json.action ? "REPLAY" : "SCHEDULE")')],
  ]));
  w.add(pg('Load Metrics', `WITH d AS (
  SELECT coalesce(NULLIF($2, '')::date,
                  (now() AT TIME ZONE (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.timezone'))::date - 1) AS report_date)
SELECT d.report_date::text AS report_date,
       api.start_workflow_execution($1::jsonb, $3, 'REPORT', d.report_date::text) AS run_id,
       reporting.daily_metrics(d.report_date) AS metrics,
       r.delivered_at,
       coalesce((SELECT string_agg(s.email, ',' ORDER BY s.email) FROM hiring.staff_members s
                  WHERE s.is_active AND s.roles && ARRAY['HR_ADMIN', 'APPROVER_L2']),
                (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'ops.alert_email')) AS recipients,
       (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.name') AS company
  FROM d LEFT JOIN ops.daily_reports r ON r.report_date = d.report_date`,
  ['JSON.stringify($json.ctx)', '$json.report_date', '$json.trigger_type']));
  w.add(iff('Already Delivered?', [cond('$json.delivered_at ? "yes" : ""', 'notEmpty'), cond(`!${RC}.force`, 'true', '', 'boolean')]));
  w.add(pg('Finish: Already Delivered',
    `SELECT api.finish_workflow_execution($1::jsonb, 'SKIPPED') AS duration_ms, 'SKIPPED' AS action_outcome, $2 AS action_reason, $3 AS report_date`,
    [`JSON.stringify(${RC}.ctx)`, '"report for " + $json.report_date + " was already delivered"', '$json.report_date']));
  w.add(exec('Summarize (SWF-01)', 'SWF-01', {
    ctx: x(`${RC}.ctx`), method: 'POST', path: '/v1/reports/daily-summary',
    body: x(`({ report_date: ${LM}.report_date, metrics: ${LM}.metrics })`), fault_inject: '', entity_type: 'REPORT', entity_id: x(`${LM}.report_date`),
  }));
  const FALLBACK = '"The summary service was unavailable; the figures below come directly from the database."';
  w.add(set('Compose Report', [
    ['summary', x(`$json.ok ? $json.body.summary : ${FALLBACK}`)],
    ['summary_source', x('$json.ok ? $json.body.summary_source : "TEMPLATE"')],
    ['subject', x(`"Daily hiring report " + ${LM}.report_date + " - " + (${LM}.company || "NovaTech")`)],
    ['html', x(`(() => { ${H} const m = ${LM}.metrics || {}; const label = k => k.replace(/_/g, " ").replace(/^./, c => c.toUpperCase()); const sections = $json.ok ? $json.body.sections : [{ title: "Metrics", rows: Object.keys(m).filter(k => typeof m[k] === "number").sort().map(k => ({ label: label(k), value: m[k] })) }]; const table = sections.map(s => "<h3 style='margin:18px 0 6px'>" + esc(s.title) + "</h3><table cellpadding='6' style='border-collapse:collapse;border:1px solid #d0d5dd'>" + s.rows.map(r => "<tr><td style='border:1px solid #d0d5dd'>" + esc(r.label) + "</td><td style='border:1px solid #d0d5dd;text-align:right'><b>" + esc(r.value ?? "-") + "</b></td></tr>").join("") + "</table>").join(""); return layout("<h2 style='margin:0 0 8px'>Daily hiring report, " + esc(${LM}.report_date) + "</h2><p>" + esc($json.ok ? $json.body.summary : ${FALLBACK}) + "</p>" + table, "All figures are computed by the database (reporting.daily_metrics). Summary: " + esc($json.ok ? $json.body.summary_source : "TEMPLATE") + "."); })()`)],
  ]));
  w.add(pg('Save Report', 'SELECT * FROM api.save_daily_report($1::date, $2::jsonb, $3, $4, $5::jsonb)',
    [`${LM}.report_date`, `JSON.stringify(${LM}.metrics)`, '$json.summary', '$json.summary_source', `JSON.stringify(${RC}.ctx)`]));
  w.add(email('Send Report (SWF-02)', {
    ctx: `${RC}.ctx`, template: 'report.daily', entityType: 'REPORT',
    dedupe: `"report.daily:" + ${LM}.report_date + (${RC}.force ? ":" + $execution.id : "")`,
    recipient: `${LM}.recipients`,
    subject: '$("Compose Report").first().json.subject',
    html: '$("Compose Report").first().json.html',
    entityId: `${LM}.report_date`,
  }));
  w.add(when('Report Sent?', '$json.status !== "FAILED"'));
  w.add(pg('Mark Delivered',
    `SELECT api.mark_daily_report_delivered($1::date, $2::jsonb) AS delivered_at, api.finish_workflow_execution($2::jsonb, 'SUCCEEDED') AS duration_ms,
       'DONE' AS action_outcome, $3 AS action_reason, $1 AS report_date, $4 AS summary_source`,
    [`${LM}.report_date`, `JSON.stringify(${RC}.ctx)`, '$json.duplicate_suppressed ? "daily report was already sent" : "daily report delivered"', '$("Compose Report").first().json.summary_source']));
  w.add(exec('Record Failure (SWF-03)', 'SWF-03', {
    ctx: x(`${RC}.ctx`),
    error: x(`({ workflow_name: "WF-08", node_name: "Send Report (SWF-02)", error_class: "RETRYABLE", error_code: "REPORT_NOT_DELIVERED", error_message: "the daily report email could not be sent", entity_type: "REPORT", entity_id: ${LM}.report_date, replay_workflow: "WF-08", payload: { action: { action_type: "DAILY_REPORT", payload: { report_date: ${LM}.report_date } } } })`),
  }));
  w.add(set('Return: Report Failed', [
    ['action_outcome', 'FAILED'],
    ['action_reason', x('"report email failed; queued as error " + $json.error_id')],
    ['error_id', x('$json.error_id')],
  ]));
  w.chain('Daily 09:00', 'Report Context');
  w.connect('Run Report On Demand', 'Report Context');
  w.chain('When Called by Workflow', 'Route by Action Type');
  w.connect('Route by Action Type', 'Report Context', 1);
  w.chain('Report Context', 'Load Metrics', 'Already Delivered?', 'Finish: Already Delivered');
  w.connect('Already Delivered?', 'Summarize (SWF-01)', 1);
  w.chain('Summarize (SWF-01)', 'Compose Report', 'Save Report', 'Send Report (SWF-02)', 'Report Sent?', 'Mark Delivered');
  w.connect('Report Sent?', 'Record Failure (SWF-03)', 1);
  w.connect('Record Failure (SWF-03)', 'Return: Report Failed');

  // ---- review queue alert (WF-00 action NOTIFY_REVIEW_QUEUE) ---------------------------------------------
  const RV = '$("Review Context").first().json';
  w.add(set('Review Context', [
    ['ctx', x('({ actor_type: "SYSTEM", actor_id: "n8n", workflow_name: "WF-08", workflow_version: "1.0.0", execution_id: $execution.id, correlation_id: $json.action.correlation_id })'), 'object'],
    ['action', x('$json.action'), 'object'],
    ['application_id', x('$json.action.application_id || $json.action.entity_id')],
  ]));
  w.add(pg('Load Review Case', `SELECT api.start_workflow_execution($1::jsonb, 'SUB_WORKFLOW', 'APPLICATION', $2) AS run_id,
       api.application_snapshot($2::uuid) AS app,
       (SELECT string_agg(s.email, ',' ORDER BY s.email) FROM hiring.staff_members s
         WHERE s.is_active AND s.roles && ARRAY['RECRUITER', 'HR_ADMIN']) AS recruiters,
       (SELECT s.email FROM hiring.applications a
          JOIN hiring.job_positions p ON p.id = a.job_position_id
          JOIN hiring.staff_members s ON s.id = p.hiring_manager_id
         WHERE a.id = $2::uuid AND s.is_active) AS hiring_manager_email,
       (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.portal_url') AS portal_url`,
  ['JSON.stringify($json.ctx)', '$json.application_id']));
  w.add(when('Still Awaiting Review?', '["SCREENING_REVIEW", "INTERVIEW_REVIEW"].includes($json.app?.status)'));
  w.add(email('Notify Reviewers (SWF-02)', {
    ctx: `${RV}.ctx`, template: 'staff.review_needed', entityType: 'APPLICATION',
    dedupe: `"review.queue:" + ${RV}.action.id`,
    recipient: '$json.app.status === "INTERVIEW_REVIEW" ? ($json.hiring_manager_email || $json.recruiters) : $json.recruiters',
    subject: '"[Action needed] " + $json.app.application_code + " " + ($json.app.status === "INTERVIEW_REVIEW" ? "needs a hiring decision" : "needs a screening review")',
    html: `(() => { ${H} const a = $json.app; const n = v => v === null || v === undefined ? "-" : esc(v); const link = String($json.portal_url || "").replace(/\\/$/, "") + "/staff/applications/" + a.application_id; return layout("<p>An application needs a human decision.</p><table cellpadding='5' style='border-collapse:collapse'>" + [["Application", a.application_code], ["Candidate", a.candidate.full_name], ["Position", a.position.title], ["Status", a.status], ["Reason", a.review_reason], ["Screening score", a.application_score], ["AI recommendation (advisory)", a.ai_recommendation], ["Interview score", a.interview_score], ["Final score", a.final_score]].map(r => "<tr><td style='color:#667085'>" + r[0] + "</td><td><b>" + n(r[1]) + "</b></td></tr>").join("") + "</table><p><a href='" + esc(link) + "'>Open the application</a> to decide. Decisions are recorded with your name and reason.</p>", "Correlation id " + esc(a.correlation_id)); })()`,
    applicationId: '$json.app.application_id', entityId: '$json.app.application_id',
  }));
  w.add(pg('Finish: Reviewers Notified',
    `SELECT api.finish_workflow_execution($1::jsonb, $2) AS duration_ms, $3 AS action_outcome, $4 AS action_reason`,
    [`JSON.stringify(${RV}.ctx)`, '$json.status === "FAILED" ? "FAILED" : "SUCCEEDED"', '$json.status === "FAILED" ? "FAILED" : "DONE"',
      '$json.status === "FAILED" ? "review alert email failed (will retry)" : ($json.duplicate_suppressed ? "review alert already sent" : "review alert sent")']));
  w.add(pg('Finish: No Longer Needed',
    `SELECT api.finish_workflow_execution($1::jsonb, 'SKIPPED') AS duration_ms, 'SKIPPED' AS action_outcome, $2 AS action_reason`,
    [`JSON.stringify(${RV}.ctx)`, '"application is " + ($json.app?.status || "missing") + "; no review needed"']));
  w.connect('Route by Action Type', 'Review Context', 0);
  w.chain('Review Context', 'Load Review Case', 'Still Awaiting Review?', 'Notify Reviewers (SWF-02)', 'Finish: Reviewers Notified');
  w.connect('Still Awaiting Review?', 'Finish: No Longer Needed', 1);

  w.add(set('Unknown Action', [
    ['action_outcome', 'FAILED'],
    ['action_reason', x('"WF-08 has no handler for " + ($json.action?.action_type || "unknown")')],
  ]));
  w.connect('Route by Action Type', 'Unknown Action', 2);

  addNotes(w, 'WF-08');
  return w.toJSON({ timezone: 'Asia/Karachi' });
}
