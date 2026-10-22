// A build that fails, in headless Chrome (Node 22 or later), driven over the DevTools protocol (cdp.mjs).
//
//   node app_retry.mjs --url http://127.0.0.1:PORT --run DIR --target FILE --reference FILE --loose-reference FILE [--chrome PATH] [--name NAME]
//
// The steps of a person who made a mistake in the import page. The reference has two chains: A is the target, B has another sequence (the toy reference with
// the residue types of chain B shuffled), so the page can say which chain fits:
//   inputs       one run and the target are chosen (window.BSNEW), the reference is dropped on its drop zone (uploaded): the page suggests chain A and says that
//                the target structure fits it (green note under the reference); the Options select for the placement is there, set to its default
//   first_try    chain B is chosen in the page's own list (Options): the note under the reference turns red, "does not fit chain B". Build is clicked anyway and
//                the build fails: the message and the state of the page are recorded
//   back         "Back to the inputs": the form must be back, with chain B and the red note still there, the progress panel clean, Build enabled by the page
//                itself, and no script error
//   fix          chain A is chosen again in Options (the note turns green) and the placement is changed: the note under the reference names the placement
//                that is chosen (the rigid core, then the whole target, which is what the build uses)
//   second       Build again: the second build must start in the same project (the dropped file moved into it on the first try), finish, and the page must
//                show its success state
//   edit_again   the import page opened for the finished project (/new?from=SLUG): it is filled in, Options are open because the placement is not the default
//   edit_chain_b the import page opened for a project (put into the folder by the test) whose reference chain is B, which is not the chain the page suggests:
//                the fit note is for chain B, the chain the project was made with
//   loose_fit    a reference in which a domain is turned (--loose-reference): the target structure fits it only loosely and the yellow note says what the chosen
//                placement does about that (interface, whole target, core)
//   home_cards   the list of projects: the project that was just built, and one that an older Binderscope built (the test put it into the folder before the
//                session): its card says that the analysis is older and offers Rebuild first
// and prints one JSON document with what every step saw, the console errors and the requests that left the app. Nothing here helps the page: when Build is
// still disabled after the fix, the step records that and does not click. The Python test is test_app_browser.py.
import { readFileSync } from 'node:fs';
import { openPage, evaluate, waitFor, closePage, sleep, send, settled } from './cdp.mjs';

