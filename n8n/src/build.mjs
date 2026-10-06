// Builds the NovaTech workflows into n8n/build/<slug>.json (ready for `n8n import:workflow`).
//   * code-defined workflows: n8n/src/wf-*.mjs
//   * workflows maintained in the n8n editor: their export in n8n/workflows/, with the documented notes (notes.mjs)
//     and the patches below applied
// Usage: node n8n/src/build.mjs [WF-04 SWF-02 ...]   (no arguments = all)
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { renoteExported } from './lib.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', 'build');
const MODULES = {
  'WF-00': './wf-00-dispatcher-scheduler.mjs',
  'WF-02': './wf-02-candidate-processing.mjs',
  'WF-04': './wf-04-interview-management.mjs',
  'WF-05': './wf-05-offer-management.mjs',
  'WF-06': './wf-06-employee-onboarding.mjs',
  'WF-07': './wf-07-error-recovery.mjs',
  'WF-08': './wf-08-monitoring-reporting.mjs',
  'WF-09': './wf-09-notification-api.mjs',
};
const EXPORTED = {
  'WF-01': 'wf-01-application-intake',
  'WF-03': 'wf-03-scoring-ai-review',
  'SWF-01': 'swf-01-backend-call',
  'SWF-02': 'swf-02-send-notification',
  'SWF-03': 'swf-03-fail-record-error',
};

// SWF-02: one SMTP credential with a neutral name, and the sender taken from the settings (company.name,
// company.careers_email) instead of a fixed address, so switching to a real mailbox is configuration only.
const SMTP = { smtp: { id: 'ntMailpitSmtp001', name: 'NovaTech SMTP (outgoing email)' } };
const PATCHES = {
  'SWF-02': (wf) => {
    const claim = wf.nodes.find((n) => n.name === 'Claim Notification');
    const base = 'SELECT * FROM api.begin_notification($1, $2, $3, $4, NULLIF($5, \'\')::uuid, $6, $7, $8::jsonb)';
    if (claim.parameters.query === base) {
      claim.parameters.query = `SELECT b.*,
       (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.name') AS sender_name,
       (SELECT value #>> '{}' FROM hiring.settings WHERE key = 'company.careers_email') AS sender_email
  FROM api.begin_notification($1, $2, $3, $4, NULLIF($5, '')::uuid, $6, $7, $8::jsonb) AS b`;
    }
    const send = wf.nodes.find((n) => n.name === 'Send Email');
    send.credentials = SMTP;
    send.parameters.fromEmail =
      '={{ ($("Claim Notification").item.json.sender_name || "NovaTech") + " Careers <" + $("Claim Notification").item.json.sender_email + ">" }}';
    return wf;
  },
};

const wanted = process.argv.slice(2).length ? process.argv.slice(2) : [...Object.keys(MODULES), ...Object.keys(EXPORTED)];
mkdirSync(out, { recursive: true });
for (const key of wanted) {
  let wf;
  if (MODULES[key]) {
    const { default: build } = await import(MODULES[key]);
    wf = build();
  } else if (EXPORTED[key]) {
    wf = JSON.parse(readFileSync(join(here, '..', 'workflows', `${EXPORTED[key]}.json`), 'utf8'));
    wf = renoteExported((PATCHES[key] || ((x) => x))(wf), key);
  } else {
    throw new Error(`unknown workflow ${key}; known: ${[...Object.keys(MODULES), ...Object.keys(EXPORTED)].join(', ')}`);
  }
  // A whole-value expression must not contain "}}" inside its code (n8n would end the expression there).
  const check = (value, where) => {
    if (typeof value === 'string' && value.startsWith('={{ ') && value.endsWith(' }}') && value.slice(4, -3).includes('}}')) {
      throw new Error(`${key}/${where}: expression contains "}}"`);
    }
    if (value && typeof value === 'object') for (const [k, v] of Object.entries(value)) check(v, `${where}.${k}`);
  };
  for (const node of wf.nodes) check(node.parameters, node.name);
  const slug = wf.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  writeFileSync(join(out, `${slug}.json`), JSON.stringify(wf, null, 2) + '\n');
  console.log(`built ${key} -> n8n/build/${slug}.json (${wf.nodes.length} nodes)`);
}
