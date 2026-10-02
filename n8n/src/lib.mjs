// Workflow-as-code helpers for the NovaTech n8n workflows.
//
// Each n8n/src/wf-*.mjs file describes one workflow with these helpers; `node n8n/src/build.mjs` turns them into
// n8n workflow JSON (n8n/build/), which scripts/n8n-deploy imports and publishes. Node ids are derived from the
// workflow id and node name, so rebuilding a workflow updates it in place instead of creating new nodes.
import { createHash } from 'node:crypto';

export const CRED = {
  pg: { postgres: { id: 'ntPostgresApp001', name: 'NovaTech DB (n8n_app)' } },
  apiKey: { httpHeaderAuth: { id: 'ntBackendApiKey1', name: 'NovaTech Backend API key' } },
};

// Fixed ids, so workflows can call each other and the error workflow setting survives re-imports.
export const WF = {
  'WF-00': ['kyyh3d0dVadoBjAF', 'WF-00 Dispatcher & Scheduler'],
  'WF-01': ['5ffc9mQNtFMFyUwW', 'WF-01 Application Intake'],
  'WF-02': ['76ahG9hzRP7cuQL4', 'WF-02 Candidate Processing'],
  'WF-03': ['ebKtDTWlruup0Nbg', 'WF-03 Scoring & AI Review'],
  'WF-04': ['ntWf04Interview1', 'WF-04 Interview Management'],
  'WF-05': ['ntWf05OfferMgmt1', 'WF-05 Offer Management'],
  'WF-06': ['ntWf06Onboarding', 'WF-06 Employee Onboarding'],
  'WF-07': ['ntWf07ErrRecover', 'WF-07 Error & Recovery'],
  'WF-08': ['ntWf08Monitoring', 'WF-08 Monitoring & Reporting'],
  'WF-09': ['ntWf09NotifyApi1', 'WF-09 Notification API'],
  'SWF-01': ['aIsZuTp9Z6s1IqLi', 'SWF-01 Backend Call'],
  'SWF-02': ['zNrGlzM4KLrFVMAo', 'SWF-02 Send Notification'],
  'SWF-03': ['IBNva3yYim0EcUCT', 'SWF-03 Fail & Record Error'],
};

/** n8n expression: x('$json.a') -> '={{ $json.a }}' */
export const x = (code) => `={{ ${code} }}`;

/** HTML-escape helper available inside expressions that build emails. */
export const ESC =
  `const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);`;

/** Wraps email body HTML in the shared layout (used inside expressions). */
export const LAYOUT =
  `const layout = (body, footer) => "<div style='font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55;color:#1d2939;max-width:640px'>" + body + "<p style='margin-top:24px;color:#667085;font-size:12px'>" + (footer || "NovaTech Solutions") + "</p></div>";`;

/** Formats an ISO timestamp in the company time zone (inside expressions; Luxon DateTime is built in). */
export const FMT =
  `const fmt = (iso, tz) => iso ? DateTime.fromISO(String(iso)).setZone(tz || "Asia/Karachi").toFormat("cccc d LLLL yyyy, h:mm a") : "-"; const fmtDate = (iso) => iso ? DateTime.fromISO(String(iso)).toFormat("d LLLL yyyy") : "-";`;

