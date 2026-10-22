# Inspector page

`viewer/pages/inspector.html` looks at one design in depth: a verdict, the scores as meters against their limits, findings that fly the 3D view to their evidence, the full-length context of the target, the interface, confidence, specificity, sibling sequences and sequence tracks. This document is for a developer or an advanced user: behaviour first, then the data contract; shared code (checks, presets, run filter, decisions, profile helpers) is in [viewer.md](viewer.md).

## The layout

One screen, in three bands: a header of one or two lines (the design in hand,
its verdict, its meters, the decision), the structure beside the analysis tabs,
and the sequence tracks pinned along the bottom.

**Why the tracks are pinned.** They are only useful next to the structure --
hover a residue to light it in 3D, click to fly there -- and they used to sit at
y 930 on a 900 px screen, so the two views that only work together could not be
seen together. The band is collapsible (click its heading): open, the structure
is about 1001x360 and the tracks show five rows; collapsed, the structure takes
the space back (about 1001x409). On a 900 px screen it cannot be both, so which
one matters is the reader's choice, and it is remembered per browser.

**The tabs open on Interface.** The contact diagram is what a reader looks at
first on a design they have not seen; Findings is next to it and carries its own
count, so a design with something to answer for still says so.

**The picker keeps its list.** Prev, Next and the position sit together, the
filter takes the rest of the line, and the designs scroll in a fixed height
below the chips. It is beside the verdict and the meters, which set the header's
height, so the list is using room that would otherwise be empty. The height is
fixed on purpose: told to fill the cell it grew to all of its rows, and the
header it was meant to fit became 1031 px tall.

## What the page shows

- **Design picker.** Up to 12 designs: one button each (label from `inspector.names`, else `inspector.method_names`, else `design.label`). More than 12: a searchable list with **Prev**, **Next** and a position counter, a filter box (id, family, run, mode, status and verdict words; all words must match) and chips for Clean, Issues, Fix first, Accepted, Near miss (needs `rejected`) and Undecided (needs `reviews`). The header run filter limits the list.
- **Verdict strip.** Two pills. The first is the board's PASS, REVIEW or NO PASS for the same design (accepted designs of exporter blobs). The second is the inspector's finer reading: *Clean*, *Promising, with n issues*, or a fix-first label (*Wrong site for this campaign*, *Clashes with the other subunit*, *Score not trustworthy*, *Fix before ordering*). It also names single warnings that the board tolerates, so a design can be PASS and "promising, with 1 issue". Then come the design id, the binding mode (a link to the map), the modality and length, the run, the rank or `Rejected by BindCraft · near miss`, and one line of why.
- **Meters.** One tile per score with the value, a bar and a tick at the limit; pass, warn (within 8 percent of the scale of the limit) or fail. Thresholds are the preset in force; on the native basis (see "Meters") the run's own BindCraft filter from `manifest.runs[].filters` is used for the scores BindCraft filters, when there is one.
- **Decision bar.** Keep, Maybe, Reject with their keys, a line saying what the decision does to the order list, and links to the family on the board, the near-miss list of the query page, the mode on the map and the order list itself (with its count; it opens the same dialog as the header chip). Before a family is kept the line says that Keep adds this sequence and that the family's other sequences stay out unless they are kept too; once it is kept it says how many of the family's sequences are in the list and whether this one is. A near miss says that a Keep puts it in the list, marked near-miss. Needs `reviews` and `inspect.runs[]`.
- **Structure.** The 3D view (3Dmol.js; a simplified canvas view when WebGL is not available). The target is the design's own copy of it, as BindCraft modelled it (chain A of the design's structure file, placed on the reference frame), so the binder, the contacts, the hydrogen bonds and the salt bridges are drawn exactly against it. The other subunit, the rest of the protein, the removed regions and the sugars come from the reference structure. Click a residue to fly to it, hover for residue, side, pLDDT and buried area.
  - **Colour by**: Chain, pLDDT, Interface, Hydrophobicity, ΔΔG scan (needs `ddg_scan`), Conservation (needs `species_panel`), CDRs (designs with antibody regions) or **Segments** (a design placed on its scaffold, see "Paratope of a scaffolded run"). A scaffolded design opens coloured by segment; the choice is the reader's afterwards.
  - **Toggles**: interface residues, H-bonds, salt bridges, hotspots, unsatisfied polars, hydrophobic patch, target surface, and by capability glycans, membrane zone, rest of the protein, other subunit and known binders. Layers also have a legend chip that hides them.
  - **Overlays**: predictor poses with `models` (and a 5-seed overlay when `has_seeds` is on too). **Pull apart** (0 to 24 Å) moves the binder away from the target.
