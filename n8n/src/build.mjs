// Builds the code-defined workflows into n8n/build/<slug>.json (ready for `n8n import:workflow`).
// Usage: node n8n/src/build.mjs [WF-04 WF-05 ...]   (no arguments = all)
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', 'build');
const MODULES = {
  'WF-00': './wf-00-dispatcher-scheduler.mjs',
  'WF-04': './wf-04-interview-management.mjs',
  'WF-05': './wf-05-offer-management.mjs',
  'WF-06': './wf-06-employee-onboarding.mjs',
  'WF-07': './wf-07-error-recovery.mjs',
  'WF-08': './wf-08-monitoring-reporting.mjs',
};

const wanted = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(MODULES);
mkdirSync(out, { recursive: true });
for (const key of wanted) {
  if (!MODULES[key]) throw new Error(`unknown workflow ${key}; known: ${Object.keys(MODULES).join(', ')}`);
  const { default: build } = await import(MODULES[key]);
  const wf = build();
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
