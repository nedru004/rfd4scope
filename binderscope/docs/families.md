# Families, clusters and diversity

`binder_review.families` (`src/binder_review/families.py`) groups the designs of each run into backbone families, clusters the families by fold, by binding site or by both, and estimates whether more designs would still find new clusters. This page is for developers and advanced users: it says what is counted, how clusters and estimates are computed, what the diversity panel of the viewer's Board page shows, and where the method stops being reliable.

Count families, never sequences. Every number here is a screen: a passing design is a predicted binder, not a measured one.

## What is counted

| Term | Meaning here |
|---|---|
| **family** (the "individual" of the statistics) | One BindCraft backbone, which is one trajectory (for BindCraft 1 the design name without its `_mpnn{n}` suffix), with at least one accepted design. The sequences designed on it are its siblings: they belong to the family but are never counted as separate individuals |
| **cluster** (the "class") | A group of families that are alike at one level (`fold`, `site` or `both`), found by average linkage cut at a distance |
| **n**, **S**, **f1**, **f2** | The number of families; the number of clusters; the number of clusters that contain exactly one family; the number that contain exactly two |
| **passed trajectory** | A row of the run's trajectory table: BindCraft 1 `trajectory_stats.csv`, BindCraft 2 `!_Trajectories.csv` (see [bindcraft2_reader.md](bindcraft2_reader.md) for what that table holds). It is the unit of **N**, and `traj_basis` is `passed` |
| **attempted trajectories** | Unknown for every reader today. A derived estimate (`FunnelCounts.trajectories_attempted_estimate`) may exist, but it is never used as a denominator here |
| **yield** | Families per passed trajectory, with a Wilson 80 percent interval. It is not a yield per attempt, and the flag `unknown_attempts` says so |
| `traj` of a family | The 1-based row of its backbone in the trajectory table (`BackboneRecord.traj`); 0 when the backbone has no row |

A backbone whose sequences were all rejected is not a family (`build_families(..., include_rejected_only=True)` lists them). Rejected siblings are still members of the family and are counted (`n_rejected`, `n_rejected_with_structure`), but they never enter clustering or diversity.

## Method

**Representative.** The representative of a family is its best accepted design: lowest BindCraft `Rank`, then highest `Average_i_pTM`, then id (`design_sort_key`). `quality_scores(run)` turns the same order into the exporter's `design.q`, so the viewer's choice of representative (largest `q`) is the same design as `FamilyRecord.best`.

**Footprint.** The footprint of a family is the set of target residues touched by at least half of its accepted siblings, where "touched" means any heavy-atom pair within 4.5 angstrom (`model.CONTACT_CUTOFF`) in the saved structure. With two siblings the footprint is therefore the union. Footprints use reference numbering (the code and the field names call it UniProt numbering; the offset comes from the target frame, see [target_frame.md](target_frame.md)), which makes the site basis comparable between runs and keeps deleted stretches of a construct out of every footprint. When a run has no offset yet the module falls back to construct numbers and records a warning.

**Fold distance.** `1 - TM`, where TM is the TM-align score of the two representatives' binder CA traces (`structure.tm_matrix`, tmtools) normalised by the shorter chain. The matrix is computed once over the accepted designs of all runs and stored as round(100 x TM) in a symmetric `uint8` matrix with a diagonal of 100. A cut of 0.5 means "same fold"; a cut of 0.2 (TM 0.8) is closer to "same backbone".

**Site distance.** The Jaccard distance of two footprints, `1 - |A and B| / |A or B|`; two empty footprints are 1 apart. A cut of 0.5 means "same site".

**Both.** `fold_weight x fold distance + (1 - fold_weight) x site distance` with `fold_weight` 0.5.

**Clustering.** Average-linkage agglomerative clustering, merging while the smallest average distance between two clusters is at most the cut (inclusive). Ties are broken by the first pair in row-major order. Clusters are numbered largest first, and families are processed in generation order. The browser implements the same algorithm (`viewer/js/family.js`), and the two agree at the default cuts; the values the exporter writes are the reference for them.

