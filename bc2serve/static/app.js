/* BindCraft2 web UI.
   No framework and no build step: the machine this runs on cannot reach npm,
   and a tool a lab depends on should still start in five years. */

'use strict';

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, kids = []) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const kid of [].concat(kids)) node.append(kid);
  return node;
};

/** A target's chain selection is a spec like "A" or "A,B": shipped presets name
    assemblies, and BindCraft takes either. */
const chainList = (target) => (target.chain || '').split(',').map(c => c.trim()).filter(Boolean);

const state = {
  presets: null,
  targets: [],          // {name, target_path, chains[], chain, hotspots:Set, coldspots:Set, objective}
  active: 0,
  mode: 'hotspots',
  look: { sidechains: false, surface: false, exposure: false },
  format: 'binder',
  viewer: null,
  designViewer: null,
  jobs: [],
  results: [],
  sort: { key: null, dir: 1 },
  review: {
    list: null,         // rows from /api/review, or null until asked
    projects: null,     // rows from /api/review/projects, or null until asked or when that call failed
    projectsProblem: '',// why projects could not be listed, when it is more than "this server has none"
    unavailable: '',    // why the Review API cannot be used, when it cannot
    chosen: null,       // campaign name, or a project's "@slug", the panel is about
    detail: null,       // /api/review/<campaign>, or /api/review/project/<slug>
    formOpen: false,    // setup form shown even though a review exists
    hasAF: null,        // does this server offer /api/fetch-af?
    mode: 'reference',  // 'reference' | 'none': whether the build gets a full-length structure at all
    check: null,        // how far the chosen reference can be trusted (PAE), for the form
    checkKey: '',       // what that answer was for, so it is not asked for twice
    comp: null,         // what the chosen reference holds: its chains, and what the build will do with each
    compKey: '',        // what that answer was for
    compPath: '',       // the file it was about
    chain: '',          // the target chain asked for by hand; '' lets the construct's sequence decide
    partners: 'auto',   // 'auto', 'none', 'all' or chain names: what the binder is checked against besides the target
    assembly: '',       // the assembly the subunits come from; '' = the first one that holds the target
    syncing: false,
  },
};

// ───────────────────────────────────────────────────────────── plumbing

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('json') ? response.json() : response.text();
}

let toastTimer;
function toast(message, bad = false) {
  const box = $('toast');
  box.textContent = message;
  box.classList.toggle('bad', bad);
  box.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add('hidden'), bad ? 7000 : 3500);
}

function note(node, message, kind) {
  node.textContent = message || '';
  node.className = 'note' + (kind ? ' ' + kind : '') + (message ? '' : ' hidden');
}

// ───────────────────────────────────────────────────────── residue spans

/** Residue selections, kept chain-qualified.

    A multi-chain target numbers each chain separately, and the shipped IL-2
    receptor preset says "A67,...,B229-231" for exactly that reason. Holding bare
    numbers would merge the chains and silently retarget the campaign, so every
    residue is stored as "<chain><number>" and the chain is only dropped on the
    way out when the target has a single chain. */
function spanToSet(text, defaultChain) {
  const picked = new Set();
  for (const piece of (text || '').split(',')) {
    const bit = piece.trim();
    if (!bit) continue;
    const range = bit.match(/^([A-Za-z]?)(\d+)-([A-Za-z]?)(\d+)$/);
    const one = bit.match(/^([A-Za-z]?)(\d+)$/);
    if (range) {
      const chain = range[1] || defaultChain || '';
      const from = +range[2], to = +range[4];
      for (let n = Math.min(from, to); n <= Math.max(from, to); n++) picked.add(chain + n);
    } else if (one) {
      picked.add((one[1] || defaultChain || '') + one[2]);
    }
  }
  return picked;
}

/** Collapse runs into ranges, per chain, so 66,67,68,69,70 reads as 66-70. */
function setToSpan(picked, showChain) {
  const byChain = new Map();
  for (const key of picked) {
    const match = String(key).match(/^([A-Za-z]*)(\d+)$/);
    if (!match) continue;
    const chain = match[1] || '';
    if (!byChain.has(chain)) byChain.set(chain, []);
    byChain.get(chain).push(+match[2]);
  }
  const parts = [];
  for (const chain of [...byChain.keys()].sort()) {
    const numbers = byChain.get(chain).sort((a, b) => a - b);
    const tag = showChain ? chain : '';
    let run = null;
    const runs = [];
    for (const n of numbers) {
      if (run && n === run.to + 1) { run.to = n; continue; }
      if (run) runs.push(run);
      run = { from: n, to: n };
    }
    if (run) runs.push(run);
    for (const r of runs) {
      if (r.to - r.from >= 2) parts.push(`${tag}${r.from}-${tag ? '' : ''}${r.to}`);
      else for (let n = r.from; n <= r.to; n++) parts.push(`${tag}${n}`);
    }
  }
  return parts.join(',');
}

/** Split a selection into the chains it names, for a viewer selection per chain. */
function groupByChain(target, picked) {
  const grouped = new Map();
  for (const key of picked) {
    const match = String(key).match(/^([A-Za-z]*)(\d+)$/);
    if (!match) continue;
    const chain = match[1] || '';
    if (!grouped.has(chain)) grouped.set(chain, []);
    grouped.get(chain).push(+match[2]);
  }
  return grouped;
}

/** Does this target span more than one chain, so selections need naming? */
const multiChain = (target) => chainList(target).length > 1;

/** The key a residue is stored under. */
const residueKey = (target, chain, number) =>
  (multiChain(target) ? (chain || '') : '') + number;

// ───────────────────────────────────────────────────────────── targets

function current() { return state.targets[state.active] || null; }

function addTarget(info) {
  const chains = info.chains || [];
  const chainSpec = info.chain || (chains[0] ? chains[0].chain : 'A');
  const several = String(chainSpec).split(',').filter(Boolean).length > 1;
  const first = String(chainSpec).split(',')[0] || '';
  state.targets.push({
    name: info.name,
    target_path: info.path || info.target_path,
    chains,
    chain: chainSpec,
    hotspots: spanToSet(info.hotspots || '', several ? first : ''),
    coldspots: spanToSet(info.coldspots || '', several ? first : ''),
    objective: 'bind',
  });
  state.active = state.targets.length - 1;
  if (!$('name').value.trim()) $('name').value = info.name.toLowerCase();
  drawTargets();
  loadViewer().then(loadAnalysis);
}

function drawTargets() {
  const box = $('target-list');
  box.replaceChildren();
  state.targets.forEach((target, index) => {
    const row = el('div', { className: 'target' + (index === state.active ? ' is-on' : '') });
    row.append(el('b', { textContent: target.name }));

    if (target.chains.length > 1) {
      const pick = el('select', { title: 'Chains to design against' });
      target.chains.forEach(c =>
        pick.append(el('option', { value: c.chain, textContent: `chain ${c.chain}`,
                                   selected: c.chain === target.chain })));
      pick.onchange = () => { target.chain = pick.value; if (index === state.active) loadViewer(); };
      row.append(pick);
    } else if (target.chains.length === 1) {
      row.append(el('span', { className: 'path', textContent: `chain ${target.chains[0].chain}` }));
    }

    const role = el('select', { title: 'What this target is for' });
    role.append(el('option', { value: 'bind', textContent: 'bind it' }));
    role.append(el('option', { value: 'detarget', textContent: 'avoid it' }));
    role.value = target.objective;
    role.onchange = () => { target.objective = role.value; };
    row.append(role);

    row.append(el('span', { className: 'spacer' }));
    const spots = target.hotspots.size;
    row.append(el('span', { className: 'path',
      textContent: spots ? `${spots} residue${spots > 1 ? 's' : ''} marked` : 'no site chosen' }));

    const remove = el('button', { className: 'x', textContent: '×', title: 'Remove' });
    remove.onclick = (event) => {
      event.stopPropagation();
      state.targets.splice(index, 1);
      state.active = Math.max(0, Math.min(state.active, state.targets.length - 1));
      drawTargets(); loadViewer();
    };
    row.append(remove);

    row.onclick = () => { state.active = index; drawTargets(); loadViewer(); };
    box.append(row);
  });

  // Step 2 stays on the page with no target chosen, in a waiting state. Hiding it
  // left the numbering reading 1, 3, 4, which tells a first-time reader they have
  // missed something; showing it tells them what is coming and what unlocks it.
  const site = $('step-site');
  site.classList.toggle('is-waiting', state.targets.length === 0);
  syncSpotFields();
}

function syncSpotFields() {
  const target = current();
  const named = target ? multiChain(target) : false;
  $('hotspots').value = target ? setToSpan(target.hotspots, named) : '';
  $('coldspots').value = target ? setToSpan(target.coldspots, named) : '';
  const pick = $('chain-pick');
  pick.replaceChildren();
  if (target) {
    target.chains.forEach(c =>
      pick.append(el('option', { value: c.chain, textContent: `chain ${c.chain}`,
                                 selected: c.chain === target.chain })));
    pick.classList.toggle('hidden', target.chains.length < 2);
  }
  const count = target ? target.hotspots.size + target.coldspots.size : 0;
  $('pick-status').textContent = count
    ? `${count} residue${count > 1 ? 's' : ''} marked`
    : 'Click a residue to mark it';
}

// ──────────────────────────────────────────────────────── sequence track

/** Per-residue facts for the active target: sequence, numbering, exposure. */
async function loadAnalysis() {
  const target = current();
  if (!target) return;
  const key = target.target_path + '|' + target.chain;
  if (target._analysisKey === key) return;
  $('sequence').innerHTML = '<span class="help">reading the structure…</span>';
  try {
    const answer = await api(`/api/analysis?path=${encodeURIComponent(target.target_path)}`
      + `&chains=${encodeURIComponent(target.chain || '')}`);
    target.residues = answer.residues || [];
    target.chainFacts = answer.chains || [];
    target._analysisKey = key;
  } catch (problem) {
    $('sequence').innerHTML = '';
    toast('Could not read the sequence: ' + problem.message, true);
    return;
  }
  drawSequence();
  checkEpitope();
  refreshEstimate();
}

let dragging = null;

function drawSequence() {
  const target = current();
  const box = $('sequence');
  box.replaceChildren();
  if (!target || !target.residues || !target.residues.length) return;

  // Blocks of ten with the first number above, which is how a numbered sequence
  // is read on paper and the quickest way to confirm the numbering is what you
  // think it is.
  let block = null;
  target.residues.forEach((residue, index) => {
    if (index % 10 === 0) {
      block = el('div', { className: 'blk' });
      block.append(el('div', { className: 'blk-num',
        textContent: (multiChain(target) ? residue.chain : '') + residue.number }));
      block.rowNode = el('div', { className: 'blk-row' });
      block.append(block.rowNode);
      box.append(block);
    }
    const key = residueKey(target, residue.chain, residue.number);
    const marked = target.hotspots.has(key) ? ' hot'
      : target.coldspots.has(key) ? ' cold' : '';
    const cell = el('div', {
      className: 'res' + marked + (residue.exposed ? '' : ' buried')
        + (residue.suggested && !marked ? ' suggested' : ''),
      textContent: residue.letter,
      title: `${residue.name}${residue.number}`
        + (multiChain(target) ? ` (chain ${residue.chain})` : '')
        + ` · ${Math.round(residue.rel_sasa * 100)}% exposed`
        + (residue.suggested ? ' · often a good contact' : '')
        + (residue.exposed ? '' : ' · buried'),
    });
    cell.onmousedown = (event) => {
      event.preventDefault();
      dragging = { from: residue.number, chain: residue.chain, mode: state.mode };
      applyRange(residue, residue);
    };
    cell.onmouseenter = () => {
      // Dragging stays inside one chain: a range across a chain break is not a thing.
      if (dragging && dragging.chain === residue.chain) {
        applyRange({ number: dragging.from, chain: dragging.chain }, residue);
      }
    };
    block.rowNode.append(cell);
  });
}

/** Mark every residue between two numbers. Dragging is how a stretch gets chosen. */
function applyRange(from, to) {
  const target = current();
  if (!target) return;
  const low = Math.min(from.number, to.number), high = Math.max(from.number, to.number);
  const set = target[state.mode];
  const other = target[state.mode === 'hotspots' ? 'coldspots' : 'hotspots'];
  for (const residue of target.residues) {
    if (residue.chain !== from.chain) continue;
    if (residue.number < low || residue.number > high) continue;
    const key = residueKey(target, residue.chain, residue.number);
    set.add(key);
    other.delete(key);
  }
  afterPickChange();
}

function afterPickChange() {
  drawSequence();
  paintTarget();
  state.viewer && state.viewer.render();
  syncSpotFields();
  drawTargets();
  checkEpitope();
}

/** The offered folders, plus any the user has typed before. */
function buildRootChoices() {
  const list = $('root-choices');
  list.replaceChildren();
  const offered = state.presets.result_roots || [state.presets.results_root];
  const seen = new Set();
  for (const root of [...offered, ...rememberedRoots()]) {
    if (!root || seen.has(root)) continue;
    seen.add(root);
    list.append(el('option', { value: root }));
  }
}

function rememberedRoots() {
  try { return JSON.parse(localStorage.getItem('bc2_roots') || '[]'); }
  catch { return []; }
}

function rememberRoot(root) {
  if (!root) return;
  localStorage.setItem('bc2_root', root);
  const offered = new Set(state.presets.result_roots || []);
  if (offered.has(root)) return;      // no need to remember what is already offered
  const kept = [root, ...rememberedRoots().filter(r => r !== root)].slice(0, 6);
  localStorage.setItem('bc2_roots', JSON.stringify(kept));
  buildRootChoices();
}

function describeRoot() {
  const root = ($('results-root').value || '').trim();
  const offered = state.presets.result_roots || [];
  let text;
  if (!root) {
    text = 'Give a full path, or pick one of the suggestions.';
  } else if (!root.startsWith('/')) {
    text = 'Needs to be a full path, starting with /.';
  } else if (root.startsWith('/ssd') || root.startsWith('/data')) {
    text = 'Large scratch disk — the right home for big campaigns.';
  } else if (offered.includes(root)) {
    text = 'Under your home directory, which has less room than /ssd or /data.';
  } else {
    text = 'Your own path. It is made if it does not exist yet.';
  }
  $('root-note').textContent = text;
}

// ───────────────────────────────────────────────── advice and estimates

let checkTimer;
function checkEpitope() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    const target = current();
    const box = $('epitope-notes');
    box.replaceChildren();
    if (!target || !target.hotspots.size) return;
    try {
      const answer = await api('/api/epitope-check', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          path: target.target_path, chains: target.chain,
          hotspots: [...target.hotspots],
        }),
      });
      for (const note of answer.notes || []) {
        box.append(el('p', { className: 'note ' + note.level, textContent: note.text }));
      }
    } catch { /* advice is optional; never block on it */ }
  }, 400);
}

/** Which card the estimate assumed. "2 designs at a time" means nothing without
    it: the same campaign fits twice on an A100 and not at all on a 2080 Ti. */
function cardBasis(answer) {
  const gb = Math.round(answer.card_gb);
  return {
    local: `this machine's ${gb} GB card`,
    'largest free on the cluster': `a ${gb} GB card, the largest free on the cluster`,
    'largest on the cluster, none free now':
      `a ${gb} GB card — the largest on the cluster, though none is free now`,
    chosen: `a ${gb} GB card`,
    assumed: `an assumed ${gb} GB card`,
  }[answer.card_basis] || `a ${gb} GB card`;
}

let estimateTimer;
function refreshEstimate() {
  clearTimeout(estimateTimer);
  estimateTimer = setTimeout(async () => {
    const target = current();
    const box = $('estimate');
    const format = state.presets.formats.find(f => f.name === state.format);
    if (!target || !target.residues || !format) return note(box, '', '');
    const targetSize = target.residues.length;
    const low = format.lengths ? +$('len-min').value : 130;
    const high = format.lengths ? +$('len-max').value : 130;
    try {
      const answer = await api('/api/estimate', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ target_residues: targetSize, length_min: low, length_max: high }),
      });
      lastEstimate = answer;
      redrawCluster();
      const head = `${targetSize} target + up to ${Math.max(low, high)} binder residues, `
        + `padded to ${answer.padded}. About ${answer.per_worker_gb} GB per design`
        + (answer.card_gb ? ` on ${cardBasis(answer)}.` : '.');
      if (!answer.fits) {
        note(box, head + ' That does not fit on one card — trim the target or shorten the binder.', 'bad');
      } else if (answer.workers_per_card <= 2) {
        note(box, head + ` Only ${answer.workers_per_card} design`
          + `${answer.workers_per_card === 1 ? '' : 's'} at a time per GPU; `
          + 'trimming the target would speed this up a lot.', 'warn');
      } else {
        note(box, head + ` ${answer.workers_per_card} designs at a time per GPU.`, '');
      }
    } catch { note(box, '', ''); }
  }, 350);
}

// ───────────────────────────────────────────────────────────── 3D viewer

async function loadViewer() {
  const target = current();
  const host = $('viewer');
  if (!target) { host.replaceChildren(); state.viewer = null; return; }

  if (!state.viewer) {
    state.viewer = $3Dmol.createViewer(host, { backgroundColor: canvasColour() });
  }
  const viewer = state.viewer;
  viewer.setBackgroundColor(canvasColour());
  viewer.clear();

  let text;
  try {
    text = await api('/api/structure?path=' + encodeURIComponent(target.target_path));
  } catch (problem) {
    toast('Could not load the structure: ' + problem.message, true);
    return;
  }
  const format = /\.(cif|mmcif)$/i.test(target.target_path) ? 'cif' : 'pdb';
  viewer.addModel(text, format);

  // Clicking an atom marks the residue it belongs to. Whole residues, not atoms:
  // a hotspot is a residue number, and clicking one atom of a side chain should
  // not mean something different from clicking another.
  // This has to come before render(): the click targets are built as the scene is
  // drawn, so marking atoms clickable afterwards does nothing until the next draw.
  viewer.setClickable({}, true, (atom) => {
    if (!atom) return;
    const target = current();
    if (!target) return;
    const designing = chainList(target);
    if (designing.length && atom.chain && !designing.includes(atom.chain)) {
      toast(`That residue is on chain ${atom.chain}; you are designing against ${target.chain}.`);
      return;
    }
    const set = target[state.mode];
    const other = target[state.mode === 'hotspots' ? 'coldspots' : 'hotspots'];
    const key = residueKey(target, atom.chain, atom.resi);
    if (set.has(key)) set.delete(key);
    else { set.add(key); other.delete(key); }
    afterPickChange();
  });

  // Naming the residue under the pointer is what makes clicking confident: you
  // can see it is Tyr56 before you mark it, rather than finding out afterwards.
  viewer.setHoverable({}, true,
    (atom, view) => {
      if (atom.hoverLabel) return;
      atom.hoverLabel = view.addLabel(`${atom.resn || ''}${atom.resi}`, {
        position: atom, backgroundColor: '#16181d', backgroundOpacity: 0.92,
        fontColor: '#ffffff', fontSize: 12, borderThickness: 0, inFront: true,
      });
      view.render();
    },
    (atom, view) => {
      if (!atom.hoverLabel) return;
      view.removeLabel(atom.hoverLabel);
      delete atom.hoverLabel;
      view.render();
    });

  paintTarget();
  viewer.zoomTo();
  viewer.render();
}

/** 3Dmol draws onto its own canvas, so it is told the theme rather than inheriting it. */
function canvasColour() {
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  return dark ? '#14161a' : '#ffffff';
}

function paintTarget() {
  const viewer = state.viewer;
  const target = current();
  if (!viewer || !target) return;
  const designing = chainList(target);

  if (state.look.exposure && target.residues && target.residues.length) {
    // Exposure says what a binder can actually reach, which identity alone does not.
    viewer.setStyle({}, { cartoon: { color: '#2b3038' } });
    const buckets = { buried: [], middling: [], open: [] };
    for (const residue of target.residues) {
      const where = residue.rel_sasa >= 0.45 ? 'open'
        : residue.rel_sasa >= 0.25 ? 'middling' : 'buried';
      buckets[where].push(residue.number);
    }
    const shades = { buried: '#3b4250', middling: '#8aa0c8', open: '#ffd166' };
    for (const [where, numbers] of Object.entries(buckets)) {
      if (!numbers.length) continue;
      const selection = { resi: numbers };
      if (designing.length) selection.chain = designing;
      viewer.setStyle(selection, { cartoon: { color: shades[where] } });
    }
  } else {
    viewer.setStyle({}, { cartoon: { color: '#d3d8e0' } });
    if (designing.length) {
      viewer.setStyle({ chain: designing }, { cartoon: { color: '#8a94a6' } });
    }
  }

  // A cartoon on its own is not pickable: 3Dmol builds its click targets out of
  // atom-level geometry. A small sphere on each alpha carbon gives every residue
  // something to hit, and doubles as the cue for what can be clicked.
  const backbone = designing.length ? { chain: designing, atom: 'CA' } : { atom: 'CA' };
  viewer.addStyle(backbone, { sphere: { radius: 0.62, color: '#6b7484', opacity: 0.9 } });

  const marks = [['hotspots', '#e8590c'], ['coldspots', '#0b7285']];
  for (const [kind, colour] of marks) {
    const grouped = groupByChain(target, target[kind]);
    for (const [chain, residues] of grouped) {
    if (!residues.length) continue;
    const selection = { resi: residues };
    if (chain) selection.chain = [chain];
    else if (designing.length) selection.chain = designing;
    viewer.setStyle(selection, { cartoon: { color: colour } });
    viewer.addStyle({ ...selection, atom: 'CA' }, { sphere: { radius: 0.85, color: colour } });
    // Side chains are what you actually judge a contact on, so marked residues show
    // theirs by default and the toggle extends it to the whole chain.
    viewer.addStyle(selection, { stick: { color: colour, radius: 0.25 } });
    }
  }

  if (state.look.sidechains) {
    const sel = designing.length ? { chain: designing } : {};
    viewer.addStyle({ ...sel, sidechain: true },
      { stick: { radius: 0.13, colorscheme: 'default' } });
  }

  if (state.look.surface) {
    viewer.removeAllSurfaces();
    const sel = designing.length ? { chain: designing } : {};
    // An epitope is a patch on a surface; a cartoon hides whether it is one.
    viewer.addSurface($3Dmol.SurfaceType.VDW, { opacity: 0.72, color: '#9aa4b4' }, sel);
  } else {
    viewer.removeAllSurfaces();
  }
}

/** Write a trimmed copy and switch the campaign onto it. */
async function doTrim() {
  const target = current();
  if (!target) return;
  const numbers = [...spanToSet($('trim-keep').value)];
  const keep = numbers.length
    ? numbers
    : target.residues.map(r => r.number);
  if (!keep.length) return toast('Nothing to keep', true);

  const button = $('trim-go');
  button.disabled = true;
  try {
    const answer = await api('/api/trim', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        path: target.target_path, chains: target.chain, keep, name: target.name,
      }),
    });
    const before = target.residues.length;
    target.target_path = answer.path;
    target.chain = answer.chains[0] || target.chain;
    target.chains = [];
    target._analysisKey = null;
    // Hotspots outside the kept range are gone from the structure, so drop them
    // rather than leave numbers pointing at residues that are no longer there.
    const kept = new Set(keep);
    for (const field of ['hotspots', 'coldspots']) {
      for (const key of [...target[field]]) {
        const number = +String(key).replace(/^[A-Za-z]*/, '');
        if (!kept.has(number)) target[field].delete(key);
      }
    }
    $('trim-state').textContent = `— ${before} → ${answer.residues} residues`;
    drawTargets();
    await loadViewer();
    await loadAnalysis();
    toast(`Trimmed to ${answer.residues} residues (${answer.first}-${answer.last})`);
  } catch (problem) {
    toast(problem.message, true);
  } finally {
    button.disabled = false;
  }
}

// ───────────────────────────────────────────────────────── form building

function buildFormats() {
  const box = $('formats');
  box.replaceChildren();
  for (const format of state.presets.formats) {
    const card = el('button', { className: 'card' + (format.name === state.format ? ' is-on' : ''),
                                type: 'button' });
    card.append(el('b', { textContent: format.label }));
    card.append(el('span', { textContent: format.blurb }));
    card.onclick = () => { state.format = format.name; buildFormats(); applyFormat(); refreshEstimate(); };
    box.append(card);
  }
}

function applyFormat() {
  const format = state.presets.formats.find(f => f.name === state.format);
  const scaffolded = !format.lengths;
  $('length-box').classList.toggle('hidden', scaffolded);
  $('scaffold-note').classList.toggle('hidden', !scaffolded);
  $('copies-box').classList.toggle('hidden', state.format !== 'homo_oligomer');
  $('domains-box').classList.toggle('hidden', state.format !== 'multidomain');
  if (format.lengths) {
    $('len-min').value = format.lengths[0];
    $('len-max').value = format.lengths[1];
  }
}

function buildProperties() {
  const box = $('properties');
  box.replaceChildren();
  for (const property of state.presets.properties) {
    const label = el('label', { className: 'check' });
    label.append(el('input', { type: 'checkbox', value: property.name, className: 'prop' }));
    const text = el('div');
    text.append(el('b', { textContent: property.label }));
    text.append(el('span', { textContent: property.blurb }));
    label.append(text);
    box.append(label);
  }
  const pick = $('conformational');
  for (const option of state.presets.conformational) {
    pick.append(el('option', { value: option.name, textContent: `${option.label} — ${option.blurb}` }));
  }
}

