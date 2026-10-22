# The viewer

The viewer is the part of Binderscope that a person looks at: five static HTML pages and a few shared scripts that read the data blobs of a bundle and run entirely in the browser. This document is for a developer or an advanced user and covers what is in `viewer/`, how the pages are assembled and how their data reaches them, the shared scripts, profiles and capability flags, the checks and the verdict rule, the decisions store, offline behaviour, and how to change a page and rebuild a bundle. Each page has its own document: [viewer_board.md](viewer_board.md), [viewer_inspector.md](viewer_inspector.md), [viewer_map.md](viewer_map.md) and [viewer_query.md](viewer_query.md); how to use the pages is in [GUIDE.md](GUIDE.md).

There is no framework and no build step for the viewer itself: plain HTML, JavaScript and CSS, with 3Dmol.js vendored. Nothing is loaded from a network unless you ask for the optional Google Fonts link (see "Offline and privacy").

## What is in `viewer/`

```
viewer/
  pages/     index.html board.html inspector.html map.html query.html   page templates (guide.html is built, see below)
  js/        bs.js common.js family.js mini3d.js                         shared scripts
  css/       shared.css                                                 light and dark palettes, header, shared components
  vendor/    3Dmol-min.js  3Dmol-LICENSE.txt  README.md                  vendored 3Dmol.js (BSD-3-Clause); do not edit
  assets/    binderscope-mark.svg                                       the logo mark, inlined in the header
  guide/  content.html  guide.template.html                       source of the guide page
```

| Page | What it is for | Blobs it loads | Scripts |
|---|---|---|---|
| `index.html` (Overview) | The project at a glance: counts, a card per page, "What stands out" (statements counted from the data and written by the exporter in `campaign.highlights`, else `manifest.highlights`, each may link to a page or a design), a table of runs, the funnel of each run, a row of capability chips ("What this bundle carries"), the review file (reviewer name, save, load) and the build record from the manifest (tool, time, bundle id, inputs, counts check, warnings, privacy). | campaign, manifest | `bs.js` |
| `board.html` | Triage backbone families: [viewer_board.md](viewer_board.md) | campaign, manifest; homolog after start when `species_panel` is on | `mini3d.js`, `bs.js`, `common.js`, `family.js` |
| `inspector.html` | One design in depth: [viewer_inspector.md](viewer_inspector.md) | inspect, homolog, manifest | `3Dmol-min.js`, `mini3d.js`, `bs.js`, `common.js` |
| `map.html` | Binding modes and hotspot coverage: [viewer_map.md](viewer_map.md) | campaign, homolog, manifest | `mini3d.js`, `bs.js`, `common.js` |
| `query.html` | Rule stack, funnel and near-miss rescue: [viewer_query.md](viewer_query.md) | campaign, homolog, manifest | `mini3d.js`, `bs.js`, `common.js` |
| `guide.html` | The user guide: the five pages, workflows and how to read each check; no data | none | its own inline script |

The blobs themselves are described in [data_contract.md](data_contract.md) and [schema/README.md](schema/README.md).

## A built site

`bundle.py` (through `viewer_build.render_site`) writes a folder that stands alone:

```
bundles/my-target/
  index.html board.html inspector.html map.html query.html [guide.html]
  assets/css/shared.css   assets/js/{bs,common,family,mini3d}.js   assets/3Dmol-min.js   assets/3Dmol-LICENSE.txt
  assets/binderscope-mark.svg   assets/favicon.svg   [assets/guide/...]
  data/{campaign,inspect,homolog,profile,manifest}.json    canonical copies, for tools
  data/{campaign,inspect,homolog,profile,manifest}.js      the same objects as scripts, for the pages
  structures/<design id>.pdb and .js                       written by bundle.py, see export.md
  reviews/README.txt                                       where saved review files go
  README.txt                                               written by bundle.py
```

Shared files are copied into the bundle, never linked to the source folder, so editing `viewer/` changes no existing bundle until it is rebuilt.

## How a page is assembled

A template is plain HTML with four markers, each of which must occur exactly once. `render_page` replaces them and sets `<body data-page="<page>">`; an unknown `<!--@x-->` marker or a missing `<body>` is a `BuildError`.