| Basis | Distance | Default cut |
|---|---|---|
| `fold` | fold distance | 0.5 |
| `site` | site distance | 0.5 |
| `both` | the weighted sum (the default basis) | 0.5 |

### What the Board exposes

The Board's diversity panel recomputes the clusters live from the data, per run and never pooled:

- **Count by** chooses the basis: *Backbone* (`fold`), *Both* or *Binding site* (`site`). The first button reads *Fold* when the data have no backbone families.
- **Same cluster if** sets the cut as a similarity from 0.30 to 0.70 in steps of 0.01 (the cut distance is 1 minus that value), shown as "at least 0.50 alike" at the default. Switching *Count by* resets the slider to that basis's default from `diversity.distance_default`.
- The *Both* weight is `diversity.fold_weight` of the data.

The numbers on the Board therefore change with the slider and may differ from the stored defaults. See [viewer_board.md](viewer_board.md) for the page.

## Estimates

All estimates treat families as individuals and clusters as classes, per run and per basis.

- **Counts.** n families, S clusters, f1 and f2.
- **Coverage** (Chao-Jost sample coverage): the estimated chance that the next passing family falls into a cluster that has already been seen. `C = 1 - (f1/n) * [(n-1) f1 / ((n-1) f1 + 2 f2)]`. For f2 = 0 the bias-corrected form is used, `(n-1)(f1-1)` in place of `(n-1) f1` and 2 in place of `2 f2`. With no singleton (f1 = 0) the coverage is 1. `p_new_family` is `1 - C`.
- **Unseen clusters** (bias-corrected Chao1): `f0 = (n-1)/n * f1^2 / (2 f2)`, or `f1 (f1-1) / 2` when f2 = 0. `chao1 = S + f0` is a lower bound on the number of clusters.
- **Forecast**: after m more families the expected number of clusters is `S + f0 * (1 - (1 - f1 / (n f0 + f1))^m)`. It is valid for m up to n, and `snew` lists the expected new clusters for m = 1 to n. The dashed forecast curve on the Board is this extrapolation, not a fitted curve. At the observed yield, `expected_more_600` is the number of new clusters expected after 600 more passed trajectories, and `families_more_600` the number of further families that implies (capped at n). The Board states the same forecast over a step that depends on the run (600 trajectories for 800 or more, otherwise half the run, rounded to 50, at least 50).
- **Yield**: families per passed trajectory with a Wilson interval at z = 1.2816 (80 percent).
- **Shape of the clusters**: `hill1` (the effective number of clusters, the exponential of the Shannon entropy), `hill2` (inverse Simpson), `dominance` (share of families in the largest cluster), `recent_new` and `early_new` (clusters first seen in the last and in the first 30 percent of the trajectories).

A worked example with invented numbers: 40 families in 25 clusters, 15 clusters seen once and 5 seen twice, give `C = 0.6313`, `f0 = 21.94`, `chao1 = 46.94`, and 35.8 expected clusters after 40 more families.

### Do you need more designs?

The level of the readout comes from the number of families and the coverage:

| `level` | Board label | Rule | Meaning |
|---|---|---|---|
| `early` | Too early to judge | fewer than 10 families | Only the yield is shown; no coverage is given |
| `keep` | Still discovering | coverage below 0.7 | More than 3 in 10 new passing families are expected to be in a cluster not seen yet, so more designs with these settings should add clusters |
| `maybe` | Diminishing returns | coverage from 0.7 up to 0.9 | New clusters still turn up but more slowly; changing hotspots or lengths may give more new ideas than more of the same |
| `enough` | Saturated for this setup | coverage 0.9 or more | Fewer than 1 in 10 new families is expected to be new; more designs with these settings add little, so test the current set or change the setup |

Below 30 families the readout carries `low_confidence`, and the Board says that coverage estimates need about 30 families. The thresholds are the constants `MIN_FAMILIES`, `LOW_CONFIDENCE_BELOW`, `COVERAGE_KEEP` and `COVERAGE_ENOUGH`.

