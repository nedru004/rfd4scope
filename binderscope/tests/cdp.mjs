// A small DevTools-protocol driver for headless Chrome (Node 22 or later: global WebSocket and fetch), shared by the browser tests
// (browser_smoke.mjs and app_smoke.mjs).
//
// openPage starts a fresh headless Chrome with its own profile folder, opens a URL in it and collects what a test wants to know: the
// console errors and exceptions (page.errors) and the requests that were not allowed (page.external). Every request that is not a file,
// data or blob URL, or below one of the `allow` prefixes, is blocked and recorded, so a test also shows that nothing goes to the network.
// Chrome runs in its own process group and is killed when the page is closed or this process ends.
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const live = new Set();
export const killAll = () => { for (const p of live) { try { process.kill(-p.pid, 'SIGKILL'); } catch (e) { /* gone */ } } live.clear(); };
process.on('exit', killAll);
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(sig, () => { killAll(); process.exit(130); });

export async function openPage(chrome, url, { allow = [] } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'bs-smoke-'));
  const flags = ['--headless=new', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--hide-scrollbars',
    '--remote-debugging-port=0', `--user-data-dir=${dir}`, '--window-size=1500,1000', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--disable-background-networking', '--disable-component-update', '--disable-sync', '--mute-audio'];
  if (process.getuid && process.getuid() === 0) flags.push('--no-sandbox');         // Chrome refuses to start as root (a container) without it
  const proc = spawn(chrome, [...flags, 'about:blank'], { stdio: 'ignore', detached: true });
  live.add(proc);
  let port = 0;
  for (let i = 0; i < 150 && !port; i++) {
    try { port = Number(readFileSync(join(dir, 'DevToolsActivePort'), 'utf8').split('\n')[0]) || 0; } catch (e) { /* not yet */ }
    if (!port) await sleep(100);
  }
  if (!port) throw new Error('Chrome did not start');
  let target;
  for (let i = 0; i < 80 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((x) => x.type === 'page'); } catch (e) { /* retry */ }
    if (!target) await sleep(100);
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  const page = { ws, id: 0, pending: new Map(), errors: [], external: [], proc, dir };
  const allowed = (u) => u.startsWith('file://') || u.startsWith('data:') || u.startsWith('blob:') || allow.some((a) => u.startsWith(a));
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && page.pending.has(d.id)) { const { res, rej } = page.pending.get(d.id); page.pending.delete(d.id); d.error ? rej(new Error(JSON.stringify(d.error))) : res(d.result); return; }
    const p = d.params;
    if (d.method === 'Runtime.exceptionThrown') page.errors.push('exception: ' + (p.exceptionDetails.exception?.description || p.exceptionDetails.text));
    else if (d.method === 'Runtime.consoleAPICalled' && p.type === 'error') page.errors.push('console.error: ' + p.args.map((a) => a.value ?? a.description ?? '').join(' '));
    else if (d.method === 'Log.entryAdded' && p.entry.level === 'error') page.errors.push(`log.${p.entry.source}: ${p.entry.text} ${p.entry.url || ''}`);
    else if (d.method === 'Fetch.requestPaused') {
      const u = p.request.url;
      if (allowed(u)) send(page, 'Fetch.continueRequest', { requestId: p.requestId }).catch(() => {});
      else { page.external.push(u); send(page, 'Fetch.failRequest', { requestId: p.requestId, errorReason: 'BlockedByClient' }).catch(() => {}); }
    }
  };
  await send(page, 'Page.enable'); await send(page, 'Runtime.enable'); await send(page, 'Log.enable');
  await send(page, 'Fetch.enable', { patterns: [{ urlPattern: '*' }] });
  await send(page, 'Page.navigate', { url });
  return page;
}

export function send(page, method, params = {}) {
  const id = ++page.id;
  page.ws.send(JSON.stringify({ id, method, params }));
  return new Promise((res, rej) => {
    const timer = setTimeout(() => { page.pending.delete(id); rej(new Error(`Chrome did not answer ${method}`)); }, 120000);
    page.pending.set(id, { res: (v) => { clearTimeout(timer); res(v); }, rej: (e) => { clearTimeout(timer); rej(e); } });
  });
}

/** Evaluate a JavaScript expression in the page (a promise is awaited) and return its value. */
export async function evaluate(page, expr) {
  const r = await send(page, 'Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}

/** True once the document is complete and its text, canvases, svgs, images, tables and buttons stopped changing for 600 ms. */
export async function settled(page, timeout = 30000) {
  const t0 = Date.now(); let prev = null; let since = Date.now();
  while (Date.now() - t0 < timeout) {
    let cur;
    try {
      cur = await evaluate(page, `document.readyState + ':' + document.body.innerText.length + ':' + document.querySelectorAll('canvas,svg,img,table,button').length`);
    } catch (e) { cur = 'err'; }
    if (cur !== prev) { prev = cur; since = Date.now(); } else if (cur.startsWith('complete') && Date.now() - since >= 600) return true;
    await sleep(80);
  }
  return false;
}

/** Poll an expression until it is true (errors, such as a page that is being replaced, count as not yet) and return its value. */
export async function waitFor(page, expr, timeout = 30000, what = expr) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    try { const v = await evaluate(page, expr); if (v) return v; } catch (e) { /* the page is loading */ }
    await sleep(100);
  }
  throw new Error(`timed out after ${Math.round(timeout / 1000)} s waiting for ${what}`);
}

export async function closePage(page) {
  try { page.ws.close(); } catch (e) { /* ignore */ }
  try { process.kill(-page.proc.pid, 'SIGKILL'); } catch (e) { /* gone */ }
  live.delete(page.proc);
  for (let i = 0; i < 5; i++) { await sleep(80); try { rmSync(page.dir, { recursive: true, force: true }); break; } catch (e) { /* helpers exiting */ } }
}