| Marker | Replaced by |
|---|---|
| `<!--@head-->` | charset and viewport meta tags, the Content-Security-Policy meta tag when asked for, `<title>` (`profile.brand.titles.<page>`, else a neutral default; the overview adds the target label and the brand name), the theme script, the favicon link, the Google Fonts link only with `fonts="google"`, and `assets/css/shared.css`. The page's own `<style>` follows it. |
| `<!--@brand-->` | A link to `index.html` with the inline mark, `profile.brand.name` (default Binderscope) and the short target label `profile.target.short`. |
| `<!--@badge-->` | The data badge of `profile.badge` (`text`, and `class`, which selects the badge style: `mock`, `real` or `run`) with the tooltip of the text key `badge.title.<page>`; a hidden empty span when the profile has no badge. |
| `<!--@nav-->` | The run-filter slot `#bsRunSlot` and the links of the pages that were built (Overview, Board, Inspector, Map, Query, and Guide when present), with `aria-current="page"` on the current one. |

Everything else is the page's own: crumbs, buttons, panels, styles and scripts. Script tags are explicit in the template, in load order: the shared scripts, the data scripts the page reads, then the page script, which starts with `const RAW = BS.data('campaign');` (and `HOM` and `INS` likewise). The inspector loads `assets/3Dmol-min.js` in `<head>`. Other code (links, scripts) refers to element ids and `data-` attributes, so keep them when you edit a template.

```python
from binder_review.viewer_build import render_site, render_page
index = render_site(project_or_data_dir_or_None, out_dir, profile=None, data_files=None, manifest=None, pages=None,
                    guide=None, fonts="google", csp=False, created=None, name=None, clean=False, inject_profile=True)
html = render_page("board", profile, run=campaign["run"], available=["index", "board"], guide=False, fonts="system", csp=False)
```

- **Input.** A folder with `campaign.json`, `inspect.json`, `homolog.json`, `profile.json` and `manifest.json` (here or in `data/`), optionally `structures/`; `data_files={blob: path or object}` overrides or adds blobs. `bundle.write_bundle` passes the blobs of the project's `export/` folder.
- **Profile.** The argument, else `data/profile.json`, else the profile inside the campaign or inspect blob. The profile must be the same object in every copy: a blob without one gets it embedded (`inject_profile`), a blob with a different one is a `BuildError`.
- **Pages built.** `index` always, and every page whose data is present: board needs campaign; map and query need campaign and homolog; inspector needs inspect and homolog. A page requested without its data is an error. The nav lists the built pages only.
- **Manifest.** The one given, else `data/manifest.json`, else a minimal one is written (its `bundle.id` is the first 16 hex characters of a hash of the blobs). Every page loads `data/manifest.js`, so a manifest always exists. With a fixed `created` and the same inputs two builds are byte-identical.
- **Guide.** `guide=None` includes `viewer/pages/guide.html` (and `viewer/assets/guide/` when it exists) if the file exists and adds the nav link. With `fonts` other than `google` a Google Fonts link in that page is removed.
- **Checks after writing.** Every relative `src` and `href` of the built pages must exist in the site (`check_links`); a template that points at a missing file fails the build.
- **CSP.** `csp=True` adds a Content-Security-Policy meta tag to every page: scripts from the same folder and inline scripts, no eval, no remote scripts, `connect-src 'none'`, and fonts from Google only with `fonts="google"`. It is off by default. `csp` may also be a policy string.
- **Where the sources are.** `viewer_dir()` finds `viewer/` next to `src/` in a source checkout (or inside the package folder); `BINDERSCOPE_VIEWER` points the build at another copy and is an error if that folder has no `pages/` and `js/bs.js`. `viewer/` is not package data (only `profiles/*.json` is), so run the tool from an editable install of a checkout or set the variable.
- **Command line.** `python -m binder_review.viewer_build DATA_DIR OUT_DIR [--profile FILE] [--fonts google|system] [--csp] [--no-guide] [--clean] [--name NAME]` writes a site from a folder of blobs. The usual route is `binder-review build-bundle` ([cli.md](cli.md)).

`render_template` is the Python twin of `BS.render` (below), for text that has to be rendered outside the browser.

## How data reaches a page opened from disk

A page opened from disk (`file://`) cannot read other files with `fetch()`: browsers block it. A `<script src>` of a file in the same folder always loads. So every blob exists twice: `data/<blob>.json` for tools, and `data/<blob>.js`, which assigns the same object to globals:

