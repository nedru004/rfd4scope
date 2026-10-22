# Architecture

Binderscope (package `binder_review`, source in `src/binder_review/`) turns BindCraft run folders into a static review bundle. This page is the map for a developer or an advanced user: the data flow, the conventions every module follows, where intermediate files go, what each module does and how to add a reader. The details of each stage are in the module documents listed at the end.

## Data flow

```
run folders (read-only)          readers/bindcraft1.py   (BindCraft 2: readers/bindcraft2.py)
        |                                  -> model.RunData  (settings, designs, backbones, funnel, hashes, warnings)
        v
structure.py        parsing, solvent accessibility, contacts, secondary structure, superposition (no judgement)
        v
target_frame.py     numbering map construct -> reference, per-run target definition, other subunits, glycans, cut ends;
                    places every design in the reference frame and runs the context checks
        v
interface.py, liabilities.py     recomputed interface analysis per design, per-residue tracks, sequence liabilities
                    (disulfides.py pairs the binder's cysteines when the structure file records no bond)
        v
families.py, modes.py            families (one backbone each), design clusters, diversity and coverage;
                                 binding modes; near-miss records of rejected designs
        v
specificity.py      orthologs and paralogs (from refdata or files), conservation grades, identity at contacted residues
        v
paratope.py, termini.py          called by the exporter: what a scaffolded campaign's designs bind with;
                                 which end of each design can carry a tag or a fusion
        v
export.py           campaign / inspect / homolog / profile / manifest blobs, validated against docs/schema
        v
bundle.py           writes bundles/<name>/: pages from viewer/ (through viewer_build.render_site), structures,
                    data/*.json and data/*.js, vendored 3Dmol.js
```

`pipeline.py` runs these stages as cached functions and returns a `Project`; `export.py` turns a `Project` into blobs and is the only module that writes them; `bundle.py` writes the site. `refdata.py` is the offline-first cache of public reference records and feeds `target_frame.py` and `specificity.py`. `config.py` holds BindCraft's default filter thresholds and folder names; `model.py` holds the dataclasses that stages exchange and the shared analysis constants. `cli.py` is a thin shell over all of this (see [cli.md](cli.md)), and the `app/` package is a second shell: a local server and an import page that start the same `import` command as a child process and serve the bundle it writes (see [app.md](app.md) and the section on why the app is local, below).

Nothing downstream of a reader opens the CSVs again. A binder sequence or a design id never goes into a network request: `refdata.py` only sends accessions, PDB ids, gene symbols and taxon ids (see [reference_data.md](reference_data.md)).

## Conventions

**Identity.** A design id is a file stem without the `_model{k}` suffix (for example `binder_l68_s123456_mpnn3`). A backbone id drops `_mpnn{n}` and is the family key: a family is one BindCraft trajectory and the sequences designed on it. Ids are unique within a run only, so the global key is `(run_id, id)`. Runs are never pooled in a diversity estimate; each run has its own `RunSettings` and its own `TargetConstruct`.

**Numbering.** There are three numberings.
- Construct numbering is what BindCraft writes: the target chain numbered as in the file you gave BindCraft, the binder chain numbered from 1 (BindCraft 2 versions 1.0.0 and 1.0.1 number it from 0; the import analyses one-based copies of those structures, see [export.md](export.md)). A run can delete stretches of the construct, so numbers need not be contiguous: always key by residue number, never by position. The per-run target is `RunData.target` (numbers actually present, deleted ranges, a `consistent` flag).
- Reference numbering is the author numbering of the full-length reference structure.
- UniProt numbering is used when you give an accession (`--uniprot`, or `target.uniprot` in the profile). The reference numbering must then equal UniProt numbering, which is checked by sequence. Without an accession the reference numbering is the common numbering.
The target frame builds the map between construct and common numbering from sequence, not from file offsets. Construct numbers stay inside `RunData` and the per-design analysis; everything that enters a blob is in the common numbering (`TargetConstruct.to_uniprot` does the one conversion in the exporter, and the field names call that numbering "UniProt" even when no accession was given). See [target_frame.md](target_frame.md) and [data_contract.md](data_contract.md).

