# Target frame

The target frame (`src/binder_review/target_frame.py`) puts every design back on the full-length target and asks what the construct hid. This page defines its numbering, what it stores, how designs are placed, what each context check measures and when it warns or fails, and what the exporter takes from it. It is for developers and advanced users.

## Why it exists

BindCraft designs a binder against a construct: a stretch of the target chain, sometimes with loops deleted, cut from a larger protein that may be an oligomer, may carry glycans and may be anchored in a membrane. A binder that fits the construct can still overlap the rest of the chain, another subunit or a sugar that BindCraft never saw, or make its contacts at a place where the chain was cut. The frame superposes the target of each design on the reference structure of the full-length protein, matching residues by number and weighting the fit around the binder (see Placement), and then measures what lies near the placed binder.

The frame produces metrics. Pass, warn and fail are applied afterwards from the thresholds in `profile.checks.context` ([profile_spec.md](profile_spec.md)); `verdicts()` is the same rule set in Python. Without a reference structure none of this can run; see [Without a reference](#without-a-reference). Every result is a screen: the rest of the chain and the other subunits are the reference structure placed rigidly, and the design's own target is never identical to the reference chain, so distances are estimates.

## Quick start

```python
from binder_review import target_frame as TF
from binder_review import structure as S

frame = TF.build_frame("full_length.cif", "construct.pdb", runs)   # runs: {run id: RunData}
frame.save("work/my-target/target")     # frame.json + frame.npz; TargetFrame.load(dir) reads them back
frame.assign(runs)                      # sets uniprot_offset (and the accession) on every run's TargetConstruct

cx = S.load_complex(design.structure_path)        # the design's own frame, construct numbering
pl = TF.place_design(frame, cx, "run01")          # Placement: R, t, fit, rmsd, iface_rmsd, per_residue, apply(...)
placed = pl.apply(cx.binder)                      # binder heavy atoms in the reference frame
tc = [n + pl.offset for n in contacts_in_construct_numbers]    # contacted residues, reference numbers
res = frame.context(placed, "run01", tc)          # ContextResult: metrics, glycan, ctx, octx, diag
```

`TF.check_run(frame, run)` does all of this for every design of a run that has a structure (accepted designs first, in rank order, then rejected ones) and returns `DesignCheck` rows. `python -m binder_review.target_frame --reference full_length.cif --construct construct.pdb --run run01=PATH --run run02=PATH --out work/my-target` builds the frame for BindCraft 1 runs, writes `work/my-target/target/frame.json` and `frame.npz` and a per-design table `work/my-target/target_checks.json` (no sequences), and with `--report FILE` the markdown tables of `report_tables`. The options `--uniprot`, `--reference-id`, `--reference-chain`, `--partner-chains` and `--placement` are the ones of `import` ([cli.md](cli.md)), which runs the frame as one stage of the pipeline and keeps it in `work/<project>/target/`.

## Inputs

| Input | Meaning |
|---|---|
| reference structure | the full-length protein, as an mmCIF or a PDB file; optional (`None` in Python, `--reference-cif` left out on the command line: [Without a reference](#without-a-reference)). A PDB-format file carries its assemblies in `REMARK 350`, and the subunits are chosen and made from them as from an mmCIF's; only the summary in `frame.meta["dimer"]` (`assemblies`, `generators`, `oligomeric_details`, `oligomeric_count`) is read from mmCIF records and stays empty for a PDB file. Coordinates carry the three decimals of the PDB format |
| reference chain | the chain of the target in the reference. `None` in Python, `auto` on the command line (the default): the chain that carries the construct's sequence, and the frame says which one it took and why ([Chains of the reference](#chains-of-the-reference)); a name takes that chain. Only this chain is the target |
| partner chains | what the binder is checked against besides the target chain (`--partner-chains` of `import`). `None` or `"auto"` (the default): the other subunits, that is chains of the same protein that belong to the target's assembly, and the copies the assembly's symmetry operators make from them when the file holds only part of it. `()` or `none`: nothing. `"all"`: every other polymer chain of the assembly. A list or a comma list: exactly those chains. Another molecule is never counted unless it is named or `all` is asked for; ligands, ions, additives and waters never are |
| assembly | the assembly of the reference file the subunits are taken from (`--assembly`); default the first one that holds the target chain |
| reference id | the name of the reference in labels; the file name without its extension unless it is given. A PDB id (a digit and three letters or digits) is written in upper case |
| construct | the structure file that was given to BindCraft, numbered 1 to N, with the target in chain `A` |
| runs | one entry per run: `RunData`, a `RunSpec`, `{"hotspots": [...], "numbers": [...]}` or a plain list of hotspots, always in construct numbers. The list of hotspots may be empty (BindCraft can be run without hotspots). A run given by hotspots alone is assumed to carry the whole construct |
| accession | the UniProt accession of the target; optional. With it the numbering is checked against UniProt and the annotations, the strip and the membrane plane come from public records ([reference_data.md](reference_data.md)). Without it the frame comes from the reference structure and the construct alone and says so in `frame.meta["notes"]` |
| `config` | optional overrides of `DEFAULT_CONFIG` (below); they are stored in the frame |

`FrameError` (a `ValueError`) is raised when the inputs do not fit: a reference chain that is not a polymer, no chain that carries the construct (or a construct that is split over two chains), a named chain that holds less than 90 percent of the construct, a partner that is not a polymer chain or is the target itself, an ambiguous or non-zero numbering offset, run residues that are not in the construct file or not in the reference chain, hotspots outside a run's target.

## Chains of the reference

A reference file that holds more than the target chain holds several different things, and the frame does not treat them alike. `analyse_reference` reads the file and says, for every chain, what it is, how it relates to the target, whether the binder is checked against it, and why; `binder-review chains` prints it, the build log repeats it, the web service shows it before a build, and `frame.meta["composition"]` keeps it with the frame. Nothing is chosen silently: when the target or the partners were not named, the reason is written down, and a situation the file cannot settle is left to the person.

**Which chain is the target.** A named chain is taken as named (a warning says so when it holds less than 90 percent of the construct). Otherwise the construct's sequence decides: the chain that carries it (at least 90 percent of its residues, found by sequence and not by numbering) is the target. When several chains carry it equally, which is what a homo-oligomer looks like, the first chain in the file is taken, so the default is predictable. The reason says that all of them fit and that another can be chosen, and gives the CA RMSD of the construct's own coordinates against each: copies of one molecule usually fit equally well, and when one fits clearly better (by 0.25 Å or more) the reason says to choose it if the construct was cut from it. The frame does not decide that for you. A construct that no single chain holds, or that is split over two chains, is refused with that sentence; a file with several protein chains and no construct to choose by is refused until a chain is named.

**Which chains are partners.** Each other chain falls into one of four kinds, and they are not mixed up:

| Kind (`role`) | What it is | In `auto` |
|---|---|---|
| `subunit` | the same protein as the target (the same entity, or at least 90 percent identical over 20 residues or more) and part of the target's assembly. A homodimer's other chain, or a copy that the assembly's symmetry operators make | counted. A clash with it means the binder cannot sit on the assembled protein |
| `other_molecule` | a different polymer in the file: a receptor, a ligand protein, a nanobody or Fab used to crystallise the target, DNA | **not** counted. The file cannot say whether it belongs to the complex one cares about or was added to get a crystal, and counting a chaperone would flag every design that binds near it. Name it, or ask for `all`, to count it; a clash with it then means the binder competes with that molecule, and is worded so |
| `outside_assembly` | a chain of the file that the target's assembly does not hold: a second copy of the target in the asymmetric unit that belongs to another assembly, a neighbour in the crystal | not counted; counted only when named |
| `target` | the chain the designs are placed on | |

The assembly is the first one in the file that holds the target chain (`--assembly` names another). A mmCIF carries assemblies in `_pdbx_struct_assembly`, a PDB file in `REMARK 350`; without any, every chain that has the target's sequence is a subunit, and the composition says that it had nothing to go by. A hint such as "named like an antibody-like binder or a crystallisation aid" is shown beside a chain whose name in the file suggests it. It decides nothing.

**Copies the assembly describes.** Most entries from the PDB hold one subunit of a crystallographic oligomer; the rest of the assembly is described by operators and is not in the file. The frame applies the operators of the chosen assembly to the chains it lists, names the copies like the PDB's own assembly files (`A-2` for operator 2 of chain A), and counts the copies of the target as subunits. A copy of another molecule is made too and listed, and is counted only on request. An assembly of more than 40 copies keeps those that come within 30 Å of the target chain. Sugars attached to a chain move with its copy. The result is the same, atom for atom, as reading the PDB's own assembly file (checked on streptavidin, 1STP, against `1stp-assembly1.cif`).

**What is never a partner.** Ligands, ions, buffer and crystallisation additives (glycerol, sulfate, PEG), detergent and waters are listed with a count and with the target residues they touch (within 4 Å), and are never measured. A detergent molecule or a sulfate counted as a partner would produce clashes that mean nothing about the protein. A ligand or ion bound to the target itself is not measured either, which matters when it sits in the epitope; the composition says which touch the target, and `frame.meta["notes"]` names the ones that are not named like additives. N-linked glycans are treated as before: those of the target and of the chains counted as partners are measured.

**What the composition holds** (`frame.meta["composition"]`, `ReferenceChains.data`): `target` (`chain`, `how` as `given`, `only_chain`, `only_match`, `first_of_equal` or `best_match`, `why`, `alternatives`, `warning`), `partners` (`mode` `auto`, `none`, `all` or `given`, `chains`, `why`), `chains` (per chain or copy: `chain`, `kind`, `entity`, `name`, `n_residues`, `first`, `last`, `role`, `used`, `why`, `generated`, `copy_of` and `operator` for a copy, `touches` and `closest` against the target, `construct` with what it holds of the construct, `hint`), `assembly` (`id`, `oligomeric`, `records`, `available`, `generated`), `non_polymers`, `waters`, `glycans`, `cases` (`single_chain`, `subunits_in_file`, `subunits_by_symmetry`, `other_molecules`, `crystal_neighbours`, `target_ambiguous`), `notes`, `summary` and, when no target could be found, `problem`.

## Numbering

| Numbering | Used for |
|---|---|
| construct | what BindCraft wrote: the numbers of the file given to BindCraft, with gaps where a run deleted loops. Used in `RunData`, in the per-design analysis, for the hotspots as written in the settings and for `orig_resi` (shown on hover) |
| reference | the residue numbers of the reference chain (author numbering of the reference file) |
| UniProt | the numbering of the UniProt entry of the target. It must equal the reference numbering; the check is by sequence |

Everything the frame hands to the exporter is numbered like the reference chain, which is UniProt numbering when an accession is given (the fields say "UniProt" in both cases). A construct number `i` is reference number `i + offset`. `frame.label(u)` gives the one-letter code with the number, for example `A45`; `frame.uniprot_to_construct(u, run_id)` returns `None` outside the construct file, or outside that run's target when a run id is given.

The offset is found, not assumed. `_vote_offset` compares the construct with the reference chain residue by residue, by number, and counts for every possible offset how many residues agree. The offset with the most agreeing residues wins, so gaps in either sequence need no special case. A tie is an error rather than a guess, and the construct must match the reference at 90 percent or more of its residues. With an accession, the reference chain is voted against the UniProt sequence the same way; it must match at 90 percent or more and its offset must be 0, because a different offset would need every output renumbered. SIFTS segments (`uniprot_segments`) are recorded as a second witness. All counts are in `frame.meta["numbering"]` (`offset`, `reference_offset`, `construct_first`, `construct_last`, `checks`: construct against reference, construct against UniProt, reference against UniProt, SIFTS). Without an accession the native range is taken as 1 to the last residue of the reference chain.

Designs are matched to the reference by number, so a reference number that two residues carry (an insertion code, an alternate residue type) cannot be matched: `frame.ambiguous_numbers` lists such numbers, `frame.ref_ca` leaves them out, they take no part in any placement and the build warns ("reference chain A: n residue numbers are carried by more than one residue (insertion codes?), first [...]; designs are matched to the reference by residue number, so these residues are left out of the placement"; it is in `frame.meta["warnings"]`).

Each run is read with its own construct. The residues of its target are the numbers its structures carry. The deleted ranges follow from them: `removed` holds the gaps between the stretches of the run's residues inside its construct, and `removed_native` holds every stretch of the native chain (1 to its length) that the run lacks, the ends included. `RunSpec.offset` overrides the offset for a run that was built from a different construct file.

## What the frame stores

`frame.save(dir)` writes `frame.json` (`meta`, schema `binderscope.target_frame/1`) and `frame.npz` (`arrays`). Nothing else is state, so a loaded frame behaves like the built one. `TargetFrame.load` raises `FrameError` for an unreadable folder or another schema.

`frame.meta` keys: `schema`, `generated`, `inputs` (file name and sha256 of the reference and the construct), `config`, `chain`, `partner_chains` (names of the chains or generated copies, subunits first), `composition` (what the reference holds and what was done with each chain; none without a reference or in a frame saved before it existed), `reference` (`pdb`, which holds the reference id, `assembly` (the assembly the subunits come from, null when the file has no assembly records), `resolution`, `label`, `given` and `class`, the classification of the reference file; see Context check standing), `numbering`, `runs`, `annotations`, `dimer`, `membrane`, `glycans`, `glycan_sites_in_construct`, `warnings` (a missing record, a skipped check) and `notes`.

`frame.arrays` holds the heavy atoms of the reference chain (`A.*`, with per-atom `A.sasa`), of every partner chain (`P_<chain>.*`) and of every glycan (`G<k>.*`, with the Asn ND2 position `G<k>.anchor`), the ND2 positions of the glycan sites inside the construct (`glycan.anchor_xyz`), per-residue arrays over the reference chain (`res.number`, `res.one`, `res.ss`, `res.ca`, `res.sasa`, `res.rsa`, `res.sasa_chain_gly`, `res.sasa_dimer`, `res.buried_partner`) and per-run arrays (`run.<id>.sasa_alone`, `run.<id>.sasa_rest`).

- **Secondary structure** is the three-state, DSSP-like assignment of the whole reference chain (`structure.assign_ss`: H, E, L), cut to the construct.
- **Solvent accessibility** (probe 1.4 Å, 960 points, the radii and sphere of Biopython's Shrake-Rupley, so equal settings give equal numbers) is kept per residue in four settings: the run's target alone (what BindCraft saw), the full reference chain alone, the chain with its sugars, and the assembly with all sugars. `res.rsa` is the relative accessibility of the chain alone (residue area over the Gly-X-Gly maximum of Tien et al. 2013, limited to 0 to 1), so a residue that the rest of the chain buries is not drawn as surface.
- **The area a run's residues lose to the removed regions** is the run's target alone minus the target together with the rest of the chain and the sugars attached to it (`covered_area`). **The area buried by the other subunits** is the chain with its sugars minus the assembly with all sugars (`partner_buried`).
- **Glycans** are found through the covalent links of the file (Asn ND2 to a sugar C1, then sugar to sugar, so a branched chain is followed to its end), never by chain name. The sugar residues may be in a branched chain of their own, single ligands of the polymer chain, or carry the polymer's own chain id (newer entries number the sugars of chain A as part of chain A; the structure reader files them as `A:branched`). A glycan belongs to the polymer chain of its Asn. In `target_pdb` and `glycan_atoms` the sugars of the in-construct sites get the chain ids C, D, E and so on in site order; the source residues of the file are in `glycan_atoms.<site>.src`.
- **The rest of the chain**, per run: the reference-chain residues that the run's target lacks (everything outside its construct plus its deleted stretches) and the sugars attached to them. Sugars at sites inside the run's target belong to neither the partner nor the rest; they feed the glycan distances.

## Frame strip annotations

The inspector draws the target as a strip: domains, the construct, cut ends, glycan sites and disulfide bonds. The frame derives the data from public records and the reference file, and exports it as `profile.frame` (`frame.profile_frame()`): `native_range` (`[1, length]`), `segments`, `glycan_sites` and `disulfides`. All of it is in `frame.meta["annotations"]`, each part with its source; a source that cannot be read is left out and noted in `warnings`.

| Part | Source |
|---|---|
| `topology` | UniProt "Topological domain" and "Transmembrane" features |
| `domains` | CATH domains of the reference chain from SIFTS; `domains_fine` adds the Pfam and InterPro spans |
| `segments` | built from the two above, see below |
| `glycosylation` | UniProt N-linked glycosylation sites, each with its sequon (N-X-S/T, X not P, read from the UniProt sequence), whether the sequon is intact, whether the site lies in the construct, and the sugars the reference file shows on it for every chain |
| `disulfides` | UniProt disulfide bonds with the SG-SG distance the reference file reports for each chain |
| `unresolved`, `observed` | the native range minus the residues the reference chain contains, and the stretches it contains |
| `isoforms`, `chains`, `signal`, `functional_sites` | UniProt isoforms (with the ranges each one lacks and whether it still contains the construct), chains, signal peptide, active and binding sites (with `in_construct`) |

**Segments.** Cytoplasmic and transmembrane regions become segments of kind `x`. When the entry has an extracellular topological domain and SIFTS gives CATH domains, the stretch between the extracellular start and the first domain is a linker (kind `l`), and each CATH domain becomes a segment named by its CATH homology name, with kinds `v`, `c`, `b`, `s`, `h` in order (a sixth and later domain reuses `h`). A domain runs on to the start of the next one; the last runs to the end of the extracellular region when it reaches the last observed residue. A target without such a topological domain gets no segments from the frame and a warning, and the profile can carry its own `frame` instead. The exporter completes the profile's `frame` from the frame when the profile has none. When an accession is given and the profile's strip differs from the derived one in ranges, kinds or sites, the exporter keeps the profile's strip and warns. A profile can also name the segments in its own words: where a segment of the profile's `frame` has the same start, end and kind as a derived one, the exporter writes the profile's label (and phrase) into the target block's strip and its per-residue domain labels (`dom`).

Without an accession no UniProt or SIFTS record is read: topology, domains, isoforms and functional sites stay empty, and the glycosylation sites and disulfide bonds are the ones the reference structure itself shows, with the sequon read from its residues.

## The oligomer partner

The partner chains (see [Chains of the reference](#chains-of-the-reference)) are chains of the reference file or copies the assembly's operators make from them. Their atoms (several partner chains are concatenated, subunits first) and their sugars form the partner environment, in the same frame as the reference chain. The assembly records are summarised below and are what the subunits are chosen by.

`frame.meta["dimer"]` holds:

- the assembly records (`assemblies`, `generators`) and `oligomeric_details`, `oligomeric_count`, `identity_only` (every operator of the assembly is the identity, so the file already holds the whole assembly) and `chains_in_assembly`;
- the buried surface, SASA(chain) plus SASA(partner) minus SASA(both) over polymer atoms (`bsa_chain_a`, `bsa_partner`, `bsa_total`), and the reference-chain residues that lose at least `olig_min_area` (5 Å²) (`interface_residues`, `n_interface`) or more than 0.5 Å² (`interface_residues_any`);
- an overlap check between the reference chain and the partner: `min_distance`, `closest_pair`, `pairs_below` (heavy-atom pairs closer than 2.2, 2.5 and 3.0 Å) and `min_distance_with_sugars`;
- `two_fold`: a rigid superposition of the reference chain on the first partner chain, with `rmsd`, `n`, `angle`, `axis`, `point`, `screw`, `R` and `t`. Residues are paired by number when the two chains agree at 90 percent or more of the shared numbers, and by sequence otherwise (the second chain of many entries is numbered from 301). It is read only when the first partner is a subunit, and not when the two do not superpose within 5 Å (`two_fold_skipped` then says so). For a homodimer with a true two-fold axis the angle is near 180 degrees and the screw translation near zero.

For a homodimer the operator `two_fold` also gives a second check: the binder moved by it is the copy that sits on the other subunit, and the two copies must not overlap. The smallest distance between them and the number of atoms of the copy within 3 Å of the binder are reported as `octx.sym_min` and `octx.sym_n3` (information only, no verdict). For other oligomers the operator maps the reference chain onto the first partner chain only.

Without partner chains the partner metrics are `None`, the partner part of the Context check is absent and `caps.olig` is false in the exported profile. That is also what an explicit `none` gives, and what a file gives that holds other molecules but no subunit: the exporter's reason for it (`reference_lacks`) is worded for a file with no other chain, and `frame.composition["partners"]["why"]` says which of the three it was.

## Membrane plane (optional)

A plane that marks the side of the construct where the membrane would be. It exists only when the UniProt topology names a transmembrane region; otherwise `frame.meta["membrane"]["known"]` is false, the plane is a placeholder labelled as such (the target block still carries it), `mem_excess` is `None` for every design and the exporter leaves the membrane check out (`caps.membrane` false).

- the centroid is the centroid of the CA atoms of the union of the runs' constructs;
- the axis is the unit vector from the centroid to the CA of one end of the reference chain, the end nearer the transmembrane region in sequence: the last residue when the region starts beyond the middle of the chain's residue range, the first residue otherwise;
- the offset is the distance from the centroid to that CA minus `membrane_offset` (6 Å);
- a binder atom `x` is past the plane when `(x - centroid) . axis > offset`, and `mem_excess` is the largest such value over the binder's heavy atoms (negative: clear by that much).

The plane is labelled approximate. `frame.meta["membrane"]["diagnostics"]` compares it with other geometry when it can: the angle to the two-fold axis of an oligomer, the heights of the chain end and of the construct along that axis, and, with an accession, an AlphaFold DB model placed on the reference chain by CA superposition: how far its transmembrane region lies beyond the plane and its mean pLDDT. These are plausibility checks. A single plane built from one construct and one chain end cannot follow a protein whose termini lie at different heights, so `mem_excess` is a distance to this construction, not to a membrane. Read it as "far from the membrane side", and use it as a coarse screen: the membrane flag is separate from the overall verdict.

## Run targets

Every run is read with its own construct and its own hotspots. The run entry `frame.run_info(run_id)` holds `label`, `offset`, `hotspots_construct` (as written in the settings), `hotspots` (reference numbers), `resi_ranges`, `removed`, `removed_native`, `first`, `last`, `n_resi`, `cut_points`, `epitope`, `sequence_matches_reference` (the run's target has the reference residue at every number it carries) and `consistent` (every structure of the run carries the same residues, from the reader).

- **Cut points** (`cut_points_for`): the first and last residue of the run's target, unless the construct reaches a native end (the first residue is within one residue of the native start, or the last is the native end), and both flanks of every removed stretch.
- **Epitope**: the hotspots plus the residues of the run's target with a heavy atom within `epitope_radius` (5.0 Å) of a heavy atom of a hotspot residue, taken on the reference chain. It is a definition, not an observation. A run without hotspots has an empty epitope.
- **Union of the runs**: `frame.union_residues()` is the union of the constructs (the campaign's `target.resi`); `frame.union_array({construct number: value}, fill=None)` lays a per-residue value over it; `frame.hotspots_touched(run_id, contacted)` returns the contacted residues that are hotspots of the design's own run, the value `design.hot` holds.

## Site A1 and the view axis

`frame.target_block()` gives the campaign target its site `A1`, the intended epitope, and the unit vector `normal` that every 3D view is oriented by (`sites.A1` in [data_contract.md](data_contract.md), section 4.1).

- **With hotspots**, `center` is the mean CA position of the hotspots of the selected runs, `residues` is their epitope (the hotspots and their neighbours) and `normal` is the unit vector from the CA centroid of the construct to `center`.
- **Without hotspots.** BindCraft can be run without any hotspot (an empty `Target_Hotspot` column and no hotspots in the settings file, or no settings file; a BindCraft 2 campaign without its metadata file is the same), and then there is no intended epitope. When none of the selected runs records a hotspot, `target_block(site_residues=...)` and `inspector_block(site_residues=...)` build A1 from where the binders sit instead. `site_residues` are residue numbers in reference numbering; the pipeline takes them from the pose records (`pipeline.site_residues`: the residues that at least half as many accepted designs contact as the most contacted residue, or all designs with a pose when none was accepted). A1 then has their mean CA position as `center`, them as `residues` and a `source` that reads "no hotspots recorded: the residues the designs contact most". Without `site_residues`, or when none of them lies in the construct, `center` is the CA centroid of the construct, `residues` is empty and `source` reads "no hotspots recorded and no design contacts: the whole construct, viewed along its thinnest axis". As soon as one selected run has a hotspot, `site_residues` is ignored and A1 is the hotspot site.
- **The view axis.** `normal` is always a unit vector. When `center` coincides with the centroid (within 1e-6 Å), `normal` is `default_view_axis(ca)`: the direction along which the CA atoms vary least (the right singular vector of the smallest singular value of the centred coordinates), signed so that its largest component is positive, so a view along it shows the flattest face. It is `[0, 0, 1]` for fewer than three atoms or atoms without any spread.

A1 built from contacts says where the designs bind, not where they were meant to bind. It orients the views and names the site of a design and of a mode (`design.site`, `modes.<id>.site`); no check or verdict reads it, and the epitope check of a run without hotspots has nothing to test.

## Placement

`place_design(frame, design, run_id, *, fit=None)` superposes the design's own target chain (chain A of its structure file) on the reference chain over the CA atoms of the residues they share: a rigid transform from a Kabsch fit. Residues are matched by number (construct number plus the run's offset is the reference number), so deleted loops and any other absent residue simply take no part, and nothing assumes contiguous numbering. A reference number that two residues carry is not matched either (see Numbering). At least 10 shared residues are needed; with fewer the build stops. The reference is the crystal chain, never the construct file. `fit` defaults to the frame's `geometry.fit`, which `import --placement` sets.

**Why the fit is weighted around the binder.** BindCraft predicts and relaxes its own copy of the target, and that copy is not the reference chain: it differs a little everywhere, and more where one part of the chain moved against another (a loop, a domain that hinges). A fit over the whole target spreads that disagreement over all residues, so the part the binder sits on is off by whatever the rest of the chain disagrees by, and the binder goes with it. In the case that led to the current default the design's copy differed from the reference by about 1.5 Å at the contacted residues, and the whole-target fit left a quarter of the designs 1.5 to 3.5 Å off at the epitope (one case, not a benchmark). The context checks measure the distance from the binder to parts that exist only in the reference (the other subunits, the rest of the chain, the sugars), so they need the binder to be right where it matters, at the interface.

| `fit` | `import --placement` | What it does |
|---|---|---|
| `"interface"` (default) | `interface` | weights every shared CA by its distance to the nearest binder heavy atom, measured in the design's own frame (`interface_weights`): 1 up to `iface_full` (8 Å), then `exp(-((d - 8) / 6)^2)` (`iface_decay`, 6 Å), never below `iface_floor` (0.01), so the far part of the target still keeps the transform from drifting but counts for little. It then trims and refits (`interface_fit`): residues near the binder (weight at least 0.3, within about 14.6 Å of a binder atom) that deviate by more than `iface_cutoff` (2.5 Å) under the transform are left out, and so are residues farther away that deviate by more than three times that (7.5 Å), because a far part that hinged against the part around the binder would act as a long lever on the rotation. The rounds go on until the set stops changing, at most 5, and a round that would leave fewer than `iface_min_n` (12) near residues is not taken |
| `"all"` | `whole` | every shared CA counts the same: the plain Kabsch fit |
| `"core"` | `core` | refits on the residues a trimmed fit keeps (the `core_*` row below) |

When fewer than `iface_min_n` (12) shared residues lie near the binder, or the file has no binder atoms, there is nothing to weight around. The design is then placed by the whole-target fit: `Placement.fit` is `"all"` and `fit_note` says why (for example "only 8 target residues lie near the binder (at least 12 are needed): placed by the whole-target fit"). The exporter adds a note to the run when that happened to some of its designs.

The design's target is not the reference chain, so deviations are expected and reported:

| Field of `Placement` | Meaning |
|---|---|
| `R`, `t` | the transform, `x' = x @ R.T + t`; `rf()` gives `[R (9, row-major), t (3)]` |
| `fit`, `fit_note` | the fit the transform comes from (`"interface"`, `"all"` or `"core"`; it differs from the one asked for only in the fallback above) and the reason when it does, else an empty string |
| `rmsd`, `n` | CA RMSD over all shared residues under that transform, and their number. `rmsd` is the exported `place_rmsd`. It is never smaller than `global_rmsd`, and under the interface fit it is larger: the fit gives up some match over the far part of the target for a good one where the binder sits |
| `iface_rmsd`, `iface_n` | CA RMSD over the shared residues near the binder (weight at least 0.5, within about 13 Å of a binder heavy atom, in the design's own frame) and their number; NaN and 0 when there are none. This is the number that says how far the context distances can be trusted. Exported as `place_iface_rmsd` and `place_iface_n` |
| `global_rmsd` | the CA RMSD over all shared residues that the whole-target fit gives, the smallest any rigid transform reaches. Exported as `place_global_rmsd` |
| `per_residue`, `deviation`, `max_deviation` | the CA deviation of every shared residue, keyed by reference number |
| `core_rmsd`, `core_n`, `core_numbers`, `dropped_numbers()` | the trimmed refit: refit on the residues that deviate by at most `core_cutoff` (2.0 Å) and repeat, at most `core_cycles` (5) times, until the set stops changing. A trim that would leave fewer than half of the residues, or fewer than three, is not applied. `core_rmsd` is that fit's RMSD and `dropped_numbers()` lists the residues it rejected, the mobile part of the target. It is the transform itself only with `fit="core"` |
| `n_mismatch`, `warnings` | shared residues whose type differs from the reference; text warnings: residues outside the run's target, residues of the run's target the design lacks, mismatches, and an `iface_rmsd` above `place_warn_rmsd` (1.5 Å) |

`fit_fields()` returns `{fit, iface_rmsd, iface_n, global_rmsd, fit_note}` (NaN as `None`), the part of a placement that pose records, `target_checks.json` and the exporter carry. `check_design` copies `core_rmsd` into `octx.rms_head`. The local deviation of the contacted residues (`contact_dev_max` in `target_checks.json`) says how far a distance near them can be trusted.

**What depends on the placement.** The interface analysis (contacts, hydrogen bonds, salt bridges, buried polars and areas, patches, liabilities), the contacted residues, the hotspots touched, the epitope numbers, the binding modes and the families come from the design's own structure in its own frame and are the same for every fit. The placement decides where the binder lies against the parts of the protein that only the reference has, so these depend on it: the distances and atom counts against the other subunits and against the rest of the chain (`olig_min`, `olig_n3`, `olig_n4`, `rest_min`, `rest_n3`, `rest_n4` and the nearest-pair and clash diagnostics), the glycan distances, `mem_excess`, the twin check of a homodimer (`octx.sym_*`), the Context verdicts built from them, and every coordinate in the reference frame (`design.ca`, `pdb_b`, the structure files, the thumbnail `view` and `vc`). `olig_n`, `cut_n` and `cov_n` count contacted residues and do not move with the fit.

The exporter writes one warning for every run that has designs with an `iface_rmsd` above `place_warn_rmsd` (the whole-target RMSD when a record has none), saying how many: the design's own target then differs from the crystal chain at the interface, and distances to the other subunit and the rest of the protein are less certain. The core RMSD in the same warning says whether the rigid part of the target still fits.

## Without a reference

`build_frame(None, construct, runs)` (the command line without `--reference-cif`) builds the frame from the construct alone, and `frame.reference_given` is then false (`meta["reference"]["given"]`; a frame saved before the field existed always had a reference). What follows from that:

- **The construct is the frame.** The construct file's chain A is the reference chain. Each design is superposed on it by the same placement as before (the fit options apply), so all designs share one frame and the viewer can draw them on one target; numbering stays the construct's (offset 0, or the accession's when `--uniprot` is given, in which case the construct's numbers must equal UniProt numbering and the build stops with a message when they do not). The placement RMSDs are against the construct, which is BindCraft's own input, so they are small and say little about the design.
- **Nothing outside the construct is known.** There are no partner chains, no sugars and no membrane plane (`membrane.known` false, labelled "no reference structure was given"), no SIFTS record is read, nothing is reported as unresolved, and `native_range` is the construct's span unless an accession gives the protein's length. The construct's own ends are therefore not reported as cut ends, only the stretches a run deleted inside it (`cut_points`, `removed`). A stretch deleted inside the construct is not "the rest of the protein" here: the construct file holds it, but no check is run for it.
- **`context()` returns no metric.** Every key of `METRIC_KEYS` is `None`, `glycan` is empty and `ctx` is empty (exported as `null`); `octx` holds `rms_head` only. A `0` would read as "checked, clear", which is the one thing a design that was never tested must not look like. `verdicts()` has no part to judge and gives no `overall`.
- **What is unaffected** is everything computed from the designs' own structures in their own frame: the interface analysis, the contacted residues, the hotspots touched, families, binding modes, the paratope analysis, near-miss rescue and the specificity panel. A build with and without a reference gives identical values for all of them (tests/test_no_reference.py compares them design by design); the epoch hashes differ only because they carry construct numbers instead of reference numbers when the reference is offset from the construct.

The exporter records which situation it is in, because two look the same in `caps` and are not: with no reference `profile.caps_off` gives `no_reference` for `olig`, `rest_ctx`, `glycan` and `membrane`; with a reference that holds nothing for a check (no other chain, nothing outside the construct, no sugar site, no transmembrane region) it gives `reference_lacks` ([profile_spec.md](profile_spec.md), section 4). The Overview words them differently, the manifest carries both (`target.reference_given`, `caps_off`, a note on every run), and the run record `runs/<run>/runrecord.json` has `target_frame.reference_given` and `context_checks`.

## Context check standing

The context checks measure the binder against the reference as one static model. The metrics are the same whatever the reference is, but what they mean depends on it, so the exporter classifies the reference file itself (`classify_reference`, on the command line as well as in any service): `experimental` when the file records an experimental method (`_exptl.method`, `EXPDTA`) and no model record, `predicted` when it has a model record (the `_ma_` categories of AlphaFold DB and model archives, naming a pLDDT), a theoretical-model method, or no method and one B-factor per residue between 0 and 100 (how a pLDDT is stored), and `unknown` otherwise. `unknown` and a file that records both a method and a model are treated as predicted. The result carries its `evidence` in words and is recorded in `meta["reference"]["class"]`, `profile.target.reference` and `manifest.target.reference_class`.

The Context check is **advisory by default** and **hard only when the reference is experimental** or when `--context-check hard` or `checks.context.hard: "hard"` says so (`advisory` forces the other way; the option wins over the profile). An advisory check keeps its findings and reasons and can lead to REVIEW through the soft-check rule, never to NO PASS on its own. This is because a predicted reference places a region that has no fixed position (a flexible linker, a domain with a high predicted aligned error) essentially at random: on one real VHH campaign every one of 18 BindCraft-accepted designs collided with such a region, the check alone turned all of them to NO PASS, and that hid the sequence liabilities of 15 of them that were real. With the check soft the same designs read REVIEW with the liabilities listed. The resolved setting and its reason are in `profile.checks.context` (`hard`, `hard_setting`, `hard_basis`) and in the manifest (`target.context_check`); the pages say "advisory" next to the Context reason wherever a verdict is explained. Without a reference the check is not advisory, it is absent.

## Context checks

All distances use the binder's heavy atoms in the reference frame (hydrogens are dropped). A binder atom counts for `n3`, `n4` or `n6` when its nearest atom of the set in question is closer than 3, 4 or 6 Å (`DEFAULT_CONFIG["geometry"]["n_close"]`).

| Metric | Definition |
|---|---|
| `olig_min` | the smallest distance from a binder atom to the partner chains or their sugars |
| `olig_n3`, `olig_n4` | binder atoms within 3 and 4 Å of them |
| `olig_n` | contacted residues buried at the partner interface (they lose at least `olig_min_area`, 5 Å², to the partner chains and their sugars) |
| `rest_min` | the smallest distance from a binder atom to the rest of the chain: the reference-chain residues that the run's target lacks, and the sugars attached to them |
| `rest_n3`, `rest_n4` | binder atoms within 3 and 4 Å of the rest of the chain |
| `cut_n` | contacted residues within `cut.k` residues (4) of a cut point of the run, by residue number |
| `cov_n` | contacted residues that lose more than `cover.min_area` (10 Å²) when the rest of the chain and its sugars are present |
| `mem_excess` | the signed distance past the membrane plane, largest over the binder's atoms; `None` without a plane |
| `glycan` | per glycan site inside the run's target: the distance from the nearest binder atom to the sugar atoms of that site on the reference chain, or to the Asn ND2 atom when the reference models no sugar there; `glycan_min` is the smallest |

A metric whose environment does not exist (no partner chains, nothing outside the run's target) is `None`. Only residues of the run's target count as contacted. Also in `diag`: the nearest atom pairs (`partner_pair`, `rest_pair`, with residue and atom names), steric clashes (binder atoms within `clash`, 2.2 Å, of the partner or the rest of the chain: `partner_clash`, `rest_clash`), the split of the atoms within 4 Å of the rest of the chain by region (`before` and `after` the construct, `deleted_loop`, `glycan`, `other`), the dimer-buried contacts, the cut contacts, the covered residues with their areas (`covered`) and the contacted residues that the whole assembly buries (`buried_in_assembly`, a superset).

The inspector objects are `ctx` (`ref`, `removed` (the native-chain ranges the run lacks), `nearest`, `n3`, `n4`, `n6`, `pair` (binder residue number, rest residue number), `pair_resn`, `pair_glycan` when the nearest rest atom is a sugar, `covered`, `covered_all` (every residue of the run's target that loses more than the minimum area), `cut` with `points` and `near`) and `octx` (`olig_min`, `olig_n3`, `olig_n4`, `olig_buried`, `sym_min`, `sym_n3`, `rms_head`).

### What a finding says about what it ran into

A clash with another subunit, a clash with a different molecule and a clash with the rest of the target's own chain are three different claims, and the reasons say which. Against another subunit of the assembly: the assembled protein has no room for the binder there. Against a different molecule that was counted as a partner: the binder competes with it for the site, which matters only if that molecule is part of the complex one cares about. Against the rest of the target chain: the full-length protein has material where the binder sits that the construct left out. The distance and the chain are in the reason. The wording follows `diag["partner_role"]` and `diag["partner_roles"]` (the role of the nearest partner chain and the roles of every chain within 4 Å); a frame or a record without them says "the other subunit", as before. `olig.roles` of the Inspector block and `olig.labels` carry the same distinction (other subunit, symmetry copy, different molecule, neighbour outside the assembly).

### What warns and what fails

`verdicts(metrics, diag=None, checks=None)` returns a level (`pass`, `warn`, `fail`) and a plain-language reason per part. A missing metric takes no part and never fails a design.

| Part | Fails | Warns |
|---|---|---|
| `partner` | `olig_n3` at or above `partner.fail_n3` (10) | any atom within 3 Å, or `olig_min` below `partner.warn_min` (4.0 Å), or `olig_n` at or above `partner.warn_buried` (3) |
| `rest` | `rest_n3` at or above `rest.fail_n3` (10) | any atom within 3 Å, or `rest_min` below `rest.warn_min` (4.0 Å) |
| `cut` | never | `cut_n` at or above `cut.warn_n` (2) |
| `cover` | never | `cov_n` at or above `cover.warn_n` (2) |
| `overall` | the worst of the four parts above | |
| `membrane` | `mem_excess` above 0 | never; it is a separate flag and not part of `overall` |

The defaults are `DEFAULT_CONFIG["checks"]`, and the built-in default profile carries the same values; the pages read the keys from `profile.checks.context`. The `near` bands (`bad_lt` 3, `warn_lt` 4, `info_lt` 8 Å) are used by the inspector for its rest-of-chain finding: a distance below `info_lt` is worth a note, and without atom counts the bands alone grade clash, touch and close. Two of the keys act when the frame is built, not when the pages judge a design. `cut.k` and `cover.min_area` define what `cut_n` and `cov_n` count, so they are fixed in the stored metrics, and the pipeline builds the frame with `DEFAULT_CONFIG`. A profile that sets other values changes the text of the pages but not the counts; change the counts with `build_frame(config=...)` or keep the profile equal to the frame. The other keys (`fail_n3`, `warn_min`, `warn_buried`, `warn_n`) are applied to the stored metrics when the pages and the exporter judge a design.

## API

| Name | What |
|---|---|
| `build_frame(reference_cif or None, construct_pdb, runs, uniprot_acc=None, *, chain=None, partner_chains=None, assembly_id=None, config=None, use_alphafold=True, reference_id=None)` | builds a `TargetFrame`. `chain` and `partner_chains` are decided as described in [Chains of the reference](#chains-of-the-reference); `assembly_id` selects the assembly the subunits come from. `use_alphafold` switches off the AlphaFold plausibility check of the membrane plane |
| `RunSpec(run_id, hotspots, numbers=None, label="", offset=None, sequence=None, consistent=None)` | what the frame needs to know about a run |
| `TargetFrame.save(dir)`, `TargetFrame.load(dir)` | persistence |
| `frame.construct_to_uniprot(i, run_id=None)`, `uniprot_to_construct(u, run_id=None)`, `residue(u)`, `label(u)`, `labels(us)`, `assign(runs)`, `union_array(...)` | numbering |
| `frame.run_ids`, `run_info(run_id)`, `hotspots(run_id=None)`, `epitope(run_id=None)`, `union_residues()`, `hotspots_touched(run_id, contacted)` | the per-run targets |
| `place_design(frame, design, run_id, *, fit=None)`, `frame.place(...)`, `Placement` | placement of a `structure.Complex` or the path of a design file; `fit` is `"interface"`, `"all"` or `"core"` (`PLACEMENT_FITS`), default the frame's `geometry.fit` |
| `frame.context(binder, run_id, contacted=(), *, config=None)`, `ContextResult` | the context of a binder that is already placed (heavy-atom `Atoms` or an `(n, 3)` array); `ContextResult` holds `metrics` (exactly `olig_min`, `olig_n3`, `olig_n4`, `olig_n`, `rest_min`, `rest_n3`, `rest_n4`, `cut_n`, `cov_n`, `mem_excess`), `glycan`, `glycan_min`, `ctx`, `octx`, `diag` and `verdicts()` |
| `verdicts(metrics, diag=None, checks=None)` | the pass, warn and fail rules above |
| `check_design(...)`, `check_run(...)`, `DesignCheck` | placement (both take `fit=`), contacts (heavy-atom pairs within 4.5 Å in the design's own frame, as everywhere on the pages), context and verdicts of one design or a whole run; `DesignCheck.to_dict()` is a row of `target_checks.json` |
| `analyse_reference(reference, construct_sequence=None, *, chain=None, partner_chains=None, assembly_id=None, construct_atoms=None)`, `describe_reference(...)`, `format_reference_chains(data)`, `ReferenceChains` | the chain composition of a reference and the decision about the target and the partners ([Chains of the reference](#chains-of-the-reference)); `describe_reference` returns the plain JSON, `ReferenceChains.atoms(name)` the heavy atoms of a partner (a chain of the file or a generated copy), `.role(name)` its role. `frame.composition`, `frame.partner_role(chain)`, `frame.partner_label(chain)` read it back from a frame |
| `auto_partner_chains(sf, chain, min_shared=20, min_identity=0.9)`, `reference_label(id)` | the earlier partner rule (chains that share residue numbers and residue types with the target chain), kept for callers; the frame no longer uses it, because it misses a subunit that is numbered differently and takes a crystal neighbour for a subunit |
| `classify_reference(path or None)`, `frame.reference_given`, `frame.reference_class` | the reference's own method and model records read as `experimental`, `predicted`, `unknown` or `none`, with `treated_as`, `method`, `resolution` and `evidence`; whether the frame has a reference |
| `frame.target_block(runs=None, name=None, site_residues=None)`, `run_blocks(runs=None)`, `inspector_block(runs=None, site_residues=None)`, `target_pdb(runs=None)`, `profile_frame()` | the target side of the blobs; `runs` selects runs by id (default all); `site_residues` builds site A1 when no selected run has a hotspot (see Site A1 above) |
| `frame.residue_table(run_id=None)`, `partner_buried()`, `covered_area(run_id)`, `assembly_loss(run_id)`, `summary()` | per-residue table, areas, short summary |
| `report_tables(frame, rows)` | markdown tables of placement and context results for a set of designs |
| `plane_from_points`, `membrane_excess`, `trimmed_fit`, `interface_weights(dist, full, decay, floor)`, `interface_fit(P, Q, dist, ...)`, `cut_points_for`, `ss_records`, `default_view_axis(ca)` | building blocks; `interface_fit` returns `(R, t, keep, weights, interface)`, with `interface` false when it fell back to the plain fit |

`DEFAULT_CONFIG["geometry"]` holds `contact_cutoff` (4.5 Å, `model.CONTACT_CUTOFF`), `clash` (2.2 Å), `n_close` (3, 4 and 6 Å), `epitope_radius` (5.0 Å), `olig_min_area` (5 Å²), `membrane_offset` (6 Å), `place_warn_rmsd` (1.5 Å, the limit for the CA RMSD near the binder), `fit` (`"interface"`), `iface_full` (8 Å), `iface_decay` (6 Å), `iface_floor` (0.01), `iface_cutoff` (2.5 Å), `iface_min_n` (12), `core_cutoff` (2.0 Å), `core_cycles` (5), `sasa_probe` (1.4 Å) and `sasa_points` (960). The placement settings are stored in the frame (`frame.config`), so a frame built with another `fit` places differently; the pipeline puts the `--placement` option into the key of the frame and of every pose.

## What the exporter takes from the frame

| Blob field | From |
|---|---|
| campaign `target` | `frame.target_block()`: `name`, `uniprot`, `construct`, `length`, `resi`, `resn`, `ss`, `rsa`, `ca`, `atoms`, `hotspots`, `epitope`, `glycan`, `glycan_xyz`, `glycan_atoms`, `membrane`, `sites`, `olig`, `rest`, `orig_resi`, `numbering`, `reference`, `unresolved`, `hotspots_by_run`, `epitope_by_run`, `dom`, `frame`. Coordinates are rounded to 0.01 Å and areas to 0.1 Å². `sites.A1` has the centroid of the pooled hotspot CA atoms as `center` and the unit vector from the construct CA centroid to it as `normal`; with no hotspot in any run it is built from `site_residues` or from the whole construct and carries a `source` (see Site A1 above) |
| campaign `runs[].target` | `frame.run_blocks()[run id]`; `runs[].settings.hotspots` is `frame.hotspots(run id)` and `hotspots_input` holds the construct numbers |
| inspector blob (target side) | `frame.inspector_block()`: `target_pdb` (always chain A, whatever the chain of the reference is called, so the pages can tell the target from the binder, chain B), `target_seq`, `target_resi`, `t_ss`, `hotspots`, `epitope`, `glycan`, `glycan_xyz`, `glycan_atoms`, `membrane`, `runs`, `olig`, `rest` |
| `design.m.olig_*`, `rest_*`, `cut_n`, `cov_n`, `mem_excess` | `ContextResult.metrics` (full precision; the exporter rounds) |
| `design.m.place_rmsd`, `place_iface_rmsd`, `place_iface_n`, `place_global_rmsd` | `Placement.rmsd`, `iface_rmsd`, `iface_n` and `global_rmsd` (`core_rmsd` is the value for the rigid part) |
| `design.prov.fit`, `design.prov.fit_note` | `Placement.fit` and `fit_note` |
| `design.glycan` | `ContextResult.glycan` |
| `design.ctx`, `design.octx` | `ContextResult.ctx` and `.octx` (`check_design` adds `octx.rms_head`) |
| `design.tc`, `design.hot` | the contact set of the interface module ([interface.md](interface.md)), converted to reference numbers with the run's offset. The exporter compares it with the contacts and the hotspots touched that the frame found in the placed design, and reports a mismatch |
| `design.mem_margin` | `-mem_excess` |
| per-residue arrays over the target (for example pLDDT and buried area of the design's own target) | `frame.union_array({construct number: value}, fill=None)`, so a run that lacks part of the union gets `None` there |

`ctx.covered_all` is the same list for every design of a run, because it depends on the run only. Sequences never appear in `frame.json` or `target_checks.json`. The capability flags follow from what the frame found: `olig` needs partner chains and `olig_min` on the designs, `rest_ctx` needs rest-of-chain traces, `glycan` needs glycan sites in the construct and `membrane` needs `mem_excess` ([profile_spec.md](profile_spec.md)).

## Verification

A frame checks itself while it is built, and the results are in `frame.meta`: the identity counts of `numbering.checks`, the `warnings` of records that could not be read, the assembly summary and the overlap check in `dimer`, and the per-run flags `sequence_matches_reference` and `consistent`. Look at them on your own data before you trust a project:

```python
frame = TF.TargetFrame.load("work/my-target/target")
print(frame.summary())                       # reference, offset, construct, runs, dimer, warnings
print(frame.meta["numbering"]["checks"])     # how many residues agree, at which offset
print(frame.meta["warnings"], frame.meta["notes"])
```

When you change this module, protect at least: the offset search (a tie is refused), the invariance of a placement under a rigid motion of the whole design, that a part of the target which hinges away from the binder does not pull the binder off its place under the interface fit, the save and load round trip, probes with a known answer (binder atoms placed on the partner give distance 0 and all counts, a far point gives all counts 0, a point on a glycan atom gives glycan distance 0), and that the blobs still validate against the schemas ([data_contract.md](data_contract.md)).

## Limits

- Ligands, ions, additives and waters are never measured, nor is a ligand bound to the target; a binder that overlaps a cofactor in the epitope is not flagged.
- The reference is a static model even when it is given. The pages word a context finding as "in the reference structure" and not as a measurement of the real protein, and the Context check cannot decide a verdict on a predicted reference (see Context check standing).
- The rest of the chain and the other subunits are the reference structure placed rigidly. Deleted loops, the stretch before the construct and flexible interface loops move in solution, and the design's own target differs from the reference chain. A distance of a few angstrom is an estimate, and a small overlap can be absorbed by a loop move. The verdicts are screens. With the interface fit the transform is right at the binder and as far off elsewhere as the design's copy of the target differs from the reference there: a part of the target that moved against the epitope shows up as a mismatch of the layers (the rest of the chain, the other subunit) and not as a binder out of place. `iface_rmsd` and `global_rmsd` tell which case you have.
- The frame places by the target's CA atoms alone. A design target that differs strongly from the reference (a different conformation of a domain) has a large `rmsd`; compare it with `iface_rmsd` (near the binder) and `core_rmsd` (the rigid part) before you doubt the placement.
- Glycans: only the sugars that the reference models, in one conformation. A site without a modelled sugar is measured to its Asn ND2 atom; real glycans are larger and mobile.
- The membrane plane is a construction from one construct and one chain end; it does not know where the membrane is. It needs a transmembrane region in the UniProt topology.
- Solvent accessibility uses Biopython's radii, not Rosetta's ([structure_api.md](structure_api.md)); areas are for screening, and `cover.min_area` and `olig_min_area` are thresholds on them.
- The reference numbering must equal UniProt numbering when an accession is given; there is no renumbering table. The symmetry operators of the chosen assembly are applied to make the subunits the file lacks, but not the non-crystallographic or helical operators of a very large assembly beyond the 40 nearest copies. Only one reference chain is the target: a construct that is split over two chains of the reference, and a target of several chains (BindCraft can design against one), are not supported, and the web service refuses a campaign whose target has several chains rather than read its second chain as the binder. A different molecule is counted only when asked for: whether a chain belongs to the complex is a judgement the file cannot make, and nothing here makes it.
- The domain strip needs an extracellular topological domain in UniProt and CATH domains in SIFTS; other targets need a `frame` in the profile.
- `cut.k` and `cover.min_area` are fixed when the frame is built (see above).
- `epitope` is the hotspots plus their neighbours within 5 Å, a definition and not an observation.
- Site A1 of a project without hotspots is made from where the designs bind. It orients the views; it is no statement about the intended epitope.
- Reference records come from the cache ([reference_data.md](reference_data.md)); a missing record turns the part that needs it off and adds a warning.

## See also

[architecture.md](architecture.md) · [data_contract.md](data_contract.md) · [profile_spec.md](profile_spec.md) · [export.md](export.md) · [reference_data.md](reference_data.md) · [structure_api.md](structure_api.md) · [bindcraft2_reader.md](bindcraft2_reader.md) · [GUIDE.md](GUIDE.md)
