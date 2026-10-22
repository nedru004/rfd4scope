# Data contract

The contract between the Python side of Binderscope (readers, target frame, interface analysis, families, modes, specificity and the exporter) and everything that reads its output: the static viewer pages and any tool that opens a bundle. It is for developers and advanced users who read, produce or validate the JSON blobs.

The exact rules are machine-readable: the JSON Schemas in `schema/` (draft 2020-12; start with [schema/README.md](schema/README.md)). This page is the human part. It gives the conventions, says what each blob carries and which module produces and which page reads it, lists the rules a schema cannot express, and explains how validation runs and how to change the contract. It does not repeat the exhaustive field lists: read the schema for those.

## 1. The blobs

A bundle carries five JSON blobs and, next to them, the review files a reviewer saves.

| Blob | File in a bundle | Page global | Schema | Written by | Read by |
|---|---|---|---|---|---|
| campaign | `data/campaign.json` and `.js` | `RAW` | `campaign.schema.json` | `export` | overview, board, map, query |
| inspect | `data/inspect.json` and `.js` | `INS` | `inspect.schema.json` | `export` | inspector |
| homolog | `data/homolog.json` and `.js` | `HOM` | `homolog.schema.json` | `specificity` builds it, `export` adds the build date | inspector, map, query; the board loads it after it starts |
| profile | `data/profile.json` and `.js`, and inside campaign and inspect | `PROFILE` | `profile.schema.json` | `export` completes the project profile | every page, through the copy inside the blob it loads |
| manifest | `data/manifest.json` and `.js` | `MANIFEST` | `manifest.schema.json` | `export`, then `bundle` | every page; tools |
| review events | `reviews/<reviewer>.jsonl` | none | `review_event.schema.json` | the board, inspector and query page, through one store in `viewer/js/bs.js` | overview, board, inspector, query, tools |

Shared definitions (residue lists, ranges, coordinates, ids, a failed filter) are in `common.schema.json`. `profile.keys.json` is the registry of the text keys a profile may define. A key that depends on an entry of the profile is registered as a pattern: `finding.spec_orth.<entry>.some` and `finding.spec_orth.<entry>.none` stand for one pair per species key or group name of `specificity.detail`, so the registry names no species.

`export.py` is the only module that writes blobs; the other modules hand it values. [architecture.md](architecture.md) has the data flow, [export.md](export.md) how the exporter assembles the blobs.

| Module | What it contributes to the blobs |
|---|---|
| `readers.bindcraft1`, `readers.bindcraft2` | ids, status, rank, backbone, seed, sequence index, generation order, the BindCraft scores in their own scale, run settings, the per-run target construct, funnel counts, failure counters, input hashes, warnings |
| `target_frame` | the `target` block, `runs[].target`, the placement of every design in the reference frame (`design.ca`, the coordinates of `pdb_b`, `place_rmsd`), glycan distances, the context metrics (`olig_*`, `rest_*`, `cut_n`, `cov_n`, `mem_excess`), `ctx`, `octx`, the target side of the inspector blob |
| `interface`, `liabilities` | contacts (`tc`, `bc`, `nc`, `hot`, `contacts`), hydrogen bonds, salt bridges, buried unsatisfied polars, patches, per-residue tracks, secondary-structure fractions, `nterm_d`, `cterm_d`; `liab`, `pI`, `charge`, `gravy`, `n_cys`, `n_met`, `n_trp` |
| `families` | the fold class `sc`, `fold`, `diversity`, the quality score `q` |
| `modes` | `modes`, `design.mode`, `emb`, `site`; `fail` and `nm` of rejected designs |
| `specificity` | the homolog blob |
| `pipeline` | the pose record of each design (placement, context metrics, thumbnail `view` and its centre `vc`) that the exporter copies |
| `export` | `models`, `rf`, `hot_of`, `epi_prec`, `epi_rec`, the completed profile, the manifest, the highlights, the inspector records; runs the validator |
| `bundle`, `viewer_build` | the data wrappers, the structure files, the file hashes in the manifest |

## 2. Reading the schemas

