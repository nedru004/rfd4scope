# Paratope analysis for scaffolded campaigns

`binder_review.paratope` (`src/binder_review/paratope.py`) judges a campaign built on a fixed scaffold (a nanobody, an scFv, a Fab, an ankyrin-repeat protein) by what actually varies between its designs. This page is for developers and advanced users: why the module exists, how it divides the scaffold and aligns designs without any antibody numbering, what the blocks in the campaign blob carry, and where the analysis stops.

Count families, never sequences. Every number is a screen: a passing design is a predicted binder, not a measured one.

## Why this exists

Binderscope counts families by backbone fold. In a scaffolded campaign every design is the same fold by construction, so the fold figure saturates: a scaffolded VHH run reports fold coverage of 1.00 while a de novo run on the same target reports much less, and that 1.00 means "these share a framework", not "the space has been explored". Read at face value it says the opposite of the truth.

The house rule applies: a figure a run cannot support is switched off, never guessed. For a scaffolded run the fold-based figures are switched off ([families.md](families.md), "Scaffolded runs and epitope spread") and three things that do vary are reported instead: which designed segments touch the target, how long the loops that were allowed to change length came out, and where on the target the binders land.

## Where the regions come from

Not from IMGT or Kabat numbering. `abnumber` needs `anarci`, which needs HMMER, and more to the point numbering a sequence by inference is a guess where an exact answer exists: the campaign records which positions it was allowed to change. BindCraft 2 writes it in `campaign_metadata.json`:

```
settings.filters.Scaffold_Sequence_Retained_Fraction.params = {
  "scaffold":       "/…/scaffolds/VHH.cif",
  "scaffold_edits": "A27-33,A35*,A37*,A45*,A47*,A50*,A52-57(5-7),A59,A97-99*,A100-114(3-25),A115,A116"}
```

The BindCraft 2 reader copies this, with the filter's own `threshold` and `mandatory` flag, to `RunData.annotations["scaffold"]` (the only change the reader makes for this analysis; see [bindcraft2_reader.md](bindcraft2_reader.md)). A run without such a filter is not scaffolded and is untouched by everything here.

**Grammar.** `<chain><start>[-<end>][*][(<lo>-<hi>)]`, comma separated; white space and line breaks are ignored. A bare or starred entry is a fixed-length substitution of those scaffold positions; `(lo-hi)` marks a stretch whose designed length varies in that range. A token that does not parse, a range that runs backwards, a length range with its limits swapped or two entries that overlap switch the analysis off for the run, with the reason in the run's notes. Nothing is repaired.