```js
window.BINDERSCOPE = window.BINDERSCOPE || {};
window.BINDERSCOPE.campaign = window.RAW = { ...the campaign blob... };
```

The globals are `RAW` (campaign), `INS` (inspect), `HOM` (homolog), `PROFILE` (profile) and `MANIFEST`. The wrapper assigns instead of declaring with `var`, so a page can still declare `const RAW = BS.data('campaign')`. `BS.data(name)` returns the blob or stops the page with a visible message ("data/<name>.js is missing or did not load"); `BS.blob(name)` returns the blob or null.

Structures follow the same pattern. `structures/<design id>.js` (a `/` in an id is written `__`) assigns the PDB text to `window.BINDERSCOPE.structures[<design id>]`. The inspector loads it when a design opens and shows a loading state; `data/inspect.js` is written without the binder structures by default so that the page opens fast (`--embed-structures` keeps them in; `data/inspect.json` always has them).

## The shared scripts

| Script | Global | What it does |
|---|---|---|
| `bs.js` | `BS`, `PF` | The core. Needs nothing else and is loaded first. Data access (`BS.data`, `BS.blob`, `BS.fail`), the profile and capability flags, the text templates, links and hash parsing, the decisions store, the order list and its header chip (`BS.order`), guarded storage, the run filter, dialog focus handling, small helpers (`BS.esc`, `BS.fmtN`, `BS.download`, `BS.pl`, `BS.plural`, `BS.page`, `BS.projectId`, `BS.badge`, `BS.tipsFor`). On `DOMContentLoaded` it sets the badge, mounts the run filter, adds a skip link and a visually hidden `h1` where a page has none, and gives text that a page cuts with an ellipsis a `title` on first mouse-over (profiles of `kind` `run` only). |
| `common.js` | `Common` | The data layer and the judgement shared by board, map, query and inspector: `Common.load(raw)` indexes the campaign (residue maps, per-design arrays, `hot_of`, quality score, checks and verdict for the preset in force), the threshold presets, `evalChecks`, `contextVerdict`, `quality`, the quality-diversity selection `select`, the species helper `Spec`, tooltips (`tip`), the "What to try" popover (`tips`), colour ramps and theme helpers. Two namespaces hang off it for features that several pages draw the same way: `Common.para`, the paratope of a scaffolded campaign (segments, labels, framework; [paratope.md](paratope.md)), and `Common.term`, the terminus analysis (tag table, grades, the end a family picks, the evidence lines, the tag-plan CSV; [termini.md](termini.md)). Each has the one predicate a page asks before it draws anything (`Common.para.present(run)`, `Common.term.has(design)`), so a run or a design without the data leaves the feature out instead of drawing an empty one. |
| `family.js` | `Family` | Family analytics, used by the board: `group` (designs into backbone families), `cluster` (average-linkage clustering of families cut at a distance), `setFold` (the TM-score matrix), `hill`, `coverage`, `chao1`, `chaoJost`, `wilson`, `curve`, `fit` and `advice` (the plain-language level and forecast). See [families.md](families.md). |
| `mini3d.js` | `Mini3D` | A dependency-free 2D-canvas renderer for protein complexes: depth-sorted shaded atoms, smoothed CA tubes, markers with label placement, translucent planes. `Mini3D.Viewer` handles drag to rotate, shift or right drag to pan, wheel to zoom, double-click to reset, hover and click picking. `Mini3D.drawThumb` draws the small fixed-view pictures of tiles and rows. Board, map and query use it; the inspector uses 3Dmol.js and falls back to `Mini3D.Viewer` when 3Dmol.js or WebGL is not available. |

## Profile and capability flags

A profile is a JSON file or the built-in default profile; the exporter completes it from the data and embeds it in the campaign and inspect blobs (and writes `data/profile.json`). Its sections are described in [profile_spec.md](profile_spec.md) and [schema/profile.schema.json](schema/profile.schema.json).