const uuidFrom = (seed) => {
  const h = createHash('sha1').update(seed).digest('hex');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-5${h.slice(13, 16)}-a${h.slice(17, 20)}-${h.slice(20, 32)}`;
};

export class Workflow {
  constructor(key, description) {
    [this.id, this.name] = WF[key];
    this.key = key;
    this.description = description;
    this.nodes = [];
    this.connections = {};
    this.positions = {};
  }

  add(def) {
    if (this.nodes.some((n) => n.name === def.name)) throw new Error(`${this.key}: duplicate node name ${def.name}`);
    const node = {
      id: uuidFrom(`${this.id}:${def.name}`),
      name: def.name,
      type: def.type,
      typeVersion: def.typeVersion,
      position: def.position || [0, 0],
      parameters: def.parameters || {},
    };
    for (const k of ['credentials', 'onError', 'executeOnce', 'alwaysOutputData', 'retryOnFail', 'maxTries', 'waitBetweenTries', 'notes']) {
      if (def[k] !== undefined) node[k] = def[k];
    }
    if (def.type === 'n8n-nodes-base.webhook') node.webhookId = uuidFrom(`${this.id}:webhook:${def.name}`);
    this.nodes.push(node);
    return def.name;
  }

  /** Connects `from` (output index) to `to` (input index). */
  connect(from, to, output = 0, input = 0) {
    for (const name of [from, to]) {
      if (!this.nodes.some((n) => n.name === name)) throw new Error(`${this.key}: unknown node ${name}`);
    }
    const outputs = (this.connections[from] ??= { main: [] }).main;
    while (outputs.length <= output) outputs.push([]);
    outputs[output].push({ node: to, type: 'main', index: input });
  }

  /** chain(a, b, c) connects a->b->c through output 0. */
  chain(...names) {
    for (let i = 0; i < names.length - 1; i++) this.connect(names[i], names[i + 1]);
    return names[names.length - 1];
  }

  /** Simple layered layout: x by longest path from a trigger, y by order of appearance in each layer. */
  layout() {
    const incoming = new Map(this.nodes.map((n) => [n.name, 0]));
    for (const outs of Object.values(this.connections)) {
      for (const list of outs.main) for (const c of list) incoming.set(c.node, incoming.get(c.node) + 1);
    }
    const depth = new Map();
    const queue = this.nodes.filter((n) => incoming.get(n.name) === 0 && !n.type.endsWith('stickyNote')).map((n) => n.name);
    queue.forEach((n) => depth.set(n, 0));
    for (let guard = 0; queue.length && guard < 10000; guard++) {
      const name = queue.shift();
      for (const list of this.connections[name]?.main || []) {
        for (const c of list) {
          const d = depth.get(name) + 1;
          if (!depth.has(c.node) || (depth.get(c.node) < d && d < 40)) {
            depth.set(c.node, d);
            queue.push(c.node);
          }
        }
      }
    }
    const rows = new Map();
    for (const n of this.nodes) {
      if (n.type.endsWith('stickyNote')) continue;
      const d = depth.get(n.name) ?? 0;
      const row = rows.get(d) ?? 0;
      rows.set(d, row + 1);
      n.position = [240 + d * 260, 320 + row * 200];
    }
  }

  toJSON({ errorWorkflow = true, timezone } = {}) {
    this.layout();
    const settings = { executionOrder: 'v1', availableInMCP: true, callerPolicy: 'workflowsFromSameOwner' };
    if (errorWorkflow) settings.errorWorkflow = WF['WF-07'][0];
    if (timezone) settings.timezone = timezone;
    return {
      id: this.id,
      name: this.name,
      description: this.description,
      active: false,
      isArchived: false,
      nodes: this.nodes,
      connections: this.connections,
      settings,
      pinData: {},
      tags: [],
    };
  }
}

// ---- node factories ---------------------------------------------------------------------------------
export const sticky = (name, content, { width = 520, height = 260, color = 5, position = [-320, 120] } = {}) => ({
  name, type: 'n8n-nodes-base.stickyNote', typeVersion: 1, position, parameters: { content, width, height, color },
});

export const execTrigger = (name, inputs) => ({
  name, type: 'n8n-nodes-base.executeWorkflowTrigger', typeVersion: 1.2,
  parameters: { inputSource: 'workflowInputs', workflowInputs: { values: inputs.map(([n, t]) => ({ name: n, type: t || 'string' })) } },
});

export const schedule = (name, interval) => ({
  name, type: 'n8n-nodes-base.scheduleTrigger', typeVersion: 1.3, parameters: { rule: { interval: [interval] } },
});

export const errorTrigger = (name) => ({ name, type: 'n8n-nodes-base.errorTrigger', typeVersion: 1, parameters: {} });

export const webhook = (name, path, { responseMode = 'responseNode', auth = true } = {}) => ({
  name, type: 'n8n-nodes-base.webhook', typeVersion: 2.1,
  parameters: { httpMethod: 'POST', path, authentication: auth ? 'headerAuth' : 'none', responseMode, options: {} },
  ...(auth ? { credentials: CRED.apiKey } : {}),
});

export const respond = (name, status, bodyExpr) => ({
  name, type: 'n8n-nodes-base.respondToWebhook', typeVersion: 1.5,
  parameters: { respondWith: 'json', responseBody: x(bodyExpr), options: { responseCode: status } },
});

export const pg = (name, query, params = [], opts = {}) => ({
  name, type: 'n8n-nodes-base.postgres', typeVersion: 2.7, credentials: CRED.pg, ...opts,
  parameters: { operation: 'executeQuery', query, options: params.length ? { queryReplacement: x(`[ ${params.join(', ')} ]`) } : {} },
});

/** Set node. fields: [name, value, type?]; values starting with '=' are expressions (use x()). */
export const set = (name, fields, opts = {}) => ({
  name, type: 'n8n-nodes-base.set', typeVersion: 3.5, ...opts,
  parameters: {
    mode: 'manual', includeOtherFields: false,
    assignments: { assignments: fields.map(([n, v, t]) => ({ id: uuidFrom(`${name}:${n}`), name: n, value: v, type: t || 'string' })) },
  },
});

const INPUT_TYPES = { ctx: 'object', body: 'object', action: 'object', validation: 'object', error: 'object' };

/** Execute Sub-workflow node. target: a WF key; inputs: { name: value } (values may be expressions). */
export const exec = (name, target, inputs, { mode = 'once', wait = true, dynamicId, ...opts } = {}) => {
  const [id, wfName] = WF[target] || [dynamicId, 'dynamic target'];
  return {
    name, type: 'n8n-nodes-base.executeWorkflow', typeVersion: 1.3, ...opts,
    parameters: {
      mode, source: 'database',
      workflowId: dynamicId ? { __rl: true, mode: 'id', value: dynamicId } : { __rl: true, mode: 'id', value: id, cachedResultName: wfName },
      workflowInputs: {
        mappingMode: 'defineBelow', value: inputs, matchingColumns: [],
        schema: Object.keys(inputs).map((k) => ({ id: k, displayName: k, required: false, defaultMatch: false, display: true, canBeUsedToMatch: true, type: INPUT_TYPES[k] || 'string' })),
        attemptToConvertTypes: false, convertFieldsToString: false,
      },
      options: { waitForSubWorkflow: wait },
    },
  };
};

/** SWF-01 call (backend with retry/backoff). All arguments are expression code except path. */
export const backend = (name, { ctx, path, body, entityType, entityId, fault = '""', mode = 'once' }) =>
  exec(name, 'SWF-01', {
    ctx: x(ctx), method: 'POST', path, body: x(body), fault_inject: x(fault), entity_type: entityType, entity_id: x(entityId),
  }, { mode });

/** SWF-02 call (email at most once per dedupe key). All arguments are expression code except template/entityType. */
export const email = (name, { ctx, dedupe, template, recipient, subject, html, applicationId = '""', entityType, entityId = '""', mode = 'once' }, extra = {}) =>
  exec(name, 'SWF-02', {
    ctx: x(ctx), dedupe_key: x(dedupe), channel: 'EMAIL', template_key: template, recipient: x(recipient), subject: x(subject),
    html: x(html), application_id: x(applicationId), entity_type: entityType, entity_id: x(entityId),
  }, { mode, ...extra });

export const cond = (left, operation, right = '', type = 'string') => ({
  id: uuidFrom(`${left}:${operation}:${right}`),
  leftValue: x(left),
  operator: ['true', 'false', 'notEmpty', 'empty', 'exists', 'notExists'].includes(operation)
    ? { type, operation, singleValue: true }
    : { type, operation },
  rightValue: right,
});

/** IF node: output 0 = true, output 1 = false. */
export const iff = (name, conditions, combinator = 'and') => ({
  name, type: 'n8n-nodes-base.if', typeVersion: 2.3,
  parameters: { conditions: { options: { caseSensitive: true, leftValue: '', typeValidation: 'loose', version: 3 }, conditions, combinator }, options: {} },
});

/** Shortcut: IF the expression evaluates to true. */
export const when = (name, code) => iff(name, [cond(`Boolean(${code})`, 'true', '', 'boolean')]);

/** Switch on an expression: one output per key (in order) plus a fallback output. */
export const route = (name, left, keys, fallback = 'unhandled') => ({
  name, type: 'n8n-nodes-base.switch', typeVersion: 3.4,
  parameters: {
    mode: 'rules',
    rules: {
      values: keys.map((k) => ({
        outputKey: k, renameOutput: true,
        conditions: { options: { caseSensitive: true, leftValue: '', typeValidation: 'strict', version: 2 }, conditions: [cond(left, 'equals', k)], combinator: 'and' },
      })),
    },
    options: { fallbackOutput: 'extra', renameFallbackOutput: fallback },
  },
});

export const splitOut = (name, field) => ({
  name, type: 'n8n-nodes-base.splitOut', typeVersion: 1, parameters: { fieldToSplitOut: field, include: 'noOtherFields', options: {} },
});

export const http = (name, { url, method = 'POST', headers = [], body }) => ({
  name, type: 'n8n-nodes-base.httpRequest', typeVersion: 4.5, onError: 'continueRegularOutput',
  parameters: {
    method, url,
    sendHeaders: headers.length > 0, specifyHeaders: 'keypair',
    headerParameters: { parameters: headers.map(([n, v]) => ({ name: n, value: v })) },
    sendBody: Boolean(body), contentType: 'json', specifyBody: 'json', jsonBody: body,
    options: { response: { response: { fullResponse: true, neverError: true } }, timeout: 60000 },
  },
});

// ---- shared handler pieces ----------------------------------------------------------------------------
/** Standard context for a dispatcher-invoked handler. */
export const handlerContext = (wfName) => set('Build Context', [
  ['ctx', x(`({ actor_type: "SYSTEM", actor_id: "n8n", workflow_name: "${wfName}", workflow_version: "1.0.0", execution_id: $execution.id, correlation_id: $json.action.correlation_id })`), 'object'],
  ['action', x('$json.action'), 'object'],
  ['application_id', x('$json.action.application_id || ""')],
  ['entity_type', x('$json.action.entity_type || ""')],
  ['entity_id', x('$json.action.entity_id || ""')],
]);

/** Adds the shared result/finish/failure nodes every dispatcher handler uses. Returns their names. */
export function addHandlerTail(w, wfName, emailNodes = []) {
  const CTX = '$("Build Context").first().json.ctx';
  w.add(pg('Finish & Return',
    `SELECT api.finish_workflow_execution($1::jsonb, $2) AS duration_ms, $3 AS action_outcome, $4 AS action_reason`,
    [`JSON.stringify(${CTX})`, '({ DONE: "SUCCEEDED", SKIPPED: "SKIPPED" })[$json.action_outcome] || "FAILED"', '$json.action_outcome', '$json.action_reason']));
  w.add(set('Skip: No Longer Applies', [
    ['action_outcome', 'SKIPPED'],
    ['action_reason', x(`(() => { const s = $("Start Log & Load State").first().json; const parts = []; if (s.app) parts.push("application " + s.app.status); for (const k of ["interview", "offer", "employee"]) if (s[k]) parts.push(k + " " + s[k].status); return $("Build Context").first().json.action.action_type + " no longer applies (" + (parts.join(", ") || "entity missing") + ")"; })()`)],
  ]));
  w.add(set('Result: Notification', [
    ['action_outcome', x(`[${emailNodes.map((n) => JSON.stringify(n)).join(', ')}].some(n => $(n).isExecuted && $(n).first().json.status === "FAILED") ? "FAILED" : "DONE"`)],
    ['action_reason', x(`$("Build Context").first().json.action.action_type + ": " + ([${emailNodes.map((n) => JSON.stringify(n)).join(', ')}].some(n => $(n).isExecuted && $(n).first().json.status === "FAILED") ? "a message could not be sent (will retry)" : ($json.duplicate_suppressed ? "message already sent earlier" : "message sent"))`)],
  ]));
  w.add(when('Retryable Failure?', '$json.error_class === "RETRYABLE"'));
  w.add(set('Result: Retry Later', [
    ['action_outcome', 'FAILED'],
    ['action_reason', x('($json.error_code || "RETRYABLE") + ": " + ($json.error_message || "temporary failure")')],
  ]));
  w.add(exec('Record Failure (SWF-03)', 'SWF-03', {
    ctx: x(CTX),
    error: x(`(() => { const db = $json.error !== undefined; const msg = db ? String($json.message ?? $json.error?.message ?? (typeof $json.error === "string" ? $json.error : $json.error?.description) ?? "step failed") : String($json.error_message || "step failed"); const m = msg.match(/^([A-Z][A-Z0-9_]+): /); const a = $("Build Context").first().json.action; return { workflow_name: "${wfName}", node_name: a.action_type, error_class: db ? (m ? "NON_RETRYABLE" : "UNKNOWN") : ($json.error_class || "NON_RETRYABLE"), error_code: db ? (m ? m[1] : "DB_ERROR") : ($json.error_code || "STEP_FAILED"), error_message: msg, http_status: db ? null : ($json.status || null), retry_count: db ? 0 : ($json.attempts || 0), entity_type: a.entity_type, entity_id: a.entity_id, replay_workflow: "${wfName}", payload: { action: a } }; })()`),
  }));
  w.add(set('Result: Moved to Error Queue', [
    ['action_outcome', 'DONE'],
    ['action_reason', x('"permanent failure moved to the error queue (" + $json.error_id + ")"')],
    ['error_id', x('$json.error_id')],
  ]));
  w.connect('Skip: No Longer Applies', 'Finish & Return');
  w.connect('Result: Notification', 'Finish & Return');
  w.connect('Retryable Failure?', 'Result: Retry Later', 0);
  w.connect('Retryable Failure?', 'Record Failure (SWF-03)', 1);
  w.connect('Result: Retry Later', 'Finish & Return');
  w.connect('Record Failure (SWF-03)', 'Result: Moved to Error Queue');
  return { finish: 'Finish & Return', skip: 'Skip: No Longer Applies', notified: 'Result: Notification', backendFailed: 'Retryable Failure?', failed: 'Record Failure (SWF-03)' };
}

/** A backend call followed by an "ok?" check; failures go to the shared failure path. Returns the IF node name. */
export function addBackendStep(w, tail, name, call) {
  w.add(backend(name, call));
  const check = w.add(when(`${name.replace(/ \(SWF-01\)$/, '')} OK?`, '$json.ok'));
  w.connect(name, check);
  w.connect(check, tail.backendFailed, 1);
  return check;
}
