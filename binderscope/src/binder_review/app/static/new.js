/* Import page: choose runs and structures, then build. The server runs `binder-review import` and the page follows its progress. */
(function () {
  const $ = BSA.$, esc = BSA.esc;
  const S = { runs: [], target: null, reference: null, refChain: 'A', profile: null, suggestions: [], retry: null };
  const baseName = (p) => String(p).replace(/\/+$/, '').split('/').pop();
  const dirOf = (p) => String(p).replace(/\/+$/, '').split('/').slice(0, -1).join('/') || '/';

  // ---------------------------------------------------------------- runs
  function uniqueId(want, skip) {
    let id = want || 'run', n = 2; const used = new Set(S.runs.filter((r) => r !== skip).map((r) => r.id));
    while (used.has(id)) id = (want || 'run') + '-' + n++;
    return id;
  }
  function chipsOf(i) {
    const c = [];
    c.push('<span class="chip good">' + esc(i.reader) + (i.experimental ? ' · experimental' : '') + '</span>');
    c.push('<span class="chip">' + i.accepted + ' accepted</span><span class="chip">' + i.rejected + ' rejected</span><span class="chip">' + i.families + ' families</span>');
    if (i.hotspots && i.hotspots.length) c.push('<span class="chip">hotspots ' + esc(i.hotspots.join(', ')) + '</span>');
    if (i.binder_length) c.push('<span class="chip">binders ' + i.binder_length[0] + '–' + i.binder_length[1] + ' aa</span>');
    if (!i.settings_found) c.push(i.hotspots && i.hotspots.length ? '<span class="chip" title="There is no settings file in the folder, so the hotspots were read from the result tables or contig.">hotspots read from the tables</span>' : '<span class="chip warn" title="BindCraft writes hotspots into a settings file; RFdiffusion 4 may carry them in the contig. Without either, Binderscope cannot say which hotspots a design touches.">no settings file: hotspots unknown</span>');
    (i.warnings || []).slice(0, 1).forEach((w) => c.push('<span class="chip warn">' + esc(w).slice(0, 90) + '</span>'));
    return c.join('');
  }
  function renderRuns() {
    const box = $('#runs');
    if (!S.runs.length) { box.innerHTML = '<div class="drop">No runs yet. Use “Add run folders…”.</div>'; check(); return; }
    box.innerHTML = S.runs.map((r, k) => '<div class="runrow" data-k="' + k + '"><input type="text" value="' + esc(r.id) + '" aria-label="Name of this run" data-f="id"><span class="path" title="' + esc(r.path) + '"><bdo dir="ltr">' + esc(r.path) + '</bdo></span>' +
      '<button class="btn ghost sm" data-f="rm" type="button" aria-label="Remove this run">Remove</button>' +
      (r.loading ? '<div class="chips"><span class="hint">Reading the folder…</span></div>' : r.error ? '<div class="msg bad">' + esc(r.error) + '</div>' : '<div class="chips">' + chipsOf(r.info) + '</div>') + '</div>').join('');
    check();
  }
  async function addRuns(paths) {
    for (const path of paths) {
      if (S.runs.some((r) => r.path === path)) continue;
      const run = { path, id: '', loading: true, info: null, error: null };
      S.runs.push(run); renderRuns();
      try {
        const info = await BSA.get('inspect-run?path=' + encodeURIComponent(path));
        run.loading = false;
        if (!info.ok && info.children && info.children.length) {
          S.runs.splice(S.runs.indexOf(run), 1); renderRuns();
          BSA.toast('Found ' + info.children.length + (info.children.length === 1 ? ' run' : ' runs') + ' inside ' + baseName(path) + '.');
          await addRuns(info.children); continue;
        }
        if (!info.ok) { run.error = info.error; run.id = uniqueId(baseName(path).replace(/[^A-Za-z0-9_.-]/g, '-'), run); }
        else {
          run.info = info; run.id = uniqueId(info.id, run); run.path = info.path;
          (info.target_candidates || []).forEach((c) => { if (!S.suggestions.some((x) => x.path === c.path)) S.suggestions.push(c); });
          if (!$('#name').value && !S.nameTouched) $('#name').value = suggestName();
        }
      } catch (e) { run.loading = false; run.error = e.message; run.id = uniqueId('run', run); }
      renderRuns(); renderSuggestions();
    }
  }
  function suggestName() {
    const first = S.runs.find((r) => r.info); if (!first) return '';
    const parent = baseName(dirOf(first.path));
    return parent && !/^(Users|home|Documents|Desktop|Downloads|data|runs?|results?|output|outputs|designs?|bindcraft|bindcraft[ _-]?(data|runs?|output|results?))$/i.test(parent) ? parent : baseName(first.path);
  }
  $('#runs').addEventListener('input', (ev) => {
    const row = ev.target.closest('.runrow'); if (!row) return;
    if (ev.target.dataset.f === 'id') { S.runs[+row.dataset.k].id = ev.target.value.trim(); check(); }
  });
  $('#runs').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-f="rm"]'); if (!b) return;
    S.runs.splice(+b.closest('.runrow').dataset.k, 1); renderRuns();
  });
  $('#addruns').addEventListener('click', async () => {
    const last = S.runs.length ? dirOf(S.runs[S.runs.length - 1].path) : '';
    try { const p = await BSA.choose({ kind: 'folder', multiple: true, prompt: 'Choose design run folders (BindCraft or RFdiffusion 4)', start: last }); if (p) addRuns(p); } catch (e) { BSA.toast(e.message); }
  });
  const addTyped = () => { const v = $('#runpath').value.trim(); if (v) { addRuns([v]); $('#runpath').value = ''; } };
  $('#runadd').addEventListener('click', addTyped);
  $('#runpath').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') addTyped(); });

  // ---------------------------------------------------------------- structures
  function renderTarget() {
    const t = S.target;
    $('#tinfo').innerHTML = t ? (t.info.ok ? '<span class="filechip"><b title="' + esc(t.path) + '">' + esc(t.info.name) + '</b><span class="hint">' + t.info.format + ' · ' + t.info.chains.map((c) => 'chain ' + c.id + ', ' + c.residues + ' residues').join('; ') + '</span></span>' : '<span class="msg bad">' + esc(t.info.error) + '</span>') : '<span class="hint">not chosen yet</span>';
    renderSuggestions(); check();
  }
  function renderSuggestions() {
    const box = $('#tsugg'); const list = S.suggestions.filter((c) => !S.target || S.target.path !== c.path);
    if (!list.length || S.target) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = 'Found next to your run: ' + list.slice(0, 4).map((c) => '<button class="chip btnlike' + (c.match ? ' on' : '') + '" data-p="' + esc(c.path) + '" type="button" title="' + esc(c.path) + '">' + esc(c.name) + (c.match ? ' (named in the settings)' : '') + '</button>').join('');
  }
  $('#tsugg').addEventListener('click', (ev) => { const b = ev.target.closest('[data-p]'); if (b) setTarget(b.dataset.p); });
  async function setTarget(path) {
    const info = await BSA.get('check-structure?path=' + encodeURIComponent(path));
    S.target = { path, info }; renderTarget();
    if (S.reference) await setReference(S.reference.path, S.reference.id);
  }
  function renderReference(keepChain) {
    const r = S.reference, box = $('#rinfo');
    if (!r) { box.innerHTML = '<span class="hint">not chosen yet</span>'; fillChains(); check(); return; }
    if (!r.info.ok) { box.innerHTML = '<div class="msg bad">' + esc(r.info.error) + '</div>'; fillChains(); check(); return; }
    const i = r.info, ch = i.chains.map((c) => c.id + ' (' + c.residues + ')').join(', ');
    let note = '';
    if (S.target && S.target.info.ok) {
      note = i.suggest_chain ? '<div class="msg good" style="margin-top:8px">Chain ' + esc(i.suggest_chain) + ' matches your target structure' + (i.partners.length ? '; chain' + (i.partners.length > 1 ? 's ' : ' ') + esc(i.partners.join(', ')) + ' will be treated as the other subunit' + (i.partners.length > 1 ? 's' : '') : '') + '.</div>' :
        '<div class="msg warn" style="margin-top:8px">No chain of this file clearly matches your target structure. Check that it is the full-length protein of the same target, or pick the chain in Options.</div>';
    }
    box.innerHTML = '<span class="filechip"><b title="' + esc(r.path) + '">' + esc(i.name) + '</b><span class="hint">' + i.format + ' · chains ' + esc(ch) + '</span></span>' + note + fitNote(i.fit);
    fillChains(); check();
  }
  // how the target construct fits the chosen chain of the reference (the same numbering vote and CA fit the build uses)
  const placeVal = () => ($('#placement') ? $('#placement').value : 'interface');
  const placeNote = () => placeVal() === 'interface' ? 'Each design is placed by a fit around its own interface.' : placeVal() === 'core' ? 'Each design is placed by a fit of the rigid core of the target.' : 'Each design is placed by a fit of the whole target.';
  function fitNote(f) {
    if (!f) return '';
    if (!f.ok) return '<div class="msg warn" style="margin-top:8px">' + esc(f.error) + '</div>';
    const n = f.n, N = f.n_construct;
    if (f.rmsd == null || n < 10 || n < 0.6 * N || f.identity < 0.7)
      return '<div class="msg bad" style="margin-top:8px">Your target structure does not fit chain ' + esc(f.chain) + ': ' + n + ' of its ' + N + ' residues match by number and ' + Math.round(100 * f.identity) + '% of those are the same amino acid. This is usually the wrong chain, another protein, or a different numbering. Choose the chain in Options, or another reference.</div>';
    const core = f.core_rmsd != null && f.core_rmsd < f.rmsd - 0.2 ? ' (' + f.core_rmsd + ' Å without the flexible parts)' : '';
    if (f.rmsd > 3 || f.identity < 0.9)
      return '<div class="msg warn" style="margin-top:8px">Your target structure fits chain ' + esc(f.chain) + ' only loosely: CA RMSD ' + f.rmsd + ' Å over ' + n + ' residues' + core + ', ' + Math.round(100 * f.identity) + '% identical. Check that this is the same protein. ' + (placeVal() === 'interface' ? 'Each design is placed on the reference by a fit around its interface, so the binder stays on the epitope even when other parts differ.' : 'The placement option in Options is not the fit around the interface, so the binder can sit off the epitope where the structures differ.') + '</div>';
    return '<div class="msg good" style="margin-top:8px">Your target structure fits chain ' + esc(f.chain) + ': CA RMSD ' + f.rmsd + ' Å over ' + n + ' residues' + core + '. ' + placeNote() + '</div>';
  }
  async function refreshFit() {
    if (!S.reference || !S.reference.info.ok || !S.target || !S.target.info.ok) return;
    try {
      const i = await BSA.get('check-structure?path=' + encodeURIComponent(S.reference.path) + '&against=' + encodeURIComponent(S.target.path) + '&chain=' + encodeURIComponent($('#rchain').value));
      S.reference.info.fit = i.fit; renderReference(true);
    } catch (e) { /* the preview is optional */ }
  }
  function fillChains() {
    const sel = $('#rchain'), r = S.reference && S.reference.info.ok ? S.reference.info : null;
    const ids = r ? r.chains.map((c) => c.id) : ['A'];
    const want = S.keepChain && ids.includes(S.keepChain) ? S.keepChain : null;
    sel.innerHTML = ids.map((c) => '<option value="' + esc(c) + '">' + esc(c) + '</option>').join('');
    S.refChain = want || (r && r.suggest_chain ? r.suggest_chain : ids[0]); sel.value = S.refChain; S.keepChain = null;
  }
  async function setReference(path, id) {
    const info = await BSA.get('check-structure?path=' + encodeURIComponent(path) + (S.target && S.target.info.ok ? '&against=' + encodeURIComponent(S.target.path) : ''));
    S.reference = { path, info, id: id || null }; renderReference();
  }
  $('#pickt').addEventListener('click', async () => {
    const start = S.runs.length ? dirOf(S.runs[0].path) : '';
    try { const p = await BSA.choose({ kind: 'file', prompt: 'Choose the target construct used for design', start }); if (p) await setTarget(p[0]); } catch (e) { BSA.toast(e.message); }
  });
  $('#pickr').addEventListener('click', async () => {
    const start = S.runs.length ? dirOf(S.runs[0].path) : '';
    try { const p = await BSA.choose({ kind: 'file', prompt: 'Choose the full-length reference structure', start }); if (p) await setReference(p[0]); } catch (e) { BSA.toast(e.message); }
  });
  $('#fetch').addEventListener('click', async () => {
    const id = $('#pdbid').value.trim(); if (!id) { BSA.toast('Type a four-character PDB id first.'); return; }
    const b = $('#fetch'); b.disabled = true; b.textContent = 'Downloading…';
    try { const r = await BSA.post('fetch-reference', { id }); await setReference(r.path, r.id); } catch (e) { BSA.toast(e.message, 6000); }
    b.disabled = false; b.textContent = 'Download';
  });
  $('#placement').addEventListener('change', () => { if (S.reference) renderReference(true); });
  $('#rchain').addEventListener('change', (ev) => { S.refChain = ev.target.value; S.keepChain = ev.target.value; refreshFit(); });
  $('#partners').addEventListener('change', (ev) => { $('#pcustom').hidden = ev.target.value !== 'custom'; });
  $('#pickp').addEventListener('click', async () => {
    try { const p = await BSA.choose({ kind: 'file', prompt: 'Choose a profile file (JSON)' }); if (p) { S.profile = p[0]; $('#pinfo').textContent = baseName(p[0]); } } catch (e) { BSA.toast(e.message); }
  });
  function dropZone(el, handler) {
    ['dragenter', 'dragover'].forEach((t) => el.addEventListener(t, (ev) => { ev.preventDefault(); el.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((t) => el.addEventListener(t, (ev) => { ev.preventDefault(); el.classList.remove('over'); }));
    el.addEventListener('drop', async (ev) => {
      const f = ev.dataTransfer.files && ev.dataTransfer.files[0]; if (!f) return;
      try { const r = await BSA.api('/api/upload?name=' + encodeURIComponent(f.name), { method: 'POST', body: f }); await handler(r.path); } catch (e) { BSA.toast(e.message, 6000); }
    });
  }
  dropZone($('#tdrop'), setTarget); dropZone($('#rdrop'), (p) => setReference(p));

  // ---------------------------------------------------------------- readiness and build
  function missing() {
    const m = [];
    const good = S.runs.filter((r) => r.info);
    if (!S.runs.length) m.push('add a run folder'); else if (S.runs.some((r) => r.loading)) m.push('wait for the runs to be read'); else if (!good.length) m.push('add a run folder Binderscope can read');
    else if (S.runs.some((r) => r.error)) m.push('remove the run that could not be read');
    if (!S.target || !S.target.info.ok) m.push('choose the target construct used for design');
    if (!S.reference || !S.reference.info.ok) m.push('choose the full-length reference');
    const ids = S.runs.map((r) => r.id); if (ids.some((x) => !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,30}$/.test(x))) m.push('give each run a short name (letters, digits, - _ .)'); else if (new Set(ids).size !== ids.length) m.push('give each run its own name');
    return m;
  }
  function check() {
    const m = missing();
    $('#build').disabled = m.length > 0;
    $('#need').textContent = m.length ? 'Still to do: ' + m.join(', ') + '.' : 'Ready. The first build takes a minute or two.';
  }
  $('#name').addEventListener('input', () => { S.nameTouched = true; });
  function inputs() {
    const pc = $('#partners').value;
    const cut = ($('#cutoffs') && $('#cutoffs').value || '').split(/\n/).map((s) => s.trim()).filter(Boolean);
    return { runs: S.runs.filter((r) => r.info).map((r) => ({ id: r.id, path: r.path, settings: r.settings || null })), target_pdb: S.target.path, reference: S.reference.path,
      reference_id: S.reference.id || null, reference_chain: $('#rchain').value || 'A', partner_chains: pc === 'custom' ? ($('#pcustom').value.trim() || 'auto') : pc,
      uniprot: $('#uniprot').value.trim() || null, online: $('#online').checked, profile: S.profile, placement: $('#placement').value,
      cutoffs: cut.length ? cut : null };
  }
  let poll = null, jobId = null, since = 0, shown = [];
  function showProgress(on) { $('#form').hidden = on; $('#progress').hidden = !on; $('#bar').hidden = on; window.scrollTo(0, 0); }
  function drawJob(j) {
    $('#pstages').innerHTML = j.stages.map((s, i) => { const done = j.done.includes(i), now = j.status === 'running' && i === j.stage; return '<li class="' + (done ? 'done' : now ? 'now' : '') + '"><span class="i">' + (done ? '✓' : '') + '</span>' + esc(s) + '</li>'; }).join('');
    $('#pmeter').style.width = Math.round(100 * (j.status === 'done' ? 1 : j.progress)) + '%';
    $('#pelapsed').textContent = BSA.dur(j.elapsed);
    $('#pnote').textContent = j.status === 'running' ? (j.detail || '') : '';
    if (j.log && j.log.length) { const l = $('#plog'); l.textContent += j.log.join('\n') + '\n'; l.scrollTop = l.scrollHeight; since = j.log_from + j.log.length; }
  }
  async function tick() {
    let j;
    try { j = await BSA.get('jobs/' + jobId + '?since=' + since); } catch (e) { $('#pnote').textContent = e.message; poll = setTimeout(tick, 2000); return; }
    drawJob(j);
    if (j.status === 'running') { poll = setTimeout(tick, 700); return; }
    finish(j);
  }
  async function finish(j) {
    const pc = $('#pcancel'); if (pc) pc.hidden = true;
    if (j.status === 'done') {
      $('#ptitle').textContent = 'Your project is ready';
      let found = '';
      try { const p = await BSA.get('projects/' + j.slug); found = (p.highlights || []).map((h) => '<li>' + esc(h.text) + '</li>').join(''); } catch (e) { /* the list is optional */ }
      $('#pstages').hidden = true; $('#pnote').hidden = true;
      $('#pout').innerHTML = '<div class="msg good" style="margin-top:14px">All ' + j.stages.length + ' steps done in ' + BSA.dur(j.elapsed) + '.</div>';
      $('#pacts').innerHTML = '<a class="btn primary big" id="openp" href="/p/' + esc(j.slug) + '/">Open the project</a><a class="btn" href="/">All projects</a>';
      if (found) $('#pout').insertAdjacentHTML('beforeend', '<h2 style="margin-top:20px">What stands out</h2><ul class="found">' + found + '</ul>');
    } else {
      $('#ptitle').textContent = j.status === 'cancelled' ? 'Build cancelled' : 'The build did not finish';
      const hint = j.error_kind === 'inputs' ? '<p class="hint" style="margin:8px 0 0">The files do not fit together. Check that the reference is the full-length version of the same target and that its chain is the right one (Options).</p>' : '';
      $('#pout').innerHTML = '<div class="msg bad" style="margin-top:14px">' + esc(j.error || 'The build stopped.') + hint + '</div>';
      $('#pacts').innerHTML = '<button class="btn primary" id="back" type="button">Back to the inputs</button><a class="btn" href="/">All projects</a>';
      S.retry = j.slug;
    }
  }
  function backToInputs() {
    showProgress(false);
    $('#pacts').innerHTML = '<button class="btn" id="pcancel" type="button">Cancel build</button>';
    $('#plog').textContent = ''; $('#pout').innerHTML = ''; $('#pstages').hidden = false; $('#pnote').hidden = false;
    $('#ptitle').textContent = 'Building…';
    check();
  }
  $('#pacts').addEventListener('click', (ev) => { if (ev.target.id === 'back') backToInputs(); });
  $('#pacts').addEventListener('click', (ev) => { if (ev.target.id === 'pcancel' && jobId) BSA.post('jobs/' + jobId + '/cancel'); });
  $('#build').addEventListener('click', async () => {
    const b = $('#build'); b.disabled = true;
    const name = $('#name').value.trim() || suggestName() || 'My project';
    try {
      const r = await BSA.post('projects', { name, inputs: inputs(), retry: S.retry });
      if (r.inputs) {                                              // dropped or downloaded files now live in the project: keep their new paths for a retry
        if (S.target && r.inputs.target_pdb) S.target.path = r.inputs.target_pdb;
        if (S.reference && r.inputs.reference) S.reference.path = r.inputs.reference;
        if (S.profile && r.inputs.profile) S.profile = r.inputs.profile;
      }
      jobId = r.job.id; since = 0; $('#plog').textContent = ''; $('#ptitle').textContent = 'Building ' + name + '…'; showProgress(true); drawJob(r.job); tick();
    } catch (e) { BSA.toast(e.message, 7000); b.disabled = false; check(); }
  });
  // ---- open the page with the inputs of an existing project (Edit the inputs): a failed build is retried in the same folder, otherwise a copy is made
  async function prefill(slug) {
    try {
      const p = await BSA.get('projects/' + slug), inp = p.inputs || {}, retry = p.status === 'failed' || p.status === 'cancelled';
      $('#name').value = retry ? p.name : p.name + ' 2'; S.nameTouched = true;
      for (const r of inp.runs || []) { const n = S.runs.length; await addRuns([r.path]); const run = S.runs[n]; if (run && r.id) { run.id = r.id; if (r.settings) run.settings = r.settings; } }
      renderRuns();
      if (inp.target_pdb) await setTarget(inp.target_pdb);
      if (inp.reference) await setReference(inp.reference, inp.reference_id);
      if (inp.reference_chain) { $('#rchain').value = inp.reference_chain; S.refChain = inp.reference_chain; S.keepChain = inp.reference_chain; await refreshFit(); }
      const pc = inp.partner_chains || 'auto';
      if (pc === 'auto' || pc === 'none') $('#partners').value = pc; else { $('#partners').value = 'custom'; $('#pcustom').hidden = false; $('#pcustom').value = pc; }
      $('#uniprot').value = inp.uniprot || ''; $('#online').checked = !!inp.online; if (inp.placement) $('#placement').value = inp.placement;
      if (inp.profile) { S.profile = inp.profile; $('#pinfo').textContent = baseName(inp.profile); }
      if ($('#cutoffs') && inp.cutoffs && inp.cutoffs.length) $('#cutoffs').value = inp.cutoffs.join('\n');
      $('#opt').open = !!(inp.uniprot || inp.profile || pc !== 'auto' || (inp.placement && inp.placement !== 'interface') || (inp.cutoffs && inp.cutoffs.length));
      S.retry = retry ? slug : null;
      if (S.reference) { S.keepChain = inp.reference_chain || $('#rchain').value; renderReference(true); }     // the note under the reference names the stored chain and placement
      check();
    } catch (e) { BSA.toast(e.message, 6000); }
  }
  renderRuns(); renderTarget(); renderReference();
  const fromSlug = new URLSearchParams(location.search).get('from');
  if (fromSlug) prefill(fromSlug);
  window.BSNEW = { S, addRuns, setTarget, setReference };      // used by the browser tests
})();
