# Profile specification

A profile is a JSON file that holds everything target-specific that Binderscope shows or decides: the names and numbering of the target, the frame strip, the specificity panel, the capability flags that say which data a project carries, how the checks are read, and the sentences the pages use. This document is for a developer or an advanced user who wants to describe a new target. The schema is [schema/profile.schema.json](schema/profile.schema.json) and the registry of text keys is [schema/profile.keys.json](schema/profile.keys.json). Every example below is invented: "Example protein A", "species 1" and the accessions refer to no real target.

## 1. Where a profile lives and how it is used

A project has one profile: a JSON file anywhere on disk, or the built-in `default` profile (`src/binder_review/profiles/default.profile.json`; `binder_review.pipeline.profiles_dir()` returns the folder), which the build uses when none is given. `binder-review import` takes it with `--profile` ([cli.md](cli.md)); in the app it is the **Profile file** field under Options on the import page ([app.md](app.md)). The default profile names no target and no species (its target is a placeholder), so copy it and edit the copy.

What happens to it, in order:

1. **Load.** `pipeline.load_profile` reads the file as JSON. Nothing is validated yet.
2. **Accession.** The UniProt accession used for reference records is `--uniprot`, else `target.uniprot`, else none. Without one no UniProt, PDBe or AlphaFold record is read: the frame strip has no domains, there is no membrane plane and no specificity panel ([target_frame.md](target_frame.md), [reference_data.md](reference_data.md)).
3. **Specificity stage.** The panel is built from the `specificity` section (section 7). Changing that section invalidates the cached stage.
4. **Completion.** `export.build_profile` completes the profile from the data (table below).
5. **Validation.** The exporter validates every blob, the completed profile included, with the rules of `scripts/validate_schema.py`. An error stops the build with exit code 1 and a list of messages; no bundle is written from an invalid profile.
6. **Copies.** The completed profile is stored inside `campaign.json` and `inspect.json` (key `profile`), as `export/profile.json`, and in the bundle as `data/profile.json` and `data/profile.js` (the global `PROFILE`). The copies must be identical, and the bundle builder and the validator check it. The manifest records the profile `id`, the SHA-256 of the profile, a copy of `caps` and the check settings under `thresholds`.

| Written by the exporter | From |
|---|---|
| `id`, `kind`, `schema` | `custom`, `run` and `binderscope.profile/1` when the profile has none |
| `target.short`, `target.name` | when missing: `target.gene`, else the project name (`name` falls back to `short`) |
| `checks.context` | every part (`partner`, `rest`, `cut`, `cover`, `near`) and every limit the profile does not give, taken from the frame's defaults (section 5) |
| `target.construct.label` | `construct` when missing |
| `caps`, every flag | the data (section 4); a profile can only switch a flag off |
| `target.uniprot` | the accession of step 2 |
| `target.name`, `short`, `gene`, `organism` and `specificity.subject` | only when the profile `id` is `default` and an accession is known: the UniProt record. A profile with any other `id` keeps its own words |
| `target.construct.first`, `last` | the union of the constructs of the runs, in UniProt numbering (`label` stays yours) |
| `target.length`, `target.reference.pdb`, `chain`, `assembly`, `target.numbering.input_offset` | the frame: the reference structure and the numbering found by sequence |
| `target.reference.label`, `target.numbering.input_label` | written only when the profile has none |
| `target.reference.given`, `kind`, `treated_as`, `method`, `resolution`, `evidence` | the frame: whether a full-length reference was given (`given` false: `pdb` is empty and the construct is the frame) and what the exporter concluded from the reference file, with the evidence (section 5, Context standing) |
| `checks.context.hard`, `hard_setting`, `hard_basis` | whether the Context check decides a verdict, the setting that led to it (`auto`, `hard`, `advisory`) and the reason in words (section 5, Context standing). The profile's own `checks.context.hard` is read as the setting and replaced by the resolved boolean |
| `caps_off` | for each context flag (`olig`, `rest_ctx`, `glycan`, `membrane`) that is false: `why` and `note` (section 4, Why a context flag is off). Absent when none is off |
| `target.label`, `target.span_label` | `<short> <first>-<last>` and `<short> <first>–<last>`, where `short` is `target.short`, else `target.gene`, else the project name |
| `frame` | derived from the reference structure and the UniProt record when the profile has none |
| `version` | 1 when absent |

Every other key is copied unchanged. A profile that leaves out `id`, the target names, `construct.label` or `checks.context` is completed with these defaults: the build does not stop for them.

### How the pages read it

Each page takes the profile that sits in the blob it loads (`viewer/js/bs.js`), merges it over a neutral profile and offers four helpers on `PF`:

| Call | Result |
|---|---|
| `PF.cap('flag')` | true only when the flag is `true` in `profile.caps`; an absent flag is false. The pages call it as `PF.cap('flag')`, or through a one-line wrapper named `cap` or `CAP` |
| `PF.get('a.b.c', fallback)` | the value at a dotted path, or `fallback` when the path is absent or null |
| `PF.has('key')` | whether `profile.text` defines the key (used to choose between variants of a text) |
| `PF.t('key', tokens, fallback)` | the text template of the key, rendered with the tokens the page supplies (section 3); the page's own `fallback` wording when the profile has no such key |

`PF.missing()` lists the text keys that the pages asked for and the profile does not define; type it in the browser console of a bundle page. Every setting and nearly every text has a default inside the page, so a profile states only what differs. Pages never write the profile.

The bundle id is a hash of the input files and of the completed profile, and the browser keeps decisions per bundle id. A rebuild with a changed profile therefore starts with no decisions in the browser: save a review file first and load it afterwards ([viewer_board.md](viewer_board.md)).

## 2. Structure

Objects accept keys that are not listed, so a misspelt key is not an error: it is ignored. The limits are `caps` (every value is a boolean), `text` (every value is a string) and `tips` (every entry is a list of strings).

| Section | Req. | Type | Holds | Read by |
|---|---|---|---|---|
| `schema` | yes | string | the constant `binderscope.profile/1` | validator |
| `id` | yes | string | slug, `^[a-z0-9][a-z0-9_-]*$` | manifest; names of exported rule sets and CSV files; the id `default` is special (section 1) |
| `kind` | yes | `run`, `synthetic`, `dataset` | what the data are (below) | pages |
| `version` | | integer, at least 1 | version of your profile; recorded only, the exporter writes 1 when absent | |
| `brand` | | object | `name` (the word next to the logo, default `Binderscope`) and `titles.<page>` (tab titles for `index`, `board`, `inspector`, `map`, `query`) | page head and header |
| `badge` | | object | `class` (`mock`: dashed grey, `real` or `run`: green) and `text`; both are required when the badge is given | header; tooltip from the text key `badge.title.<page>` |
| `credit` | | object or null | `html` (a footer line on every page) and `text` (plain-text twin, not read in this release); null for none | every page |
| `target` | yes | object | the target's identity and numbering (below) | all pages |
| `frame` | | object | the frame strip (section 8) | Inspector; the exporter derives it when absent |
| `membrane` | | object | plane settings (section 8) | Inspector, Map, Query |
| `view` | | object | camera and score settings (section 8) | Inspector, Query, Map, Board |
| `specificity` | | object | species, paralogs and specificity rules (section 7) | build, Inspector, Map, Board, Query |
| `predictors` | | object | names of the models (section 9) | Inspector, Query |
| `caps` | yes | object of booleans | capability flags (section 4); `{}` in a source profile | every page |
| `checks` | | object | thresholds and settings of the checks (section 5) | every page; the exporter reads `checks.context` |
| `rules` | | object | Query rule groups, overrides and extra rules (section 6) | Query |
| `nl` | | object | Ask box examples and words (section 6) | Query |
| `modes` | | object | `notes` per binding mode (section 9) | Map |
| `reasons` | | array of `[key, label]`, at most nine | reason chips of the decision panel (section 9) | Board |
| `flag_region` | | object or null | a region whose contacts are counted apart (section 9) | Board, Inspector, Map |
| `footprints` | | object | known-complex footprints (section 9) | Inspector |
| `inspector` | | object | labels and order of the Inspector's designs (section 9) | Inspector |
| `import` | | object | content of the Board's import dialog (section 9) | Board |
| `tips` | | object of string lists | "What to try" tips per page (section 9) | Board, Inspector, Map, Query |
| `text` | | object of strings | sentence templates (section 3) | every page |