- **`BS.profile`** is the embedded profile laid over neutral defaults; `BS.hasProfile` says whether one was found. Without a profile the pages still work: they use the defaults and, for older blobs, the blob's own `caps` object.
- **`BS.caps`** has every flag of `BS.CAP_FLAGS` as a boolean; a flag absent from `profile.caps` is false. `PF.cap('olig')` reads one. `BS.capsFor(raw)` and `Common.caps` give the same set for a blob. The list of flags exists in `viewer/js/bs.js`, in `export.py` and in the profile schema; keep the three in step when you add one. Pages ask which flag is on, not which project they show.
- **Where flags come from.** The exporter writes `caps` from the data: a flag is true exactly when its data is present, and a profile can only switch one off (`caps.<flag>: false`).

| Group | Flags |
|---|---|
| Run structure | `families` (some backbone has more than one sequence), `has_traj` (generation order known), `traj_total` (attempted trajectories known for every run), `has_fold` (TM-align matrix), `multi_run`, `rejected` (rejected designs with structures), `reviews` (decisions persist; on in every export of the tool) |
| Context | `olig` (other-subunit data), `rest_ctx` (removed-region data), `glycan`, `membrane`; `flag_region` and `footprints` are for data sources that carry that information and are never set by the BindCraft export |
| Specificity | `species_panel`, `paralog_panel`, `consurf` (needs the ConSurf option and data), `decoy_cofold` |
| Scores | `ipsae`, `min_ipae`, `complex_rmsd`, `dg`, `sc`, `iptm_b`, `ipae_norm`, `surf_hyd`, `hotspot_rmsd`, `binder_rmsd`, `seed_spread`, `n_agree`, `esm_plddt`, `ciplddt`: true when some design carries the metric |
| Predictors and scans | `models`, `has_boltz`, `has_seeds`, `has_pae`, `ddg_scan`: extra predictors, repeat seeds, PAE matrices, alanine-scan values. A BindCraft run folder does not provide them, so the export leaves them false |
| Lab results | `outcomes` (designs carry `outcome`), `weights` (designs carry a sampling weight `wt`): optional, for data that has lab results; the BindCraft export leaves them false. They add a **Show lab results** switch and lab panels to the pages that support it |

- **Text keys.** Wording that depends on the project (breadcrumbs, check explanations, finding texts, funnel labels, tips) is read with `PF.t(key, tokens, default)`: the template in `profile.text[key]`, else the default written in the page. A profile therefore only defines the keys whose neutral wording is wrong for it. A template may contain `{token}` (from the call), `{token|n}` (a number with thousands separators) and `{@profile.path}` (a value of the profile); an unknown token stays visible, an unresolved `{@path}` renders empty. `PF.has(key)` asks whether a template exists, `PF.missing()` lists the keys asked for so far that the profile lacks, `PF.get('target.short', default)` reads a dotted path and `BS.tipsFor(page, default)` returns `profile.tips[page]` or the default tips. [schema/profile.keys.json](schema/profile.keys.json) lists the registered keys with their tokens.
- **`profile.kind`** is `run` for the output of a tool run; the schema also allows `synthetic` and `dataset`. Several pages test it: the Query page offers its pool switch, rescue, export and rule sets only for `run`, and the board offers the simulated reviewer by default only when it is not `run`.

## Checks, verdicts and the quality score

`Common.evalChecks(design, preset)` judges every design with six checks, plus Context when `olig` or `rest_ctx` is on. A missing metric switches its part off; it never fails a design. Context is added by `Common.addContextCheck()` as a soft check unless `checks.context.hard` is true, which the exporter sets when the reference is an experimental structure or the user chose it ([profile_spec.md](profile_spec.md), Context standing); a soft Context failure can therefore lead to REVIEW but not to NO PASS. When neither flag is on, `Common.contextOff()` says why (`no_reference` or `reference_lacks`, from `profile.caps_off`) and the pages show the check as not run instead of leaving it out; `Common.contextAdvisoryNote(state)` is the clause the Board and the Inspector add to a Context reason that cannot decide.

