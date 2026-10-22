/* Home: the list of projects, with progress for the one that is building. */
(function () {
  const $ = BSA.$, esc = BSA.esc;
  let state = null, timer = null, openMenu = null;

  function chips(p) {
    const c = p.counts || {};
    const out = [];
    if (p.runs && p.runs.length) out.push('<span class="chip">' + p.runs.length + (p.runs.length === 1 ? ' run' : ' runs') + '</span>');
    if (c.accepted != null) out.push('<span class="chip">' + c.accepted + ' accepted designs</span>');
    if (c.families != null) out.push('<span class="chip">' + c.families + ' families</span>');
    if (p.outdated) out.push('<span class="chip warn">built with an older version</span>');
    return out.join('');
  }
  function card(p, job) {
    const building = p.status === 'building';
    let body = '';
    if (building && job && job.slug === p.slug) {
      body = '<div class="note">' + esc(job.stage_label || 'Starting…') + (job.detail ? ' <span class="hint">' + esc(job.detail) + '</span>' : '') + '</div><div class="meter"><i style="width:' + Math.round(100 * job.progress) + '%"></i></div>';
    } else if (building) {
      body = '<div class="note">Building…</div>';
    } else if (p.status === 'failed' || p.status === 'cancelled') {
      body = '<div class="msg bad">' + esc(p.error || 'The build did not finish.') + '</div>';
    } else if (p.status === 'missing') {
      body = '<div class="msg warn">The review pages are missing. Rebuild the project to write them again.</div>';
    } else if (p.rebuild_needed) {
      body = '<div class="msg warn">Built with an older analysis. Rebuild to get the improved placement of designs on the full-length protein and the exact binder-target view in the Inspector.</div>' +
        (p.highlights && p.highlights.length ? '<div class="note">' + esc(p.highlights[0].text) + '</div>' : '');
    } else if (p.highlights && p.highlights.length) {
      body = '<div class="note">' + esc(p.highlights[0].text) + '</div>';
    }
    const when = p.finished ? 'Built ' + BSA.when(p.finished) + (p.seconds ? ' in ' + BSA.dur(p.seconds) : '') : '';
    const acts = [];
    if (p.rebuild_needed && !building) acts.push('<button class="btn primary" data-act="rebuild" type="button">Rebuild</button>');
    if (p.ready && !building) acts.push('<a class="btn' + (p.rebuild_needed ? '' : ' primary') + '" href="/p/' + esc(p.slug) + '/">Open</a>');
    if (building) acts.push('<button class="btn" data-act="cancel" data-job="' + esc(job ? job.id : '') + '" type="button">Cancel build</button>');
    acts.push('<div class="menu"><button class="btn ghost sm" data-act="menu" type="button" aria-haspopup="true" aria-label="More actions for ' + esc(p.name) + '">More ▾</button></div>');
    return '<article class="pcard' + (p.status === 'failed' ? ' err' : '') + '" data-slug="' + esc(p.slug) + '"><h3>' + esc(p.name) + '</h3><div class="meta">' + chips(p) + '</div>' + body +
      (when ? '<div class="when">' + esc(when) + '</div>' : '') + '<div class="acts">' + acts.join('') + '</div></article>';
  }
  function render() {
    const ps = state.projects;
    $('#loading').hidden = true;
    $('#welcome').hidden = ps.length > 0;
    $('#list').hidden = ps.length === 0;
    $('#foot').hidden = false;
    $('#home').textContent = state.home;
    $('#ver').textContent = 'version ' + state.version;
    if (ps.length) $('#cards').innerHTML = ps.map((p) => card(p, state.job)).join('');
    const building = ps.some((p) => p.status === 'building') || !!state.job;
    clearTimeout(timer);
    timer = setTimeout(load, building ? 1500 : 6000);
  }
  async function load() {
    try { state = await BSA.get('state'); } catch (e) { $('#loading').hidden = false; $('#loading').textContent = e.message; return; }
    if (openMenu) return;       // do not redraw under an open menu
    render();
  }
  function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; } }
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-act]');
    if (!btn) { closeMenu(); return; }
    const art = btn.closest('.pcard'); const slug = art ? art.dataset.slug : null;
    const act = btn.dataset.act;
    if (act === 'menu') {
      const had = openMenu && openMenu.parentNode === btn.parentNode; closeMenu(); if (had) return;
      const p = state.projects.find((x) => x.slug === slug);
      const m = document.createElement('div');
      m.innerHTML = '<button data-act="edit" type="button">Edit the inputs…</button>' + (p.ready ? '<button data-act="pages" type="button">Update the review pages</button>' : '') +
        '<button data-act="rebuild" type="button">Rebuild</button><button data-act="rebuild0" type="button">Rebuild from scratch</button>' +
        '<button data-act="reveal" type="button">Show in Finder</button><button data-act="delete" class="danger" type="button">Delete project…</button>';
      btn.parentNode.appendChild(m); openMenu = m; return;
    }
    closeMenu();
    if (act === 'edit') { location.href = '/new?from=' + encodeURIComponent(slug); return; }
    if (act === 'cancel') { BSA.post('jobs/' + btn.dataset.job + '/cancel').then(load); return; }
    if (act === 'reveal') { BSA.post('projects/' + slug + '/reveal').catch((e) => BSA.toast(e.message)); return; }
    if (act === 'pages') { BSA.post('projects/' + slug + '/update-pages').then(load).catch((e) => BSA.toast(e.message)); return; }
    if (act === 'rebuild' || act === 'rebuild0') { BSA.post('projects/' + slug + '/rebuild', { no_cache: act === 'rebuild0' }).then(load).catch((e) => BSA.toast(e.message)); return; }
    if (act === 'delete') {
      const p = state.projects.find((x) => x.slug === slug); const dlg = $('#confirm');
      $('#ctitle').textContent = 'Delete “' + p.name + '”?';
      $('#cbody').innerHTML = '<p>This removes the project folder with its analysis and review pages. Your BindCraft run folders are not touched. Decisions you saved in the review pages are kept by your browser, and you can rebuild the project any time.</p>';
      $('#cno').onclick = () => dlg.close();
      $('#cyes').onclick = async () => { dlg.close(); try { await BSA.post('projects/' + slug + '/delete'); BSA.toast('Project deleted.'); } catch (e) { BSA.toast(e.message); } load(); };
      dlg.showModal();
    }
  });
  $('#showhome').addEventListener('click', () => BSA.post('reveal-home').catch(() => {}));
  load();
})();