**No reference.** The reference structure is optional. Without one the construct is the frame (`TargetFrame.reference_given` false): designs are superposed on it, numbering stays the construct's, and every check that needs something outside the construct is absent in the data (`None`, never zero) and recorded as off for that reason (`profile.caps_off`, `no_reference`), which is a different statement from a reference that holds nothing for a check (`reference_lacks`). Whether the Context check can decide a verdict depends on what the reference is: advisory unless the file records an experimental method ([target_frame.md](target_frame.md)).

**Frames.** The design-native frame is the frame of the PDB file BindCraft wrote. The reference frame is the reference chain. Every design is placed in the reference frame by superposing its target on the reference chain by shared residue number, with a fit that is weighted around the binder by default because BindCraft's copy of the target differs a little from the reference ([target_frame.md](target_frame.md), Placement); the other subunits of the reference then come along rigidly. A transform is `(R, t)` with `x' = x @ R.T + t`. Everything a blob carries as coordinates is in the reference frame.

**Units and scales.** Metrics keep BindCraft's CSV scales (`model.METRIC_INFO`, `model.SCALE_NOTE`): pLDDT, pTM, i_pTM and similar are 0 to 1 (PDB B-factors are 0 to 100); pAE and i_pAE are PAE divided by 31; shares ending in `%` are 0 to 100; RMSD and distances are in angstrom; dG is in Rosetta energy units. A missing value is `None`. A metric that a run lacks switches the dependent check off (`RunData.caps()`); nothing is guessed.

**Which pose.** The `_model{k}` file is the AlphaFold2 model with the higher complex pLDDT. BindCraft writes `InterfaceResidues` from one fixed model, so that column describes the file only when `k` matches. Binderscope recomputes the interface from the file it places; `DesignRecord.pose_metric` gives model-specific CSV values.

**Filters.** The filter set is a property of the run: `config.filters_for_run(run)`. A run that recorded its own thresholds (BindCraft 2 writes them to `campaign_metadata.json`, which its reader leaves in `RunData.annotations["filters"]`) is judged by them, every rule with provenance `campaign`. A run that did not (BindCraft 1 does not copy its filter file into the output folder) is judged by `config.DEFAULT_FILTERS`, BindCraft 1's default thresholds as the tool reconstructs them, whose rules say how well each is supported (`counter-verified`, `bounded`, `memory`). `config.check_filters` evaluates a rule list and returns the failures with their numeric gaps. Near-miss rescue and the Query page use the run's set; a campaign's recorded failures (what BindCraft itself reported) stand, and the metrics only add what BindCraft did not name. A campaign threshold on a metric Binderscope does not carry is kept as unevaluated, never dropped, and an infinite threshold is no limit. The two claims, "the threshold your campaign used" and "our best reconstruction of BindCraft 1's default", are never mixed. They are screens, not verdicts. See [modes.md](modes.md) and [bindcraft2_reader.md](bindcraft2_reader.md).

**Contacts.** A contact on the pages is a heavy-atom pair within `model.CONTACT_CUTOFF` (4.5 angstrom). BindCraft's own interface rule (4.0 angstrom, hydrogens counted, `model.BINDCRAFT_INTERFACE_CUTOFF`) is used only to compare with its `InterfaceResidues` column. A hotspot counts as touched when a binder heavy atom is within `HOTSPOT_TOUCH_CUTOFF` of it. The near-miss rule is exactly one failed filter, missed by at most `NEAR_MISS_MAX_REL` (25 percent) of its threshold.

**Statements the pages may make.** Count families, never sequences. Every score is a screen. Derived figures carry a status of `observed`, `derived` or `unknown`; unknown is never drawn as zero.

**Determinism.** Every stage result is cached under the project folder keyed by the hashes of its inputs and of the code it ran. The same inputs give the same bundle id.

## The project folder

`import` writes one project folder (the `--out` folder), never next to the inputs:

```
work/my-target/
  runs/<run_id>/runrecord.json      the run as read (no sequences unless asked)
  target/                           frame.json, frame.npz, frame.key: numbering map, per-run targets, reference arrays
  poses/<run_id>/                   one placed pose record per design
  analysis/<run_id>/                per-design interface analysis, tracks, liabilities
  stages/                           families, modes, homolog (specificity) results
  onebased/<run_id>/                one-based copies of the structures of a BindCraft 2 campaign whose binder numbers start at 0 (only then)
  export/                           campaign.json inspect.json homolog.json profile.json manifest.json structures/*.pdb
  project.json                      what was imported, counts, timings, highlights
```

`build-bundle` reads `export/` only, so it needs no analysis and takes seconds. See [export.md](export.md).

## Module table

| Module | Role |
|---|---|
| `cli.py`, `__main__.py` | argparse shell: `app`, `import`, `chains`, `build-bundle`, `inspect` |
| `app/server.py` | the local app: an HTTP server on `127.0.0.1` with a small JSON API, the project list and import pages, and the review pages of each project ([app.md](app.md)) |
| `app/store.py`, `app/jobs.py` | projects as folders below the Binderscope folder; builds as child processes with progress read from their log |
| `app/dialogs.py`, `app/probe.py` | native file and folder dialogs on macOS; quick checks of a run folder or a structure file for the import page |
| `app/static/` | the app's own pages: home, import, the shared script, the **‹ Projects** bar that is added to review pages |
| `config.py` | BindCraft default filters (`DEFAULT_FILTERS`), the filter set of a run (`filters_for_run`, `FilterSet`), `check_filters`, folder and CSV names |
| `model.py` | `RunData`, `DesignRecord`, `BackboneRecord`, `RunSettings`, `TargetConstruct`, `FunnelCounts`; metric scales; shared cut-offs; the map from BindCraft stems to page metrics; `fail` and `nm` record helpers |
| `readers/__init__.py` | reader registry (`register_reader`, `available_readers`, `detect_reader`, `get_reader`) |
| `readers/bindcraft1.py` | BindCraft 1 reader |
| `readers/bindcraft2.py` | BindCraft 2 reader. Verified against a de novo (`binder`) campaign of v1.0.3; any other campaign is read on the same assumptions, keeps `experimental = True` and carries a warning that names the reason ([bindcraft2_reader.md](bindcraft2_reader.md)) |
| `structure.py` | parsing, SASA, contacts, secondary structure, hydrogen bonds, superposition ([structure_api.md](structure_api.md)) |
| `refdata.py` | offline-first cache of public reference records ([reference_data.md](reference_data.md)) |
| `target_frame.py` | numbering map, per-run targets, other subunits, glycans, cut ends, placement, context checks ([target_frame.md](target_frame.md)) |
| `specificity.py` | orthologs, paralogs, conservation, identity at contacted residues ([specificity.md](specificity.md)) |
| `interface.py`, `liabilities.py` | recomputed interface analysis and sequence liabilities ([interface.md](interface.md)) |
| `disulfides.py` | which cysteines of a binder are paired, read from SG-SG geometry when the file records no bond ([interface.md](interface.md), Disulfides) |
| `families.py` | families, TM-align clustering, diversity, coverage ([families.md](families.md)) |
| `modes.py` | binding modes, hotspot coverage, near-miss records ([modes.md](modes.md)) |
| `antibody.py` | CDR numbering and checks for nanobody and scFv profiles ([antibody.md](antibody.md)) |
| `paratope.py` | the paratope of a scaffolded campaign from its own edit spec: segments, alignment of every design, shares of the interface by region ([paratope.md](paratope.md)) |
| `termini.py` | which end of a binder can carry a tag or a fusion, for four tag sizes, with the evidence: format, what BindCraft was asked about the ends, exit direction, room, side of the binder ([termini.md](termini.md)) |
| `pipeline.py` | the whole analysis as cached functions; `run_pipeline` returns a `Project` |
| `export.py` | `build_blobs(project)`, `write_export`; validates against `docs/schema` |
| `bundle.py`, `viewer_build.py` | writes the static site: `render_site`, `render_page`, `render_template` |
| `viewer/` | page templates, shared scripts and styles, vendored 3Dmol.js ([viewer.md](viewer.md)) |

## Why a local app and not a hosted web app

Binderscope reaches people in two ways that share all of their code: the command line, and a small app on your computer that adds a window for choosing runs, watching a build and opening the review pages. It is a local app on purpose. The reasons, in the order they usually matter:

- **Privacy of unpublished designs.** The inputs are designs that are often unpublished: binder sequences, structures and scores, the target construct, the hotspots, and the choices that show a strategy. Many groups may not hand such material to a third party before a publication or a patent filing, however well it is protected on the way. A local app keeps every file on your machine. Its only network requests are public reference lookups by accession that you switch on, and they carry no design data ([reference_data.md](reference_data.md), [app.md](app.md)).
- **Large inputs.** A BindCraft run folder holds hundreds or thousands of structure files and several tables. Uploading that to a service is slow, duplicates the data, runs into size limits and would have to be repeated for every rebuild. The app reads the folders where they are.
- **The analysis needs scientific libraries and compute.** Placing designs on the reference, TM-align clustering, the interface analysis and the clustering of binding modes use compiled libraries (numpy, scipy, gemmi, TM-align) and several processor cores for tens of seconds to minutes. That fits a laptop. In a browser tab it would need a bundled interpreter and rebuilt libraries; as a service it would need servers, queues and isolation between users.
- **A static bundle stays portable and shareable.** The result is a folder of plain files that opens by double-click, needs no account and no server, can be zipped and sent to a colleague, archived next to the lab results and opened again years later. Nothing depends on a service staying online. The app does not change this: it writes the same bundle.
- **A hosted service would mean uploads, accounts and costs.** Running one means accounts and sign-in, rules for how long data are kept and how they are deleted, security duties for other people's unpublished work, storage and compute bills, and users who must trust the operator. None of that is needed to review designs.

How the app keeps to this: it listens on `127.0.0.1` only and refuses requests that do not come from its own pages (the Host, Origin and custom-header checks in [app.md](app.md)); builds run as separate processes, so a crash or a cancel cannot take the app down; a project is a folder you can see, move and delete; the review pages are the unchanged static pages.

**What a later viewer-only web page could do without uploads.** The review pages need only the files of a bundle. A page hosted on the web could ask you to choose a bundle folder (or a ZIP of one) on your disk and open it entirely in your browser: the files would be read locally and never sent anywhere. The same page could carry the guide and a download link for the installer. What it could not do without a server is run the analysis, so building a bundle would still happen on your computer. This is a possibility, not a feature of this repository.

## Reader contract

A reader module defines `NAME`, `detect(path) -> bool` and `read_run(path, run_id, label=None, settings_path=None, **options) -> RunData`, and is registered in `readers/__init__.py` (append to `_REGISTRY` or call `register_reader`). A module that fails to import is skipped, so a broken optional reader never blocks the others. `RunData.designs` holds every sequence of the run, accepted or not; use the helpers `accepted()`, `rejected()`, `rejected_with_structure()` and `accepted_backbones()`. Rejected designs of a run without a `rejected` folder are metric-only (`structure_path` is `None`). A reader reports oddities as `RunData.warnings` (the user should look) and `RunData.notes` (only worth knowing), never as exceptions, because people prune run folders by hand.

## Where each stage's API is documented

| Stage | Entry points | Document |
|---|---|---|
| Target frame | `build_frame`, `TargetFrame.save` and `load`, `place_design`, `frame.context`, `target_block`, `run_blocks`, `inspector_block` | [target_frame.md](target_frame.md) |
| Interface and liabilities | `load_for_analysis`, `analyze_design`, `analyze_run`; `find_liabilities`, `sequence_properties` | [interface.md](interface.md) |
| Families | `analyze_families`, `build_families`, `fold_matrix`, `diversity`, `diversity_all`, `epitope_diversity`, `diversity_paratope` | [families.md](families.md) |
| Paratope | `analyse_run`, `parse_edits`, `build_layout`, `place_design` | [paratope.md](paratope.md) |
| Termini | `format_of`, `designed_for`, `assess`, `family_pick` | [termini.md](termini.md) |
| Modes | `build_modes`, `build_from_runs`, `rescue_records`, `rescue_summary` | [modes.md](modes.md) |
| Specificity | the panel builders in `specificity.py` | [specificity.md](specificity.md) |
| Pipeline, export, bundle | `run_pipeline`, `build_blobs`, `write_export`, `write_bundle`, `render_site` | [export.md](export.md) |
| Structure primitives | `structure.py` | [structure_api.md](structure_api.md) |

## See also

[app.md](app.md) · [cli.md](cli.md) · [data_contract.md](data_contract.md) · [profile_spec.md](profile_spec.md) · [viewer.md](viewer.md) · [GUIDE.md](GUIDE.md)
