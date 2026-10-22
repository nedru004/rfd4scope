# Query page

`viewer/pages/query.html` filters the designs of a bundle with a stack of editable rules, shows what each rule and group of rules removes, rescues near-misses (including designs BindCraft rejected) onto the board, and exports any list as CSV, FASTA or a saved rule set. This document is for a developer or an advanced user: behaviour first, then the data contract; shared code (checks, presets, run filter, decisions) is in [viewer.md](viewer.md).

## What the page shows

- **Ask bar.** Typed keywords compiled into the same rules; no language model is involved (see "Plain-words queries"). **Add to current rules** keeps the rules already on, **Clear all rules** turns every rule off, and **Undo** (shown after a compile or a rule-set load) restores the earlier state. The example chips come from `nl.examples`, else four generic ones.
- **Rules (left).** Groups, each with "n of m on". Per rule: a checkbox, the label, `pass/total`, and "kills n alone" (designs that fail only this rule). A slider sets the limit; the `pI` rule has two, a set rule has chips (binding modes, runs), a yes/no rule has none. A design with no value for a rule passes it. Clicking the label opens a histogram, what relaxing or tightening the limit would change ("+n designs", new backbones), the rule's note and, with several runs, a selector that scopes the rule to one run (the other runs pass it). The **preset** select applies the thresholds of a preset to the matching rules and turns them on.
- **Funnel (middle).** The pool, then one row per group with the designs left after that group and every group before it, and the loss. Under it, "Rules blocking the most near-misses": the rules that alone remove the most designs; a click opens the near-miss list for that rule. **Copy recipe** puts the rule set on the clipboard as JSON (`target`, `hotspots`, `forbidden`, and per rule `op` and `value`, `min` and `max` or `values`, plus `run` when scoped).
- **Results.** Three tabs: **Survivors** (fail no rule), **Near-misses** (fail exactly one) and **Failing 2+ rules** (called "Rejected" when the profile is not `kind: run`). Survivors are tiles (best 60 by the page's quality score, with a 3D thumbnail, the headline score and `hotspots/required`); near-misses (40 rows) and failing designs (60 rows) are tables. The counts in the tabs are not limited. A click on a tile or row draws the design on the target.
- **Pick residues on the target (right).** The target in 3D. **Require** or **Forbid** decides what a click on a residue does: a required residue (orange) feeds the Hotspots rule, a forbidden one (red) feeds "Stays off chosen residues". That rule follows its set: the first forbidden residue switches it on and the last one off, since an empty one passes every design; a box ticked by hand before any residue was chosen is left alone. The best survivors are drawn as thin lines; the membrane-side plane is drawn when that rule is on. Hotspot chips under the Hotspots rule do the same by name.

## The rule catalogue

The base catalogue is `RULES` in the page. The id of a rule is what `rules.overrides` and a rule-set file use. Groups and ids:

- **BindCraft filters** (first): built from the runs, see below. They come first because they are the thresholds the campaign was already judged by, so every design on the page has passed them; the groups under them are Binderscope's own recomputed rules.
- **Confidence**: `plddt`, `ptm`, `ipsae`, `ipae` (min iPAE), `rmsd`, `agree`, `seeds`.
- **Epitope and geometry**: `hot`, `avoid`, `mem`, `gly`, `cterm`, `iface`.
- **Specificity**: `consv`.
- **Interface chemistry**: `sc`, `dsasa`, `dg`, `unsat`, `hb`, `patch`, `loops`, `helixif`.
- **Sequence**: `cys`, `sequon`, `ng`, `stretch`, `pi`, `ek`, `len`, `helix`.

The rules `plddt`, `ptm`, `ipsae`, `ipae`, `rmsd`, `hot`, `mem`, `sc`, `unsat`, `patch`, `cys` and `sequon` start on; the rest start off. A numeric rule that no design carries a value for is dropped; `mem`, `gly` and `consv` are also dropped without `membrane`, `glycan` and `species_panel`. Order and presence of groups follow `rules.groups`; a group the profile does not list is appended, and an empty group is not drawn.

- **Profile rules.** `rules.overrides[id]` changes label, note or numbers of a base rule. `rules.extra[]` and `specificity.rules[]` add rules (`id`, `group`, `label`, `metric` or `expr`, `op`, `val`, `lo`, `hi`, `step`, `dec`, `unit`, `on`, `note`, `rescue`, `nl`; see [schema/profile.schema.json](schema/profile.schema.json)). A specificity `expr` is one of `{species}`, `{min_species}`, `{paralog}` or `{paralog_max_excl}` over the identity at the contacted residues; such rules need `species_panel`, and `paralog_panel` for paralogs.
- **Hotspots touched** counts the hotspots of the design's own run against `min(limit, hot_of)`, so designs of runs with different hotspot sets are judged fairly. For a run without hotspots the limit is 0: the rule passes every design of that run. With the header run filter the chips and the denominator are those of the runs in view; residues chosen on the target or named in a query stay until you restore the defaults. The chips are the campaign's own hotspots, so one turned off can be turned back on, plus every residue you required on the target or named in a query, drawn with a dashed border because it belongs to no run; clicking such a chip drops it from the set and from the row.
- **BindCraft filters.** Each entry of `runs[].filters` becomes one rule, on, with BindCraft's own number as the value (`design.src`: the `average` of a stem, or `interface_aas` for the interface amino-acid counts).
  - A filter with a per-model limit also needs every model to pass, the limit moving with the one you edit, so the stack with only these rules reproduces BindCraft's accept and reject.
  - The rule's note gives the filter's provenance (`counter-verified`, `bounded` or `memory`, defined in `config.py`). Runs that differ in a filter get one rule per run.
  - A base or profile rule that measures the same metric in the same direction is switched off by default (a preset turns it on again).
  - Without `runs[].filters` four legacy rows (i_pTM, i_pAE, surface hydrophobicity, hotspot RMSD) are used; the hotspot RMSD row stays in any case.
- **Rules for run projects** (profile `kind: run`), off by default: **Only designs from the runs** (several runs) and **Sequences kept per family** (group "Run and family"), **Binding mode** and **Sequence liabilities (warn or worse)**. "Sequences kept per family" ranks inside the current pool by the quality score before the other rules are applied, so it is a property of the pool, not "the best survivor of each family".

## Pool, near-misses, rescue, export

These controls exist when the profile `kind` is `run`; other kinds get a reduced page (no pool switch, rescue, send, export or rule sets).

- **Pool.** *Accepted* (default) or *+ BindCraft-rejected* (only with `rejected` on): the designs BindCraft rejected that have a structure. The funnel, rule counts, histograms and lists follow the pool; the buttons count what the run filter leaves.
- **Near-miss rows** show:
  - the design and a tag: `near-miss` when BindCraft itself missed one filter by at most 25 percent of its threshold, `rejected · n filters`, or `accepted`;
  - the rule that blocks it here, with value and need, and the filters BindCraft missed, with value, limit and gap;
  - a suggested next step (the rule's `rescue`, else the page's table) and, under the 3D view when a row is focused, **Rescue as maybe** and **Rescue and keep** (**Add as maybe** and **Keep** for an accepted design). A maybe is not in the order list; a keep is, and a rescued near-miss is marked `near-miss` there. A design that already has a decision is left as it is.

  **BindCraft near-misses only** filters the tab.
- **Send to board.** The toolbar over the list has a Maybe or Keep choice and **Send n to board**. It acts on the ticked designs, or on the whole current tab when none is ticked. A line under it says before you press it what the send would do to the order list: Keep adds every sequence of a family that is in the list (not only its best one) and the designs decided one by one, Maybe adds nothing, and decisions you already have are left alone. After a send the message says how many sequences the order list now holds. A family project writes one `unit: "family"` event per family with its sequences; a rejected design gets a `unit: "design"` event. Decisions you already have are left alone. The note records the rules that were on, or, for a rescue, the missed filters and the rule that blocks the design here. Events go through `BS.decisions.add` ([viewer.md](viewer.md)); the page reads decisions only to draw a pill on rows.
- **CSV (n) and FASTA (n)** act on the ticked designs or the whole tab. Files are made in the browser (`binderscope-query-<profile id>-<n>.csv` or `.fasta`) and nothing is sent anywhere.
  - The CSV has id, run, status, family, sequence index, rank, mode, fold class, length, quality, hotspots touched and `hotspots_of`, the labels of the hotspots touched, the metrics the project carries, the identity at the epitope per headline species, liabilities, the rules failed here and BindCraft's failed filters. It never contains a sequence.
  - FASTA is written for designs with `seq`; the button is disabled when none has one.
- **Save and Load** (rule sets). Save writes `binderscope-rules-<profile id>.json`, schema `binderscope.ruleset/1`: `bundle` (the manifest's bundle id), `profile`, `saved`, `pool`, `hot_custom`, `hotspots`, `forbidden` and `rules[]` with `id`, `on`, and `op`, `val`, `val2`, `sel`, `scope` where they apply. It holds numbers and ids, never a sequence or a design id. Load applies the rules it knows, clamps each value to the rule's range, skips and counts unknown ids, notes a file saved for another profile and offers Undo.

## Plain-words queries

The Ask box matches keywords; it fills in the same typed rules and says what it did. A new query turns off every rule except the Confidence group and the BindCraft filters (unless **Add to current rules** is ticked); the word `reset` restores the defaults. The exact patterns are in `compileNL`.

- **Built-in keywords** (a keyword whose rule the project lacks is ignored): on or off the intended epitope or hotspots, glycans (distance), the membrane side, unsatisfied polars, sticky or hydrophobic patch, confident (ipSAE and min iPAE when present, else i_pTM and normalised i_pAE), refold or pose consistency, a free C-terminus, helical, loops, sequence liabilities, a length ("length at most 90", "under 80 residues"; "short" means 90) and expression.
- **In run projects also**: named residues (`A45 or G60` requires one of them, `A45 G60` all of them, `avoid A45` forbids it; one-letter code and number as in the target numbering), a run (`run02`), a mode (`mode M2`), `one per family`, cut ends and buried-in-the-full-protein.
- **From the profile**: the words that name a project (species, paralogs, the intended epitope, named glycans) are `nl.words` (`epitope`, `off_epitope_exclude`, `glycan_named`) and the `nl` entry (`words`, `text`, `val`) of each rule.

## Keys, hash, decisions

There are no single-letter shortcuts. Everything clickable can be reached with Tab and used with Enter or Space; in run projects each list (rule names, tiles, rows, blocking rules) is one Tab stop and the arrow keys, Home and End move inside it. Enter in the Ask box compiles; Esc closes **What to try**. `query.html#pool=all&tab=near` opens the pool of all designs on a tab (`tab` is `surv`, `near` or `rej`); it is read at load, in run projects only. `#light` and `#dark` set the theme. Design ids link to `inspector.html#d=<id>`.

## Data the page reads

- Blobs: `data/campaign.js`, `data/homolog.js` (required) and `data/manifest.js`.
- Per design: `m.*`, `tc`, `hot`, `hot_of`, `bb`, `fam`, `s`, `run`, `status`, `rank`, `mode`, `sc`, `len`, `seq`, `liab`, `glycan`, `src` (`average`, `models`, `interface_aas`), `fail[]`, `nm` (`n_failed`, `near`), `inspect`, `ca`, `view`, `vc`; with `outcomes`, `outcome` and `wt`. Per run: `id`, `label`, `filters[]` (`stem`, `metric`, `threshold`, `op`, `per_model`, `provenance`, `note`), `failure[]`. Target: `hotspots`, `hotspots_by_run`, `glycan`, `glycan_xyz`, `membrane`, `sites.A1.normal`, `resi`, `resn`, `construct`. The homolog blob feeds the identity at the contacted residues.
- Flags: `families`, `glycan`, `membrane`, `multi_run`, `outcomes`, `weights`, `paralog_panel`, `rejected`, `species_panel`, `ipsae`, `olig`, `rest_ctx`.
- Profile: `kind`, `id`, `rules.*`, `specificity.*`, `nl.*`, `checks.*` (presets), `membrane.plane_half_size`, `view.query_fit_pad`, `predictors.primary.label`, `credit`, `tips.query`, the text keys `crumb.query.*`, `query.funnel.first`, `query.nl.placeholder`, `query.recipe.target` and `query.rule.mem.fail`.

## When data is missing

A metric no design carries drops its rule, so a project without ipSAE has no ipSAE rules and "confident" means i_pTM and i_pAE. A source that reports fewer metrics (for example a BindCraft 2 run, see [bindcraft2_reader.md](bindcraft2_reader.md)) simply has fewer rules. Without `runs[].filters` there is no BindCraft group beyond the legacy rows; without `rejected` there is no pool switch; without `species_panel` there are no specificity rules and no identity columns; with one run there is no run scope or run chips; without a profile the page is the base page. `outcomes` (never set by the BindCraft export) adds **Show lab results**, lab badges and a strip with the share of survivors that bound; it is optional.

## Limits

- "Near-miss" on this page means failing exactly one enabled rule; BindCraft's own near-miss tag is separate.
- Rejected designs without a structure are not in the blobs (they are only counted in `runs[].n_designs`), so they are not on the page.
- Residue mentions in a query need the one-letter code and the number; a glycan site named in the profile is read as the glycan rule instead.
- A rescued rejected design is a `unit: "design"` event: the board draws families, so it has no tile there. If it is kept it is in the order list (header chip, Board panel, files) marked `near-miss`; if it is a maybe it is counted under the list and is not in it. The CSV and FASTA buttons of this page export the designs of the tab as they are; the order list's own files are in the header chip's dialog.

## See also

[viewer.md](viewer.md) · [viewer_board.md](viewer_board.md) · [viewer_inspector.md](viewer_inspector.md) · [viewer_map.md](viewer_map.md) · [modes.md](modes.md) · [specificity.md](specificity.md) · [profile_spec.md](profile_spec.md) · [schema/review_event.schema.json](schema/review_event.schema.json)