| Check | Kind | Rule |
|---|---|---|
| Fold | hard | binder pLDDT at least `plddt`, and pTM at least `ptm` when present |
| Interface | hard | native basis: i_pTM at least `iptm_b` and normalised i_pAE at most `ipae_norm`; otherwise ipSAE at least `ipsae` and min iPAE at most `min_ipae`. "Native" is `checks.interface.basis` `native`, or `auto` without `ipsae` |
| Pose | hard | re-score RMSD at most `complex_rmsd` (fail); without it binder-alone RMSD at most `binder_rmsd` (warn if above); else pass |
| Chemistry | soft | count of misses among shape complementarity, interface ΔG, buried unsatisfied polars (a count, or a density with `checks.physics.unsat_basis` `per_1000_A2`) and hydrophobic patch: none pass, one or two warn, more fail |
| Epitope | soft | hotspots touched at least `min(hotspots, hot_of)` pass, at least one warn, none fail. A run without hotspots has `hot_of` 0, so its designs pass and none is off-site |
| Sequence | soft | any liability of severity `bad` fail, `warn` warn |
| Context | advisory unless `profile.checks.context.hard` is true | the worst of: other subunit (`olig_n3` atoms within 3 Å at least `fail_n3` fail; any atom within 3 Å, `olig_min` below `warn_min`, `olig_n` at least `warn_buried` or flagged-region contacts warn), rest of the protein (`rest_n3` fail from `fail_n3`; warn for any atom within 3 Å or `rest_min` below `warn_min`), cut ends (`cut_n` at least `warn_n` warn), covered residues (`cov_n` at least `warn_n` warn) |

The verdict is **NO PASS** when a hard check fails, **REVIEW** when a soft check fails or two checks warn, otherwise **PASS**. These are screens, not verdicts on the binder; every page says so.

Thresholds come from a preset. The built-ins are `lit` ("Literature-informed", the default), `relaxed` (BindCraft-style) and `strict`; `profile.checks.presets` adds more (each a `name` and a `v` that overrides `lit`), and `profile.checks.preset_default` picks the one that is in force at load. The map's selector calls `Common.applyPreset`, which recounts every design; the query page copies the values of the chosen preset into its rules.

| Key | Meaning | `lit` | `relaxed` | `strict` |
|---|---|---|---|---|
| `plddt`, `ptm` | binder pLDDT, pTM, minimum | 0.80, 0.75 | 0.80, 0.55 | 0.88, 0.85 |
| `ipsae`, `min_ipae` | ipSAE minimum, min iPAE maximum (Å) | 0.60, 8.0 | 0.50, 11 | 0.70, 5.5 |
| `iptm_b`, `ipae_norm` | i_pTM minimum, normalised i_pAE maximum | 0.70, 0.30 | 0.50, 0.35 | 0.80, 0.25 |
| `complex_rmsd`, `binder_rmsd` | re-score, binder-alone RMSD maximum (Å) | 2.5, 3.0 | 3.5, 3.5 | 2.0, 2.5 |
| `sc`, `dg` | shape complementarity minimum, ΔG maximum (REU) | 0.60, -30 | 0.55, -15 | 0.65, -40 |
| `n_unsat`, `hphob_patch` | buried unsatisfied polars maximum, hydrophobic patch maximum (Å²) | 3, 500 | 4, 650 | 2, 400 |
| `hotspots` | hotspots touched, minimum | 3 | 2 | 4 |

Other limits come from `profile.checks` and fall back to these values when the profile has none: `context.partner` (`fail_n3` 10, `warn_min` 4.0, `warn_buried` 3), `context.rest` (`fail_n3` 10, `warn_min` 4.0), `context.cut` (`k` 5, `warn_n` 2), `context.cover` (`min_area` 10, `warn_n` 2), `context.flag.warn_n` (3), `physics.unsat_density_max` (2.3), `physics.sc_check` and `physics.pack_needs_sc`. The inspector reads further `checks.findings.*` and `checks.context.near` values.

`Common.quality(design)` is the composite quality score used to rank and to pick representatives. It is a weighted mean of terms that exist for the design (a term without a value is dropped and the weights renormalised): ipSAE or i_pTM (0.30), min iPAE or i_pAE (0.15), binder pLDDT (0.10), re-score or binder-alone RMSD (0.15), interface ΔG (0.10), shape complementarity (0.05), unsatisfied polars (0.05), hydrophobic patch (0.05), share of hotspots touched (0.05), and the other-subunit and rest-of-protein terms (0.10 each). `Common.select(pool, k, alpha)` picks designs greedily by `(1 - alpha) x quality + alpha x 0.9 x distance to the picks so far`.

`Common.Spec(HOM)` builds the species panel from `profile.specificity` (`species[]` with `match`, `label`, `key`, `fallback`, `optional`; `show`; `subject`; `kind`; `paralogs.focus.key`) and the homolog blob, and `Spec.stats(residues)` returns the identity over a set of contacted residues per species and per paralog, and the mean conservation grade. Identity over contacted residues is a proxy for cross-reactivity, not a measurement. See [specificity.md](specificity.md).