function buildShipped() {
  const pick = $('shipped-target');
  pick.replaceChildren();
  for (const target of state.presets.targets) {
    pick.append(el('option', { value: target.name, textContent: target.name }));
  }
  const blurb = () => {
    const found = state.presets.targets.find(t => t.name === pick.value);
    $('shipped-blurb').textContent = found ? found.description : '';
  };
  pick.onchange = blurb;
  blurb();
}

// ───────────────────────────────────────────────────────────── GPUs

async function refreshGpus() {
  let cards = [];
  try { cards = (await api('/api/gpus')).cards; } catch { /* shown as none */ }

  const strip = $('gpu-strip');
  strip.replaceChildren();
  for (const card of cards) {
    let kind = 'free', label = 'free';
    if (card.reserved) { kind = 'reserved'; label = 'reserved'; }
    else if (card.held_by_job) { kind = 'ours'; label = `job ${card.held_by_job}`; }
    else if (card.busy_elsewhere) { kind = 'other'; label = 'in use elsewhere'; }
    const box = el('div', { className: 'gpu ' + kind });

    const head = el('div', { className: 'gpu-head' });
    head.append(el('b', { textContent: 'GPU ' + card.index }));
    head.append(el('span', { className: 'state ' + kind, textContent: label }));
    box.append(head);

    // Memory is what the card reports, not what this queue allocated: a card
    // can read "in use elsewhere" and still be nearly empty, or sit at "free"
    // with another user's process winding down on it. The bar is how full the
    // card is; the line above it is who has it. Both are worth knowing.
    const total = card.memory_total_mib || 0;
    const used = card.memory_used_mib || 0;
    const part = total > 0 ? Math.min(1, used / total) : 0;
    const level = part >= 0.85 ? 'high' : part >= 0.5 ? 'mid' : 'low';
    const percent = Math.round(part * 100);

    // aria-hidden because the GB line below says the same thing in words; a
    // screen reader announcing both would read the card's memory twice.
    const track = el('div', { className: 'mem' });
    track.setAttribute('aria-hidden', 'true');
    track.title = `${percent}% of memory in use`;
    track.append(el('i', { className: 'lvl-' + level, style: `width:${part * 100}%` }));
    box.append(track);

    const gb = (mib) => (mib / 1024).toFixed(1);
    const line = el('div', { className: 'mem-num' });
    line.append(el('b', { textContent: gb(used) }));
    line.append(el('span', { textContent: ` / ${gb(total)} GB` }));
    box.append(line);

    strip.append(box);
  }

  const usable = cards.filter(c => !c.reserved).length || 1;
  const pick = $('gpus');
  if (pick.options.length !== usable) {
    pick.replaceChildren();
    for (let n = 1; n <= usable; n++) {
      pick.append(el('option', { value: String(n), textContent: n === 1 ? '1 GPU' : `${n} GPUs` }));
    }
    pick.value = String(Math.min(2, usable));
  }
  const free = cards.filter(c => !c.reserved && !c.held_by_job && !c.busy_elsewhere).length;
  $('gpu-note').textContent = `${free} of ${usable} free now. More GPUs is faster; it waits if none are free.`;
}

/** What the cluster can give a campaign right now.

    Three states, and they must not look alike: a server that cannot see a
    cluster is the normal case here and shows nothing at all, a scheduler that
    did not answer is a fault worth reading, and real numbers are the point. */
let lastEstimate = null, lastCluster = null;

/** Re-paint the table when the estimate changes, so the fit marks follow the
    form rather than whichever arrived last. */
function redrawCluster() {
  if (lastCluster && !$('cluster-box').classList.contains('hidden')) drawCluster(lastCluster);
}

async function refreshCluster(fresh) {
  const box = $('cluster-box');
  let data;
  try {
    data = await api('/api/cluster' + (fresh ? '?fresh=1' : ''));
  } catch (problem) {
    box.classList.remove('hidden');
    drawCluster({ backend: 'sge', gpus: [], error: problem.message });
    return;
  }
  // No cluster behind this server is not a failure, so it gets no box at all
  // rather than an empty one that reads as "the cluster has nothing free".
  const absent = data.backend !== 'sge' && !data.error;
  box.classList.toggle('hidden', absent);
  lastCluster = data;
  if (!absent) drawCluster(data);
}

function drawCluster(data) {
  const rows = $('cluster-rows'), lead = $('cluster-lead');
  const when = $('cluster-when'), wrap = $('cluster-wrap'), help = $('cluster-help');

  when.textContent = data.read_at
    ? 'as of ' + new Date(data.read_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      + (data.cached ? '' : ' · just now')
    : '';

  if (data.error) {
    note($('cluster-note'), 'Could not read the queue: ' + data.error, 'bad');
  } else if (data.note) {
    note($('cluster-note'), data.note, 'info');
  } else {
    note($('cluster-note'), '');
  }

  const gpus = (data.gpus || []).slice()
    .sort((a, b) => (b.free - a.free) || (b.evictable - a.evictable) || a.model.localeCompare(b.model));
  const nothing = !gpus.length;
  wrap.classList.toggle('hidden', nothing);
  help.classList.toggle('hidden', nothing);
  rows.replaceChildren();

  if (nothing) {
    lead.textContent = data.error ? '' : 'No GPU queues are visible to you on this cluster.';
    return;
  }

  // A card holds less than it advertises, and the server already knows by how
  // much: the gap between the card it costed and the memory it called usable.
  // Taking the overhead from its own answer beats subtracting a number of mine,
  // which would drift the moment the server changed its mind.
  const need = lastEstimate && lastEstimate.per_worker_gb;
  const overhead = lastEstimate && lastEstimate.card_gb != null && lastEstimate.usable_gb != null
    ? Math.max(0, lastEstimate.card_gb - lastEstimate.usable_gb) : 0;
  const holds = (memGb) => !need || (memGb - overhead) >= need;

  // Count only models with a free card that is actually big enough. "Free" and
  // "big enough" are different questions and the second is the one that decides
  // whether a campaign runs.
  const freeAndBig = gpus.filter(g => g.free > 0 && holds(g.free_mem_gb)).length;
  const evictAndBig = gpus.filter(g => g.evictable > 0 && holds(g.evictable_mem_gb)).length;
  const anyCardBig = gpus.some(g => holds(g.card_gb));
  const withFree = gpus.filter(g => g.free > 0).length;
  const needs = need ? ` This campaign needs ${need} GB per design.` : '';

  lead.textContent = !need
    ? (withFree === gpus.length ? 'Cards are free on every model.'
      : withFree ? `Cards are free on ${withFree} of ${gpus.length} models.`
      : 'No cards are free, so a campaign will wait in the queue.')
    : !anyCardBig
      ? `No card on this cluster is big enough.${needs}`
      : freeAndBig === gpus.length
        ? `Every model has a free card big enough.${needs}`
        : freeAndBig
          ? `${freeAndBig} of ${gpus.length} models have a free card big enough.${needs}`
          : evictAndBig
            ? `Nothing free is big enough. ${evictAndBig} model${evictAndBig === 1 ? '' : 's'} would fit if you accept being stopped mid-run.${needs}`
            : `No free card is big enough.${needs}`;

  drawDestination(gpus, data.queues || [], need, holds);

  for (const g of gpus) {
    // Read the count before the memory: a model with nothing free reports no
    // memory either, and "0 GB" would read as a tiny card rather than no card.
    const reachable = g.free > 0 ? g.free_mem_gb : g.evictable > 0 ? g.evictable_mem_gb : null;
    const neverFits = need && g.card_gb != null && !holds(g.card_gb);
    const notNowFits = need && !neverFits && reachable != null && !holds(reachable);

    const row = el('tr', { className: neverFits ? 'is-small' : '' });
    const name = el('td', { className: 'model' });
    name.append(el('span', { className: 'model-name', textContent: g.model }));
    if (g.card_gb != null) {
      name.append(el('span', { className: 'model-mem',
        textContent: `${Math.round(g.card_gb)} GB card`
          + (neverFits ? ' · too small for this campaign'
            : notNowFits ? ' · not enough free right now' : '') }));
    }
    row.append(name);
    row.append(el('td', { className: 'num-cell free' + (g.free ? '' : ' is-zero'),
                          textContent: String(g.free) }));
    row.append(el('td', { className: 'num-cell evict',
                          textContent: g.evictable ? String(g.evictable) : 'none' }));
    row.append(el('td', { className: 'num-cell total', textContent: String(g.total) }));
    rows.append(row);
  }
}

// ─────────────────────────────────────────────────────── notifications

let ntfyServer = 'https://ntfy.sh';

function setUpNotifications() {
  const field = $('notify-topic');
  field.value = localStorage.getItem('bc2_topic') || '';
  field.oninput = () => { localStorage.setItem('bc2_topic', field.value.trim()); describe(); };

  function describe() {
    const topic = field.value.trim();
    $('notify-state').textContent = topic ? '— on' : '— off';
    $('notify-how').innerHTML = topic
      ? `To receive these: install the <b>ntfy</b> app on your phone (App Store or Play Store),
         or open <a href="${ntfyServer}/${encodeURIComponent(topic)}" target="_blank"
         rel="noopener noreferrer">${ntfyServer}/${topic}</a> in a browser tab, and subscribe
         to <b>${topic}</b>. Then press Send test.`
      : 'Leave this empty for no notifications.';
  }

  $('notify-new').onclick = async () => {
    try {
      const answer = await api('/api/notify/topic');
      ntfyServer = answer.server || ntfyServer;
      field.value = answer.topic;
      localStorage.setItem('bc2_topic', answer.topic);
      describe();
      toast('Created your notification channel');
    } catch (problem) { toast(problem.message, true); }
  };

  $('notify-test').onclick = async () => {
    const topic = field.value.trim();
    if (!topic) return toast('Press New first', true);
    const button = $('notify-test');
    button.disabled = true;
    try {
      await api('/api/notify/test', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ topic }),
      });
      toast('Test sent — check your phone or the ntfy tab');
    } catch (problem) { toast(problem.message, true); }
    finally { button.disabled = false; }
  };

  describe();
  if (field.value) $('notify-box').open = false;
}

// ───────────────────────────────────────────────────────────── submit

async function submit() {
  const button = $('submit');
  const target = current();
  if (!state.targets.length) return note($('submit-note'), 'Choose a target first.', 'bad');
  if (!$('name').value.trim()) return note($('submit-note'), 'Give the campaign a name.', 'bad');

  // Whatever is in the text fields wins: someone may have typed rather than clicked.
  if (target) {
    const home = chainList(target)[0] || '';
    target.hotspots = spanToSet($('hotspots').value, home);
    target.coldspots = spanToSet($('coldspots').value, home);
  }

  const payload = {
    name: $('name').value.trim(),
    project: $('project').value.trim(),
    targets: state.targets.map(t => ({
      name: t.name,
      target_path: t.target_path,
      chains: t.chains.length ? t.chain : '',
      hotspots: setToSpan(t.hotspots, multiChain(t)),
      coldspots: setToSpan(t.coldspots, multiChain(t)),
      objective: t.objective,
    })),
    format: state.format,
    conformational: $('conformational').value,
    length_min: +$('len-min').value,
    length_max: +$('len-max').value,
    copies: +$('copies').value,
    domains: +$('domains').value,
    properties: [...document.querySelectorAll('.prop:checked')].map(i => i.value),
    number_of_final_designs: +$('wanted').value,
    max_trajectories: +$('attempts').value,
    gpus: +$('gpus').value,
    notify_topic: $('notify-topic').value.trim(),
    results_root: $('results-root').value.trim(),
    filter_i_pTM: $('f-iptm').value,
    filter_i_pAE: $('f-ipae').value,
    reproducible: $('reproducible').checked,
    // Destination, and the three numbers the server needs to judge it. Without
    // them it lets the submission through rather than guessing, so leaving them
    // out would quietly disable the size check rather than loosen it.
    gpu_type: $('cluster-gpu').value || undefined,
    queue: $('cluster-queue').value || undefined,
    target_residues: state.targets.reduce(
      (most, t) => Math.max(most, (t.residues || []).length), 0) || undefined,
  };

  button.disabled = true;
  try {
    const answer = await api('/api/jobs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    });
    rememberRoot($('results-root').value.trim());
    note($('submit-note'), `Submitted as job ${answer.id}.`, 'good');
    toast(`Campaign queued as job ${answer.id}`);
    show('jobs');
    // The server narrows the destination when "no preference" would have let a
    // campaign land on a card too small for it. That is a choice made on the
    // user's behalf, so it is said on the page they are sent to rather than in
    // a toast that leaves — and the note this replaced sits on the tab they
    // just left, where nobody would see it.
    note($('jobs-note'), answer.placed || '', answer.placed ? 'warn' : '');
    refreshJobs();
  } catch (problem) {
    note($('submit-note'), problem.message, 'bad');
  } finally {
    button.disabled = false;
  }
}

// ───────────────────────────────────────────────────────────── jobs

let watching = null;

// null is "All"; '' is Unfiled, which is a real group you file *from*, not the
// absence of a filter. Keeping them distinct is the whole reason this is not a
// plain string.
let projectFilter = null;
let picked = new Set();
let projects = [];

/** What a row is filed under. Old rows have no project and hold a person's name
    in `owner`; they belong to Unfiled rather than to a group named after a
    person who is no longer what the column means. */
function projectOf(job) {
  return (job.project || '').trim();
}

function projectLabel(name) {
  return name === '' ? 'Unfiled' : name;
}

/** The chips. Each carries its own count, so the row says how much is in each
    project before anything is clicked. Offered only once a project exists: a
    lone "All" chip is a control that cannot do anything. */
function drawProjectChips(list) {
  const box = $('project-chips');
  const real = list.filter(p => p.name !== '');
  const unfiled = list.find(p => p.name === '');
  const hasUnfiled = unfiled && (unfiled.campaigns || unfiled.reviews);
  // Two groups to tell apart, or none of this does anything: with everything in
  // one project, "All 15" beside "Spike 15" is two chips saying the same thing.
  const groups = real.length + (hasUnfiled ? 1 : 0);
  box.classList.toggle('hidden', groups < 2);
  if (groups < 2) { projectFilter = null; return; }

  if (projectFilter !== null && !list.some(p => p.name === projectFilter)) projectFilter = null;

  const total = list.reduce((n, p) => n + p.campaigns + p.reviews, 0);
  const rows = [{ name: null, label: 'All', count: total }]
    .concat(real.map(p => ({ name: p.name, label: p.name, count: p.campaigns + p.reviews })));
  if (hasUnfiled) rows.push({ name: '', label: 'Unfiled', count: unfiled.campaigns + unfiled.reviews });

  box.replaceChildren(el('span', { className: 'eyebrow', textContent: 'Project' }));
  for (const row of rows) {
    const on = projectFilter === row.name;
    const chip = el('button', {
      className: 'chip' + (on ? ' is-on' : ''), type: 'button',
      'aria-pressed': String(on),
    });
    chip.append(document.createTextNode(row.label));
    chip.append(el('span', { className: 'n', textContent: String(row.count) }));
    chip.onclick = () => { projectFilter = row.name; refreshJobs(); };
    box.append(chip);
  }
}

/** The selection bar and the mover below it. */
function drawPickedBar() {
  const bar = $('picked-bar');
  bar.classList.toggle('hidden', !picked.size);
  if (!picked.size) { $('mover').classList.add('hidden'); return; }
  $('picked-count').textContent = `${picked.size} selected`;
}

function openMover() {
  const box = $('mover'), pick = $('mover-pick');
  const names = projects.map(p => p.name).filter(Boolean).sort((a, b) => a.localeCompare(b));
  pick.replaceChildren();
  for (const name of names) pick.append(el('option', { value: name, textContent: name }));
  pick.append(el('option', { value: '', textContent: 'Unfiled' }));
  pick.append(el('option', { value: '\u0000new', textContent: 'New project\u2026' }));
  pick.value = names.length ? names[0] : '';
  $('mover-what').textContent = picked.size === 1 ? 'Move this to' : `Move ${picked.size} to`;
  const showNew = () => $('mover-new-wrap').classList.toggle('hidden', pick.value !== '\u0000new');
  pick.onchange = showNew;
  showNew();
  box.classList.remove('hidden');
  pick.focus();
}

/** Filing. The server moves a campaign's reviews with it, so the count it
    returns is rows touched and not rows chosen — saying "15 items" to someone
    who ticked two would read as a bug. */
async function applyMove() {
  const pick = $('mover-pick');
  const fresh = pick.value === '\u0000new';
  const name = fresh ? $('mover-new').value.trim() : pick.value;
  if (fresh && !name) return toast('Give the project a name', true);
  const ids = [...picked];
  note($('jobs-note'), '');
  try {
    const answer = await api('/api/jobs/project', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ids, project: name }),
    });
    const extra = (answer.changed || 0) - ids.length;
    toast(`Filed ${ids.length} campaign${ids.length === 1 ? '' : 's'}`
      + (extra > 0 ? ' and their reviews' : '')
      + ` under ${name || 'Unfiled'}`);
    picked = new Set();
    $('mover').classList.add('hidden');
    await refreshJobs();
  } catch (problem) { note($('jobs-note'), problem.message, 'bad'); }
}

/** Archiving. The server refuses anything queued or running and says so in
    `note`; swallowing that would leave a live campaign on the page with no
    sign of why it stayed. */
async function applyArchive() {
  const ids = [...picked];
  if (!confirm(`Archive ${ids.length} campaign${ids.length === 1 ? '' : 's'}? `
    + 'They leave the list; nothing on disk is deleted.')) return;
  note($('jobs-note'), '');
  try {
    const answer = await api('/api/jobs/archive', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ids, archived: true }),
    });
    picked = new Set();
    await refreshJobs();
    if (answer.note) note($('jobs-note'), answer.note, 'warn');
    else { note($('jobs-note'), ''); toast(`Archived ${answer.changed}`); }
  } catch (problem) { note($('jobs-note'), problem.message, 'bad'); }
}

async function refreshJobs() {
  let answer;
  try { answer = await api('/api/jobs'); } catch { return; }
  state.jobs = answer.jobs;
  // The projects come with the jobs, so the chip row costs no second call.
  projects = answer.projects || [];

  const live = state.jobs.filter(j => LIVE.has(j.state)).length;
  const pill = $('job-count');
  pill.textContent = live;
  pill.classList.toggle('show', live > 0);

  drawProjectChips(projects);
  const shown = projectFilter === null
    ? state.jobs : state.jobs.filter(j => projectOf(j) === projectFilter);

  // A tick on a row that has since left the list would act on something the
  // reader can no longer see.
  const alive = new Set(shown.map(j => j.id));
  for (const id of [...picked]) if (!alive.has(id)) picked.delete(id);
  drawPickedBar();

  drawJobGroup('jobs', 'count-design', shown.filter(j => j.kind !== 'analysis'), 'design');
  const { heads, earlier } = collapseReviews(shown.filter(j => j.kind === 'analysis'));
  drawJobGroup('reviews', 'count-review', heads, 'analysis', earlier);

  // Only campaigns have results to show. A review job in this list would offer a
  // job id that loadResults cannot do anything with.
  fillResultPickers();
}

/** The campaign a review job belongs to. Its own name carries a " (review)" suffix
    for the jobs list, so the folder is the reliable source. */
const campaignOf = (job) => (job.project_folder || '').split('/').filter(Boolean).pop() || '';

/** What to call what a review job reviews, in words. A campaign review is its campaign's
    name. A project review's folder is its slug (`@spike-ab12cd`), which is an address and
    not a name, so it is the project's label from the job row instead; `project` is the
    label itself, and `name` ("Spike (project review)") is the fallback. campaignOf stays
    the slug, because that is what the Review tab is opened with. */
const reviewedOf = (job) => {
  const folder = campaignOf(job);
  return isProject(folder) ? (job.project || job.name) : folder;
};

const LIVE = new Set(['running', 'queued']);

/** How many finished runs a section shows before it offers the rest behind a
    button. Four is roughly a screenful beside the live ones; the point is that
    a year of campaigns should not push the running one off the page. */
const SHOW_FINISHED = 4;

/* What the reader has opened: whole sections, and the rebuild history under a
   particular review row. Both live out here because refreshJobs runs on a timer
   and rebuilds the lists from scratch — held on the thing they open, they would
   close themselves under the reader every five seconds. */
const openFinished = { design: false, analysis: false };
const openBuilds = new Set();

/** One row per campaign for the Reviews section, newest build at the head.

    Reviewing a campaign again replaces the previous review rather than adding
    one: the bundle lives at a path named for the campaign, and a new build
    clears it first. The old job rows stay in the queue, so eight rebuilds of one
    campaign were eight rows whose Review button all opened the same page. What
    the earlier builds still carry that the newest does not is their log — why
    that attempt failed, how long it took — so they are kept, one level down. */
function collapseReviews(jobs) {
  const byCampaign = new Map();                   // insertion order: newest first
  for (const job of jobs) {
    // A review with no folder cannot be attributed to a campaign, so it stands
    // alone rather than being lumped in with every other unattributable one.
    const key = campaignOf(job) || `#${job.id}`;
    (byCampaign.get(key) || byCampaign.set(key, []).get(key)).push(job);
  }
  const heads = [], earlier = new Map();
  for (const builds of byCampaign.values()) {
    heads.push(builds[0]);
    if (builds.length > 1) earlier.set(builds[0].id, builds.slice(1));
  }
  return { heads, earlier };
}

/** One section of the Jobs page: live runs first, then the finished tail.
    Sorting by state rather than by time is the whole point of the page — what
    is running now is why anyone opened it, and it is otherwise scattered
    through a list ordered by when things started. */
/** A project heading's line: what is in it, in the words the section already
    uses, so the summary and the counts beside the heading cannot disagree. */
function summarise(jobs) {
  const running = jobs.filter(j => LIVE.has(j.state)).length;
  const done = jobs.length - running;
  const bits = [`${jobs.length} campaign${jobs.length === 1 ? '' : 's'}`];
  if (running) bits.push(`${running} running or queued`);
  if (done) bits.push(`${done} finished`);
  return bits.join(' \u00b7 ');
}

function drawJobGroup(boxId, countId, jobs, kind, earlier) {
  const box = $(boxId), count = $(countId);
  const redraw = () => drawJobGroup(boxId, countId, jobs, kind, earlier);
  box.replaceChildren();

  const review = kind === 'analysis';
  // Running above queued, newest first within each. The list arrives newest
  // first, which on its own puts a job that was queued a minute ago above the
  // one that has been holding two cards for an hour — the wrong way round for
  // a page whose question is what the machine is doing right now.
  const live = jobs.filter(j => LIVE.has(j.state))
                   .sort((a, b) => (a.state === b.state ? 0 : a.state === 'running' ? -1 : 1));
  const past = jobs.filter(j => !LIVE.has(j.state));

  const bits = [];
  if (live.length) bits.push(`${live.length} ${live.length === 1 ? 'is' : 'are'} running or queued`);
  if (past.length) bits.push(`${past.length} finished`);
  count.textContent = bits.length ? '— ' + bits.join(', ') : '';

  if (!jobs.length) {
    // "None yet" is false when a filter is what emptied the list, and it sends
    // the reader looking for work they have already done.
    box.append(el('div', { className: 'empty', textContent: projectFilter !== null
      ? `No ${review ? 'reviews' : 'campaigns'} in ${projectLabel(projectFilter)}.`
      : review ? 'No reviews yet. Start one from the Review tab.'
      : 'No campaigns yet.' }));
    return;
  }

  /** A row, plus the rebuilds behind it if this one stands for several. */
  const put = (job) => {
    box.append(jobRow(job, review));
    const older = earlier && earlier.get(job.id);
    if (!older || !older.length) return;
    const key = String(job.id), open = openBuilds.has(key);
    const toggle = el('button', { className: 'ghost builds-toggle', textContent: open
      ? 'Hide earlier builds'
      : `${older.length} earlier build${older.length > 1 ? 's' : ''}` });
    toggle.onclick = () => { open ? openBuilds.delete(key) : openBuilds.add(key); redraw(); };
    box.append(toggle);
    if (open) for (const old of older) box.append(jobRow(old, review, true));
  };

  // Grouped only while every project is on screen and there is more than one to
  // tell apart. Inside a chosen project a heading would name what the chip
  // above already says, and one group is not a grouping.
  const names = [...new Set(jobs.map(projectOf))];
  if (!review && projectFilter === null && names.length > 1) {
    names.sort((a, b) => a === '' ? 1 : b === '' ? -1 : a.localeCompare(b));
    for (const name of names) {
      const mine = jobs.filter(j => projectOf(j) === name);
      const head = el('div', { className: 'group-project' });
      head.append(el('span', { className: 'name', textContent: projectLabel(name) }));
      head.append(el('span', { className: 'summary', textContent: summarise(mine) }));
      box.append(head);
      const liveHere = mine.filter(j => LIVE.has(j.state))
        .sort((a, b) => (a.state === b.state ? 0 : a.state === 'running' ? -1 : 1));
      for (const job of liveHere) put(job);
      for (const job of mine.filter(j => !LIVE.has(j.state))) put(job);
    }
    return;
  }

  for (const job of live) put(job);

  if (past.length) {
    // The divider earns its place only when there is something above it to be
    // divided from; with nothing running it would label the whole list.
    if (live.length) box.append(el('div', { className: 'group-label', textContent: 'Finished' }));
    const shown = openFinished[kind] ? past : past.slice(0, SHOW_FINISHED);
    for (const job of shown) put(job);
    if (past.length > SHOW_FINISHED) {
      const more = el('button', { className: 'ghost more', textContent: openFinished[kind]
        ? 'Show fewer' : `Show ${past.length - SHOW_FINISHED} more finished` });
      // Redrawn from the list already in hand, not refetched: opening a section
      // is the reader's own click and should not wait on the network.
      more.onclick = () => { openFinished[kind] = !openFinished[kind]; redraw(); };
      box.append(more);
    }
  }
}