- **Analysis tabs.** **Findings**, **Interface**, **Confidence**, **Specificity** (needs `species_panel`) and **Siblings** (**Similar** without `families`).
- **Sequence tracks.** The binder sequence with interface residues underlined, structure, pLDDT, buried area, ΔΔG scan (`ddg_scan`), flags (unpaired Cys, liabilities, disulfides, hydrophobic patch) and antibody regions when present; for the target the numbering ruler, hotspots and contacts, the interface with the other subunit (`olig`), conservation and paralog identity (`par_ident`). Hover a residue to see it in 3D, click to fly there. Residues the design's run removed are hatched.

## Tabs

**Findings.** A list sorted *Fix or explain*, *Worth a look*, *Context*, *Looks good*. Click one: the 3D view highlights and flies to the evidence and a "suggested next step" opens.

- Generated from geometry and scores: epitope (hotspots touched, judged against the design's own run), interface packing, buried unsatisfied polars (BindCraft's own count when the blob has `src`, next to the recomputed one), hydrophobic patch, loops at the interface, low-confidence interface residues, pose (see below), sequence liabilities, charge, glycan distance (`glycan`), membrane side (`membrane`), specificity (`species_panel`, `paralog_panel`) and the context findings. A rejected design gets a finding that lists every failed filter with value and limit.
- **Target frame** is a strip of the full-length protein: the domains of `profile.frame`, the construct of the design's own run, stretches the run removed (hatched), cut ends, hotspots, contacted residues, the interface with the other subunit and glycan sites, with a magnified construct row.
- **Target checks** are cards for the other subunit (`olig`), the rest of the protein (`rest_ctx`: nearest atom, atom counts within 3, 4 and 6 Å), the epitope residues the rest of the protein would bury, cut ends, glycan distance and membrane side. Each card shows its numbers and, on a click, what would confirm or refute it. The limits are those of the board's Context check (`checks.context.*`). When Context is advisory the findings that would be failures read as warnings and say so; when it was not run (no reference structure, or a reference with nothing to test against) the section is one card, *Context checks were not run*, with the reason and what to do, and the headline of a design with no other issue says the context checks were not run instead of "no issues".

**Interface.** Contact diagram (target left, binder right; H-bonds, salt bridges, other contacts), per-residue contribution (buried area; the alanine-scan proxy only with `ddg_scan`) and the ten largest contacts.

**Confidence.** The PAE matrix (`has_pae`; drag a box to light up those residues in 3D), pLDDT along the chain, and the predictors block (`models` lists every pose with its RMSD to the design; otherwise the design model and the monomer).

**Specificity.** Needs `species_panel`.

- A reading of the profile's specificity rules (cross-species reactivity and off-target risk, each with the assay that would settle it); epitope conservation (variable, average, conserved); a species table with the residues that differ; paralog bars (`paralog_panel`); the residue-by-residue alignment (`*` marks a low-confidence cell); stickiness proxies.
- The ConSurf-DB overlay toggle needs `consurf` and `homolog.consurf` and is off by default.
- A decoy co-fold section (`decoy_cofold`, off in every export of this tool) shows values labelled illustrative.

**Siblings.** Sequences on the same backbone with their scores and a bar of the positions that differ from the first sequence; a rejected sequence is listed but never suggested as better. **Similar** (no families) lists the nearest designs by shared contacted residues (`nbrs`).