const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : dflt; };
const URL_ = opt('--url');
const CHROME = opt('--chrome', process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
const NAME = opt('--name', 'Retry flow');
const RUN = opt('--run');
const TARGET = opt('--target');
const REFERENCE = opt('--reference');
const LOOSE = opt('--loose-reference');
const J = JSON.stringify;

const out = { url: URL_, steps: {}, failed_at: null };
const page = await openPage(CHROME, URL_ + '/new', { allow: [URL_ + '/'] });

async function step(name, fn) {
  if (out.failed_at) return;
  try { out.steps[name] = await fn(); } catch (e) { out.failed_at = name; out.steps[name] = { error: String(e.message || e).slice(0, 500) }; }
}
const projects = () => evaluate(page, "fetch('/api/state').then((r) => r.json()).then((s) => s.projects.filter((p) => p.slug === 'retry-flow').map((p) => ({ slug: p.slug, name: p.name, status: p.status })))");
// the notes under the reference: the one about the chain and the one about how the target structure fits it
const NOTES = `[...document.querySelectorAll('#rinfo .msg')].map((m) => ({ cls: m.className.replace('msg ', ''), text: m.innerText }))`;
const fitNote = (chain, cls) => `(() => { const m = [...document.querySelectorAll('#rinfo .msg')].find((x) => x.innerText.startsWith('Your target structure')); return !!m && m.classList.contains(${J(cls)}) && m.innerText.includes('chain ${chain}'); })()`;
const choose = (chain) => evaluate(page, `(() => { const s = document.getElementById('rchain'); s.value = ${J(chain)}; s.dispatchEvent(new Event('change', { bubbles: true })); return s.value; })()`);
const place = async (value) => {                                      // the Options select for the placement, as a person changes it
  await evaluate(page, `(() => { const s = document.getElementById('placement'); s.value = ${J(value)}; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await sleep(300);
  return evaluate(page, NOTES);
};

await step('inputs', async () => {
  await waitFor(page, "document.readyState === 'complete' && !!window.BSNEW", 30000, 'the import page');
  const cfg = J({ run: RUN, target: TARGET, reference: readFileSync(REFERENCE, 'utf8'), name: NAME });
  const seen = await evaluate(page, `(async () => {
    const c = ${cfg};
    await BSNEW.addRuns([c.run]);
    await BSNEW.setTarget(c.target);
    const dt = new DataTransfer(); dt.items.add(new File([c.reference], 'reference.cif'));
    document.getElementById('rdrop').dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    for (let i = 0; i < 150 && !document.getElementById('rinfo').innerText.includes('reference.cif'); i++) await new Promise((r) => setTimeout(r, 100));
    const name = document.getElementById('name'); name.value = c.name; name.dispatchEvent(new Event('input', { bubbles: true }));
    const p = document.getElementById('placement');
    return { reference_path: BSNEW.S.reference.path, chains: [...document.getElementById('rchain').options].map((o) => o.value), chain: document.getElementById('rchain').value,
      build_disabled: document.getElementById('build').disabled, need: document.getElementById('need').textContent,
      placement: { value: p.value, options: [...p.options].map((o) => o.value), in_options: !!p.closest('details#opt'), opt_open: document.getElementById('opt').open } };
  })()`);
  return { ...seen, notes: await evaluate(page, NOTES) };
});

await step('first_try', async () => {
  const chosen = await choose('B');
  await waitFor(page, fitNote('B', 'bad'), 15000, 'the note that the target structure does not fit chain B');
  const notes = await evaluate(page, NOTES);
  await evaluate(page, "document.getElementById('build').click()");
  await waitFor(page, "!!document.getElementById('back') || !!document.getElementById('openp')", 120000, 'the first build to end');
  return evaluate(page, `({ title: document.getElementById('ptitle').textContent, out: document.getElementById('pout').innerText, hint: !!document.querySelector('#pout .hint'),
    back_button: !!document.getElementById('back'), retry: BSNEW.S.retry, reference_path: BSNEW.S.reference.path, target_path: BSNEW.S.target.path })`)
    .then(async (seen) => ({ ...seen, chosen, notes, projects: await projects() }));
});

await step('back', async () => {
  const before = page.errors.length;
  await evaluate(page, "document.getElementById('back').click()");
  await sleep(500);
  const seen = await evaluate(page, `({ form_hidden: document.getElementById('form').hidden, bar_hidden: document.getElementById('bar').hidden, build_disabled: document.getElementById('build').disabled,
    need: document.getElementById('need').textContent, chain: document.getElementById('rchain').value, pout_text: document.getElementById('pout').innerText,
    pacts_ids: [...document.querySelectorAll('#pacts button')].map((b) => b.id), pstages_hidden: document.getElementById('pstages').hidden, plog_length: document.getElementById('plog').textContent.length,
    ptitle: document.getElementById('ptitle').textContent })`);
  return { ...seen, notes: await evaluate(page, NOTES), errors: page.errors.slice(before) };
});

await step('fix', async () => {
  const chosen = await choose('A');
  await waitFor(page, fitNote('A', 'good'), 15000, 'the note that the target structure fits chain A');
  const notes = await evaluate(page, NOTES);
  const notes_core = await place('core');
  const notes_whole = await place('whole');
  return evaluate(page, `({ chain: document.getElementById('rchain').value, placement: document.getElementById('placement').value, build_disabled: document.getElementById('build').disabled,
    need: document.getElementById('need').textContent })`).then((seen) => ({ ...seen, chosen, notes, notes_core, notes_whole }));
});

await step('second', async () => {
  const disabled = await evaluate(page, "document.getElementById('build').disabled");
  if (disabled) return { build_disabled: true, started: null, ended: null, projects: await projects() };          // nothing is done for the page: no click on a disabled button
  await evaluate(page, "document.getElementById('build').click()");
  await waitFor(page, "!document.getElementById('progress').hidden && document.getElementById('ptitle').textContent.startsWith('Building')", 20000, 'the second build to start');
  const started = await evaluate(page, `({ title: document.getElementById('ptitle').textContent, stale_errors: document.querySelectorAll('#pout .msg.bad').length,
    pacts_ids: [...document.querySelectorAll('#pacts button')].map((b) => b.id), reference_path: BSNEW.S.reference.path })`);
  // the build is followed on the server first, so that a page that does not show the result is seen at once and not after a long wait
  await waitFor(page, "fetch('/api/state').then((r) => r.json()).then((s) => !s.job && s.projects.length > 0 && s.projects.every((p) => p.status !== 'building'))", 240000, 'the second build to end');
  await waitFor(page, "/^(Your project|The build did not|Build cancelled)/.test(document.getElementById('ptitle').textContent)", 15000, 'the page to show the result').catch(() => null);
  const ended = await evaluate(page, `({ title: document.getElementById('ptitle').textContent, out: document.getElementById('pout').innerText,
    open_href: document.getElementById('openp') ? document.getElementById('openp').getAttribute('href') : null,
    pacts: [...document.querySelectorAll('#pacts button, #pacts a')].map((b) => b.id || b.textContent) })`);
  return { build_disabled: false, started, ended, projects: await projects() };
});

await step('edit_again', async () => {
  await send(page, 'Page.navigate', { url: URL_ + '/new?from=retry-flow' });
  await waitFor(page, "location.pathname === '/new' && document.readyState === 'complete' && !!window.BSNEW && document.querySelectorAll('#runs .runrow').length === 1 && !document.getElementById('build').disabled", 60000, 'the import page to be filled in');
  await waitFor(page, "[...document.querySelectorAll('#rinfo .msg')].some((m) => m.innerText.startsWith('Your target structure'))", 15000, 'the fit note');
  await settled(page);
  return evaluate(page, `({ name: document.getElementById('name').value, chain: document.getElementById('rchain').value, placement: document.getElementById('placement').value,
    opt_open: document.getElementById('opt').open, retry: BSNEW.S.retry, notes: ${NOTES} })`);
});

await step('edit_chain_b', async () => {
  await send(page, 'Page.navigate', { url: URL_ + '/new?from=chain-b' });
  await waitFor(page, "location.pathname === '/new' && document.readyState === 'complete' && !!window.BSNEW && document.querySelectorAll('#runs .runrow').length === 1 && !document.getElementById('build').disabled", 60000, 'the import page to be filled in');
  await waitFor(page, "[...document.querySelectorAll('#rinfo .msg')].some((m) => m.innerText.startsWith('Your target structure'))", 15000, 'the fit note');
  await settled(page);
  return evaluate(page, `({ name: document.getElementById('name').value, chain: document.getElementById('rchain').value, chains: [...document.getElementById('rchain').options].map((o) => o.value),
    placement: document.getElementById('placement').value, notes: ${NOTES} })`);
});

await step('loose_fit', async () => {
  await send(page, 'Page.navigate', { url: URL_ + '/new' });
  await waitFor(page, "location.pathname === '/new' && document.readyState === 'complete' && !!window.BSNEW", 30000, 'the import page');
  await evaluate(page, `(async () => { await BSNEW.setTarget(${J(TARGET)}); await BSNEW.setReference(${J(LOOSE)}); })()`);
  await waitFor(page, "[...document.querySelectorAll('#rinfo .msg')].some((m) => m.innerText.startsWith('Your target structure'))", 15000, 'the fit note');
  const seen = { interface: await evaluate(page, NOTES) };
  for (const v of ['whole', 'core', 'interface']) seen[v === 'interface' ? 'interface_again' : v] = await place(v);
  return seen;
});

await step('home_cards', async () => {
  await send(page, 'Page.navigate', { url: URL_ + '/' });
  await waitFor(page, "location.pathname === '/' && document.querySelectorAll('.pcard').length === 3", 30000, 'the three project cards');
  await settled(page);
  return evaluate(page, `[...document.querySelectorAll('.pcard')].map((c) => ({ slug: c.dataset.slug, name: c.querySelector('h3').textContent,
    chips: [...c.querySelectorAll('.chip')].map((x) => x.textContent), warn: [...c.querySelectorAll('.msg.warn')].map((x) => x.innerText),
    note: (c.querySelector('.note') || {}).textContent || '', acts: [...c.querySelectorAll('.acts > a.btn, .acts > button.btn')].map((b) => ({ text: b.textContent, primary: b.classList.contains('primary'), act: b.dataset.act || 'open' })) }))`);
});

out.errors = page.errors.slice();
out.external = page.external.slice();
await closePage(page);
console.log(JSON.stringify(out));