/** The destination: which card model, and which of the user's own queues.

    The server sizes a chosen model by its card size, not by what is free this
    second — a queued campaign lands on an empty card of that model whenever one
    frees up. So a model can be offered here and still show nothing free in the
    table above: "can this ever run it" and "could it start now" are different
    questions, and the table answers the second. */
function drawDestination(gpus, queues, need, holds) {
  const pick = $('cluster-gpu'), queue = $('cluster-queue');
  const keep = pick.value, keepQueue = queue.value;

  pick.replaceChildren(el('option', { value: '', textContent: 'No preference' }));
  for (const g of gpus) {
    if (!g.flag) continue;
    const size = g.card_gb != null ? `${Math.round(g.card_gb)} GB` : '';
    const tooSmall = need && g.card_gb != null && !holds(g.card_gb);
    const tail = tooSmall ? 'too small'
      : g.free ? `${g.free} free now`
      : g.evictable ? 'none free; queues' : 'none free now';
    const option = el('option', { value: g.flag,
      textContent: `${g.model} — ${[size, tail].filter(Boolean).join(', ')}` });
    // A card that cannot hold the campaign is not a choice, and the server
    // would refuse it anyway. Left listed rather than dropped so its absence
    // does not read as the cluster not owning one.
    if (tooSmall) option.disabled = true;
    pick.append(option);
  }
  pick.value = [...pick.options].some(o => o.value === keep && !o.disabled) ? keep : '';

  // Only queues the user can actually submit to. The rest need a manager's
  // permission, so listing them would offer a choice that is not theirs.
  const mine = queues.filter(q => q.mine);
  queue.replaceChildren(el('option', { value: '', textContent: 'No preference' }));
  for (const q of mine) queue.append(el('option', { value: q.name, textContent: q.name }));
  queue.value = mine.some(q => q.name === keepQueue) ? keepQueue : '';
  queue.closest('label').classList.toggle('hidden', !mine.length);

  // Naming no model submits without a size constraint, so the scheduler may
  // still hand over a card too small to run it. Say so rather than letting the
  // quiet default look like the safe one.
  const anyTooSmall = need && gpus.some(g => g.card_gb != null && !holds(g.card_gb));
  note($('cluster-pick-note'),
    !pick.value && anyTooSmall
      ? 'With no model named, the queue may start this on a card too small for it. Pick one above.'
      : '',
    'warn');
}

/** When a superseded build was made. A job id reads as a per-campaign build
    number and is not one: ids are handed out across every campaign and kind, so
    one campaign's history runs #32, #30, #28, #22 and the gaps look like builds
    that went missing. The time is what actually tells two builds apart. */