The label depends on the basis and the cut, because those decide what counts as a different cluster. It depends on the run's settings too: change the hotspots, the length range, the construct or the filters and the estimate starts again (see epochs below).

## Replay

`replay` backtests the readout over the generation order. After every family, in order, it records what the tool would have said at that trajectory: the clusters of the full clustering restricted to the families found so far (as the Board does), `upto` (passed trajectories), `n_families`, `S`, `coverage` (null while the level is `early`), `level`, `found_later` (clusters the run went on to find), `chao1` and `forecast_at_end` (the clusters expected at the last trajectory at the yield of that moment). `replay` is for the default basis; `replay_by_basis` has all three. A replay exists only when every family has a `traj` above 0. The Board's replay slider shows the same readout for any earlier point.

## Distance sweep and the pooled block

`sweep.<basis>` gives S, f1, f2, coverage and Chao1 at the distances 0.2, 0.3, ... 0.8, so that a setting that cannot saturate (every family a cluster of its own at a tight cut) or that merges everything is visible.

`diversity_all` returns one block per run and one pooled block. The pooled block clusters the families of all runs together (fold across runs, site on reference numbers; each cluster carries `by_run`). It is descriptive: it has `advice` and `replay` only when the project has a single run, and its `estimates_valid` is false, with the flag `epoch` naming what differs, when the runs' epochs differ.

## Settings epochs and pitfalls

`epoch_hash` is the first 12 hex characters of the SHA-1 of the settings epoch: the hotspots, the target construct (the residue ranges present), the length range and the filters (the name of the run's filter settings and the rule list of the run's filter set, `config.filters_for_run(run)`: the thresholds the run recorded, or `config.DEFAULT_FILTERS`), in reference numbers when the offset is known. Protocol, helicity and other advanced settings are not part of it. A change in any component starts a new epoch, and estimates are not carried across.

Each diversity block carries `flags`, each with `code`, `severity`, `text` and `data`:

| Code | Severity | Raised when |
|---|---|---|
| `unknown_attempts` | info | the number of attempted trajectories is unknown, so yield is per passed trajectory |
| `small_sample` | warn below 10 families, info below 30 | too few families for coverage (below 10) or for confidence (below 30) |
| `sibling_cap` | info | a family has as many accepted sequences as BindCraft allows per backbone (`max_mpnn_sequences` from the settings, else 2 when no backbone exceeds it, else the observed maximum). Sibling counts are then lower bounds; family counts are not affected |
| `unlogged_af2_failures` | info | the failure counters show failures of the AlphaFold2 pre-filters (pLDDT, i_pTM, i_pAE), which leave no row |
| `threshold_artefact` | warn | at the fold cut of 0.2, at least 90 percent of the families (at least 3) are clusters of their own, so the fold count cannot saturate at that setting |
| `restart_counters` | info | always: a restarted run resets BindCraft's own counters and cannot be detected |
| `extrapolation_limit` | info | always: forecasts hold for m up to n |
| `predicted_only` | info | always |
| `epoch` | warn | the pooled block, when the runs' epochs differ |

## Chronology

The only generation order in a BindCraft 1 folder is the row order of `trajectory_stats.csv`: BindCraft appends a row when a trajectory finishes and passes. `TrajectoryTime` is the duration of each trajectory, not a clock time, so the order cannot be checked against it. `chronology(run)` says this and reports `verified: false`. Where the reader records durations, `FamilyRecord.cum_time_h` is their running sum up to the family's trajectory, a serial-time proxy and not wall-clock time.

## API

```python
from binder_review import families as F

res = F.analyze_families(runs, rejected_labels=True)    # runs: list of RunData, structures read once
F.write_artefacts(res, "work/my-target/families")        # <run>.json per run and fold.json
```

`analyze_families(runs, *, uniprot_offset=None, n_jobs=None, basis_cuts=None, fold_weight=0.5, rejected_labels=False)` returns a `FamilyAnalysis` with `families`, `fold`, `fold_labels`, `diversity`, `design_ids`, `chronology`, `rejected_fold` and `warnings`. `to_dict()` and `run_result(run_id)` are JSON-serialisable. Everything is deterministic (no timestamps, no random numbers; the process pool does not change results) and free of binder sequences. The build calls it with its defaults and keeps the result in `stages/families.json` of the project folder; `--jobs` of `import` sets the worker processes ([cli.md](cli.md)).

