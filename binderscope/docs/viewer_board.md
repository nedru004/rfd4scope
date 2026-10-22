# Board page

`viewer/pages/board.html` is the working page of a review: it shows how a campaign went, lets one reviewer keep, maybe or reject backbone families with reasons, and builds the order list. This document is for a developer or an advanced user; it describes what the page does first and the data it reads second, and leaves the code shared with the other pages (decisions store, run filter, profile helpers, checks, verdict rule) to [viewer.md](viewer.md).

## The layout

Three columns and a drawer, in the order the work is done:

| | |
|---|---|
| **Left rail** (250 px) | One family per row: what is decided, what is left. The arrow keys walk it. |
| **Stage** (the rest) | The family being decided: its verdict and mode, the 3D view, K/M/X, then the sequence table, checks and reasons scrolling in at most a third of the column under them. |
| **Right column** (330 px) | What the decisions add up to: the order list, the reviewer model, what to review next. |
| **Drawer** (below, shut) | The campaign statistics: run funnel, diversity, whether more designs would pay, where a tag can go. |

The page fits one screen at 1512x900 and does not scroll; the viewer takes
whatever the column has left, so it is the largest thing on screen. Below
1400 px the right column wraps to a full-width row under the stage; below
1200 px the rail returns to a tile grid above the stage.

**Why this way.** The statistics used to have the whole first screen and the
review the bottom right corner: a 395 px column starting 957 px down, with a
393x248 viewer, barely larger than the 198x218 thumbnail on the tile that was
clicked to get there. A campaign's funnel is read once at the start of a
session; the decision under it is made for every family. The drawer remembers
whether it was left open, per browser.

## What the page shows

**Summary panels (in the drawer).**