function builtWhen(job) {
  const at = job.finished_at || job.started_at || job.created_at;
  if (!at) return 'earlier build';
  const when = new Date(at * 1000);
  // Rebuilds cluster in a working day, where a repeated date down every row is
  // noise and the clock time is the whole distinction.
  const today = when.toDateString() === new Date().toDateString();
  return when.toLocaleString([], today
    ? { hour: '2-digit', minute: '2-digit' }
    : { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/** One run. A review and a campaign share a shape because they are both work in
    a queue, and differ in what they say and what you can do with them. */
function jobRow(job, review, superseded) {
  const row = el('div', { className: 'job' + (review ? ' is-review' : '') + (superseded ? ' is-old' : '')
    + (picked.has(job.id) ? ' is-picked' : '') });

  // Only campaigns are selectable. A review follows the campaign it is about,
  // so filing one on its own would claim a thing the data cannot hold.
  if (!review && !superseded) {
    const tick = el('input', { type: 'checkbox', className: 'tick', checked: picked.has(job.id) });
    tick.setAttribute('aria-label', 'Select ' + job.name);
    tick.onchange = () => {
      picked.has(job.id) ? picked.delete(job.id) : picked.add(job.id);
      row.classList.toggle('is-picked', picked.has(job.id));
      drawPickedBar();
    };
    row.append(tick);
  }

  row.append(el('span', { className: 'state-chip state-' + job.state, textContent: job.state }));
  // In the Reviews section the " (review)" suffix on every name is noise, and
  // the campaign is what the reader is actually looking for. A superseded build
  // would then repeat the name of the row above it, so it is dated instead.
  row.append(el('b', { textContent: superseded ? builtWhen(job)
                                  // A project review's own chip, beside this, already carries the
                                  // label, so naming it here would print "Spike  Spike". What the row
                                  // has to say instead is which of that project's work went in.
                                  : review ? (isProject(campaignOf(job)) ? 'all campaigns'
                                                                         : (campaignOf(job) || job.name))
                                  : job.name }));

  // A campaign's project can be changed from here; a review's is shown and not
  // offered, because it is inherited and changing it here would not stick.
  if (!superseded) {
    const name = projectOf(job);
    if (review) {
      if (name) row.append(el('span', { className: 'proj', textContent: name }));
    } else {
      const chip = el('button', { className: 'proj', type: 'button',
        title: 'Change the project', textContent: projectLabel(name) });
      chip.onclick = () => { picked = new Set([job.id]); refreshJobs(); openMover(); };
      row.append(chip);
    }
  }

  const bits = [];
  // The project has its own chip now, so only the pre-rename `owner` is left to
  // say here — old rows keep showing the name they were actually given.
  if (!job.project && job.owner) bits.push(job.owner);
  // A review takes no card, so the GPU wording would only confuse. Its own
  // section now says what kind of work it is, so the row stays quiet about it.
  if (!review) {
    if (job.state === 'running' && job.gpus_assigned) bits.push('GPU ' + job.gpus_assigned);
    else if (job.state === 'queued') bits.push(`waiting for ${job.gpus_wanted} GPU${job.gpus_wanted > 1 ? 's' : ''}`);
  }
  if (job.started_at) bits.push(since(job.started_at, job.finished_at));
  if (job.message) bits.push(job.message);
  row.append(el('span', { className: 'meta', textContent: bits.join(' · ') }));

  // The buttons share one box rather than sitting loose in the row. Loose, each
  // was its own flex item, so a narrow window broke the set apart and left Log
  // stranded on the first line with Results and Run again below it. Wrapped,
  // they move as the one group they are.
  const acts = el('div', { className: 'actions' });
  row.append(acts);

  const logButton = el('button', { className: 'ghost', textContent: 'Log' });
  logButton.onclick = () => watchLog(job.id, job.name);
  acts.append(logButton);

  if (LIVE.has(job.state)) {
    const stop = el('button', { className: 'ghost', textContent: 'Stop' });
    stop.onclick = async () => {
      const warning = !review ? `Stop "${job.name}"? Designs that have already passed are kept.`
        : isProject(campaignOf(job)) ? `Stop building the review across the campaigns of "${reviewedOf(job)}"?`
        : `Stop building the review of "${campaignOf(job)}"?`;
      if (!confirm(warning)) return;
      try { await api('/api/jobs/' + job.id, { method: 'DELETE' }); refreshJobs(); }
      catch (problem) { toast(problem.message, true); }
    };
    acts.append(stop);
  } else if (review) {
    // A review's own id means nothing to the Results tab, and there are no
    // campaign settings behind it to run again. Its one useful action is the
    // review itself — except on a superseded build, whose button would open the
    // review the row above it owns. That row is where the review lives; this one
    // is here for its log.
    if (!superseded) {
      const open = el('button', { className: 'ghost', textContent: 'Review' });
      if (isProject(campaignOf(job))) open.title = `Open the review across the campaigns of "${reviewedOf(job)}"`;
      open.onclick = () => { show('review'); chooseReview(campaignOf(job)); };
      acts.append(open);
    }
  } else {
    const look = el('button', { className: 'ghost', textContent: 'Results' });
    look.onclick = () => { show('results'); $('result-job').value = String(job.id); loadResults(); };
    acts.append(look);
  }
  if (!review) {
    const again = el('button', { className: 'ghost', textContent: 'Run again',
                                 title: 'Load these settings into the design form' });
    again.onclick = () => cloneCampaign(job);
    acts.append(again);
  }
  return row;
}

/** Load a finished campaign's settings back into the form, ready to change one thing.
    Re-entering a target and its epitope by hand to try another binder format is the
    kind of friction that loses hotspots between runs. */
async function cloneCampaign(job) {
  let settings;
  try { settings = await api(`/api/jobs/${job.id}/campaign`); }
  catch (problem) { return toast(problem.message, true); }

  state.targets = (settings.targets || []).map(entry => ({
    name: entry.name,
    target_path: entry.target_path,
    chains: [],
    chain: entry.chains || 'A',
    hotspots: spanToSet(entry.hotspots || '', String(entry.chains || 'A').split(',')[0]),
    coldspots: spanToSet(entry.coldspots || '', String(entry.chains || 'A').split(',')[0]),
    objective: entry.objective === 'detarget' ? 'detarget' : 'bind',
  }));
  state.active = 0;

  const modalities = String(settings.modality || 'binder').split(',');
  state.format = modalities[0] || 'binder';
  $('conformational').value = modalities[1] || '';
  buildFormats();
  applyFormat();
  if (Array.isArray(settings.binder_lengths)) {
    $('len-min').value = settings.binder_lengths[0];
    $('len-max').value = settings.binder_lengths[1];
  }
  document.querySelectorAll('.prop').forEach(box => { box.checked = settings[box.value] === true; });
  $('wanted').value = settings.number_of_final_designs || 10;
  $('attempts').value = settings.max_trajectories || 200;
  $('name').value = (settings.campaign_name || job.name) + '_2';
  $('gpus').value = String(job.gpus_wanted || 1);

  show('design');
  drawTargets();
  await loadViewer();
  await loadAnalysis();
  window.scrollTo({ top: 0, behavior: 'smooth' });
  toast(`Loaded ${job.name}. Change what you want and start it.`);
}

function since(from, to) {
  const seconds = Math.max(0, Math.round(((to || Date.now() / 1000) - from)));
  const hours = Math.floor(seconds / 3600), minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m`;
  return `${seconds}s`;
}

async function watchLog(id, name) {
  watching = id;
  $('log-box').classList.remove('hidden');
  $('log-for').textContent = '— ' + name;
  await pullLog();
  $('log-box').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/** Put log text in a <pre>, following the end only if the reader was already there. */
function fillLog(box, text) {
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  box.textContent = text;
  if (atBottom) box.scrollTop = box.scrollHeight;
}

async function pullLog() {
  if (watching == null) return;
  try {
    fillLog($('log'), await api(`/api/jobs/${watching}/log`));
  } catch { /* the job may have gone */ }
}

// ───────────────────────────────────────────────────────────── results

/** Counts stay counts: a rank of 1 should not read as 1.00. */
function tidy(number) {
  if (Number.isInteger(number)) return String(number);
  return Math.abs(number) >= 100 ? number.toFixed(0) : number.toFixed(2);
}

/** BindCraft names a design <campaign>_<modality>_l<len>_<hash>_<seq>. The campaign
    and length are the same down the whole table, so the hash is what tells them apart. */
function nameParts(full) {
  const parts = full.split('_');
  const sequence = parts[parts.length - 1];
  const hash = parts[parts.length - 2] || '';
  return hash ? { hash: hash.slice(0, 7), sequence } : null;
}
function shortName(full) {
  const found = nameParts(full);
  return found ? `${found.hash}·${found.sequence}` : full;
}

/** Columns of the Results table: key, header, what it means, kind, and which way
    a first click should sort.

    Which way is good is not guessable from the name -- i_pDAE is a confidence, so
    higher is better, while i_pAE is an error, so lower is -- so the headers say so
    on hover. `across` marks the columns that cannot be compared between campaigns:
    each campaign used its own filters, target and binder length, so an i_pDAE or a
    rank from one is not on the same scale as the other's. Length, mass, charge and
    free cysteines are properties of the sequence and compare anywhere. */
const RESULT_COLUMNS = [
  { key: 'campaign', label: 'Campaign', kind: 'text', first: 1,
    title: 'The campaign this design came from' },
  { key: 'design', label: 'Design', kind: 'text', first: 1,
    title: 'Identifier of this design: backbone hash, then sequence number. Hover a row for the full name' },
  { key: 'family', label: 'Family (backbone)', kind: 'text', first: 1,
    title: 'One backbone. Sequences on the same backbone are siblings: hedges on one idea, not independent ideas' },
  { key: 'length', label: 'Len', kind: 'num', first: 1,
    title: 'Binder length in residues' },
  { key: 'i_pTM', label: 'i_pTM', kind: 'num', first: -1, across: true,
    title: 'Interface confidence, 0 to 1. Higher is better. Comparable only within one target and modality' },
  { key: 'i_pDAE', label: 'i_pDAE', kind: 'num', first: -1, across: true,
    title: 'Distance-masked interface confidence, what the ranking uses. Higher is better. Comparable only within one target and modality' },
  { key: 'Binder_Free_Cysteines', label: 'Free Cys', kind: 'num', first: 1,
    title: 'Cysteines in the binder that are not paired in a disulfide' },
  { key: 'Binder_Net_Charge', label: 'Net charge', kind: 'num', first: 1,
    title: 'Net charge of the binder sequence' },
  { key: 'Binder_Mass_kDa', label: 'Mass kDa', kind: 'num', first: 1,
    title: 'Binder mass in kilodaltons' },
  { key: 'Binder_Sequence', label: 'Sequence', kind: 'seq', sortable: false,
    title: 'The binder sequence, shortened here. Hover for all of it, or use Copy. The CSV export carries the whole sequence' },
  { key: 'rank', label: 'Rank', kind: 'num', first: 1, across: true,
    title: "Position among this campaign's ranked designs, best first. An ordinal within one campaign's own pool: rank 1 of one campaign is not rank 1 of another" },
];

/** How much of a sequence the table shows: the first few residues and the last few.
    Binders run 64 to 140, and a column that held one whole would push everything
    else off a laptop screen. The end is shown as well as the start because every
    VHH opens with the same framework and a prefix alone would make them look identical. */
const SEQ_HEAD = 6, SEQ_TAIL = 4;

/** "All campaigns" in the campaign select. A job id is a number, so this cannot
    collide with one; the empty string is what the select holds before it has any
    options, and is treated the same way. */
const ALL_CAMPAIGNS = 'all';
/** "All projects" in the project select. Unfiled is the empty string, and a label
    is free text, so the sentinel has to be something no label can be. */
const ALL_PROJECTS = '\u0000all';

/* What Results is showing. Out here and not on the nodes: refreshJobs rebuilds
   the selects from scratch on a timer, and anything held on them goes with them. */
const resultsUi = {
  project: ALL_PROJECTS,  // what the Project select holds
  rankedOnly: true,       // the default: the shortlist, one best sequence per backbone
  campaigns: [],          // campaigns[] of the last /api/designs answer
  error: '',              // why the last load failed, or ''
  loading: false,
  empty: '',              // why there is nothing to ask for, when there is not
  picked: '',             // key of the design whose structure is open
};
let resultsSeq = 0;       // a slow answer for an earlier choice must not overwrite a later one
let resultPickSig = '';   // what the selects were last built from

const jobFor = (id) => state.jobs.find(j => String(j.id) === String(id));
const resultsVisible = () => !$('view-results').classList.contains('hidden');
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;
const designKey = (d) => `${d.campaign_path}\n${d.design}`;

/** Fill the Project and Campaign selects from the jobs and projects refreshJobs
    already has, so Results costs no second poll.

    The campaign select lists every campaign, not only the chosen project's. Other
    code sets its value by job id (the Jobs and Review tabs both do, and
    drawBridge reads it back); if the list were filtered, a campaign from another
    project would silently set nothing. Choosing such a campaign instead moves the
    Project select to match, in loadResults. The lists are rebuilt only when what
    they would say has changed, so a dropdown held open is not closed every five
    seconds. */
function fillResultPickers() {
  const campaigns = state.jobs.filter(j => j.kind !== 'analysis');
  const named = projects.filter(p => p.name !== '').map(p => p.name)
    .sort((a, b) => a.localeCompare(b));
  const filed = new Set(campaigns.map(projectOf));
  const groups = named.filter(n => filed.has(n)).concat(filed.has('') ? [''] : []);

  const sig = JSON.stringify([
    projects.map(p => [p.name, p.campaigns]),
    campaigns.map(j => [j.id, j.name, j.state, projectOf(j)]),
  ]);
  const pick = $('result-job'), projectPick = $('result-project');
  const chosen = pick.value;
  if (sig !== resultPickSig) {
    resultPickSig = sig;

    projectPick.replaceChildren(el('option', { value: ALL_PROJECTS, textContent: 'All projects' }));
    for (const name of groups) {
      const mine = campaigns.filter(j => projectOf(j) === name).length;
      projectPick.append(el('option', { value: name, textContent: `${projectLabel(name)} (${mine})` }));
    }

    pick.replaceChildren(el('option', { value: ALL_CAMPAIGNS, textContent: 'All campaigns' }));
    const option = (job) => el('option', { value: String(job.id), textContent: `${job.name} (${job.state})` });
    if (groups.length > 1) {
      for (const name of groups) {
        const group = el('optgroup', { label: projectLabel(name) });
        for (const job of campaigns.filter(j => projectOf(j) === name)) group.append(option(job));
        pick.append(group);
      }
    } else {
      for (const job of campaigns) pick.append(option(job));
    }
  }

  projectPick.value = resultsUi.project;
  if (projectPick.value !== resultsUi.project) {   // that project has no campaigns left
    resultsUi.project = ALL_PROJECTS;
    projectPick.value = ALL_PROJECTS;
  }
  const want = chosen || ALL_CAMPAIGNS;
  pick.value = want;
  if (pick.value !== want) {                        // that campaign has gone from the list
    pick.value = ALL_CAMPAIGNS;
    if (resultsVisible()) loadResults();
  }
}

/** What to ask /api/designs for, from the two selects. Null when the choice
    cannot have designs, with the reason. */
function resultsQuery() {
  const value = $('result-job').value;
  const params = new URLSearchParams();
  const job = value && value !== ALL_CAMPAIGNS ? jobFor(value) : null;
  if (job) {
    // The whole path, not the name: the same name can exist in two results roots.
    if (!job.project_folder) return { why: 'This campaign has not started yet, so it has no designs.' };
    params.set('campaign', job.project_folder);
  } else if (resultsUi.project !== ALL_PROJECTS) {
    params.set('project', resultsUi.project);        // '' is Unfiled, and must still be sent
  }
  return { query: params.toString() };
}

async function loadResults() {
  const seq = ++resultsSeq;
  const value = $('result-job').value;
  $('design-box').classList.add('hidden');
  resultsUi.picked = '';
  drawBridge();

  // Another tab can have set the campaign directly. If it belongs to a different
  // project than the one chosen, the project follows it, so the two selects
  // never disagree about what is on the page.
  const job = value && value !== ALL_CAMPAIGNS ? jobFor(value) : null;
  if (job && resultsUi.project !== ALL_PROJECTS && projectOf(job) !== resultsUi.project) {
    resultsUi.project = projectOf(job);
    $('result-project').value = resultsUi.project;
  }

  const ask = resultsQuery();
  resultsUi.error = '';
  resultsUi.empty = ask.why || '';
  if (ask.why) {
    state.results = []; resultsUi.campaigns = []; resultsUi.loading = false;
    drawResults();
    return;
  }
  resultsUi.loading = true;
  drawResults();

  let answer;
  try { answer = await api('/api/designs' + (ask.query ? '?' + ask.query : '')); }
  catch (problem) {
    if (seq !== resultsSeq) return;
    state.results = []; resultsUi.campaigns = [];
    resultsUi.loading = false; resultsUi.error = problem.message;
    drawResults();
    return;
  }
  if (seq !== resultsSeq) return;
  state.results = answer.designs || [];
  resultsUi.campaigns = answer.campaigns || [];
  resultsUi.loading = false;
  drawResults();
}

// The order a reader wants first: by campaign, then by rank inside it, so each
// campaign's shortlist reads top to bottom. Unranked rows follow their campaign's
// ranked ones.
const byText = (a, b) => String(a ?? '').localeCompare(String(b ?? ''), undefined, { numeric: true });
function byDefault(a, b) {
  return byText(a.campaign, b.campaign) || byText(a.campaign_path, b.campaign_path)
    || ((a.rank ?? Infinity) - (b.rank ?? Infinity) || 0)
    || byText(a.design, b.design);
}

function sortedResults(rows) {
  const { key, dir } = state.sort;
  if (!key) return rows.sort(byDefault);
  const column = RESULT_COLUMNS.find(c => c.key === key);
  const text = column && column.kind === 'text';
  return rows.sort((a, b) => {
    const x = a[key], y = b[key];
    // A missing value goes last whichever way the column is sorted: 0.0 would be
    // read as the best i_pDAE, and a blank must never be.
    if (x == null || y == null) return (x == null) - (y == null) || byDefault(a, b);
    return (text ? byText(x, y) : x - y) * dir || byDefault(a, b);
  });
}

/** The rows on screen: what the chip lets through, in the order the header asks for. */
function shownResults() {
  const rows = resultsUi.rankedOnly ? state.results.filter(d => d.ranked) : state.results.slice();
  return sortedResults(rows);
}

function resultScope() {
  const value = $('result-job').value;
  const job = value && value !== ALL_CAMPAIGNS ? jobFor(value) : null;
  if (job) return job.name;
  return resultsUi.project === ALL_PROJECTS ? 'all projects'
    : `${projectLabel(resultsUi.project)} · all campaigns`;
}

function drawResults() {
  const rows = state.results;
  const ranked = rows.filter(d => d.ranked).length;
  const live = resultsUi.campaigns.filter(c => !c.missing);
  const ready = !resultsUi.loading && !resultsUi.error && !resultsUi.empty;

  $('results-scope').textContent = '— ' + resultScope();
  drawResultsSummary(ready, rows.length, ranked, live.length);
  drawResultChips(ready, rows.length, ranked);
  // Hidden, not disabled: a dimmed button reads at half contrast, and with nothing to export it has no use.
  $('results-export').classList.toggle('hidden', !ready || !shownResults().length);
  drawProjectStrip();
  drawReviewStrip(ready);

  const box = $('results-table');
  box.replaceChildren();
  if (resultsUi.loading) {
    note($('results-caveat'), '');
    box.append(el('div', { className: 'empty', textContent: 'Loading…' }));
    return;
  }
  if (resultsUi.error) {
    note($('results-caveat'), '');
    box.append(el('div', { className: 'note bad', textContent: 'Could not load designs: ' + resultsUi.error }));
    return;
  }
  if (resultsUi.empty) {
    note($('results-caveat'), '');
    box.append(el('div', { className: 'empty', textContent: resultsUi.empty }));
    return;
  }

  const shown = shownResults();
  if (!shown.length) {
    note($('results-caveat'), '');
    box.append(emptyResults(rows.length, ranked, resultsUi.campaigns));
    return;
  }

  // A design opened before a filter or a refresh may no longer be in the table.
  if (resultsUi.picked && !shown.some(d => designKey(d) === resultsUi.picked)) {
    resultsUi.picked = '';
    $('design-box').classList.add('hidden');
  }
  drawSortCaveat(shown);

  const rankedIn = new Map(resultsUi.campaigns.map(c => [c.path, c.counts && c.counts.ranked]));
  // At RESULTS_CARDS_BELOW and narrower a design is a stacked card, not a table row:
  // eleven columns do not fit, and scrolling sideways hid the Rank column where nobody found it.
  if (narrowResults.matches) { drawResultCards(box, shown, rankedIn); return; }
  const table = el('table', { className: 'results-table' });
  const head = el('tr');
  RESULT_COLUMNS.forEach((column, index) => {
    // A sequence has no order worth sorting by, so its header is a label and not a control.
    if (column.sortable === false) {
      head.append(el('th', { scope: 'col', className: 'plain', title: column.title, textContent: column.label }));
      return;
    }
    const active = state.sort.key === column.key;
    const cell = el('th', {
      scope: 'col', tabIndex: 0, title: column.title,
      textContent: column.label + (active ? (state.sort.dir > 0 ? ' ▲' : ' ▼') : ''),
    });
    cell.setAttribute('aria-sort', active ? (state.sort.dir > 0 ? 'ascending' : 'descending') : 'none');
    const sortBy = (byKey) => {
      state.sort.dir = active ? -state.sort.dir : column.first;
      state.sort.key = column.key;
      drawResults();
      // The header was rebuilt, so a keyboard user's place in it has to be put back.
      if (byKey) $('results-table').querySelectorAll('th')[index].focus();
    };
    cell.onclick = () => sortBy(false);
    cell.onkeydown = (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); sortBy(true); }
    };
    head.append(cell);
  });
  table.append(el('thead', {}, head));

  const body = el('tbody');
  for (const design of shown) {
    const row = el('tr', { tabIndex: 0 });
    if (designKey(design) === resultsUi.picked) row.classList.add('is-on');
    row.title = design.design || '';
    for (const column of RESULT_COLUMNS) {
      row.append(resultCell(design, column, rankedIn.get(design.campaign_path)));
    }
    const open = () => {
      resultsUi.picked = designKey(design);
      [...body.children].forEach(r => r.classList.toggle('is-on', r === row));
      showDesign(design);
    };
    row.onclick = open;
    // Only the row's own Enter: the Copy button inside it has an Enter of its own.
    row.onkeydown = (event) => { if (event.key === 'Enter' && event.target === row) open(); };
    body.append(row);
  }
  table.append(body);
  box.append(el('div', { className: 'table-scroll' }, table));
}

/** Below the breakpoint the table becomes a list of cards. It is not the 760px of the
    rest of the server UI: that is where multi-column strips fold, and this table is
    wider than they are. It is the width at which the table stops fitting its card,
    measured on real data. The table's smallest width is 772px, and 800px with every
    design id and campaign name at its cap (see .id-tail in style.css); the card is the
    viewport less 105px up to 830px, so 800px needs a viewport of 905px, and 920px
    leaves 16px spare. Above it the table never scrolls sideways, whatever the data.
    The one query lives here, so that the markup cannot disagree with the stylesheet
    about which layout is on screen: style.css has no media query for it. */
const RESULTS_CARDS_BELOW = 920;
const narrowResults = window.matchMedia(`(max-width: ${RESULTS_CARDS_BELOW}px)`);
narrowResults.addEventListener('change', () => { if (resultsVisible()) drawResults(); });

const SORT_DEFAULT = 'default';

/** What the narrow layout's Sort select offers: the same columns the headers sort,
    each way round, driving the same state.sort. The direction a header's first click
    would take comes first. Written as words because there is no arrow to read. */
function sortChoices() {
  const choices = [{ value: SORT_DEFAULT, text: 'Campaign, then rank' }];
  for (const column of RESULT_COLUMNS) {
    if (column.sortable === false) continue;
    const words = column.kind === 'text'
      ? { 1: 'A to Z', '-1': 'Z to A' } : { 1: 'lowest first', '-1': 'highest first' };
    for (const dir of [column.first, -column.first]) {
      choices.push({ value: `${column.key}:${dir}`, text: `${column.label}, ${words[dir]}` });
    }
  }
  return choices;
}

/** The select that stands in for the column headers. One sort state: it reads
    state.sort to show itself and writes state.sort when it changes. */
function sortSelect() {
  const select = el('select');
  select.setAttribute('aria-label', 'Sort designs by');
  for (const choice of sortChoices()) {
    select.append(el('option', { value: choice.value, textContent: choice.text }));
  }
  select.value = state.sort.key ? `${state.sort.key}:${state.sort.dir}` : SORT_DEFAULT;
  select.onchange = () => {
    if (select.value === SORT_DEFAULT) {
      state.sort.key = null; state.sort.dir = 1;
    } else {
      const at = select.value.lastIndexOf(':');
      state.sort.key = select.value.slice(0, at);
      state.sort.dir = Number(select.value.slice(at + 1));
    }
    drawResults();
    // The list was rebuilt, and a keyboard user's place in it has to be put back.
    const again = $('results-table').querySelector('.results-sort select');
    if (again) again.focus();
  };
  return el('label', { className: 'inline results-sort' }, [el('span', { textContent: 'Sort by' }), select]);
}

/** One design as a card: who it is and where it ranks on the first line, the
    figures as label and value pairs, the sequence and its Copy on a line of their own.
    The cells come from resultCell, so a number is formatted one way in both layouts.
    Labels are elements, not CSS text, so a screen reader and a copy both carry them. */
function resultCard(design, rankedInCampaign) {
  const card = el('li', { className: 'result-card', tabIndex: 0, title: design.design || '' });
  const cell = (key) => resultCell(design, RESULT_COLUMNS.find(c => c.key === key), rankedInCampaign);
  const fill = (target, key) => {
    const source = cell(key);
    target.append(...source.childNodes);
    if (source.title) target.title = source.title;
    return target;
  };

  const head = el('div', { className: 'rc-head' });
  head.append(fill(el('b', { className: 'rc-id' }), 'design'));
  head.append(fill(el('span', { className: 'rc-campaign' }), 'campaign'));
  // "Rank 3 of 11", never a bare 3: it is a position in one campaign's pool.
  if (design.ranked && design.rank != null) {
    head.append(fill(el('span', { className: 'rc-rank' }), 'rank'));
    head.querySelector('.rc-rank').prepend('Rank ');
  } else {
    head.append(el('span', { className: 'rc-rank', textContent: 'Not ranked',
      title: 'Not the best sequence on its backbone, so not on the ranked shortlist' }));
  }
  card.append(head);

  const metrics = el('dl', { className: 'rc-metrics' });
  for (const key of ['length', 'i_pTM', 'i_pDAE', 'Binder_Free_Cysteines', 'Binder_Net_Charge',
    'Binder_Mass_kDa', 'family']) {
    const column = RESULT_COLUMNS.find(c => c.key === key);
    const pair = el('div', { className: 'rc-pair' });
    pair.append(el('dt', { textContent: column.label, title: column.title }));
    pair.append(fill(el('dd'), key));
    metrics.append(pair);
  }
  card.append(metrics);

  const seq = el('div', { className: 'rc-seq' });
  seq.append(el('span', { className: 'rc-k', textContent: 'Sequence' }));
  fill(seq, 'Binder_Sequence');
  card.append(seq);
  return card;
}

/** The narrow layout: the sort select, then the cards. Behaves as the table does: a
    click or Enter on a card opens the design, and Copy inside it does not. */
function drawResultCards(box, shown, rankedIn) {
  box.append(sortSelect());
  const list = el('ul', { className: 'result-list' });
  for (const design of shown) {
    const card = resultCard(design, rankedIn.get(design.campaign_path));
    if (designKey(design) === resultsUi.picked) card.classList.add('is-on');
    const open = () => {
      resultsUi.picked = designKey(design);
      [...list.children].forEach(c => c.classList.toggle('is-on', c === card));
      showDesign(design);
      // The structure sits below the whole list, which at this width is a long way down.
      $('design-box').scrollIntoView({ block: 'start' });
    };
    card.onclick = open;
    card.onkeydown = (event) => { if (event.key === 'Enter' && event.target === card) open(); };
    list.append(card);
  }
  box.append(list);
}

function resultCell(design, column, rankedInCampaign) {
  const key = column.key, value = design[key];
  const cell = el('td', { className: column.kind === 'num' ? 'num-cell' : '' });
  if (key === 'design') {
    // The hash is fixed at seven characters. What follows it is "seq0" or
    // "candidate12", and it is the part that tells siblings apart, so it is the part
    // that must not be cut off at the front. In the table it sits on its own line
    // (rows are two lines tall already, for the family's sibling count) and is capped
    // at a width, with the whole id in the title. In a card it runs on after the hash.
    const full = String(value || '');
    const found = nameParts(full);
    if (found) {
      cell.append(found.hash + '·', el('span', { className: 'id-tail', title: full, textContent: found.sequence }));
    } else {
      cell.append(el('span', { className: 'id-tail', title: full, textContent: full }));
    }
  } else if (key === 'family') {
    // One backbone. The count includes this row, so a family of one says so.
    cell.append(value ? String(value).slice(0, 7) : '–');
    if (value) {
      cell.append(' ');
      cell.append(el('span', { className: 'sibs',
        title: `${plural(design.siblings, 'passed sequence')} on this backbone`,
        textContent: `${design.siblings} seq${design.siblings === 1 ? '' : 's'}` }));
    }
  } else if (key === 'rank') {
    // A bare 1 reads as the best design anywhere; "of 11" says it is one pool's.
    if (design.ranked && value != null) {
      cell.append(String(value));
      if (rankedInCampaign) cell.append(' ');
      if (rankedInCampaign) cell.append(el('span', { className: 'sibs', textContent: `of ${rankedInCampaign}` }));
    } else {
      cell.textContent = '–';
      cell.title = 'Not the best sequence on its backbone, so not on the ranked shortlist';
    }
  } else if (column.kind === 'seq') {
    cell.className = 'seq-cell';
    if (!value) { cell.textContent = '–'; return cell; }
    const full = String(value);
    // A cut and an ellipsis, not CSS clipping: the DOM then holds only what is
    // shown, and a screen reader is not asked to spell out 140 residues a row.
    cell.append(el('code', { className: 'seq-cut', title: full,
      textContent: full.length > SEQ_HEAD + SEQ_TAIL
        ? full.slice(0, SEQ_HEAD) + '…' + full.slice(-SEQ_TAIL) : full }));
    const copy = copyButton('Copy', full, `Copy the sequence of ${shortName(String(design.design || ''))}`);
    // The row opens the structure on click; copying must not.
    copy.addEventListener('click', (event) => event.stopPropagation());
    cell.append(copy);
  } else if (column.kind === 'num') {
    cell.textContent = value == null ? '–' : tidy(Number(value));
  } else {
    cell.textContent = String(value ?? '–');
    if (key === 'campaign') cell.title = design.campaign_path || '';
  }
  return cell;
}

/** Both numbers, always. The review bundle counts every passed sequence, so a page
    that gave only the ranked count beside it would read as a bug. */
function drawResultsSummary(ready, passed, ranked, campaigns) {
  const box = $('results-summary');
  box.replaceChildren();
  if (!ready) return;
  if (!campaigns) return;
  if (!passed) {
    box.append('No sequences have passed yet.');
    return;
  }
  box.append(el('b', { textContent: (ranked
    ? `${plural(ranked, 'backbone')}, best sequence each`
    : 'No ranked shortlist yet') + ` · ${plural(passed, 'passed sequence')}` }));
  const bits = [];
  if (campaigns > 1) bits.push(`across ${campaigns} campaigns`);
  bits.push(resultsUi.rankedOnly
    ? `showing ${ranked} ranked`
    : `showing all ${passed}`);
  box.append(el('span', { className: 'dim', textContent: ' · ' + bits.join(' · ') }));
}

/** The chip. It carries the ranked count, like the project chips carry theirs. */
function drawResultChips(ready, passed, ranked) {
  const box = $('results-chips');
  box.replaceChildren();
  if (!ready || !passed) return;
  const on = resultsUi.rankedOnly;
  const chip = el('button', { className: 'chip' + (on ? ' is-on' : ''), type: 'button',
    title: 'One best sequence per backbone. Turn off to see every sequence that passed, siblings included' });
  // setAttribute, not a property: el() assigns properties, and aria-pressed is only an attribute.
  chip.setAttribute('aria-pressed', String(on));
  chip.append(document.createTextNode('Ranked only'));
  chip.append(el('span', { className: 'n', textContent: String(ranked) }));
  chip.onclick = () => { resultsUi.rankedOnly = !resultsUi.rankedOnly; drawResults(); };
  box.append(el('span', { className: 'eyebrow', textContent: 'Show' }), chip);
  if (state.sort.key) {
    const reset = el('button', { className: 'linky', type: 'button', textContent: 'Reset sort' });
    reset.onclick = () => { state.sort.key = null; state.sort.dir = 1; drawResults(); };
    box.append(reset);
  }
}

/** Nothing to list, and why. "No designs" is false when a filter is what emptied
    the list, and it sends the reader looking for work they have already done. */
function emptyResults(passed, ranked, campaigns) {
  if (passed && !ranked && resultsUi.rankedOnly) {
    const wrap = el('div', { className: 'empty' });
    wrap.append(`None of the ${passed} passed sequences are on a ranked shortlist yet. `);
    const all = el('button', { className: 'linky', type: 'button', textContent: `Show all ${passed}` });
    all.onclick = () => { resultsUi.rankedOnly = false; drawResults(); };
    wrap.append(all);
    return wrap;
  }
  if (!campaigns.length) {
    return el('div', { className: 'empty', textContent: resultsUi.project !== ALL_PROJECTS
      && $('result-job').value === ALL_CAMPAIGNS
      ? `No campaigns in ${projectLabel(resultsUi.project)}.`
      : 'No campaigns yet. Start one from the Design tab.' });
  }
  const gone = campaigns.filter(c => c.missing).length;
  const why = gone === campaigns.length
    ? 'The folder for this campaign has gone from disk, so there is nothing to list.'
    : 'No sequences have passed yet. A campaign that is still running adds to this as it goes; use Refresh to look again.';
  return el('div', { className: 'empty', textContent: why });
}

/** What the campaigns on screen were asked to do, by path. A campaign whose
    metadata is absent (an older server, a folder that predates the file) is held
    with every field null, which is "does not say" and nothing stronger. */
const metaOf = (campaign) => (campaign && campaign.meta) || {};

/** The distinct values of one meta field across the campaigns behind these rows.
    null is left out: a campaign that does not say what it is has not been shown
    to differ from the others, and claiming it did would be the same invention
    as claiming it matched. */
function metaValues(shown, field) {
  const paths = new Set(shown.map(d => d.campaign_path));
  const values = new Set();
  for (const campaign of resultsUi.campaigns) {
    if (!paths.has(campaign.path)) continue;
    const value = metaOf(campaign)[field];
    if (value != null && value !== '') values.add(value);
  }
  return [...values];
}

/** Sorting across campaigns by a metric that means something different in each.
    Said where it applies, not in a footnote. When the campaigns on screen differ
    in modality or in target, which is the case where the numbers are not on one
    scale at all, it says that first and by name. */
function drawSortCaveat(shown) {
  const column = RESULT_COLUMNS.find(c => c.key === state.sort.key);
  const many = new Set(shown.map(d => d.campaign_path)).size;
  if (!column || !column.across || many < 2) { note($('results-caveat'), ''); return; }

  const differ = [];
  const modalities = metaValues(shown, 'modality'), targets = metaValues(shown, 'target');
  if (modalities.length > 1) differ.push(`modality (${modalities.join(' and ')})`);
  if (targets.length > 1) differ.push(`target (${targets.join(' and ')})`);

  // A campaign the reader has not been verified on may also have different column
  // names or value formats, so its figures are unverified before they are compared.
  const paths = new Set(shown.map(d => d.campaign_path));
  const unverified = resultsUi.campaigns.filter(c => paths.has(c.path) && metaOf(c).experimental === true);

  let sharper = differ.length
    ? `These campaigns differ in ${differ.join(' and in ')}, so their i_pTM, i_pDAE and binder pLDDT `
      + 'are not on one scale and this order does not say which design is better. '
    : '';
  if (unverified.length) {
    const why = [...new Set(unverified.flatMap(c => metaOf(c).experimental_why || []))];
    sharper += `${unverified.map(c => c.name).join(' and ')} ${unverified.length === 1 ? 'is' : 'are'} read by an `
      + `experimental reader${why.length ? ` (${why.join('; ')})` : ''}, so `
      + `${unverified.length === 1 ? 'its' : 'their'} figures are unverified for this kind of campaign `
      + "and want checking against the campaign's own CSVs before they are compared. ";
  }
  note($('results-caveat'),
    `Sorted by ${column.label} across ${many} campaigns. ` + sharper
    + "Rank is an ordinal inside one campaign's own pool, "
    + 'and the campaigns used different filters, targets and binder lengths, so i_pTM, i_pDAE and rank '
    + 'are only comparable within one target and modality. Sort by Campaign to compare inside one.', 'warn');
}

/** Modality, target and the epitope, in words, on one campaign's line.

    Three states for the hotspots and they must not merge: set (here they are),
    not set (no intended epitope, so anything derived from one is not measured,
    which is not zero), and not recorded (the metadata did not say). The middle
    one is named outright, since a blank would read as "bad". */
function metaTags(campaign) {
  const meta = metaOf(campaign);
  const box = el('span', { className: 'rv-meta' });
  const tag = (text, className, title) =>
    box.append(el('span', { className: 'rv-tag ' + className, textContent: text, title: title || '' }));

  tag(meta.modality ? meta.modality : 'modality not recorded',
    meta.modality ? 'modality' : 'unknown',
    'Binder modality. i_pTM, i_pDAE and binder pLDDT are comparable only between campaigns of the same modality and target');
  tag(meta.target ? 'target ' + meta.target : 'target not recorded',
    meta.target ? 'target' : 'unknown');
  if (meta.hotspots_set === true) {
    tag(meta.hotspots ? 'hotspots ' + meta.hotspots : 'hotspots set', 'target',
      'The residues this campaign was asked to bind');
  } else if (meta.hotspots_set === false) {
    tag('no hotspots set', 'unset',
      'This campaign had no intended epitope, so any figure derived from a hotspot is not measured here. It is not zero');
  } else {
    tag('hotspots not recorded', 'unknown', 'The campaign metadata does not say whether hotspots were set');
  }
  // Three states, kept apart. true: say so. false: say nothing, because a "verified"
  // pill would claim more than the data supports and the absence is the message.
  // null: say nothing either; it only occurs for a folder that is gone, which the row
  // already reports.
  if (meta.experimental === true) {
    const why = (meta.experimental_why || []).join('; ');
    tag('experimental reader', 'experimental',
      'Unverified for this kind of campaign' + (why ? ': ' + why : '') + '. '
      + 'The reader was checked only on de novo ("binder") campaigns written by BindCraft 2 v1.0.3. '
      + 'This one is still read, on the same assumptions, but its column names, value formats and '
      + "metadata keys may differ, so check its numbers against the campaign's own CSVs before relying on them. "
      + 'This is not a judgement on the campaign.');
  }
  return box;
}

/** "Review: built 3 h ago · current", the same words for a campaign's strip and a
    project's. `none` is what to say when there is no review; `project` words the stale reason for one. */
function reviewWords(review, { none = 'none', project = false } = {}) {
  const words = el('span', {});
  words.append('Review: ');
  if (!review) {
    words.append(none);
  } else if (review.state === 'done') {
    const when = reviewWhen(review.built_at);
    words.append(when ? `built ${when} · ` : 'built · ');
    words.append(el('span', { className: review.stale ? 'is-stale' : 'is-current',
      title: review.stale ? staleWords(review, project) : 'Built from the designs as they are now',
      textContent: review.stale ? 'stale' : 'current' }));
  } else {
    words.append(review.state === 'failed' ? 'failed' : review.state || 'unknown');
    if (review.error) words.title = review.error;
  }
  return words;
}

/** The way into a finished review from a strip, or null when there is none to open. */
function openReviewLink(review) {
  if (!(review && review.state === 'done' && review.bundle_url)) return null;
  const open = el('a', { className: 'ghost', textContent: 'Open review →',
    href: review.bundle_url, target: '_blank', rel: 'noopener' });
  return el('span', { className: 'actions' }, open);
}

/** Per campaign: is there a review, how old, and the way in. Reads the review
    state /api/designs already carries, so this costs no call of its own. */
function drawReviewStrip(ready) {
  const box = $('results-reviews');
  box.replaceChildren();
  if (!ready || !resultsUi.campaigns.length) return;
  for (const campaign of resultsUi.campaigns) {
    const line = el('div', { className: 'rv-line' });
    line.append(el('b', { textContent: campaign.name }));
    if (campaign.missing) {
      line.classList.add('is-missing');
      line.append(el('span', { textContent: 'The folder has gone from disk, so nothing is listed from it.' }));
      box.append(line);
      continue;
    }
    const counts = campaign.counts || {};
    line.append(el('span', { className: 'counts',
      textContent: `${counts.passed ?? 0} passed · ${counts.ranked ?? 0} ranked` }));
    line.append(metaTags(campaign));

    const review = campaign.review;
    line.append(reviewWords(review));
    const open = openReviewLink(review);
    if (open) line.append(open);
    box.append(line);
  }
  // One fixed line, true of every campaign above, and the reason there is no
  // decisions column: they are kept by the review, in this browser, and the
  // server never sees them.
  box.append(el('p', { className: 'help',
    textContent: 'Keep, maybe and reject decisions are stored in this browser for this address, '
      + 'inside each review. They are not shown here.' }));
}

/** The project's own line above the campaigns', when Results is showing a project that has a
    review or could have one. It reads what the Review tab already fetched (`review.projects`,
    from /api/review/projects, loaded each time Results is opened), so it costs no call and no
    poll, and when that list is missing the strip is simply not drawn. A project with no review
    points at the Review tab and offers no build here: building lives in one place. */
function drawProjectStrip() {
  const box = $('results-project');
  box.replaceChildren();
  const { projects } = state.review;
  const label = resultsUi.project;
  if (resultsUi.loading || resultsUi.error || label === ALL_PROJECTS || label === '' || !projects) return;
  const entry = projects.find(p => p.project === label);
  if (!entry) return;                              // fewer than two campaigns: nothing to pool

  const line = el('div', { className: 'rv-line rv-project' });
  line.append(el('b', { textContent: entry.project }));
  line.append(el('span', { className: 'rv-meta' },
    el('span', { className: 'rv-tag modality', textContent: 'project review',
      title: 'One review across every campaign filed under this project' })));
  const designs = entry.designs || {};
  const idle = entry.members - entry.buildable;
  line.append(el('span', { className: 'counts',
    textContent: plural(entry.members, 'campaign') + (idle ? ` (${idle} with no finished designs)` : '')
      + ` · ${designs.passed ?? 0} passed · ${designs.ranked ?? 0} ranked, pooled` }));

  const review = entry.review;
  const words = reviewWords(review, { none: entry.can_build ? 'none yet' : 'none yet, and it cannot be built yet',
                                      project: true });
  if (!review && !entry.can_build && entry.why_not) words.title = entry.why_not;
  line.append(words);

  const open = openReviewLink(review);
  if (open) {
    line.append(open);
  } else if (!review) {
    const go = el('button', { type: 'button', className: 'ghost', textContent: 'Review tab →',
      title: 'Projects are reviewed from the Review tab' });
    go.onclick = () => show('review');
    line.append(el('span', { className: 'actions' }, go));
  }
  box.append(line);
}

// ── export

const CSV_COLUMNS = ['campaign', 'design', 'family', 'siblings', 'length', 'i_pTM', 'i_pDAE',
  'Binder_Free_Cysteines', 'Binder_Net_Charge', 'Binder_Mass_kDa', 'ranked', 'rank', 'Binder_Sequence'];

/** One CSV field. Text that a spreadsheet would run as a formula is defused, so a
    campaign named "=…" cannot do anything when the file is opened. Numbers are
    left alone: a net charge of -2 is a number, not a formula. */
function csvField(value) {
  if (value == null) return '';
  let text = typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value);
  if (typeof value === 'string' && /^[=+\-@\t\r]/.test(text)) text = "'" + text;
  return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}

/** The rows on screen, in the order on screen, built from what is already in hand.
    Whole values, not the rounded ones in the table, and the full design id and
    sequence, which the table shortens or leaves out. */
function resultsCsv() {
  const lines = [CSV_COLUMNS.join(',')];
  for (const design of shownResults()) {
    lines.push(CSV_COLUMNS.map(key => csvField(design[key])).join(','));
  }
  return lines.join('\r\n') + '\r\n';
}

function exportResults() {
  const count = shownResults().length;
  if (!count) return;
  const slug = resultScope().replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '') || 'designs';
  const name = `binderscope_${slug}_${resultsUi.rankedOnly ? 'ranked' : 'passed'}_${new Date().toISOString().slice(0, 10)}.csv`;
  const url = URL.createObjectURL(new Blob([resultsCsv()], { type: 'text/csv;charset=utf-8' }));
  const link = el('a', { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast(`Exported ${plural(count, 'design')} to ${name}`);
}

async function showDesign(design) {
  const box = $('design-box');
  box.classList.remove('hidden');
  $('design-name').textContent = design.design ? '— ' + design.design : '';
  $('design-sequence').textContent = design.Binder_Sequence || '(no sequence recorded)';

  // /api/designs names it `structure`; the older results endpoint called it `_structure`.
  const structure = design.structure || design._structure;
  if (!structure) {
    $('design-note').textContent = 'No structure file for this design.';
    return;
  }
  if (!state.designViewer) {
    state.designViewer = $3Dmol.createViewer($('design-viewer'), { backgroundColor: canvasColour() });
  }
  const viewer = state.designViewer;
  viewer.setBackgroundColor(canvasColour());
  viewer.clear();
  let text;
  try { text = await api('/api/structure?path=' + encodeURIComponent(structure)); }
  catch (problem) { $('design-note').textContent = problem.message; return; }

  viewer.addModel(text, /\.(cif|mmcif)$/i.test(structure) ? 'cif' : 'pdb');
  paintDesign();
  viewer.zoomTo();
  viewer.render();
  $('design-note').textContent = 'Drag to rotate, scroll to zoom';
}

/** AlphaFold's four confidence bands, highest first so the first match wins.
    The cut points are AlphaFold's own, and the words are what the legend
    prints: a reader who cannot separate the colours still gets the scale from
    their order. */
const PLDDT_BANDS = [
  { from: 90, token: '--plddt-90' },
  { from: 70, token: '--plddt-70' },
  { from: 50, token: '--plddt-50' },
  { from: -Infinity, token: '--plddt-0' },
];

let designColouring = 'chain';
function paintDesign() {
  const viewer = state.designViewer;
  if (!viewer) return;
  if (designColouring === 'plddt') {
    // AlphaFold writes per-residue confidence into the B-factor column.
    // Four named bands, not a continuous ramp. The ramp this replaced ran red
    // through green to blue, which puts the low/confident line — the one that
    // decides whether a region can be trusted — at ΔE2000 2.7 under
    // protanopia, against a just-noticeable difference of about 2.3. These
    // bands put the same line at 54. They are also the palette the review
    // bundle uses, so the two viewers stop disagreeing.
    const root = getComputedStyle(document.documentElement);
    const bands = PLDDT_BANDS.map(b => ({ from: b.from, colour: root.getPropertyValue(b.token).trim() }));
    viewer.setStyle({}, { cartoon: { colorfunc: (atom) => {
      const b = atom.b;
      for (const band of bands) if (b >= band.from) return band.colour;
      return bands[bands.length - 1].colour;
    } } });
  } else {
    viewer.setStyle({}, { cartoon: { color: '#8f98a8' } });
    // The binder is the last chain BindCraft writes; colour it so it stands out.
    viewer.setStyle({ chain: 'B' }, { cartoon: { color: '#e8590c' } });
  }
  // Driven from here rather than from the .dmode click, so the key cannot
  // desync from what is actually drawn.
  $('plddt-key').classList.toggle('hidden', designColouring !== 'plddt');
}


// ───────────────────────────────────────────────────────────── review

/** Review hands a finished campaign to a deeper analysis that places every design on
    the full-length target. The campaign already records the construct, its chains and
    the hotspots, so this tab reads them back as facts and asks for one thing only: the
    full-length structure. The API may not exist on an older server; every call here
    says so plainly rather than drawing an empty list. */

const REVIEW_BUSY = ['queued', 'running'];
const RV_TAGS = { trajectories: 'trajectories', refolded: 'refolded', passed: 'passed', ranked: 'ranked' };
const RV_TITLES = {
  trajectories: 'Design attempts, from the 1_Trajectories folder',
  refolded: 'Entries in the 2_Refolded folder',
  passed: 'Designs that passed BindCraft\'s filters',
  ranked: 'Designs in the final ranking, 3_Ranked',
};

const KIND_TAG = { experimental: 'experimental structure', alphafold: 'AlphaFold model', predicted: 'predicted model' };

/** A project's review is named by its slug, which begins with "@" and which no
    campaign name can (campaign.py, review.PROJECT_NAME). Every review call goes
    through rvUrl, so this one test is what lets the panel serve both. */
const isProject = (name) => typeof name === 'string' && name.startsWith('@');

/** The review's address by name, for a route that takes a campaign's name or a project's
    slug in the same slot: `/reference` and `/chains`. The server finds the construct for
    either (for a project, the one its members share), so the page asks the same way. */
const rvNameUrl = (name) => '/api/review/' + encodeURIComponent(name);

/** The review's own address. A campaign's is the shared one; a project's sits under
    `/project/`, where the routes that only a project has (its form, build, log, discard)
    live. */
const rvUrl = (name) => isProject(name)
  ? '/api/review/project/' + encodeURIComponent(name)
  : rvNameUrl(name);

/** What to call the review in a sentence: the project's label, not its slug. */
function reviewLabel(name) {
  const detail = state.review.detail;
  return isProject(name) && detail && detail.project ? detail.project : name;
}

/** The honest wording for a server that does not have the Review API (yet). */
function reviewMissing(problem) {
  return /^404\b|not found/i.test(problem.message)
    ? 'Review is not available on this server yet.'
    : 'Could not load reviews: ' + problem.message;
}

function reviewWhen(iso) {
  const when = new Date(iso);
  if (!iso || isNaN(when)) return '';
  return when.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/** "147 trajectories · 341 refolded · ..." with a hover for what each folder is. */
function designCounts(designs) {
  const box = el('span', { className: 'meta counts' });
  for (const key of Object.keys(RV_TAGS)) {
    if (designs && designs[key] != null) {
      const word = key === 'trajectories' && designs[key] === 1 ? 'trajectory' : RV_TAGS[key];
      box.append(el('span', { textContent: `${designs[key]} ${word}`, title: RV_TITLES[key] }));
    }
  }
  return box;
}

/** Why a built review is out of date, in words. */
function staleWords(review, project = false) {
  if (review.stale_reason === 'code') {
    return 'The review tool has been updated since this was built. Rebuild to use the new checks.';
  }
  return project
    ? 'The campaigns in this project have changed since the review was built (more designs, or a campaign added or removed), so it does not match them.'
    : 'This campaign has produced designs since the review was built, so they are not in it.';
}

const reviewState = (row) => (row && row.review ? row.review.state : null);
const reviewBusy = (row) => REVIEW_BUSY.includes(reviewState(row));

async function loadReviewList() {
  const review = state.review;
  // Asked together and judged apart: the project list failing must not empty the campaigns, nor the reverse.
  const [campaigns, projects, brought] = await Promise.allSettled(
    [api('/api/review'), api('/api/review/projects'), api('/api/review/imports')]);
  if (campaigns.status === 'fulfilled') {
    review.list = campaigns.value;
    review.unavailable = '';
  } else {
    review.list = null;
    review.unavailable = reviewMissing(campaigns.reason);
  }
  if (projects.status === 'fulfilled') {
    review.projects = projects.value;
    review.projectsProblem = '';
  } else {
    review.projects = null;
    // A server without the route has no project reviews to offer, which is not news. Anything else is.
    review.projectsProblem = /^404\b|not found/i.test(projects.reason.message)
      ? '' : 'Could not load project reviews: ' + projects.reason.message;
  }
  // A server without the route has nothing imported, which is not news either.
  review.imports = brought.status === 'fulfilled' ? brought.value : null;
  drawReviewList();
  drawImports();
  drawBridge();
  drawProjectStrip();
}

/** What a panel needs to redraw for: the state of the build and whether it is out of date. */
const reviewSig = (review) => (review ? review.state + (review.stale ? '+stale' : '') : '');

/** Bring the panel in line with the list: a build that started, finished or went stale
    somewhere else, or while this page was in another tab. */
async function syncPanel() {
  const review = state.review;
  if (review.syncing) return;
  review.syncing = true;
  try {
    const was = review.detail && review.detail.review;
    await loadReviewList();
    const row = reviewRow(review.chosen);
    if (!row || !review.detail) return;
    if (reviewSig(row.review) === reviewSig(was)) return;
    await chooseReview(review.chosen, { quiet: true, keepForm: true });
    const now = row.review && row.review.state;
    if (was && REVIEW_BUSY.includes(was.state)) {
      if (now === 'done') toast('The review is built');
      else if (now === 'failed') toast('The review build failed. The log says why.', true);
    }
  } finally { review.syncing = false; }
}

const reviewRow = (name) => isProject(name)
  ? (state.review.projects || []).find(r => r.slug === name) || null
  : (state.review.list || []).find(r => r.campaign === name) || null;

/** The chip, the buttons and the one line of explanation for a row's review: the same
    story for a campaign and for a project, so one function tells it. `key` is what
    chooseReview takes (a campaign's name or a project's slug) and `subject` the noun
    on the first button. `canBuild` false drops every button that would start a build;
    a review that already exists can still be opened. */
function reviewRowParts(row, key, subject, canBuild = true) {
  const review = row.review;
  const chip = el('span', { className: 'state-chip' });
  const project = subject === 'project';
  let primary = null, secondary = null, extra = '', extraKind = '';
  const rebuild = (isPrimary) => canBuild
    ? reviewButton('Rebuild', () => chooseReview(key, { formOpen: true }), isPrimary) : null;

  if (!review) {
    chip.className += ' state-none'; chip.textContent = 'not reviewed';
    if (canBuild) primary = reviewButton('Review this ' + subject, () => chooseReview(key), true);
  } else if (review.state === 'queued' || review.state === 'running') {
    chip.className += ' state-running';
    chip.textContent = review.state === 'queued' ? 'waiting' : 'building';
    primary = reviewButton('Watch the build', () => chooseReview(key), true);
  } else if (review.state === 'failed') {
    chip.className += ' state-failed'; chip.textContent = 'failed';
    extra = review.error || 'The last build did not finish.'; extraKind = 'bad';
    primary = rebuild(true);
  } else if (review.stale) {
    chip.className += ' state-stale'; chip.textContent = 'out of date';
    extra = staleWords(review, project); extraKind = 'warn';
    primary = rebuild(true);
    secondary = reviewLink(review.bundle_url, 'Open the old one');
  } else {
    chip.className += ' state-done';
    chip.textContent = review.built_at ? 'built ' + reviewWhen(review.built_at) : 'built';
    primary = reviewLink(review.bundle_url, 'Open review', true);
    secondary = rebuild(false);
  }
  return { chip, primary, secondary, extra, extraKind };
}

/** A finished review whose reference is not to be trusted outside the construct says
    so on the row that links to it, not only inside the panel. */
function addDoubt(parts, review) {
  const doubt = review && review.state === 'done' ? confidenceLine(review.reference_check) : null;
  if (!doubt) return;
  parts.extra = parts.extra ? parts.extra + ' ' + doubt.text : doubt.text;
  if (doubt.warn) parts.extraKind = 'warn';
}

/** The runs brought in from elsewhere, under the import form.

    Each says where it really is, because that is the thing a person cannot see
    from the campaign list: everything else about an imported run looks exactly
    like a campaign this service started, which is the point. */
function drawImports() {
  const box = $('im-list');
  if (!box) return;
  box.replaceChildren();
  const rows = state.review.imports;
  if (!rows || !rows.length) return;
  box.append(el('h3', { className: 'sub-h', textContent: plural(rows.length, 'imported run') }));
  for (const row of rows) {
    const bits = [
      el('b', { textContent: row.name }),
      el('span', { className: 'dim', textContent: row.path, title: row.path }),
    ];
    // A run whose folder has gone: the link is still here and the campaign has
    // vanished from the list above, which on its own looks like a bug.
    if (!row.here) bits.push(el('span', { className: 'warn', textContent: 'the folder is no longer there' }));
    bits.push(el('button', { className: 'ghost', textContent: 'Forget',
      title: 'Remove the link. Your run is not touched.',
      onclick: () => forgetImport(row.name) }));
    box.append(el('div', { className: 'im-row' }, bits));
  }
}

/** Link a run that BindCraft wrote elsewhere, then show it in the list above. */
async function importRun() {
  const path = $('im-path').value.trim();
  if (!path) return note($('im-note'), 'Give the path of the run folder.', 'bad');
  const button = $('im-add');
  button.disabled = true;
  note($('im-note'), 'Looking at that folder\u2026');
  try {
    const row = await api('/api/review/import', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ path, name: $('im-name').value.trim(), construct: $('im-construct').value.trim() }),
    });
    $('im-path').value = ''; $('im-name').value = ''; $('im-construct').value = '';
    note($('im-note'), '');
    toast(`Imported ${row.name}`);
    await loadReviewList();
    // Straight into its panel: importing is only ever the first half of what
    // the person came to do.
    chooseReview(row.name);
  } catch (problem) {
    note($('im-note'), problem.message, 'bad');
  } finally {
    button.disabled = false;
  }
}

/** Forget an imported run. The link goes; their run is not touched. */
async function forgetImport(name) {
  if (!confirm(`Forget ${name}? The link here goes; your run folder is not touched.`)) return;
  try {
    await api('/api/review/import/' + encodeURIComponent(name), { method: 'DELETE' });
    toast(`Forgot ${name}`);
    await loadReviewList();
  } catch (problem) { toast(problem.message, true); }
}

function drawReviewList() {
  const box = $('review-list');
  box.replaceChildren();
  const { list, unavailable, projects, projectsProblem, chosen } = state.review;

  // The panel goes when what it describes cannot be listed; a project's does not depend on the campaign list.
  if (unavailable && !(isProject(chosen) && projects)) $('review-panel').classList.add('hidden');
  if (!list && !unavailable) return;

  // Projects first: they are the larger step, and a person who has several campaigns
  // of one project should meet that before picking one campaign out of it.
  const sectioned = !!((projects && projects.length) || projectsProblem);
  if (sectioned) {
    box.append(el('div', { className: 'group-project' }, [
      el('span', { className: 'name', textContent: 'Project reviews' }),
      el('span', { className: 'summary', textContent: 'Several campaigns, one review' }),
    ]));
    box.append(el('p', { className: 'help', textContent:
      'A project review pools the campaigns of a project into a single review, so their designs '
      + 'can be compared and triaged together.' }));
    if (projectsProblem) box.append(el('div', { className: 'empty', textContent: projectsProblem }));
    for (const row of projects || []) box.append(projectRow(row));
    box.append(el('div', { className: 'group-project' }, [
      el('span', { className: 'name', textContent: 'Campaigns' }),
      el('span', { className: 'summary', textContent: 'One review each' }),
    ]));
  }

  if (unavailable) {
    box.append(el('div', { className: 'empty', textContent: unavailable }));
    return;
  }
  if (!list.length) {
    box.append(el('div', { className: 'empty',
      textContent: 'No campaign has designs to review yet. One appears here as soon as it has some.' }));
    return;
  }

  for (const row of list) {
    const review = row.review;
    const line = el('div', {});
    const parts = reviewRowParts(row, row.campaign, 'campaign');

    const acts = el('span', { className: 'acts' });
    if (row.job_id != null) {
      const results = reviewButton('Results', () => {
        show('results'); $('result-job').value = String(row.job_id); loadResults();
      });
      results.title = 'The sequences that passed, as they came out';
      acts.append(results);
    }
    if (parts.secondary) acts.append(parts.secondary);
    acts.append(parts.primary);
    line.className = 'job rv-row';
    line.append(parts.chip, el('b', { textContent: row.campaign }), acts, designCounts(row.designs));

    // A campaign that is still designing can be reviewed, but the answer is only
    // about the designs that exist right now. Saying so here is cheaper than
    // letting someone draw a conclusion from a third of a campaign.
    if (stillDesigning(row) && !parts.extra) {
      parts.extra = stillDesigningWords(row.designs && row.designs.ranked);
      parts.extraKind = 'warn';
    }
    addDoubt(parts, review);
    if (parts.extra) line.append(el('div', { className: 'line ' + parts.extraKind, textContent: parts.extra }));
    box.append(line);
  }
}

/** The warning a review built from half a campaign needs, for a campaign and for a member of a project. */
function stillDesigningWords(made) {
  return 'Still designing. A review built now covers only the '
       + (made ? made + ' design' + (made === 1 ? '' : 's') : 'designs') + ' so far.';
}

/** One project in the list: its label, how many campaigns, what is pooled, where its review stands. */
function projectRow(row) {
  const parts = reviewRowParts(row, row.slug, 'project', row.can_build !== false);
  const line = el('div', { className: 'job rv-row' });
  const acts = el('span', { className: 'acts' });
  // A project that cannot be built still shows who is in it, which is usually what explains why.
  if (!parts.primary) acts.append(reviewButton('See the campaigns', () => chooseReview(row.slug)));
  if (parts.secondary) acts.append(parts.secondary);
  if (parts.primary) acts.append(parts.primary);

  const counts = el('span', { className: 'meta counts' });
  const said = row.members === 1 ? '1 campaign' : `${row.members} campaigns`;
  counts.append(el('span', { textContent: row.buildable != null && row.buildable < row.members
    ? `${said}, ${row.buildable} with designs` : said }));
  const designs = row.designs || {};
  if (designs.passed != null) counts.append(el('span', { textContent: `${designs.passed} passed`,
    title: 'Designs that passed BindCraft’s filters, pooled across the campaigns that can go in. ' + RV_TITLES.passed }));
  if (designs.ranked != null) counts.append(el('span', { textContent: `${designs.ranked} ranked`, title: RV_TITLES.ranked }));

  line.append(parts.chip, el('b', { textContent: row.project }), acts, counts);

  // Why it cannot be built, said where it is seen. An existing review keeps its own notes beside it.
  if (row.can_build === false && row.why_not) {
    parts.extra = parts.extra ? parts.extra + ' ' + row.why_not : row.why_not;
    parts.extraKind = parts.extraKind || 'warn';
  }
  addDoubt(parts, row.review);
  if (parts.extra) line.append(el('div', { className: 'line ' + parts.extraKind, textContent: parts.extra }));
  return line;
}

/** True while the campaign behind this row is still producing designs. The jobs
    list is polled on its own timer, so this is current whichever tab is open. */
function stillDesigning(row) {
  if (row.job_id == null) return false;
  const job = (state.jobs || []).find(j => j.id === row.job_id);
  return !!job && (job.state === 'running' || job.state === 'queued');
}

function reviewButton(label, onclick, primary = false) {
  const button = el('button', { className: primary ? 'go' : 'ghost', textContent: label });
  button.onclick = onclick;
  return button;
}

/** The bundle is a multi-page site of its own, so it opens in a tab of its own. */
function reviewLink(url, label, primary = false) {
  return el('a', {
    className: primary ? 'go' : 'ghost', textContent: label + ' ↗',
    href: url, target: '_blank', rel: 'noopener',
  });
}

// ── the strip on the Results tab

function drawBridge() {
  const box = $('results-bridge');
  box.replaceChildren();
  const { list, unavailable } = state.review;
  const jobId = Number($('result-job').value);

  // Nothing to say until we know the server has reviews and which campaign this is.
  if (unavailable || !jobId || list == null) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const row = list.find(r => r.job_id === jobId);
  const text = el('span', { className: 'text' });
  text.append(el('b', { textContent: 'Results' }), ' is immediate, and works while designs are still being made. ',
              el('b', { textContent: 'Review' }), ' is a build of a couple of minutes that places the designs '
              + 'on the full-length target and groups them into backbone families.');
  box.append(text);

  if (!row) {
    text.append(' It is offered once there are designs.');
    return;
  }
  const review = row.review;
  const waiting = reviewBusy(row);
  if (review && review.state === 'done' && !review.stale) {
    box.append(reviewLink(review.bundle_url, 'Open review', true));
  } else {
    const label = waiting ? 'Watch the build' : !review ? 'Review this campaign' : 'Rebuild the review';
    box.append(reviewButton(label, () =>
      chooseReview(row.campaign, { formOpen: !waiting && !!review }), true));
  }
}

// ── the panel for one campaign

async function chooseReview(name, { formOpen = false, keepForm = false, quiet = false } = {}) {
  const review = state.review;
  if (review.chosen !== name) { review.detail = null; review.formOpen = false; }
  review.chosen = name;
  if (!keepForm) review.formOpen = formOpen;
  if (!quiet) show('review');

  let detail;
  try { detail = await api(rvUrl(name)); }
  catch (problem) {
    $('review-panel').classList.add('hidden');
    return toast(reviewMissing(problem), true);
  }
  const fresh = !review.detail || (review.detail.slug || review.detail.campaign) !== (detail.slug || detail.campaign);
  review.detail = detail;

  drawFacts(detail);
  drawMembers(detail);
  if (fresh || !quiet) setUpReferenceForm(detail);
  drawReviewMode();
  $('review-panel').classList.remove('hidden');
  $('rv-name').textContent = '— ' + (isProject(name) ? detail.project + ' · project' : name);
  if (!quiet) $('review-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  pullReviewLog();
}

/** What the campaign already knows, read back so nobody types it twice. */
function drawFacts(detail) {
  const box = $('rv-facts');
  box.replaceChildren();
  const add = (label, ...value) => {
    box.append(el('dt', { textContent: label }), el('dd', {}, value));
  };

  const construct = detail.construct || {};
  const what = [];
  if (construct.name) what.push(construct.name);
  if (construct.chains) what.push('chain ' + construct.chains);
  if (construct.residues) what.push('residues ' + construct.residues);
  const cell = [el('span', { textContent: what.join(', ') || 'recorded in the campaign' })];
  if (construct.path) cell.push(el('span', { className: 'path', textContent: construct.path }));
  if (construct.exists === false) {
    cell.push(el('span', { className: 'bad',
      textContent: 'This file is no longer there, so the review cannot be built.' }));
  }
  add('Designed against', ...cell);

  const project = isProject(detail.slug);
  // A project has no hotspots of its own, and "none recorded" would be a claim about
  // campaigns this page has not looked at. Its campaigns' sites are in their own reviews.
  if (project) {
    // nothing: the panel says what a project is made of in the list below
  } else if (detail.hotspots) {
    add('Binding site', el('span', { className: 'mono', textContent: detail.hotspots }),
        el('span', { className: 'path', textContent: 'The hotspots you marked. Designs are checked against them.' }));
  } else {
    add('Binding site', el('span', { className: 'warn', textContent: 'No hotspots recorded.' }),
        el('span', { className: 'path',
          textContent: 'The review still runs, but the check that a design landed on the intended site is switched off.' }));
  }

  const counts = designCounts(detail.designs);
  add(project ? 'Pooled designs' : 'Designs so far', counts);
  if (project) {
    box.lastChild.append(el('span', { className: 'path',
      textContent: 'Across the campaigns below that can go in. A campaign left out is not counted.' }));
  }

  const kind = [detail.modality, detail.version].filter(Boolean).join(' · ');
  if (kind) {
    add('Made with', kind);
  }
  if (/BindCraft ?2/i.test(detail.version || '')) {
    box.lastChild.append(el('span', { className: 'path',
      textContent: 'BindCraft 2 does not report every score the review can use; checks that need a missing one are switched off.' }));
  }
}

/** The campaigns a project's review would pool, each with what it would put in. This is
    what a person needs to see before building, because it says what is going in. */
function drawMembers(detail) {
  const box = $('rv-members');
  box.replaceChildren();
  if (!isProject(detail.slug)) return;
  const members = detail.members || [];
  box.append(el('h3', { className: 'sub-h', textContent: 'Campaigns in this review' }));
  if (!members.length) {
    box.append(el('div', { className: 'empty', textContent: 'No campaigns are filed under this project.' }));
    return;
  }
  for (const member of members) {
    const line = el('div', { className: 'job rv-row' });
    const chip = el('span', { className: 'state-chip state-' + member.state, textContent: member.state || 'unknown' });
    line.append(chip, el('b', { textContent: member.name }), designCounts(member.designs));
    let extra = '', kind = '';
    if (!member.buildable) {
      extra = 'Left out. ' + (member.why_not || 'It cannot be reviewed yet.'); kind = 'warn';
    } else if (REVIEW_BUSY.includes(member.state)) {
      extra = stillDesigningWords(member.designs && member.designs.ranked); kind = 'warn';
    }
    if (extra) line.append(el('div', { className: 'line ' + kind, textContent: extra }));
    box.append(line);
  }
}

/** One of: done (go and look), running, failed, or the setup form. */
function drawReviewMode() {
  const { detail, formOpen } = state.review;
  const rv = detail.review;
  const status = rv ? rv.state : null;
  const busy = REVIEW_BUSY.includes(status);
  const done = status === 'done';
  const failed = status === 'failed';

  $('rv-build').classList.toggle('hidden', !busy);
  $('rv-done').classList.toggle('hidden', !done);
  $('rv-failed').classList.toggle('hidden', !failed);
  const blocked = detail.can_build === false;
  $('rv-blocked').classList.toggle('hidden', !blocked || busy);
  $('rv-blocked').textContent = detail.why_not
    || (isProject(detail.slug) ? 'This project cannot be reviewed yet.' : 'This campaign cannot be reviewed yet.');
  $('rv-form').classList.toggle('hidden', busy || blocked || (done && !formOpen));
  $('rv-log-box').classList.toggle('hidden', !(busy || failed || (done && formOpen)));

  if (busy) drawBuilding(status);
  if (done) drawDone(detail);
  if (failed) drawFailed(detail);

  $('rv-start').textContent = rv ? 'Rebuild the review' : 'Build the review';
  $('rv-start').disabled = false;
  // Said by the button that starts it, once, so nobody finds it out after building.
  $('rv-carry').classList.toggle('hidden', !isProject(detail.slug));
}

function drawBuilding(status) {
  $('rv-build-title').textContent = status === 'queued'
    ? 'Waiting for a free slot' : 'Building the review';
  $('rv-build-step').textContent = '';
  $('rv-track').classList.add('hidden');
  $('rv-spin').classList.remove('off');
}

function drawDone(detail) {
  const rv = detail.review;
  const box = $('rv-done');
  box.replaceChildren();

  const built = rv.built_at ? 'Built ' + reviewWhen(rv.built_at) : 'Built';
  const none = !!(detail.reference && detail.reference.none);
  const using = none ? ' without a full-length reference'
    : detail.reference && detail.reference.path
    ? ` on ${detail.reference.path.split('/').pop()}` + (detail.reference.chain ? `, chain ${detail.reference.chain}` : '')
    : '';
  if (rv.stale) {
    box.append(el('p', { className: 'note warn', textContent: `${built}${using}. ${staleWords(rv, isProject(detail.slug))}` }));
  } else {
    box.append(el('p', { className: 'note good', textContent: `${built}${using}.` }));
  }

  // Which chain was the target and what it was checked against, as the build did it: a reader should not have to guess.
  const comp = rv.composition;
  if (comp && comp.summary && !none) {
    box.append(el('p', { className: 'help chains-built' }, [el('b', { textContent: 'Chains. ' }), comp.summary + '.']));
  }

  // The caveat travels with the link. Whoever opens this review next week should meet
  // it before the findings, not have to remember it.
  const caveat = confidenceBox(rv.reference_check, { built: true });
  if (caveat) box.append(caveat);

  const open = el('div', { className: 'open-row' });
  if (rv.stale) {
    open.append(reviewButton('Rebuild the review', () => { state.review.formOpen = true; drawReviewMode();
      $('rv-form').scrollIntoView({ behavior: 'smooth', block: 'center' }); }, true));
    open.append(reviewLink(rv.bundle_url, 'Open the old one'));
  } else {
    open.append(reviewLink(rv.bundle_url, 'Open the review', true));
  }
  open.append(el('span', { className: 'help', textContent: 'Opens in a new tab.' }));
  box.append(open);

  const inside = el('ul', { className: 'inside' });
  for (const [name, what] of [
    ['Family board', 'designs grouped by backbone, so you can see how many different ideas you have'],
    ['Design inspector', none ? 'one design at a time, with its interface and scores'
                              : 'one design at a time, with its checks against the full-length protein'],
    ['Binding-mode map', 'where on the target the designs land, and whether you covered one site or several'],
    ['Structure query', 'rules to narrow the designs, and to find the ones that narrowly missed BindCraft’s filters'],
  ]) inside.append(el('li', {}, [el('b', { textContent: name }), ' — ' + what]));
  box.append(inside);

  box.append(el('p', { className: 'help', textContent:
    'Everything there is a prediction, and counts are of families, not sequences. A check whose '
    + 'data are missing is switched off rather than guessed; the review’s overview page lists which.' }));

  const actions = el('div', { className: 'done-actions' });
  if (!rv.stale) {
    actions.append(reviewButton('Rebuild with a different reference…', () => {
      state.review.formOpen = true; drawReviewMode();
      $('rv-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
    }));
  }
  const remove = reviewButton('Remove this review', removeReview);
  remove.title = isProject(detail.slug)
    ? 'Deletes the built pages. The campaigns themselves are not touched.'
    : 'Deletes the built pages. The campaign itself is not touched.';
  actions.append(remove);
  box.append(actions);
}

function drawFailed(detail) {
  const box = $('rv-failed');
  box.replaceChildren();
  const why = detail.review.error;
  box.append(el('p', { className: 'note bad', textContent:
    'The build did not finish.' + (why ? '\n' + why : '') + '\nThe end of the log is below.' }));
  $('rv-log-box').open = true;
}

// ── the setup form

function setUpReferenceForm(detail) {
  const construct = (detail.construct || {}).path || '';
  const ref = detail.reference || {};
  const options = [...(detail.suggestions || [])];
  if (ref.path && !ref.none && !options.some(o => o.path === ref.path)) {
    options.unshift({ path: ref.path, name: ref.path.split('/').pop(), why: 'used for the last build' });
  }
  state.review.options = options;
  state.review.construct = construct;

  // A review last built without a reference comes back that way; a legacy one built on the
  // construct itself is reported as the same thing by the API.
  state.review.mode = ref.none ? 'none' : 'reference';
  // Choose for them when there is a good answer; the construct itself is no answer.
  const chosen = ref.none ? '' : ref.path
    || (options.find(o => o.path !== construct && o.fits !== false) || {}).path
    || '';
  $('rv-ref').value = chosen;
  // A review last built on this same file comes back with the partners it was asked for. The target starts from the default
  // choice again (the last build always names its chain, so carrying it over would read as "asked for" when nobody asked).
  state.review.chain = '';
  state.review.partners = !ref.none && ref.path && ref.path === chosen ? ref.partner_chains || 'auto' : 'auto';
  state.review.assembly = !ref.none && ref.path && ref.path === chosen ? ref.assembly || '' : '';
  state.review.comp = null; state.review.compKey = '';
  $('rv-uniprot').value = ref.none ? '' : ref.uniprot || '';
  $('rv-uniprot').dataset.auto = '';
  $('rv-placement').value = 'interface';
  $('rv-online').checked = false;
  $('rv-pdb').value = '';
  $('rv-af').value = '';
  drawReferenceOptions();
  adoptUniprot();
  describeAdvanced();
  state.review.checkKey = '';
  drawMode();
  scheduleChains({ then: scheduleConfidence });
}

/** Which of the three situations the form is set up for. A reference is then described by
    what it is (see confidenceBox); "none" is described here, once, for what it leaves out. */
function drawMode() {
  const none = state.review.mode === 'none';
  document.querySelectorAll('input[name=rv-mode]').forEach(r => { r.checked = (r.value === 'none') === none; });
  $('rv-refpart').classList.toggle('hidden', none);
  const box = $('rv-nonepart');
  box.replaceChildren();
  if (none) box.append(confidenceBox({ kind: 'none', status: 'none' }));
  box.classList.toggle('hidden', !none);
  $('rv-start').textContent = state.review.detail && state.review.detail.review ? 'Rebuild the review' : 'Build the review';
  note($('rv-start-note'), '');
  scheduleConfidence();
}

/** The suggestions as rows to click, and as the datalist behind the typing box. */
function drawReferenceOptions() {
  const { options, construct } = state.review;
  const picked = $('rv-ref').value.trim();
  const box = $('rv-suggest');
  box.replaceChildren();
  const list = $('rv-ref-choices');
  list.replaceChildren();

  for (const option of options) {
    list.append(el('option', { value: option.path, label: option.name }));
    const row = el('button', { type: 'button',
      className: 'refopt' + (option.path === picked ? ' is-on' : '') });
    row.append(el('b', { textContent: option.name }));
    // The API compares each file with the construct and says so in `why`; a file that
    // is the construct, or does not look like it, gets that sentence in the warning colour.
    const caution = option.path === construct || option.fits === false;
    const bits = [option.why, option.uniprot ? 'UniProt ' + option.uniprot : ''].filter(Boolean);
    const sort = KIND_TAG[option.kind];
    if (sort) row.append(el('span', { className: 'kind', textContent: sort,
      title: option.kind === 'experimental' ? 'Recognised as a measured structure from the file itself.'
                                            : 'Recognised as a prediction from the file itself.' }));
    if (bits.length) {
      row.append(el('span', { className: 'why' + (caution ? ' warn' : ''), textContent: bits.join(' · ') }));
    }
    row.onclick = () => {
      $('rv-ref').value = option.path;
      adoptUniprot();
      afterReferenceChange();
    };
    box.append(row);
  }
  box.classList.toggle('hidden', !options.length);
  describeReference();
}

function afterReferenceChange() {
  note($('rv-start-note'), '');
  const review = state.review;
  if (review.compPath && review.compPath !== $('rv-ref').value.trim()) {
    // Chain names belong to a file; a different one starts from the default choice.
    review.chain = ''; review.partners = 'auto'; review.assembly = ''; review.comp = null; review.compKey = '';
    $('rv-chains').classList.add('hidden');
  }
  drawReferenceOptions();
  describeAdvanced();
  scheduleChains({ then: scheduleConfidence });
}

/** Say what the typed path is, the way the results-folder field does. */
function describeReference() {
  const { options, construct } = state.review;
  const value = $('rv-ref').value.trim();
  const known = options.find(o => o.path === value);
  let text;
  if (!value) {
    text = 'Choose one above, or give a full path.';
  } else if (!value.startsWith('/')) {
    text = 'Needs to be a full path, starting with /.';
  } else if (value === construct) {
    text = 'That is the construct itself, which holds nothing it left out. To review without a full-length structure, choose that above.';
  } else if (known && known.fits === false) {
    text = 'This file does not look like the construct\u2019s protein. The build will refuse it if it does not match.';
  } else if (known) {
    text = 'Check this is the whole protein the construct was cut from. The build says if the two do not match.';
  } else {
    text = 'Your own path. The service only reads structures from its uploads and results folders, so upload the file if it is anywhere else.';
  }
  $('rv-ref-note').textContent = text;
}

/** A suggestion that names its UniProt entry fills the accession, unless they typed one. */
function adoptUniprot() {
  const option = state.review.options.find(o => o.path === $('rv-ref').value.trim());
  // Filled from the file, and replaced when they pick another one; anything they typed stays.
  for (const [field, value] of [[$('rv-uniprot'), option && option.uniprot]]) {
    if (value && (!field.value || field.dataset.auto === '1')) {
      field.value = value;
      field.dataset.auto = '1';
    } else if (!value && field.dataset.auto === '1') {
      field.value = '';                      // it belonged to the file they have moved off
      field.dataset.auto = '';
    }
  }
}

function describeAdvanced() {
  const bits = [];
  if ($('rv-uniprot').value.trim()) bits.push('UniProt ' + $('rv-uniprot').value.trim().toUpperCase());
  if ($('rv-placement').value !== 'interface') bits.push('whole-target fit');
  if ($('rv-online').checked) bits.push('online lookup');
  $('rv-adv-state').textContent = bits.length ? '— ' + bits.join(', ') : '';
}

/** A file added here becomes the choice, and joins the list for this visit. */
function referenceAdded(info, extra = {}) {
  const path = info.path || info.target_path;
  const { options } = state.review;
  if (!options.some(o => o.path === path)) {
    options.unshift({ path, name: info.name || path.split('/').pop(), why: 'added just now',
                     uniprot: info.uniprot || null, ...extra });
  }
  $('rv-ref').value = path;
  adoptUniprot();
  afterReferenceChange();
}

// ── which chain is the target, and which chains the binder is checked against

/** "chain A", "chains A and B", "chains A, B and C". */
function chainWords(names) {
  const list = names.map(String);
  return list.length === 1 ? 'chain ' + list[0]
    : 'chains ' + list.slice(0, -1).join(', ') + ' and ' + list[list.length - 1];
}

const ROLE_WORDS = {
  target: 'Target',
  subunit: 'Same protein',
  other_molecule: 'Different molecule',
  outside_assembly: 'Not in the assembly',
  unassigned: '',
};

/** One short phrase per chain: what it is, how much of it, and what it does to the target. */
function chainWhat(row) {
  const bits = [];
  if (row.name) bits.push(row.name);
  else if (row.kind !== 'protein') bits.push(row.kind);
  if (row.first != null) bits.push(`residues ${row.first}–${row.last}`);
  if (row.construct && row.role === 'target') bits.push(`holds ${row.construct.found} of the construct’s ${row.construct.of}`);
  return bits.join(' · ');
}

let chainsTimer;
/** Ask what the chosen structure holds. Advice that the build then follows: the same
    function decides here and there, so what is shown is what is built. */
function scheduleChains({ then = null } = {}) {
  clearTimeout(chainsTimer);
  chainsTimer = setTimeout(() => loadChains().then(then || (() => {})), 200);
}

function chainsKey() {
  const review = state.review;
  const query = new URLSearchParams({ path: $('rv-ref').value.trim() });
  if (review.chain) query.set('chain', review.chain);
  if (review.partners && review.partners !== 'auto') query.set('partners', review.partners);
  if (review.assembly) query.set('assembly', review.assembly);
  return { key: review.chosen + '?' + query, query };
}

async function loadChains() {
  const review = state.review;
  const box = $('rv-chains');
  const path = $('rv-ref').value.trim();
  if (!review.chosen || review.mode === 'none' || !path.startsWith('/') || $('rv-form').classList.contains('hidden')) {
    review.comp = null; review.compKey = '';
    return box.classList.add('hidden');
  }
  const { key, query } = chainsKey();
  if (key === review.compKey && review.comp) return drawChains();
  review.compKey = key;
  review.compPending = true;
  let comp = null;
  try { comp = await api(rvNameUrl(review.chosen) + '/chains?' + query); }
  catch (problem) {
    if (review.compKey !== key) return;
    review.compPending = false;
    review.comp = null;
    box.replaceChildren(el('p', { className: 'note bad', textContent: problem.message }));
    return box.classList.remove('hidden');
  }
  if (review.compKey !== key) return;                  // they have moved on to another file
  review.compPending = false;
  review.comp = comp;
  review.compPath = path;
  drawChains();
}

function drawChains() {
  const review = state.review;
  const comp = review.comp;
  const box = $('rv-chains');
  box.replaceChildren();
  if (!comp) return box.classList.add('hidden');
  box.classList.remove('hidden');

  const head = el('div', { className: 'chains-head' });
  head.append(el('b', { textContent: 'Chains in this structure' }));
  const changed = !!(review.chain || review.partners !== 'auto' || review.assembly);
  if (changed) {
    const reset = el('button', { type: 'button', className: 'ghost small', textContent: 'Use the default choice' });
    reset.onclick = () => { review.chain = ''; review.partners = 'auto'; review.assembly = ''; scheduleChains(); };
    head.append(reset);
  }
  box.append(head);

  if (comp.problem) box.append(el('p', { className: 'note bad', textContent: comp.problem }));

  const target = comp.target && comp.target.chain;
  const partners = (comp.partners && comp.partners.chains) || [];
  if (target) {
    const lead = el('p', { className: 'chains-lead' });
    lead.append(el('b', { textContent: 'Target: chain ' + target }), ` — ${comp.target.why}.`);
    box.append(lead);
    if (comp.target.warning) box.append(el('p', { className: 'chains-warn', textContent: comp.target.warning + '. The build will refuse this chain.' }));
    const kinds = comp.chains.filter(r => r.role !== 'target');
    if (!kinds.length) {
      box.append(el('p', { className: 'chains-lead', textContent: 'This is the only protein chain in the file, so there is no other subunit to check the binder against.' }));
    } else {
      const lead2 = el('p', { className: 'chains-lead' });
      lead2.append(el('b', { textContent: partners.length ? 'Checked against: ' + chainWords(partners) : 'Checked against: no other chain' }),
                   ` — ${comp.partners.why}${comp.partners.mode === 'auto' ? ' (the default)' : ''}.`);
      box.append(lead2);
    }
  }

  if (comp.chains.length > 1 || (comp.chains.length === 1 && !target)) {
    const table = el('table', { className: 'chain-table' });
    table.append(el('thead', {}, el('tr', {}, ['Target', 'Chain', 'What it is', 'Check the binder against it']
      .map(t => el('th', { textContent: t })))));
    const body = el('tbody');
    for (const row of comp.chains) {
      const protein = row.kind === 'protein' && !row.generated;
      const radio = el('input', { type: 'radio', name: 'rv-target', value: row.chain, checked: row.role === 'target', disabled: !protein });
      radio.title = protein ? 'Use this chain as the target' : 'Only a protein chain of the file can be the target';
      radio.onchange = () => {
        review.chain = row.chain;
        // A chain cannot be its own partner: when partners were ticked by hand, the new target leaves that list.
        if (!['auto', 'none', 'all'].includes(review.partners)) {
          const keep = review.partners.split(',').filter(c => c !== row.chain);
          review.partners = keep.length ? keep.join(',') : 'none';
        }
        scheduleChains();
      };
      const tick = el('input', { type: 'checkbox', checked: row.used, disabled: row.role === 'target' });
      tick.onchange = () => {
        const used = new Set(comp.partners.chains);
        if (tick.checked) used.add(row.chain); else used.delete(row.chain);
        review.partners = used.size ? [...used].join(',') : 'none';
        scheduleChains();
      };
      const what = el('div', {});
      const line = el('span', { textContent: chainWhat(row) });
      const word = row.generated ? 'Symmetry copy of ' + row.copy_of : ROLE_WORDS[row.role];
      if (word) what.append(el('span', { className: 'role role-' + row.role, textContent: word }), ' ');
      what.append(line);
      if (row.hint) what.append(el('span', { className: 'hint', textContent: row.hint }));
      if (row.role !== 'target') what.append(el('span', { className: 'why', textContent: row.why }));
      body.append(el('tr', { className: 'role-row-' + row.role + (row.used ? ' is-used' : '') }, [
        el('td', {}, radio),
        el('td', {}, el('b', { textContent: row.chain })),
        el('td', {}, what),
        el('td', {}, row.role === 'target' ? el('span', { className: 'dim', textContent: 'the target' })
          : row.role === 'unassigned' ? el('span', { className: 'dim', textContent: 'no target found' })
          : el('label', { className: 'tick' }, [tick, row.used ? 'counted' : 'not counted'])),
      ]));
    }
    table.append(body);
    box.append(el('div', { className: 'conf-scroll' }, table));
  }

  // Everything that is not a chain: never a partner, said once, with what touches the target.
  const others = [...(comp.non_polymers || [])];
  if (others.length || comp.waters) {
    const bits = others.slice(0, 8).map(r => `${r.name} ×${r.count}` + (r.n_touch ? ' (touches the target)' : '')
      + (r.hint ? ' (' + r.hint + ')' : ''));
    if (others.length > 8) bits.push(`${others.length - 8} more`);
    if (comp.waters) bits.push(`${comp.waters} waters`);
    box.append(el('p', { className: 'chains-lead' }, [
      el('b', { textContent: 'Never checked against: ' }), bits.join(', ') + '. ',
      'Ligands, ions, buffer and crystallisation additives and waters are not part of the protein, so a binder overlapping one would mean nothing. '
      + 'A ligand or ion bound to the target is not measured either, which is worth knowing if it sits in the epitope.']));
  }
  const glycans = (comp.glycans || []);
  if (glycans.length) {
    const mine = glycans.filter(g => g.measured).length;
    box.append(el('p', { className: 'chains-lead', textContent:
      `${glycans.length} N-linked glycan${glycans.length > 1 ? 's' : ''} in the file; ${mine} on the chains the binder is checked against.` }));
  }
  const asm = comp.assembly || {};
  const notes = [];
  if (asm.id) notes.push(`Subunits are taken from assembly ${asm.id}${asm.oligomeric ? ' (' + asm.oligomeric + ')' : ''}.`);
  else if (asm.records === false) notes.push('The file has no assembly record, so every chain with the target’s sequence is taken as a subunit.');
  if (asm.generated && asm.generated.length) {
    notes.push(`The file holds only part of the assembly, so ${asm.generated.length === 1 ? 'one copy was' : asm.generated.length + ' copies were'} made from its symmetry operators.`);
  }
  for (const text of notes) box.append(el('p', { className: 'help', textContent: text }));
  for (const text of comp.notes || []) box.append(el('p', { className: 'help', textContent: text.charAt(0).toUpperCase() + text.slice(1) + '.' }));
}

// ── how far to trust the reference (AlphaFold PAE)

/** "180–197", or "42" for one residue. */
const spanOf = (part) => part.start === part.end ? String(part.start) : `${part.start}–${part.end}`;

/** "residues 1–26, 198–300 and 400–618", cut short when a long list would drown the point. */
function spansOf(parts) {
  const spans = parts.map(spanOf);
  const shown = spans.length > 4 ? [...spans.slice(0, 4), `${spans.length - 4} more`] : spans;
  const text = shown.length > 1 ? shown.slice(0, -1).join(', ') + ' and ' + shown[shown.length - 1] : shown[0];
  const one = parts.length === 1 && parts[0].start === parts[0].end;
  return (one ? 'residue ' : 'residues ') + text;
}

/** "PAE 30.4 Å", or "PAE 17.6–30.4 Å" when the pieces differ. */
function paeOf(parts) {
  const low = Math.min(...parts.map(p => p.pae_min)), high = Math.max(...parts.map(p => p.pae_max));
  const fmt = (x) => x.toFixed(1);
  return fmt(low) === fmt(high) ? `PAE ${fmt(low)} Å` : `PAE ${fmt(low)}–${fmt(high)} Å`;
}

const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const modelWord = (check) => check.kind === 'alphafold' ? 'an AlphaFold model' : 'a predicted model';
const isPrediction = (check) => check.kind === 'alphafold' || check.kind === 'predicted';

/** The checks that need the rest of the protein, in the order the review names them. */
const CONTEXT_CHECKS = 'clashes with other subunits or the rest of the chain, contacts that run off a cut end, a buried epitope and glycans';

/** A second limit of a predicted reference, separate from how well AlphaFold placed its
    regions: one model is one conformation. A paragraph a designer will read, so it says
    what to do with it. */
const ONE_CONFORMATION = {
  lead: 'Also one conformation.',
  text: 'Even where the fold is right and PAE is low, a single predicted model is one static shape of a '
      + 'protein that moves. Domains shift, loops open, and an epitope can be hidden in the model and '
      + 'reachable in solution, or the reverse. So read this reference’s clash, cut-end and buried-epitope '
      + 'findings as advisory: a clash with a well-placed domain is a hypothesis to test, not a measurement. '
      + 'If your campaign expects the target to change shape when the binder lands, that is the one thing '
      + 'a model cannot show.',
};

/** What the reference is and what the check found, as words. Returns null when there is
    nothing worth saying. `kind` decides the weight findings carry; the PAE check is a
    separate matter, about how well each part was placed. */
function confidenceCopy(check, built) {
  if (!check) return null;

  // No reference at all: nothing is checked against it, and that is said, once.
  if (check.kind === 'none') {
    return {
      kind: 'info', head: 'No full-length reference: a narrower review, not a worse one.',
      paras: [
        { lead: 'Not made:', text: `${capital(CONTEXT_CHECKS)}. Each needs the rest of the protein, and the review makes no claim about them.` },
        { lead: 'Unaffected:', text: 'backbone families, binding modes, the recomputed interface, the paratope breakdown, near-miss rescue and BindCraft’s own scores. They come from the designs’ own structures.' },
      ],
      advice: 'A good choice when you have no full-length structure you trust, or only a prediction you would rather not lean on.',
    };
  }

  // A measured structure: its findings carry their full weight.
  if (check.kind === 'experimental') {
    const how = [check.method, check.resolution ? check.resolution.toFixed(1) + ' Å' : ''].filter(Boolean).join(', ');
    const paras = [{ text: 'Clash, cut-end and buried-epitope findings against this reference carry their full weight.'
      + (check.method ? '' : ' The file records no method, but its B-factors differ from atom to atom, as a measurement’s do.') },
      { text: 'The ordinary caveat still applies: a crystal or cryo-EM structure is also one conformation of a protein that moves, so a call near a threshold stays soft.' }];
    return { kind: 'info', head: 'An experimental structure' + (how ? ` (${how}).` : '.'), paras };
  }

  // Cannot tell whether it is measured or predicted.
  if (check.kind === 'unclear') {
    return { kind: 'info', head: 'Not known whether this is a measured or a predicted structure.',
             paras: [(check.reason || 'The file does not say.') + ' Until you know, read its clash, cut-end and buried-epitope findings as advisory: if it is a prediction it is also one conformation of a protein that moves.'] };
  }

  if (check.status === 'not_applicable') {   // a prediction we could not read a chain from
    return isPrediction(check)
      ? { kind: 'info', head: `This reference is ${modelWord(check)}.`, paras: [ONE_CONFORMATION] } : null;
  }

  if (check.status === 'nothing_outside') {
    if (built) return null;       // the build's own note already says it checks nothing extra
    return { kind: 'info', head: 'No PAE check applies.',
             paras: ['Nothing lies outside the construct in this structure, so there is nothing for AlphaFold to have placed.'] };
  }

  if (check.status === 'could_not_check') {
    const paras = [(check.reason || 'The check could not be made.')
              + ' This is not a pass: until it is known, treat clash and buried-epitope findings about residues outside the construct as unchecked.'];
    if (isPrediction(check)) paras.push(ONE_CONFORMATION);
    return {
      kind: 'warn', retry: !!check.transient,
      head: check.kind === 'predicted'
        ? (built ? 'When this was built, how' : 'How') + ' well this model places its parts could not be checked.'
        : (built ? 'When this was built, AlphaFold’s' : 'AlphaFold’s') + ' confidence in this reference could not be checked.',
      paras,
    };
  }

  const groups = check.groups || [];
  const by = (kind) => groups.filter(g => g.class === kind);
  const [bad, weak, good] = [by('independent'), by('uncertain'), by('confident')];
  const what = 'PAE is AlphaFold’s own estimate, in Å, of how far off one part is when the model is lined up on the other; its scale ends near 32 Å, where it is saying it has no idea';
  const paras = [];
  let head, advice = false;

  if (check.level === 'warn') {
    head = good.length ? 'Part of this reference is not reliably placed.'
                       : 'AlphaFold has not placed the rest of this protein relative to your construct.';
    paras.push(`${capital(spansOf(bad))} ${bad.length === 1 && bad[0].start === bad[0].end ? 'is' : 'are'} placed essentially independently of it (${paeOf(bad)}). ${what}.`);
    if (weak.length) paras.push(`${capital(spansOf(weak))} ${weak.length === 1 && weak[0].start === weak[0].end ? 'is' : 'are'} placed only weakly (${paeOf(weak)}).`);
    if (good.length) paras.push(`${capital(spansOf(good))} ${good.length === 1 && good[0].start === good[0].end ? 'is' : 'are'} placed confidently (${paeOf(good)}).`);
    paras.push(`Clash, cut-end and buried-epitope findings involving ${spansOf(bad)} cannot be trusted either way: a design reported as colliding there may not, and one reported clear is not shown clear. If every design hits the same patch, that points at the reference, not the designs.`);
    advice = true;
  } else if (check.level === 'note') {
    head = 'AlphaFold places the rest of this protein only weakly relative to your construct.';
    paras.push(`${capital(spansOf(weak))} ${weak.length === 1 && weak[0].start === weak[0].end ? 'is' : 'are'} placed with weak confidence (${paeOf(weak)}). ${what}.`);
    if (good.length) paras.push(`${capital(spansOf(good))} ${good.length === 1 && good[0].start === good[0].end ? 'is' : 'are'} placed confidently (${paeOf(good)}).`);
    paras.push('Findings involving the weakly placed parts are soft: treat a contact near a threshold, or a marginally buried epitope, as open.');
    advice = true;
  } else {
    head = 'AlphaFold places everything outside the construct confidently.';
    paras.push(`${capital(spansOf(good))} ${good.length === 1 && good[0].start === good[0].end ? 'is' : 'are'} placed confidently relative to the construct (${paeOf(good)}). ${what}. PAE this low still leaves a few Å of slack, so a call near a threshold stays soft.`);
  }
  paras.push(ONE_CONFORMATION);
  return {
    kind: check.level === 'good' ? 'info' : 'warn', head, paras, table: check.regions || [],
    advice: advice ? 'For a multi-domain target, a reference that stops at the domain you designed against, or an experimental structure of it, gives an answer you can rely on. You can still build against this one.' : null,
  };
}

/** The confidence note as a block, or null. Used in the setup form and beside a built review's link. */
function confidenceBox(check, { built = false, retry = null } = {}) {
  const copy = confidenceCopy(check, built);
  if (!copy) return null;
  const box = el('div', { className: 'conf ' + copy.kind });
  box.append(el('b', { className: 'head', textContent: copy.head }));
  for (const para of copy.paras) {
    if (typeof para === 'string') { box.append(el('p', { textContent: para })); continue; }
    const p = el('p', { className: para === ONE_CONFORMATION ? 'apart' : '' });
    if (para.lead) p.append(el('b', { className: 'lead', textContent: para.lead + ' ' }));
    p.append(para.text);
    box.append(p);
  }
  if (copy.advice) box.append(el('p', { className: 'advice', textContent: copy.advice }));
  if (copy.retry && retry) {
    const again = el('button', { type: 'button', className: 'ghost small', textContent: 'Check again' });
    again.onclick = retry;
    box.append(again);
  }
  if (copy.table && copy.table.length) {
    const more = el('details', { className: 'conf-more' });
    more.append(el('summary', { textContent: 'Region by region' }));
    const table = el('table', { className: 'conf-table' });
    table.append(el('thead', {}, el('tr', {}, ['Residues', 'Length', 'PAE to construct', 'Placement', 'pLDDT']
      .map(t => el('th', { textContent: t })))));
    const body = el('tbody');
    for (const region of copy.table) {
      const word = { confident: 'confident', uncertain: 'weak', independent: 'independent' }[region.class];
      body.append(el('tr', { className: 'c-' + region.class }, [
        el('td', { textContent: spanOf(region) }),
        el('td', { textContent: String(region.count) }),
        el('td', { textContent: region.pae.toFixed(1) + ' Å' }),
        el('td', { textContent: word }),
        el('td', { textContent: region.plddt == null ? '' : Math.round(region.plddt) + (region.disordered ? ' (likely disordered)' : '') }),
      ]));
    }
    table.append(body);
    more.append(el('div', { className: 'conf-scroll' }, table), el('p', { className: 'help', textContent:
      'Regions are stretches AlphaFold treats as one unit, cut from its own PAE, so a well-placed neighbour is not averaged with a floppy one. '
      + `PAE is the mean of both directions; below ${check.thresholds.confident} Å is confident and above ${check.thresholds.independent} Å is essentially independent.` }));
    box.append(more);
  }
  return box;
}

/** One sentence for the list row of a built review, or null when there is nothing to say.
    {text, warn}: warn is for a reference that cannot be trusted where it matters; the
    plain ones only say what kind of reference the findings stand on. */
function confidenceLine(check) {
  if (!check) return null;
  if (check.kind === 'none') {
    return { warn: false, text: 'Built without a full-length reference: the clash, cut-end, buried-epitope and glycan checks were not made.' };
  }
  if (check.kind === 'unclear') {
    return { warn: false, text: 'The reference does not say whether it was measured or predicted; read its full-length findings as advisory.' };
  }
  if (!isPrediction(check)) return null;
  if (check.status === 'could_not_check') {
    return { warn: true, text: `The reference is ${modelWord(check)} whose confidence could not be checked when this was built; treat full-length findings outside the construct as unchecked.` };
  }
  if (check.status === 'checked') {
    const groups = check.groups || [];
    const worst = groups.filter(g => g.class === 'independent');
    const weak = groups.filter(g => g.class === 'uncertain');
    if (worst.length) return { warn: true, text: `The reference does not place ${spansOf(worst)} reliably (${paeOf(worst)}), so full-length findings there cannot be trusted.` };
    if (weak.length) return { warn: true, text: `The reference places ${spansOf(weak)} only weakly (${paeOf(weak)}); findings there are soft.` };
  }
  if (check.status === 'nothing_outside') return null;
  return { warn: false, text: `The reference is ${modelWord(check)}, one conformation, so its full-length findings are advisory.` };
}

/** What a confidence answer is for: this campaign, this file, this chain and accession. */
function confidenceKey() {
  const query = new URLSearchParams({ path: $('rv-ref').value.trim() });
  // The chain is the one the build will take, as the chains block has decided it, not a second guess of ours.
  const comp = state.review.comp;
  const chain = comp && comp.target && comp.target.chain && state.review.compPath === $('rv-ref').value.trim() ? comp.target.chain : '';
  if (chain) query.set('chain', chain);
  if ($('rv-uniprot').value.trim()) query.set('uniprot', $('rv-uniprot').value.trim().toUpperCase());
  return { key: state.review.chosen + '?' + query, query };
}

let confTimer;
function scheduleConfidence() {
  clearTimeout(confTimer);
  // The note on screen belongs to the file it was asked about; once another is chosen it
  // goes, rather than sit under the new name for as long as the answer takes.
  if (confidenceKey().key !== state.review.checkKey) $('rv-conf').classList.add('hidden');
  confTimer = setTimeout(checkConfidence, 350);
}

/** Ask what the chosen reference is worth outside the construct. Advice only: nothing
    here ever disables the build button, because someone who knows their protein may
    have good reason to go ahead. */
async function checkConfidence() {
  const review = state.review;
  const box = $('rv-conf');
  const name = review.chosen;
  const path = $('rv-ref').value.trim();
  if (!name || review.mode === 'none' || !path.startsWith('/') || $('rv-form').classList.contains('hidden')) {
    review.check = null; review.checkKey = '';
    return box.classList.add('hidden');
  }
  if (review.compPending) return;               // the chains block is deciding which chain this is about; it asks again when it knows
  const { key, query } = confidenceKey();
  if (key === review.checkKey && review.check && !review.check.transient) {
    return box.classList.toggle('hidden', !box.childElementCount);    // the same answer: show it again
  }
  review.checkKey = key;
  box.classList.add('hidden');

  // PAE may have to come from AlphaFold, which can take several seconds. Saying so only
  // when it is slow keeps the fast answers from flickering.
  const slow = setTimeout(() => {
    if (review.checkKey !== key) return;
    box.replaceChildren(el('p', { className: 'help', textContent: 'Checking how confidently AlphaFold places the rest of this protein…' }));
    box.className = 'conf info';
  }, 600);
  let check = null;
  try { check = await api(rvNameUrl(name) + '/reference?' + query); }
  catch { /* a path still being typed, or a server without this route: say nothing */ }
  clearTimeout(slow);
  if (review.checkKey !== key) return;          // they have moved on to another file
  review.check = check;
  // A file added just now has not been classified yet; the check has done it.
  const added = check && review.options.find(o => o.path === path);
  if (added && !added.kind && KIND_TAG[check.kind]) { added.kind = check.kind; drawReferenceOptions(); }
  const block = confidenceBox(check, { retry: () => { review.checkKey = ''; checkConfidence(); } });
  box.replaceChildren(...(block ? [block] : []));
  box.className = block ? 'conf-wrap' : 'conf-wrap hidden';
}

async function startReview() {
  const review = state.review;
  const name = review.chosen;
  const path = $('rv-ref').value.trim();
  let request;
  if (review.mode === 'none') {
    // Said out loud, so a blank field is never mistaken for it.
    request = { no_reference: true };
  } else {
    if (!path) return toast('Choose a full-length structure, or choose to review without one', true);
    if (!path.startsWith('/')) return toast('The structure needs a full path, starting with /', true);
    request = { reference_path: path, placement: $('rv-placement').value, online: $('rv-online').checked };
    // The target and the partners are said outright, as the chains block shows them, never left to a default.
    const comp = state.review.comp;
    if (comp && comp.target && comp.target.chain && state.review.compPath === path) request.reference_chain = comp.target.chain;
    else if (state.review.chain) request.reference_chain = state.review.chain;
    request.partner_chains = state.review.partners;
    if (state.review.assembly) request.assembly = state.review.assembly;
    if ($('rv-uniprot').value.trim()) request.uniprot = $('rv-uniprot').value.trim().toUpperCase();
  }

  const button = $('rv-start');
  button.disabled = true;
  note($('rv-start-note'), '');
  try {
    await api(rvUrl(name), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(request),
    });
  } catch (problem) {
    // The refusal says what to change (a reference that does not fit, say), so it stays
    // next to the button rather than vanishing like a toast.
    button.disabled = false;
    return note($('rv-start-note'), problem.message, 'bad');
  }
  state.review.formOpen = false;
  $('rv-log').textContent = '';
  await loadReviewList();
  await chooseReview(name, { quiet: true });
}

async function stopReview() {
  const name = state.review.chosen;
  if (!name || !confirm(`Stop building the review of "${reviewLabel(name)}"? Nothing from this build is kept.`)) return;
  try { await api(rvUrl(name), { method: 'DELETE' }); }
  catch (problem) { return toast(problem.message, true); }
  state.review.formOpen = true;
  await loadReviewList();
  await chooseReview(name, { quiet: true, keepForm: true });
}

async function removeReview() {
  const name = state.review.chosen;
  if (!name || !confirm(`Remove the review of "${reviewLabel(name)}"? `
    + (isProject(name) ? 'The campaigns themselves are not touched.' : 'The campaign itself is not touched.'))) return;
  try { await api(rvUrl(name), { method: 'DELETE' }); }
  catch (problem) { return toast(problem.message, true); }
  state.review.formOpen = false;
  await loadReviewList();
  await chooseReview(name, { quiet: true });
}

// ── progress

/** The log endpoint also says where the build is. Either number may be missing, and
    then a plain spinner is the honest thing to show. */
async function pullReviewLog() {
  const review = state.review;
  if (!review.chosen || !review.detail || !reviewState(review.detail)) return;
  let answer;
  try { answer = await api(rvUrl(review.chosen) + '/log?tail=400'); }
  catch { return; }                           // nothing to show yet, or the review was removed
  fillLog($('rv-log'), answer.text || '');

  if (REVIEW_BUSY.includes(answer.state)) {
    const step = answer.step ? 'step: ' + String(answer.step).replace(/_/g, ' ') : '';
    const percent = Number.isFinite(answer.percent) ? Math.max(0, Math.min(100, answer.percent)) : null;
    $('rv-build-title').textContent = answer.state === 'queued' ? 'Waiting for a free slot' : 'Building the review';
    $('rv-build-step').textContent = [step, percent != null ? Math.round(percent) + '%' : ''].filter(Boolean).join(' \u00b7 ');
    $('rv-track').classList.toggle('hidden', percent == null);
    $('rv-fill').style.width = (percent || 0) + '%';
    $('rv-spin').classList.toggle('off', percent != null);
  } else if (REVIEW_BUSY.includes(reviewState(review.detail))) {
    syncPanel();                              // it stopped since the panel was drawn
  }
}

let reviewTicks = 0;
function reviewTick() {
  if ($('view-review').classList.contains('hidden')) return;
  reviewTicks++;
  const detail = state.review.detail;
  const busy = detail && REVIEW_BUSY.includes(reviewState(detail));
  if (busy) pullReviewLog();
  // Quick while a build runs; slow when idle, and slower still when there is no API to ask.
  const every = busy ? 3 : state.review.unavailable ? 24 : 4;
  if (reviewTicks % every === 0) syncPanel();
}

// ── wiring

async function setUpReview() {
  $('rv-ref').oninput = () => {
    adoptUniprot();
    afterReferenceChange();
  };
  $('rv-uniprot').oninput = () => { $('rv-uniprot').dataset.auto = ''; describeAdvanced(); scheduleConfidence(); };
  for (const id of ['rv-placement', 'rv-online']) $(id).onchange = describeAdvanced;
  document.querySelectorAll('input[name=rv-mode]').forEach(r => {
    r.onchange = () => {
      state.review.mode = r.value;
      // Coming back to a reference with nothing chosen: offer the one that fits, as on first opening.
      const { options, construct } = state.review;
      if (r.value === 'reference' && !$('rv-ref').value.trim()) {
        const fit = options.find(o => o.path !== construct && o.fits !== false);
        if (fit) { $('rv-ref').value = fit.path; adoptUniprot(); drawReferenceOptions(); describeAdvanced(); }
      }
      drawMode();
      scheduleChains();
    };
  });
  $('im-add').onclick = importRun;
  $('im-path').onkeydown = (event) => { if (event.key === 'Enter') importRun(); };

  $('rv-start').onclick = startReview;
  $('rv-stop').onclick = stopReview;

  $('rv-upload').onclick = () => $('rv-file').click();
  $('rv-file').onchange = async () => {
    const file = $('rv-file').files[0];
    $('rv-file').value = '';
    if (!file) return;
    try {
      const body = new FormData();
      body.append('file', file);
      referenceAdded(await api('/api/upload', { method: 'POST', body }));
      toast(`Loaded ${file.name}`);
    } catch (problem) { toast(problem.message, true); }
  };

  $('rv-pdb-go').onclick = async () => {
    const code = $('rv-pdb').value.trim();
    if (!code) return toast('Type a four-character PDB code first', true);
    const button = $('rv-pdb-go');
    button.disabled = true;
    try {
      const body = new FormData();
      body.append('entry', code);
      referenceAdded(await api('/api/fetch-pdb', { method: 'POST', body }));
      toast(`Loaded ${code.toUpperCase()}`);
    } catch (problem) { toast(problem.message, true); }
    finally { button.disabled = false; }
  };

  // The AlphaFold shortcut exists only if this server offers it and the firewall allows
  // it; otherwise upload and PDB are the routes and the box is simply not drawn.
  try {
    const spec = await api('/openapi.json');
    state.review.hasAF = !!(spec.paths && spec.paths['/api/fetch-af']);
  } catch { state.review.hasAF = false; }
  $('rv-af-box').classList.toggle('hidden', !state.review.hasAF);
  $('rv-af-go').onclick = async () => {
    const accession = $('rv-af').value.trim().toUpperCase();
    if (!accession) return toast('Type a UniProt accession, like P69905', true);
    const button = $('rv-af-go');
    button.disabled = true;
    try {
      const body = new FormData();
      body.append('uniprot', accession);
      referenceAdded(await api('/api/fetch-af', { method: 'POST', body }),
                     { uniprot: accession, why: 'AlphaFold model, added just now' });
      toast(`Loaded the AlphaFold model for ${accession}`);
    } catch (problem) { toast(problem.message, true); }
    finally { button.disabled = false; }
  };

  setInterval(reviewTick, 2500);
}

// ───────────────────────────────────────────────────────────── storage

/* The Storage tab reports what is on disk and what could be given back. It
   archives (Restore is the way back) and it reports; it never deletes, so
   nothing here removes anything or builds a command to. A path is shown so it
   can be copied and acted on in a shell.

   Held at module scope for the same reason as openBuilds: the page redraws from
   scratch on every poll, and anything kept on a node would go with it. */
const storageUi = {
  data: null,          // the last good /api/storage answer
  error: '',           // why the last read failed, or ''
  notice: null,        // {text, kind} left by Refresh or Restore, kept across redraws
  restoring: new Set(),   // folders with a Restore in flight
  picked: new Set(),      // archived folders ticked for a bulk Restore
  rowBusy: new Map(),     // key -> 'posting' | 'waiting': a folder being re-measured by itself
  focusKey: null,         // the row whose Refresh was pressed, to hand focus back to when it ends
};
let storageTimer = null;
let storageSeq = 0;

const storageVisible = () => !$('view-storage').classList.contains('hidden');

function stopStoragePoll() {
  clearTimeout(storageTimer);
  storageTimer = null;
}

/** 1024-based, like du, with three figures or so. null is "not measured", not 0. */
function fmtBytes(n) {
  if (n == null || isNaN(n)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let value = Number(n), step = 0;
  while (value >= 1024 && step < units.length - 1) { value /= 1024; step++; }
  const digits = step === 0 || value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units[step]}`;
}

const clockTime = (seconds) =>
  new Date(seconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

const dayOf = (seconds) => seconds
  ? new Date(seconds * 1000).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })
  : '—';

/** Read the page's numbers, then keep reading while a walk is running. The server
    answers at once whatever it has, so this never waits; it polls every two
    seconds only while `measuring` is true, and stops when it is not, when the tab
    is left, and when the browser tab is in the background. */
async function loadStorage() {
  stopStoragePoll();
  const seq = ++storageSeq;
  let data;
  try {
    data = await api('/api/storage');
  } catch (problem) {
    if (seq !== storageSeq) return;
    storageUi.error = problem.message;
    drawStorage();
    return;
  }
  if (seq !== storageSeq) return;
  storageUi.error = '';
  storageUi.data = data;
  pruneStorageState(data);
  drawStorage();
  if (data.measuring && storageVisible() && !document.hidden) {
    storageTimer = setTimeout(loadStorage, 2000);
  }
}

/** Forget what the answer says is over. A folder whose own walk has finished (or
    that has gone) stops showing as re-measuring; a ticked archived campaign that
    is no longer archived has been restored, and a tick on a row the reader can
    no longer see would act on something they cannot. A row the server skipped
    stays archived and stays ticked, which is how the reader sees what is left. */
function pruneStorageState(data) {
  const entries = new Map(data.campaigns.concat(data.reviews).map(e => [e.key, e]));
  for (const [key, phase] of [...storageUi.rowBusy]) {
    const entry = entries.get(key);
    if (phase === 'waiting' && !(entry && entry.size && entry.size.measuring)) storageUi.rowBusy.delete(key);
    else if (!entry) storageUi.rowBusy.delete(key);
  }
  const archived = new Set((data.archived || []).map(a => a.folder));
  for (const folder of [...storageUi.picked]) if (!archived.has(folder)) storageUi.picked.delete(folder);
}

document.addEventListener('visibilitychange', () => {
  const data = storageUi.data;
  if (!document.hidden && storageVisible() && data && data.measuring) loadStorage();
});

async function refreshStorage() {
  const button = $('st-refresh');
  button.disabled = true;
  button.textContent = 'Starting…';
  storageUi.notice = null;
  try {
    await api('/api/storage/refresh', { method: 'POST' });
  } catch (problem) {
    storageUi.notice = { text: 'Could not start measuring: ' + problem.message, kind: 'bad' };
  } finally {
    button.disabled = false;
    button.textContent = 'Refresh';
  }
  loadStorage();
}

/** Put campaigns, and the reviews that were archived with them, back. Both kinds
    of id go together: archiving a campaign does not cascade to its review rows
    the way filing it under a project does, so they have to be named. The server
    says how many it could not restore and why; that is shown, not swallowed.
    One campaign and a ticked set go through here alike. */
async function restoreCampaigns(entries) {
  const ids = [...new Set(entries.flatMap(e => [...(e.design_ids || []), ...(e.review_ids || [])]))];
  const nothing = entries.filter(e => !(e.design_ids || []).length && !(e.review_ids || []).length);
  if (!ids.length) {
    return toast('Nothing to restore for ' + (entries.length === 1 ? entries[0].name : 'those campaigns'), true);
  }
  for (const entry of entries) storageUi.restoring.add(entry.folder);
  storageUi.notice = null;
  drawStorage();
  try {
    const answer = await api('/api/jobs/archive', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ids, archived: false }),
    });
    // Rows, not campaigns: a campaign and its reviews are several rows, so a
    // count of "restored" alone would not say what it counted.
    const bits = [`${answer.changed} changed`];
    if (answer.skipped) bits.push(`${answer.skipped} skipped`);
    storageUi.notice = {
      text: `Restore requested for ${plural(entries.length, 'campaign')} (${plural(ids.length, 'row')}): `
        + bits.join(', ') + '.' + (answer.note ? ' ' + answer.note : '')
        + (nothing.length ? ` ${plural(nothing.length, 'campaign')} had nothing to restore.` : ''),
      kind: answer.skipped ? 'warn' : 'good',
    };
    refreshJobs();
  } catch (problem) {
    storageUi.notice = { text: 'Could not restore: ' + problem.message, kind: 'bad' };
  } finally {
    for (const entry of entries) storageUi.restoring.delete(entry.folder);
  }
  await loadStorage();
}

