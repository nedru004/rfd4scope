# Map page

`viewer/pages/map.html` shows where the designs of a campaign bind: which filters remove designs, which target residues each binding mode touches, and which diverse set of designs to order. This document is for a developer or an advanced user; it describes what the page does first and the data it reads second, and leaves shared code (checks, presets, run filter, profile helpers) to [viewer.md](viewer.md). Binding modes themselves are defined in [modes.md](modes.md).

## What the page shows

- **Funnel and thresholds (top).** One button per stage with the count that passes it, the drop from the stage before and the limits in words. Clicking a stage keeps designs that pass it in full colour and fades the rest. The **Thresholds** select holds the built-in presets plus `checks.presets` and starts on `checks.preset_default`; changing it recounts the funnel and the picks. A note counts the designs in full colour, with the region you brushed, and, with several runs, the count of each run.
- **Epitope map.** The target in 3D, coloured by how many designs in scope contact each residue (**Contacts**) or by conservation grade (**Conservation**). **Binders** overlays up to seven best designs of the scope, **Surface** shows the target alone. Hotspot markers are those of the runs in view (a hotspot that only some runs have carries the run tag); glycan sites are diamonds; residues that no run in view has in its target fade out. Drag to rotate, wheel to zoom, double-click or the reset button to restore the view.
- **Contact strip.** Contact frequency by residue along the target. Drag across it to keep only designs that touch that region (a chip clears it); a click without moving clears it too. Residues a run lacks are hatched: strongly (labelled `removed · r02`) when every run in view lacks them, lightly (`not in r02`) when only some do; runs are named by a short tag such as `r02`. A shaded band marks the membrane side: `membrane.strip_shade` (residue ranges), else the 10 Å of the target nearest the membrane plane when `membrane` is on.
- **Binding modes.** Four views, chosen with the buttons above the plot:
  - **Map**: designs on a two-dimensional embedding (`design.emb`) with a hull per mode and one marker shape per mode. Size is the headline score (ipSAE, or i_pTM without `ipsae`; `view.map_size_metric` can force i_pTM), small and faint = NO PASS, ring outline = PASS or REVIEW. A dashed ring marks a design that touches none of its run's hotspots. Drag over empty space to select a custom group. Hovering a marker shows a card with a thumbnail and the main scores.
  - **Fingerprints**: one row per design, one column per target residue.
  - **By mode**: one row per mode, colour = share of the mode's designs in view that contact the residue, with the hatching and hotspots of the runs in view.
  - **Table**: the 80 best designs that pass the selected stage.
- **Legend.** One button per mode in view with its count (`live/all` when a stage or region fades some designs); a click selects the mode. Modes without a design in view are left out.
- **Mode detail.** For the selected mode, a custom selection or all modes: the mode's note (`campaign.modes[m].note`, else `profile.modes.notes`), counts (in view, PASS or REVIEW, median headline score, on epitope), cells by capability (below), by-run counts, the residues touched most, **Hotspots touched** per hotspot (the denominator is the designs whose own run has it), **Context** (median and closest approach to the other subunit and to the rest of the protein), **Specificity** (conservation and identity bars for the species of `profile.specificity.show`, the focus paralog and the closest other paralogs), the four best designs (star to shortlist) and the picks.
- **Quality-diversity picks.** **Budget** (4 to 24), **Diversity** (0 to 1) and **Pool** (designs on the intended epitope, or any confident design that fits its surroundings). Greedy selection maximises `(1 - a) x quality + a x 0.9 x distance to what is already picked`; distance is the footprint overlap, and sequences of one backbone count as near-duplicates. Buttons: **Add picks to shortlist**, **Show only picks** (the last funnel stage) and **Copy FASTA** (clipboard, needs `design.seq`).

Cells of the mode detail by capability: Backbones and Folds (`families`) or Classes; Membrane-side (`families` and `membrane`); Pose flips (`complex_rmsd`); Clash (`olig`); Flagged (`flag_region`, metric `<flag_region.key>_n`); Rest overlap and Cut ends (`rest_ctx`); Wrong site (per-run data); Rejected (`rejected`); Bound (`outcomes`).

