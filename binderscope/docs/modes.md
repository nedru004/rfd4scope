# Binding modes, hotspot coverage and near-miss rescue

`binder_review.modes` (`src/binder_review/modes.py`) groups designs by where they sit on the target (their contact footprints), lays them out on a two-dimensional map, reports how well each design and each mode covers the run's hotspots, and lists the rejected designs that narrowly missed BindCraft's filters. This page is for developers and advanced users: it gives the inputs, the method, the outputs, the near-miss rule and the limits.

Nothing in the module is specific to a target. Modes, names and notes are generated from the contacted residues, residue letters come from the runs' target sequences, and hotspots come from each run's settings. The module uses numpy, scipy and scikit-learn on top of `structure` and `model`, and needs no network.

## What goes in

| Input | Form | Notes |
|---|---|---|
| footprints | `{design key: contacted target residues}` | Reference numbers (the code and field names call them UniProt numbers), heavy atoms within `model.CONTACT_CUTOFF` (4.5 angstrom), for accepted designs and for rejected designs that have a structure. The build passes the contact sets of the placement stage (the same sets the pages use), so no structure is read twice. `collect_footprints(runs)` reads them again for standalone use, and needs `target.uniprot_offset` |
| centroids | `{design key: xyz}` or `None` | The mean CA of the contacted residues in one reference CA table, so no per-design placement is needed. `contact_centroids(footprints, ref_ca)` does the averaging. `reference_ca_from_construct(path, offset)` reads a construct file; any complete CA table by reference number works. An empty footprint gets the mean centroid and is flagged `no_contacts` |
| `runs_info` | `{run id: RunInfo}` | `hotspots` (reference numbers, `RunData.hotspots_uniprot()`), `designs` (`{key: "accepted" or "rejected"}`) and an optional `residues` set; a footprint residue outside it raises, which catches a numbering mistake |
| `residue_names` | `{number: letter}` | From `residue_names_from_runs(runs)`; used only to write labels such as `K48` (an invented label) |
| `sites` | `target.sites` | `{key: {"residues": [...]}}`; `None` builds a single site `A1` from the pooled hotspots (no site when there is no hotspot). The pipeline passes the sites of the target block, whose `A1` is built from where the designs bind when no run records a hotspot ([target_frame.md](target_frame.md)). Sites only name the site that a design or a mode overlaps; they never move a design between modes |

Design keys are the blob ids: `project_ids(runs)` gives the id itself and prefixes `run/` only when two runs share an id.

## Method