/** Measure one folder again, and only that one. The row says so while it runs; the
    page does not go into its measuring state, because nothing else is. A key the
    server no longer knows comes back 404 and is said on the page, and the reload
    that follows drops the row if it has gone. */
async function refreshRow(key, name) {
  if (storageUi.rowBusy.has(key)) return;
  storageUi.rowBusy.set(key, 'posting');
  storageUi.focusKey = key;
  storageUi.notice = null;
  drawStorage();
  try {
    await api('/api/storage/refresh', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    storageUi.rowBusy.set(key, 'waiting');
  } catch (problem) {
    storageUi.rowBusy.delete(key);
    storageUi.notice = { text: `Could not measure ${name}: ${problem.message}`, kind: 'bad' };
  }
  loadStorage();
}

/** Copy needs a secure page; the address this is served from may not be one, so
    fall back to selecting into a hidden box. */
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* try the old way */ }
  const box = el('textarea', { value: text, className: 'sr-only' });
  document.body.append(box);
  box.select();
  let done = false;
  try { done = document.execCommand('copy'); } catch { /* not allowed here */ }
  box.remove();
  return done;
}

/** A button that copies `text`, says so on itself for a moment, and says so
    aloud when the browser refuses. Shared by the Storage paths and the Results
    sequences, so there is one way to copy in this app. */
