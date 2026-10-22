# Structure primitives API

`binder_review.structure` holds the structure primitives that the rest of Binderscope builds on: loading PDB and mmCIF files, geometry, contacts, solvent accessibility, secondary structure, hydrogen bonds, TM-align and a few sequence helpers. This page is for developers. It lists the rules a caller must respect, the data structures and the functions, and the known limits.

The module needs numpy, scipy, gemmi and tmtools. It needs no PyRosetta, no DSSP program and no Foldseek, and it never touches the network. Import it as `from binder_review import structure as S`.

## Rules that matter to callers

- **The residue number is the key, never the position.** A construct can have deleted stretches, so residue numbers need not be contiguous. Use the residue table (`atoms.residues`): `index_of(number)`, `mask_for_numbers(...)`, `gaps()` and `missing_numbers()`. Per-residue arrays (SASA, pLDDT, secondary structure, the `ResidueTable` columns) are ordered by residue ordinal, which is file order, with one entry per residue of the table they came from.
- **Atoms of one residue are contiguous** in an `Atoms` table. Every PDB and mmCIF writer does this, and the residue table relies on it.
- **Transforms are `(R, t)`** with `x' = x @ R.T + t` (`apply_transform`). `kabsch(P, Q)` returns the pair that maps `P` onto `Q`.
- **Chain breaks** (in secondary structure and in computed amide hydrogens) are detected from the C(i) to N(i+1) distance (above 2.0 angstrom), not from numbering, so deleted stretches are safe.
- **Hydrogens.** BindCraft 1 relaxed PDBs carry explicit hydrogens. `load_complex(strip_h=True)` (the default) removes them from `target`, `binder` and `atoms` but keeps them in `complex.hydrogens`, so `bindcraft_interface_residues` can still count them. SASA, contacts and DSSP are heavy-atom quantities. To use explicit hydrogens in `hbonds`, load with `strip_h=False`.
- **B-factor is pLDDT on a 0 to 100 scale** for BindCraft output. The CSV columns use 0 to 1.
- **Only the first altloc and the first model** (by default) are read.

## Data structures

| Name | What |
|---|---|
| `Atoms` | A flat atom table: `xyz` (N, 3) float64, `name`, `element` (upper case), `resname`, `resnum` (int), `icode`, `chain`, `bfactor`, `hetero`. Properties and methods: `.residues` (a `ResidueTable`), `.res_index` (atom to residue ordinal), `.n_residues`, `.sequence`, `.plddt`, `.is_hydrogen` (H and D), `.heavy()`, `.select(mask_or_idx)`, `.select_residues(numbers=... or ordinals=...)`, `.atom(resnum, name)`, `.ca_xyz()`, `.ca_by_number()`, `.backbone_arrays()` (N, CA, C and O per residue, NaN when absent), `.per_residue(values, "sum" or "mean" or "max" or "min")`, `.transformed(R, t)`, `.with_xyz()`, `.copy()`, `Atoms.concat([...])`, `Atoms.empty()` |
| `ResidueTable` | `number`, `icode`, `name`, `chain`, `one` (one-letter code), `start` and `stop` (the atom slice), `ca` (index of the CA atom, or -1), `.sequence`, `.plddt` (the CA B-factor), `.index_of(number, chain=None, icode=None)`, `.indices_of(numbers, missing="skip")`, `.has(n)`, `.gaps()`, `.missing_numbers(lo, hi)`, `.mask_for_numbers(set)`, `.atom_indices(i)` |
| `Complex` | `path`, `atoms`, `target`, `binder` (both `Atoms`), `hydrogens` (every H of the target and binder chains found in the file), `other`, `target_chain`, `binder_chain`, `hydrogens_stripped`, `issues` (a list of `ValidationIssue`), `.errors`, `.has_hydrogens`, `.target_numbers`, `.binder_numbers`, `.target_plddt`, `.binder_plddt`, `.target_sequence`, `.binder_sequence`, `.combined()` (target atoms, then binder atoms) |
| `ValidationIssue` | `code`, `severity` (`error`, `warning` or `info`) and `message`. Codes: `missing_chain` (error), `extra_chains`, `hydrogens`, `numbering_gaps` and `bfactor_range` (info), `hetatm` (warning: waters and non-protein residues dropped) and `incomplete_backbone` (warning) |
| `StructureFile` | The result of `read_structure`: `groups` (name to `Atoms`), `kinds` (`polymer`, `branched`, `ligand` or `water`), `links` (`Link(kind, a, b, distance, name)` with `AtomRef(chain, resnum, resname, atom)`; kinds `covale`, `disulf`, `metalc`, `hydrog`, `other`), `resolution`, `entry_id`, `.polymer(chain)`, `.polymer_chains()`, `.glycan_groups()` and `.find_atom(chain, resnum, atom)`, which returns `(group, atom_index)` or `None` |