| Function | Returns |
|---|---|
| `build_families(run, geometry=None, *, include_rejected_only=False, uniprot_offset=None)` | `list[FamilyRecord]` in generation order: `id`, `traj`, `length`, `seed`, `members`, `accepted` (by rank), `rejected`, `n_rejected_with_structure`, `best`, `ranks`, `footprint`, `footprint_numbering`, `summary` (mean over the accepted siblings of a set of BindCraft stems), `best_metrics`, `trajectory_metrics`, `cum_time_h`, `at_sequence_cap` |
| `load_geometry(designs)`, `design_geometry(design)` | the binder CA trace, its sequence (only to seed TM-align, never written out) and the contacted target residues |
| `fold_matrix(designs, geometry=None, *, id_of=None, n_jobs=None)` | the campaign `fold` dict: `n`, `ids`, `tm` (base64, `uint8`, x100), `note`, plus the parallel lists `runs` and `names`. Design ids must be unique, so pass `id_of` from `project_design_ids` |
| `decode_fold(fold)` | a `FoldLookup` with the matrix and `row(run, design)` and `distance(a, b)` |
| `fold_labels(fold, cut=0.5)` | `{design id: "F<n>"}`: average-linkage clusters of all designs in the matrix, largest first. Pooled over runs and descriptive: this is `design.sc` |
| `build_fold_reps`, `fold_label_for`, `fold_label_details`, `fold_labels_for` | the label of a design that is not in the matrix (a rejected one): the label of its nearest accepted design by TM-align when TM is at least `1 - cut`, otherwise `unclustered`. The batch version uses a spawn pool |
| `diversity(run, fold, basis_cuts=None, ...)`, `diversity_all(runs, fold, ...)` | the per-run block and the `campaign.diversity` block `{schema, basis_default, distance_default, fold_weight, runs, pooled}` |
| `epoch_hash`, `epoch_components`, `epoch_differences` | settings epochs |
| `project_design_ids(runs)` | `{(run, id): project id}`: the id, or `<run>/<id>` when it is not unique across the project |
| `average_linkage`, `chao_jost`, `coverage`, `hill`, `wilson`, `level_for`, `advice`, `curve` | the statistics, as used by the browser |
| `pitfall_flags`, `chronology`, `traj_info` | flags, what is known about the order, the trajectory unit |

A per-run diversity block holds `run`, `n_designs`, `n_families`, `estimates_valid`, `distance`, `clusters` (basis to a list of `{id, size, fams}`), `counts` (basis to `{n, S, f1, f2}`), `sweep`, `advice` (basis to the readout of the estimates section, with `yield {p, lo, hi}`), `replay`, `replay_by_basis`, `order`, `epoch` and `flags`. The contract is in [data_contract.md](data_contract.md) and the schema `schema/campaign.schema.json`.

The module also runs from a shell on BindCraft 1 run folders:

```
python -m binder_review.families --run run01=PATH[,SETTINGS.json] --run run02=PATH \
    --construct construct.pdb --offset N --out DIR [--rejected]
```

`--offset` is the number to add to a construct number to get the reference number, needed only where the target frame has not set it.

## Scaffolded runs and epitope spread

A campaign built on a fixed scaffold (a nanobody, scFv, Fab or ankyrin repeat; the BindCraft 2 reader marks it with `annotations["scaffold"]`, see [paratope.md](paratope.md)) produces designs that are the same fold by construction, so fold similarity measures the scaffold. On a real scaffolded VHH campaign the fold basis puts every family in one cluster and the default `both` basis does too, which reads as full coverage and says only "these share a framework". For such a run `diversity()` switches the fold-based figures off instead of reporting them:

