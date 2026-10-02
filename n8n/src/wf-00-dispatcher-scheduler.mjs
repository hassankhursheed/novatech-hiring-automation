// WF-00 Dispatcher & Scheduler: claims due rows of ops.scheduled_actions (outbox + durable timers) and runs the
// owning workflow for each one.
import { Workflow, x, sticky, execTrigger, schedule, webhook, pg, set, exec, route, splitOut } from './lib.mjs';

// Routing table: action type -> owning workflow. Adding an action type = one line here + its handler branch.
export const ROUTES = {
  SCREEN_APPLICATION: 'WF-03',
  SEND_REJECTION_NOTICE: 'WF-02',
  INVITE_TO_INTERVIEW: 'WF-04',
  INTERVIEW_INVITE_REMINDER: 'WF-04',
  INTERVIEW_INVITE_EXPIRY: 'WF-04',
  FINALIZE_INTERVIEW_BOOKING: 'WF-04',
  FEEDBACK_REMINDER: 'WF-04',
  FEEDBACK_ESCALATION: 'WF-04',
  EVALUATE_INTERVIEW: 'WF-04',
  PREPARE_OFFER: 'WF-05',
  REQUEST_OFFER_APPROVAL: 'WF-05',
  SEND_OFFER: 'WF-05',
  OFFER_REMINDER: 'WF-05',
  OFFER_FINAL_REMINDER: 'WF-05',
  OFFER_EXPIRY: 'WF-05',
  NOTIFY_NEGOTIATION: 'WF-05',
  NOTIFY_OFFER_CLOSED: 'WF-05',
  START_ONBOARDING: 'WF-06',
  NOTIFY_ONBOARDING_COMPLETE: 'WF-06',
  NOTIFY_REVIEW_QUEUE: 'WF-08',
};

export default function build() {
  const w = new Workflow('WF-00',
    'WF-00: durable timers + transactional outbox. Claims due scheduled actions (SKIP LOCKED), routes each to its owning workflow and records DONE/SKIPPED/FAILED with exponential backoff.');

  w.add(schedule('Every Minute', { field: 'minutes', minutesInterval: 1 }));
  w.add(execTrigger('When Kicked by Workflow', []));
  w.nodes.at(-1).parameters = { inputSource: 'passthrough' };
  w.add({ ...webhook('Kick Webhook', 'ops/kick', { responseMode: 'onReceived' }) });
  w.add(set('Build Context', [
    ['ctx', x('({ actor_type: "SYSTEM", actor_id: "n8n-dispatcher", workflow_name: "WF-00", workflow_version: "1.0.0", execution_id: $execution.id })'), 'object'],
    ['worker', x('"n8n-wf00-" + $execution.id')],
  ], { executeOnce: true }));
  w.add(pg('Claim Due Actions',
    'SELECT id, action_type, entity_type, entity_id, application_id, correlation_id, payload, attempts, max_attempts, run_at FROM api.claim_scheduled_actions($1, 25, 300)',
    ['$json.worker']));
  w.add(pg('Start Execution Log', `SELECT api.start_workflow_execution($1::jsonb, 'SCHEDULE', 'SCHEDULED_ACTION', $2) AS run_id`,
    ['JSON.stringify($("Build Context").first().json.ctx)', '$input.all().length + " action(s)"'], { executeOnce: true }));
  w.add(set('Claimed Actions', [['actions', x('$("Claim Due Actions").all().map(i => i.json)'), 'array']], { executeOnce: true }));
  w.add(splitOut('One Item per Action', 'actions'));
  w.add({ name: 'Loop Actions', type: 'n8n-nodes-base.splitInBatches', typeVersion: 3, parameters: { batchSize: 1, options: {} } });
  w.add(pg('Finish Execution Log', `SELECT api.finish_workflow_execution($1::jsonb, 'SUCCEEDED') AS duration_ms`,
    ['JSON.stringify($("Build Context").first().json.ctx)'], { executeOnce: true }));
  w.chain('Every Minute', 'Build Context');
  w.connect('When Kicked by Workflow', 'Build Context');
  w.connect('Kick Webhook', 'Build Context');
  w.chain('Build Context', 'Claim Due Actions', 'Start Execution Log', 'Claimed Actions', 'One Item per Action', 'Loop Actions');
  w.connect('Loop Actions', 'Finish Execution Log', 0);

  const targets = ['WF-02', 'WF-03', 'WF-04', 'WF-05', 'WF-06', 'WF-08'];
  w.add(route('Route by Action Type', `(${JSON.stringify(ROUTES)})[$json.action_type] || "none"`, targets, 'no handler'));
  w.connect('Loop Actions', 'Route by Action Type', 1);

  w.add(pg('Complete Action', 'SELECT * FROM api.complete_scheduled_action($1::uuid, $2, $3::jsonb, NULLIF($4, \'\'), $5::jsonb)', [],
    {}));
  w.nodes.at(-1).parameters.options = {
    queryReplacement: x('(() => { const a = $("Loop Actions").first().json; const failedHard = $json.error !== undefined; const outcome = failedHard ? "FAILED" : ($json.action_outcome || "DONE"); const reason = failedHard ? String($json.message ?? $json.error?.message ?? (typeof $json.error === "string" ? $json.error : $json.error?.description) ?? "sub-workflow failed") : ($json.action_reason || ""); return [ a.id, outcome, JSON.stringify({ reason, handler: $prevNode.name }), outcome === "FAILED" ? reason : "", JSON.stringify({ ...$("Build Context").first().json.ctx, correlation_id: a.correlation_id, retry_count: a.attempts }) ]; })()'),
  };

  const labels = { 'WF-02': 'Run WF-02 Rejection Notice', 'WF-03': 'Run WF-03 Screening', 'WF-04': 'Run WF-04 Interviews',
    'WF-05': 'Run WF-05 Offers', 'WF-06': 'Run WF-06 Onboarding', 'WF-08': 'Run WF-08 Review Alerts' };
  targets.forEach((target, i) => {
    const inputs = target === 'WF-02'
      ? { mode: 'action', event_id: '', correlation_id: x('$json.correlation_id'), validation: x('({})'), action: x('$json') }
      : { action: x('$json') };
    w.add(exec(labels[target], target, inputs, { onError: 'continueRegularOutput' }));
    w.connect('Route by Action Type', labels[target], i);
    w.connect(labels[target], 'Complete Action');
  });
  w.add(set('No Handler Registered', [
    ['action_outcome', 'FAILED'],
    ['action_reason', x('"no handler registered for " + $json.action_type + " (retried with backoff; requeue after deploying the handler)"')],
  ]));
  w.connect('Route by Action Type', 'No Handler Registered', targets.length);
  w.connect('No Handler Registered', 'Complete Action');
  w.connect('Complete Action', 'Loop Actions');

  w.add(sticky('Overview', '## WF-00 Dispatcher & Scheduler (durable timers + transactional outbox)\nEvery status change enqueues its follow-up in `ops.scheduled_actions` in the same DB transaction; reminders and expiries are rows with a future `run_at`.\nThis workflow claims due rows with `FOR UPDATE SKIP LOCKED` (overlapping runs are safe), routes each one to its owning workflow (routing table in **Route by Action Type**) and records DONE / SKIPPED / FAILED.\nFAILED is retried with exponential backoff (30 s up to 1 h); after max attempts it lands in the error queue.\nTriggers: every minute (safety net), **Kick Webhook** `POST /webhook/ops/kick` (backend, after a person acts) and **When Kicked by Workflow**.', { width: 620, height: 300, color: 4 }));
  return w.toJSON();
}