- Every blob is permissive: `additionalProperties` is true everywhere, so a producer may add keys and a consumer must ignore keys it does not know.
- `required` lists what a page needs to draw something. A field that is not required may be absent; the page then hides the feature that needs it (section 12).
- A description that starts with `LEGACY` belongs to blobs written before profiles existed (a top-level `caps` object and `real` flag, the inspector blob's `frame`, `names` and `target_name`, a per-design `real`, `par[].ident_igv`, the homolog mirrors `species`, `show`, `subject` and `kind`). New exports do not write them, except the four homolog mirrors; the pages still tolerate them. A description that calls a field reserved means the pages do not read it.
- Many fields describe data that BindCraft runs never carry (ipSAE, PAE matrices, repeat seeds, lab outcomes, sampling weights, known-complex footprints). They exist so that importers of other sources can use the same pages, and they stay null, empty or absent for BindCraft output.
- The exporter writes a few keys the schemas do not list: `highlights` (campaign and manifest); `designs[].q`; `fold.runs` and `fold.names`; the extra mode keys (section 4.3); in a diversity block `distance`, `sweep`, `epoch`, `flags`, `order`, `replay_by_basis` and more `advice` keys (`p_new_family`, `recent_new`, `early_new`, `expected_more_600`, `families_more_600`, `n_families`, `S`, `f1`, `f2`); `target.frame`, `target.rest.traces`, `target.rest.not_in_reference`; in an inspector record `fail`, `nm`, `src` and `liab[].reason`; in the manifest `bundle.structures` (`format`, `folder`, `count`, `scripts`, `naming`); and the extra homolog keys of section 6.

## 3. Conventions

### 3.1 Numbering

| What | Numbering | Where |
|---|---|---|
| Target residues | the numbering of the full-length reference chain, ascending, gaps allowed. The schemas and the code call it UniProt numbering: with a UniProt accession the reference numbering must equal UniProt numbering (checked by sequence); without one the reference numbering is used as it is | `target.resi`, `hotspots`, `epitope`, `glycan` and the keys of `glycan_xyz`, `glycan_atoms` and `olig.buried`, `hotspots_by_run`, `epitope_by_run`, `runs[].target.*`, `modes.*.top` and `enr`, `designs[].tc` and `hot`, `contacts[].t`, `hbonds[].t`, `salt[].t`, the target side of `buns`, `ctx`, `t_contact`, `target_resi`, `homolog.resis` |
| Construct numbering (what BindCraft was given) | `orig = reference number + numbering.input_offset`; the exporter finds the offset by sequence and never assumes it | `target.orig_resi`, `target.numbering.input_offset`, `runs[].target.input_offset`, `runs[].settings.hotspots_input` |
| Binder residues in the campaign blob | `bc` is a 0-based index into `seq`, `ca` and `pl`; `liab[].p` is 1-based | `designs[]` |
| Binder residues in the inspector blob | 1-based everywhere | `contacts[].b`, `hbonds[].b`, `salt[].b`, the binder side of `buns`, `b_contact`, `patches[].res`, `ssbonds`, `liab[].pos`, `sibs[].diff`, `ctx.pair[0]`, the residue numbers of `pdb_b` |

Never index by position when numbers can have gaps. `target.resi[i]` is the number of entry `i` of every array over the target (`resn`, `ss`, `rsa`, `ca`, `atoms.res`, `orig_resi`, `dom`, the homolog arrays, `plddt_t`, `t_dsasa`). Arrays over the binder (`seq`, `ca`, `pl`, `plddt_res`, `rsa_res`, `dsasa_res`) run in residue order: entry `i` is binder residue `i + 1`.

A run can delete stretches from the construct, so a design only has contacts, coordinates and metrics for the residues of its own run. Arrays over the target in the inspector blob hold `null` or 0 where the design's run lacks the residue.

### 3.2 Frames and coordinates

All coordinates are in ångström in the reference frame, the frame of the reference chain. A design is placed by a rigid superposition of its own target chain on the reference chain over the residues they share (matched by number), so the binder, the other subunits and the removed regions come along. The superposition is weighted around the binder by default, because BindCraft's copy of the target differs a little from the reference (`import --placement`; [target_frame.md](target_frame.md), Placement). This applies to `target.ca`, `atoms.x`, `design.ca`, `glycan_xyz`, `glycan_atoms`, `membrane`, `sites`, `olig.neigh`, `rest.traces`, `design.vc`, `center` and to both PDB texts (`target_pdb`, `pdb_b`).

- `ca`, `atoms.x`, `olig.neigh[chain]`, `rest.traces[].ca` and `glycan_atoms.*.xyz` are flat arrays `[x0, y0, z0, x1, ...]` whose length is a multiple of 3 (a JSON Schema cannot say so; the validator does).
- `view` is a row-major 3x3 rotation whose rows are the camera x, y and z axes expressed in the reference frame (`pipeline.canonical_view`). `rf` and `poses[].R` with `poses[].t` are rigid transforms, a row-major rotation (9 numbers) and a translation (3), with `x' = R x + t`.
- A membrane plane is `{axis, centroid, offset}` with a unit `axis` pointing towards the membrane. A binder atom `x` is past the plane when `(x - centroid) . axis > offset`. `mem_excess` is the largest value of `(x - centroid) . axis - offset` over the binder's heavy atoms: negative means clear by that much.

### 3.3 Units and scales

- Values that come straight from BindCraft keep BindCraft's CSV scale: pLDDT, pTM, i_pTM and i_pLDDT are 0 to 1; `ipae_norm` is the inter-chain PAE divided by 31 and `ipae_mean = ipae_norm * 31` is in ångström; `dg` is in Rosetta energy units; RMSDs and distances are in ångström.
- Shares and fractions in `m` are 0 to 1, never percent. BindCraft's own percent columns stay in `src` as written (0 to 100).
- PDB B-factors and the per-residue arrays `pl`, `plddt_res` and `plddt_t` are pLDDT on 0 to 100. Areas are in Å^2. Kinetic constants in `outcome` are SI (M, 1/(M s), 1/s).
- Rounding: BindCraft values are written as the CSV had them. Recomputed areas and most distances are rounded to 0.1 (contact, hydrogen-bond and salt-bridge distances and `place_rmsd` to 0.01), fractions to 0.001, `pI` to 0.01, and campaign CA coordinates to 0.1 Å.

### 3.4 Null, absent, empty

- **Absent**: the exporter did not produce the thing at all. The page falls back or hides the feature.
- **null**: produced but unknown for this item. The page shows an en dash and the checks leave the part that needs the value out; a missing value never fails a design. `design.m` always carries its 38 core keys, and a quantity the data does not have is `null` with the matching capability flag off.
- **Empty list**: known to be none (a design without liabilities, a target without glycans).
- **Unknown counts** (funnel stages, `n_traj`) are `null` with `status: "unknown"`. The pages show them as unknown (the overview draws a `?`), never as 0, and draw a `derived` count as an estimate.

### 3.5 Identity

- `design.id` is the BindCraft file stem without `_model{k}` (for example `binder_l68_s123456_mpnn3`), unique across the project. When two runs would collide the exporter writes `<run>/<id>` and keeps the original in `design.name`. In file names the `/` is written `__`; a link to a design is `#d=<id>`.
- `design.bb`, the backbone id (the stem without `_mpnn{n}`), is one BindCraft trajectory and the unit of a family; `design.fam` equals `bb`. A backbone belongs to exactly one run: the same backbone id in two runs is a validator error. BindCraft ids contain the binder name, length and seed, so this happens only when two runs share a binder name and a seed.
- Run ids (`runs[].id`, `design.run`) are short slugs given at import (`run01`, `run02`). Without an explicit id the importer takes `runNN` from the folder name, else the folder name.
- Mode ids are `M1` to `M7` (the pages have seven colours and shapes). Site ids are `A1`, ...; `A1` is the intended epitope and is required. When no run records a hotspot there is no intended epitope and `A1` is built from where the binders sit (section 4.1).
- An inspector tag is the design id. A review event names a family by its `bb` and a design by its id.

### 3.6 Versions and compatibility

Each blob carries `schema: "binderscope.<blob>/1"`. Adding fields does not change the number; a change that makes an old reader wrong bumps it. The validator accepts exactly the version its schema names; the viewer does not check the string. `profile.version` counts the content versions of one profile. A review event has `v: 1`.

### 3.7 Delivery to a page opened from disk

A browser blocks `fetch` of a local file, so a page cannot read `data/campaign.json` when it is opened from `file://`. The bundle therefore writes every blob twice: the canonical `.json`, and a same-folder script that a page loads with a plain `<script src>`:

```
window.BINDERSCOPE = window.BINDERSCOPE || {};
window.BINDERSCOPE.campaign = window.RAW = { ...the blob... };
```

The page globals are `RAW`, `INS`, `HOM`, `PROFILE` and `MANIFEST`. Each page loads only the blobs it needs: the overview and the board load `campaign.js` and `manifest.js`; the map and query pages add `homolog.js`; the inspector loads `inspect.js`, `homolog.js` and `manifest.js`. The board adds `homolog.js` after it has started, when `species_panel` is on and the manifest lists the file. The profile travels inside the campaign and inspect blobs (`campaign.profile` and `inspect.profile` must be equal), so no page loads `profile.js`; the file is there for tools.

The inspector blob is the large one. By default `data/inspect.js` is written without the binder structures (`pdb_b`): the inspector loads `structures/<design id>.js` when it opens a design, and that script sets `window.BINDERSCOPE.structures[<id>]` to the PDB text of the whole complex. `data/inspect.json` keeps `pdb_b` because the schema requires it. The PDB text of the whole complex has the design's own copy of the target, as BindCraft modelled it, in chain A (placed on the reference frame by the same transform as the binder) and the binder in chain B; the inspector draws chain A as the target once the file is loaded (a record that already carries `pdb_b`, as in a bundle built with embedded structures, never loads it, so the reference chain stays the target). See [export.md](export.md) for the bundle layout.

## 4. Campaign blob

`data/campaign.json` is everything the overview, board, map and query pages read, for all runs of a project. Required: `profile`, `target`, `designs`, `modes`, `run`.

| Field | Meaning | Produced by |
|---|---|---|
| `schema` | `binderscope.campaign/1` | `export` |
| `profile` | the project profile; the same object in every blob | `export` |
| `target` | the reference chain and its context (below) | `target_frame` |
| `designs` | one record per exported design, all runs together (below) | `export` from the modules above |
| `modes` | binding modes `M1` to `M7`, clusters of contact position | `modes` |
| `run` | project summary: `source`, `name`, summed counts (`n_traj` is null when any run's is unknown), the pooled failure counters | `export` |
| `runs` | one entry per run (below). Required when `caps.multi_run` is true; without it the viewer builds one virtual run from `run` | `export` from `readers`, `target_frame`, `families` |
| `fold` | the TM-align matrix between binders | `families` |
| `diversity` | the family statistics at the default thresholds | `families` |
| `highlights` | the "What stands out" statements ([export.md](export.md)) | `export` |
| `generated` | `{tool, version, date}` | `export` |

### 4.1 `target`

The reference chain the designs are placed on and the context the checks use. `resi` is the union of the residues of all runs' constructs; what one run has is `runs[].target.resi_ranges`. Required: `resi`, `resn`, `ss`, `ca`, `atoms`, `hotspots`, `epitope`, `glycan`, `glycan_xyz`, `membrane`, `sites`.

| Group | Fields | Meaning |
|---|---|---|
| Residues | `resi`, `resn`, `ss`, `rsa`, `ca`, `dom` | numbers, one-letter names, secondary structure (H, E, L), relative accessibility of the chain alone, CA coordinates, domain label per residue (`rsa` and `dom` are optional) |
| Atoms | `atoms.{x, res, el, name, sasa}` | heavy atoms of the reference chain, no hydrogens; `res` is the 0-based index into `resi`; `sasa` is per-atom area of the chain alone in Å^2 |
| Epitope | `hotspots`, `epitope`, `hotspots_by_run`, `epitope_by_run` | pooled sets and one set per run. A design is judged against its own run's set. `epitope` is the hotspots plus the residues within 5 Å of one. BindCraft can be run without hotspots: such a run has empty sets (and so has the pooled set when no run has hotspots), and its designs have `hot` empty and `hot_of` 0 |
| Sites | `sites.A1.{center, normal, residues, source}` | the intended epitope site. `center` is the mean CA position of the pooled hotspots, `residues` the epitope (the hotspots and their neighbours) and `source` is absent. When none of the runs records a hotspot, A1 is where the binders sit: `residues` are the target residues that the most accepted designs contact (each contacted by at least half as many designs as the most contacted residue; all designs with a pose when none was accepted), `center` is their mean CA position and `source` reads `no hotspots recorded: the residues the designs contact most`. With no contacted residue to go by, `center` is the centre of the construct, `residues` is empty and `source` reads `no hotspots recorded and no design contacts: the whole construct, viewed along its thinnest axis`. `normal` is always a unit vector: from the CA centre of the construct to `center`, or, when `center` is the centre itself, the axis along which the construct's CA atoms vary least (a view along it shows the flattest face). It orients every 3D view |
| Membrane | `membrane.{axis, centroid, offset, label, source}` | the membrane-side exclusion plane (section 3.2). Without a known transmembrane region the plane is a placeholder that no page reads, and `mem_excess` is null for every design |
| Glycans | `glycan`, `glycan_xyz`, `glycan_atoms` | N-glycosylation sites inside the construct that have an anchor atom, the anchor (Asn ND2) per site, and the sugar atoms of the reference structure attached to them (`chain`, `resn`, `el`, `xyz`, `name`, `res`, `src`) |
| Other subunits | `olig.{buried, neigh, chains, labels, source, min_area}` | CA traces of the other subunits of the reference assembly, and the area (Å^2) each reference-chain residue buries against them above `min_area` |
| Rest of the chain | `rest.{ref, ranges, n_res, traces, not_in_reference, source}` | reference residues that no run's target contains, drawn as CA traces; per-run removed stretches are in `runs[].target.removed` |
| Numbering | `orig_resi`, `numbering.{scheme, input_offset, input_label}` | section 3.1 |
| Reference | `reference.{pdb, chain, assembly, uniprot, label, given}`, `unresolved` | where the coordinates come from (`given` false and `pdb` empty when no reference structure was given: the construct is the frame); stretches inside the construct span that the reference structure lacks (a chain break in the CA trace) |
| Names | `name`, `uniprot`, `construct` (first and last residue), `length`, `frame` | copied from the profile and the frame; `frame` is the frame strip (the segments of the full-length chain that the inspector draws) derived from annotations |
| Known complexes | `foot` | named residue footprints of known complexes; reserved, never written for BindCraft output |

Read by: the board, map and query pages read the target block through `viewer/js/common.js`; the overview shows the hotspots next to chips from the profile. The inspector reads the same information from its own blob. No page reads `orig_resi`, `numbering`, `unresolved`, `epitope_by_run`, `olig.{chains, labels, source, min_area}` or `sites.A1.{center, residues, source}` today (the pages use `sites.A1.normal` only); they are there for tools and for the audit of the numbering.

`hot` and `hot_of` of a design count hotspots of the design's own run: show `hot.length / hot_of`, never `hot.length / hotspots.length`. `hot_of` is 0 for a run that records no hotspot: there is then no ratio to show, and a count such as "0 of 0 hotspots touched" says nothing about the design.

### 4.2 `runs[]`

Two runs on one target can differ in settings, construct, hotspots, lengths and filters, so each run has its own entry. Required: `id`.

| Field | Meaning |
|---|---|
| `id`, `label`, `folder` | run id, display label, basename of the run folder (absolute paths are not written) |
| `reader`, `source`, `tool_version`, `experimental` | `bindcraft1` or `bindcraft2`; the tool as the pages name it; the version the settings record; true for a reader written from documentation only |
| `n_designs` | `accepted`, `rejected`, `rejected_with_structure`, `rows` (rows of `mpnn_design_stats.csv`), `families` |
| `settings` | `binder_name`, `protocol`, `advanced_settings`, `filters_name`, `target_settings`, `helicity`, `chains`, `length_range`, `final_designs_target`, `hotspots` (reference numbering), `hotspots_input` (as written, construct numbering), `sources` (for `hotspots`, `length_range` and `final_designs_target`: `settings_json`, `csv`, `inferred` or `unknown`) |
| `target` | the per-run construct (below) |
| `filters` | the filter rules designs were checked against: `stem`, `metric` (the key of `design.m` that carries the same quantity, or null), `threshold`, `op`, `per_model`, `provenance`, `note`. For a run that records no thresholds (BindCraft 1) this is BindCraft's default filter set as `binder_review.config` reconstructs it, because BindCraft does not copy its filter file into the run folder; `provenance` then says how well a threshold is supported: `counter-verified` (the rule reproduces BindCraft's own failure counter), `bounded` (the rows of the run only bound it) or `memory` (taken from BindCraft's documentation). For a run that recorded its own thresholds (BindCraft 2, `campaign_metadata.json`) the rules are those thresholds, `provenance` is `campaign` (better evidence than any reconstruction: the threshold the campaign used), `stem` is the campaign's own filter name, and each rule adds `source` (`campaign_metadata`, or `settings.min_plddt_final` for the pLDDT filter), `mandatory`, `judged_per_model` (BindCraft judges it on each validation model while the CSV holds the rounded mean) and `value_stem` (the key of `design.src.average` that holds the values; the value is also repeated there under the campaign's name). Such a run also has `filter_set`: `source` (`campaign`), `n_rules`, `unevaluated` (recorded thresholds on metrics Binderscope does not carry or that no design carries: name, op, threshold, mandatory, reason), `unlimited` (names recorded with an infinite threshold, which are no limit and no rule) and a `note`. A run on the defaults has no `filter_set` |
| `funnel` | ordered stages `{key, label, n, status, note}`; `status` is `observed`, `derived` or `unknown`, and `n` is null exactly when the status is `unknown` |
| `failure` | `[[stem, label, count], ...]`, most frequent first. A sequence can fail several filters, so the counts overlap |
| `n_traj`, `n_traj_pass`, `n_scored`, `n_accepted` | the main funnel counts again, for the overview cards |
| `traj_basis` | what `design.traj` counts: `attempted` trajectories, `passed` trajectories (the row order of `trajectory_stats.csv`) or `none` (then `traj` is 0) |
| `epoch` | `{hash, fields}`: a hash of the settings that change the statistics (numbering, hotspots, construct, length range, filters). Estimates are never pooled across different epochs |
| `paratope`, `paratope_rejected`, `diversity_paratope` | only for a run built on a fixed scaffold: where the interface residues of the accepted (and of the rejected) designs fall on the scaffold's designed segments and framework, and the axes that replace fold diversity (paratope profiles, loop-length classes, epitope spread; fold itself is `off` with its reason). `paratope` and `paratope_rejected` are absent when the edit spec cannot be used or no design aligns; `diversity_paratope` is then present with `unknown` axes and the reason. Shapes: [paratope.md](paratope.md) |
| `term` | the run's terminus block: `format` (`kind`, `known`, `copies`, `chains`, `applies`, `why`, `convention`, `convention_why`, `scaffold`, `peptide`, `notes`), `designed_for` (`known`, `accessible`, `together`, `buried`, `items`: what BindCraft was asked to do about the ends), `tags` (key, what it stands for, radius in A), `const` (every threshold of the analysis), `env` (a reference was given) and `partner` ([termini.md](termini.md)) |
| `warnings`, `notes` | what the reader found odd, and what is only worth knowing: for example that the run records no hotspots (the epitope checks and `epi_prec` and `epi_rec` are then empty), or that a BindCraft 2 campaign numbers its binder residues from 0 and was analysed through renumbered copies of its structure files |

`runs[].target` is the construct of one run, in reference numbering. `resi_ranges` are the present stretches, ascending (required); `removed` lists the stretches missing between the first and last residue and must equal the gaps between the ranges; `first`, `last`, `n_resi`; `cut_points`; `input_offset`; `hotspots` and `epitope` of the run; `sequence_matches_reference` (the run's target residues agree with the reference chain at the same numbers) and `consistent` (every structure of the run carries the identical residue set). The cut points are the first and last residue (unless the construct reaches a native end of the chain) and both flanks of every removed stretch. An invented example: ranges `[[10, 60], [75, 120]]` give `removed` `[[61, 74]]` and cut points `[10, 60, 75, 120]` for a construct that is cut at both ends.

Read by: the overview (runs table, funnel), the board (funnel, failure counters, trajectory counts, diversity), the query page (`filters`, `failure`) and the inspector (`resi_ranges`, `removed`, `cut_points` for the frame strip). No page reads `epoch`, `settings.sources` or `settings.hotspots_input`.

### 4.3 `modes`

Keys `M1` to `M7`, ordered by size: the pages have seven colours and shapes, and the modes module never chooses more than seven clusters. Each mode: `name` (generated from the enriched residues, `auto_name` true), `note` (one to three sentences built from the numbers), `n` (accepted designs), `n_rejected`, `by_run`, `top` (most contacted residues), `enr` (residues enriched against the other modes) and `site` (the key of `target.sites` the mode overlaps most). The modes module also writes `hot_share`, `n_wrong_site`, `mean_hot_frac` and `nearest_hot`; no page reads them. See [modes.md](modes.md).

### 4.4 `designs[]`

One designed binder sequence with its model, placed in the reference frame. Accepted designs and rejected designs that have a structure share the shape and are told apart by `status`; which designs are listed is in [export.md](export.md). The schema's `required` list (identity, class, sequence, geometry, contacts, `m`, `glycan`, `liab`) is the minimum a page needs to draw a design.

| Group | Fields | Meaning |
|---|---|---|
| Identity | `id`, `name`, `run`, `status`, `rank`, `bb`, `fam`, `s`, `seed`, `traj` | section 3.5. `s` is the sequence index on the backbone (the MPNN number). `traj` is the generation order of the backbone within its run, in the units of `runs[].traj_basis` (1 is first, 0 unknown). `rank` is BindCraft's rank (accepted designs); the pages that load `common.js` replace it in memory with their own rank, and the board reads BindCraft's from `prov.rank` |
| Class | `sc`, `mode`, `site`, `emb` | the fold class (a fold-cluster label `F1`, `F2`, ... from the families module, `unclustered` for a rejected design that fits no cluster), the binding mode, the nearest site, and the position on the 2-D map (0 to 1) |
| Sequence and shape | `len`, `seq`, `ca`, `pl`, `view`, `vc`, `rf`, `flip` | length, sequence, CA trace (3 * `len` numbers), per-residue pLDDT (0 to 100, optional), the thumbnail view and its centre, the pose of a re-scored model (identity for BindCraft output) and a re-score flag (false) |
| Contacts | `tc`, `bc`, `nc`, `hot`, `hot_of`, `glycan` | contacted target residues (any heavy-atom pair within 4.5 Å), contacted binder residues (0-based), the number of atom-pair contacts, the contacted hotspots of the run and their denominator, and the distance in Å from the nearest binder atom to each glycan site (sugar atoms when modelled, else the Asn ND2 anchor; key is the site number as a string) |
| Metrics | `m` | section 4.5 |
| Predictors | `models` | one entry for BindCraft output: `{model: "AF2 (BindCraft)", ipsae: null, iptm, rmsd}` where `rmsd` is the hotspot RMSD |
| Sequence flags | `liab` | `[{k, p, m, s}]`: kind, 1-based position, motif and severity (`info`, `warn`, `bad`). A whole-sequence flag has position 1. See [interface.md](interface.md) |
| Provenance | `prov`, `src` | `prov`: run, binder name, backbone, length, seed, MPNN index, protocol, `model` (the AlphaFold2 model whose pose is in the structure file) and `model_file` (relative to the run folder), rank, the CSV the scores came from, design time, BindCraft's `Notes`, `structure` (`present` or `missing`), `marked` (a highlighted row of the run's spreadsheet; its meaning is not recorded and no page reads it), `fit` (how the design was placed on the reference chain: `interface`, `all` or `core`) and `fit_note` (why it was not placed by the fit that was asked for; absent otherwise). `src`: every BindCraft number exactly as written: `average` (stem to `Average_<stem>`), `models` (model number to stem to value) and `interface_aas` |
| Paratope | `para` | only for a design of a scaffolded run that aligned to its scaffold: `segments` (region to interface residues), `loops`, `framework`, `lengths`, `face`, `face_mutated` and `framework_at` ([paratope.md](paratope.md)) |
| Terminus analysis | `term` | only for accepted designs and near-miss rescues: `applies` (false for a cyclic peptide, with `why`), `n` and `c` (`d`, `d_env`, `env_what`, `at`, `dir`, `aim`, `away`, `side`, `contact`, `iface_n`, `rsa`, `plddt`, `ss`, `elem`, `tail`, `room`, `limit`, `g`, `conf`, `via_tail`), `nc_d`, `env`, `pick` and `immob` ([termini.md](termini.md)) |
| Inspector link | `inspect` | the tag of the design in the inspector blob, or null |
| Rejected designs | `fail`, `nm` | `fail[]` lists the filters the design fails under the run's filter set, one entry per rule and scope (`Average` or a model number): `stem`, `metric`, `scope`, `value`, `threshold`, `op`, `gap`, `rel` (the miss as a share of the threshold). `nm` summarises: `n_failed` (distinct stems), `stems`, `worst_rel`, `score` (0 to 1, higher is closer to passing) and `near` (exactly one stem missed by at most 25 percent of its threshold). An accepted design has `fail: []` and no `nm`. For a run judged by its own recorded thresholds, `fail[]` starts from BindCraft's own verdict (every failure BindCraft named stands) and adds only a failure that the CSV mean shows and BindCraft did not name (it stops at the first validation model that fails); each entry then also has `reported` (BindCraft named it) and `measured` (false when BindCraft judged it on one validation model and the CSV mean passes: `scope` is `Model`, `gap` is 0.0, a lower bound, and `rel` is null). A design with an unmeasured failure, or with a name BindCraft reported that no rule can score, is never `near`: its `nm` has `score` 0 and `unmeasured` (the names). Rejected designs take no part in families or diversity |
| Other | `q`, `wt`, `outcome`, `method`, `author`, `pbid`, `fpov` | `q` is a quality score in (0, 1] that orders the accepted designs of a run by BindCraft rank, then average i_pTM; the pages that load `common.js` overwrite it with their own score. The rest are dataset and lab fields (`wt` sampling weight, `outcome` assay results in SI units, `fpov` footprint overlaps); the exporter never writes them for BindCraft output |

Read by: the overview (counts), the board, map and query pages. The board reads `bb`, `s`, `traj`, `mode`, `sc`, `ca`, `view`, `vc`, `tc`, `bc`, `hot`, `m`, `liab`, `pl`, `fail`, `inspect` and `prov.rank`; the map adds `emb`; the query page adds `nm` and `src`. No page reads `site` or `nc`.

### 4.5 `designs[].m`

All 38 core keys are always present and `null` where the data does not carry the quantity; the capability flag named after a metric is then off.

| Keys | Source |
|---|---|
| `plddt`, `ptm`, `iptm`, `iptm_b`, `ipae_norm`, `sc`, `dg`, `surf_hyd`, `binder_rmsd`, `hotspot_rmsd`, `target_rmsd`, `ciplddt` | copied from BindCraft's `Average_<stem>` columns (CSV scale). `iptm` and `iptm_b` both carry `i_pTM`; `ipae_mean` is `ipae_norm * 31` in Å |
| `dsasa`, `n_hbonds`, `n_salt`, `n_unsat`, `hyd_frac`, `hphob_patch`, `loop_frac`, `helix_frac`, `strand_frac`, `iface_helix`, `nterm_d`, `cterm_d` | recomputed from the structure by the interface module; BindCraft's own value stays in `src` |
| `pI`, `charge`, `gravy`, `n_cys`, `n_met`, `n_trp` | `liabilities` (bulk properties of the sequence) |
| `epi_prec`, `epi_rec` | `export`: the contacted residues against the run's epitope (precision: share of contacted residues inside it, 0 for a design with no contacted residue; recall: share of it that is contacted). Both are `null` for a run that records no hotspot, since there is no epitope to be precise about or to recall (the schema allows `null`) |
| `olig_min`, `olig_n3`, `olig_n4`, `olig_n`, `rest_min`, `rest_n3`, `rest_n4`, `cut_n`, `cov_n`, `mem_excess`, `place_rmsd` | `target_frame`: distances and atom counts against the other subunits and the removed regions, residues near a cut, residues the removed regions would bury, the membrane excess, and the CA RMSD of the design's target on the reference chain over all shared residues under the transform that placed the design |
| `place_iface_rmsd`, `place_iface_n`, `place_global_rmsd` | `target_frame`: the CA RMSD over the residues near the binder (within about 13 Å of a binder heavy atom, in the design's own frame) and how many residues that is (`null` when none is that near), and the CA RMSD that the plain whole-target fit gives. The exporter warns when `place_iface_rmsd` is above 1.5 Å: the design's own target then differs from the reference chain at the interface and the context distances are less certain |
| `min_ipae`, `ipsae`, `complex_rmsd`, `seed_spread`, `n_agree`, `esm_plddt`, `ipsae_max`, `cplddt`, `pde`, `lis`, `stalk_n` | null for BindCraft output; the capability flag is off |

The definitions of the recomputed quantities are in [interface.md](interface.md) and [target_frame.md](target_frame.md); the context metrics and the thresholds that turn them into verdicts (`profile.checks.context`) are in [profile_spec.md](profile_spec.md).

### 4.6 `fold` and `diversity`

`fold` is `{n, ids, tm, note}` with `runs` and `names` as parallel lists. `tm` is the base64 of an `n x n` uint8 matrix, row-major: `round(100 * TM-score)` normalised by the shorter chain, symmetric, diagonal 100. `ids[i]` names row and column `i`. It covers the accepted designs of all runs; clusters and estimates are still formed per run.

`diversity` holds the reference results of the family statistics: `basis_default`, `distance_default` (the average-linkage cut distance per basis; a basis is `fold`, `site` or `both`), `fold_weight`, one block per run in `runs.<id>` and a `pooled` block. A block has `n_designs`, `n_families`, `estimates_valid`, `clusters.<basis>` (clusters of families, largest first), `counts.<basis>` (`n`, `S`, `f1`, `f2`), and, when a generation order is known, `advice.<basis>` (coverage, richness estimates, `level`, `low_confidence`, yield interval, expected new clusters) and `replay`. It also carries `distance`, `sweep`, `epoch`, `flags` and `order`, and for every run `epitope`, the spread of the families' epitopes (`status`, `pairwise_jaccard`, `shared_by_all`, `n_families`, `sizes`, `source`; [families.md](families.md)). For a run built on a fixed scaffold the fold-based bases (`fold`, `both`) are left out of `clusters`, `counts`, `sweep`, `advice` and `replay_by_basis`, and the block says `fold` `{status: off, why}`, `basis_off` and `basis_default` `site`. The pooled block is descriptive: `estimates_valid` is false when the runs' epochs differ, and it carries advice only for a single-run project. The board reads only `basis_default`, `distance_default` and `fold_weight` and recomputes clusters and estimates in the browser (`viewer/js/family.js`); the blocks per run serve tools. Definitions and formulas: [families.md](families.md).

## 5. Inspect blob

`data/inspect.json` is what the inspector reads: for each inspected design the binder as PDB text, contacts, hydrogen bonds, per-residue tracks, confidence, context and sibling sequences, plus the target side. It has its own copy of the target so that the inspector needs no campaign blob. Required: `profile`, `target_pdb`, `target_seq`, `target_resi`, `designs`, `order`, `hotspots`, `epitope`, `glycan`, `glycan_xyz`, `membrane`, `t_ss`.

| Group | Fields | Meaning |
|---|---|---|
| Target structure | `target_pdb`, `target_seq`, `target_resi`, `t_ss` | PDB text of the reference chain (always chain A, whatever the chain of the reference structure is called, because the pages take chain B for the binder; reference numbering, heavy atoms, `HELIX` and `SHEET` records; sugars of the glycan sites follow as `HETATM`), its sequence, numbers and secondary structure. `target_resi` equals the campaign `target.resi` |
| Target context | `hotspots`, `epitope`, `glycan`, `glycan_xyz`, `glycan_atoms`, `membrane`, `olig`, `rest`, `runs` | the same objects as in the campaign blob. `olig.buried_all` is the whole interface of the reference chain with the other subunits (residues outside the union construct included) next to `olig.buried`, which keeps the residues inside it. `rest.traces` holds one trace per stretch that at least one run lacks; for a design of run `r` the page draws only the stretches that `runs[r].target` does not contain. `runs[]` is reduced to `id`, `label`, `hotspots` and `target` |
| Designs | `designs`, `order` | `designs` maps a tag (the design id) to a record; `order` lists the tags: accepted designs by run and rank, then near-miss rejected designs by score. More than twelve designs get a searchable list |
| Other | `names`, `foot`, `partners`, `frame`, `real`, `target_name`, `run`, `modes`, `generated` | optional or legacy; the exporter writes `generated` only |

Per design (`designs[tag]`) the schema requires the identity keys `tag`, `id`, `bb`, `mode`, `mode_name`, `scaffold`, `site` and `modality`, the binder, the tracks, the interactions, the summary values, `poses`, `sibs`, `metrics`, `liab`, `flip` and `center`. `label`, `run`, `status`, `rank`, `prov`, `ctx`, `octx`, `pae`, `seeds`, `fail`, `nm` and `src` are optional for the schema (the validator needs `ctx` and `octx` when the matching capability flags are on).

| Group | Fields | Meaning |
|---|---|---|
| Identity | `tag`, `id`, `label`, `run`, `status`, `rank`, `prov`, `bb`, `mode`, `mode_name`, `scaffold`, `site`, `modality` | as in the campaign record. `scaffold` is the fold class `sc`; `modality` is `minibinder` for BindCraft designs (the schema also allows `nanobody`, `scFv` and `peptide`) |
| Binder | `seq`, `n`, `ss`, `pdb_b` | sequence, length, secondary structure (H, E, L) and PDB text of chain B: residues 1 to `n`, heavy atoms, `HELIX` and `SHEET` records first, B-factor equal to pLDDT (0 to 100), reference frame |
| Per-residue tracks | `plddt_res`, `rsa_res`, `dsasa_res` (binder, length `n`); `plddt_t`, `t_dsasa` (target, one per `target_resi`) | pLDDT, relative accessibility in the complex, buried area in Å^2. `plddt_t` is null and `t_dsasa` is 0 where the design's run lacks the residue. `ddg_res` and `t_ddg` are null (the alanine-scan proxy is not produced) |
| Interactions | `contacts`, `hbonds`, `salt`, `buns`, `patches`, `ssbonds`, `ssbond_basis`, `disulfides`, `voids` | `contacts[]`: `t`, `b`, `d` (closest heavy-atom distance, 4.5 Å cut-off), `n` (atom pairs). `hbonds[]`: donor-acceptor pairs within 3.5 Å with residue and atom names and the direction `k` (`D>A`: the target donates, `A<D`: it accepts). `salt[]`: pairs within 4.0 Å. `buns[]`: buried unsatisfied polar atoms, `side` `t` or `b`. `patches[]`: up to three exposed hydrophobic patches, largest first, `area` and `res`. `ssbonds`: the binder's disulfides as pairs of 1-based residues, those the structure file records or, when it records none, those read from SG-SG geometry; `ssbond_basis` (`recorded`, `inferred` or `none`) says which, and `disulfides[]` gives each pair's `a`, `b`, `d` and `band` (`close` or `loose`). A reader that treats `ssbonds` as measured fact without reading `ssbond_basis` overstates what a BindCraft 2 file says, because such a file records no bond at all ([interface.md](interface.md), Disulfides). `voids` is reserved and empty |
| Summary | `dsasa`, `hyd_frac`, `t_contact`, `b_contact`, `hot`, `nterm_d`, `cterm_d`, `mem_margin`, `glycan`, `center` | the same values as in the campaign record, `mem_margin = -m.mem_excess`, and the centre of the interface in the reference frame |
| Context | `ctx`, `octx` | `ctx`: the removed-region context: `ref`, `removed` (a list of ranges), `nearest` (Å), `n3`, `n4`, `n6` (binder atoms within 3, 4 and 6 Å), `pair` (binder residue and the nearest removed residue), `pair_resn`, `covered` and `covered_all` (residue and the area the removed regions bury, Å^2), `cut` (`points`, `near`). `octx`: the other-subunit context: `olig_min`, `olig_n3`, `olig_n4`, `olig_buried`; for a homo-oligomeric reference also `sym_min` and `sym_n3`, the approach of the binder to its own copy on the other protomer (information only) |
| Confidence | `pae`, `pae_n`, `poses`, `seeds` | `pae` is null (BindCraft writes no PAE matrix; when present it is the base64 of a uint8 matrix of PAE / 0.125 with the binder residues first); `poses[0]` is the design itself (BindCraft output has exactly one entry with the identity transform); `seeds` is empty |
| Siblings | `sibs` | the sequences on the same backbone, the design included: accepted ones by rank, then rejected ones. Each has `id`, `seq`, scores (`iptm`, `ipae_norm`, `plddt`, `dg`, `sc`), `unsat`, `hb` and `hot` (null for a row without a structure), `ndiff` and `diff` (1-based positions that differ from the first listed sequence) and `status` |
| Metrics and flags | `metrics`, `liab`, `flip` | `metrics` is the campaign `m` plus `length`; `liab[]` uses long keys `kind`, `pos`, `motif`, `sev` (and a one-line `reason`) |
| Failed filters, BindCraft columns | `fail`, `nm`, `src` | copies of the campaign `fail` (empty for an accepted design) and `nm` (rejected designs only), and the three BindCraft columns the inspector shows next to its own recomputed counts (`n_InterfaceUnsatHbonds`, `n_InterfaceHbonds`, `dSASA`) in `src.average` |

Read by: the inspector only.

## 6. Homolog blob

`data/homolog.json` holds conservation and specificity data over the same residues as `target.resi`: every per-position array and every aligned sequence (`map`) has one entry per entry of `resis`. Required: `resis`, `seq`, `cons`, `ident`, `grade`, `cover`, `n_orth`, `orth`, `par`. Which species, labels and keys the pages show is decided by `profile.specificity`.

| Field | Meaning |
|---|---|
| `resis`, `seq` | the residues (equal to the campaign `target.resi`) and the reference residue at each |
| `cons`, `ident`, `cover`, `grade` | per position, computed from the orthologs only: conservation (0 to 1, from the entropy of the column), the share of orthologs with the reference residue, the share with a residue instead of a gap, and a grade from 1 (variable) to 9 (conserved) |
| `n_orth`, `orth[]` | the number of orthologs that went into those arrays (entries marked `extra` excluded) and the aligned orthologs: `acc`, `org`, `ident` (over the whole alignment), `map` (one letter per entry of `resis`, `-` for a gap), `gene`, `label`, `key`, `taxon`, `reviewed`, `length`, and `optional`, `lowconf`, `stats` |
| `par[]` | paralogs (related proteins of the same organism), empty for a target without a paralog panel: `acc`, `name`, `gene`, `cov` (share of the aligned region), `ident_ref`, `map`, `label`, `key`, `length`, and `ident_full`, `optional`, `distant`, `lowconf`, `stats`, `aligner`, `struct` |
| `par_cons`, `par_ident`, `par_cover`, `par_grade`, `n_par`, `par_keys`, `par_note` | the same tracks for the default paralogs, and one sentence on how they were matched |
| `species`, `show`, `subject`, `kind` | mirrors of `profile.specificity` for older pages |
| `consurf` | optional ConSurf-DB overlay (academic, non-commercial use; off unless requested): `source`, `pdb`, `chain`, `retrieved`, `grade` per entry of `resis` (null where ConSurf has none), `licence` |
| `note`, `generated` | where the sequences came from; tool, version, date, parameters, warnings |

A species entry of the profile matches an ortholog by organism name or by `acc:<accession>`. A panel is built when the target has a UniProt accession and the profile names species or paralogs. Otherwise the project still gets a blob, with no orthologs, no paralogs and flat tracks, and the flags `species_panel` and `paralog_panel` are false. How the panel is built and what each track means: [specificity.md](specificity.md) and [reference_data.md](reference_data.md).

Read by: the inspector, map and query pages, and the board's Specificity row.

## 7. Profile

One profile per project holds everything target-specific that the pages show or decide: names and numbering, the frame strip, the specificity panel, the capability flags, how the checks read the data and the sentence templates. It is written into `campaign.profile`, `inspect.profile` and `data/profile.json`; the three copies are identical (a validator rule). Its fields are described in [profile_spec.md](profile_spec.md) and `schema/profile.schema.json`; required: `schema`, `id`, `kind`, `target`, `caps`.

The exporter completes the project's profile from the data:

- `caps` is written for all 38 flags. A flag is true exactly when its data is present and the profile does not switch it off; a profile can switch a flag off but never on. `consurf` is true only when the overlay was requested and exists, and `reviews` is always true. The flags `outcomes`, `weights`, `flag_region`, `footprints`, `has_pae`, `has_boltz`, `models`, `has_seeds`, `ddg_scan` and `decoy_cofold` are always false, and the metric flags `ipsae`, `min_ipae`, `complex_rmsd`, `seed_spread`, `n_agree` and `esm_plddt` are false for BindCraft output because those metrics are null.
- `target.uniprot` is filled from the frame's accession. For the built-in default profile, whose target is a placeholder, the name, short name, gene and organism come from the UniProt record when an accession was given; a profile that names its target keeps its own words.
- `target.construct` (first and last residue), `length`, `reference`, `numbering.input_offset`, `label` and `span_label` come from the target frame.
- `frame` stays as authored when the profile has one (the profile's own segment names win); with an accession, a difference to the strip derived from the reference annotations is reported as a manifest warning.

A capability flag promises data. A page asks the flag, never the project name; the validator checks the promise against the data (section 10).

## 8. Manifest

`data/manifest.json` records how a bundle was made, for audit and reproduction. Required: `schema`, `tool`, `created`, `bundle`, `sources`, `runs`, `caps`.

| Group | Fields | Meaning |
|---|---|---|
| Tool | `tool.{name, package, version, git_commit, python, platform, command, libraries}` | the tool and library versions and the command line that made the bundle |
| Bundle | `bundle.{id, name, title, pages, data, structures, files}` | `id` is the first 16 hex characters of the sha256 of the sorted source hashes and the profile; review events refer to it. `files` lists every file of the bundle with size and sha256 except the two manifest files, which cannot hash themselves |
| Inputs | `sources[]`, `reference[]` | every input file with `role`, `path`, `sha256`, `size` (a structure folder is one entry per run and role with a `count` and a hash of the file hashes), and the public reference records used, with url, retrieval date and hash |
| Runs | `runs[]` | per run: reader, versions, `settings`, `settings_raw` (the settings file unchanged), `target`, `filters`, `counts` (designs, funnel, failure counters, and the table of counts read from the files against what was exported), `epoch`, warnings, notes and `annotations` (highlighted spreadsheet rows, note files) |
| Capabilities | `caps` | a copy of `profile.caps`, so a tool can see what the bundle carries without opening the blobs (a validator rule keeps them equal) |
| Thresholds | `thresholds.{checks, context, filters, diversity, near_miss, interface, placement}` | the thresholds that shaped numbers and verdicts, as used: the check presets and context thresholds of the profile, the filter rules, the diversity cut distances, the near-miss rule (`max_failed_stems`, `max_rel`), the interface cut-offs (contact, hydrogen bond, salt bridge, partner contact and nearness, clash, SASA probe and points, placement warning RMSD) and `placement`, how the designs were placed (`mode`: `interface`, `whole` or `core` as chosen; `fit`: the name the frame uses for it; `iface_full`, `iface_decay`, `iface_floor`, `iface_cutoff`, `iface_min_n`, `near_weight`, `report_weight`, `warn_rmsd`) |
| Counts | `counts.{designs, accepted, rejected, families, runs, inspected}` | headline counts of the export |
| Validation | `validation.{counts_match_csv, mismatches, metric_agreement}` | what the build checked against the sources (section 6 of [export.md](export.md)); a mismatch is listed, never hidden. Only runs in the BindCraft 1 layout can be compared with their CSVs: `counts_match_csv` is `true` or `false` when at least one run could be, and `null` when none could (a project of BindCraft 2 runs only) |
| Privacy | `privacy.{network_requests, binder_sequences_sent, design_ids_sent}` | `network_requests` is the number of reference records added to the cache during the build (0 for an offline build); the other two are always false. A binder sequence never enters the manifest |
| Other | `licences[]`, `warnings`, `notes`, `highlights` | licence notes that travel with the bundle, build warnings, fixed caveats, and a copy of the campaign highlights |

Absolute paths are cut to their last component when the manifest is built (`export.portable_paths`): `sources[].path`, `runs[].source_dir`, `runs[].settings_raw` and `tool.command` hold file and folder names only, so a bundle can be handed on. The manifest still records the command line, the platform and the names of your inputs: look at it before sharing a bundle outside your group.

Read by: every page loads it (the overview shows the build record); tools read the rest.

## 9. Review events

`reviews/<reviewer>.jsonl` holds one JSON object per line, append-only and never edited. The pages write the events through one store (`BS.decisions` in `viewer/js/bs.js`): it keeps the log in the browser's storage per bundle (in memory when the browser blocks storage), and **Save review file** downloads `<reviewer>.jsonl`, which the user moves into the bundle's `reviews/` folder (a page cannot write there itself). **Load review file** imports a file by union of event ids.

| Field | Meaning |
|---|---|
| `v` | 1 (required) |
| `type` | `decision`, the only type |
| `id` | `ev` plus 8 to 32 lowercase hex digits from a hash of reviewer, unit, design, decision, time and a per-session counter; unique across reviewers |
| `reviewer`, `ts` | the name the reviewer chose (free text, at most 80 characters) and the UTC time |
| `unit`, `design`, `run` | `family` or `design`; the family (`bb`) or design id; the run id, null when it spans runs |
| `decision` | `keep`, `maybe`, `reject` or `clear` |
| `sequences` | design ids put on the order list (a `keep` on a family: the chosen sequences) |
| `reasons` | reason keys, from `profile.reasons` (pairs of key and label) or the board's default list: `wrong site`, `small interface`, `floppy`, `sticky`, `low confidence`, `liability`, `duplicate` |
| `learning`, `note`, `bundle`, `app`, `supersedes` | whether the board's ranking was learning from decisions at that moment, a free comment, the `bundle.id` the reviewer was looking at, the writing tool, and the id of the event this one restores |

The current decision of a reviewer on an item is the event with the latest `ts` among the events with the same `reviewer`, `unit` and `design` (a tie goes to the later line). Undo appends the event that restores the earlier state, a `clear` when there was none; a `clear` event carries no sequences. Merging two reviewers is the union of events by `id`; events with another `bundle` are kept and counted as foreign.

The query page also saves and loads rule sets as JSON with `schema: "binderscope.ruleset/1"` ([viewer_query.md](viewer_query.md)). That format has no JSON Schema and the validator does not check it.

## 10. Rules a schema cannot express

`scripts/validate_schema.py` checks the following after the JSON Schema pass. A message reads `<json path>: <what is wrong>`. Errors mean a page would break or show a wrong thing; warnings mean the data is odd but renders.

**Target.** `resi` ascending; `resn`, `ss`, `rsa`, `dom`, `orig_resi` aligned with `resi`; `ca` has `3n` numbers; the arrays of `atoms` agree and `atoms.res` stays below `n`; `hotspots`, `epitope`, `glycan` and `sites.A1.residues` lie inside `resi`; every glycan has a `glycan_xyz` anchor; `sites.A1.normal` and `membrane.axis` are unit vectors; `orig = reference number + input_offset`; `unresolved` and `rest.ranges` do not overlap `resi`; `hotspots` equals the union of `hotspots_by_run` (`epitope` should equal its union: a warning); `glycan_atoms` have equal-length `xyz` and `el`; `olig.neigh` arrays have a multiple of 3 numbers; the flags `olig` and `glycan` need their blocks (a false flag with data present is a warning).

**Runs.** Run ids are unique; `resi_ranges` are ascending, disjoint and each `first <= last`; `removed` equals the gaps between the ranges; `first`, `last` and `n_resi` agree with them; `cut_points` are ascending, present in the construct and include both flanks of every removed stretch; hotspots and epitope lie inside the run's own residues; `target.resi` is the union of the runs' constructs; funnel keys are unique and `n` is null exactly when the status is `unknown`; `traj_basis: attempted` needs `n_traj`; `multi_run` needs at least two runs (several runs with the flag off is a warning); `n_designs.accepted` and `n_designs.rejected_with_structure` equal the number of listed designs of that status.

**Designs.** Ids are unique; `len` agrees with `seq`, `ca` and `pl`; `bc` is below `len`; `tc` and `hot` are ascending, `tc` lies inside `resi` and inside the design's own run, `hot` lies inside `tc` and inside the run's hotspots; `hot_of` equals the run's hotspot count; the mode is defined in `modes`; the run id exists; `glycan` keys are sites of the target; `liab` positions are within `len`; a backbone belongs to one run; `fam` should equal `bb` (warning); `nm` only on a rejected design (a rejected design without `nm` or `fail` is a warning); `outcome` only with `caps.outcomes`; `traj_basis: none` means every `traj` is 0; modes list residues inside `resi` and a `site` that exists.

**Capabilities against data.** A flag that promises a metric needs at least one design that carries it (a false flag with data is a warning); `rejected` needs rejected designs and rejected designs need `rejected`; `weights` needs `wt`; `has_fold` needs `fold`; `has_traj` needs a design with `traj` above 0; `has_pae` needs a PAE matrix of the right size and a PAE matrix needs the flag; `olig` needs `olig.neigh` in both blobs and an `octx` block per inspected design; `rest_ctx` needs a `ctx` block per inspected design (an empty `rest.traces` in the inspector blob is a warning).

**Fold and diversity.** `fold.ids` are unique designs, the base64 matrix has `n * n` bytes, is symmetric and has a diagonal of 100 (a different diagonal is a warning). Cluster sizes agree with the listed families, a family sits in one cluster, `S` and `n` agree with the clusters, and the families belong to the run; a pooled block with `estimates_valid` true needs equal epochs, and `advice` needs `estimates_valid`.

**Inspector.** Arrays agree with `n` and `target_resi`; `target_pdb` has a CA record of chain A per `target_resi` entry; `order` lists existing tags without duplicates; contact, hydrogen-bond, salt-bridge, buried-polar and patch residues are in range; `pdb_b` has chain B residues 1 to `n` with one CA each, residue names equal to `seq` and B-factors from 0 to 100, and `plddt_res` agrees with them within half a unit; PAE size agrees with `pae_n`; every pose and seed rotation is a proper rotation; `poses` is not empty; `sibs` should list the design itself. Cross rules against the campaign: the same `target_resi`, `hotspots`, `epitope` and `glycan`; the same sequence and run per design; warnings when contact sets, `dsasa` or CA positions (more than 0.2 Å apart) differ; the same profile in both blobs.

**Homolog.** Every aligned sequence has one letter per entry of `resis`; `resis` equals the campaign `target.resi`; every species of the profile resolves to an ortholog unless it is optional or has a fallback; the focus paralog is present; `consurf.grade` has one entry per residue; `n_orth` agrees with the entries not marked `extra` (warning).

**Profile.** Text templates have balanced braces and tokens the key registry knows, and `{@path}` lookups resolve; frame segments ascend and do not overlap; species keys are unique and every referenced key exists; a flag needs the profile section that makes it usable (`olig` needs `checks.context.partner`, `rest_ctx` needs `checks.context.rest`, `species_panel` needs `specificity.species`, `paralog_panel` needs `specificity.paralogs.focus`, `glycan` needs `frame.glycan_sites`, `flag_region` and `footprints` need their sections).

**Manifest, bundle and events.** Run ids and file paths are unique, every source names an existing run, `caps` equals `campaign.profile.caps`, `data/profile.json` equals `campaign.profile`, the run ids of the manifest equal the campaign's; for a bundle every file in `bundle.files` exists with the recorded size and sha256, every page and data file named in the manifest exists, and an inspected design without a structure file is a warning; event ids are unique and a `clear` event carries no sequences.

## 11. Validation

At build time `export.validate_blobs` runs the validator on the campaign, inspect, homolog and manifest blobs (the profile is checked inside the campaign blob), and any error stops the build with `ExportError`; the bundle writer runs it again on the finished folder, where an error raises `BundleError`. Warnings are kept in the build's warning list. By hand:

```
python scripts/validate_schema.py --bundle bundles/my-target
python scripts/validate_schema.py --kind campaign path/to/campaign.json
```

`--kind` is one of `campaign`, `inspect`, `homolog`, `manifest`, `profile` and `review_event` (a JSONL file for the last); `--no-hashes` skips the sha256 check of a bundle's files, and `-q` hides warnings. The exit status is 0 without errors (warnings may remain) and 1 with errors. The checker needs the `jsonschema` package, which `pyproject.toml` lists among the dependencies.

As a library: load the script as a module (it is a script, not a package module), then call `check_bundle(dir)`, `check_blobs(campaign, inspect, homolog)`, `check_manifest`, `check_profile` or `check_review_events`; each returns a report with `.errors` and `.warnings` (lists of strings).

## 12. Hard-to-produce and optional fields

The hardest fields to produce are, in order: the placement of every design on the reference chain and everything derived from it (`ca`, `pdb_b`, `place_rmsd`); the context metrics (other subunits, removed regions, cut ends and coverage, which need solvent accessibility with extra atoms, sugars and deleted stretches); the glycan atoms and anchors, found from the covalent carbohydrate links of the reference file; the funnel with its unknowns; per-run diversity, whose generation order exists only as the row order of `trajectory_stats.csv`; near-miss records, which need the rejected structures; the ortholog and paralog alignments over the union construct; and the agreement of the recomputed interface metrics with BindCraft's columns, which is an approximation for some quantities ([interface.md](interface.md)).

The pages do not guess. A page that lacks a field hides the feature that needs it, and the capability flag tells the page in advance. What each page does without the data is in [viewer_board.md](viewer_board.md), [viewer_inspector.md](viewer_inspector.md), [viewer_map.md](viewer_map.md) and [viewer_query.md](viewer_query.md).

| Data | Flag | Without it |
|---|---|---|
| per-design geometry in the reference frame (`ca`, `pdb_b`, `view`) | none: required | nothing can be drawn; a design that cannot be placed stops the build instead of being left out |
| other subunits (`olig`, `octx`, `olig_*` metrics) | `olig` | no partner part of the Context check (the check of overlap with the other subunits, the removed regions and the cut ends), no partner traces |
| removed regions (`rest`, `ctx`, `rest_*`, `cut_n`, `cov_n`) | `rest_ctx` | no removed-region, cut-end or coverage part of the Context check. With no reference structure the metrics are `null` (never 0), `design.ctx` is `null`, and `profile.caps_off` says `no_reference`; a reference that holds nothing gives `reference_lacks`. A false flag alone cannot tell the two apart, which is why `caps_off` exists ([profile_spec.md](profile_spec.md), section 4) |
| membrane plane (`mem_excess`) | `membrane` | no membrane check or plane; null when the target topology is unknown |
| glycans | `glycan` | no glycan finding, rule or toggle |
| generation order (`traj` above 0) | `has_traj` | no accumulation curve, replay or forecast, and no sort by generation order |
| attempted trajectories (`runs[].n_traj`) | `traj_total` | the attempted-trajectory row reads "unknown"; yield and forecasts count passed trajectories |
| `fold` | `has_fold` | the fold basis of the clustering falls back to a stand-in distance from `design.sc` and the helix fraction, which is approximate |
| rejected designs with a structure | `rejected` | no rejected toggle, near-miss rescue or rejected sibling rows |
| ortholog and paralog panel | `species_panel`, `paralog_panel` | no specificity features |
| PAE, ipSAE, a co-folding re-score, repeat seeds | `has_pae`, `ipsae`, `min_ipae`, `complex_rmsd`, `models`, `has_seeds`, `seed_spread`, `n_agree` | the matching meters, columns and rules disappear |
| lab outcomes, sampling weights | `outcomes`, `weights` | no reveal switch, lab pills or lab statistics; a blob that carries `outcome` with the flag off fails validation, because the reveal switch would leak it |
| several runs | `multi_run` | no run filter or run column; with several runs every estimate is per run |

`pl` (per-residue pLDDT in the campaign record) is optional in the schema, and the exporter always writes it. The board draws a synthetic profile from `m.plddt` when it is absent instead of hiding the colouring, so a hand-made blob should carry `pl`.

## 13. Changing the contract

1. Edit the schema in `schema/`. Add the field with a `description`, and make it `required` only if a page cannot draw without it. Keep additive changes inside the major version; a change that makes an old reader wrong bumps the `schema` constant in the schema, the exporter and the validator.
2. Produce the field in `export.py` from the module that owns it. Convert numbering at the one conversion point (the exporter), round as in section 3.3, and write `null` rather than a guess.
3. If a capability flag guards the field, add it to `CAP_FLAGS` in `export.py`, to the `caps` of the profile schema and to `CAP_FLAGS` in `viewer/js/bs.js`, and say in [profile_spec.md](profile_spec.md) what it promises.
4. If a page reads the field, handle its absence there and document it in the page's document.
5. If the rule cannot be written in the schema, add it to `scripts/validate_schema.py` and a test under `tests/`.
6. Update this page and `schema/README.md`.

## See also

[schema/README.md](schema/README.md) · [export.md](export.md) · [architecture.md](architecture.md) · [profile_spec.md](profile_spec.md) · [target_frame.md](target_frame.md) · [interface.md](interface.md) · [families.md](families.md) · [modes.md](modes.md) · [specificity.md](specificity.md) · [viewer.md](viewer.md) · [cli.md](cli.md) · [GUIDE.md](GUIDE.md)