Funnel stages: All designs; Fold confident (fold check); Interface confident (fold and interface); Pose-consistent (only with `complex_rmsd` or `binder_rmsd`); Fits its surroundings (only with `olig` or `rest_ctx`; the Context check does not fail); On the intended epitope (at least `min(preset hotspots, hot_of)` hotspots of the design's own run; a run without hotspots has `hot_of` 0 and passes it, and its limit text reads "of 0 hotspots touched"); Diverse picks. Most stage labels and limit texts come from the `map.stage.*` text keys, with defaults in the page.

## Run filter, rejected designs, lab results

- **Runs** in the header (several runs, `multi_run`) restricts the page to one run: its hotspots, removed regions, counts and legend. With all runs the view is pooled: per-run counts sit under the funnel and the page says that the pooled view is descriptive only.
- **Show rejected (n)** (`rejected` on and the blob holds rejected designs with structures) draws them as hollow marks that can be hovered and clicked. They never count in the funnel, the picks or the stats and follow the run filter. The button is disabled when the runs in view have none.
- **Show lab results** (only with `outcomes` on) rings the designs that bound and adds a per-mode lab panel and a Lab column. The weighted wording needs `weights` as well.

## Keys

Active only when the blob has `campaign.runs[]` (every bundle written by the tool does). Ignored in form fields and with Ctrl, Alt or Cmd held.

| Key | Action |
|---|---|
| `1` to `9` | pick the nth binding mode in view (again to clear) |
| left, right | step through the modes |
| `M`, `F`, `B`, `T` | views: map, fingerprints, by mode, table |
| `R` | show or hide rejected designs |
| `Esc` | clear the brush, the residue region and the focus |

## Decisions

The page neither reads nor writes review decisions. Stars (and **Add picks to shortlist**) build a shortlist that is only a count in the page: it is lost on reload and has no export. **Copy FASTA** copies the picks. Use the board or the query page to record decisions. Design ids in the table, the best-designs list and the picks link to the inspector (`inspector.html#d=<id>`).

## URL hash

`map.html#m=<mode>` opens a mode, `#d=<design id>` focuses a design and its mode (the inspector links back this way), `#fp`, `#tbl` or `#v=<map|fp|hm|tbl>` open a view (`hm`, the by-mode heat map, only with per-run data), `#light` and `#dark` set the theme.

## Data the page reads

- Blobs: `data/campaign.js`, `data/homolog.js` and `data/manifest.js`. The homolog blob is required: without it the page stops with a message. With `species_panel` off the page ignores its ortholog and paralog content.
- `campaign.target`: `resi`, `resn`, `ss`, `ca`, `atoms`, `sites.A1.normal` (the view is oriented by it), `hotspots`, `hotspots_by_run`, `glycan`, `glycan_xyz`, `membrane` (`axis`, `centroid`). `campaign.runs[]`: `id`, `target.removed`. `campaign.modes[m]`: `name`, `note`, `by_run`.
- Per design: `emb`, `mode`, `tc`, `hot`, `hot_of`, `m.*`, `status`, `run`, `bb`, `sc`, `seq`, `liab`, `ca`, `view`, `vc`; with `outcomes` also `outcome` and `wt`. `homolog`: `resis`, `seq`, `grade`, `orth`, `par`, `n_orth`, `show`, `species`, `subject`.
- Capability flags: `ipsae`, `min_ipae`, `complex_rmsd`, `binder_rmsd`, `dg`, `families`, `membrane`, `olig`, `rest_ctx`, `flag_region`, `rejected`, `outcomes`, `weights`.
- Profile: `view.map_size_metric`, `membrane.strip_shade`, `modes.notes`, `specificity` (`species[]` with `table`, `card`, `phrase`; `paralogs.focus` and `paralogs.bars_other`; `show`; `subject`; `panel_noun`), `flag_region.key`, `target.uniprot`, `kind`, `credit`, `checks.*`, `tips.map`, the text keys `crumb.map.*` and `map.*` (each has a default in the page).

Per-run behaviour (hatching, the by-mode view, wrong-site marks, hotspot tags, the keys) is on only when `campaign.runs[]` is present. With one run there are no run tags and no by-run counts.

## When data is missing

| Missing or off | What changes |
|---|---|
| `species_panel`, `paralog_panel` (no specificity data) | The Specificity block, the species columns of the table and the hover card, and the paralog bars are left out. The Conservation colour stays selectable but draws the base colour. |
| `olig`, `rest_ctx` | No context stage, stats or block. |
| `complex_rmsd` and `binder_rmsd` | No pose stage. |
| `ipsae`, `min_ipae` | The headline score is i_pTM; the second metric is ipTM or i_pAE. |
| `dg`, `membrane` | No ΔG column or membrane stat; the strip band comes only from `membrane.strip_shade`. |
| a source that reports fewer metrics (for example a BindCraft 2 run, see [bindcraft2_reader.md](bindcraft2_reader.md)) | The flags of the missing metrics are false and their columns, cells and stages go. A missing value never fails a design. |
| no design in view (an empty run, or a run filter that leaves none) | Each view says "No designs to show" and nothing throws. |

## Limits

- The table lists 80 designs, the best-designs list four; the picks budget stops at 24.
- The embedding is the exporter's; marker positions get a small deterministic jitter so that equal points do not hide each other.
- The pooled view mixes designs of runs whose targets and hotspots differ, which is why hatching and run tags are drawn.
- Hotspot percentages and mode counts are descriptive. Nothing on this page is an estimate of what more designs would bring (that is on the board).

## See also

[viewer.md](viewer.md) · [viewer_board.md](viewer_board.md) · [viewer_inspector.md](viewer_inspector.md) · [viewer_query.md](viewer_query.md) · [modes.md](modes.md) · [specificity.md](specificity.md) · [data_contract.md](data_contract.md)
