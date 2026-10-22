# Pipeline, export and bundle

How `binder_review.pipeline`, `export`, `bundle` and `viewer_build` turn BindCraft run folders into the validated blobs of [data_contract.md](data_contract.md) and into the static bundle that opens from disk. This page is for developers and advanced users. The commands and their options are in [cli.md](cli.md); every field of every blob is in [data_contract.md](data_contract.md) and in the schemas under [schema/](schema/README.md).

```
run folders --read_runs--> RunData --build_target_frame--> TargetFrame (reference chain, other subunits, rest, glycans, membrane)
     |                                    |
     |             compute_poses ---------+   placement, context checks, thumbnail view, CA trace (per design)
     |             compute_interface -----+   contacts, hydrogen bonds, buried polars, tracks, liabilities
     |             compute_families ------+   families, TM-align matrix, per-run diversity, replay, fold labels of rejected designs
     |             compute_modes ---------+   binding modes, map embedding, near-miss records
     |             compute_specificity ---+   orthologs and paralogs from the reference data
     v
   Project --export.build_blobs--> campaign, inspect, homolog, profile, manifest (+ structures), validated against docs/schema
     |                                             |
     +-------------- write_export --------------> work/my-target/export/
                                                      |
                                       bundle.write_bundle --> bundles/my-target/  (pages, data/*.json and *.js, structures, assets)
```

The `import` command runs `run_pipeline`, `build_blobs` and `write_export`, writes `project.json` and, when it is given a bundle folder, runs `write_bundle`. The `build-bundle` command runs `write_bundle` on an existing `export/` folder. The Binderscope app ([app.md](app.md)) starts these two commands for you: the project folder of a project in the app is `~/Binderscope/<project>/work` and its bundle is `~/Binderscope/<project>/bundle`.

## 1. The project folder

`import` writes everything under one project folder. Every cache file is keyed, so a rerun reads back what is still valid and recomputes the rest.

```
work/my-target/
  project.json              what was imported, counts, timings, the manifest's highlights (written by the CLI; read by `inspect`)
  runs/<run>/runrecord.json the run as read (RunData.to_dict(): audit only, no binder sequences)
  target/frame.json         the target frame: its metadata here and its arrays in frame.npz; frame.key holds the cache key
  poses/<run>/<id>.json     one pose record per exported design
  analysis/<run>/<id>.json  the interface analysis of one design (interface.analyze_run's own cache)
  stages/families.json      families, fold matrix, diversity
  stages/modes.json         binding modes, map embedding, near-miss records
  stages/homolog.json       the homolog blob
  onebased/<run>/           one-based copies of the structure files of a zero-based BindCraft 2 campaign (only for such a run)
  export/                   campaign.json inspect.json homolog.json profile.json manifest.json structures/<id>.pdb
```

`<id>` in `poses/` and `analysis/` is the run-local design id. `onebased/` is explained in section 2; it is not a stage cache and `drop_caches` leaves it alone. A corrupt or mismatching cache file is a miss, never an error. A rebuild without the cache (`use_cache=False`, or `--no-cache` in [cli.md](cli.md)) first deletes the `*.json` and `*.tmp` files under `analysis/`, `poses/` and `stages/` and `target/frame.key` (`pipeline.drop_caches`), never whole folders, and rewrites them.

Input folders are read-only: nothing is ever written into a run folder.

## 2. Stages and the cache

| Stage | Function | Result | Cache |
|---|---|---|---|
| read the runs | `read_runs`, `one_based_structures` | `RunData` per run: CSV rows, settings, funnel counts, per-run target construct, structure hashes; for a zero-based BindCraft 2 run the designs point at one-based copies of their structures | none (read on every build; the readers hash every input file); the copies in `onebased/` are reused by name |
| target frame | `build_target_frame` | `TargetFrame`: numbering check by sequence, per-run constructs, other subunits, glycans, membrane plane, solvent accessibilities | `target/` |
| poses and context checks | `compute_poses` | per design: placement on the reference chain (by default a fit weighted around the binder, `--placement`) and how well the target fits there, context metrics, glycan distances, contact set, thumbnail view, CA trace | `poses/<run>/<id>.json` |
| interface analysis and liabilities | `compute_interface` | `DesignAnalysis` per design: contacts, hydrogen bonds, buried polars, patches, tracks, liabilities | `analysis/<run>/<id>.json` |
| families, fold matrix, diversity | `compute_families` | `families.analyze_families`, serialised | `stages/families.json` |
| modes, embedding, near-miss records | `compute_modes` | `modes.build_modes` and `modes.rescue_records` | `stages/modes.json` |
| specificity panel | `compute_specificity` | the homolog blob | `stages/homolog.json` |
| export, validation, write | `build_blobs`, `write_export` | blobs and structure files | `export/` |
| bundle | `write_bundle` | the static site | the bundle folder |