**Segments.** The designed stretches of a chain are grouped into segments: stretches separated by at most one undesigned residue are one segment (`MERGE_GAP`; 27-33 and 35 are one loop, 59 and 97 are not). The extent of a segment is the span from its first to its last designed position, so an undesigned residue inside it (34 of the VHH's first loop) belongs to it and an undesigned residue outside it (26 or 36) does not. Everything else is scaffold framework. This is the campaign's own definition and not the usual CDR convention: the conventional CDR-H1 starts at 26, the spec designs from 27, and the analysis follows the spec.

**Naming.** Segments are named from the modality, falling back to numbers when the scaffold does not fit the expected pattern (a note says so):

| Modality | Segments | Framework |
|---|---|---|
| VHH | CDR1, CDR2, CDR3 by order, when the spec has three segments | FR1 to FR4; positions 37, 44, 45 and 47 are the **FR2 face** (`FR2-face`) when the scaffold carries Cys22 and Trp36 |
| scFv, Fab | H1-H3 and L1-L3, per domain, when each chain holds whole domains of three segments and the scaffold's J region (WGxG heavy, FGxG light) tells them apart | `H-FR1` to `H-FR4` and `L-FR1` to `L-FR4`; between two domains of one chain: `inter-domain` |
| ARP and anything else | "designed segment 1..n" | "framework" |

CDR language is used only where it is true: an ankyrin repeat has no CDRs, and a VHH-named run whose spec lists other than three segments gets numbers. The FR2 face is a VHH idea: on a lone VHH the former VL interface of framework 2 is an exposed hydrophobic patch, and the natural hallmark substitutions (F/Y37, E44, R45, G47) are what a nanobody does about it. The face positions are set apart from the CDRs before the segments are grouped, because 37 sits one residue from the end of the first loop.

## How a design is aligned

Because `Scaffold_Sequence_Retained_Fraction` was mandatory at 1.0, every position the spec does not list keeps the scaffold residue exactly. The invariant stretches between the designed segments are therefore exact anchors. The analysis reads the scaffold's own sequence from the scaffold file (the path in the spec; failing that a file of the same name next to the campaign folder or in a `scaffolds` folder beside it), lays the scaffold out as invariant, substituted and variable pieces, and finds the one choice of lengths for the variable pieces under which every invariant piece matches the design exactly (`align_chain`).

A design is **reported, not guessed at** when it aligns in no way, or in more than one: the block counts it under `unaligned`, names the first few with the reason, and leaves it out of every share. A real scaffolded VHH campaign aligns every one of its designs.

The analysis is switched off for the run, with a reason, when the scaffold file cannot be found (a campaign moved off the machine that ran it has usually left its scaffold behind), when the file cannot be read, when the retained fraction was not mandatory at 1.0 (then positions outside the spec may have changed and the anchors are not exact), or when no design aligns. A scaffolded run whose analysis is off carries no `paratope` or `paratope_rejected` block, a note on the run with the reason, and a `diversity_paratope` block whose fold is off and whose paratope and loop-length axes are `unknown` for that reason.

## What the blobs carry

All shapes are in `docs/schema/campaign.schema.json` (`paratope_block`, `diversity_paratope`, `design_para`, `epitope_diversity`).

**`runs[].paratope`** (accepted designs) and **`runs[].paratope_rejected`** (rejected designs, same shape, so the contrast is visible):

| Field | Meaning |
|---|---|
| `source`, `scaffold`, `modality`, `naming` | `scaffold_edits`; the scaffold file name; the run's modality; `VHH`, `antibody` or `generic` |
| `segments[]` | `name`, `chain`, `scaffold` (first and last designed position), `designed` (the designed stretches), `length` (`[lo, hi]` of the stretch the spec lets vary, else the span) and `variable` (`[first, last, lo, hi]` of each varying stretch) |
| `framework_designed` | designed positions outside every segment (a VHH's 37, 45, 47) |
| `aligned`, `unaligned`, `unaligned_designs` | designs of the **whole run** placed and not placed, and the first few unplaced with the reason |
| `n`, `group` | designs of this group that went into the block; `accepted` or `rejected` |
| `share` | region to percent of the group's interface residues, one decimal, regions with none left out |
| `framework_share` | percent of interface residues outside every designed segment |
| `loop_use` | designs per set of designed segments touched (an empty set is a design that binds through framework alone) |
| `framework_positions` | the scaffold positions of framework contacts, with the scaffold residue and the number of designs |
| `note`, `notes`, `chains`, `no_interface` | the sentence that travels with the numbers; reader and layout notes; the chains of a multi-chain binder; designs with no interface residue |

`share` and `loop_use` are over the interface residues BindCraft wrote (`Interface_Binder_Residues`), so the contact criterion is BindCraft's, not Binderscope's recomputed one. Its amino-acid letters were checked against `Binder_Sequence` for every entry of a real campaign with no disagreement.

**`designs[].para`** (every exported design of a scaffolded run that aligned): `segments` (region to interface residues), `loops` (designed segments touched, in scaffold order), `framework` (interface residues outside every segment), `lengths` (the length of each varying stretch in this design), and for a VHH `face` (the designed face positions and the residue the design has there) and `face_mutated`. `framework_at` lists `[region, scaffold position]` for each framework contact so a page can call it out by position (an extension of the contract the pages were first written to).

**`runs[].diversity_paratope`** replaces the fold figure for a scaffolded run. Four axes, each with a `status` of `observed`, `unknown` or `off`; `unknown` and `off` carry a `why` in words, because a number that is silently missing reads as a bug:

- `fold`: `off`, "every design is built on the same scaffold, so fold similarity measures the scaffold, not the designs".
- `paratope`: `profiles`, the number of distinct sets of designed segments touching the target among accepted designs (one if all use the same), over `n` designs in `n_families` families.
- `loop_length`: the segment whose length the spec lets vary most (CDR3 for the VHH), `values` (one per family, ascending), `distinct`, and `classes` with `class_ranges`, `class_families` and `class_gap`. A **class** is a chain of lengths each within `class_gap` (3) residues of the next; this is a convention of the module, written beside the result. `off` when the spec fixes every segment's length; `unknown` when fewer than two families placed a design or siblings of one family disagree (one backbone cannot have two lengths).
- `epitope`: the epitope spread, below.

**Epitope spread** is computed for every run, scaffolded or not, because it does not depend on fold or modality: `diversity.runs.<id>.epitope` ([families.md](families.md)). In a scaffolded run's `diversity_paratope` it is the same object.

`campaign.diversity.runs.<id>` also changes for a scaffolded run: `fold` (off, with the reason), `basis_off` (`fold` and `both`, the bases that include fold distance, are removed from `clusters`, `counts`, `sweep`, `advice` and `replay_by_basis`), `basis_default` (`site`) and a `fold_off` flag. The binding-site basis is exactly what it was.

## What a real campaign shows, and two rules it settled

On a real scaffolded VHH campaign the shape of the answer is consistent: CDR3
dominates the interface, CDR1 and CDR2 take most of the rest, and framework
contact is a small share among accepted designs but several times larger among
rejected ones. That gap is the point of showing both — a reader given only the
accepted figure learns much less, and on a campaign with a mandatory
off-paratope filter the gap is selection, not biology.

The measured values are not reproduced here: they come from unpublished work.
Two rules that came out of checking them against a first write-up by hand are
worth stating, because both change the answer and neither is obvious:

- **A conventional CDR boundary is not the right one; the campaign's own edit
  spec is.** Gly26 is conventionally the start of CDR-H1, but a campaign that
  did not design position 26 has it as framework. Counting it as CDR1 shifted the
  rejected-design framework share. Assign regions from the
  spec, never from the numbering convention. See **How regions are assigned**.
- **"CDR3 length" means the stretch the spec lets vary**, not the whole
  designed CDR3, which can be several residues longer. Reporting the designed
  span instead silently changes every length class.

## Limits to carry into the copy

- Region boundaries are this campaign's own edit spec, not IMGT or Kabat. The FR2 face positions are scaffold numbers, which equal the Kabat-like 37/44/45/47 only for a scaffold numbered like the VHH one.
- Interface residues are BindCraft's `Interface_Binder_Residues`: the contact criterion is BindCraft's.
- The shares are shares of interface residues pooled over designs, and the tallies by design (`loop_use`, `framework_positions`) count designs, not families: siblings of one backbone are near copies in the paratope. The diversity axes count families.
- A multi-chain binder (scFv with its domains on one chain is the simple case; a Fab has two chains) is read through the same layout, with the scaffold's chains matched to the design's chains in order. No real scFv or Fab campaign has exercised this; the tests use synthetic scaffolds, and the block says so in `notes`. The reader's own limit still stands: the analysis after it (poses, interface, families) sees only chain B as the binder.
- A handful of families is too few to call a trend. Binderscope flags estimates below 30 families as low-confidence, and `diversity_paratope.low_confidence` and `epitope.low_confidence` carry that.
- The scaffold file has to be reachable. See "How a design is aligned".

## API

| Name | Role |
|---|---|
| `parse_edits(text)` | the spec as sorted `Edit` entries, or `ParatopeError` with the reason |
| `read_scaffold_file(path)` | `{chain: {number: letter}}` from a scaffold structure file (gemmi) |
| `find_scaffold_file(recorded, run_dir, extra)` | the recorded path, else the same name beside the campaign |
| `build_layout(edits, scaffold, modality, label)` | the scaffold divided as the spec says: pieces, segments, region of every position |
| `align_chain(pieces, seq)`, `place_design(layout, id, seq, interface, chain_map)` | align one design and tally where its interface residues fall |
| `has_scaffold(run)`, `off_reason(run)`, `analyse_run(run)` | whether a run is scaffolded, whether the record allows the analysis, and the analysis: `(RunParatope, None)`, `(None, reason)` or `(None, None)` |
| `RunParatope.block(group)`, `.design_blobs()` | `runs[].paratope` / `paratope_rejected`, and `designs[].para` |

`families.diversity_paratope`, `families.epitope_diversity` and `families.loop_length_classes` build the diversity axes from it ([families.md](families.md)); `export.paratope_items` puts the blocks on `runs[]`.

## Tests

`tests/test_paratope.py` builds a synthetic VHH-like scaffold file, designs with the real campaign's edit spec at every combination of loop lengths, and an interface placed region by region, so every number asserted is known by construction. It covers the grammar, the layout and the region of every scaffold position, alignment (including mutated anchors, wrong lengths and an ambiguous placement), the shares, the face, the off paths, naming for VHH, scFv, Fab and an ankyrin-repeat scaffold, the reader, the epitope spread, the fold switch against an unscaffolded twin of the same run, and the blocks against the JSON schema.

## See also

[families.md](families.md) · [export.md](export.md) · [bindcraft2_reader.md](bindcraft2_reader.md) · [data_contract.md](data_contract.md) · [antibody.md](antibody.md) · [architecture.md](architecture.md)
