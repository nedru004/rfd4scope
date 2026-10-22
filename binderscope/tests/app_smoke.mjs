// Smoke test of the Binderscope app in headless Chrome, driven over the DevTools protocol (Node 22 or later: global WebSocket and fetch).
//
//   node app_smoke.mjs --url http://127.0.0.1:PORT --run run01=DIR [--run run02=DIR ...] --target FILE --reference FILE [--chrome PATH] [--name NAME]
//
// One Chrome, one page, the steps of a first session:
//   home_empty        the welcome page of an app without projects
//   new_page          the import page (opened with the welcome page's own link)
//   browse_runs       Add run folders with the page's own file browser (the server says there is no native dialog): the folder of runs is
//                     opened and chosen, the two runs inside it are added
//   inputs            the target chosen through window.BSNEW, the reference dropped on its drop zone (uploaded and kept by the project): the notes under
//                     the reference (the chain that matches and how the target structure fits it) and the Options select for the placement are recorded
//   build             Build project, then wait for the success state
//   overview          Open the project: the Overview page of the served bundle with the '< Projects' link that appbar.js adds
//   pages             the five review pages one after the other, each served with the bar
//   decision_saved    on the Board page a family decision is made through BS.decisions.add and found in the project folder a few seconds later
//   decision_restored the page's own copy is removed and the page reloaded: the decision comes back from the project folder
//   home_card         the link back to the projects: the project's card with its counts
//   project_inputs    what the project recorded of its inputs: the dropped reference was copied into its folder
//   edit_inputs       More > Edit the inputs: the import page opens with the project's inputs filled in
//   menu              the card's More menu, then Update the review pages from it
//   delete            Delete project, confirmed in the dialog: the welcome page is back
// and prints one JSON document with what every step saw, the console errors (all pages) and the requests that left the app. A step that
// fails stops the run; "failed_at" names it. The Python test (test_app_browser.py) asserts on the document.
import { readFileSync } from 'node:fs';
import { openPage, evaluate, settled, waitFor, send, closePage, sleep } from './cdp.mjs';