`kind` decides how much of the interface a page offers. Use `run` for BindCraft output. With `run`, the Query page offers the pool switch, near-miss rescue, export and saved rule sets and its lists can be operated from the keyboard, the Board does not offer the simulated reviewer unless `board.simulate` is true, and several labels use the wording of a tool run. `dataset` and `synthetic` are for blobs from other sources: they give a reduced Query page, a simulated reviewer on the Board and different wording (the Board funnel is headed "Dataset funnel" for `dataset`).

**`target`.**

| Key | Req. | Type | Meaning |
|---|---|---|---|
| `name` | yes | string | full name of the target: the heading of the Overview and the tooltip of the short name in every header |
| `short` | yes | string | short name used in titles and in sentences (`{@target.short}`) |
| `gene`, `organism` | | string | gene symbol (the fallback for `short`) and organism name; recorded in the manifest |
| `uniprot` | | string | accession; empty or absent for none (`--uniprot` wins when both are given) |
| `length` | | integer | length of the full-length protein; written by the exporter |
| `reference` | | object | `pdb`, `chain`, `assembly` (a string or null) written by the exporter, and `label` (yours, or written when absent) |
| `construct` | | object | `first` and `last` (integers, written by the exporter) and `label`, a short noun phrase for breadcrumbs and sentences (required when the block is given) |
| `numbering` | | object or null | `input_offset` (integer, written) and `input_label`: how the numbering of the file given to BindCraft relates to UniProt |
| `label`, `span_label` | | string | written by the exporter: the target label of exported rule sets and the subtitle of the Inspector's structure panel. The default profile carries the placeholder `TGT` for both, so that `{@target.label}` and `{@target.span_label}` resolve when a profile is validated alone; the exporter replaces them |

## 3. Text templates and tokens

`profile.text` is a flat map from a dotted key to a template string. The key names a place on a page (`crumb.board.run`, `finding.gly.text`, `spec.intro`). The registry lists, for every key, the page, the tokens that the page supplies and what the text is for; the catalogue at the end of this page is generated from it.

The grammar is implemented by `BS.render` in `viewer/js/bs.js` and by `viewer_build.render_template` in Python:

- `{name}` is replaced by the token `name` that the page supplies when it renders the key. The page formats the value: a number with the decimals that suit it, a list as a comma-separated string.
- `{name|n}` formats a number with thousands separators (`1,234`). `n` is the only format: another format name is ignored, and a value that is not a number is left as it is.
- `{@path}` looks `path` up in the profile (`{@target.short}`, `{@specificity.panel_noun}`). A path that does not resolve renders as an empty string, and the validator reports it as an error.
- A token the page does not supply is left as written, so a misspelt token is visible on the page. The validator reports a token that is not in the registry's list for that key, and unbalanced braces.
- There is no escaping and no conditional. Wording that depends on a count is built by the page into a token (`{count_phrase}`) or by choosing between keys (`.some` and `.none`).
- Some texts are inserted into the page as HTML (findings, notes, tips, `spec.intro`) and others as plain text (breadcrumbs, tooltips). Keep `<`, `>` and `&` out of a text unless the catalogue says it is HTML.

A key the profile does not define falls back to the wording inside the page, rendered with the same grammar, so a default that refers to the profile (`inspector.struct_sub` reads `target.span_label`) resolves like any other. The default profile defines `inspector.struct_sub`. The exporter writes `target.span_label`, `target.label`, `target.length` and the other numbers of the target, so the validator reports a reference to one of them as unresolved when it checks the source profile on its own. The default profile therefore carries the placeholder `TGT` for `target.label` and `target.span_label`; the exporter replaces it.

Common tokens (the registry lists the exact set per key):

| Token | Meaning |
|---|---|
| `run_source`, `run_name`, `run_license`, `run_credit` | fields of the campaign `run` block; empty when the block has none |
| `n_runs`, `n_designs`, `n_families`, `n_sample`, `n_total`, `n_accepted`, `n_rejected` | counts for the runs or the current selection |
| `n_orth` | number of orthologs behind the conservation grades |
| `hot_of`, `nh`, `hot_list` | hotspots of the design's own run, hotspots it touches, and those as a list such as `A45, G60` |
| `ipsae`, `iptm`, `ipae`, `min_ipae`, `rmsd`, `n3`, `mem` and similar | metric values, formatted by the page |
| `label`, `phrase`, `subject`, `gene`, `name` | species or paralog label, in-sentence name and gene, taken from `specificity` |

An invented override, for a target that carries N-glycans:

```json
"text": {
  "finding.gly.text": "{@target.short} carries N-glycans at {glycan_list}; a bulky sugar would sit in the way."
}
```

## 4. Capabilities

A flag says whether the data behind a feature are present. The exporter writes all 38 flags, and a flag is true exactly when its data are there. A profile may set a flag to `false` to hide a feature the data would allow; it cannot set one to `true` for data that are missing, because the exporter writes `false` for them. The exception is `consurf`, which is true when the build was asked for the overlay (`--consurf`) and the overlay exists. In the completed profile an absent flag reads as false.

A false flag removes the feature from the pages. It never fails a design: a check whose metric is missing is not judged ([viewer.md](viewer.md)). The 16 flags marked "never" have no source in BindCraft run folders, so they are false in every bundle built from them; the pages implement the features for blobs that carry the data ([data_contract.md](data_contract.md)).