Liabilities are computed inside the interface analysis (`DesignAnalysis.liab`, `seq_props`); there is no separate liabilities stage.

**Zero-based BindCraft 2 campaigns.** BindCraft 2 versions 1.0.0 and 1.0.1 write the residue numbers of the binder chains from 0, while the interface analysis, the exporter and the inspector expect 1 to n. The BindCraft 2 reader records which numbering a campaign uses ([bindcraft2_reader.md](bindcraft2_reader.md)). In the same stage, `pipeline.one_based_structures` rewrites every design structure of a zero-based run as a one-based mmCIF copy under `work/onebased/<run>/` (the binder chains are shifted, the target chains keep their numbers) and points the design at the copy. A copy is named by the first 12 characters of the original's sha256, a six-character tag of the shift and of the code that writes it, and the original's file name, so it is reused only while all of them are unchanged. The original files are inputs and are never touched: `design.prov.model_file` names the original, the run gets a note that says so and the log prints `<run>: binder numbering is zero-based, N structures renumbered to one-based copies`. Runs of any other reader, and one-based campaigns, are left as they are.

**Cache keys.** Each stage stores `{key, data}` and reads it back only when the key matches. A key is a hash of the inputs of the stage and of the code it ran (`pipeline.code_hash` hashes the source of the modules), so a code change can never serve a stale result and an edited input invalidates exactly what depends on it:

| Cache | The key covers |
|---|---|
| target frame | the hashes of the reference structure and of the construct file, the accession, the reference id, chain and partner chains, the placement option, each run's hotspots, residue numbers, label, target sequence and consistency flag, the code of `target_frame` and `structure`, the reference-data hashes |
| pose record | the hash of the structure file, the frame key (so the placement option too), the code of the frame, structure, `pose_one` and `canonical_view`, the pose version |
| interface analysis | the hash of the structure file, the run's hotspots, which AlphaFold2 model the file holds, BindCraft's columns for the design, the analysis settings, the code of `interface`, `liabilities`, `structure` and `model` |
| families | the input digests (tool version, reference and construct hashes, accession, every run's file list and definition: hotspots, residue numbers, label, length range, reader), the run offsets, the code of `families` and `structure` |
| modes | the input digests, the contact set of every pose, the frame signature (offset, hotspots per run, sites), the code of `modes`, `config` and `model` |
| homolog | the residues, `profile.specificity`, the accession and reference structure, whether the ConSurf overlay is on, the reference-data hashes, the code of `specificity` |

The first build is dominated by the interface and family stages. Later builds reuse the cache and take far less; `build-bundle` reads only `export/` and needs no analysis at all.

The pose, interface and TM-align stages run in spawned worker processes (`jobs`). A script that calls the pipeline needs the usual `if __name__ == "__main__":` guard; the CLI has it. Progress lines carry the elapsed seconds (`pipeline.Log`; `quiet=True` keeps none).

The stages run with the reference-data layer offline: a record that is not in the cache becomes a warning or an error, never a request, unless the project allows fetching (`ProjectSpec.online`, `--online`). See [reference_data.md](reference_data.md).

## 3. Numbering and frames: one conversion point

| Where | Numbering |
|---|---|
| `RunData`, `DesignAnalysis` (interface, liabilities) | construct numbers: the numbering of the file given to BindCraft, with gaps where a run deleted stretches; binder residues are 1-based (a zero-based BindCraft 2 campaign is read through one-based copies of its structures, section 2) |
| pose records (`pipeline.pose_one`) | reference numbers in the reference frame; the placement transform is stored as `R` (9 numbers, row-major) and `t` (3) with `x' = x @ R.T + t`, next to the fit it comes from and how well the target fits (`fit`, `iface_rmsd`, `iface_n`, `global_rmsd`, `fit_note`) |
| every blob | reference numbers; construct numbers survive in `target.orig_resi`, `target.numbering.input_offset`, `runs[].target.input_offset` and `runs[].settings.hotspots_input` |
| campaign `design.bc` | a 0-based index into `seq`; everywhere else binder residues are 1-based |

The exporter converts the construct-numbered interface values by adding the run's offset (`TargetConstruct.uniprot_offset`, found by sequence in the target frame and never assumed): `tc`, `contacts[].t`, `hbonds[].t`, `salt[].t`, the target side of `buns`, and the arrays over the target (`plddt_t`, `t_dsasa`), which `frame.union_array` fills with 0 or null for residues a run lacks. Nothing is ever indexed by position, so a run that deleted a stretch is handled by number.

Two independent computations of the contact set exist: the interface module on the hydrogen-bearing structure, and the target frame on the heavy-atom tables. The exporter compares them for every design, and the hotspots touched, and lists a disagreement in `manifest.validation.mismatches`. Section 3 of [data_contract.md](data_contract.md) has the conventions; [target_frame.md](target_frame.md) the placement.

## 4. Which designs are exported

| Set | Campaign `designs[]` | Inspector | Structure file |
|---|---|---|---|
| accepted designs | yes | yes | yes |
| rejected, with a structure, and a near miss (exactly one failed filter, missed by at most 25 percent of its threshold) | yes, `inspect` set | yes | yes |
| rejected, with a structure, other | yes, `inspect: null` (CA trace only) | no | no |
| rejected, metric-only rows (the run kept no structure) | no: counted in `runs[].n_designs` and the funnel | no | no |

The pages need a pose for every listed design, which is why metric-only rows are not listed. A design counts as a near miss when `modes.rescue_records` finds exactly one failed stem, missed by at most `model.NEAR_MISS_MAX_REL` of its threshold (`near` in `design.nm`); [modes.md](modes.md) has the rule. The inspector `order` lists accepted designs by run and rank, then near misses by score.

## 5. What each blob carries

The field lists are in [data_contract.md](data_contract.md). What the exporter does to build them:

- **Campaign.** `target` is `frame.target_block(site_residues=...)`: site A1 is built from the hotspots or, when no run records one, from the residues that most accepted designs contact (`pipeline.site_residues`; [target_frame.md](target_frame.md)). `designs[]` has one record per exported design. `runs[]` has one entry per run: the settings with the source of each value (`settings_json`, `csv`, `inferred`), the construct with `removed` and `cut_points`, the filter rules, the funnel with its `observed`, `derived` and `unknown` stages, the non-zero failure counters and the epoch. `diversity`, `fold` and `modes` come from the families and modes modules unchanged, and `run` is the project summary. The overview's `highlights` are section 8.
- **Scaffolded runs.** For a run built on a fixed scaffold `export.paratope_items` adds `runs[].paratope`, `runs[].paratope_rejected` and `runs[].diversity_paratope`, `designs[].para` carries where each design's interface residues fall, the overview gains a highlight on the paratope, the families highlight says its coverage is on the binding site alone, and the manifest's `annotations.scaffold` records the edit spec and the retained-fraction threshold (never the cluster path of the scaffold file). A run that is not scaffolded has none of these. The shapes and the reasons the analysis can be off are in [paratope.md](paratope.md); the epitope block in `diversity.runs.<id>.epitope` is written for every run ([families.md](families.md)).
- **Terminus blocks.** `design.term` (also `inspect.designs[].term`) says which end of the binder can carry a tag or a fusion, for four tag sizes, with the evidence ([termini.md](termini.md)); `runs[].term` (also `inspect.runs[].term`) holds the run's format, what BindCraft was asked to do about the ends, the tag sizes and the constants. The exporter loads each judged design's structure, asks `termini.assess` with an environment function built on `TargetFrame.context` (the placed pose decides what lies outside the construct; without a reference the function is absent and the block says `env` false), and rotates the exit directions into the frame of `pdb_b`. Accepted designs and near-miss rescues carry a block, the other rejected designs none, and a format with no free ends carries one that says so. The overview gains one highlight per run with the family counts.
- **Metrics.** `design.m` always carries the 38 core keys (null when the data has no value) plus the context keys and the placement numbers `place_rmsd`, `place_iface_rmsd`, `place_iface_n` and `place_global_rmsd`. Values BindCraft reports are copied from its `Average_<stem>` columns; the rest are recomputed from the structure. `model.PAGE_METRIC_COPIED` and `PAGE_METRIC_RECOMPUTED` are the code's map from BindCraft stems to page metrics; stems without a page metric (`Binder_pLDDT`, `Binder_pTM`, `Binder_pAE`, `PackStat`, `MPNN_score`, and so on) stay in `design.src` only. `ipae_mean` is `ipae_norm * 31`. Rounding follows section 3.3 of the contract. A run without hotspots has no intended epitope, so `epi_prec` and `epi_rec` are `null` for its designs.
- **Provenance.** `design.src` holds every BindCraft number as written (`average`, `models`, `interface_aas`); `design.prov` holds the run, binder name, backbone, seed, MPNN index, model file (relative to the run folder), rank, CSV, design time, BindCraft's notes, the spreadsheet highlight flag and how the design was placed on the reference chain (`fit`, and `fit_note` when it was not placed by the fit that was asked for).
- **Placement.** Every design is placed on the reference chain by `place_design` with the fit of `--placement` (default `interface`, a fit weighted around the binder; [target_frame.md](target_frame.md)). The exporter records, per design, `m.place_rmsd` (CA RMSD of the design's target over all shared residues under the transform used), `m.place_iface_rmsd` and `m.place_iface_n` (over the residues within about 13 Å of the binder, the ones the context checks depend on) and `m.place_global_rmsd` (what the whole-target fit gives), and `prov.fit` (`interface`, `all` or `core`) with `prov.fit_note` when the fit is not the one asked for. A design with fewer than 12 target residues near the binder falls back to the whole-target fit under `interface`; the run then gets a note ("n of m designs were placed by the whole-target fit ..."). The build warns once per run that has designs with a CA RMSD near the binder above `place_warn_rmsd` (1.5 Å): "placement RMSD near the binder above 1.5 Å for n of m accepted designs: their own target differs more from the crystal chain at the interface, so distances to the other subunit and the rest of the protein are less certain (the rigid core of every design fits within c Å)". `manifest.thresholds.placement` holds the mode, the fit and the weights (`iface_full`, `iface_decay`, `iface_floor`, `iface_cutoff`, `iface_min_n`, `near_weight`, `report_weight`, `warn_rmsd`), `project.json` the option as `spec.placement`, and each structure file a `REMARK 250 placement:` line. Contacts, hydrogen bonds, hotspots touched and the other interface numbers are computed in the design's own frame and do not depend on the fit; the distances to the other subunit, the rest of the protein and the sugars, the membrane excess and the Context verdicts do.
- **Rejected designs.** `design.fail[]` and `nm` come from `modes.rescue_records`, which judges each run by its own filter set (`config.filters_for_run`). A run that recorded its thresholds (BindCraft 2) is judged by them, BindCraft's own `failed_filters` verdict first (see [modes.md](modes.md)); a run that did not (BindCraft 1) is judged by the reconstructed default set applied to the CSV values (`config.check_filters`). BindCraft 1 does not copy its filter file into the run folder, so such a run that used other filters can show `fail` entries that differ from BindCraft's own decisions. `runs[].filters` and the manifest's `thresholds.filters` say which set it was: a rule's `provenance` is `campaign` for a recorded threshold and `counter-verified`, `bounded` or `memory` for a reconstructed one, and a run judged by recorded thresholds has `runs[].filter_set` and a note on the run. `thresholds.filters` is the default list when every run is judged by the defaults, else every run's rules with the run id in `run`.
- **Inspector.** `target_pdb`, `target_seq`, `runs[]`, `olig`, `rest` and the glycan blocks are `frame.inspector_block()`; the exporter adds `olig.buried_all`, the whole interface of the reference chain with the other subunits, next to `olig.buried`, which keeps only the residues inside the union construct (the frame strip draws the whole interface). `target_pdb` is always chain A, whatever the chain of the reference structure is called (the pages take chain B for the binder). The design's own copy of the target, as BindCraft modelled it, travels in `structures/<id>.pdb` (chain A, placed by the same transform as the binder), and the inspector draws it from there. Each inspected design carries `pdb_b`, the per-residue arrays, contacts, hydrogen bonds, salt bridges, buried polars, patches, `sibs` (every sequence of the backbone that has a CSV row), `liab` with long keys, `ctx` and `octx`, a single identity `poses` entry and `center`.
- **Homolog.** `specificity.build_panel` over the union construct with the species and paralogs of `profile.specificity`. A panel is built when the target has a UniProt accession and the profile names species or paralogs; otherwise the blob is empty and the pages hide the specificity features. The ConSurf-DB overlay is left out unless requested.
- **Profile.** The project's profile completed from the data (`export.build_profile`): `caps` written for all 38 flags from what the designs carry, `caps_off` saying why a context flag is off (`no_reference`, `reference_lacks` or `profile`; [profile_spec.md](profile_spec.md), section 4), `checks.context.hard` resolved from the reference's own file (advisory unless it is an experimental structure, or you say otherwise; section 5), `target.construct` first and last residue, `length`, `reference`, `numbering.input_offset`, `label` and `span_label` from the frame. See section 7 of the contract.
- **Manifest.** Tool and library versions, the command, every input file with its sha256 (structure folders summarised: one entry per run and role with a `count` and a hash of the file hashes), the reference records used, per-run settings (`settings_raw` is the settings file unchanged), filters, counts, warnings, the thresholds, `validation` (section 6), licences, `privacy`, `highlights` and, after bundling, `bundle.files`, `pages`, `data` and `structures`. `bundle.id` is the first 16 hex characters of the sha256 of the sorted source hashes and the profile; review files refer to it. The manifest never carries a binder sequence. Absolute paths are cut to their last component (`export.portable_paths`), so a bundle can be handed on; the settings file of a BindCraft run records the directories of the machine that ran it, and the original stays in the run folder.

## 6. Validation at build time

`export.validate_blobs` runs `scripts/validate_schema.py` on the campaign, inspect, homolog and manifest blobs: JSON Schema (draft 2020-12), the semantic rules (array lengths, numbering, run references, capability flags against the data, per-run hotspots and removed regions, diversity consistency, fold matrix size and symmetry, `pdb_b` against `seq` and `plddt_res`, the cross rules between campaign, inspector and homolog; section 10 of the contract lists them) and `manifest.caps == profile.caps`. Any error raises `ExportError` and the build stops; warnings are kept in `Blobs.warnings`. `bundle.write_bundle` runs `check_bundle` again on the finished folder (file hashes, links, review files). The validator needs the `jsonschema` package, which `pyproject.toml` lists among the dependencies, so a normal install has it.

`manifest.validation` records what was checked against the sources:

- `counts_match_csv` and `runs[].counts.csv`: per run, each count read independently from the files and compared with what was exported and with the funnel: rows of `final_design_stats.csv` against the exported accepted designs, the number of files in `accepted/` and `rejected/` against the exported accepted and rejected designs, rows of `mpnn_design_stats.csv` and `trajectory_stats.csv` against the funnel, and the distinct backbones of `final_design_stats.csv` against the families. Each entry is `{csv, exported, match}`. Only runs in the BindCraft 1 layout can be compared: a BindCraft 2 run has an empty `runs[].counts.csv` and counts as neither a match nor a mismatch. `counts_match_csv` is `true` or `false` when at least one run could be compared and `null` when none could. The `import` and `inspect` summaries then print `counts against the CSVs: not checked`, and the Overview's Build panel leaves out its Counts check row.
- `mismatches`: every difference found, never hidden, including a contact or hotspot set that differs between the interface module and the target frame.
- `metric_agreement`: every recomputed quantity against BindCraft's own column for the accepted designs, in the page scale (percent columns divided by 100) and against the column of the AlphaFold2 model whose pose is in the file. Per metric: `stem`, `n`, `mean_abs_diff`, `max_abs_diff`, `mean_diff`, `r`, `rho`, `mean_bindcraft`, `same_definition`, `scale`. `InterfaceResidues` gets `n`, `exact` and `mean_jaccard`, because BindCraft writes that column from one fixed model whatever file is saved. How close the recomputed numbers are depends on the run, so read the block for your own data; [interface.md](interface.md) explains each definition.

## 7. The bundle

```
bundles/my-target/
  index.html board.html inspector.html map.html query.html [guide.html]
  assets/css/shared.css
  assets/js/{bs,common,family,mini3d}.js
  assets/3Dmol-min.js  assets/3Dmol-LICENSE.txt
  assets/binderscope-mark.svg  assets/favicon.svg  [assets/guide/*]
  data/{campaign,inspect,homolog,profile,manifest}.json    canonical copies for tools
  data/{campaign,inspect,homolog,profile,manifest}.js      the same objects as same-folder scripts
  structures/<design id>.pdb   structures/<design id>.js   one pair per inspected design
  reviews/README.txt           where saved review files go
  README.txt
```

| Path | What it is for |
|---|---|
| `index.html` and the four pages | the overview, board, inspector, map and query pages, rendered from `viewer/pages` |
| `assets/` | the shared stylesheet and scripts copied from `viewer/` (every `*.js` of `viewer/js` is copied), the vendored 3Dmol.js with its licence, the mark and the favicon |
| `data/*.json` | the canonical blobs, for tools |
| `data/*.js` | the same objects as scripts for pages opened from `file://`: each sets `window.BINDERSCOPE.<blob>` and the page global (`RAW`, `INS`, `HOM`, `PROFILE`, `MANIFEST`). By default `inspect.js` leaves out the binder structures (`pdb_b`) of every design |
| `structures/<id>.pdb` | the design's own target (chain A, reference numbering, the residues of its run) and the binder (chain B, residues 1 to n, B-factor equal to pLDDT), heavy atoms, in the reference frame, with `REMARK 250` lines naming the design, run, AlphaFold2 model, frame and how the design was placed (`REMARK 250 placement:` gives the fit and the CA RMSD near the interface and over the whole target; chain A is the design's own copy of the target, not the reference chain) |
| `structures/<id>.js` | the same text as a lazy-loadable script, `window.BINDERSCOPE.structures[<id>]`; the inspector loads it when a design opens and shows a loading state until it arrives. It is also where the inspector gets each design's own copy of the target (chain A): in a bundle built with `structure_scripts=False` or `embed_structures=True` the blob already holds the binder (`pdb_b`), the script is never loaded and the inspector draws the reference chain as the target |
| `reviews/` | where the user keeps saved review files. A page cannot write there; the board's **Save review file** downloads a file to move here |
| `README.txt` | how to open the bundle |
| `guide.html`, `assets/guide/` | `guide.html` is written when `viewer/pages/guide.html` exists, and `assets/guide/` is copied when `viewer/assets/guide` exists too (the overview then shows a Guide card and the navigation a Guide link). A guide that points at a missing file is skipped with a log line and the build goes on |

A run-prefixed id (`run01/x`) is written with `__` in file names. The page set is every page whose blobs are present; the overview is always written. Which blobs each page loads is in section 3.7 of the contract (`viewer_build.NEEDS` in code).

The manifest is written last. After the pages, data and structures exist, `write_bundle` records `bundle.name`, `bundle.pages`, `bundle.data`, `bundle.structures` and `bundle.files` in it and rewrites `data/manifest.json` and `data/manifest.js`; `bundle.id` stays what the exporter computed.

Nothing is fetched and nothing is loaded from a CDN: the pages and their data load as plain scripts from the bundle folder, and a bundle has no network reference by default (system fonts). `fonts="google"` adds the optional Google Fonts link in `<head>` with a system-font fallback, and an opened page then contacts fonts.googleapis.com. `csp=True` adds a strict Content-Security-Policy meta tag to every page (`connect-src 'none'`).

`manifest.bundle.files` lists every file of the bundle with its size and sha256, except the two manifest files, which cannot hash themselves. `write_bundle` refuses to clean an output folder that is not empty and not an earlier bundle (it has `index.html` and `data/manifest.json`), so a mistyped path cannot delete anything else.

The pages come from `viewer/pages`. `viewer_build.render_site` replaces four placeholders in each template (`<!--@head-->`, `<!--@brand-->`, `<!--@badge-->`, `<!--@nav-->`), copies the shared files, writes the data wrappers, and checks that every relative link of the written pages resolves (`check_links`). `viewer_build.viewer_dir()` finds the viewer sources next to `src/`, inside the package, or in the folder named by `BINDERSCOPE_VIEWER`. `python -m binder_review.viewer_build DATA_DIR OUT_DIR` renders a site from a folder of blobs. The template contract is in [viewer.md](viewer.md).

## 8. What stands out

`export.compute_highlights` writes up to eight sentences into `campaign.highlights` and `manifest.highlights` for the overview page. Each is `{id, text, severity, link}`. `severity` is `info`, `warn` or `bad`; `link` is optional, `{page, design, run, filter}`: the overview turns `design` into `inspector.html#d=<id>`, otherwise it opens the page, and `filter` is the tooltip. Every number is counted from the exported data; a statement is left out when its data is absent.

| id | Statement | Severity |
|---|---|---|
| `rest_overlap` | accepted designs that fail the rest-of-protein check, per run: `m.rest_n3`, the binder atoms within 3 Å of residues the run's target lacks, is at or above `checks.context.rest.fail_n3` (ten atoms unless the profile says otherwise); and how many designs have an atom closer than 2.2 Å to those residues | `bad` when more than a quarter fail, `warn` for any, else `info` |
| `run_notes` | only with `rest_overlap`, and only when a free-text note file (`.rtf`, `.txt` or `.md`) in a run folder names two or more of its backbones: how many of them the context checks fail or warn on | `info` |
| `partner_overlap` | designs that put binder atoms within 3 Å of the other subunits (`checks.context.partner.fail_n3` or more atoms fail the check), or the closest approach and the median distance when none does | `bad` when a design fails the check, `warn` when any design is within 3 Å, else `info` |
| `hotspots` | per run and hotspot, how many accepted designs touch it, how many touch all, how many touch none; for a run without hotspots, "No hotspots recorded for <run>: its designs have no hotspots touched and no intended epitope", and, when no run has hotspots, that the binding-site view (site A1) is built from the residues the designs contact most | `warn` when a run has a hotspot touched by under a quarter of its designs, else `info` |
| `run_differences` | what differs between the runs (hotspots, with "none recorded" for a run that has none; construct size and removed stretches; length range) and that estimates are therefore per run | `info` |
| `families` | families per run with the Chao-Jost coverage and level at the default basis, a low-confidence note below 30 families, a note when attempted trajectories are unknown | `warn` when a level is `keep`, else `info` |
| `near_misses` | how many rejected designs with a structure are near-miss rescues, by failed filter | `info` |
| `specificity` | which orthologs keep every hotspot residue, which hotspot residues vary and in whom, and how well the focus paralog matches | `warn` when a headline species differs at a hotspot residue, else `info` |

## 9. Python API

```python
from pathlib import Path
from binder_review import pipeline as P, export as E, bundle as B

if __name__ == "__main__":                       # the worker pools are spawned: keep the guard
    spec = P.ProjectSpec(name="my-target",
                         runs=[P.RunSpec(Path("run01"), "run01"), P.RunSpec(Path("run02"), "run02")],
                         target_pdb=Path("construct.pdb"), reference_cif=Path("full_length.cif"))
    project = P.run_pipeline(spec, "work/my-target")      # cached stages -> Project
    blobs = E.build_blobs(project)                        # validated: campaign, inspect, homolog, profile, manifest, structures
    E.write_export(blobs, "work/my-target/export")
    B.write_bundle("work/my-target/export", "bundles/my-target")   # returns bundles/my-target/index.html
```

| Name | What it does |
|---|---|
| `pipeline.RunSpec(path, run_id, label=None, settings=None)` | one run folder, its short id, an optional label and an optional settings file |
| `pipeline.ProjectSpec(name, runs, target_pdb, reference_cif=None, ...)` | everything `run_pipeline` needs. `reference_cif` is optional: `None` builds the frame from the construct and switches off every check that needs the rest of the protein ([cli.md](cli.md), Without a reference structure). The optional fields include `context_hard` (`auto`, `hard`, `advisory`) next to are the accession, the profile (a profile JSON path, a dict or the id of the built-in default), the reference id, chain and partner chains, `consurf`, `online`, `jobs`, `use_cache` and `command` (recorded in the manifest as `tool.command`); see the dataclass for their defaults |
| `pipeline.run_pipeline(spec, work, *, log=None) -> Project` | runs every stage with the cache under `work` |
| `pipeline.Project` | the in-memory analysis: `spec`, `work`, `runs`, `frame`, `profile`, `poses`, `analyses`, `families`, `modes`, `rescue`, `homolog`, `project_ids`, `digests`, `timings`, `warnings`, `network_requests`; `designs(run_id=None, status=None, with_structure=True)` lists the exported designs and `summary()` the counts |
| `pipeline.read_runs`, `one_based_structures`, `build_target_frame`, `compute_poses`, `compute_interface`, `compute_families`, `compute_modes`, `compute_specificity` | the stages, each usable on its own (`one_based_structures(runs, work)` belongs to the read stage and returns how many structures it copied); `pipeline.drop_caches(work)` removes the cache files |
| `pipeline.site_residues(runs, poses, share=0.5)` | the residues the designs contact most (those that at least `share` times as many accepted designs contact as the most contacted residue, or all designs with a pose when none was accepted), which the target block uses for site A1 when no run records a hotspot |
| `pipeline.pose_one(frame, complex, run_id, design_id, status, rank, model_k)` | the pure function behind a pose record |
| `pipeline.canonical_view(target_xyz, binder_xyz)` | the thumbnail view: rows are the view axes in the reference frame, plus the interface centre |
| `export.build_blobs(project, *, created=None, validate=True, strict=True) -> Blobs` | assembles and validates the blobs. `created` fixes the timestamp for a reproducible manifest; `strict=False` returns the blobs even when validation finds errors |
| `export.Blobs` | `campaign`, `inspect`, `homolog`, `profile`, `manifest` (dicts), `structures` (file name to PDB text), `warnings`, `validation` |
| `export.write_export(blobs, out_dir) -> list[Path]` | writes the five JSON files and `structures/<id>.pdb` |
| `export.check_counts`, `metric_agreement`, `compute_highlights`, `build_profile`, `portable_paths` | the building blocks of the sections above, public and usable on their own (`check_counts` compares only BindCraft 1 runs) |
| `export.ExportError`, `pipeline.PipelineError`, `bundle.BundleError`, `viewer_build.BuildError` | what each layer raises |
| `bundle.write_bundle(export_dir, out_dir, *, name=None, fonts="system", csp=False, guide=None, clean=True, validate=True, structure_scripts=True, embed_structures=False, log=None) -> Path` | writes the bundle from an export folder and returns `index.html`. `structure_scripts=False` writes `structures/<id>.pdb` only (the binder structures then stay in `inspect.js`); `embed_structures=True` keeps them in `inspect.js` as well |
| `viewer_build.render_site(...)`, `render_page(...)`, `render_template(...)` | the page assembly: a whole site, one page as a string, and the Python twin of the profile text templates |

## 10. Known gaps and limits

- **Attempted trajectories are unknown.** The readers do not read the `Trajectory/` folders, so `n_traj` is null, the funnel stage has status `unknown`, yield and forecasts count passed trajectories, and `caps.traj_total` is off. Any estimate of attempts is derived and never a denominator.
- **A run without a settings file.** The hotspots come from the `Target_Hotspot` column, and the lengths and the final-design target are inferred; `settings.sources` marks them `csv` or `inferred` (`unknown` when there is none).
- **A run without hotspots.** BindCraft can be run without any (an empty `Target_Hotspot` column and no hotspots in a settings file, or a BindCraft 2 campaign without its metadata file), and such a run imports and builds. It has no intended epitope: `settings.hotspots` is empty with the source `unknown`, its designs have `hot` empty and `hot_of` 0, `epi_prec` and `epi_rec` are `null`, the epitope check has nothing to test and passes, and no design is off-site. The run gets the note "No hotspots recorded for this run ...", and the highlights say "No hotspots recorded for <run>" and, in the comparison of runs, "none recorded". When no run has a hotspot, site A1 comes from the residues most accepted designs contact (`sites.A1.source` says so). Counts that divide by the hotspots, such as "0 of 0 hotspots touched", are not meaningful for such a run.
- **Rejected designs without structures** (a run with no `rejected/` folder) are counted and never listed.
- **No PAE, ipSAE or independent pose.** BindCraft output has none, so those fields stay null or absent and the checks and meters that need them are off. `Hotspot_RMSD`, `Surface_Hydrophobicity`, `dG` and `ShapeComplementarity` are BindCraft's own values; recomputed approximations appear only in `validation.metric_agreement`.
- **The filter set is a reconstruction for BindCraft 1.** See section 5: for a run that records no thresholds, `fail` and `nm` apply BindCraft's default thresholds as `binder_review.config` knows them. A BindCraft 2 run is judged by the thresholds its campaign recorded; the ones on metrics Binderscope does not carry are listed in `runs[].filter_set.unevaluated` and not re-checked, and the near-miss highlight says so.
- **Placement is a rigid fit** of the design's own target on the reference chain, by default weighted around the binder because BindCraft's copy of the target differs a little from the reference. A design whose target matches fewer than ten reference residues by number cannot be placed and stops the build; one with fewer than 12 residues near the binder is placed by the whole-target fit; the exporter warns when the CA RMSD near the binder exceeds `place_warn_rmsd`. A reference number that two residues carry (an insertion code) is left out of the fit, with a warning. Distances to the other subunits and to the rest of the protein are estimates to a few ångström, and the reference holds one crystal conformation. [target_frame.md](target_frame.md) has the limits.
- **Backbone ids must be unique across runs.** The same backbone id in two runs fails validation.
- **Mode names** are generated from the enriched residues and make no claim about a biological site.
- **The spreadsheet highlight** of a run's `final_design_stats` workbook is stored as `prov.marked` with its meaning unconfirmed; nothing reads it.
- **Install from a checkout.** The viewer sources, the schemas and the validator script are found next to `src/`; the package data holds only the profiles. Without the validator script `build_blobs` raises `ExportError` unless `strict=False`, and without the viewer `write_bundle` raises `BundleError`. In a source tree without the `jsonschema` package (a dependency of the package), `build_blobs` raises an `ExportError` that says the blobs cannot be validated, unless `validate=False`.
- **Load time.** The inspector's start-up reads `inspect.js`; each design then loads its own `structures/<id>.js` the first time it opens.

## See also

[data_contract.md](data_contract.md) · [cli.md](cli.md) · [architecture.md](architecture.md) · [target_frame.md](target_frame.md) · [interface.md](interface.md) · [families.md](families.md) · [modes.md](modes.md) · [specificity.md](specificity.md) · [reference_data.md](reference_data.md) · [viewer.md](viewer.md) · [profile_spec.md](profile_spec.md) · [GUIDE.md](GUIDE.md)
