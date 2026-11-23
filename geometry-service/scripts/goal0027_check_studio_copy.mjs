/**
 * Goal 0027 headless DOM check.
 *
 * No Chrome extension is connected (eighth goal running), so this runs the REAL
 * shipped function bodies out of studio.js against a stub document whose element
 * ids are scraped from the REAL studio.html. It proves the copy flips with the
 * gate and fails closed; it does not prove pixels.
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', 'fast_mvp', 'static');
const js = readFileSync(`${ROOT}/studio.js`, 'utf8');
const html = readFileSync(`${ROOT}/studio.html`, 'utf8');

// Every id the real markup defines, so a typo'd id in studio.js shows up here.
const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]));

function makeDocument() {
  const nodes = new Map();
  for (const id of ids) nodes.set(id, { id, textContent: '', dataset: {}, hidden: false, classList: new Set() });
  return {
    nodes,
    getElementById(id) {
      const el = nodes.get(id) || null;
      if (el && !el.classList.toggle) {
        el.classList = {
          _s: new Set(),
          toggle(cls, on) { on ? this._s.add(cls) : this._s.delete(cls); },
          has(cls) { return this._s.has(cls); },
        };
      }
      return el;
    },
  };
}

// Pull the exact function bodies out of the shipped file.
function extract(name) {
  const start = js.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`missing ${name}`);
  let i = js.indexOf('{', start), depth = 0;
  for (let j = i; j < js.length; j++) {
    if (js[j] === '{') depth++;
    else if (js[j] === '}' && --depth === 0) return js.slice(start, j + 1);
  }
  throw new Error(`unbalanced ${name}`);
}

const src = ['set', 'toggleClass', 'perceptionDisclosure', 'applyPerceptionCopy', 'updateMountingSourceUI']
  .map(extract).join('\n\n');

function run(perception, result) {
  const document = makeDocument();
  const ctx = vm.createContext({ document, state: { perception, result }, console });
  vm.runInContext(`${src}\napplyPerceptionCopy(); updateMountingSourceUI();`, ctx);
  const read = id => document.getElementById(id);
  return {
    pathCopy: read('pathUploadCopy').textContent,
    badge: read('pathUploadBadge').textContent,
    badgeRefused: read('pathUploadBadge').classList.has('is-refused'),
    headline: read('uploadLaneHeadline').textContent,
    statement: read('uploadLaneStatement').textContent,
    why: read('uploadLaneWhy').textContent,
    noticeReads: read('uploadLaneNotice').dataset.readsPhoto,
    dialogCopy: read('stoneDialogLaneCopy').textContent,
    confirm: read('stoneDialogConfirm').textContent,
    sourceHidden: read('wsMountingSource').hidden,
    sourceHeadline: read('wsMountingSourceHeadline').textContent,
  };
}

const REFUSED = {
  status: 'lane_paused',
  customer_disclosure: {
    reads_your_photo: false,
    headline: 'Your photo is not being read',
    statement: 'MyJewl is not reading photographs right now. …',
  },
};
const AVAILABLE = {
  status: 'acknowledged_diagnostic',
  customer_disclosure: {
    reads_your_photo: true,
    headline: 'Your photo is read as an unvalidated suggestion',
    statement: 'MyJewl compared your photo against its own catalog …',
  },
};
const REFUSED_RESULT = {
  extraction: {
    mounting_source: {
      provider: 'perception_refused_typed_prior',
      read_your_photo: false,
      headline: 'Your photo was not read',
      statement: 'MyJewl did not look at your photograph. …',
    },
  },
};

let failures = 0;
function check(label, actual, predicate) {
  const ok = predicate(actual);
  if (!ok) failures++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}\n        ${JSON.stringify(actual)}`);
}

console.log('── shipped state (lane_paused) ──');
let out = run(REFUSED, REFUSED_RESULT);
check('path card says the photo is not read', out.pathCopy, v => /not reading photographs/.test(v));
check('badge reads "Photo not read"', out.badge, v => v === 'Photo not read');
check('badge is styled as refused', out.badgeRefused, v => v === true);
check('upload notice headline from the server', out.headline, v => v === REFUSED.customer_disclosure.headline);
check('upload notice names the reason', out.why, v => v === 'Reason: lane paused.');
check('notice marked data-reads-photo=false', out.noticeReads, v => v === 'false');
check('stone dialog says the photo is not read', out.dialogCopy, v => /not read/.test(v));
check('confirm button no longer promises analysis', out.confirm, v => v === 'Confirm & Build →');
check('workspace source panel is visible', out.sourceHidden, v => v === false);
check('workspace names the refusal', out.sourceHeadline, v => v === 'Your photo was not read');

console.log('\n── null perception (fetch failed) — must FAIL CLOSED ──');
out = run(null, null);
check('path card still says the photo is not read', out.pathCopy, v => /not reading photographs/.test(v));
check('badge still refused', out.badge, v => v === 'Photo not read');
check('headline still the refusing one', out.headline, v => v === 'Your photo is not being read');
check('confirm button still "Build"', out.confirm, v => v === 'Confirm & Build →');
check('no reason shown when the gate is unknown', out.why, v => v === '');
check('source panel hidden with no result', out.sourceHidden, v => v === true);

console.log('\n── acknowledged lane — copy must UPGRADE ──');
out = run(AVAILABLE, null);
check('path card offers the match', out.pathCopy, v => /suggest a mounting/.test(v));
check('badge marks it unvalidated', out.badge, v => v === 'Photo read · unvalidated');
check('badge not styled as refused', out.badgeRefused, v => v === false);
check('headline is the available one', out.headline, v => v === AVAILABLE.customer_disclosure.headline);
check('notice marked data-reads-photo=true', out.noticeReads, v => v === 'true');
check('confirm button reads Analyze again', out.confirm, v => v === 'Confirm & Analyze →');
check('no refusal reason on the open lane', out.why, v => v === '');

console.log(`\n${failures === 0 ? 'ALL CHECKS PASSED' : `${failures} CHECK(S) FAILED`}`);
process.exit(failures === 0 ? 0 : 1);