| Flag | Set to true when | What it adds (page) |
|---|---|---|
| `outcomes` | never (needs `design.outcome`, a lab result) | **Show lab results**, lab pills and the lab panel (Board, Inspector, Map, Query) |
| `weights` | never (needs `design.wt`, sampling weights) | weighted lab shares (Map, Query) |
| `olig` | the reference has other subunits and designs carry `m.olig_min` | the partner part of the Context check, partner meter, traces and toggle (Inspector), partner rows and flags (Board, Map), the Context stage of the Map funnel |
| `rest_ctx` | designs carry `m.rest_min` and the construct leaves out part of the reference chain | the removed-region, cut-end and coverage parts of the Context check, their meters, findings and layers |
| `flag_region` | never (needs a per-design count of contacts in the flagged region) | contacts in the region named by `flag_region` counted apart (Board, Inspector, Map) |
| `footprints` | never (needs `target.foot`, `design.fpov`) | known-complex footprints: the footprint epitope findings, known-binder traces, a Board feature |
| `has_pae` | never (BindCraft writes no PAE) | the PAE panel (Inspector) |
| `has_traj` | some design has `traj` of 1 or more (a generation order) | Generation-order sort, accumulation curve, replay and forecasts (Board) |
| `traj_total` | every run has `n_traj` (attempted trajectories) | no page reads the flag; the Board reads `runs[].n_traj` and says "unknown" where it is null. The validator warns when the flag is true and a run has no `n_traj` |
| `has_fold` | the campaign has a fold block (`campaign.fold`, a TM-score matrix) | the Backbone basis of the diversity clustering uses it; without it a coarse stand-in is used (Board) |
| `families` | some backbone has more than one design | tiles per family, the Unit switch, the Siblings tab; without it one tile per design and a Similar tab (Board, Inspector, Overview, Map, Query) |
| `has_boltz` | never (needs a co-folding model other than the design model) | co-fold wording of the pose finding and model label (Inspector) |
| `models` | never (needs more than one predictor per design) | pose overlay buttons and the re-score findings (Inspector) |
| `has_seeds` | never | seed overlay (Inspector) |
| `ddg_scan` | never | alanine-scan track and colour; buried area stands in without it (Inspector) |
| `species_panel` | the homolog blob has orthologs | Specificity tab, Conservation colour, species columns, specificity findings, rules and the Board's Specificity row |
| `paralog_panel` | the homolog blob has paralogs | paralog bars, focus-paralog column, paralog findings and rules |
| `decoy_cofold` | never | decoy co-fold section of the Specificity tab (Inspector) |
| `consurf` | `--consurf` was given and the overlay exists | ConSurf-DB checkbox in the Specificity tab (Inspector) |
| `glycan` | the target has glycan sites inside the construct | glycan finding, viewer toggle, Query rule `gly` |
| `membrane` | designs carry `m.mem_excess` (a membrane plane is known) | membrane finding, the plane in the viewers, strip shading, Query rule `mem`, a Board feature |
| `multi_run` | more than one run | run filter, run chips and columns, per-run estimates |
| `rejected` | some design has status `rejected` (a rejected design with a structure) | near-miss rescue, the rejected pool and rows |
| `reviews` | always | decisions persist in the browser and can be saved and loaded as review files; without it they live in memory only |
| `ipsae` | never (ipSAE of a co-folding model) | headline score, meter, Query rules, and the ipSAE basis of the Interface check |
| `min_ipae` | never | min iPAE column, meter and rule |
| `complex_rmsd` | never (re-score pose against the design) | the Pose check on that metric, the Pose stage of the Map funnel, the `pose` mark on tiles |
| `dg` | designs carry BindCraft's `dG` | ΔG column, meter, rule and Chemistry check part |
| `sc` | designs carry shape complementarity | shape complementarity column, meter, rule and Chemistry check part |
| `iptm_b` | designs carry `Average_i_pTM` | the headline score when ipSAE is absent, and the limit of the BindCraft basis of the Interface check |
| `ipae_norm` | designs carry `Average_i_pAE` (PAE divided by 31) | the same, for interface PAE |
| `surf_hyd` | designs carry `Surface_Hydrophobicity` | no page reads the flag; the Inspector meter and a Query rule appear when designs carry the value |
| `hotspot_rmsd` | designs carry `Hotspot_RMSD` | Board compare row and tile line, Inspector meter and, with `checks.findings.hotspot_rmsd_warn`, a pose warning |
| `binder_rmsd` | designs carry `Binder_RMSD` | the Pose check when no complex RMSD exists, a Board feature |
| `seed_spread` | never | a Board feature |
| `n_agree` | never | a Board feature |
| `esm_plddt` | never | monomer pLDDT, a Board feature |
| `ciplddt` | designs carry `i_pLDDT` | interface pLDDT column and Board feature |

`real` is a retired flag: new profiles must not write it.

**Why a context flag is off.** A false flag in `caps` does not say whether a check did not run or ran on nothing, and a reader deciding whether to trust a campaign needs to know which. `profile.caps_off` (copied to `manifest.caps_off`) holds, for each of `olig`, `rest_ctx`, `glycan` and `membrane` that is false, `{"why": ..., "note": ...}`:

| `why` | Meaning | Pages |
|---|---|---|
| `no_reference` | no full-length reference structure was given (`--reference-cif` left out), so nothing outside the construct was ever known and the check could not run | the Overview strikes the chip and says no design was tested; the Board's Context row reads *not checked*; the Inspector shows a *Target checks, not run* card |
| `reference_lacks` | a reference was given and holds nothing for the check: no other chain of the target, nothing outside the construct, no sugar site inside it, no transmembrane region | the same places, with the wording that the check ran on nothing, which is not a pass |
| `profile` | the profile switched the flag off | the chip's tooltip |

`note` is the specific reason. In both first cases the metrics are absent in the data, never zero, and a design with no context finding must not be read as one that was found clear: `C.contextOff()` in `viewer/js/common.js` is the one place the pages ask, and it returns nothing when the Context check ran.

The validator ties flags to the data and to the profile: a metric flag that is true needs at least one design with the metric; `outcomes` false forbids `outcome` on designs, `rejected` false forbids rejected designs and `has_pae` false forbids PAE matrices; `multi_run` needs two runs; `olig` needs `checks.context.partner`, `rest_ctx` needs `checks.context.rest`, `species_panel` needs `specificity.species`, `paralog_panel` needs `specificity.paralogs.focus`, `glycan` needs `frame.glycan_sites`, `flag_region` needs `flag_region` and `footprints` needs `footprints`.

## 5. Checks

`profile.checks` says how the checks and findings read the data. The page code keeps the logic; the numbers and the basis come from here. [viewer.md](viewer.md) describes the checks and lists the built-in presets. Keep the `checks.context` block of the default profile: the exporter copies it into the manifest (`thresholds`) and reads it for its highlights, and a profile without `checks` or without `checks.context` stops the build with a `KeyError`.

**Verdicts.** `Common.evalChecks` judges every design with up to seven checks against the threshold preset in force. The Context check is **advisory by default** (below), the other three of Fold, Interface and Pose are hard. Any failed hard check gives **NO PASS**; otherwise the verdict is **REVIEW** when a soft check fails or when two or more of Chemistry, Epitope, Sequence, Pose and Context warn, and **PASS** when neither holds. A check whose metric is missing is not judged. What the profile can change for each check:

| Check | Kind | Rule in short | The profile sets |
|---|---|---|---|
| Fold | hard | binder pLDDT and pTM at least the preset | `plddt`, `ptm` of the preset |
| Interface | hard | ipSAE and min iPAE, or on the BindCraft basis i_pTM and normalised i_pAE, against the preset | the preset; `interface.basis` |
| Pose | hard | complex RMSD at most the preset (fail); without it, binder RMSD at most the preset (warn only) | `complex_rmsd`, `binder_rmsd` of the preset |
| Chemistry | soft | failed parts among shape complementarity, interface energy, unsatisfied polars and hydrophobic patch: none pass, one or two warn, more fail | `sc`, `dg`, `n_unsat`, `hphob_patch` of the preset; `physics.*` |
| Epitope | soft | hotspots touched at least `min(preset.hotspots, hotspots of the design's own run)` pass, some warn, none fail | `hotspots` of the preset: the required count |
| Sequence | soft | a bad liability fails, a warning one warns | nothing |
| Context | advisory; hard on evidence or by choice | the worst of the parts below | `context.*`, `context.hard` |

**Context standing.** The Context check places the binder against the reference as one static model. Against an experimental structure that is a measurement; against a predicted model it is as good as the prediction, and a region that a predictor cannot place (a flexible linker has no fixed position) lands at an arbitrary one. On one real VHH campaign every design collided with such a region and the check alone turned 18 BindCraft-accepted designs to NO PASS, which also hid the sequence liabilities that were real. So the check is a soft one unless the reference is experimental: its findings keep appearing with their reasons and can lead to REVIEW through the soft-check rule, but not to NO PASS on their own, and the Board and the Overview say so where the verdict is shown.

`checks.context.hard` is `auto` (the default; absent means `auto`), `hard` or `advisory` (`true` and `false` are read as hard and advisory), and `--context-check` overrides it. With `auto` the exporter classifies the reference itself (`target_frame.classify_reference`), from the file, so it holds on the command line with no web service involved:

- **experimental**: the file records an experimental method (`_exptl.method` of an mmCIF, `EXPDTA` of a PDB file: X-ray, electron microscopy, NMR and so on) and no model record. The method and the resolution are the evidence;
- **predicted**: the file has a model record (the `_ma_` categories that AlphaFold DB and other model archives write, naming a pLDDT), a theoretical-model method, or, when it names no method at all, one B-factor per residue between 0 and 100, which is how a pLDDT is stored. A file that records both a method and a model is read as predicted;
- **unknown**: none of these. It is treated as predicted, the safer reading.

`hard` follows only from an experimental reference. What was concluded is in `profile.target.reference` (`kind`, `treated_as`, `method`, `resolution`, `evidence`), in `profile.checks.context` (`hard`, `hard_setting`, `hard_basis`) and in the manifest (`target.reference_class`, `target.context_check`). Without a reference the check is not run at all, and `hard_basis` says so: absent, not advisory, because nothing was examined.