## Loading

- **`load_complex(path, target_chain="A", binder_chain="B", strip_h=True, strict=True, keep_hetero=False, model=0) -> Complex`.** PDB or mmCIF (gemmi). Pass `binder_chain=None` for a single chain, such as the truncated target. Waters are always dropped; other HETATM residues are dropped unless `keep_hetero`. Modified residues such as selenomethionine map to their parent residue (MSE becomes MET, with the coordinates converted). With `strict`, a missing chain or an empty file raises `StructureError`; otherwise the problem is recorded in `issues`.
- **`read_structure(path, model=0, include_waters=False) -> StructureFile`.** Any chain or group. A polymer chain keeps its chain id. Branched chains (glycans) have the kind `branched`. Non-polymer ligands that share a polymer chain are `A:ligands`, and waters are `A:waters` when requested. Covalent links and disulfides are read from `_struct_conn` or from `LINK` and `SSBOND` records. Assembly expansion is not done.
- **`deposited_ss(path, model=0)`** gives chain to `{residue number: "H" or "E"}` from the HELIX and SHEET records or the mmCIF `struct_conf` and `struct_sheet_range` tables.
- **`to_pdb_lines(atoms, serial0=1, chain=None, resnum_offset=0, bfactor=None)`** and **`write_pdb(path, *groups)`** write ATOM lines, with TER after each group.

## Geometry

`kabsch(P, Q, weights=None) -> (R, t, rmsd)` is a least-squares superposition by SVD with a proper rotation; it needs at least three point pairs. `apply_transform`, `invert_transform` and `rmsd(P, Q)` (no superposition) complete the basics.

`superpose_residues(mobile, ref, atom="CA" or "backbone", numbers=None, mapping=None) -> Superposition(R, t, rmsd, n, numbers)` matches residues by number. `numbers` restricts the reference residues used, and `mapping` maps mobile numbers to reference numbers when the numbering differs. `Superposition.apply(xyz_or_Atoms)` applies it. `rmsd_by_number(a, b, atom="CA", superpose=True, numbers=None)` gives the RMSD over residues with the same number in both tables.

## Contacts

- **`residue_contacts(a, b, cutoff=4.0, heavy_only=True) -> Contacts`** has `ia` and `ib` (residue ordinals), `num_a` and `num_b` (residue numbers), `min_dist`, `n_atom_pairs` and `cutoff`, with `.a_residues`, `.b_residues` (sorted numbers), `.pairs()`, `.as_records()` and `.n_atom_contacts`. **`atom_contacts(a, b, cutoff, heavy_only)`** gives the atom index pairs and their distances. Both use a k-d tree.
- **`bindcraft_interface_residues(cx, cutoff=4.0, include_hydrogens=True) -> list[int]`** is BindCraft 1's interface rule: the binder residues with any atom within 4.0 angstrom of any target atom. Hydrogens count, because BindCraft runs the rule on the relaxed PDB with explicit hydrogens; `include_hydrogens=False` gives a heavy-atom-only set. BindCraft writes the `InterfaceResidues` column for one fixed AlphaFold model (model 2 with the default multimer settings), so a saved `_model1` file can give a slightly different set. `format_bindcraft_interface([1, 5])` gives `"B1,B5"`.

## Surface

