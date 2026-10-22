// Smoke test of a built bundle in headless Chrome, driven over the DevTools protocol (Node 22 or later: global WebSocket and fetch).
//
//   node browser_smoke.mjs BUNDLE_DIR [--chrome PATH] [--pages index,board,inspector,map,query] [--checks FILE.json]
//
// --checks names a JSON file {page: {name: "JavaScript expression"}}; every expression is evaluated in the page after it has settled
// (promises are awaited) and the value, or the error text, is reported under "checks".
//
// For every page it opens file://BUNDLE_DIR/<page>.html in a fresh headless Chrome (one at a time, killed on exit), waits for
// the document and the page's own rendering to settle, and prints one JSON document with, per page: the console errors and
// exceptions, the requests that left the bundle, the title, the visible text and the number of key elements. The Python test
// (test_browser_bundle.py) asserts on that document. Nothing is fetched from the network: every request that is not a file of the bundle is
// blocked and recorded. The driver itself is cdp.mjs, shared with app_smoke.mjs.
import { readFileSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { openPage, evaluate, settled, closePage } from './cdp.mjs';

const args = process.argv.slice(2);
const bundle = resolve(args[0] || '.');
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : dflt; };
const CHROME = opt('--chrome', process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
const PAGES = opt('--pages', 'index,board,inspector,map,query').split(',');
const CHECKS = opt('--checks', null) ? JSON.parse(readFileSync(opt('--checks'), 'utf8')) : {};

const PROBE = `(() => {
  const q = (s) => document.querySelectorAll(s).length;
  return {
    title: document.title, h1: (document.querySelector('h1, .brand, header')?.innerText || '').slice(0, 80),
    text: document.body.innerText.slice(0, 4000), n_text: document.body.innerText.length,
    n_button: q('button'), n_link: q('a[href]'), n_table: q('table'), n_svg: q('svg'), n_canvas: q('canvas'), n_img: q('img'),
    n_row: q('tr'), n_input: q('input,select'), scripts: q('script'),
    overflow_x: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
})()`;

const out = { bundle, pages: {} };
for (const name of PAGES) {
  const file = join(bundle, name + '.html');
  if (!existsSync(file)) { out.pages[name] = { missing: true }; continue; }
  const page = await openPage(CHROME, pathToFileURL(file).href);
  try {
    const ok = await settled(page);
    const probe = await evaluate(page, PROBE);
    const checks = {};
    for (const [label, expr] of Object.entries(CHECKS[name] || {})) {
      try { checks[label] = await evaluate(page, expr); } catch (e) { checks[label] = { error: String(e.message || e).slice(0, 300) }; }
    }
    out.pages[name] = { settled: ok, errors: page.errors.slice(), external: page.external.slice(), checks, ...probe };
  } finally {
    await closePage(page);
  }
}
console.log(JSON.stringify(out));