## The decisions store

`BS.decisions` is an append-only event log, not a map. One decision is one event in the format of [schema/review_event.schema.json](schema/review_event.schema.json): `v` (1), `id` (`ev` plus lowercase hex), `reviewer`, `ts` (UTC, to the second), `unit` (`family` or `design`), `design` (the family id or the design id), `run`, `decision` (`keep`, `maybe`, `reject` or `clear`), `sequences` (the design ids chosen for ordering), `reasons`, `learning`, `note`, `bundle` (the manifest's bundle id), `app` and `supersedes`.

```js
const D = BS.decisions;                       // the store of this bundle
D.setReviewer('Alex'); D.setLearning(false);  // the learning flag is written on every event made after it
D.add({ unit: 'family', design: 'binder_l68_s123456', decision: 'keep', run: 'run01',
        sequences: ['binder_l68_s123456_mpnn3'], reasons: [], note: null });
D.state('family')       // { 'binder_l68_s123456': 'keep' }: current decisions of the current reviewer
D.current({ unit, reviewer })   // Map 'unit\0design' -> event; reviewer '*' = every reviewer
D.undo()                // appends the event that restores the earlier state; supersedes = the undone id
D.clearAll(unit?)       // a clear event for every current decision of the current reviewer
D.list(), D.count(), D.reviewers(), D.onChange(fn)
D.exportJSONL(), D.importJSONL(text)   // union by event id: { added, restored, duplicates, invalid, foreign, reviewers }
D.saveFile(), D.pickFile(), D.loadFile(file)
```

- **Current decision.** For a reviewer, an item (unit and design) has the decision of the event with the latest `ts`; a later line wins a tie; `clear` removes it. Pages show one reviewer at a time (the default name is `you`; a name is trimmed and limited to 80 characters).
- **Undo** appends a restoring event and only undoes decisions made in the current page session.
- **Review files.** `saveFile` offers the events of the current reviewer as `<reviewer>.jsonl` (characters outside letters, digits, `.`, `_` and `-` become `_`), one JSON object per line, through a Blob download, which works from disk. The user moves the file into the bundle's `reviews/` folder; a browser cannot write there itself. `importJSONL` (or `pickFile`, a hidden file input) merges by event id, reports invalid lines with their line number and counts events of another bundle (`foreign`). Loading a file also re-applies, as new events stamped now, every decision the file holds that a later `clear` in this log removed (`restored`), so loading a saved file after "Clear decisions" brings the decisions back; a decision made here since the file was saved is never overwritten. There is no merge view: merging two reviews is loading both files.
- **Persistence.** Every change writes the whole log, the reviewer and the learning flag to local storage under `binderscope.v1.<bundle id>.events`, `.reviewer` and `.learning` (the run filter uses `.runfilter`); the bundle id is `manifest.bundle.id`, else the profile id. Every access is in `try/catch`: when the browser blocks storage the store keeps working in memory and `BS.storage.persistent` turns false, which the overview and the board show. A corrupt stored log is ignored. Pages of one bundle open in two tabs share the log: a tab takes in the events the other stored before it saves and on the `storage` event, so neither overwrites the other. The review file is the durable form; how local storage is scoped for pages opened from disk is up to the browser.
- `BS.createDecisions({ storage, prefix, now, salt, bundle, persist, app })` makes an isolated store (for example one in memory only).

## The order list

The order list is what the decisions add up to: the designs you would send on for ordering and further analysis. It is defined once, in `BS.order` (`bs.js`), and read by the Board's panel, by the header chip and its dialog, and by the files, so the count, the list and the files cannot disagree.

- **What is in it.** A kept family adds the sequences chosen in it (the `sequences` of its event), and its best sequence by quality score when none is chosen. A kept design (`unit: "design"`) adds that design: a sequence kept on its own, or a BindCraft-rejected near miss that was rescued. A design that arrives both ways is listed once. A maybe or a reject adds nothing. Only the current reviewer's decisions count, and the run filter does not hide anything (the `run` column says where each design comes from). The list is in the order the decisions were made.
- **A kept family is not all its sequences.** Keeping 5 families gives 5 sequences unless more are chosen: the Board's sequence table has an Order checkbox for each, and `K` in the Inspector on a sequence adds it to its family's choice. The dialog says this in words and, where the page holds the data, how many of the kept families' accepted sequences are in the list and how many are left out. The Query page's Send to board with Keep is the other route: it writes every sequence of a family that is in the tab it was sent from.
- **Files.** **CSV** (`order-list.csv`: `design_id, run, family, rank, verdict, bindcraft_rank, reasons, source`; `rank` is the position in this list, `source` is `family`, `sequence` or `near-miss`), **FASTA file** and **Copy FASTA** (`order-list.fasta`; the header has the id, run, family, binding mode and the headline score), and **Save review file**. The files are made in the browser. They are written only when every design in the list can be looked up with its sequence on the page; a page that cannot (a rescued near-miss without a structure record is not in the Inspector's data) says so and points to the Board.
- **The chip and the dialog.** `BS.order.mount()` (called by `bs.js` on load) adds **Order list** with a count to the header of every page that has decisions in it, just before the page links; with no decision at all nothing is drawn, with decisions but no keep it reads 0. A click opens the dialog (Esc or Close leaves it, and the page's own keys do nothing while it is open). On the Overview, which does not hold the design data, a click goes to `board.html#order`, which opens the dialog on the Board. Any element with `data-ol-open` opens it too. The dialog and the Board's panel are one function, `BS.order.render(el, opts)`.
- **How a page lets the list look designs up.** `BS.order.use(adapter)` with `row(id)` (id, run, family, mode, sequence, verdict word and class, BindCraft rank, headline score text, status, link), `best(familyId)` and `family(familyId)` (`{ n }`, the accepted sequences on that backbone). `Common.load` registers one for the campaign (`Common.orderAdapter()`), so the Board, the Map and the Query page have it; the Inspector registers its own from the inspect blob. Without an adapter the count still works (a kept family with none chosen counts as one), the files do not.
- **API.** `BS.order.resolve(store?)` returns `{ rows, families, nRows, nFamilies, nFromFamilies, nSeq, nRescue, maybe, reject, decided, reviewer, reviewers }`; `summary(resolved, opts)`, `files(resolved)`, `csv(resolved)`, `fasta(resolved)`, `render(el, opts)`, `open()`, `close()`, `refresh()`. The store argument lets the Board draw its panel from the simulated reviewer's memory-only copy.

## The header, links, theme and run filter

- **Header.** The brand (a link to the overview with the mark, which follows the theme through `currentColor`; the target label shows from 1400 px up), the page's crumbs and buttons, the **What to try** button (`Common.tips`, from `profile.tips.<page>` or generic tips), the badge, the run-filter slot, the **Order list** chip (see "The order list") and the page links. The header wraps on narrow screens instead of squeezing its content.
- **Run filter.** With `multi_run` and more than one run, `bs.js` shows "Runs: All | run01 | run02" in the slot. `BS.runFilter.selected()` is `null` (all) or `[runId]`; `filter(designs)` keeps the designs of the selected run (`design.run`); `onChange(fn)` fires on a change; the choice is stored per bundle and read back by the next page. Estimates (diversity, need more designs) are per run and are never pooled: with all runs selected a page shows per-run numbers side by side or says that the view is descriptive.
- **Links.** `BS.link.design(id, run)` is `inspector.html#d=<id>` (the run is appended as `&run=`, which no page reads); `family(id)` is `board.html#f=<id>`; `mode(m)` is `map.html#m=<m>`; `page(name, hash)`; `parse(hash)` turns `#d=a&run=b` into `{ d: 'a', run: 'b' }` and `#light` or `#dark` into `{ theme }`; `onChange(fn)` listens to `hashchange`. Ids are URL-encoded. The hashes each page understands are in its document: `board.html#f=` and `#d=`; `inspector.html#d=`; `map.html#m=`, `#d=`, `#v=` (and the bare `#fp`, `#tbl`); `query.html#pool=all&tab=near`.
- **Theme.** `prefers-color-scheme`, overridden by a bare `#light` or `#dark` hash (a script in `<head>` sets `data-theme` before the page paints). `shared.css` defines both palettes.
- **Dialogs and landmarks.** `BS.dialog.watch(el, { isOpen, modal })` moves focus into a dialog that a page opens by toggling a class or `[hidden]`, keeps Tab inside a modal one, and returns the focus on closing. `bs.js` adds a skip link to `main` (it focuses `main` and does not change the address, because pages read the hash) and a visually hidden `h1` where a page has none.

## Offline and privacy

- **No network request on open** with the default fonts setting. The bundle commands (`import --bundle`, `build-bundle`) write system fonts unless `--fonts google` is given ([cli.md](cli.md)); the pages, their scripts and `shared.css` contain no URL (besides the SVG namespace) and use no `fetch`, no `XMLHttpRequest` and none of 3Dmol.js's download functions: the only files a page loads are script files of its own folder. With `fonts="google"` a stylesheet link to Google Fonts (IBM Plex Sans and Mono) is added to every page, so an opened page contacts that server; the font stack falls back to system fonts. Note that `render_site` and `python -m binder_review.viewer_build` themselves default to `google`; pass `fonts="system"` or `--fonts system`.
- Data stays in the folder. Decisions stay in the browser's local storage. Review files, order lists, CSV, FASTA and rule sets are made in the browser and saved with a download; the clipboard is used only when you press a copy button. Nothing is uploaded and no server is needed.
- `data/manifest.json` records the tool version, the input files with their paths (as given) and hashes, the runs with their filters, validation results, warnings, and a `privacy` block (`network_requests`, and that no binder sequence or design id was sent). Read it before sharing a bundle outside your group; the review files carry the bundle id.

## Change a page and rebuild a bundle

1. Edit `viewer/pages/<page>.html` (keep the four markers once, the script tags and the ids that other code uses), or `viewer/js/*.js`, or `viewer/css/shared.css`. To change wording only, define text keys in the profile instead.
2. Rebuild: `binder-review build-bundle work/my-target --out bundles/my-target`. It reads `work/my-target/export` and does no analysis, so it takes seconds; `--keep` keeps existing files of the output folder, `--fonts`, `--csp` and `--no-guide` are described in [cli.md](cli.md). For a folder of blobs use `python -m binder_review.viewer_build`.
3. Open `bundles/my-target/index.html` from disk.
4. If a page needs data that the blobs lack, the change is in the exporter and the schema ([export.md](export.md), [data_contract.md](data_contract.md)), and a new capability flag goes in the three lists named above.

A build should keep these properties: every marker once per template, every relative link of a built page resolving inside the site, `data/*.js` holding the same objects as `data/*.json`, the same profile in every copy, and identical output for identical inputs and a fixed `created`. Run `pytest tests` after a change.

## The guide page

`viewer/pages/guide.html` is generated by `scripts/build_guide.py` from `viewer/guide/content.html` and `viewer/guide/guide.template.html`: the content file is the page body, the template is the page shell with a `<!--CONTENT-->` marker. In the content, `{{CMD_BUILD}}` is replaced by the command shown to the reader (`--cmd`, default `binder-review import ... && binder-review build-bundle ...`), and a `{{FIG:<name>}}` placeholder becomes a figure when figure data and screenshots exist in the source folder, else an HTML comment. Other options: `--src DIR` (the folder with the content), `--page-out`, `--assets-out`, `--assets-rel`, and `--standalone` (no app navigation and no web-font link, for a page that lives outside a bundle). `scripts/guide_to_md.py` converts the content to Markdown (`--out FILE`; without it the output goes to `docs/GUIDE.draft.md`, a rougher copy that this repository does not need; the default is not `docs/GUIDE.md` so that the maintained file is never overwritten). The maintained Markdown version of the same text is `docs/GUIDE.md`: when you change `content.html`, change `docs/GUIDE.md` to match, and use `scripts/guide_to_md.py --out` to compare the two. After editing the content, run the build script and then rebuild the bundle: `render_site` copies the page and adds the nav link and the card on the overview.

## See also

[GUIDE.md](GUIDE.md) · [viewer_board.md](viewer_board.md) · [viewer_inspector.md](viewer_inspector.md) · [viewer_map.md](viewer_map.md) · [viewer_query.md](viewer_query.md) · [profile_spec.md](profile_spec.md) · [data_contract.md](data_contract.md) · [export.md](export.md) · [cli.md](cli.md) · [architecture.md](architecture.md) · [schema/README.md](schema/README.md)
