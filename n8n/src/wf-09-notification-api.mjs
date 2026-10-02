// WF-09 Notification API: lets the backend send a one-off message (e.g. a staff sign-in link) through SWF-02,
// so the mail provider stays configured in exactly one place and every message is sent at most once.
import { Workflow, x, sticky, webhook, respond, set, exec, iff, cond } from './lib.mjs';

export default function build() {
  const w = new Workflow('WF-09',
    'WF-09: authenticated POST /webhook/ops/notify for backend-originated messages (staff sign-in links); validates the request and sends through SWF-02 (at most once per dedupe key).');
  w.add(webhook('Message Requested', 'ops/notify'));
  w.add(set('Message', [
    ['ctx', x('({ actor_type: "SYSTEM", actor_id: "backend", workflow_name: "WF-09", workflow_version: "1.0.0", execution_id: $execution.id, correlation_id: $json.headers?.["x-correlation-id"] || "" })'), 'object'],
    ['dedupe_key', x('String($json.body?.dedupe_key || "").slice(0, 200)')],
    ['template_key', x('String($json.body?.template_key || "")')],
    ['recipient', x('String($json.body?.recipient || "").trim()')],
    ['subject', x('String($json.body?.subject || "").slice(0, 300)')],
    ['html', x('String($json.body?.html || "")')],
    ['entity_type', x('String($json.body?.entity_type || "SYSTEM").toUpperCase()')],
    ['entity_id', x('String($json.body?.entity_id || "")')],
    ['application_id', x('String($json.body?.application_id || "")')],
  ]));
  w.add(iff('Valid Message?', [
    cond('$json.dedupe_key', 'notEmpty'),
    cond('/^[a-z][a-z0-9_.]{2,60}$/.test($json.template_key)', 'true', '', 'boolean'),
    cond(String.raw`/^[^@\s,]+@[^@\s,]+\.[^@\s,]+$/.test($json.recipient)`, 'true', '', 'boolean'),
    cond('$json.subject', 'notEmpty'),
    cond('$json.html', 'notEmpty'),
  ]));
  w.add(respond('Respond 400', 400, '({ code: "INVALID_MESSAGE", detail: "dedupe_key, template_key (lower.dot_case), one recipient address, subject and html are required" })'));
  w.add(exec('Send (SWF-02)', 'SWF-02', {
    ctx: x('$json.ctx'), dedupe_key: x('$json.dedupe_key'), channel: 'EMAIL', template_key: x('$json.template_key'),
    recipient: x('$json.recipient'), subject: x('$json.subject'), html: x('$json.html'),
    application_id: x('$json.application_id'), entity_type: x('$json.entity_type'), entity_id: x('$json.entity_id'),
  }));
  w.add(respond('Respond Result', 200, '({ status: $json.status, duplicate_suppressed: Boolean($json.duplicate_suppressed) })'));
  w.chain('Message Requested', 'Message', 'Valid Message?', 'Send (SWF-02)', 'Respond Result');
  w.connect('Valid Message?', 'Respond 400', 1);
  w.add(sticky('Overview', '## WF-09 Notification API\n`POST /webhook/ops/notify` (header auth, backend only). Body: `dedupe_key`, `template_key`, `recipient`, `subject`, `html`, optional `entity_type` / `entity_id` / `application_id`.\nThe message goes through **SWF-02**, so it is recorded in `ops.notifications`, sent at most once per dedupe key, and uses the same mail credential as every other message. Used for staff sign-in links.', { width: 560, height: 220 }));
  return w.toJSON();
}