## Paratope of a scaffolded run

A design of a run that carries a paratope block (`runs[].paratope`) and has its own `para` block gets four additions that share one vocabulary: a colour for every segment of the scaffold.

- **Where each residue sits.** `Common.para.layout` puts the run's edit spec (`segments[]`: scaffold positions, which stretch varies in length and by how much) together with the design's `para.lengths` and gives every residue a label: a designed segment (CDR1, CDR2, CDR3, H1 to L3 or "designed segment n"), FR1 to FR4, or the FR2 face. The labels drive the 3D colouring, the **Segments** track of the sequence tracks and the findings, the way `regions` does for an antibody numbered by IMGT. Segment boundaries are the campaign's own edit spec, not IMGT or Kabat numbering; the colour legend and the tooltips say so.
- **It refuses rather than guesses.** A design is placed only if its loop lengths lie inside the ranges the spec allowed and its placement agrees with what the design reports about itself: the interface list (`para.interface`) when the design carries one, else BindCraft's own count per loop against the structure-based contact list (which must hold at least that many residues on the loop), and each framework position named in `para.framework_at` must be a contact residue carrying the same region name. A design that fails, a segment on another chain, a block that covers more than one chain, or a missing length gives the reason in the finding and is not coloured; its counts stay. When the stretch that varies in length is not marked in the block, the longest designed stretch of that segment is used and the finding says it was inferred.
- **Findings.** *Paratope* (which designed segments touch the target, with the residue count of each, the designed loop lengths against the range the spec allowed, and a pointer when the structure-based contact list is wider than BindCraft's own); *Framework residues in the interface* (the positions in the scaffold's numbering from `para.framework_at`, with the design residue number where the loops moved it; a rejected design that failed `Off_Paratope_Contact_Fraction` says so and that the two counts differ); for an antibody modality *FR2 face* (which designable positions were changed, which kept, and how many of the face's residues are in contact). A paratope that is mostly framework is a warning; otherwise the findings state and do not judge. Clicking a finding colours the view by segment and shows its residues.
- **Interface tab.** A **Paratope by segment** section above the contact diagram: one bar per segment with its residue count (BindCraft's list); a click shows the segment's residues in 3D.
- **Strip.** A chip beside the modality, for example "3 of 3 loops · 3 framework", and the modality chip reads "VHH scaffold" rather than the fold label, which means nothing on a scaffold.
- A scaffolded run's designs no longer get the generic "x% of the interface is loops" finding, for the same reason an IMGT-numbered antibody does not: loops are what the paratope is made of.

A run with no paratope block, and a design with no `para` in a run that has none, behave exactly as before.

## Tag and fusion

A design with a `term` block ([termini.md](termini.md)) gets a *Tag and fusion* card at the top of the Findings tab, above the target frame. It is the level at which someone chooses a construct:

- **A size switch** (His6, Twin-Strep, GFP / SNAP, Fc; remembered in this browser) and **a headline** for that size: which end to tag and why in half a sentence ("The C-terminus has room for His6; the N-terminus has room but sits on the paratope side of the binder"), or that neither end takes it as it stands. The icon is green when the end is clear, amber when the chosen end lies on the paratope side or its position is a low-confidence guess, red when neither end can.
- **One column per end**, with the status for the size in force (fits as it is, needs a linker of about n residues, no room, or the end itself touches the target), a chip row with the status of every size (the number after a plus is the linker in residues) and the evidence in words: distance to the nearest target atom, distance to the other subunit or the rest of the protein when that is nearer, the direction the chain leaves in against the target, the side of the binder, the terminal helix or strand and the loop before it, exposure, confidence, and the room that fits against the end without a linker with what limits a larger one. Hover an end (or focus it with the keyboard) to see its sphere, exit direction and status in the 3D view; click it to fly there. **Chain ends** in the viewer toolbar keeps them drawn.
- **What the choice decides**: which end to hold the binder by (Strep-Tactin for a Twin-Strep, Ni-NTA for a His6; SPR, BLI or a coated plate) so that the binding face points into solution, and that capture through the end nearest the paratope can purify perfectly and still give a useless binding measurement (or that no end that can take a His6 lies away from the paratope), where display and avidity constructs can join, whether the two ends are close enough to be joined (within 12 A), and for a homo-oligomer that the tag is on every copy.
- **Notes**: for a format with a convention the convention and whether this design agrees (a VHH is tagged at the C-terminus; the geometry here does not use it), whether the run's format was recorded, what BindCraft was asked to do about the ends in this run, whether the other subunits and the rest of the protein were part of the check, and the caveat that this is a prediction from one conformation.

A design whose block says the question does not apply (a cyclic peptide) shows the reason and nothing else. The old one-line terminus finding (a C-terminus 12 A or more from the target is free) is drawn only for a design without a `term` block.

## Pose findings

Three configurations, chosen by flags: several predictors (`models`: flip, odd one out, all agree); one co-folding model plus a monomer prediction (`has_boltz`); otherwise BindCraft only (`hotspot_rmsd` against `checks.findings.hotspot_rmsd_warn`, plus a low-interface-confidence finding on i_pTM and i_pAE against the thresholds in force). The design model made the design, so only a re-score by another predictor is an independent check; the page says so.

## Meters

One catalogue, shown when the value exists and its rule holds: pLDDT, pTM and i_pAE (native basis), ipSAE, ipTM or i_pTM (without a re-score), min iPAE, re-score RMSD (`complex_rmsd`), binder RMSD (without `complex_rmsd`), hotspot RMSD (native basis and `hotspot_rmsd`), partner gap (`olig`), rest gap (`rest_ctx`), shape complementarity (`checks.physics.sc_check`), interface ΔG (`dg`), buried area, unsatisfied polars (count or density, `checks.physics.unsat_basis`), surface hydrophobicity (native basis) and, for antibodies, the CDR share. "Native basis" means `checks.interface.basis` is `native`, or `auto` without `ipsae`: the interface is then judged by BindCraft's own i_pTM and i_pAE.

## Decisions and keys

Keys (ignored in text fields and with Ctrl, Alt or Cmd): left and right step through the visible list; `/` focuses the filter box (list mode); `K`, `M`, `X` keep, maybe or reject; `U` undoes the last decision of this page session. Decisions are written to the shared log ([viewer.md](viewer.md)) and appear on the board and the query page; the header shows the order list they add up to ([viewer.md](viewer.md), "The order list"). The Inspector holds the accepted designs and the near misses that have a structure record, so a list that contains a rescued near-miss without one cannot be written as files from here (the dialog says so and points to the board). With families the decision is for the family (`unit: "family"`, `design` = backbone id, `sequences` = the sequences chosen for ordering): `K` on a sequence adds it, a second `K` on a chosen sequence removes it, and removing the last one clears the family. A design BindCraft rejected has no tile on the board, so it is decided per design (`unit: "design"`), as the query page records a rescue. Without families every decision is per design.

## URL hash

`inspector.html#d=<design id>` opens that design at load and on `hashchange`. The address follows the design you open (`history.replaceState`, so there is no history entry per arrow key). An unknown id shows a notice, and the first design opens. A link to a design of a run the header filter hides clears the filter. `#light` and `#dark` set the theme and are left alone. Links written by `BS.link.design` also carry `&run=<run id>`; no page reads it, because design ids are unique across the project.

## Data the page reads

- Blobs: `data/inspect.js`, `data/homolog.js` (required) and `data/manifest.js`. The binder structure of a design is read from `pdb_b` when the record has it; otherwise from `structures/<design id>.js` (`/` in an id is written `__`), loaded on demand with a loading overlay and a message when the file is missing. The findings, meters and tracks do not need it. The same file supplies the target: its chain A is the design's own copy of the target, and the page draws it as the target as soon as the file has loaded. A record that already carries `pdb_b` (a bundle built with `--embed-structures` or `--no-structure-scripts`) never loads the file, so there the target stays the reference chain. Before the first file has loaded, and in the simplified view without WebGL, the target is `inspect.target_pdb` (the reference chain in the reference frame, always written as chain A); `inspect.target_pdb` also supplies the removed stretch of the "rest of the protein" layer when the blob has no `rest.traces`. `bundle.py` leaves `pdb_b` out of `data/inspect.js` by default; `data/inspect.json` keeps it.
- `inspect`: `order`, `designs[tag]`, `runs[]` (per-run target ranges, removed stretches, cut points, hotspots), `hotspots`, `target_pdb`, `target_seq`, `target_resi`, `t_ss`, `glycan`, `glycan_xyz`, `glycan_atoms`, `membrane`, `olig` (`neigh`, `buried`, `buried_all`, `min_area`), `rest.traces`, `foot` and `partners`. Per design: `id`, `run`, `status`, `rank`, `bb`, `label`, `mode`, `mode_name`, `modality`, `seq`, `ss`, `plddt_res`, `plddt_t`, `dsasa_res`, `contacts`, `hbonds`, `salt`, `buns`, `patches`, `t_contact`, `b_contact`, `hot`, `ssbonds`, `glycan`, `mem_margin`, `poses`, `sibs`, `metrics`, `liab`, `ctx`, `octx`, `prov`, and for rejected designs `fail` and `nm`; `src.average` for BindCraft's own counts. `homolog`: `resis`, `seq`, `grade`, `orth`, `par`, `n_orth`, `consurf`, `par_ident`, `par_cover`, `par_note`.
- Terminus analysis (optional, [termini.md](termini.md)): `inspect.runs[].term` (the format, `designed_for`, the tag sizes, the constants) and, per design, `term` (a copy of the campaign design's block, including `dir`, the exit direction of each end in the frame of `pdb_b`).
- Scaffolded runs: `inspect.runs[].paratope` (the same block the board reads, in particular `segments[]` with `name`, `chain`, `scaffold`, `designed`, `length` and `variable`, and `modality`, `chains`, `scaffold_length` when given) and, per design, `para` (`segments`, `loops`, `framework`, `lengths`, `face`, `face_mutated`, `framework_at`, and `interface` when given). Both are optional; the page never reads them from the campaign blob.
- Flags: `species_panel`, `paralog_panel`, `consurf`, `decoy_cofold`, `glycan`, `membrane`, `olig`, `rest_ctx`, `flag_region`, `footprints`, `models`, `has_boltz`, `has_seeds`, `has_pae`, `ddg_scan`, `families`, `multi_run`, `rejected`, `reviews`, `outcomes`, `ipsae`, `complex_rmsd`, `hotspot_rmsd`, `dg`.
- Profile: `checks.*` (`interface.basis`, `physics.*`, `context.*`, `findings.*`), `frame`, `target.{short,uniprot,length,span_label}`, `inspector.{order,names,method_names,method_strip,id_strip}`, `specificity.{headline,detail,rules,species,decoy_species,paralogs.focus}`, `predictors.{primary,monomer,colors,design_model_prefix}`, `footprints.*`, `flag_region.key`, `membrane.plane_radius`, `view.{inspector_focus,inspector_zoom}`, `credit`, `tips.inspector`, and the text keys `crumb.inspector.camp`, `finding.*`, `inspector.*`, `meter.*`, `spec.*`, `verdict.*` and `view.*` (every one has a default in the page; [schema/profile.keys.json](schema/profile.keys.json) lists the registered keys).

The extras that need per-run context (decision bar, run filter, frame detail, target-check cards, rejected-design handling, the board verdict pill) are on only when `inspect.runs[]` is present, which it is in every blob the tool writes.

## When data is missing

| Missing or off | What changes |
|---|---|
| `species_panel` | No Specificity tab, no Conservation colour, no specificity findings. |
| `paralog_panel` | No paralog bars, rows or finding. |
| `olig`, `rest_ctx` | The cards, layers and meters that need that data are left out; the strip keeps the construct, hotspots and contacts. |
| no domain list in the profile (`frame`) | The strip draws no domain segments. |
| `glycan`, `membrane` | No finding, toggle or layer. |
| `has_pae`, `ddg_scan`, `has_seeds` | No PAE panel; no ΔΔG colour or track (buried area stands in); no seed overlay. |
| `families` | The fifth tab is **Similar**; decisions are per design. |
| `rejected`, `reviews`, `multi_run` | No near-miss chips and finding; no decision bar or keys; no run filter or run chips. |
| a source that reports fewer metrics (for example a BindCraft 2 run, see [bindcraft2_reader.md](bindcraft2_reader.md)) | The meters and findings of the missing metrics go; pose findings fall back to the configuration the data supports. A missing value never fails a design. |
| structure file of a design | Message in the 3D panel; everything else works. |
| no `term` on a design | No Tag and fusion card and no *Chain ends* toggle; the one-line terminus finding is drawn instead. |
| `term.applies` false | The card gives the reason (a cyclic peptide has no free ends) and no numbers. |
| `term.env` false, or `runs[].term.designed_for.known` false | The notes say the other subunits and the rest of the protein were not part of the check, or make no claim about what BindCraft was asked to do about the ends. |
| an end with `conf` `off` | That column reads "Not judged" with the reason; there is no pick for the design. |
| no `runs[].paratope`, no `para` | Not a scaffolded design: nothing above changes. |
| a scaffolded run but this design has no `para` (it did not align) | One finding says so; no segment colouring, no Segments track, no chip. |
| `para` but the run's block lists no segments, or the design cannot be placed | The counts, loop lengths and face stay (they are BindCraft's own); the reason is in the finding; no segment colouring. A framework position is named only with a placed design. |
| `framework_at` absent | Framework positions are read from the structure-based contact list of a placed design and the finding says so. |
| `face` absent | No FR2 face finding. |
| `outcomes` (never set by the BindCraft export) | Optional: **Show lab results** in the header, a lab badge and a lab card. |

## Limits

- The 3D view draws each design's own target (chain A of its structure file), which lacks the stretches its run removed; the "removed regions" layer draws those from the reference chain as ghosts. The other subunit, the rest of the protein and the sugars are the reference structure placed by the same transform as the design, so where the design's copy differs from the reference they can sit slightly off against the target: the placement is weighted around the binder, not over the whole target ([target_frame.md](target_frame.md), Placement). The simplified view without WebGL draws the reference chain as the target.
- Segment colours come from the campaign's edit spec, not from IMGT or Kabat numbering; `para` counts are BindCraft's `Interface_Binder_Residues`, while the 3D view and tracks mark Binderscope's structure-based contacts, which are wider (the finding gives both numbers when they differ); an scFv or Fab block covers one chain only and its designs are not laid out here.
- The inspector's verdict counts more than the board's (packing, sequence liabilities, glycan distance, unsatisfied polars), so it can be stricter.
- Sequence liabilities come from `liabilities.py` ([interface.md](interface.md)). An exposed methionine, for example, is a warning and can lower a verdict, although a tagged construct may not care.
- The tag card is a prediction from one predicted conformation: the probe is a sphere in a cone, the tag sizes are round figures, the rest of the protein is the reference structure placed rigidly, and a flexible tail's position is a guess ([termini.md](termini.md)).
- Nothing here is a prediction of binding; every finding says what would confirm or refute it.

## See also

[viewer.md](viewer.md) · [viewer_board.md](viewer_board.md) · [viewer_map.md](viewer_map.md) · [viewer_query.md](viewer_query.md) · [interface.md](interface.md) · [target_frame.md](target_frame.md) · [specificity.md](specificity.md) · [antibody.md](antibody.md) · [export.md](export.md)