- **Run funnel.** One row per stage of the run (`runs[].funnel`), bars on a log scale. A count that is not known reads "unknown" and has no bar; a derived count reads `~n` with "estimate". Below the bars, "Filters failed most": the first four entries of `runs[].failure` (BindCraft's own counters, most frequent first; they overlap because a sequence can fail several filters), or, for a run without counters, the checks of this page that fail most.
- **Backbone diversity.** Design clusters of the families of one run, by average-linkage clustering on `w x fold distance + (1 - w) x footprint distance`, cut at the similarity of the slider "Same cluster if" (0.30 to 0.70). "Count by" picks the weight: Backbone (`w` = 1), Binding site (`w` = 0) or Both (`w` = `diversity.fold_weight` when `has_fold` is on, else 0.5). Fold distance is 1 minus the TM-score of `campaign.fold`; footprint distance is 1 minus the overlap of the target residues that at least half of a family's sequences touch. The panel shows the cluster count, the cluster sizes (grey = seen once), the effective number of clusters (Hill number of order 1), singletons, the largest cluster's share of families and the number of binding modes.
- **What the designs bind with (scaffolded runs).** A run built on one scaffold (a nanobody, scFv, Fab or ankyrin-repeat campaign) has one fold by construction, so fold similarity measures the scaffold and the Backbone diversity card would read as a saturated campaign. When every run in view carries a paratope block (`runs[].paratope`, `paratope_rejected` or `diversity_paratope`), this card replaces the Backbone diversity card, and its first line says why: fold diversity is not shown, because every design is built on the same scaffold. A run without any of the three blocks (every de novo run) keeps the Backbone diversity card exactly as above. The card answers four questions in this order:
  - *Which loops, and is framework involved?* Two stacked bars of the interface residues by region, **accepted** over **rejected**, in one colour per designed segment (CDR1, CDR2, CDR3, or H1 to L3, or "designed segment n") and one for framework, with the FR2 face apart from the rest of the framework. Putting the rejected designs under the accepted ones is the point: on a real scaffolded campaign framework contact is a small share of the interface among accepted designs and several times larger among rejected ones, because the campaign set a mandatory `Off_Paratope_Contact_Fraction` filter, and a reader who sees only the accepted figure learns much less. The card says so in a sentence when the run's `filters` list that rule, and otherwise only that the gap is selection. Below the bars: the share of designs that touch the target with every designed segment (accepted against rejected), and the framework positions that still make contact in accepted designs, named in the scaffold's own numbering (`framework_positions`).
  - *Do the designs all bind the same way?* **Paratope** reads `diversity_paratope.paratope`: how many distinct region-use profiles the accepted designs have.
  - *Where does the variation lie?* **Loop length** (the segment `diversity_paratope.loop_length.segment`, usually CDR3) as one dot per family with the length classes the analysis found, and **Epitope** as the range of pairwise Jaccard overlap between family epitopes and the target residues every family touches. An axis whose `status` is `unknown` reads "not measured" with its reason; `off` reads "not shown" with its reason. A handful of families is too few to call a trend, so below 30 families the card carries the block's own low-confidence note.
  - *What to hold against it?* A footnote: regions are the campaign's own edit spec and not IMGT or Kabat numbering; the interface residues are BindCraft's own `Interface_Binder_Residues`, so the contact criterion is BindCraft's; an scFv or Fab block covers one chain only; designs that could not be placed on the scaffold are left out and counted; what the bundle does not carry (rejected-design comparison, axes) is listed as missing, never drawn as zero.

  With several runs in view each scaffolded run gets a compact block (bars, the two sentences and the footnote) inside the per-run list of the Backbone diversity card, next to the usual block of any de novo run; the Count-by switch and the slider stay for the runs that use them. The layout of the summary row gives the card more width while it is shown.
- **Where a tag can go.** Shown when any design carries a `term` block (`campaign.designs[].term`, [termini.md](termini.md)). One block per run in view, never pooled across runs: the format of the run (a VHH, a de novo binder; "assumed" when the run did not record it), then one row per tag size (His6, Twin-Strep, GFP / SNAP, Fc) with a bar of the run's **families** by the end to tag (C-terminus, either end, N-terminus, neither) and the four counts. Click a row to make that the tag size (the *Tag* switch in the toolbar does the same). Under the rows: for a format with a convention (a VHH is tagged at the C-terminus) how many families the geometry puts there for a His6 and for a Twin-Strep, which is a check on the analysis and not an input to it; what BindCraft was asked to do about the ends in this run (`runs[].term.designed_for`), so that a free end reads as confirmed or as discovered; a note when the sequences of some families do not all agree (a family is judged by what most of its sequences say, and by C, N, either, neither in that order when they are tied); whether the other subunits and the rest of the protein were part of the check; and the caveat that this is a prediction from one conformation. A cyclic peptide run says it has no free ends and shows no bars.
- **Do you need more designs?** Needs `has_traj` and families. It shows Chao-Jost coverage (the chance that the next passing family falls in a cluster already seen), the Chao1 lower bound for the number of clusters, the yield per trajectory with an 80 percent Wilson range, an accumulation curve with the Chao1 lower bound as a dashed line and a forecast zone (the forecast is the Chao-Jost extrapolation), and a **Replay** slider that shows what the tool would have said after the first n trajectories.
  - For a scaffolded run the clusters behind this panel are counted by binding site alone (weight 0 on fold), because the fold is the same for every design; a line under the panel says so. A run without a paratope block is clustered as before.
  - The plain-language level is "too early" (fewer than 10 families), "still discovering" (coverage below 0.7), "diminishing returns" (below 0.9) or "saturated"; below 30 families a low-confidence note is added.
  - The "more designs" step is 600 trajectories for a run of 800 or more, else half the run rounded to 50 (at least 50).
  - The unit is attempted trajectories when `runs[].n_traj` is known, else passed trajectories when `traj_basis` is `passed`.

**Tiles and the focus panel.**

- **Tile grid.** One tile per family (per design when `families` is off), drawn as it scrolls into view. The picture is the target with the family's representative sequence (the highest quality score). A pill at the bottom right (`tag C-term`, `tag N-term`, `tag N or C`, `no tag site`) gives the end to tag for the tag size in force, judged from what most of the family's sequences say; a star means the sequences do not all agree. Pills show the binding mode (`M1`...), the cluster (`C3`), a `pose` mark (confident, but the re-score places the binder elsewhere; needs `complex_rmsd`), the run (several runs) and `off-site` (touches none of the hotspots of its own run). Also on a tile: the verdict of that sequence, id, length, the headline score and `hotspots touched/of`, a muted metric line (`board.tile_metrics`), the sequence strip (one mark per sequence, coloured by verdict) and `p NN%`, the reviewer model's chance that you keep it (a bar under the tile).
- **Focus panel (the stage).** The selected family: verdict, mode and cluster, an **Inspect** link, **Compare**, a "why" list, a 3D view, the K, M and X buttons, the table of sequences (verdict, metrics, the end to tag, **Order** checkbox; rejected siblings follow, marked `rejected`, when `rejected` is on), a *Tag and fusion* section (for the selected sequence: the status of each end for the tag size in force with its distance to the target and its side of the binder, how many of the family's sequences fit, and the headline; a link opens the evidence in the Inspector), one row per check with the reason in words, and the reason tags.
- **Reviewer model.** A regularised logistic regression on your keeps and rejects, starting from prior weights. The **Learning** switch (key `L`) freezes it; decisions are still recorded. The panel shows decisions used, the eight largest weights, **Review next** (undecided families with the highest expected keep) and **Worth a look** (the model is unsure).
- **Order list.** Sits in the right column, beside the stage rather than under it. It says in words what the keeps add up to (for example 4 kept families give 5 sequences, which are 5 of their 9 accepted sequences), lists the sequences with their verdict and where each came from (`family`, `sequence` or `near-miss`), the diversity of the list (design clusters and binding modes), the maybes that are not in it, and has the file buttons listed under Controls. A kept family adds only the sequences ticked in its table, and its best one when none is ticked; a sequence kept on its own and a rescued near-miss are listed as well. The list is defined once in `BS.order` ([viewer.md](viewer.md), "The order list"), so the header chip on every page, this panel and the files agree. Under K, M and X a line says what keeping the focused item would add.

## Controls

| Control | Effect |
|---|---|
| Unit: Families, Sequences | Decide per family or per sequence. Hidden when `families` is off. |
| Sort: Learned, Quality, Generation order, Group | Learned puts your decided tiles first, then ranks the rest by `p`. Generation order is hidden without `has_traj`. |
| Group by: Cluster, Mode | Switches to the Group sort and heads each group (`run01 · Cluster C3`). Clusters are made per run, so `C3` belongs to its run. |
| Tag: His6, Twin-Strep, GFP / SNAP, Fc | The tag size the whole page judges by: the tile pills, the Tag column, the focus section, the summary card's highlighted row and the order-list note. Remembered in this browser. Shown when designs carry a `term` block. |
| Colour: Chain, pLDDT, Footprint | Colouring of the binder on tiles. pLDDT needs `design.pl`; Footprint marks the binder residues in contact with the target (`design.bc`). |
| Undecided only, Hide rejected | Filters. "Rejected" means rejected by you. |
| Search box (key `/`) | Filters tiles by design or family id; Esc clears. |
| Simulate a reviewer | Takes keep and reject decisions on a memory-only copy of the log to show how the ranking learns; it stops after 18 decisions (30 in the sequence unit or without families). Never saved. Present when `board.simulate` is true, which is the default unless the profile `kind` is `run`. |
| Clear decisions | Asks first, in a bar under the toolbar. Nothing is deleted: every decision gets a `clear` event. `U` right after restores them. |
| Share | The review-file dialog: **Save review file**, **Load review file**, the reviewer name, a reviewer picker when the log holds more than one reviewer, and whether decisions persist. |
| How to import a run | Explains how a run folder is read (from `profile.import`, else from the manifest's `sources`). It imports nothing. |
| What to try | The tips of `profile.tips.board`, else generic ones. |
| Compare (key `C`) | Up to five families or sequences on one target, with a table; the best value in each row is underlined. With one chosen, the two highest-ranked undecided ones that do not fail are added. |
| Order list buttons | **Tag plan CSV** (`tag-plan.csv`, with a `term` block: one row per design in the list with the end to tag for each tag size, the end to hold the binder by for SPR or BLI, and each end's distance, side, room and status for a His6, a Twin-Strep and an Fc, plus the basis; a design that was not judged says so, a cyclic peptide says it has no free ends), **Copy FASTA**, **FASTA file** (`order-list.fasta`), **CSV** (`order-list.csv`: `design_id, run, family, rank, verdict, bindcraft_rank, reasons, source`; `source` is `family`, `sequence` or `near-miss`), **Save review file**, **Load review file**, **Share review**. FASTA needs a `seq` on every design in the list and `export.sequences` not false. The two review buttons of this panel are hidden when `reviews` is off. The same buttons are in the dialog that the header chip opens on every page. |
| Show lab results | Only with `outcomes` on, see below. |

## Keys

Keys are ignored while a text field, slider or select has the focus, and with Ctrl, Alt or Cmd held.

| Key | Action |
|---|---|
| arrows | move between tiles |
| `K`, `M`, `X` | keep, maybe, reject the focused item; the same key again clears it; the focus then moves to the next undecided item |
| `1` to `7` | tag a reason on a decided item (after a decision the tag goes to the item decided last) |
| `U` | undo the last decision of this page session |
| `L` | learning on or off |
| `C` | add to or remove from Compare |
| `[` and `]` | step through the sequences of the focused family |
| `Enter`, double-click | open the focused sequence in the inspector |
| `/` | focus the search box |
| `?` | list of shortcuts |
| `Esc` | close a dialog, the clear bar or the search |

## Verdicts and reasons

PASS, REVIEW and NO PASS come from `Common.evalChecks` with the threshold preset in force; the rule and the presets are in [viewer.md](viewer.md). A Context reason that is advisory (the default unless the reference is experimental) carries the words "advisory, it does not decide the verdict", and the reasons list ends with a line saying why and, for a design BindCraft accepted, that the verdict is Binderscope's own screen and not a ruling on BindCraft's. Each check row says why in words, and a REVIEW or NO PASS design gets a "because" list under its verdict; the same words are the hover of a tile. A design that touches none of its run's hotspots gets an `off-site` reason; the epitope check then fails, but it is a soft check, so the verdict is REVIEW unless a hard check also fails. The Context row appears when `olig` or `rest_ctx` is on and reports the worst of the other subunit, the rest of the protein, the cut ends and the covered residues. The Specificity row (only with `species_panel`) gives the share of the contacted residues that are identical in the species of `profile.specificity.show`, in the focus paralog and in the best other family member. It is information and never enters the verdict.

## Decisions and review files

Decisions are events in the shared append-only log ([viewer.md](viewer.md), "The decisions store"; format in [schema/review_event.schema.json](schema/review_event.schema.json)). In the family unit an event has `unit: "family"`, `design` = the family (backbone) id, `sequences` = the chosen design ids and `run`; in the sequence unit `unit: "design"`. A reason tag or a changed Order checkbox appends a new event with the same decision. `U` appends the event that restores the earlier state. The log, the reviewer and the learning switch are kept in the browser per bundle; **Save review file** downloads `<reviewer>.jsonl`, to be moved into the bundle's `reviews/` folder. **Load review file** merges by event id; a decision that Clear decisions removed here is brought back, invalid lines are reported with their line number, and events of another bundle are counted. A file of another reviewer is kept and shown through the reviewer picker; there is no merge view. With `reviews` off the page keeps decisions in memory only. When the browser blocks storage the page still works and the Share dialog says that decisions live in memory, so save the file before closing.

Designs that BindCraft rejected have no tile. A decision recorded for one (by the Query page or the Inspector, as a rescue) is a `unit: "design"` event. A rescued near-miss that is kept is in the order list, marked `near-miss`, and the panel says why it has no tile; one marked maybe is counted in the line about maybes, with a link to the Query page, and is not in the list.

## URL hash

`board.html#order` opens the order list dialog (used by the Overview's chip). `board.html#f=<family id>` focuses a family, `#d=<design id>` focuses a design (the family, with that sequence selected). Opening a link also clears the search, "Undecided only" and "Hide rejected", and switches the run filter to the run of the target when that run is hidden. An unknown id gives a notice. `#light` and `#dark` set the theme.

## Data the page reads

- Blobs: `data/campaign.js` and `data/manifest.js`. `data/homolog.js` is loaded after start when `species_panel` is on and the manifest lists it.
- `campaign.runs[]`: `id`, `label`, `source`, `settings.hotspots`, `settings.length_range`, `traj_basis`, `n_traj`, `funnel[]`, `failure[]`, `target.hotspots`. `campaign.diversity`: `basis_default`, `distance_default`, `fold_weight`. `campaign.fold` with `has_fold`. `campaign.modes` (names). `campaign.target`: `resi`, `resn`, `ca`, `atoms`, `sites.A1.normal`, `hotspots`.
- Scaffolded runs (all optional; see [../../PARATOPE-CONTRACT.md](../../PARATOPE-CONTRACT.md) for the shape): `campaign.runs[].paratope` and `paratope_rejected` (`modality`, `scaffold`, `naming`, `segments[]` with `name`, `scaffold`, `designed`, `length`, `variable`; `share` by region in percent of interface residues; `framework_share`; `loop_use[]` with `loops`, `n`, `share`; `framework_positions[]` with `region`, `scaffold`, `aa`, `n`; `aligned`, `unaligned`, `no_interface`, `chains`, `notes`, `note`) and `diversity_paratope` (`fold.{status,why}`, `paratope`, `loop_length` with `segment`, `values`, `classes`, `class_ranges`, `class_families`, `epitope` with `pairwise_jaccard`, `shared_by_all`, `n_families`; `low_confidence`, `note`). `runs[].filters` supplies the sentence about the off-paratope filter. The page never reads `designs[].para`; the inspector does.
- Terminus analysis (optional, [termini.md](termini.md)): `campaign.runs[].term` (`format`, `designed_for`, `tags`, `const`, `env`, `partner`) and per design `term` (`applies`, `n` and `c` with `d`, `side`, `room`, `g` and the evidence, `pick`, `immob`, `env`). Present for accepted designs and near-miss rescues only.
- Per design: `run`, `status`, `bb`, `s`, `traj`, `mode`, `sc`, `len`, `seq`, `ca`, `view`, `vc`, `tc`, `bc`, `hot`, `hot_of`, `m.*`, `liab`, `pl`, `fail`, `inspect`, `prov.rank`; with `outcomes` also `outcome` and `wt`.
- Capability flags: `families`, `has_traj`, `has_fold`, `multi_run`, `rejected`, `reviews`, `species_panel`, `outcomes`; the context flags `olig`, `rest_ctx`, `flag_region`, `footprints` and `membrane` (reviewer features and check rows); the metric flags `ipsae`, `min_ipae`, `iptm_b`, `ipae_norm`, `ciplddt`, `complex_rmsd`, `binder_rmsd`, `hotspot_rmsd`, `dg`, `sc`, `n_agree`, `seed_spread`, `esm_plddt`.
- Profile: `kind`, `reasons` (list of `[key, label]`, at most nine), `view.map_size_metric`, `import`, `credit`, `badge`, `tips.board`, `checks.*`, `export.sequences`, the text keys `crumb.board.*`, `board.*` and `features.*` (each has a default in the page). An optional `board` section (not in the profile schema, which allows extra sections) tunes the page; each list entry is a key from the page's own list of keys:

| Key | Meaning | Accepted entries |
|---|---|---|
| `board.features` | `[[key, prior weight, label?], ...]`, the reviewer-model features | `ipsae`, `min_ipae`, `iptm`, `iptm_b`, `ipae_norm`, `plddt`, `ciplddt`, `complex_rmsd`, `brmsd`, `dsasa`, `sc`, `dg`, `hb`, `unsat`, `unsatd`, `patch`, `loops`, `hot`, `epi`, `foot`, `partner`, `rest`, `cut`, `flag`, `n_agree`, `seed_spread`, `liab`, `esm`, `helix`, `mem` |
| `board.compare` | rows of the Compare table | `ipsae`, `min_ipae`, `iptm`, `iptm_b`, `ipae`, `plddt`, `ciplddt`, `complex_rmsd`, `brmsd`, `hs_rmsd`, `dsasa`, `dg`, `sc`, `hb`, `unsat`, `unsatd`, `patch`, `hot`, `gap`, `rest_gap`, `flag`, `len` |
| `board.sib_cols` | metric columns of the sequence table | `iptm`, `iptm_b`, `ipsae`, `plddt`, `dg`, `sc`, `unsat`, `hs_rmsd`, `liab`, `lab` |
| `board.tile_metrics` | the muted metric line of a tile | `plddt`, `dg`, `sc`, `unsat`, `hs_rmsd`, `ipsae`, `iptm` |
| `board.simulate` | offer the simulated reviewer | true or false |

A feature, column or row whose flag is off is dropped from the list. Without a `board.features` entry the features follow the flags (for example no ipSAE feature without `ipsae`).

## When data is missing

| Missing or off | What changes |
|---|---|
| `families` | One tile per design; the Unit switch, cluster pills and the sibling wording go. |
| `has_traj` (no trajectory table in the run), or `traj_basis` `none` | No Generation order sort and no accumulation curve, replay or forecast; the panel says so. A run without an order keeps coverage and the Chao1 bound. |
| `runs[].n_traj` unknown (`traj_total` off; no `Trajectory/` folders) | The attempted-trajectory row reads "unknown", never 0. The BindCraft 1 reader does not read the `Trajectory/` folders, so this is its usual state; when the failure counters have trajectory entries it adds a derived estimate, marked "estimate". The yield and forecast then count passed trajectories (rows of the trajectory table). |
| `has_fold` | No TM-align matrix: the Backbone basis falls back to a coarse stand-in (same fold class, helix content), so read it as approximate. |
| `species_panel` | No Specificity row. |
| `olig`, `rest_ctx` (no full-length context in the export) | No Context check, no context reasons or features. The Context row stays, reading *not checked* with the reason from `profile.caps_off`, and the verdict's reasons end with a line saying Context was not checked, so a design that was never tested does not read as one that came back clear. |
| a metric flag (a source that does not report the metric, for example a BindCraft 2 run, see [bindcraft2_reader.md](bindcraft2_reader.md)) | The column, compare row, tile item and model feature disappear. Nothing is guessed and a missing value never fails a design. |
| `rejected` | No rejected sibling rows. |
| no `paratope`, `paratope_rejected` or `diversity_paratope` on a run | Not a scaffolded run: the Backbone diversity card, and the clustering behind the other panels, are exactly as for any de novo run. |
| `paratope_rejected` absent | The rejected bar reads "not analysed" and there is no contrast sentence: only the accepted framework share is given, with a note that there is nothing to compare it with. |
| `paratope` absent but `diversity_paratope` present (the analysis was off for the run) | No bars; the card gives the reason from `diversity_paratope.paratope.why` and shows the axes it has. |
| a field of a block absent or `null` (`framework_positions`, `loop_use`, a share, an axis) | That element reads "not given", "not listed" or "not measured"; a missing share is drawn as a dash and no bar segment, never as 0%. |
| no `term` on any design | No tag card, no *Tag* switch, no tile pills, no Tag column, no tag plan file. A bundle built before the analysis looks exactly as it did. |
| `term.applies` false (a cyclic peptide, a run with several binder chains) | The summary card gives the reason and no bars; the tile pill reads `no free ends`; the Tag column shows a dash. |
| a design without a `term` (a rejected design that is not a near-miss) | A dot in the Tag column, never a letter; the family is judged from the sequences that carry one. |
| `term.env` false (no reference structure) | The card and the focus section say the other subunits and the rest of the protein were not part of the check, and that the absence of a finding there says nothing. |
| `runs[].term.designed_for.known` false (BindCraft 1, no recorded settings) | The card makes no claim about what BindCraft was asked to do about the ends. |
| `outcomes` (always off for BindCraft run folders) | No lab features. With it on: **Show lab results** in the header, lab pills, a panel that compares the checks with the lab results, and statistics weighted by `design.wt` where present; **Simulate** then uses the lab results. |
| `multi_run` or one run | No run filter and no run badges. With several runs every estimate is per run and shown side by side. |

## Limits

- The board counts families, not sequences, and every score is a screen. The tag counts are of families, judged by what most of their sequences say; they are a prediction from one conformation of each complex, and the tag sizes are round figures ([termini.md](termini.md)).
- On a scaffolded run the paratope card reads regions from the campaign's own edit spec, not IMGT or Kabat numbering; its contacts are BindCraft's own column; an scFv or Fab block covers one chain only until the analysis reads both; and a handful of families is too few to call a trend.
- Estimates are per run and never pooled across runs with different settings.
- The reviewer model is a ranking aid for your own taste. It is re-fitted from the decision log each time the page loads.

## See also

[viewer.md](viewer.md) · [viewer_inspector.md](viewer_inspector.md) · [termini.md](termini.md) · [viewer_map.md](viewer_map.md) · [viewer_query.md](viewer_query.md) · [families.md](families.md) · [modes.md](modes.md) · [export.md](export.md) · [data_contract.md](data_contract.md) · [GUIDE.md](GUIDE.md)