function copyButton(label, text, ariaLabel) {
  const copy = el('button', { type: 'button', className: 'ghost st-copy', textContent: label });
  copy.setAttribute('aria-label', ariaLabel);
  copy.onclick = async () => {
    if (await copyText(text)) {
      copy.textContent = 'Copied';
      setTimeout(() => { copy.textContent = label; }, 1500);
    } else {
      toast('Could not copy. Select the text and copy it.', true);
    }
  };
  return copy;
}

/** A path, as text you can select, with a button that copies it. */
function pathRow(path, what) {
  const row = el('div', { className: 'st-path' });
  row.append(el('code', { textContent: path }));
  row.append(copyButton('Copy path', path, `Copy the path of ${what}`));
  return row;
}

/** A size cell. A project whose folders are not all measured yet has a total that
    is only part of the answer, and shown bare it would read as the whole. */
function sizeCell(bytes, unmeasured, measuring) {
  const cell = el('td', { className: 'fig' });
  if (!unmeasured) { cell.textContent = fmtBytes(bytes); return cell; }
  cell.textContent = bytes > 0 ? '≥ ' + fmtBytes(bytes) : (measuring ? 'measuring…' : 'not measured');
  cell.title = `${plural(unmeasured, 'folder')} not measured yet, so this is at least that much`;
  return cell;
}

