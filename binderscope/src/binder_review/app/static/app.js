/* Helpers shared by the app's pages: API calls, a toast, the file chooser (native dialog, or the page's own browser when there is none). */
(function () {
  const BSA = window.BSA = { native: null };
  BSA.api = async function (path, opts) {
    opts = opts || {};
    const init = { method: opts.method || 'GET', headers: {} };
    if (init.method !== 'GET') init.headers['X-Binderscope'] = '1';
    if (opts.json !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.json); }
    if (opts.body !== undefined) init.body = opts.body;
    let r;
    try { r = await fetch(path, init); } catch (e) { throw new Error('Binderscope is not running any more. Open it again from your Applications folder.'); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* no body */ }
    if (!r.ok) { const err = new Error((data && data.error) || ('The request failed (' + r.status + ').')); err.status = r.status; throw err; }
    return data;
  };
  BSA.get = (p) => BSA.api('/api/' + p);
  BSA.post = (p, json) => BSA.api('/api/' + p, { method: 'POST', json: json || {} });
  BSA.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  BSA.$ = (s, r) => (r || document).querySelector(s);
  BSA.when = function (iso) {
    if (!iso) return '';
    const d = new Date(iso); if (isNaN(d)) return '';
    return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  };
  BSA.dur = function (s) {
    s = Math.round(s || 0);
    return s < 90 ? s + ' s' : Math.floor(s / 60) + ' min ' + (s % 60) + ' s';
  };
  let tt;
  BSA.toast = function (msg, ms) {
    const t = document.getElementById('toast'); if (!t) return;
    t.textContent = msg; t.classList.add('on'); clearTimeout(tt); tt = setTimeout(() => t.classList.remove('on'), ms || 3500);
  };
  // quit
  const q = document.getElementById('quit');
  if (q) q.addEventListener('click', async () => {
    if (!window.confirm('Stop Binderscope? Your projects are kept in your Binderscope folder.')) return;
    try { await BSA.post('quit'); } catch (e) { /* already gone */ }
    document.body.innerHTML = '<main class="wrap"><h1>Binderscope has stopped.</h1><p class="lead">You can close this window. Open Binderscope again from your Applications folder when you need it.</p></main>';
    setTimeout(() => { try { window.close(); } catch (e) { /* not allowed */ } }, 400);
  });
  setInterval(() => { fetch('/api/ping').catch(() => {}); }, 20000);

  // ---- the page's own file browser: used when the system has no native dialog
  BSA.browse = function (o) {
    return new Promise((resolve) => {
      let dlg = document.getElementById('browse');
      if (!dlg) {
        dlg = document.createElement('dialog'); dlg.id = 'browse';
        dlg.innerHTML = '<div class="dh"><h3 id="bt"></h3></div><div class="db"><div class="crumb" id="bc"></div><div class="ls" id="bl"></div><p class="hint" id="bh"></p></div><div class="df"><button class="btn" id="bcancel" type="button">Cancel</button><button class="btn primary" id="bok" type="button">Choose</button></div>';
        document.body.appendChild(dlg);
      }
      const $ = (s) => dlg.querySelector(s);
      let cur = null, sel = null;
      $('#bt').textContent = o.prompt || (o.kind === 'file' ? 'Choose a file' : 'Choose a folder');
      $('#bh').textContent = o.kind === 'file' ? 'Click a file, then Choose.' : 'Open the folder you want, then Choose.';
      async function go(path) {
        let d;
        try { d = await BSA.get('ls?path=' + encodeURIComponent(path || '')); } catch (e) { BSA.toast(e.message); return; }
        cur = d.path; sel = o.kind === 'folder' ? d.path : null;
        const parts = d.path.split('/').filter(Boolean);
        $('#bc').innerHTML = '<button data-p="/">/</button>' + parts.map((p, i) => '<span>›</span><button data-p="/' + BSA.esc(parts.slice(0, i + 1).join('/')) + '">' + BSA.esc(p) + '</button>').join('');
        $('#bl').innerHTML = (d.parent ? '<button data-p="' + BSA.esc(d.parent) + '"><span class="t">↰</span>..</button>' : '') +
          d.entries.filter((e) => o.kind === 'folder' ? e.dir : true).map((e) => '<button data-p="' + BSA.esc(e.path) + '" data-d="' + (e.dir ? 1 : 0) + '"><span class="t">' + (e.dir ? '▸' : '·') + '</span>' + BSA.esc(e.name) + '</button>').join('');
        $('#bok').disabled = o.kind === 'file';
      }
      dlg.onclick = (ev) => {
        const b = ev.target.closest('button[data-p]'); if (!b) return;
        if (b.dataset.d === '0') { sel = b.dataset.p; dlg.querySelectorAll('.ls button').forEach((x) => x.classList.toggle('sel', x === b)); $('#bok').disabled = false; return; }
        go(b.dataset.p);
      };
      const done = (v) => { dlg.close(); resolve(v); };
      $('#bcancel').onclick = () => done(null);
      $('#bok').onclick = () => done(sel ? [sel] : null);
      dlg.oncancel = () => resolve(null);
      dlg.showModal();
      go(o.start || '');
    });
  };
  BSA.choose = async function (o) {
    if (BSA.native !== false) {
      try {
        const r = await BSA.post('pick', { kind: o.kind, multiple: !!o.multiple, prompt: o.prompt, default: o.start });
        return r.cancelled ? null : r.paths;
      } catch (e) { if (e.status !== 501) throw e; BSA.native = false; }
    }
    return BSA.browse(o);
  };
})();
