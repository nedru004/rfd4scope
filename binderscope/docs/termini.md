# Terminus analysis: which end can carry a tag

After "which sequences do I order" comes "how do I make them", and that means choosing an N- or C-terminal tag. The two people usually choose between are His6 (6 residues, about 0.8 kDa, usually disordered, purified on Ni-NTA, at either end) and Twin-Strep (two Strep-tag II units, `WSHPQFEK-(GGGS)3-WSHPQFEK`, 28 residues and about 3 kDa, several times the bulk of a His6, purified on Strep-Tactin XT, commonly C-terminal and used at both ends), and sometimes an Fc or a fluorescent protein is fused as well. The choice is not free. A tag at the wrong end can sit in the interface, fray a terminal helix, or ruin an SPR or BLI assay by holding the binder by its binding face. `binder_review.termini` answers the question from the predicted complex, for every end of every design, and keeps the evidence next to the answer. This page is for developers and advanced users: what the readout contains and why, how each quantity is measured, what it looks like on real campaigns, and what the analysis cannot tell you.

Everything here is a prediction from one predicted conformation of the complex. The probe is a sphere and a cone, not a simulation of a tag, and the tag sizes are round figures. A flexible tail's exact position is the least reliable part of any model.

```python
from binder_review import termini as TM

fmt = TM.format_of(run)                                  # the molecule: de novo, VHH, cyclic peptide, homo-oligomer ...
asked = TM.designed_for(run)                             # what BindCraft was asked to do about the ends
block = TM.assess(analysis, cx.binder, cx.target, fmt, env)   # one design: a plain dict, ready for the blobs
TM.family_pick(["C", "C", "N"])                          # "C": a family is what most of its sequences say
```

The exporter calls these for you (`export._terminus_block`, `export.term_run_block`); the shape is in [schema/campaign.schema.json](schema/campaign.schema.json) (`design_term`, `run_term`).

## What a readout contains, and why

The question is "which end, for which tag", and the two tags above are what the readout is built around. The inputs are the distance to the target, solvent accessibility, secondary structure, local confidence, the direction the chain leaves the fold in, the placed pose and the format. They are combined into one answer per tag size and the evidence behind it, because the considerations a protein engineer weighs are not all distances:

- **Distance is necessary, not sufficient.** A terminus far from the target in angstrom can still point into the binder's own fold, into the rest of the protein or into a partner subunit. So the readout is not "how far", but "how large a globular tag fits against this end, and how long a linker does each size need to get clear". The distance (`d`) is kept as evidence, and it is the same number the interface module has always exported as `nterm_d` and `cterm_d`.
- **It is a direction question.** The direction the chain leaves the fold in (`dir`, `aim`) is measured, and the probe looks only inside a cone of 40 degrees around it, which is what a flexible linker can bend through. The placed pose is used for what lies outside the construct.
- **Tag size changes the answer, and the threshold is not one number.** A terminus that comfortably carries a His6 may not carry a Twin-Strep, so every end is reported against both, and against two folded fusions as well: His6 (open solvent means 5 A from every atom), Twin-Strep (10 A, so that the tetrameric Strep-Tactin can bind a Strep unit), a GFP, SNAP or mCherry barrel (a sphere of 16 A) and an Fc or MBP fusion (24 A). `g` says, per tag, `free`, `linker` with the number of residues, or `blocked`; `room` says how large a folded fusion fits without a linker.
- **Flexible tags are tested for reach, not for a straight corridor.** His6 and Twin-Strep are unstructured and Twin-Strep is normally used behind a (GGGS)n linker, so the condition tested is whether a flexible chain of that length can get from the end into open solvent without crossing the target or the binder (a path through space 3 A or more from every atom). That is a much weaker condition than needing an empty cone, because the chain routes round obstacles; it ignores self-avoidance and the entropy of a tight path, so it can only be too lenient. Only the folded fusions (GFP, Fc), which cannot route round anything, are tested with a sphere in a cone, and only they are blocked outright by a terminal residue that sits in the interface.
- **Format can void the question.** A cyclic peptide has no free ends and says so, with no number. A run with several binder chains is switched off, because Binderscope reads one chain and its first and last residues are not the ends of the molecule. A homo-oligomer binder is answered with the note that the tag is on every copy. A VHH carries its convention (C-terminal tags) as a check on the result, never as an input.
- **The choice constrains downstream work.** `side` and `away` say whether an end lies on the paratope side of the binder, and `pick` puts that second: of two ends with room, one on the paratope side loses to one that is not. `immob` names the end to hold the binder by for SPR, BLI or a coated plate so that the binding face points into solution, and is null (a finding in itself) when no end that can take a His6 lies away from the paratope. Twin-Strep is often the capture reagent and not only the purification handle (Strep-Tactin surfaces), so this is said in those terms: a design captured through a tag at the end nearest the paratope can purify perfectly and still give a useless binding measurement, because the binding face points at the chip.
- **The tag is not part of the design.** The tag sequences are not carried anywhere (a Twin-Strep has two tryptophans): the analysis neither reads nor writes a sequence, and none of the tag's residues can reach the design's liability counts (a test guards it).
- **Absent is not zero.** A check without its data is switched off and says so: no reference structure means the other subunits, the rest of the protein and the glycans were not part of the check (`env` false, `d_env` null), a terminal residue without a CA is `conf: "off"`, a design the exporter did not judge carries no `term` at all.