**Context parts.** Each part uses a metric the build stored for the design and is skipped when the metric is missing.

| Part | Needs | Fails | Warns |
|---|---|---|---|
| partner | `olig` | `olig_n3 >= partner.fail_n3` | any atom within 3 Å, `olig_min < partner.warn_min`, `olig_n >= partner.warn_buried`, or, with `flag_region`, contacts in the flagged region `>= flag.warn_n` |
| rest | `rest_ctx` | `rest_n3 >= rest.fail_n3` | `rest_n3 > 0` or `rest_min < rest.warn_min` |
| cut | `rest_ctx` | never | `cut_n >= cut.warn_n` |
| cover | `rest_ctx` | never | `cov_n >= cover.warn_n` |

`olig_n3` counts binder atoms within 3 Å of the other subunit and `rest_n3` binder atoms within 3 Å of the rest of the chain; `cut_n` counts contacted residues near a cut end and `cov_n` contacted residues that the removed regions would bury ([target_frame.md](target_frame.md)).

**Presets.** A preset is a set of limits with the keys `plddt`, `ptm`, `ipsae`, `min_ipae`, `complex_rmsd`, `sc`, `dg`, `n_unsat`, `hphob_patch`, `hotspots`, `binder_rmsd`, `iptm_b` and `ipae_norm`. `lit`, `relaxed` and `strict` are built in. `checks.preset_default` picks the one selected at load (an unknown name falls back to `lit`) and the page lets the reader change it. A preset of your own, `checks.presets.<key>`, is `{"name": ..., "v": {...}}`; the keys it leaves out take the values of `lit`, and keys the pages have no rule for are ignored. An invented preset that follows the filters of the runs:

```json
"checks": {
  "preset_default": "runs",
  "presets": { "runs": { "name": "As the runs filtered", "v": { "plddt": 0.8, "iptm_b": 0.5, "ipae_norm": 0.35, "hotspots": 2 } } }
}
```

**Other settings.** The fallback is what a page uses when the key is absent; the default profile writes explicit values.

| Key | Meaning | Fallback |
|---|---|---|
| `interface.basis` | `auto` uses the BindCraft basis when `ipsae` is off and the ipSAE basis otherwise; `ipsae` and `native` force one | `auto` |
| `physics.unsat_basis` | `count` judges `n_unsat` against the preset; `per_1000_A2` judges the density per 1000 Å² of interface | `count` |
| `physics.unsat_density_max`, `unsat_density_info` | density above which the unsatisfied-polars part of Chemistry fails and the Inspector finding warns, and from which that finding is informational | 2.3, 1.75 |
| `physics.unsat_bad_gt` | unsatisfied polars above which the Inspector finding is bad | 3 |
| `physics.sc_check` | whether shape complementarity takes part in Chemistry and shows a meter | true |
| `physics.pack_needs_sc` | whether the "well-packed interface" finding also needs `sc >= preset.sc` | true |
| `context.partner` | `fail_n3`, `warn_min` (Å), `warn_buried` | 10, 4.0, 3 |
| `context.rest` | `fail_n3`, `warn_min` (Å) | 10, 4.0 |
| `context.flag.warn_n` | contacted residues in the flagged region that warn (with `flag_region`) | 3 |
| `context.cut` | `k`: residues from a cut end; `warn_n` | 5, 2 |
| `context.cover` | `min_area`: area in Å² a residue must lose to count as covered; `warn_n` | 10, 2 |
| `context.near` | bands in Å of the removed-region finding: `bad_lt`, `warn_lt`, `info_lt` | 3.0, 4.0, 8.0 |
| `findings.loops` | `info_ge`: loop share of the interface that raises the finding; `sev`: its severity | 0.4, `warn` |
| `findings.conf` | interface residues of low confidence: `plddt_below`; the finding warns from the larger of `min_n` residues and `min_frac` of the interface, and is informational below that | 75, 3, 0 |
| `findings.gly` | distance to a glycan in Å: `ok_ge` is clear, `info_ge` is informational (null: none) | 8.0, null |
| `findings.helix.info_lt` | helix share below which the helix finding is only informational (null: never) | null |
| `findings.term.free_ge` | distance in Å from the C-terminus to the target from which it counts as free | 12 |
| `findings.hotspot_rmsd_warn` | Hotspot RMSD in Å above which the pose finding warns (null: off) | null |

Two keys describe how data were computed rather than how they are judged. `context.cut.k` and `context.cover.min_area` define what `cut_n` and `cov_n` count, and the build computes them with the fixed values in `target_frame.DEFAULT_CONFIG` (`k` 4, `min_area` 10). Keep these two at the values of the default profile: another value changes the wording of the pages, not the counts. The distances behind `olig_n3`, `rest_n3` and the clash flag (3, 4 and 6 Å atom counts, 2.2 Å) are fixed in the same place. The other keys are applied to the stored counts when the pages judge a design, so they can be changed freely, and the exporter reads `fail_n3` of `partner` and `rest` for its highlights.

## 6. Query rules and the Ask box

The Query page builds its rules from a base catalogue and the profile adds to it ([viewer_query.md](viewer_query.md) lists the base rules and their ids).

| Key | Effect |
|---|---|
| `rules.groups` | order of the rule groups on the page and in the funnel. The default puts `BindCraft filters` first, because those are the thresholds the campaign was already judged by; a group that holds a rule but is not listed is appended, and an empty group is not drawn |
| `rules.overrides.<id>` | an object merged into the base rule with that id: `label`, `note`, `val`, `lo`, `hi`, `step`, `dec`, `unit`, `on` are the fields to change. A `note` may use `{n_orth}` and `{glycan_list}` |
| `rules.extra[]` | rules added by the profile (below) |
| `specificity.rules[]` | rules built from the specificity panel, same shape plus `expr` (section 7) |

A rule of `rules.extra[]` has `id` and `label` (required), `group`, `metric` (a key of `design.m`, or the expression `unsat_density`), `op` (`>=` or `<=`), `val`, `lo`, `hi`, `step` (default 0.01), `dec` (default 2), `unit`, `on` (default false), `note`, `rescue` (a title and an action for a design the rule blocks) and `nl`. It is added only when some design has a value for its metric. An invented example:

```json
"rules": {
  "extra": [{
    "id": "rest", "group": "Chain context", "label": "Atoms within 3 Å of the rest of the protein",
    "metric": "rest_n3", "op": "<=", "val": 9, "lo": 0, "hi": 200, "step": 1, "dec": 0, "on": true,
    "rescue": ["Re-run with a larger target", "Add the neighbouring domain to the target"],
    "nl": { "words": "rest of the protein|full[- ]length", "text": "At most 9 atoms within 3 Å of the rest of the protein", "val": 9 }
  }]
}
```

A numeric base rule that no design has a value for is dropped; `mem`, `gly` and `consv` are also dropped without `membrane`, `glycan` and `species_panel`.

**The Ask box matches keywords; it does not use a language model.** The text is lower-cased and tested against regular expressions in the browser, the matching rules are switched on with a value, and nothing is sent anywhere. A fixed vocabulary covers the epitope and hotspots, glycan distance, the membrane side, unsatisfied polars, hydrophobic patches, confidence, pose consistency, the C-terminus, helicity, loops, liabilities, length and expression; [viewer_query.md](viewer_query.md) lists the phrases and the values they set. The profile adds the words that name the target:

