/* Added to every review page that the app serves: a link back to the projects (in the page header), and a note when the app has stopped. */
(function () {
  if (window.top !== window.self) return;
  if (!/^\/p\/[^/]+\//.test(location.pathname)) return;
  var a = document.createElement('a');
  a.id = 'bs-appbar'; a.href = '/'; a.textContent = '‹ Projects'; a.title = 'Back to your Binderscope projects';
  var hdr = document.querySelector('header.hdr');
  var inHeader = !!hdr;
  function style(down) {
    if (inHeader) {
      a.style.cssText = 'display:inline-flex;align-items:center;padding:3px 10px;border:1px solid ' + (down ? 'var(--bad)' : 'var(--line-strong)') + ';border-radius:99px;color:' + (down ? 'var(--bad-ink)' : 'var(--ink-2)') + ';font-size:12px;font-weight:500;text-decoration:none;white-space:nowrap;flex:none;';
    } else {
      a.style.cssText = 'position:fixed;left:12px;bottom:12px;z-index:2147483000;font:600 12px/1 -apple-system,system-ui,sans-serif;color:#fff;padding:8px 12px;border-radius:99px;text-decoration:none;box-shadow:0 2px 10px rgba(0,0,0,.28);background:' + (down ? 'rgba(170,45,45,.95)' : 'rgba(52,43,120,.92)');
    }
  }
  style(false);
  if (inHeader) hdr.insertBefore(a, hdr.firstChild); else (document.body || document.documentElement).appendChild(a);
  // ---- decisions: keep a copy in the project folder, so they survive clearing the browser or opening the project in another browser
  var slug = location.pathname.split('/')[2], lastSent = null, reloaded = false;
  function ekey() { try { return window.BS && BS.projectId ? 'binderscope.v1.' + BS.projectId() + '.events' : null; } catch (e) { return null; } }
  function parse(raw) { try { var a = JSON.parse(raw); return Array.isArray(a) ? a : []; } catch (e) { return []; } }
  function union(a, b) {
    var seen = {}, out = [];
    a.concat(b).forEach(function (e) { if (e && e.id && !seen[e.id]) { seen[e.id] = 1; out.push(e); } });
    out.sort(function (x, y) { return (x.ts || '') < (y.ts || '') ? -1 : (x.ts || '') > (y.ts || '') ? 1 : 0; });
    return out;
  }
  function send(raw) {
    lastSent = raw;
    fetch('/api/projects/' + slug + '/state', { method: 'POST', keepalive: true, headers: { 'X-Binderscope': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ events: raw }) }).catch(function () { lastSent = null; });
  }
  function syncDecisions() {
    var k = ekey(); if (!k) return;
    var raw = null; try { raw = localStorage.getItem(k); } catch (e) { return; }
    if (raw && raw !== lastSent) send(raw);
  }
  function restoreDecisions() {
    var k = ekey(); if (!k) return;
    fetch('/api/projects/' + slug + '/state', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (st) {
      var local = '', remote = parse(st && st.events);
      try { local = localStorage.getItem(k) || ''; } catch (e) { return; }
      var merged = union(parse(local), remote);
      if (merged.length > parse(local).length) {
        try { localStorage.setItem(k, JSON.stringify(merged)); } catch (e) { return; }
        var flag = 'bs-restored-' + k;
        try { if (!sessionStorage.getItem(flag)) { sessionStorage.setItem(flag, '1'); location.reload(); return; } } catch (e) { /* reload once only */ }
      }
      lastSent = null; syncDecisions();
    }).catch(function () { /* the app answers again later */ });
  }
  window.addEventListener('load', function () { setTimeout(restoreDecisions, 300); });
  setInterval(syncDecisions, 2500);
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') syncDecisions(); });
  window.addEventListener('pagehide', syncDecisions);

  var fails = 0;
  setInterval(function () {
    fetch('/api/ping', { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('x');
      if (fails) { fails = 0; a.textContent = '‹ Projects'; a.href = '/'; style(false); }
    }).catch(function () {
      if (++fails >= 2) { a.textContent = 'Binderscope has stopped'; a.removeAttribute('href'); style(true); }
    });
  }, 20000);
})();