1. **Distance.** `D = 0.5 x (Jaccard distance of the footprints) + 1.0 x (centroid distance / the largest centroid distance among the accepted designs)`. Two empty footprints are 1 apart.
2. **Clusters.** Ward linkage on classical-MDS coordinates of `D` over the n accepted designs (up to ten dimensions). There is no random element. The number of modes `k` is the silhouette-best value from 3 to 7 (the pages have seven colours and shapes, and the schema allows `M1` to `M7`) under a balance rule: no mode above 40 percent of the designs (`max_share`) and none below 3 designs (`min_size`, lowered to `n // 8`, at least 1, for fewer than 24 designs). Silhouette alone favours small `k` with one dominant mode, which does not help triage. If no `k` satisfies both rules the size limit is dropped, then both, and `k_selection.rule` says which applied. Fewer than three designs form one mode, and `k` never exceeds `n - 1`. Modes are ordered by size (ties by the mean contacted residue number) and named `M1`, `M2`, and so on.
3. **Rejected designs.** A rejected design is assigned to the mode with the smallest mean of its (up to three) smallest distances to that mode's accepted members. Rejected designs never enter the clustering, the silhouette or the embedding.
4. **Embedding.** t-SNE on `D` (precomputed metric, classical-MDS start, fixed seed 5, perplexity `(n - 1) / 3` clipped to 5 to 30, with n the number of accepted designs) with each axis scaled to 0 to 1, for ten or more accepted designs. Below ten, classical MDS replaces it, and below three the layout is trivial. A rejected design is placed at the distance-weighted mean of the positions of its three nearest accepted designs plus a deterministic jitter, at most 0.04, that grows with its distance to the nearest accepted design so that coincident points stay readable. The positions of accepted designs are exactly those of the embedding.
5. **Hotspot coverage.** For each design, `hot` is its footprint intersected with the hotspots of its own run (so it equals the exporter's `design.hot`), `hot_of` is the number of hotspots of that run, `frac = len(hot) / hot_of`, and `wrong_site` is true when the run has hotspots and the design touches none of them. A run without hotspots has `frac` null and no wrong sites.
6. **Names and notes.** A name lists up to three enriched residues by letter and number in numeric order, followed by `site`, for example `K48/D52/T55 site` (invented residues). A residue is enriched when its contact share inside the mode exceeds its share outside by at least 0.2; when no residue qualifies, the three most contacted residues are used. When none of the named residues is a hotspot and the nearest hotspot to the mode's mean contact centroid is within 12 angstrom, `· near <hotspot>` is added if the name stays within 40 characters. A duplicate name gets another enriched residue, then a counter. The note is built from numbers only: how many accepted designs and in which runs, the touch rate of each hotspot, designs touching none, the enriched residues, the overlapped site and how many rejected designs fall nearest. Names are labels for finding a group, not a claim about a biological site.
7. **Site.** A design's site is the site that holds the largest share of the design's footprint (ties by key); with no overlap, the site whose residue centroid is nearest. A mode's site is chosen the same way from the residues contacted by at least half of its designs.

## What comes out

`build_modes(footprints, centroids, runs_info, *, residue_names=None, ref_ca=None, sites=None, k_range=(3, 7), max_share=0.4, min_size=3, seed=5)` returns a `ModeResult`; `to_dict()` is JSON-serialisable. `build_from_runs(runs, ref_ca, sites=None)` does the footprints and the modes for `RunData` objects and also returns the `(run, id) -> key` map.

| Key | Content |
|---|---|
| `modes` | The campaign `modes` block, `M1` to `Mk` by size, never more than seven. Each mode has `name`, `note`, `n` (accepted designs), `n_rejected`, `by_run`, `top` (the eight most contacted residues), `enr` (up to six enriched residues), `site` and `auto_name: true`, plus the extra keys `hot_share`, `n_wrong_site`, `mean_hot_frac` and `nearest_hot`. `hot_share` maps a hotspot number (as a string) to the share of the mode's designs, among those whose run has that hotspot, that touch it. `nearest_hot` gives, per run, the hotspot nearest to the mode's mean contact centroid and its distance in angstrom |
| `assign` | `{key: {mode, site, emb: [u, v], status, run}}` for every design given. Rejected designs also carry `mode_dist`, and a design with an empty footprint carries `no_contacts` |
| `hotspots_touched` | `{key: {run, hot, hot_of, frac, wrong_site}}` |
| `k_selection` | `k`, `silhouette`, `rule`, `method` and `table` (the silhouette and the cluster sizes of every `k` tried) |
| `embedding` | `method` (`tsne`, `mds` or `trivial`), `seed`, the numbers of accepted and rejected designs and how rejected designs are placed |
| `params` | The weights and cut-offs used, `centroid_scale` and the keys without contacts |

The `modes` block validates against `schema/campaign.schema.json`; the exporter writes `design.mode`, `design.site` and `design.emb` from `assign`. See [data_contract.md](data_contract.md) and [viewer_map.md](viewer_map.md).

## Quality-diversity picks (in the viewer)

The Map page's *Quality-diversity picks* panel suggests a short list of designs that are both good and different from each other. The selection runs in the browser (`C.select` and `C.jaccardDist` in `viewer/js/common.js`), not in `modes.py`, and it works on the same contact footprints. See [viewer_map.md](viewer_map.md) for the panel.

- **Inputs.** A pool of designs (the panel offers the designs on the intended epitope, or any confident design that fits its surroundings; rejected designs are never in it), a budget `k` (slider 4 to 24, default 8) and a diversity weight `alpha` (slider 0 to 1, default 0.5).
- **Greedy selection.** Each step picks the design with the highest `(1 - alpha) x q + alpha x 0.9 x d`, where `q` is the design's `design.q` (higher for a better BindCraft rank within its run, see `families.quality_scores`) and `d` is its smallest distance to the designs already picked (1 for the first pick). With `alpha` 0 the picks follow quality alone; with `alpha` 1 they follow distance alone.
- **Distance.** The Jaccard distance of the two designs' contacted target residues, scaled so that siblings on one backbone count as near-duplicates (times 0.35), designs with the same fold label are slightly closer (times 0.92) and designs with different fold labels slightly further apart (plus 0.04, at most 1).

The panel then reports how many picks fall in each binding mode, backbone and fold. The mode labels are used for that report only: they play no part in the distance.

## Near-miss rescue

A near miss is a rejected design that failed a single filter by a small margin, so it may deserve a second look. `rescue_records(run, rules=None, *, include_metric_only=False)` (`rules` default to `config.filters_for_run(run)`: the thresholds the run recorded, or the reconstructed BindCraft 1 defaults) returns `{design id: {"fail": [...], "nm": {...}}}` for every rejected design that has a structure; `include_metric_only=True` adds the rejected rows without a structure file, which the Query statistics use.

- `fail[]` has one entry per failed rule and scope: `stem`, `metric` (the page metric key, or null), `scope` (`Average` or a model number), `value` (BindCraft's number), `threshold`, `op`, `gap` (positive, in the metric's unit) and `rel` (`gap / |threshold|`). Values are rounded to 6 decimals.
- `nm` has `n_failed` (distinct stems; the Average and per-model scopes of one stem count once), `stems`, `worst_rel` (the largest `rel`), `score = max(0, 1 - worst_rel) / n_failed` and `near`. It is `None` when the design fails no rule, which means BindCraft rejected it for a reason that the rules do not cover.

**The rule.** `near` is true when exactly one stem failed (`model.NEAR_MISS_MAX_STEMS`) and it missed its threshold by at most 25 percent of the threshold (`model.NEAR_MISS_MAX_REL`, 0.25). Thresholds are inclusive. BindCraft's CSVs round many values to two decimals, so a gap of 0.01 can be the resolution of the data. The Query page tags and filters on `nm.near`, and the exporter gives near-miss designs an inspector record like accepted ones.

For a run on the default set, and for rules that a caller passes in, the records are computed from the CSV values by `config.check_filters`, not copied from `DesignRecord.failed_filters`, so a custom list of rules works; nothing about a BindCraft 1 run depends on the run's recorded failures.

**A run that recorded its thresholds (BindCraft 2).** `config.filters_for_run` builds the set from `annotations["filters"]`: one rule per recorded threshold on a metric Binderscope carries (provenance `campaign`, the campaign's own filter name as the stem, the values read from `value_stem`), unevaluated entries for the recorded thresholds on metrics it does not carry, and the infinite thresholds as no limit. The failures of a design are then `DesignRecord.failed_filters`, which is BindCraft's own verdict as its reader recorded it, plus any stem BindCraft did not name for which the CSV mean fails a recorded threshold (BindCraft stops at the first validation model that fails, so `failed_filters` is not everything a design misses). A stem is never listed twice and a failure BindCraft named is never removed: BindCraft judges confidence filters on each validation model while the CSV holds the rounded mean, so a design can fail `pTM` with a CSV `pTM` above the threshold, and that failure has scope `Model`, gap 0.0. Such a failure is a lower bound, not a measured gap. In `fail[]` it has `measured: false` and `rel: null`, and `nm` is not near: `near` is false, `score` is 0 and `unmeasured` names the stems (the same holds for a name BindCraft reported that no rule can score, such as a mandatory metric that was not measured). Each `fail[]` entry of such a run also has `reported` (BindCraft named it) and `measured`. The count of near misses, the rescue list, `rescue_summary` and the accepted-design check all use this one set, so the funnel adds up.

| Function | Use |
|---|---|
| `rescue_order(records)` | design ids from the closest miss to the furthest: near misses first, then by `score` (higher first), then id; designs that fail nothing come last |
| `accepted_failures(run, rules=None)` | accepted designs that fail a rule. It should be empty; if it is not, the rule set does not match the filters the run used (this is how it was seen that BindCraft 1's `Surface_Hydrophobicity` rule did not belong to a BindCraft 2 campaign) |
| `rescue_summary(run, rules=None)` | per rule, the number of not-accepted rows that fail it, next to BindCraft's own failure counter, plus `n_near`, `by_n_failed` and `no_failure` (and `filters`: source, unevaluated and unlimited names, for a run judged by its recorded thresholds) |

BindCraft applies its AlphaFold2 pre-filters (pLDDT, i_pTM, i_pAE) before a sequence gets a row, so such failures leave a counter but no row and no structure. A near-miss list can therefore only contain failures of the rules that act on scored sequences.

## Limits

- Modes describe where on the target a binder sits, not whether it binds. They are a screen for triage and diversity, not a verdict.
- The clustering is pooled over all runs, which can differ in hotspots, length range and construct. Footprints are in reference numbers, so they are comparable, but a mode that holds the designs of one run only may reflect that run's settings. `by_run` shows it.
- `k` and the names depend on the balance rule and the enrichment threshold; other parameters give other groupings. `params` and `k_selection` record what was used.
- The map position of a rejected design is an estimate between accepted neighbours and carries jitter; only accepted positions come from the embedding.
- Contact centroids use one reference CA table, so they ignore the small per-design shifts of the target chain.
- `wrong_site` needs hotspots; a run whose hotspots were not recorded has none.
- t-SNE is deterministic for a fixed seed and library version, but a different scikit-learn version can move the accepted points.

## See also

[families.md](families.md) · [viewer_map.md](viewer_map.md) · [viewer_query.md](viewer_query.md) · [data_contract.md](data_contract.md) · [target_frame.md](target_frame.md) · [architecture.md](architecture.md)
