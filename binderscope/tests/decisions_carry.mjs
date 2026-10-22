/* The decision log's carry-over rule, without a browser.

   bs.js is a plain script that ends in `})(window)`, so it is run here in a vm
   context with the few DOM things its init() touches. What is under test is
   otherLogs(): which other logs in this browser's storage belong to the review
   the page is showing, and so may be brought into it.

   Run directly (`node decisions_carry.mjs`) or through test_decisions_carry.py.
   Prints one line per case and exits non-zero on the first failure. */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(join(HERE, '..', 'viewer', 'js', 'bs.js'), 'utf8');

/** A storage that behaves like localStorage and can be inspected. */
function makeStorage(seed) {
  const m = new Map(Object.entries(seed || {}));
  return {
    get: (k) => (m.has(k) ? m.get(k) : null),
    set: (k, v) => { m.set(k, String(v)); return true; },
    remove: (k) => m.delete(k),
    keys: () => Array.from(m.keys()),
    all: m,
  };
}

/** BS, as a page of a bundle whose manifest names `runs`, would see it. */
function loadBS(runs) {
  const node = () => ({ style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
                        appendChild() {}, remove() {}, addEventListener() {}, setAttribute() {},
                        getAttribute: () => null, querySelector: () => null, querySelectorAll: () => [] });
  const document = Object.assign(node(), { readyState: 'complete', documentElement: node(), body: node(), createElement: node });
  const win = { document, addEventListener() {}, setTimeout, clearTimeout, console,
                BINDERSCOPE: { manifest: { runs: runs.map((id) => ({ id })) } } };
  win.window = win;
  vm.runInContext(SOURCE, vm.createContext(win));
  return win.BS;
}

/** A decision store for a review of `runs`, with `prefix` as its storage key. */
function store(runs, prefix, storage) {
  return loadBS(runs).createDecisions({ prefix, storage, salt: 'test' });
}

// Event ids are 'ev' and 8 to 32 hex digits, which the store checks on import:
// an id outside that shape is refused as invalid and the decision is dropped.
const evId = (name, i) => 'ev' + (name.split('').map((c) => c.charCodeAt(0).toString(16)).join('') + '000000000000').slice(0, 10)
  + i.toString(16).padStart(2, '0');
const log = (name, events) => JSON.stringify(events.map((e, i) => ({
  v: 1, type: 'decision', id: evId(name, i), reviewer: 'you', ts: '2026-10-22T00:00:0' + i + '-05:00',
  unit: 'design', design: e, run: null, decision: 'keep',
})));

let failed = 0;
function check(what, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log((ok ? 'ok   ' : 'FAIL ') + what + (ok ? '' : `\n       got  ${JSON.stringify(got)}\n       want ${JSON.stringify(want)}`));
}

// Keys are 'binderscope.v1.<hex>.', one per review; the hex is a content hash of
// what was reviewed, so every one of these is a different review.
const A = 'binderscope.v1.aaaaaa.', B = 'binderscope.v1.bbbbbb.', C = 'binderscope.v1.cccccc.';
const seen = (s) => s.otherLogs().map((o) => [o.name, o.events, o.relation]);

// 1. The same campaign reviewed twice: the older log is offered.
check('the same campaign, reviewed again',
  seen(store(['a'], B, makeStorage({ [A + 'events']: log('a', ['d1', 'd2']), [A + 'name']: 'a' }))),
  [['a', 2, 'same']]);

// 2. A project review, with each member campaign already reviewed on its own.
check('two member campaigns, one project review',
  seen(store(['a', 'b'], C, makeStorage({ [A + 'events']: log('a', ['d1']), [A + 'name']: 'a',
                                          [B + 'events']: log('b', ['d2', 'd3']), [B + 'name']: 'b' }))),
  [['a', 1, 'fewer'], ['b', 2, 'fewer']]);

// 3. The other direction: the project was reviewed first, one campaign is opened now.
check('a project review, seen from one campaign',
  seen(store(['a'], C, makeStorage({ [A + 'events']: log('p', ['d1', 'd2']), [A + 'name']: 'a+b' }))),
  [['a+b', 2, 'more']]);

// 4. Another target altogether: nothing in it is about these designs.
check('a review of other runs is not offered',
  seen(store(['a', 'b'], C, makeStorage({ [A + 'events']: log('c', ['d9']), [A + 'name']: 'c' }))),
  []);

// 5. Overlapping but neither contains the other (a project of a and c): left out,
//    because most of what it holds is about designs that are not here.
check('an overlapping project is not offered',
  seen(store(['a', 'b'], C, makeStorage({ [A + 'events']: log('ac', ['d1']), [A + 'name']: 'a+c' }))),
  []);

// 6. A log with no name beside it cannot be placed, so it is left alone.
check('a log with no name is ignored',
  seen(store(['a'], C, makeStorage({ [A + 'events']: log('x', ['d1']) }))),
  []);

// 7. An empty log is not worth mentioning.
check('an empty log is not offered',
  seen(store(['a'], C, makeStorage({ [A + 'events']: '[]', [A + 'name']: 'a' }))),
  []);

// 8. Bringing them over: the decisions land in this review's own log.
{
  const storage = makeStorage({ [A + 'events']: log('a', ['d1', 'd2']), [A + 'name']: 'a' });
  const D = store(['a', 'b'], C, storage);
  const res = D.carryForward(D.otherLogs()[0].id);
  check('carrying forward adds both decisions', [res.added, res.restored], [2, 0]);
  check('and they are the current decisions here', Array.from(D.current().keys()).sort(),
    ['design\u0000d1', 'design\u0000d2']);
  check('the log they came from is untouched', JSON.parse(storage.get(A + 'events')).length, 2);
  check('bringing them over twice changes nothing', D.carryForward(D.otherLogs()[0].id).added, 0);
}

console.log(failed ? `\n${failed} failed` : '\nall passed');
process.exit(failed ? 1 : 0);