/** True when the server is measuring something other than the folders the reader
    asked for one at a time. `data.measuring` is one flag for every walk, so a
    single row's Refresh would otherwise put the whole page into its measuring
    state; this is what tells the page-wide state apart from a row's own. With no
    listed folder measuring, whatever is running is not a row (the server log and
    uploads are measured too and are not listed), so it counts as page-wide. */
function measuringElsewhere(data) {
  if (!data || !data.measuring) return false;
  const running = data.campaigns.concat(data.reviews)
    .filter(e => e.size && e.size.measuring).map(e => e.key);
  return !running.length || running.some(key => !storageUi.rowBusy.has(key));
}

function drawStorage() {
  const data = storageUi.data;
  // A failed read means nothing is being watched any more, so it cannot claim to be measuring.
  const measuring = measuringElsewhere(data) && !storageUi.error;

  // What was measured, and when. Newest across every folder, since the page has
  // no single moment: each walk finishes on its own.
  const sizes = data ? data.campaigns.map(c => c.size).concat(data.reviews.map(r => r.size)) : [];
  const stamps = sizes.map(s => s && s.measured_at).filter(Boolean);
  const when = stamps.length ? clockTime(Math.max(...stamps)) : '';
  const bits = [];
  if (when) bits.push(`measured ${when}`);
  if (measuring) bits.push('measuring…');
  $('st-when').textContent = bits.length ? '— ' + bits.join(' · ') : '';
  const done = sizes.filter(s => s && s.measurement).length;
  $('st-detail').textContent = data && measuring
    ? `${done} of ${sizes.length} folders measured so far`
    : data && !stamps.length && sizes.length ? 'Nothing has been measured yet. Refresh starts it.' : '';

  drawStorageNotes(data, sizes);

  if (!data) {
    const text = storageUi.error ? 'Not available.' : 'Loading…';
    for (const id of ['st-projects', 'st-folders', 'st-disks', 'st-reclaim', 'st-archived']) {
      $(id).replaceChildren(el('div', { className: 'empty', textContent: text }));
    }
    $('st-folders-count').textContent = '';
    $('st-reclaim-count').textContent = '';
    $('st-archived-count').textContent = '';
    drawStoragePickedBar(null);
    return;
  }
  drawStorageProjects(data, measuring);
  drawStorageFolders(data, measuring);
  drawStorageDisks(data);
  drawStorageReclaim(data, measuring);
  drawStorageArchived(data, measuring);
  returnStorageFocus();
}

