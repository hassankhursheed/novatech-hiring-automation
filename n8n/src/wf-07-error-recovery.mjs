// WF-07 Error & Recovery: safety net for crashed executions, error-queue alerts, and operator replay.
import {
  Workflow, WF, x, ESC, LAYOUT, sticky, errorTrigger, schedule, webhook, respond, pg, set, exec, email, when, route, http,
} from './lib.mjs';

export default function build() {
  const w = new Workflow('WF-07',
    'WF-07: error workflow of every NovaTech workflow (crash → error queue), 5-minute error digest to operations, and authenticated replay of error-queue entries (POST /webhook/ops/replay).');
  const H = `${ESC} ${LAYOUT}`;

  // ---- 1. Safety net: any workflow execution that crashes ----------------------------------------------------
  w.add(errorTrigger('On Workflow Error'));
  w.add(set('Describe Crash', [
    ['ctx', x('({ actor_type: "SYSTEM", actor_id: "n8n", workflow_name: "WF-07", workflow_version: "1.0.0", execution_id: $execution.id })'), 'object'],
    ['crashed_workflow', x('(($json.workflow?.name || "").match(/^(S?WF-\\d+)/) || [])[1] || $json.workflow?.name || "UNKNOWN"')],
    ['crashed_execution_id', x('String($json.execution?.id ?? $json.trigger?.error?.timestamp ?? "")')],
    ['error', x(`({ workflow_name: (($json.workflow?.name || "").match(/^(S?WF-\\d+)/) || [])[1] || $json.workflow?.name || "UNKNOWN", node_name: $json.execution?.lastNodeExecuted || $json.trigger?.error?.node?.name || "trigger", error_class: "UNKNOWN", error_code: "WORKFLOW_CRASHED", error_message: String($json.execution?.error?.message || $json.trigger?.error?.message || "the workflow stopped with an unhandled error").slice(0, 2000), entity_type: "WORKFLOW_EXECUTION", entity_id: String($json.execution?.id ?? ""), execution_id: String($json.execution?.id ?? ""), payload: { workflow_id: $json.workflow?.id, execution_url: $json.execution?.url, mode: $json.execution?.mode } })`), 'object'],
  ]));
  w.add(pg('Record Crash', 'SELECT api.record_error($1::jsonb, $2::jsonb) AS error_id',
    ['JSON.stringify($json.error)', 'JSON.stringify($json.ctx)']));
  // Close the crashed run in the execution log (it never reached its own finish step).
  w.add(pg('Close Crashed Run', `SELECT api.finish_workflow_execution($1::jsonb, 'FAILED', $2::uuid) AS duration_ms`,
    ['JSON.stringify({ ...$("Describe Crash").first().json.ctx, workflow_name: $("Describe Crash").first().json.crashed_workflow, execution_id: $("Describe Crash").first().json.crashed_execution_id || $execution.id })', '$json.error_id']));
  w.chain('On Workflow Error', 'Describe Crash', 'Record Crash', 'Close Crashed Run');

  // ---- 2. Error digest every 5 minutes (each error is alerted once) ---------------------------------------------
  w.add(schedule('Every 5 Minutes', { field: 'minutes', minutesInterval: 5 }));
  w.add(set('Alert Context', [
    ['ctx', x('({ actor_type: "SYSTEM", actor_id: "n8n", workflow_name: "WF-07", workflow_version: "1.0.0", execution_id: $execution.id })'), 'object'],
  ]));
  w.add(pg('Claim Unalerted Errors', 'SELECT * FROM api.claim_unalerted_errors($1::jsonb, 50)', ['JSON.stringify($json.ctx)']));
  w.add(pg('Start Alert Log', `SELECT api.start_workflow_execution($1::jsonb, 'SCHEDULE', 'ERROR', $2) AS run_id,
       (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'ops.alert_email') AS ops_email,
       (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.portal_url') AS portal_url`,
  ['JSON.stringify($("Alert Context").first().json.ctx)', '$input.all().length + " new error(s)"'], { executeOnce: true }));
  w.add(email('Send Error Digest (SWF-02)', {
    ctx: '$("Alert Context").first().json.ctx', template: 'ops.error_digest', entityType: 'ERROR',
    dedupe: '"ops.error_digest:" + $("Claim Unalerted Errors").first().json.error_id',
    recipient: '$json.ops_email',
    subject: '"[NovaTech automation] " + $("Claim Unalerted Errors").all().length + " error(s) need attention"',
    html: `(() => { ${H} const rows = $("Claim Unalerted Errors").all().map(i => i.json); return layout("<p>These automation errors were recorded and need a decision (fix the cause, then replay or ignore with a note):</p><table cellpadding='6' style='border-collapse:collapse;border:1px solid #d0d5dd;font-size:13px'><tr style='background:#f2f4f7'><th align='left'>Workflow / step</th><th align='left'>Class</th><th align='left'>Code</th><th align='left'>Message</th><th align='left'>Entity</th><th align='left'>Seen</th></tr>" + rows.map(r => "<tr><td>" + esc(r.workflow_name) + " / " + esc(r.node_name || "-") + "</td><td>" + esc(r.error_class) + "</td><td><b>" + esc(r.error_code) + "</b></td><td>" + esc(String(r.error_message || "").slice(0, 300)) + "</td><td>" + esc((r.entity_type || "") + " " + (r.correlation_id || "")) + "</td><td>" + r.occurrence_count + "x</td></tr>").join("") + "</table><p>Replay: <code>POST /v1/staff/errors/{error_id}/replay</code> (or from the operations dashboard). Replays are idempotent.</p>", "Error ids: " + rows.map(r => esc(r.error_id)).join(", ")); })()`,
  }, { executeOnce: true }));
  w.add(pg('Finish Alert Run', `SELECT api.finish_workflow_execution($1::jsonb, $2) AS duration_ms`,
    ['JSON.stringify($("Alert Context").first().json.ctx)', '$json.status === "FAILED" ? "FAILED" : "SUCCEEDED"'], { executeOnce: true }));
  w.chain('Every 5 Minutes', 'Alert Context', 'Claim Unalerted Errors', 'Start Alert Log', 'Send Error Digest (SWF-02)', 'Finish Alert Run');

  // ---- 3. Operator replay: POST /webhook/ops/replay { error_id, requested_by } ------------------------------------
  w.add(webhook('Replay Requested', 'ops/replay'));
  w.add(set('Replay Request', [
    ['ctx', x('({ actor_type: $json.body?.requested_by ? "STAFF" : "SYSTEM", actor_id: $json.body?.requested_by || "n8n-ops", workflow_name: "WF-07", workflow_version: "1.0.0", execution_id: $execution.id })'), 'object'],
    ['error_id', x('String($json.body?.error_id || "")')],
  ]));
  w.add(when('Valid Error Id?', '/^[0-9a-f-]{36}$/i.test($json.error_id)'));
  w.add(respond('Respond 400', 400, '({ code: "ERROR_ID_REQUIRED", detail: "body must contain error_id (uuid)" })'));
  w.add(pg('Begin Replay', 'SELECT * FROM api.begin_error_replay($1::uuid, $2::jsonb)',
    ['$json.error_id', 'JSON.stringify($json.ctx)'], { onError: 'continueErrorOutput' }));
  w.add(respond('Respond 409', 409, '(() => { const m = String($json.error?.message ?? $json.error ?? "replay refused"); const c = m.match(/^([A-Z][A-Z0-9_]+): (.*)$/); return { code: c ? c[1] : "REPLAY_REFUSED", detail: c ? c[2] : m }; })()'));
  w.add(pg('Start Replay Log', `SELECT api.start_workflow_execution($1::jsonb, 'REPLAY', 'ERROR', $2) AS run_id`,
    ['JSON.stringify({ ...$("Replay Request").first().json.ctx, correlation_id: $json.correlation_id })', '$json.error_id']));
  w.add(route('Route by Replay Target', '$("Begin Replay").first().json.replay_workflow',
    ['WF-00', 'WF-01', 'WF-02', 'WF-03', 'WF-04', 'WF-05', 'WF-06', 'WF-08'], 'no replay handler'));
  w.chain('Replay Requested', 'Replay Request', 'Valid Error Id?', 'Begin Replay', 'Start Replay Log', 'Route by Replay Target');
  w.connect('Valid Error Id?', 'Respond 400', 1);
  w.connect('Begin Replay', 'Respond 409', 1);
  const P = '$("Begin Replay").first().json.payload';
  const RCTX = 'JSON.stringify({ ...$("Replay Request").first().json.ctx, correlation_id: $("Begin Replay").first().json.correlation_id })';

  // WF-00: a scheduled action that exhausted its attempts is put back in the queue.
  w.add(pg('Requeue Scheduled Action', 'SELECT api.requeue_scheduled_action(($1::jsonb ->> \'scheduled_action_id\')::uuid, $2::jsonb) AS status',
    [`JSON.stringify(${P})`, RCTX], { onError: 'continueErrorOutput' }));
  w.connect('Route by Replay Target', 'Requeue Scheduled Action', 0);
  w.add(set('Outcome: Requeued', [['replay_ok', 'true', 'boolean'], ['detail', 'scheduled action requeued; the dispatcher runs it within a minute']]));
  w.connect('Requeue Scheduled Action', 'Outcome: Requeued', 0);

  // WF-01: re-submit through the intake webhook with the original idempotency key (failed events are reprocessed,
  // completed ones are returned as already processed).
  w.add(http('Resubmit to Intake', {
    url: 'http://127.0.0.1:5678/webhook/applications',
    headers: [['Idempotency-Key', x(`${P}.idempotency_key`)], ['X-Correlation-ID', x('$("Begin Replay").first().json.correlation_id')]],
    body: x(`JSON.stringify(${P}.body)`),
  }));
  w.connect('Route by Replay Target', 'Resubmit to Intake', 1);
  w.add(set('Outcome: Intake', [
    ['replay_ok', x('[200, 202].includes($json.statusCode)'), 'boolean'],
    ['detail', x('"intake answered HTTP " + ($json.statusCode ?? "error") + ": " + JSON.stringify($json.body ?? $json.error ?? {}).slice(0, 300)')],
  ]));
  w.connect('Resubmit to Intake', 'Outcome: Intake');

  // WF-02: persist the stored validation result again (api.submit_application is idempotent per event).
  w.add(exec('Replay Candidate Processing', 'WF-02', {
    mode: x(`${P}.mode || "intake"`), event_id: x(`${P}.event_id || ""`), correlation_id: x(`${P}.correlation_id || $("Begin Replay").first().json.correlation_id`),
    validation: x(`${P}.validation || {}`), action: x(`${P}.action || {}`),
  }, { onError: 'continueRegularOutput' }));
  w.connect('Route by Replay Target', 'Replay Candidate Processing', 2);
  w.add(set('Outcome: Candidate Processing', [
    ['replay_ok', x('!$json.error && !$json.error_id && $json.action_outcome !== "FAILED"'), 'boolean'],
    ['detail', x('$json.error ? "replay crashed: " + ($json.error.message || $json.error) : $json.error_id ? "failed again (error " + $json.error_id + ")" : "re-processed: " + ($json.outcome || $json.action_outcome || "ok") + " " + ($json.application_code || "")')],
  ]));
  w.connect('Replay Candidate Processing', 'Outcome: Candidate Processing');

  // WF-03/04/05/06/08: re-run the original dispatcher action (handlers re-check state and are idempotent).
  const ids = Object.fromEntries(['WF-03', 'WF-04', 'WF-05', 'WF-06', 'WF-08'].map((k) => [k, WF[k][0]]));
  w.add(exec('Replay Action Handler', null, { action: x(`${P}.action`) }, {
    onError: 'continueRegularOutput', dynamicId: x(`(${JSON.stringify(ids)})[$("Begin Replay").first().json.replay_workflow]`),
  }));
  for (const output of [3, 4, 5, 6, 7]) w.connect('Route by Replay Target', 'Replay Action Handler', output);
  w.add(set('Outcome: Action Handler', [
    ['replay_ok', x('!$json.error && !$json.error_id && ["DONE", "SKIPPED"].includes($json.action_outcome)'), 'boolean'],
    ['detail', x('$json.error ? "replay crashed: " + ($json.error.message || $json.error) : ($json.action_outcome || "no result") + ": " + ($json.action_reason || "")')],
  ]));
  w.connect('Replay Action Handler', 'Outcome: Action Handler');

  w.add(set('Outcome: Not Replayable', [['replay_ok', 'false', 'boolean'], ['detail', x('"no replay handler for " + $("Begin Replay").first().json.replay_workflow')]]));
  w.connect('Route by Replay Target', 'Outcome: Not Replayable', 8);
  w.add(set('Outcome: Requeue Refused', [['replay_ok', 'false', 'boolean'], ['detail', x('String($json.error?.message ?? $json.error ?? "requeue refused")')]]));
  w.connect('Requeue Scheduled Action', 'Outcome: Requeue Refused', 1);

  w.add(pg('Resolve Error', `SELECT api.resolve_error($1::uuid, $2, $3, $4::jsonb) AS status,
       api.finish_workflow_execution($4::jsonb, CASE WHEN $2 = 'RESOLVED' THEN 'SUCCEEDED' ELSE 'FAILED' END) AS duration_ms`,
  ['$("Begin Replay").first().json.error_id', '$json.replay_ok ? "RESOLVED" : "OPEN"', '"replay #" + $("Begin Replay").first().json.replay_count + ": " + $json.detail', RCTX],
  { onError: 'continueErrorOutput' }));
  for (const n of ['Outcome: Requeued', 'Outcome: Intake', 'Outcome: Candidate Processing', 'Outcome: Action Handler', 'Outcome: Not Replayable', 'Outcome: Requeue Refused']) {
    w.connect(n, 'Resolve Error');
  }
  const OUTCOMES = ['Outcome: Requeued', 'Outcome: Intake', 'Outcome: Candidate Processing', 'Outcome: Action Handler', 'Outcome: Not Replayable', 'Outcome: Requeue Refused'];
  w.add(respond('Respond Replay Result', 200, `({ error_id: $("Begin Replay").first().json.error_id, status: $json.status, replay_count: $("Begin Replay").first().json.replay_count, detail: ${JSON.stringify(OUTCOMES)}.map(n => $(n).isExecuted ? $(n).first().json.detail : null).find(Boolean) })`));
  w.connect('Resolve Error', 'Respond Replay Result', 0);
  w.add(respond('Respond Resolve Failed', 500, '({ code: "RESOLVE_FAILED", detail: String($json.error?.message ?? $json.error ?? "could not record the replay result") })'));
  w.connect('Resolve Error', 'Respond Resolve Failed', 1);

  w.add(sticky('Overview', '## WF-07 Error & Recovery\n1. **Error Trigger**: the error workflow of every NovaTech workflow. A crash is recorded in the error queue (`api.record_error`, deduplicated by fingerprint) and its run is closed as FAILED.\n2. **Every 5 minutes**: `api.claim_unalerted_errors` → one digest email to `ops.alert_email` (each error alerted once).\n3. **POST /webhook/ops/replay** (header auth, called by the backend for staff): `api.begin_error_replay` → re-run the stored payload in the original workflow → `api.resolve_error` RESOLVED or OPEN. Replays reuse the original keys and ids, so they cannot create duplicates.', { width: 620, height: 300 }));
  return w.toJSON({ errorWorkflow: false });
}