| Key | Effect |
|---|---|
| `nl.examples[]` | the example chips: `{"label": ..., "q": ...}` |
| `nl.words.epitope` | extra words that mean "on the intended epitope", added to the built-in ones (`hotspot`, `epitope`, `on-target`) |
| `nl.words.off_epitope_exclude` | a regular expression; a query that matches it is not read as "off the epitope" (default `paralog`) |
| `nl.words.glycan_named` | an object from a word to the label of a named glycan site |
| `nl` of a rule | on a rule of `rules.extra` or `specificity.rules`: `words`, a regular expression that switches the rule on; `text`, the sentence the page reports; `val`, the value it sets (default: the rule's `val`) |

## 7. Specificity

`specificity` names the species and paralogs the build compares with the target and says how the pages show the result. The method, the alignment and the grades are in [specificity.md](specificity.md); the cache is in [reference_data.md](reference_data.md).

**When a panel exists.** The build makes a panel only when the accession is known (`--uniprot` or `target.uniprot`) and the profile names species or has a `paralogs` block. Otherwise the homolog blob is empty, `species_panel` and `paralog_panel` are false and the pages hide every specificity feature.

| Key | Type | Meaning |
|---|---|---|
| `subject` (required) | string | name of the protein the conservation is about, used in sentences |
| `kind` (required) | `host`, `virus` | the Query page lists the rules of `specificity.rules` before the conservation rule for `host` and after the rules of `rules.extra` for `virus`; no other page reads it |
| `panel_noun` | string | what the panel consists of in sentences (`orthologs`) |
| `species` (required) | array of objects | the orthologs, below |
| `show` | string list | species keys shown in the Map's specificity block (default: the first two species) |
| `headline` | string list | species keys of the headline finding in the Inspector, in order (default: `show`) |
| `detail` | list of strings or `{"group": name, "keys": [...]}` | entries whose differences the detail sentence names. Each entry needs the text keys `finding.spec_orth.<entry>.some` and `.none` when `species_panel` is on |
| `decoy_species` | string list | species of the decoy co-fold section (`decoy_cofold`) |
| `paralogs` | object or null | `focus` (`key` is the gene symbol, `label`; optional `table`, `card`, `phrase`), `members` (list of `{acc, gene, optional}`: the paralogs to compare), `order` (gene symbols in display order), `bars_other` (integer: other paralogs drawn as bars, default 2). `family_label` is not read in this release |
| `rules` | array of rules | rules built from the panel (below) |

A `species[]` entry has `match`, `label` and `key` (required), and `phrase` (the in-sentence name), `table` and `card` (the column head on the Map table and the field label on its hover card; omit for none), `fallback` (a species key to use when this one is missing) and `optional`. `match` is `acc:<accession>`, which works for any UniProt entry and is fetched with `--online`, or an organism name, which is looked up in the panel file written by `scripts/fetch_reference.py --config` ([reference_data.md](reference_data.md): species are given there by organism name, common name, taxon id and role, with a minimum identity of 0.70 to the reference). The homolog blob records each member's accession, organism and taxon id from its UniProt record; the profile carries no taxon ids. The build warns when an ortholog is less than 70 percent identical to the target, which usually means a wrong accession. The validator needs every non-optional species to match an entry of the homolog blob, and a species key to be unique.

```json
"specificity": {
  "subject": "Example protein A", "kind": "host", "panel_noun": "orthologs",
  "species": [
    { "match": "acc:Q9XXXY", "label": "Species 1", "key": "species_1", "phrase": "species 1", "table": "Sp1" },
    { "match": "acc:Q9XXXZ", "label": "Species 2", "key": "species_2", "optional": true }
  ],
  "show": ["species_1"], "headline": ["species_1"],
  "paralogs": { "focus": { "key": "EXB", "label": "Example protein B" },
                "members": [{ "acc": "Q9XXYA", "gene": "EXB" }, { "acc": "Q9XXYB", "gene": "EXC", "optional": true }] },
  "rules": [{
    "id": "species_1_epitope", "group": "Specificity", "label": "Epitope identical in species 1",
    "expr": { "species": "species_1" }, "op": ">=", "val": 0.8, "lo": 0, "hi": 1, "step": 0.05, "dec": 2,
    "note": "Share of the contacted residues that are identical in species 1.",
    "rescue": ["Test in species 1", "Measure binding to the recombinant species 1 protein"],
    "nl": { "words": "species ?1", "text": "At least 80% of the epitope identical in species 1", "val": 0.8 }
  }]
}
```

**Rules and the Reading.** A rule of `specificity.rules[]` has the fields of a Query rule (section 6) and an `expr` that says what is measured, always the share of the design's contacted target residues that are identical:

| `expr` | Measures |
|---|---|
| `{"species": key}` | the share in that species |
| `{"min_species": [keys]}` | the lowest share among those species |
| `{"paralog": gene}` | the share in that paralog |
| `{"paralog_max_excl": gene}` | the highest share among the paralogs except that one |

The same rules build the **Reading** at the top of the Inspector's Specificity tab. A rule is met when the share satisfies `op` and `val` (for `>=` at least `val`, for `<=` at most `val`); every rule shows its `note`, and a rule that is not met adds the second item of its `rescue`. Species and `min_species` rules are listed under cross-species reactivity, paralog rules under off-target risk. The share is taken over contacted residues that are in the panel, and the tab needs at least three of them. Rules whose species or paralog is not in the panel are not shown, and paralog rules need `paralog_panel`.

The pages have no built-in species list and no built-in focus paralog: the ortholog columns, bars and rules come from `species`, the Map's specificity block shows `show` (default: the first two species), and a focus paralog exists only when `paralogs.focus` names one.

Some thresholds are fixed in the Inspector and cannot be set in the profile: the specificity finding needs at least four contacted residues, a paralog other than the focus one warns from half of them identical, and the focus paralog gets an informational finding from 40 percent.

## 8. Frame, membrane, view

**`frame`** draws the strip of the Inspector: where the construct sits in the full-length protein. When the profile has none, the exporter writes the one derived from the reference structure and the UniProt record (without an accession it has no segments). When it has one, the profile's own strip is used, and a profile segment with the same `start`, `end` and `kind` as a derived one gives that segment its `label` and `phrase`. The exporter warns when the profile's ranges, kinds or sites differ from the derived ones and an accession is known.

| Key | Meaning |
|---|---|
| `native_range` | two integers, `[first, last]` of the mature chain; a construct that starts after `first + 1` or ends before `last` is cut there |
| `segments` | array of objects: `label` (string, empty for an unnamed linker), `start` and `end` (integers), `kind` (the colour class: `x` outside or unstructured, `v` and `c` domains, `l` linker, `b` base, `s` stalk, `h` head) and `phrase` (the name inside a sentence, such as "domain 1") |
| `glycan_sites` | integers: N-glycosylation sites of the full-length protein |
| `disulfides` | pairs of integers, drawn as arcs |

The validator needs segments in ascending order without overlap, `first <= last`, and glycan sites inside `native_range`. An invented strip:

```json
"frame": {
  "native_range": [1, 400],
  "segments": [
    { "label": "N-terminal tail", "start": 1, "end": 30, "kind": "x" },
    { "label": "Domain 1", "start": 31, "end": 220, "kind": "v", "phrase": "domain 1" },
    { "label": "Domain 2", "start": 221, "end": 400, "kind": "c", "phrase": "domain 2" }
  ],
  "glycan_sites": [88, 301], "disulfides": [[45, 120]]
}
```

**`membrane`.** `plane_radius` (number) is the radius in Å of the disc drawn in the Inspector (fallback 42), `plane_half_size` (number) the half side in Å of the plane drawn on the Query page (48). `strip_shade` is null or a list of `[first, last]` target residue ranges shaded as membrane-side on the Map's contact strip; null derives them from the plane (residues whose CA lies within 10 Å of the end of the target nearest the plane). The plane itself comes from the build and needs a UniProt accession; a target without a transmembrane annotation has no plane.

**`view`.** `query_fit_pad` is the share of the Query viewer the target fills (0 to 1, fallback 0.92). `inspector_focus` is `all` or `binder` (the 3D view fits both chains or the binder; fallback `all`) and `inspector_zoom` the zoom after the fit (1.75 for `all`, 0.8 for `binder`). `map_size_metric` is `ipsae` or `iptm`: `iptm` sizes the Map markers by BindCraft's i_pTM and makes the Board lead with it when the designs carry it, `ipsae` uses ipSAE when they carry that. Set it explicitly, because the Map falls back to `ipsae` and the Board to `iptm`.

## 9. Other sections

| Section | Keys | Meaning |
|---|---|---|
| `predictors` | `primary` (`key`, `label`, `detail`), `monomer` (`key`, `label`, or null), `design_model_prefix`, `colors`, `rescore[]` | names of the models in the Inspector: the model that made the complex, the binder-alone model, the prefix (fallback `AF2`) of the pose entry that is the design model itself, and the overlay colour per pose name. Nothing reads `rescore` in this release |
| `modes` | `notes.<mode id>` | a note for a binding mode when the data carry none (`campaign.modes[m].note` wins) |
| `reasons` | `[[key, label], ...]` | the reason chips of the Board's decision panel; the key is stored in review events. At most nine are read and the first seven answer the keys 1 to 7. Seven generic chips when absent |
| `inspector` | `names`, `order`, `id_strip`, `method_names`, `method_strip` | `names`: design tag to display name; `order`: the Inspector's designs in display order (a design that is not listed is not shown); `id_strip`: a regular expression removed from the start of a design id to make the short label; `method_names` and `method_strip`: labels from a design's method string, for sources that record one |
| `import` | `title`, `files`, `files_extra`, `cards[]` | content of the Board's "How to import a run" dialog; texts may use the tokens of the import dialog. `cards[]` have `title` and `html`, and optionally `column`, `chips`, `actions`, `tag` |
| `tips` | `board`, `inspector`, `map`, `query` | "What to try" tips, a list of HTML strings per page; generic tips when absent |
| `flag_region` | `key`, `label`, `range`; or null | the region of the target whose contacts are counted apart (needs `flag_region` on) |
| `footprints` | `primary`, `labels`, `control_regex`, `colors` | known-complex footprints (needs `footprints` on) |

The Board also reads a `board` section (`features`, `compare`, `sib_cols`, `tile_metrics`, `simulate`) and `export.sequences`; the schema does not list them. They are described in [viewer_board.md](viewer_board.md).

## 10. Adding a target

1. **Copy the default profile.** `cp src/binder_review/profiles/default.profile.json my-target.profile.json`. Set `id` to your own slug: with `id` `default` and an accession, the exporter replaces the target's names with those of the UniProt record.
2. **Name the target.** `target.name`, `short`, `gene`, `organism`, `construct.label`, and `target.uniprot` or the `--uniprot` option. Set `brand.titles`, `badge.text` and `credit` if you want them, and keep `kind` as `run`.
3. **Frame.** Leave `frame` out to use the derived strip, or write it (section 8) when the target needs domain names the records do not give.
4. **Specificity.** Set `specificity.subject` (the default profile's is a placeholder) and fill `species` and, for related proteins, `paralogs.members` and `paralogs.focus` (section 7). A panel needs the accession; run `scripts/fetch_reference.py --profile my-target.profile.json` once with network access, or build with `--online`.
5. **Checks.** Choose `checks.preset_default`, or define a preset that matches your filters. Keep `context.cut.k` and `context.cover.min_area` at the default profile's values and adjust the others if the context verdicts do not suit the target.
6. **Rules and Ask box.** List `rules.groups`, add `rules.overrides` for notes that name the target, add `rules.extra` rules for target-specific metrics, and write `nl.examples` that make sense for it.
7. **Capabilities.** Leave `caps` as `{}`. The exporter writes it.
8. **Text.** Write only the keys whose built-in wording is wrong for the target. Keep the `inspector.struct_sub` text and the `TGT` placeholders of the default profile (section 3); the exporter replaces the placeholders.
9. **Validate.** `python scripts/validate_schema.py --kind profile my-target.profile.json` checks the schema, the tokens and lookups of `text`, the species keys and the capability promises. It exits with 1 on an error. A key that is not in the registry is a warning.
10. **Build and open.** In the app, choose **New project**, add your runs and structures, choose the profile under Options, Profile file, and press Build project ([app.md](app.md)). On the command line: `binder-review import run01=... run02=... --target-pdb construct.pdb --reference-cif full_length.cif --name my-target --out work/my-target --profile my-target.profile.json --bundle bundles/my-target`, then open `bundles/my-target/index.html` ([cli.md](cli.md)). `python scripts/validate_schema.py --bundle bundles/my-target` checks the result, and `data/manifest.json` lists the final `caps` and the warnings.

What to look at on each page:

| Page | Check |
|---|---|
| Overview | the target name, the reference, UniProt and construct chips, the highlights |
| Board | the badge tooltip, the headline score, the Context row (present only with `olig` or `rest_ctx`), the counts of PASS, REVIEW and NO PASS under your preset |
| Inspector | the frame strip (segments, glycans, cut ends), the sentences of the findings, the Specificity tab with its Reading, the toggles in the 3D view |
| Map | the specificity block and table columns, the strip shading, the funnel stages |
| Query | the rule groups and notes, the example chips, the recipe's target label |
| any page | `PF.missing()` in the console, and no literal `{...}` left in a text |

## Appendix: text key catalogue

The registry lists 232 text keys. A row with braces stands for several keys that differ in the last part (for example `.title`, `.detail` and `.fix`); its tokens are the union over those keys.

### All pages

| Key | Tokens | Meaning |
|---|---|---|
| `common.check.context.tip` |  | Tooltip of the Context check name (asked for when `olig` or `rest_ctx` is on). |

### Board

| Key | Tokens | Meaning |
|---|---|---|
| `crumb.board.{run,sub}` | `{run_source}`, `{n_runs}`, `{n_sample}`, `{n_families}`, `{n_designs}` | Breadcrumb of the Board: the run label (bold) and the muted text after it. |
| `badge.title.board` | `{run_license}`, `{run_credit}` | Tooltip of the data badge in the header. |
| `features.{footprint,partner,flag_region,monomer,rescore_pose,rest,cut}` |  | Names of the reviewer-model features that exist only with a capability: known footprint, partner, flagged region, monomer pLDDT, re-score pose, removed regions, cut ends. |
| `board.check.{fold,interface,epitope}` | `{plddt}`, `{ptm}`, `{ipsae}`, `{min_ipae}`, `{iptm}`, `{ipae}`, `{hot}`, `{hot_of}`, `{epi_rec_pct}` | Rows Fold, Interface and Epitope of the focus panel: the text next to each check. |
| `board.check.consistency.{label,ok,far,none}` | `{rmsd}`, `{note}` | Row of the pose-consistency check: its name, and the text when the metric is within its limit, above it, or missing. |
| `board.check.context.{fail,warn,flag,pass,rest,cut}` | `{n3}`, `{olig_min}`, `{n}` | Context row of the focus panel: the text for partner overlap, contact, flagged region, clear, removed regions and a cut end. |
| `board.row.{partner_gap,flag_region}` |  | Compare-table rows: distance to the partner subunit and contacts in the flagged region. |
| `board.sib.iptm_title` |  | Tooltip of the i_pTM column of the sequence table. |
| `board.div.note` |  | Note under the diversity controls. |
| `board.focus.{dashed,default}` | `{rmsd}` | Caption of the focus viewer: when a re-score places the binder elsewhere (`complex_rmsd`) and otherwise. |
| `board.demo.banner` | `{n}` | Banner shown while the simulated reviewer runs (see `board.simulate`). |
| `board.lab.{weighted_note,conclusion}` | `{n_boltz}`, `{base}` | Lab panel (`outcomes`): the note under the verdict bars (also needs `weights`) and the closing sentence. |

### Map

| Key | Tokens | Meaning |
|---|---|---|
| `crumb.map.{title,sub}` | `{run_name}`, `{run_source}`, `{n_runs}`, `{n_designs}`, `{n_families}`, `{n_sample}` | Breadcrumb of the Map: the target (bold) and the muted text after it. |
| `badge.title.map` | `{run_license}`, `{run_credit}` | Tooltip of the data badge in the header. |
| `map.stage.all.{label,thr}` | `{n_families}`, `{max_seq}`, `{n_sample}`, `{n_total}`, `{n_designs}` | First stage of the filter strip: label and detail. |
| `map.stage.iface.thr` | `{ipsae}`, `{min_ipae}`, `{iptm_b}`, `{ipae_norm}` | Detail of the interface-confidence stage. |
| `map.stage.pose.{label,thr}` | `{complex_rmsd}`, `{binder_rmsd}` | Label and detail of the pose-consistency stage (`complex_rmsd` or `binder_rmsd`). |
| `map.stage.ctx.{label,thr}` | `{n3}` | Label and detail of the context stage (`olig` or `rest_ctx`). |
| `map.stage.epi.{label,thr}` | `{hotspots}`, `{hot_of}` | Label and detail of the epitope stage. |
| `map.pool.{epi,pose}` |  | The two options of the pool selector of the picks panel. |
| `map.picks.summary` | `{n_bb}`, `{n_sc}`, `{n_modes}` | Summary line of the picks panel after the mode counts. |
| `map.strip.caption` |  | Caption of the one-dimensional contact strip. |
| `map.ramp.cons_caption` | `{n_orth}` | Caption of the conservation colour ramp. |
| `map.cons.title` |  | Tooltip of the Conservation colour button. |
| `map.hint` |  | Hint line under the binding-mode map. |
| `map.table.{refold_title,spec_title}` | `{subject}`, `{phrase}` | Tooltips in the table: the refold column and a specificity column. |
| `map.stats.{clash_title,flag_label}` | `{n3}` | Mode-detail statistics: tooltip of the clash count (`olig`) and label of the flagged-region count (`flag_region`). |
| `map.flag.{partner,rest}` |  | Warning-icon tooltips: overlap with the partner subunit (`olig`) or with the removed regions (`rest_ctx`). |
| `map.spec.{species_title,focus_title,note}` | `{label}`, `{subject}` | Specificity block of the mode detail: tooltips of a species bar and of the focus-paralog bar, and the note under the block. |

### Query

| Key | Tokens | Meaning |
|---|---|---|
| `crumb.query.{title,sub}` | `{run_name}`, `{run_source}`, `{n_runs}`, `{n_sample}`, `{n_designs}`, `{n_accepted}`, `{n_rejected}` | Breadcrumb of the Query page: the target (bold) and the muted text after it. |
| `badge.title.query` | `{run_license}`, `{run_credit}` | Tooltip of the data badge in the header. |
| `query.funnel.first` |  | First row of the funnel. |
| `query.nl.placeholder` |  | Placeholder of the Ask box. |
| `query.recipe.target` |  | Target label written into an exported rule recipe (usually `{@target.label}`). |
| `query.rule.mem.fail` | `{mem}` | Reason shown when a design fails the membrane rule. |

### Inspector

| Key | Tokens | Meaning |
|---|---|---|
| `crumb.inspector.camp` | `{run_name}`, `{run_source}`, `{n_sample}`, `{n_runs}` | Breadcrumb of the Inspector: the campaign label. |
| `badge.title.inspector` | `{run_license}`, `{run_credit}` | Tooltip of the data badge in the header. |
| `inspector.col.cons_title` |  | Tooltip of the Conservation colour button. |
| `finding.epi.ok.{title,detail}` | `{nh}`, `{hot_of}`, `{hot_list}`, `{mode}`, `{mode_name}` | Epitope finding when the design touches the required number of hotspots (`preset.hotspots`, capped by the hotspots of its run). |
| `finding.epi.warn.{title,detail,fix}` | `{nh}`, `{hot_of}`, `{hot_list}` | Epitope finding when it touches some hotspots but fewer than required. |
| `finding.epi.bad.{title,detail,fix}` | `{domain_phrase}`, `{residues}`, `{more}` | Epitope finding when it touches no hotspot. |
| `finding.buns.density_note` |  | Sentence appended to the buried-polars finding when `checks.physics.unsat_basis` is `per_1000_A2`. |
| `finding.pose.flip.{title,detail,fix}` | `{odd_list}`, `{ipsae}`, `{af_clause}`, `{ind_list}` | Pose finding: a confident design whose re-score lands elsewhere (`models`). |
| `finding.pose.odd.{title,detail,fix}` | `{nag}`, `{n_ind}`, `{odd_list}`, `{agree_clause}` | Pose finding: some independent predictors disagree with the design (`models`). |
| `finding.pose.ok.{title,detail}` | `{poses_list}`, `{seed_spread}` | Pose finding: all independent predictors agree (`models`). |
| `finding.pose.mono.far.{title,detail,fix}` | `{rm}`, `{esm}`, `{ipsae}`, `{one_model}` | Pose finding with a co-folding model (`has_boltz`): the binder predicted alone differs strongly from its shape in the complex. |
| `finding.pose.mono.warn.{title,detail,fix}` | `{rm}`, `{esm}`, `{one_model}` | Same family: the binder changes shape without the target. |
| `finding.pose.mono.ok.{title,detail}` | `{rm}`, `{esm}`, `{one_model}` | Same family: the binder refolds on its own. |
| `finding.pose.mono.none.title` |  | Same family: no monomer model is available. |
| `finding.pose.one_model` |  | Sentence saying that agreement between predictors cannot be checked with one model (`has_boltz`). |
| `finding.conf_if.{title,detail,fix}` | `{ipsae}`, `{min_ipae}`, `{iptm}` | Low interface confidence finding on ipSAE and min iPAE (`has_boltz`). |
| `finding.pose.bc.warn.{title,detail,fix}` | `{hotspot_rmsd}`, `{binder_rmsd}` | Pose finding for BindCraft-only data when Hotspot RMSD is above `checks.findings.hotspot_rmsd_warn` (`hotspot_rmsd`). |
| `finding.pose.bc.ok.{title,detail}` | `{hotspot_rmsd}`, `{binder_rmsd}` | Pose finding when Hotspot RMSD is within that limit. |
| `finding.gly.{text,confirm}` | `{glycan_list}`, `{min_area}`, `{k}` | Glycan finding: the sentence about the glycans of the target (`text`) and the paragraph on what would confirm or refute it (`confirm`; tokens `{k}`, `{min_area}`). |
| `finding.mem.{text,noun,clash,confirm}` | `{mem}`, `{noun}`, `{min_area}`, `{k}` | Membrane finding: noun phrase for the plane (`text`), word for the full-length protein (`noun`), the sentence when the binder crosses the plane (`clash`) and the confirm paragraph. |
| `verdict.ctx_olig` |  | Verdict label when overlap with the partner subunit is the first failing finding. |
| `finding.spec_orth.{detail,fix}` | `{nc}`, `{n}`, `{n_orth}`, `{mg}`, `{clauses}` | Ortholog finding: the detail sentence, where `{clauses}` is built from `specificity.detail`, and the next step. |
| `finding.spec_orth.<entry>.{some,none}` | `{list}`, `{more}`, `{label}` (a species entry); `{items}` (a group entry) | Clause of the ortholog finding for one entry of `specificity.detail`: the contacted residues differ (`some`) or are all kept (`none`). Needed for every entry. |
| `finding.spec_par.warn.{title,detail,fix}` | `{label}`, `{id}`, `{n}` | Paralog finding: a paralog other than the focus one shares at least half of the contacted residues. |
| `finding.spec_par.focus.{title,detail,fix}` | `{id}`, `{n}` | Paralog finding: the focus paralog shares at least 40% of the contacted residues. |
| `finding.spec_par.ok.{title,detail}` | `{label}`, `{id}`, `{n}` | Paralog finding: the contacted residues differ from the paralogs. |
| `spec.{ref_row,species_title,intro,cons_note,par_note,paralog_ident_title}` | `{n_orth}`, `{name}`, `{gene}`, `{pct}` | Specificity tab: label of the reference row, heading of the species section, introduction (HTML), note under the conservation bars (HTML), note under the paralog bars, tooltip of a paralog bar. |
| `spec.decoy.intro` | `{ipsae}` | Text above the decoy co-fold button (`decoy_cofold`). |
| `finding.ctx.frame.title.{trim,full}` | `{span}`, `{len}` | Title of the frame finding in two optional variants, for a trimmed and for a full construct; used only when the profile defines both `.trim` and `.full`. |
| `finding.ctx.frame.detail.{trim,full}` | `{a}`, `{b}`, `{gap_clause}` | Detail of the matching variant of the frame finding. |
| `finding.ctx.frame.{title,detail}` | `{span}`, `{len}`, `{before}`, `{removed_text}`, `{run_clause}` | Frame finding: where the construct sits in the full-length protein, and what the checks therefore assume. |
| `finding.ctx.near.{clash,touch,close,clear,detail,fix,confirm}` | `{d}`, `{bres}`, `{tres}`, `{min_area}`, `{k}` | Removed-region finding: the sentence for each band set by `checks.context.near` (clash, touch, close, clear), the detail, the next step and the confirm paragraph. |
| `finding.ctx.cov.some.title` | `{count_phrase}` | Coverage finding when contacted residues would be buried by the rest of the protein. |
| `finding.ctx.cov.none.title` |  | Coverage finding when none are. |
| `finding.ctx.cov.{detail,fix,confirm}` | `{all}`, `{min_area}`, `{tail}`, `{k}` | Coverage finding: detail, next step and confirm paragraph. |
| `finding.ctx.cov.tail.{some,none}` | `{list}`, `{n}` | Sentence ending the coverage detail: the residues listed, or none. |
| `finding.ctx.cut.some.title` | `{n}`, `{s}` | Cut-end finding when contacts lie next to a cut end. |
| `finding.ctx.cut.none.title` |  | Cut-end finding when none do. |
| `finding.ctx.cut.detail.{head,some,none}` | `{cuts}`, `{first}`, `{k}`, `{list}` | Cut-end finding detail: the opening sentence, the contacts listed, or none near a cut. |
| `finding.ctx.cut.{fix,confirm}` | `{min_area}`, `{k}` | Cut-end finding: next step and confirm paragraph. |
| `finding.ctx.olig.{buried,noun,confirm}` | `{count_phrase}`, `{runs}`, `{min_area}`, `{k}` | Partner finding: the sentence about residues buried at the partner interface, the noun for a residue and the confirm paragraph. |
| `finding.ctx.olig.bad.{title,detail,fix}` | `{n3}`, `{dm}`, `{bur_text}` | Partner finding when the binder overlaps the partner subunit. |
| `finding.ctx.olig.warn.{title,detail,fix}` | `{dm}`, `{n4_atoms}`, `{bur_text}` | Partner finding when the binder touches the partner subunit. |
| `finding.ctx.olig.ok.{title,detail}` | `{dm0}`, `{dm}` | Partner finding when the binder is clear of it. |
| `finding.ctx.flag.{title,detail,fix}` | `{n}`, `{s}`, `{runs}`, `{head_range}` | Flagged-region finding (`flag_region`). |
| `inspector.tool.{ctx,olig,fp}` |  | Labels of the viewer toggles: removed region, partner subunit (`olig`), known binders (`footprints`). |
| `view.ctx.{label,tip}` |  | Label and hover text of the removed region in the 3D view (HTML). |
| `view.olig.trace` | `{chain}` | Hover text of a partner chain trace in the 3D view. |
| `inspector.chip.{model,stand_in}` | `{method_short}` | Chip beside the verdict naming the model that made the complex, and the chip for a design record that carries a `source_note`. |
| `inspector.chip.model.title` | `{method}` | Tooltip of the model chip. |
| `meter.plddt.tip` |  | Tooltip of the binder pLDDT meter. |
| `meter.ipsae.tip` |  | Tooltip of the ipSAE meter (`ipsae`). |
| `meter.iptm.tip` |  | Tooltip of the ipTM meter. |
| `meter.min_ipae.tip` |  | Tooltip of the min iPAE meter. |
| `meter.ipae_norm.tip` |  | Tooltip of the i_pAE meter (`ipae_norm`). |
| `meter.rmsd.{label,tip}` |  | Label and tooltip of the complex RMSD meter (`complex_rmsd`). |
| `meter.brmsd.{label,tip}` |  | Label and tooltip of the binder RMSD meter (`binder_rmsd`). |
| `meter.olig.{label,tip}` |  | Label and tooltip of the partner gap meter (`olig`). |
| `meter.rest.{label,tip}` |  | Label and tooltip of the rest-of-protein gap meter (`rest_ctx`). |
| `meter.sc.tip` |  | Tooltip of the shape complementarity meter. |
| `meter.dg.tip` |  | Tooltip of the interface energy meter. |
| `meter.unsat.tip` |  | Tooltip of the unsatisfied polars meter. |
| `finding.epi.fp.ok.{title,detail}` | `{nh}`, `{n_hot}`, `{hot_clause}`, `{cover_pct}`, `{n_ov}`, `{n_fp}`, `{fp_name}`, `{mode}`, `{mode_name}`, `{tail}`, `{ctl}` | Footprint epitope finding when the design sits at the main known site (`footprints`). |
| `finding.epi.fp.warn.{title,detail,fix}` | `{nh}`, `{n_hot}`, `{hot_list}`, `{cover_pct}`, `{tail}`, `{ctl}` | Footprint epitope finding when it only partly covers that site. |
| `finding.epi.fp.other.{title,detail}` | `{fp_name}`, `{n}`, `{of}`, `{tail}`, `{ctl}` | Footprint epitope finding when it sits at another known site. |
| `finding.epi.fp.new.{title,detail,fix}` | `{domain_lc}`, `{max_ov}`, `{tail}`, `{ctl}` | Footprint epitope finding when it is away from every known site. |
| `finding.epi.fp.{tail,control}` | `{shared}`, `{pdb}`, `{fp_name}`, `{n}`, `{of}` | Sentence listing the overlap with every known footprint, and the sentence for a design flagged as a control. |
| `inspector.conf.predictors.{sub,note}` |  | Confidence tab: sub-heading of the Predictors block and the note under it (HTML). |
| `inspector.conf.pae_note` | `{nt}` | Sentence appended to the PAE note when only the nearest target residues are shown (`has_pae`). |
| `inspector.iface.{contrib_sub,contrib_note}` |  | Interface tab: sub-heading of the per-residue contribution block and the note under it. |
| `inspector.tab.sib` |  | Label of the Siblings tab. |
| `inspector.sib.{note,diff_label,diff_ref}` |  | Siblings tab: note under the table, header of the last column and what each sequence is compared with. |
| `lab.card.base_rate` | `{pct}`, `{nb}`, `{tot}` | Last sentence of the lab card for a design that did not bind although the checks passed (`outcomes`). |
| `inspector.similar.{note,try}` | `{lab_clause}`, `{n_binders_sample}`, `{n_sample}`, `{n_binders_total}`, `{n_total}` | Similar tab (used when `families` is off): note under the table (HTML) and the what-to-try paragraph. |
| `inspector.struct_sub` |  | Subtitle of the Structure panel after the design id. The default profile defines it (see section 3). |
| `finding.conf_if.native.{title,detail,fix}` | `{iptm}`, `{ipae}` | Low interface confidence finding on the design tool's own i_pTM and i_pAE (BindCraft-only data). |
| `finding.ctx.sym.{title,detail}` | `{dm}`, `{n3}` | Information finding: the binder overlaps its own copy on the other subunit of the assembly. |
| `meter.ptm.tip` |  | Tooltip of the pTM meter. |
| `meter.hotspot_rmsd.tip` |  | Tooltip of the Hotspot RMSD meter (`hotspot_rmsd`). |
| `meter.surf_hyd.tip` |  | Tooltip of the surface hydrophobicity meter. |
| `meter.unsat.own.tip` | `{n}` | Tooltip of the unsatisfied-polars meter when it shows the design tool's own count. |

## See also

[data_contract.md](data_contract.md) · [viewer.md](viewer.md) · [viewer_board.md](viewer_board.md) · [viewer_inspector.md](viewer_inspector.md) · [viewer_map.md](viewer_map.md) · [viewer_query.md](viewer_query.md) · [specificity.md](specificity.md) · [target_frame.md](target_frame.md) · [reference_data.md](reference_data.md) · [cli.md](cli.md) · [schema/README.md](schema/README.md)