/** The Refresh that was pressed has just been redrawn away. Once its walk ends,
    put the keyboard back on it, but only if focus has nowhere else to be: a
    reader who has moved on must not be pulled back. */
function returnStorageFocus() {
  const key = storageUi.focusKey;
  if (key === null || storageUi.rowBusy.has(key)) return;
  storageUi.focusKey = null;
  if (document.activeElement && document.activeElement !== document.body) return;
  const button = [...document.querySelectorAll('.st-remeasure')].find(b => b.dataset.key === key);
  if (button) button.focus();
}

function drawStorageNotes(data, sizes) {
  const box = $('st-note');
  box.replaceChildren();
  const add = (text, kind) => box.append(el('div', { className: 'note ' + kind, textContent: text }));
  if (storageUi.error) add('Could not read storage: ' + storageUi.error, 'bad');
  if (storageUi.notice) add(storageUi.notice.text, storageUi.notice.kind);
  if (!data) return;
  const failed = sizes.filter(s => s && s.error);
  if (failed.length) {
    add(`${plural(failed.length, 'folder')} could not be measured: ${failed[0].error}`, 'warn');
  }
  const unreadable = sizes.reduce((n, s) => n + ((s && s.measurement && s.measurement.unreadable) || 0), 0);
  if (unreadable) {
    add(`${plural(unreadable, 'file')} could not be read, so the sizes below are a lower bound.`, 'warn');
  }
}

function drawStorageProjects(data, measuring) {
  const box = $('st-projects');
  box.replaceChildren();
  const rows = data.projects || [];
  if (!rows.length) {
    box.append(el('div', { className: 'empty', textContent: 'No campaigns yet.' }));
    return;
  }

  // Reviews carry no flag at project level, so find the unmeasured ones through
  // the campaign they belong to.
  const projectOfFolder = new Map(data.campaigns.filter(c => !c.archived).map(c => [c.folder, c.project]));
  const unmeasuredReviews = new Map();
  for (const review of data.reviews) {
    if (review.size && review.size.measurement) continue;
    const name = projectOfFolder.get(review.campaign);
    if (name !== undefined) unmeasuredReviews.set(name, (unmeasuredReviews.get(name) || 0) + 1);
  }

  const table = el('table', { className: 'inert-table' });
  table.append(el('caption', { className: 'sr-only', textContent: 'Disk use by project' }));
  const head = el('tr');
  [['Project', ''], ['Campaigns', 'fig'], ['Passed', 'fig'], ['Campaign data', 'fig'],
    ['Reviews', 'fig'], ['Last activity', '']].forEach(([label, kind]) => {
    head.append(el('th', { scope: 'col', className: kind, textContent: label,
      title: label === 'Passed' ? 'Sequences that passed, across the live campaigns of the project' : '' }));
  });
  table.append(el('thead', {}, head));

  const body = el('tbody');
  const total = { campaigns: 0, passed: 0, bytes: 0, reviews: 0, unmeasured: 0, reviewUnmeasured: 0, last: 0 };
  for (const p of rows) {
    const reviewGap = unmeasuredReviews.get(p.name) || 0;
    const row = el('tr');
    row.append(el('td', { textContent: projectLabel(p.name) }));
    row.append(el('td', { className: 'fig', textContent: String(p.campaigns) }));
    row.append(el('td', { className: 'fig', textContent: String(p.passed) }));
    row.append(sizeCell(p.campaign_bytes, p.unmeasured, measuring));
    row.append(sizeCell(p.review_bytes, reviewGap, measuring));
    row.append(el('td', { textContent: dayOf(p.last_activity) }));
    body.append(row);
    total.campaigns += p.campaigns; total.passed += p.passed;
    total.bytes += p.campaign_bytes; total.reviews += p.review_bytes;
    total.unmeasured += p.unmeasured; total.reviewUnmeasured += reviewGap;
    total.last = Math.max(total.last, p.last_activity || 0);
  }
  table.append(body);

  if (rows.length > 1) {
    const foot = el('tr');
    foot.append(el('td', { textContent: 'All projects' }));
    foot.append(el('td', { className: 'fig', textContent: String(total.campaigns) }));
    foot.append(el('td', { className: 'fig', textContent: String(total.passed) }));
    foot.append(sizeCell(total.bytes, total.unmeasured, measuring));
    foot.append(sizeCell(total.reviews, total.reviewUnmeasured, measuring));
    foot.append(el('td', { textContent: dayOf(total.last) }));
    table.append(el('tfoot', {}, foot));
  }
  box.append(el('div', { className: 'table-scroll' }, table));
}

/** When a size was taken: the clock for today, the date for anything older. */
function measuredWhen(seconds) {
  if (!seconds) return '—';
  return new Date(seconds * 1000).toDateString() === new Date().toDateString()
    ? clockTime(seconds) : dayOf(seconds);
}

/** One folder's size cell. A row being re-measured says so here, and not on the
    page: its earlier figure goes to the hover, so it is not lost, and it is not
    shown as current either. */
function folderSize(size, busy, measuring) {
  const cell = el('td', { className: 'fig' });
  const measurement = size && size.measurement;
  if (busy) {
    cell.textContent = 'measuring…';
    if (measurement && !measurement.missing) cell.title = 'Earlier figure: ' + fmtBytes(measurement.bytes);
  } else if (measurement && measurement.missing) {
    cell.textContent = 'not on disk';
  } else if (measurement) {
    cell.textContent = fmtBytes(measurement.bytes);
  } else if (size && size.error) {
    cell.textContent = 'could not measure';
    cell.title = size.error;
  } else {
    cell.textContent = measuring ? 'measuring…' : 'not measured';
  }
  return cell;
}

/** Every campaign and every review on its own line, each with its own Refresh.
    The projects table above adds folders together, and a total cannot say which
    one is out of date; this is where one folder is looked at and measured again. */
function drawStorageFolders(data, measuring) {
  const box = $('st-folders');
  box.replaceChildren();
  const campaigns = data.campaigns || [], found = data.reviews || [];
  $('st-folders-count').textContent = campaigns.length || found.length
    ? `— ${plural(campaigns.length, 'campaign')}, ${plural(found.length, 'review')}` : '';
  if (!campaigns.length && !found.length) {
    box.append(el('div', { className: 'empty', textContent: 'No campaigns or reviews on disk yet.' }));
    return;
  }

  const table = el('table', { className: 'inert-table st-folders' });
  table.append(el('caption', { className: 'sr-only', textContent: 'Disk use by campaign and by review' }));
  const head = el('tr');
  [['Folder', ''], ['Kind', ''], ['Size', 'fig'], ['Measured', ''], ['Refresh', 'act']].forEach(([label, kind]) => {
    head.append(el('th', { scope: 'col', className: kind, textContent: label }));
  });
  table.append(el('thead', {}, head));

  const body = el('tbody');
  const line = (entry, kind, name, tags) => {
    const busy = storageUi.rowBusy.has(entry.key);
    const row = el('tr', { className: busy ? 'is-measuring' : '' });
    const first = el('td', {}, [el('b', { textContent: name })]);
    for (const [text, className] of tags) first.append(' ', el('span', { className: 'rv-tag ' + className, textContent: text }));
    row.append(first);
    row.append(el('td', { textContent: kind }));
    row.append(folderSize(entry.size, busy, measuring));
    const when = el('td', { textContent: measuredWhen(entry.size && entry.size.measured_at) });
    if (!busy && entry.size && entry.size.measurement && entry.size.stale) {
      when.append(' ', el('span', { className: 'sibs', textContent: 'out of date',
        title: 'The folder has changed since it was measured' }));
    }
    row.append(when);

    const button = el('button', { type: 'button', className: 'ghost st-copy st-remeasure',
      textContent: busy ? 'Measuring…' : 'Refresh', disabled: busy });
    button.dataset.key = entry.key;
    button.setAttribute('aria-label', `Measure the size of the ${kind.toLowerCase()} ${name} again`);
    button.onclick = () => refreshRow(entry.key, name);
    row.append(el('td', { className: 'act' }, button));
    body.append(row);
  };
  for (const c of campaigns) {
    line(c, 'Campaign', c.name, c.archived ? [['archived', 'unknown']] : []);
  }
  for (const r of found) {
    line(r, 'Review', r.name, r.orphan ? [['campaign gone', 'unset']] : []);
  }
  table.append(body);
  box.append(el('div', { className: 'table-scroll' }, table));
}

function drawStorageDisks(data) {
  const box = $('st-disks');
  box.replaceChildren();
  const disks = data.disks || [];
  if (!disks.length) {
    box.append(el('div', { className: 'empty', textContent: 'No results folder to measure.' }));
    return;
  }
  for (const disk of disks) {
    const line = el('div', { className: 'st-disk' });
    const roots = el('div', { className: 'st-roots' });
    for (const root of disk.roots || []) roots.append(el('code', { textContent: root }));
    line.append(roots);

    if (disk.error || !disk.total) {
      line.append(el('span', { className: 'st-bad',
        textContent: disk.error ? 'Could not read: ' + disk.error : 'No size reported.' }));
      box.append(line);
      continue;
    }
    const part = Math.min(1, disk.used / disk.total);
    const level = part >= 0.9 ? 'high' : part >= 0.75 ? 'mid' : 'low';
    // aria-hidden: the sentence beside it says the same in words, and the colour
    // is the redundant channel.
    const track = el('div', { className: 'mem' });
    track.setAttribute('aria-hidden', 'true');
    track.append(el('i', { className: 'lvl-' + level, style: `width:${part * 100}%` }));
    line.append(track);
    const words = el('span', { className: 'st-free' });
    words.append(el('b', { textContent: fmtBytes(disk.free) + ' free' }));
    words.append(` of ${fmtBytes(disk.total)} · ${Math.round(part * 100)}% used`
      + (level === 'high' ? ' · nearly full' : ''));
    line.append(words);
    box.append(line);
  }
}

function drawStorageReclaim(data, measuring) {
  const box = $('st-reclaim');
  box.replaceChildren();
  const items = data.reclaimable || [];
  const can = items.filter(i => i.reclaimable), cannot = items.filter(i => !i.reclaimable);
  const sum = can.reduce((n, i) => n + (i.bytes || 0), 0);
  $('st-reclaim-count').textContent = can.length ? `— ${fmtBytes(sum)} in ${plural(can.length, 'item')}` : '';

  if (!items.length) {
    box.append(el('div', { className: 'empty', textContent: measuring
      ? 'Nothing to report until the measuring finishes.'
      : 'Nothing found. Items appear here once the folders have been measured.' }));
    return;
  }
  box.append(el('p', { className: 'help',
    textContent: 'This reports and never removes. Each item says what it costs to lose and why it is or is not safe. '
      + 'To remove one, use the path in a shell, yourself.' }));
  const put = (label, list) => {
    if (!list.length) return;
    box.append(el('div', { className: 'st-sub', textContent: `${label} · ${list.length}` }));
    for (const item of list) box.append(reclaimItem(item));
  };
  put('Reclaimable', can);
  put('Not reclaimable', cannot);
}

function reclaimItem(item) {
  const card = el('div', { className: 'st-item' });
  const head = el('div', { className: 'st-item-head' });
  head.append(el('b', { textContent: item.name }));
  if (item.campaign) head.append(el('span', { className: 'dim', textContent: item.campaign }));
  head.append(el('span', { className: 'st-tag ' + (item.reclaimable ? 'yes' : 'no'),
    textContent: item.reclaimable ? 'Reclaimable' : 'Not reclaimable' }));
  head.append(el('span', { className: 'st-size', textContent: fmtBytes(item.bytes) }));
  card.append(head);

  // The server's own words, whole. The reason is the point: 2_Refolded looks like
  // the obvious thing to clear and is the review's input, and a paraphrase here
  // would be where that got lost.
  if (item.reason) card.append(el('p', {}, [el('b', { textContent: 'Why: ' }), item.reason]));
  if (item.cost) card.append(el('p', {}, [el('b', { textContent: 'If it goes: ' }), item.cost]));
  const facts = [];
  if (item.campaign_state) facts.push(`campaign is ${item.campaign_state}`);
  if (item.requires_terminal) facts.push('only once the campaign has finished');
  if (facts.length) {
    const line = facts.join(' · ');
    card.append(el('p', { className: 'st-facts', textContent: line.charAt(0).toUpperCase() + line.slice(1) }));
  }
  card.append(pathRow(item.path, item.name));
  return card;
}

/** The selection bar for the archived list: the Jobs tab's bar, with the one
    action that applies here. Hidden until something is ticked. */
function drawStoragePickedBar(data) {
  const bar = $('st-picked-bar');
  const archived = data ? data.archived || [] : [];
  const here = archived.filter(a => storageUi.picked.has(a.folder));
  bar.classList.toggle('hidden', !here.length);
  if (!here.length) return;
  $('st-picked-count').textContent = `${here.length} selected`;
  // One restore at a time: a second press would send the same ids again.
  $('st-picked-restore').disabled = here.some(a => storageUi.restoring.has(a.folder));
}

function restoreSelected() {
  const data = storageUi.data;
  if (!data) return;
  const chosen = (data.archived || []).filter(a => storageUi.picked.has(a.folder));
  if (chosen.length) restoreCampaigns(chosen);
}

function drawStorageArchived(data, measuring) {
  const box = $('st-archived');
  box.replaceChildren();
  const archived = data.archived || [];
  $('st-archived-count').textContent = archived.length ? `— ${archived.length}` : '';
  if (!archived.length) {
    drawStoragePickedBar(data);
    box.append(el('div', { className: 'empty', textContent: 'No archived campaigns. Archive one from the Jobs tab.' }));
    return;
  }
  drawStoragePickedBar(data);
  const measured = new Set(data.campaigns.filter(c => c.size && c.size.measurement).map(c => c.folder));
  for (const entry of archived) {
    const busy = storageUi.restoring.has(entry.folder);
    const row = el('div', { className: 'job' + (storageUi.picked.has(entry.folder) ? ' is-picked' : '') });

    // The Jobs tab's tick, and the same bar below: a set is chosen here and acted
    // on from the bar. Held in storageUi.picked, not on the row, since this list
    // is rebuilt on every poll.
    const tick = el('input', { type: 'checkbox', className: 'tick',
      checked: storageUi.picked.has(entry.folder), disabled: busy });
    tick.setAttribute('aria-label', 'Select ' + entry.name);
    tick.onchange = () => {
      storageUi.picked.has(entry.folder) ? storageUi.picked.delete(entry.folder) : storageUi.picked.add(entry.folder);
      row.classList.toggle('is-picked', storageUi.picked.has(entry.folder));
      drawStoragePickedBar(storageUi.data);
    };
    row.append(tick);
    row.append(el('b', { textContent: entry.name }));
    const size = measured.has(entry.folder)
      ? fmtBytes(entry.bytes) + (entry.review_bytes ? ` + ${fmtBytes(entry.review_bytes)} of reviews` : '')
      : (measuring ? 'measuring…' : 'not measured');
    const meta = [projectLabel(entry.project || ''), size];
    if (entry.finished_at) meta.push('finished ' + dayOf(entry.finished_at));
    row.append(el('span', { className: 'meta', textContent: meta.join(' · ') }));

    const restore = el('button', { type: 'button', className: 'ghost',
      textContent: busy ? 'Restoring…' : 'Restore', disabled: busy });
    restore.setAttribute('aria-label', `Restore ${entry.name}`);
    restore.onclick = () => restoreCampaigns([entry]);
    row.append(el('span', { className: 'actions' }, restore));
    row.append(pathRow(entry.folder, entry.name));
    box.append(row);
  }
}

// ───────────────────────────────────────────────────────────── wiring

function show(which) {
  for (const view of ['design', 'jobs', 'results', 'review', 'storage']) {
    $('view-' + view).classList.toggle('hidden', view !== which);
  }
  document.querySelectorAll('.tab').forEach(tab => {
    tab.classList.toggle('is-on', tab.dataset.view === which);
    // On a phone the strip scrolls sideways, and a tab chosen from somewhere else
    // (the Results button on a campaign) can be off the edge.
    if (tab.dataset.view === which) tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
  if (which === 'design') refreshCluster();
  if (which === 'jobs') refreshJobs();
  if (which === 'results') { loadResults(); loadReviewList(); }
  if (which === 'review') loadReviewList();
  // The measuring poll belongs to this tab alone: leaving it stops the timer.
  if (which === 'storage') loadStorage(); else stopStoragePoll();
}

document.addEventListener('mouseup', () => { dragging = null; });

async function boot() {
  // A new key on purpose: bc2_owner holds someone's name, and reusing it would
  // pre-fill the project box with it on the first load after the rename.
  $('project').value = localStorage.getItem('bc2_project') || '';
  $('project').oninput = () => localStorage.setItem('bc2_project', $('project').value.trim());

  document.querySelectorAll('.tab').forEach(tab => {
    tab.onclick = () => show(tab.dataset.view);
  });

  document.querySelectorAll('.src').forEach(button => {
    button.onclick = () => {
      document.querySelectorAll('.src').forEach(b => b.classList.toggle('is-on', b === button));
      for (const which of ['shipped', 'pdb', 'upload']) {
        $('src-' + which).classList.toggle('hidden', which !== button.dataset.src);
      }
    };
  });

  document.querySelectorAll('.mode').forEach(button => {
    button.onclick = () => {
      state.mode = button.dataset.mode;
      document.querySelectorAll('.mode').forEach(b => b.classList.toggle('is-on', b === button));
    };
  });

  document.querySelectorAll('.dmode').forEach(button => {
    button.onclick = () => {
      designColouring = button.dataset.dmode;
      document.querySelectorAll('.dmode').forEach(b => b.classList.toggle('is-on', b === button));
      paintDesign();
      state.designViewer && state.designViewer.render();
    };
  });

  try {
    state.presets = await api('/api/presets');
  } catch (problem) {
    toast('Could not reach the server: ' + problem.message, true);
    return;
  }
  buildShipped();
  buildFormats();
  applyFormat();
  buildProperties();
  buildRootChoices();
  const rootPick = $('results-root');
  rootPick.value = localStorage.getItem('bc2_root')
    || (state.presets.result_roots || [state.presets.results_root])[0] || '';
  rootPick.oninput = describeRoot;
  describeRoot();
  drawTargets();
  await refreshGpus();
  refreshCluster();
  $('cluster-refresh').onclick = () => refreshCluster(true);
  $('cluster-gpu').onchange = redrawCluster;
  $('picked-move').onclick = openMover;
  $('picked-archive').onclick = applyArchive;
  $('picked-clear').onclick = () => { picked = new Set(); refreshJobs(); };
  $('mover-apply').onclick = applyMove;
  $('mover-cancel').onclick = () => $('mover').classList.add('hidden');

  $('add-shipped').onclick = () => {
    const found = state.presets.targets.find(t => t.name === $('shipped-target').value);
    if (!found) return;
    for (const entry of found.targets) {
      addTarget({ name: entry.name, path: entry.target_path, chains: [],
                  chain: entry.chains || 'A', hotspots: entry.hotspots, coldspots: entry.coldspots });
    }
    // The shipped presets name their chains but not their residue lists; ask the
    // server so the viewer and the picker agree about what is there.
    refreshChains(state.targets.length - 1);
  };

  $('add-pdb').onclick = async () => {
    const code = $('pdb-id').value.trim();
    if (!code) return;
    const button = $('add-pdb');
    button.disabled = true;
    try {
      const body = new FormData();
      body.append('entry', code);
      addTarget(await api('/api/fetch-pdb', { method: 'POST', body }));
      toast(`Loaded ${code.toUpperCase()}`);
    } catch (problem) { toast(problem.message, true); }
    finally { button.disabled = false; }
  };

  const drop = $('drop');
  drop.onclick = () => $('file').click();
  $('file').onchange = () => upload($('file').files[0]);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = (e) => {
    e.preventDefault(); drop.classList.remove('over');
    if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
  };

  $('chain-pick').onchange = () => {
    const target = current();
    if (!target) return;
    target.chain = $('chain-pick').value;
    target._analysisKey = null;
    loadViewer().then(loadAnalysis);
    drawTargets();
  };

  $('clear-spots').onclick = () => {
    const target = current();
    if (!target) return;
    target.hotspots.clear();
    target.coldspots.clear();
    afterPickChange();
  };

  for (const field of ['hotspots', 'coldspots']) {
    $(field).onchange = () => {
      const target = current();
      if (!target) return;
      target[field] = spanToSet($(field).value, chainList(target)[0] || '');
      afterPickChange();
    };
  }

  document.querySelectorAll('.look').forEach(button => {
    button.onclick = () => {
      const which = button.dataset.look;
      state.look[which] = !state.look[which];
      button.classList.toggle('is-on', state.look[which]);
      paintTarget();
      state.viewer && state.viewer.render();
    };
  });

  $('suggest').onclick = () => {
    const target = current();
    if (!target || !target.residues) return;
    const picks = target.residues.filter(r => r.suggested);
    if (!picks.length) return toast('Nothing stands out as an exposed aromatic or charged residue.');
    picks.forEach(r => {
      const key = residueKey(target, r.chain, r.number);
      target.hotspots.add(key); target.coldspots.delete(key);
    });
    afterPickChange();
    toast(`Marked ${picks.length} exposed aromatic and charged residues — now narrow them to one patch`);
  };

  $('trim-selected').onclick = () => {
    const target = current();
    if (!target || !target.hotspots.size) return toast('Mark some residues first', true);
    // A margin either side, so the epitope keeps the scaffold that shapes it.
    const numbers = [...target.hotspots].map(k => +String(k).replace(/^[A-Za-z]*/, ''));
    const low = Math.min(...numbers) - 12, high = Math.max(...numbers) + 12;
    const within = target.residues.filter(r => r.number >= low && r.number <= high);
    $('trim-keep').value = within.length
      ? `${within[0].number}-${within[within.length - 1].number}` : '';
  };

  $('trim-go').onclick = doTrim;

  for (const field of ['len-min', 'len-max']) $(field).oninput = refreshEstimate;

  setUpNotifications();
  $('result-job').onchange = loadResults;
  $('result-project').onchange = () => {
    resultsUi.project = $('result-project').value;
    // A campaign from outside the chosen project would leave the page about
    // something the selects no longer say.
    const job = jobFor($('result-job').value);
    if (job && resultsUi.project !== ALL_PROJECTS && projectOf(job) !== resultsUi.project) {
      $('result-job').value = ALL_CAMPAIGNS;
    }
    loadResults();
  };
  $('results-reload').onclick = loadResults;
  $('results-export').onclick = exportResults;
  $('st-refresh').onclick = refreshStorage;
  $('st-picked-restore').onclick = restoreSelected;
  $('st-picked-clear').onclick = () => { storageUi.picked = new Set(); drawStorage(); };
  $('submit').onclick = submit;

  setInterval(() => { refreshJobs(); refreshGpus(); }, 5000);
  setInterval(pullLog, 3000);
  setUpReview();
  refreshJobs();
}

/** The upload and fetch endpoints return their chains; a shipped target's preset
    names only the chain to design against, so its residue lists are asked for here. */
async function refreshChains(index) {
  const target = state.targets[index];
  if (!target || target.chains.length) return;
  try {
    const answer = await api('/api/chains?path=' + encodeURIComponent(target.target_path));
    target.chains = answer.chains || [];
    // The preset's chain selection wins, and it may name several ("A,B"). Only
    // fall back when none of the chains it names is actually in the structure --
    // comparing the whole spec against single chain ids would throw away the rest
    // of an assembly and silently reassign its hotspots to the first chain.
    const named = chainList(target);
    const real = new Set(target.chains.map(c => c.chain));
    const usable = named.filter(c => real.has(c));
    if (usable.length) target.chain = usable.join(',');
    else if (target.chains.length) target.chain = target.chains[0].chain;
    drawTargets();
    if (index === state.active) loadViewer();
  } catch { /* the viewer will report anything really wrong */ }
}

async function upload(file) {
  if (!file) return;
  try {
    const body = new FormData();
    body.append('file', file);
    addTarget(await api('/api/upload', { method: 'POST', body }));
    toast(`Loaded ${file.name}`);
  } catch (problem) { toast(problem.message, true); }
}

boot();