- the bases that include fold distance (`fold` and `both`) are removed from `clusters`, `counts`, `sweep`, `advice` and `replay_by_basis`; the `site` basis is exactly what it was, and `replay` is its replay;
- the block gains `fold` = `{status: "off", why: ...}`, `basis_off` = `["fold", "both"]`, `basis_default` = `"site"` and a `fold_off` flag;
- the pooled block follows when every run of the project is scaffolded, and carries a `fold_scaffolded` flag when only some are (the pooled fold and combined counts then mix a scaffold-bound fold with other runs and stay descriptive);
- the fold matrix itself (`campaign.fold`, the map) is untouched.

The three things that do vary are `runs[].diversity_paratope` (built by `diversity_paratope`, see [paratope.md](paratope.md)): which designed segments touch the target, the length class of the loop the campaign let vary (`loop_length_classes`: a class is a chain of lengths each within `LOOP_CLASS_GAP` = 3 residues of the next, a convention written beside the result) and the epitope spread.

**Epitope spread** (`epitope_diversity`) is computed for **every** run, scaffolded or not, and written to `diversity.runs.<id>.epitope`. It does not depend on the fold or the modality, which is why it is the axis that still means something when fold does not. The epitope of a family is the set of target residues contacted by any of its accepted designs (not the "at least half" of the footprint: a family's reach, not its core). The block gives `pairwise_jaccard` (`[lowest, highest]` over pairs of families), `shared_by_all` (residues every family contacts), `sizes`, `n_families`, `source` and `numbering`. The residues are BindCraft 2's own `Interface_Target_Residues` when every accepted design carries it, else the contacts recomputed from the structures (4.5 angstrom); a run never mixes the two, and `source` says which. Status `unknown`, with a reason, when fewer than two families have an epitope or no residues can be taken; it is never drawn as zero. Below 30 families the block says `low_confidence` and why. Measured on real campaigns, the pairwise overlap and the residues shared by all families separate runs that fold cannot tell apart; the values are not reproduced here, because they come from unpublished work.

## Limits

- Estimates assume that families are exchangeable draws. BindCraft restarts, the per-backbone cap and changing GPU queues break that, and the output files cannot show it.
- Nothing is estimated per attempted trajectory. BindCraft 1 keeps rows only for trajectories that finished and were scored, and the reader does not use the `Trajectory/` folders that would show every attempt.
- TM normalised by the shorter chain is the larger of the two normalisations, so it is generous for short binders. Look at the distance sweep before trusting a cluster count at a loose cut.
- **Fold distance is not reported at all for short, cyclic or multi-chain binders** (`families.fold_unreliable`, `FOLD_MIN_LENGTH = 30`). TM-align normalises by `d0 = 1.24*(L-15)^(1/3) - 1.8`: at `L <= 15` that is negative and the implementation clamps `d0` to 0.5, and `d0` only rises above the clamp past `L ~ 21`, so a score on a short chain is not length-calibrated. The 0.5 cut it would be read with ("the same fold") was calibrated on protein domains, and two unrelated 15-mers that are both extended, or both helical, clear it routinely. A cyclic binder is refused for a second, independent reason: it has no chain ends, and TM-align compares it as an open CA trace. A binder written as several chains is refused for a third: fold distance compares one CA trace, and run end to end that trace would jump from the last residue of one chain to the first of the next and align a connection the molecule does not have. In every case the run's block is switched to the site basis exactly as a scaffolded run's is, so the clusters and the coverage estimate come from where on the target each design binds -- which does not depend on the binder's own fold -- and the family (the backbone) is unaffected.
- `fold_label_for` is a nearest-neighbour label, not a re-clustering, so a rejected design can take the label of a cluster it would not have joined under average linkage.
- The fold matrix compares every pair of accepted designs, so its cost grows with the square of their number.
- Fewer than 30 families give wide uncertainty whatever the label says.

## See also

[viewer_board.md](viewer_board.md) · [modes.md](modes.md) · [data_contract.md](data_contract.md) · [structure_api.md](structure_api.md) · [target_frame.md](target_frame.md) · [architecture.md](architecture.md)