## How it is measured

All geometry is rigid-body invariant, so it is computed in the design's own frame. The placed pose enters only through the environment function `env`, which the exporter builds from `TargetFrame.context` (the same function that gives `olig_min`, `rest_min` and the glycan distances): it carries points of the design's frame onto the reference chain with the design's placement and returns how close they come to the other subunits, the rest of the chain outside the construct and the glycans. Partner geometry is read only through it; nothing here computes its own.

| Quantity | Definition |
|---|---|
| `d` | terminal CA to the nearest heavy atom of the design's own target construct |
| `d_env`, `env_what` | terminal CA to the nearest atom outside the construct (`olig` the other subunit, `rest` the rest of the protein, `glycan`); null without a reference |
| `dir` | the direction the chain leaves the fold in: from the mean of the next 6 CA positions inward to the terminal CA. The mean sits near the axis of a helix and along a strand, so the vector follows the element and not one residue's wobble. Written in the placed frame |
| `aim` | degrees between `dir` and the direction to the nearest target atom (0 points at it, 180 points away) |
| `away` | cosine between the end's offset from the binder's centre and the binder's offset from the interface centre: +1 the side of the binder farthest from the paratope, -1 the paratope side |
| `side` | `paratope` when the terminal residue is in the interface, the end is within 10 A of the target (BindCraft's own figure for the two ends of `termini_together`) or `away` is below -0.25; `far` when `away` is at least 0.25 and the end is not near; `lateral` otherwise |
| `contact`, `iface_n` | the terminal residue is in the 4.5 A interface; how many of the last three residues are |
| `rsa`, `plddt` | mean relative solvent accessibility of the last three residues in the complex, mean pLDDT of the last five |
| `ss`, `elem`, `tail` | the first helix or strand reached from the end, its length, and the loop residues between the end and it |
| `g[tag]` | the grade for one tag (below); for His6 and Twin-Strep it carries `esc` as well: the length in A of the shortest path from the end to open solvent, to be set against the tag's reach (`runs[].term.tags[].budget`: 9.9 A for His6, 66 A for Twin-Strep) |
| `room`, `limit` | the radius in A of the largest sphere that fits against the end with no linker (capped at 30), which is the measure for the folded fusions, and what stops the first tag that is not free: `target`, `fold` (the binder's own fold), `olig`, `rest` or `glycan` |
| `conf`, `via_tail` | `low` when the last five residues are below pLDDT 70 or the end is a disordered tail; `off` when it could not be judged |
| `nc_d` | between the first and last CA (BindCraft's `termini_together` measure) |
| `pick[tag]` | `N`, `C`, `both` or `none` (below) |
| `immob` | `N`, `C`, `either` or null (below) |

**Flexible tags (His6, Twin-Strep).** A grid of 3 A voxels is laid around the end (60 A each way). A voxel is open when it is 3 A or more from every heavy atom of the target and of the binder's own fold (the terminal residue is left out: the chain grows from it), and the voxels within 4 A of the end that are not inside an atom are open too, because the chain leaves the end wherever the model has put it. The shortest path through open voxels from the end to a voxel with the tag's clearance (5 A for His6, 10 A for Twin-Strep) is `esc`. The tag fits when `esc` is no longer than its reach: its residues less the motif that has to lie in open solvent (the three histidines of the His6; one Strep-tag II unit, 8 residues, of the Twin-Strep), 3.3 A per residue. A linker of 0, 1, 2, 3, 4, 5, 6, 8, 10, 12 or 15 extra residues adds to the reach; the first length that makes it fit is the grade (`free` for 0, `linker` for the rest, `blocked` when none does or when the environment closes every path). The other subunits, the rest of the protein and the glycans are asked about the best path, and the voxels it crosses that are too close are blocked before the search is repeated. A path longer than 60 A from the end is not searched.

**Folded fusions (GFP, Fc): the probe.** For a tag of radius `r`, a sphere is placed `r + 3` A from the end (`STANDOFF`, about one linker residue) plus 3.3 A for every residue of linker (`REACH`), in each of 25 directions inside the 40 degree cone (`CONE_DEG`) around `dir`. It fits when its centre is farther from every heavy atom of the target and of the binder's own fold than `r + 1.7 - 2.0` A (the heavy-atom radius less the 2 A a folded tag's envelope may overlap the surface, because it packs and gives), when the path from the end to it keeps 3 A from every atom, and when the other subunits, the rest of the protein and the glycans also clear it (asked only about the few best candidates). The same linker lengths are tried. A terminal residue that is itself in the interface blocks a folded fusion outright (`why: "contact"`); it does not block a flexible tag, which can still leave the end, and is carried by `side` instead.

**A disordered tail.** An end that is 3 or more loop residues with a mean pLDDT below 70 is judged from its last ordered residue (`at`), with the tail counted as linker and `conf: "low"`. Where the tip of a floppy tail lies in a model is a guess, so the side, the contact test and the direction are read at that residue, the tail's own residues count as reach, and the tip's contact with the target stays in the evidence (`contact`) without blocking the verdict.

**The pick.** For one tag size: an end with no room for it is out; of two ends with room, one on the paratope side loses to one that is not; only when both are on the same side does fit decide (`free` beats `linker`, and a linker three or more residues shorter decides between two ends that both need one, below that the two are called alike). `both` means either end is as good, `none` that neither can carry it.

**A family** is judged by what most of its sequences say, and by C, N, either, neither in that order when they are tied (`family_pick`; the pages use the same rule, and no quality score enters it, so they cannot disagree), because sequences on one backbone refold with their ends in somewhat different places. A family whose sequences do not all agree is marked and says how many do.

**Immobilisation.** Of the ends that can take a His6, those on the far side of the binder from the interface (`away` above 0): the better one, `either` when they differ by less than 0.3, null when none exists, which means any capture through a terminal tag orients this binder by or near its binding face.

The constants are at the top of `termini.py` and are written into every run block (`runs[].term.const`, and the tags in `runs[].term.tags`): the tags (`TAGS`, `FLEXIBLE`), `FLEX_SPACING`, `FLEX_HALF`, `FLEX_START`, `CONE_DEG`, `STANDOFF`, `REACH`, `LINKER_STEPS`, `ATOM_R`, `SLACK`, `PATH_CLEAR`, `EXIT_WINDOW`, `TAIL_MIN`, `DISORDER_PLDDT`, `NEAR`, `PARA_AWAY`, `FAR_AWAY` and `ROOM_CAP`. They are judgement calls. Changing one changes verdicts.

## Formats

`format_of(run)` reads the run's own modality (the design names, the settings, the scaffold record) and never assumes a de novo binder; when the run says nothing it reads the run as de novo and says so (`format.known` false).

| Format | What the analysis does |
|---|---|
| de novo | judged from the structure alone |
| VHH | judged from the structure alone; carries `convention: "C"` and the reason (the N-terminus lies at the CDR end, the C-terminus at the opposite end of the domain), and the pages set the result beside it |
| scFv, Fab, ARP | judged from the structure; no end is assumed from convention. A Fab or any run with several binder chains is switched off (one chain is read) |
| cyclic peptide | `applies` false with the reason; no numbers |
| homo-oligomer (`copies` above 1) | judged for one protomer, with the note that a tag is on every copy at once; whether the copies crowd one another is not judged, because their structures are not read |
| peptide (25 residues or fewer in every design) | judged as above, with one note that a tag is then a large share of the molecule |

## What BindCraft was asked to do

BindCraft 2 can optimise the ends: `termini_accessible` (`weights_termini_angle`, `weights_n_terminus_away`, `weights_c_terminus_away`) angles both ends away from the target, `termini_together` (`weights_termini_distance`) brings them within about 10 A, and `protease_stable` (`weights_exposed_termini`) buries them, the opposite of what a tag wants. `designed_for(run)` reads which of these a run asked for (a missing or zero weight is not asked for; a run without recorded settings is `known: false` and nothing is claimed either way). The pages say it next to the result, because a free end in a campaign that asked for it confirms the setting, and in one that did not it is a discovery. Neither of the campaigns described below set any of them, so the variation they show is unconstrained.

## What the pages show

- **Inspector, Findings tab.** The *Tag and fusion* card: a tag switch (His6, Twin-Strep, GFP / SNAP, Fc), the headline for that size, one column per end with the status for every size and the evidence in words (distance, direction, side of the binder, secondary structure, burial, confidence, room and what limits it), what the choice decides for immobilisation and display, and the notes (convention, what BindCraft was asked, what was checked, the caveat). Hovering an end draws its sphere, exit direction and status in the 3D view, and *Chain ends* in the viewer toolbar keeps them drawn. It replaces the old one-line terminus finding, which is still drawn for a bundle without a `term` block. See [viewer_inspector.md](viewer_inspector.md).
- **Board.** A *Where a tag can go* card (counts are of families, one row per tag size, with the convention check and what BindCraft was asked), a *Tag* switch in the toolbar that drives everything, a pill on every tile, a Tag column in the sequence table, a *Tag and fusion* section in the focus panel, a line in the order-list note and a *Tag plan CSV* next to the order-list buttons. See [viewer_board.md](viewer_board.md).
- **Overview.** One highlight per run with the family counts.

## What it looks like on real campaigns

The per-campaign measurements this section used to carry came from unpublished
work and are not reproduced. What they established is general, and is the
reason the readout is shaped the way it is.

**On a scaffolded VHH campaign the convention comes out of the geometry, not
out of the format.** The probe knows nothing about VHHs, yet it picks the
C-terminus for every design and every tag size. It does so because in this fold
the N-terminus sits on the paratope side, close to the target and pointing
towards it, while the C-terminus sits on the far side, far from the target and
pointing away. So the familiar advice to tag a nanobody at the C-terminus is
recoverable from one predicted complex, which is a useful check that the probe
is measuring what it claims to.

Tag size separates the ends further. A short tag often fits at both ends, while
a Twin-Strep needs about 10 A of clear path for the Strep-Tactin and a folded
fusion needs far more, so the N-terminus loses the larger tags first. The
paratope side puts the N-terminus second even where a short tag fits, because
capture through a tag on the binding face is what ruins an SPR or BLI assay.

**An unconstrained de novo campaign is mixed, as it should be.** Families split
between ends, some take a His6 at either end but a Twin-Strep at neither, and a
minority have no taggable end away from the paratope at all — which is exactly
the case worth surfacing, since nothing else in a results table would warn you.

**The rest of the protein is what closes most ends.** Ends that look free
against the target alone are frequently blocked once the reference structure
places the remainder of the protein around it. Running the check without
`--reference-cif` is therefore optimistic, and the two runs disagreeing is
informative rather than a fault.

## What it cannot tell you

- The structure is predicted, and the pose is one conformation. An end 12 A from the target in the model may lie elsewhere in solution.
- The folded fusions are tested with a sphere in a cone, which a real GFP or Fc, tilted and joined by a linker that bends, is more flexible than. The flexible tags are tested by reach alone: self-avoidance and the entropy cost of a tight path are ignored, the grid is 3 A, and the tag sizes are round figures. The verdicts are a ranking of ends, not a prediction that a construct will express or fold.
- The rest of the protein and the other subunits are the reference structure placed rigidly on the design's target, with the placement fitted around the binder. Low-confidence regions of a predicted reference are placed arbitrarily, and the distances are estimates. A binder that already clashes with the rest of the protein (as it will where a predicted reference places that stretch arbitrarily, see the Context check) will have ends that meet it.
- A flexible tail's tip is the least reliable part of a model. Where it matters, the end is read from the last ordered residue and marked `low`.
- Homo-oligomer copies are not read, so crowding between their tags is not judged.
- It says nothing about expression, solubility, proteolysis, or whether a tagged protein folds.

## See also

[interface.md](interface.md) · [target_frame.md](target_frame.md) · [paratope.md](paratope.md) · [viewer_inspector.md](viewer_inspector.md) · [viewer_board.md](viewer_board.md) · [export.md](export.md) · [data_contract.md](data_contract.md)