- **`sasa_atoms(xyz, element, probe=1.4, n_points="default", radii=None)`** is a vectorised Shrake-Rupley SASA per atom. `n_points` is an integer or a preset (`"fast"` 48, `"default"` 200, `"accurate"` 960; `SASA_PRESETS`). The golden-spiral sphere and the element radii (`VDW_RADII`, with 1.70 angstrom as the default radius) follow Biopython's `Bio.PDB.SASA.ShrakeRupley`, so equal settings give comparable numbers; individual atoms can differ by a sampling point or two. Hydrogens count if they are in the arrays, so strip them first for heavy-atom values.
- **`sasa(atoms, probe=1.4, n_points="default", radii=None, heavy_only=True) -> SasaResult(atom, residue, relative, total)`.** `relative_sasa(residue_sasa, one_letter)` divides by `MAX_ASA`, the theoretical maxima of Tien et al. (2013). It gives NaN for non-standard residues and can exceed 1 slightly.
- **`interface_dsasa(cx, probe=1.4, n_points="default", target_alone=None, radii=None) -> InterfaceSasa`** is the binder alone minus the binder in the complex (`dsasa_binder`, with the per-atom `binder_atom` and per-residue `binder_residue` arrays), the same for the target, `dsasa_total` (both sides, Rosetta's convention), `binder_buried_fraction` and the per-atom SASA arrays in the complex and alone. Pass `target_alone` (the `target_sasa_alone` of a previous call) to skip recomputing the target when many designs share one target. Atom arrays index the heavy-atom subset.

## Secondary structure

`assign_dssp(atoms, hb_cutoff=-0.5) -> str` is an eight-state assignment (H, G, I, E, B, T, S and `-`) from N, CA, C and O only, so it needs no hydrogens. It computes Kabsch-Sander hydrogen-bond energies (`0.084 x 332 x (1/rON + 1/rCH - 1/rOH - 1/rCN)`, with a cut-off of -0.5 kcal/mol), places each amide H 1.0 angstrom from N in the direction from the preceding O to the preceding C, and gives no H to Pro. It finds n-turns, helices (G, H, I), parallel and antiparallel bridges and ladders (E needs two consecutive bridges, otherwise B) and bends (a kappa angle above 70 degrees). When states overlap the precedence, highest first, is H, E, B, G, I, T, S. Residues without a full backbone are `-`.

`ss3_from_dssp(dssp, helix="HGI", strand="E", fill_bulges=True)` reduces to H, E and L. With `fill_bulges`, a single residue that is `-`, T, S or B between two strand residues becomes E. `assign_ss(atoms, ...)` does both steps, and `ss_fractions(ss)` returns the shares of H, E and L. Pass `helix="H"` to count alpha helix only.

## Hydrogen bonds and salt bridges

**`hbonds(a, b=None, max_dist=3.5, max_h_dist=2.5, min_angle_dha=120.0, min_angle_acc=90.0, use_hydrogens=True) -> HBonds`** finds hydrogen bonds between two tables, or inside one when `b` is `None`. Only protein polar atoms take part.

- Donors: backbone N (not Pro), Arg NE, NH1 and NH2, Lys NZ, Asn ND2, Gln NE2, His ND1 and NE2, Trp NE1, Ser OG, Thr OG1, Tyr OH.
- Acceptors: backbone O and OXT, Asp OD1 and OD2, Glu OE1 and OE2, Asn OD1, Gln OE1, His ND1 and NE2, Ser OG, Thr OG1, Tyr OH.
- Criteria: donor to acceptor at most `max_dist`. With a hydrogen (an explicit H atom within 1.25 angstrom of the donor, or the backbone amide H placed from geometry), H to acceptor at most `max_h_dist` and a D-H-A angle of at least `min_angle_dha` for at least one H. Without one (side-chain donors in a heavy-atom table), the angle antecedent-A-D at the acceptor must be at least `min_angle_acc`. The per-row `method` is `explicit`, `computed` or `heavy`.
- With `b=None`, bonds inside one table are found: `a_index` is the donor, `b_index` the acceptor and `donor_is_a` is all true. Atoms of the same residue and the backbone pair N(i) to O(i-1) are skipped. The interface analysis uses this form on the combined target and binder table to find which atoms are satisfied.
- Result arrays: `a_index`, `b_index`, `donor_is_a`, `distance`, `h_distance`, `angle_dha`, `angle_acc`, `method`, with `.records(a, b)` (dicts with residue numbers and atom names) and `.residue_pairs(a, b)`. A Ser, Thr or Tyr pair that passes in both directions is listed twice.

**`salt_bridges(a, b=None, cutoff=4.0, include_his=False) -> list[SaltBridge]`** finds opposite-charge side-chain groups whose centroids are within `cutoff`: Arg guanidinium (NE, NH1, NH2, CZ), Lys NZ, Asp carboxylate (OD1, OD2) and Glu carboxylate (OE1, OE2), and His (ND1, NE2) when requested. Fields: `a_resnum`, `a_resname`, `b_resnum`, `b_resname`, `distance` and `positive_in_a`.

## Fold similarity

- **`tm_align(xyz1, seq1, xyz2, seq2) -> TMResult`** (tmtools) aligns two CA traces. The sequences only seed the alignment and may be `None`. `tm_shorter` is normalised by the shorter chain (the larger score, and the number the families code uses), `tm_longer` by the longer chain, and `tm_1` and `tm_2` by chain 1 and chain 2. The result also has `rmsd`, `aligned_length`, `R` and `t` (chain 1 onto chain 2), `len_1` and `len_2`. `tm_align_atoms(a, b)` takes two `Atoms` tables and skips residues without a CA.
- **`tm_matrix(chains, n_jobs=None, progress=None) -> TMMatrix(tm, tm_longer, rmsd, aligned)`.** The items are `(xyz, seq)` tuples, bare (n, 3) arrays or `Atoms`. Each unordered pair is aligned once and mirrored, so the matrices are exactly symmetric, and the diagonal TM is 1. `n_jobs` defaults to the CPU count minus one (a spawn pool; 1 runs in process, as do fewer than four chains), and `progress(done_rows, total_rows)` is called as rows finish. A script that uses the pool needs the usual `if __name__ == "__main__":` guard, because the workers are spawned.

## Sequence helpers

`three_to_one(name, strict=False)` and `one_to_three(code, strict=False)` map modified residues to their parent and give `X` or `UNK` for unknown ones; `THREE_TO_ONE`, `ONE_TO_THREE` and `AA_ORDER` are the tables. `KYTE_DOOLITTLE` and `gravy(seq, scale=None)`. `net_charge(seq, ph=7.4)` and `isoelectric_point(seq)` use the Henderson-Hasselbalch equation with the pKa values in `PKA` (N terminus 8.0, C terminus 3.6, D 3.9, E 4.3, H 6.0, C 8.3, Y 10.1, K 10.5, R 12.5). `sequence_hydrophobic_fraction(seq, residues=BINDCRAFT_APOLAR)`.

**`surface_hydrophobicity(binder_atoms, surface_cutoff=2.0, hydrophobic=BINDCRAFT_APOLAR)`** and **`sc_neighbors(atoms, ...)`** approximate BindCraft's `Surface_Hydrophobicity`, which is the fraction of surface residues of the binder alone that are hydrophobic. Rosetta chooses the surface layer; this module reimplements it from memory of Rosetta's defaults and has not been checked against Rosetta itself. A residue is on the surface when its side-chain neighbour count is below 2.0, where each other residue contributes a logistic distance term (midpoint 9 angstrom, measured between CB atoms, CA for Gly) times an angle term `((cos + 0.5) / 1.5)^2` relative to the CA to CB vector (a virtual CB for Gly). Hydrophobic means `BINDCRAFT_APOLAR` (A, G, C, I, L, M, P, V, F, W, Y). Name the result "a recomputed approximation of BindCraft's value", never "BindCraft's value".

## Known limits

- SASA uses Bondi-like radii (Biopython's table), not Rosetta's, so dSASA differs from BindCraft's `dSASA`.
- DSSP is a re-implementation, not a bit-for-bit copy of `mkdssp`, and it has no beta-bulge or sheet bookkeeping beyond the one-residue fill.
- `hbonds` has no energy term; its counts are related to Rosetta's energy-based counts but are not equal to them.
- Insertion codes are stored, but `index_of(number)` returns the first residue with that number unless `icode` is given.
- Only the first altloc and the first model are read, and assemblies are not expanded.
- The TM-align matrix compares every pair, so its cost grows with the square of the number of chains.

## See also

[interface.md](interface.md) · [families.md](families.md) · [modes.md](modes.md) · [target_frame.md](target_frame.md) · [architecture.md](architecture.md)