const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : dflt; };
const all = (name) => args.flatMap((a, i) => (a === name ? [args[i + 1]] : []));
const URL_ = opt('--url');
const CHROME = opt('--chrome', process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
const NAME = opt('--name', 'Toy demo');
const RUNS = all('--run').map((x) => x.slice(x.indexOf('=') + 1));
const TARGET = opt('--target');
const REFERENCE = opt('--reference');
const J = JSON.stringify;

const out = { url: URL_, steps: {}, failed_at: null };
const page = await openPage(CHROME, URL_ + '/', { allow: [URL_ + '/'] });

async function go(path) {
  await send(page, 'Page.navigate', { url: URL_ + path });
  await waitFor(page, `location.pathname === ${J(path.split('?')[0])} && document.readyState === 'complete'`, 30000, 'load ' + path);
  await settled(page);
}

async function reload() {
  const t0 = await evaluate(page, 'performance.timeOrigin');
  await send(page, 'Page.reload', {});
  await waitFor(page, `performance.timeOrigin !== ${t0} && document.readyState === 'complete'`, 30000, 'the page to reload');
}

async function step(name, fn) {
  if (out.failed_at) return;
  try { out.steps[name] = await fn(); } catch (e) { out.failed_at = name; out.steps[name] = { error: String(e.message || e).slice(0, 500) }; }
}

await step('home_empty', async () => {
  await waitFor(page, "document.readyState === 'complete' && document.getElementById('loading') && document.getElementById('loading').hidden", 30000, 'the home page to load');
  return evaluate(page, `({ title: document.title, welcome_hidden: document.getElementById('welcome').hidden, list_hidden: document.getElementById('list').hidden,
    h1: document.querySelector('#welcome h1').innerText, start_href: document.querySelector('#welcome a.btn.primary').getAttribute('href'),
    home: document.getElementById('home').textContent, version: document.getElementById('ver').textContent, n_cards: document.querySelectorAll('.pcard').length })`);
});

await step('new_page', async () => {
  await evaluate(page, "document.querySelector('#welcome a.btn.primary').click()");
  await waitFor(page, "location.pathname === '/new' && document.readyState === 'complete' && !!window.BSNEW", 30000, 'the import page');
  return evaluate(page, `({ title: document.title, build_disabled: document.getElementById('build').disabled, need: document.getElementById('need').textContent,
    runs_text: document.getElementById('runs').innerText, n_inputs: document.querySelectorAll('input,select,button').length })`);
});

await step('browse_runs', async () => {
  await evaluate(page, "document.getElementById('addruns').click()");
  await waitFor(page, "(() => { const d = document.getElementById('browse'); return !!d && d.open && document.querySelectorAll('#bl button').length > 0; })()", 15000, "the page's own file browser");
  const names = "[...document.querySelectorAll('#bl button')].map((b) => b.textContent.replace(/^[^A-Za-z0-9.]+/, ''))";
  const listed = await evaluate(page, names);
  await evaluate(page, `[...document.querySelectorAll('#bl button')].find((b) => /\\/runs$/.test(b.dataset.p)).click()`);
  await waitFor(page, "(() => { const c = document.querySelector('#bc button:last-child'); return !!c && c.textContent === 'runs'; })()", 10000, 'the folder of runs to open');
  const inside = await evaluate(page, names);
  await evaluate(page, "document.getElementById('bok').click()");
  const toast = await waitFor(page, "document.getElementById('toast').textContent.startsWith('Found') && document.getElementById('toast').textContent", 15000, 'the note that the runs were found');
  await waitFor(page, "document.querySelectorAll('#runs .runrow').length === 2 && [...document.querySelectorAll('#runs .runrow')].every((r) => !!r.querySelector('.chips') && !r.innerText.includes('Reading the folder') && r.querySelector('input[data-f=\"id\"]').value !== '')", 30000, 'the two runs to be read');
  return { native: await evaluate(page, 'BSA.native'), listed, inside, toast, dialog_open: await evaluate(page, "document.getElementById('browse').open"),
    run_ids: await evaluate(page, "[...document.querySelectorAll('#runs .runrow input[data-f=\"id\"]')].map((i) => i.value)") };
});

await step('inputs', async () => {
  const cfg = J({ runs: RUNS, target: TARGET, reference: readFileSync(REFERENCE, 'utf8'), reference_name: 'dropped reference.cif', name: NAME });
  return evaluate(page, `(async () => {
    const c = ${cfg};
    if (!BSNEW.S.runs.length) await BSNEW.addRuns(c.runs);
    await BSNEW.setTarget(c.target);
    const dt = new DataTransfer(); dt.items.add(new File([c.reference], c.reference_name));
    document.getElementById('rdrop').dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    for (let i = 0; i < 150 && !document.getElementById('rinfo').innerText.includes(c.reference_name); i++) await new Promise((r) => setTimeout(r, 100));
    const name = document.getElementById('name'); name.value = c.name; name.dispatchEvent(new Event('input', { bubbles: true }));
    const rows = [...document.querySelectorAll('#runs .runrow')];
    return { run_ids: rows.map((r) => r.querySelector('input[data-f="id"]').value), run_chips: rows.map((r) => (r.querySelector('.chips') || {}).innerText || ''),
      run_errors: rows.map((r) => (r.querySelector('.msg.bad') || {}).innerText || ''), target: document.getElementById('tinfo').innerText,
      reference: document.getElementById('rinfo').innerText, reference_path: BSNEW.S.reference.path, chain: document.getElementById('rchain').value,
      chains: [...document.getElementById('rchain').options].map((o) => o.value), need: document.getElementById('need').textContent,
      build_disabled: document.getElementById('build').disabled, name: name.value,
      notes: [...document.querySelectorAll('#rinfo .msg')].map((m) => ({ cls: m.className.replace('msg ', ''), text: m.innerText })),
      placement: { value: document.getElementById('placement').value, options: [...document.getElementById('placement').options].map((o) => o.value),
        in_options: !!document.getElementById('placement').closest('details#opt'), opt_open: document.getElementById('opt').open } };
  })()`);
});

await step('build', async () => {
  await evaluate(page, "document.getElementById('build').click()");
  await waitFor(page, "!!document.getElementById('openp') || !!document.querySelector('#pout .msg.bad')", 240000, 'the build to end');
  return evaluate(page, `({ title: document.getElementById('ptitle').textContent, out: document.getElementById('pout').innerText, error: !!document.querySelector('#pout .msg.bad'),
    open_href: document.getElementById('openp') ? document.getElementById('openp').getAttribute('href') : null, stages_hidden: document.getElementById('pstages').hidden,
    meter: document.getElementById('pmeter').style.width, log_lines: document.getElementById('plog').textContent.split('\\n').filter(Boolean).length,
    form_hidden: document.getElementById('form').hidden })`);
});

let slug = null;
await step('overview', async () => {
  await evaluate(page, "document.getElementById('openp').click()");
  await waitFor(page, "/^\\/p\\/[^/]+\\/$/.test(location.pathname) && document.readyState === 'complete'", 60000, 'the Overview page');
  await settled(page);
  slug = await evaluate(page, 'location.pathname.split("/")[2]');
  return evaluate(page, `(() => { const bar = document.getElementById('bs-appbar'); return { path: location.pathname, title: document.title,
    bar_text: bar && bar.textContent, bar_href: bar && bar.getAttribute('href'), bar_in_header: !!document.querySelector('header.hdr #bs-appbar'),
    bar_count: document.querySelectorAll('#bs-appbar').length, bar_script: [...document.scripts].filter((s) => s.getAttribute('src') === '/_app/appbar.js').length,
    designs: BS.blob('campaign').designs.length, runs: BS.blob('campaign').runs.map((r) => r.id), n_text: document.body.innerText.length,
    text_has_runs: document.body.innerText.includes('2 runs, 22 accepted designs'), n_link: document.querySelectorAll('a[href]').length }; })()`);
});

await step('pages', async () => {                                    // every review page, served over http with the bar added
  const seen = {};
  for (const name of ['index', 'board', 'inspector', 'map', 'query']) {
    const before = page.errors.length;
    await go(`/p/${slug}/${name}.html`);
    seen[name] = await evaluate(page, `(() => { const bar = document.getElementById('bs-appbar'); return { title: document.title, bar_count: document.querySelectorAll('#bs-appbar').length,
      bar_text: bar && bar.textContent, bar_in_header: !!document.querySelector('header.hdr #bs-appbar'), n_text: document.body.innerText.length,
      n_canvas: document.querySelectorAll('canvas').length, n_button: document.querySelectorAll('button').length }; })()`);
    seen[name].errors = page.errors.slice(before);
  }
  return seen;
});

let eventKey = null;
await step('decision_saved', async () => {
  await go(`/p/${slug}/board.html`);
  const added = await evaluate(page, `(() => { const fam = BS.blob('campaign').designs.find((d) => d.status === 'accepted').fam;
    const e = BS.decisions.add({ unit: 'family', design: fam, decision: 'keep', reasons: [] });
    return { id: e.id, family: fam, key: 'binderscope.v1.' + BS.projectId() + '.events', count: BS.decisions.count() }; })()`);
  eventKey = added.key;
  const t0 = Date.now();                                               // the bar sends the page's decisions to the project folder every 2.5 s
  await waitFor(page, `fetch('/api/projects/${slug}/state').then((r) => r.json()).then((s) => JSON.parse(s.events || '[]').length === 1)`, 12000, 'the decision to reach the project folder');
  const seconds = (Date.now() - t0) / 1000;
  const saved = await evaluate(page, `fetch('/api/projects/${slug}/state').then((r) => r.json())`);
  return { added, seconds, server_events: JSON.parse(saved.events || '[]'), local_ids: await evaluate(page, `JSON.parse(localStorage.getItem(${J(eventKey)}) || '[]').map((e) => e.id)`) };
});

await step('decision_restored', async () => {
  await evaluate(page, `localStorage.removeItem(${J(eventKey)})`);
  const before = await evaluate(page, `localStorage.getItem(${J(eventKey)})`);
  const t0 = Date.now();
  await reload();
  await waitFor(page, 'window.BS && BS.decisions && BS.decisions.count() === 1', 20000, 'the decision to come back');
  const seconds = (Date.now() - t0) / 1000;
  await sleep(1500);                                                   // the bar may reload the page once to show the restored decision
  await settled(page);
  return evaluate(page, `({ removed: ${J(before)} === null, seconds: ${seconds}, count: BS.decisions.count(), state: BS.decisions.state('family'),
    local_ids: JSON.parse(localStorage.getItem(${J(eventKey)}) || '[]').map((e) => e.id), path: location.pathname, bar_count: document.querySelectorAll('#bs-appbar').length })`);
});

await step('home_card', async () => {
  await evaluate(page, "document.getElementById('bs-appbar').click()");
  await waitFor(page, "location.pathname === '/' && document.querySelectorAll('.pcard').length > 0", 30000, 'the project card');
  return evaluate(page, `(() => { const c = document.querySelector('.pcard'); const open = c.querySelector('a.btn.primary');
    return { slug: c.dataset.slug, name: c.querySelector('h3').textContent, chips: [...c.querySelectorAll('.chip')].map((x) => x.textContent),
      open_href: open && open.getAttribute('href'), when: (c.querySelector('.when') || {}).textContent || '', note: (c.querySelector('.note') || {}).textContent || '',
      welcome_hidden: document.getElementById('welcome').hidden, list_hidden: document.getElementById('list').hidden, n_cards: document.querySelectorAll('.pcard').length,
      home: document.getElementById('home').textContent }; })()`);
});

const MORE = '.pcard [data-act="menu"]';                              // the More button of the card
const MENU = '.pcard .menu div button';                                // the dropdown is a div inside .menu, next to the More button

await step('project_inputs', async () => evaluate(page, `fetch('/api/projects/${slug}').then((r) => r.json()).then((p) => ({ target_pdb: p.inputs.target_pdb, reference: p.inputs.reference,
  runs: p.inputs.runs.map((r) => r.id), status: p.status }))`));

await step('edit_inputs', async () => {                               // More > Edit the inputs: the import page opens with the project's inputs filled in
  await evaluate(page, `document.querySelector('${MORE}').click()`);
  await waitFor(page, `document.querySelectorAll('${MENU}').length > 0`, 5000, 'the project menu');
  await evaluate(page, `document.querySelector('${MENU}[data-act="edit"]').click()`);
  await waitFor(page, "location.pathname === '/new' && !!window.BSNEW && document.querySelectorAll('#runs .runrow').length === 2 && !document.getElementById('build').disabled", 60000, 'the import page to be filled in');
  const seen = await evaluate(page, `({ search: location.search, name: document.getElementById('name').value, run_ids: [...document.querySelectorAll('#runs .runrow input[data-f="id"]')].map((i) => i.value),
    target: document.getElementById('tinfo').innerText, reference: document.getElementById('rinfo').innerText, chain: document.getElementById('rchain').value,
    partners: document.getElementById('partners').value, retry: BSNEW.S.retry, need: document.getElementById('need').textContent,
    placement: document.getElementById('placement').value, opt_open: document.getElementById('opt').open,
    notes: [...document.querySelectorAll('#rinfo .msg')].map((m) => ({ cls: m.className.replace('msg ', ''), text: m.innerText })) })`);
  await go('/');
  await waitFor(page, "document.querySelectorAll('.pcard').length > 0 && !document.getElementById('list').hidden", 30000, 'the project list');
  return seen;
});

await step('menu', async () => {
  await evaluate(page, `document.querySelector('${MORE}').click()`);
  await waitFor(page, `document.querySelectorAll('${MENU}').length > 0`, 5000, 'the project menu');
  return evaluate(page, `[...document.querySelectorAll('${MENU}')].map((b) => b.textContent)`);
});

await step('update_pages', async () => {
  const state = () => evaluate(page, "fetch('/api/state').then((r) => r.json()).then((s) => ({ job: s.job && s.job.slug, finished: s.projects[0].finished, status: s.projects[0].status }))");
  const before = await state();
  await evaluate(page, `document.querySelector('${MENU}[data-act="pages"]').click()`);
  await waitFor(page, `fetch('/api/state').then((r) => r.json()).then((s) => !s.job && s.projects[0].status === 'ready' && s.projects[0].finished !== ${J(before.finished)})`, 60000, 'the pages to be written');
  const after = await state();
  await waitFor(page, "!!document.querySelector('.pcard a.btn.primary')", 15000, 'the Open button to come back');
  return { before_finished: before.finished, after_finished: after.finished, status: after.status, menu_closed: await evaluate(page, `document.querySelectorAll('${MENU}').length === 0`) };
});

await step('delete', async () => {
  await evaluate(page, `document.querySelector('${MORE}').click()`);
  await waitFor(page, `!!document.querySelector('${MENU}[data-act="delete"]')`, 5000, 'the project menu');
  await evaluate(page, `document.querySelector('${MENU}[data-act="delete"]').click()`);
  await waitFor(page, "document.getElementById('confirm').open", 5000, 'the confirmation');
  const asked = await evaluate(page, "({ title: document.getElementById('ctitle').textContent, body: document.getElementById('cbody').innerText })");
  await evaluate(page, "document.getElementById('cyes').click()");
  try {
    await waitFor(page, "!document.getElementById('welcome').hidden && document.getElementById('list').hidden", 20000, 'the welcome page to come back');
  } catch (e) {
    const seen = await evaluate(page, "fetch('/api/state').then((r) => r.json()).then((s) => ({ toast: document.getElementById('toast').textContent, projects: s.projects.map((p) => p.slug + ':' + p.status), job: s.job && s.job.slug, welcome_hidden: document.getElementById('welcome').hidden, list_hidden: document.getElementById('list').hidden, cards: document.querySelectorAll('.pcard').length, dialog: document.getElementById('confirm').open }))");
    throw new Error(String(e.message) + ' ' + JSON.stringify(seen));
  }
  return { asked, dialog_open: await evaluate(page, "document.getElementById('confirm').open"), list_hidden: await evaluate(page, "document.getElementById('list').hidden") };
});

out.errors = page.errors.slice();
out.external = page.external.slice();
await closePage(page);
console.log(JSON.stringify(out));
