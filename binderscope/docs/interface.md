# Interface analysis and sequence liabilities

`binder_review.interface` recomputes the interface of a designed binder from its structure file, and `binder_review.liabilities` flags sequence and structure liabilities. Neither needs PyRosetta. This page is for developers and advanced users: it gives the API, the data each design gets, how every quantity is defined, how the numbers relate to BindCraft's own columns, and the known approximations.

Both modules are screens for triage, not verdicts. They return numbers and flags, and the pages decide what passes.

```python
from binder_review import interface as I, liabilities as L

cx = I.load_for_analysis(design.structure_path)               # hydrogens kept
a = I.analyze_design(cx, design, run.settings.hotspots)       # a DesignAnalysis, construct numbers
res = I.analyze_run(run.accepted() + run.rejected_with_structure(),
                    hotspots={run.run_id: run.settings.hotspots},
                    cache_dir="work/my-target/analysis")      # {design id: DesignAnalysis}
rows = I.validation_table(res.values())                       # agreement with BindCraft's columns
```

## Conventions

- **Numbering.** Target residue numbers in a `DesignAnalysis` are construct numbers, keyed by number and never by position, because a run can delete stretches of the construct. The exporter converts them with `TargetConstruct.to_uniprot`. Binder residues are 1-based indexes into the sequence, so `bc` is 1-based here, while the campaign blob's `design.bc` is 0-based and the exporter subtracts one. If a file's binder numbers are not 1 to n, the module indexes by residue order and says so in `meta.notes`. The import pipeline gives the structures of a zero-based BindCraft 2 campaign one-based copies before the analysis ([export.md](export.md), section 2), so it does not meet that case there.
- **Frame.** Geometry is analysed in the design's own frame; nothing here depends on it. `center` and the other coordinates stay in that frame, and the target frame places a design later ([target_frame.md](target_frame.md)). Contacts, hydrogen bonds, salt bridges, buried polars, areas, patches and liabilities are therefore the same for every `--placement` option.
- **Contact.** A heavy-atom pair within 4.5 angstrom (`model.CONTACT_CUTOFF`). BindCraft's own interface rule (4.0 angstrom, hydrogens counted, `model.BINDCRAFT_INTERFACE_CUTOFF`) only fills `bindcraft_bc` and the comparison block.
- **Not computed.** Anything that needs ipSAE, a PAE matrix, a second predictor or Rosetta energies. `dG`, `ShapeComplementarity`, `PackStat`, `i_pAE` and `Hotspot_RMSD` stay with BindCraft's copied values: there is no recomputed shape complementarity and no interface energy. `Hotspot_RMSD` cannot be recomputed from a relaxed complex, because it needs the trajectory target and the file holds only the predicted complex.

## API

**`load_for_analysis(path, target_chain="A", binder_chain="B") -> structure.Complex`** loads with `strip_h=False`: BindCraft 1 relaxed PDBs carry explicit hydrogens, and the hydrogen-bond rules use them.

**`analyze_design(cx, design=None, hotspots_construct=(), *, n_points="accurate", buns_min_burial=0.5, buns_max_sasa=0.1, run_id=None, design_id=None) -> DesignAnalysis`**

- `cx` is a loaded complex. One loaded with `strip_h=True` is reloaded from `cx.path` when the file has hydrogens.
- `design` is the `DesignRecord` and is optional. With it the result carries `id`, `run_id` and the `vs_bindcraft` comparison.
- `hotspots_construct` are the hotspots of the design's own run, as construct numbers. Hotspots missing from the design's target are listed in `meta.hotspots_missing` and never count as touched.
- `n_points` is the number of SASA sphere points. 960 (`"accurate"`) is the default because the buried-unsatisfied test needs "no accessible surface" to be sharp: with 960 points one sampling point of a polar heavy atom is worth about 0.1 square angstrom, the same size as the 0.1 square angstrom accessibility limit of the test, while a point of the 200-point preset is worth about 0.5. A lower value is faster but too coarse for that test.

**`analyze_run(designs, loader=None, n_jobs=None, cache_dir=None, hotspots=None, progress=None) -> dict[str, DesignAnalysis]`**

- Designs without a structure are skipped. Keys are design ids; if two designs of different runs share an id, every key becomes `"<run>/<id>"`.
- `hotspots` is `{run_id: [numbers]}`, one list for all designs, or a callable `design -> [numbers]`.
- `n_jobs` sets a spawn pool (default: CPU count minus one; 1 runs in process). A custom `loader` forces serial work. A script that uses the pool needs the usual `if __name__ == "__main__":` guard.
- `cache_dir` holds `<cache_dir>/<run>/<id>.json`, each with `{key, analysis}`. The key is the SHA-256 of the structure file together with the hotspots, the design's BindCraft columns, the burial and sampling constants, `ANALYSIS_VERSION` and a hash of the source of `interface.py`, `liabilities.py`, `disulfides.py`, `structure.py` and `model.py`, so neither a changed file nor a changed line of code can serve a stale result. Writes are atomic (temporary file, then rename).

**`validation_table(analyses, stems=None, reference="bindcraft")`**, `compare_with_bindcraft(...)`, `composition(letters)` and `code_fingerprint()` complete the module. `validation_table` gives per BindCraft column `n`, Pearson `r`, Spearman `rho`, `mean_diff`, `mad`, `mean_bindcraft` and `within`, the share of designs inside a per-stem tolerance (`VALIDATION_TOLERANCES`). `reference` is `"bindcraft"` (the column of the model whose pose is in the file) or `"average"` (the `Average_` column). The tolerances are exact for the interface residue count, 0.05 for the interface hydrophobicity percent, 1 for hydrogen-bond and unsatisfied-atom counts, 5 percent (relative) for dSASA, 5 percentage points for the other percent columns and 0.02 for surface hydrophobicity.

## `DesignAnalysis`

JSON-serialisable (`to_dict()` and `from_dict()`; integer keys are restored; NaN becomes null). Names follow the blob names where the contract has one.

| Field | Meaning |
|---|---|
| `id`, `run_id`, `n`, `n_target` | design id, run id, binder length, target residues present |
| `tc`, `bc`, `nc` | target residues (construct numbers) and binder residues (1-based) in contact at 4.5 angstrom; atom pairs within 4.5 angstrom |
| `close_tc`, `close_bc` | the same at 3.5 angstrom |
| `bindcraft_bc`, `bindcraft_bc_heavy` | BindCraft's interface rule (4.0 angstrom, hydrogens counted; heavy atoms only), binder residues, for comparison |
| `contacts` | `[{t, b, d, n}]` residue pairs at 4.5 angstrom: closest heavy-atom distance and atom pairs |
| `contact_map` | `{t, b, d}`: the dense matrix for the contact diagram. Rows are `bc`, columns are `tc`, entries the closest distance (0.1 angstrom) or `None` |
| `hbonds` | `[{t, tn, ta, b, bn, ba, d, k}]` target-binder hydrogen bonds; `k` is `D>A` when the target donates and `A<D` when it accepts; one entry per atom pair |
| `salt` | `[{t, tn, b, bn, d}]` one per residue pair |
| `buns` | `[{side, resi, resn, atom}]` buried unsatisfied polar atoms; `side` is `t` (construct number) or `b` (1-based) |
| `n_hbonds`, `n_salt`, `n_unsat`, `n_unsat_binder`, `n_unsat_target`, `unsat_density` | counts; the density is unsatisfied atoms per 1000 square angstrom of buried area |
| `hbond_basis` | `explicit` (hydrogens on both chains), `partial` (one chain) or `computed` (none: the backbone amide H is placed and side chains use heavy-atom geometry) |
| `dsasa`, `dsasa_binder`, `dsasa_target_total`, `binder_sasa_alone` | buried area (both sides, Rosetta's convention), the binder side, the target side, binder SASA alone |
| `dsasa_res`, `t_dsasa` | buried area per binder residue (list) and per target residue (`{construct number: area}`) |
| `hyd_frac` | share of the buried area that is carbon or sulfur |
| `patches`, `hphob_patch` | at most 3 exposed hydrophobic patches `[{area, res}]` on the binder, largest first; the area of the largest |
| `comp` | interface composition for `binder` and `target`: counts and shares of hydrophobic (A C F I L M P V W Y), aromatic (F W Y), positive (K R), negative (D E), charged and polar (S T N Q H) residues, plus the 20-letter count `aa` |
| `ss`, `helix_frac`, `strand_frac`, `loop_frac`, `iface_helix`, `iface_strand` | secondary structure of the binder (H, E, L per residue); shares over all binder residues and over `bc`; `loop_frac` is the share of interface residues in loops |
| `segments`, `loops_near` | secondary-structure runs `{ss, start, end, n_iface, min_d}`; loops with a heavy atom within 6 angstrom of the target |
| `nterm_d`, `cterm_d`, `termini` | terminal CA to the nearest target heavy atom; `termini` also says whether the terminal residue is in `bc`. `termini.assess` ([termini.md](termini.md)) reads the same distance as `d` and adds what a tag needs: direction, room, burial, secondary structure and the side of the binder |
| `center` | the mean midpoint of the atom pairs within 4.5 angstrom (design frame) |
| `rsa_res`, `plddt_res`, `plddt_t` | binder relative SASA in the complex, binder pLDDT (the B-factor, 0 to 100), target pLDDT by construct number |
| `tracks` | per-residue tracks for the sequence view: `plddt`, `rsa`, `dsasa`, `iface`, `n_target_res`, `n_atom_pairs`, `dist` (closest distance to the target), `kd` (Kyte-Doolittle), `ss`, `liab` (`[{pos, sev}]`, the worst severity per position) |
| `ssbonds` | `[[i, j]]` the binder's disulfides: the file's own record, or when it records none, pairs read from SG-SG geometry (see Disulfides below) |
| `ssbond_basis`, `disulfides` | `"recorded"`, `"inferred"` or `"none"`, and `[{a, b, d, band}]` with the SG-SG distance and the band of each pair |
| `hotspots`, `hotspot_dist`, `hot` | the run's hotspots present in the target, the closest heavy-atom distance from each to the binder, and the touched ones (`tc` intersected with the hotspots) |
| `seq_props`, `liab`, `global_flags`, `interface_aas` | pI, net charge at pH 7.4, GRAVY and counts of C, M, W, K; liabilities; whole-sequence flags; amino-acid counts of `bindcraft_bc` |
| `vs_bindcraft` | recomputed against BindCraft per stem: `recomputed`, `bindcraft`, `average`, `model`, `diff`, `same_definition`, `note`; `InterfaceResidues` adds `jaccard` and `exact` |
| `meta` | `version`, `hydrogens`, `structure_model`, `buns` (the two thresholds), `n_points`, `notes`, `hotspots_missing` |

The exporter takes `dsasa`, `n_hbonds`, `n_salt`, `n_unsat`, `hyd_frac`, `hphob_patch`, `loop_frac`, `helix_frac`, `strand_frac`, `iface_helix`, `nterm_d` and `cterm_d` of `design.m` from here, and `pI`, `charge`, `gravy`, `n_cys`, `n_met` and `n_trp` from `seq_props`. `epi_prec` and `epi_rec` are set arithmetic on `tc` and the run's epitope. BindCraft's own values stay in `design.src` ([data_contract.md](data_contract.md)).

## How each quantity is defined

- **Contacts.** `structure.residue_contacts` on the heavy-atom tables, with a cut-off of 4.5 angstrom (3.5 angstrom for `close_*`). Hydrogens never count.
- **Hydrogen bonds.** `structure.hbonds(target, binder)`: donor-acceptor distance up to 3.5 angstrom (`model.HBOND_MAX_DIST`), and, with a hydrogen in the file, H to acceptor up to 2.5 angstrom and a D-H-A angle of at least 120 degrees. Without hydrogens the backbone amide H is placed from geometry, and side-chain donors use the acceptor angle (see [structure_api.md](structure_api.md)). A Ser, Thr or Tyr pair that passes in both directions counts once. There is no energy term.
- **Salt bridges.** Opposite-charge side-chain groups across the interface whose centroids are within 4.0 angstrom (`model.SALT_BRIDGE_MAX_DIST`): Arg guanidinium, Lys NZ, Asp and Glu carboxylate. One per residue pair; His is not included.
- **Buried unsatisfied polar atoms.** A donor or acceptor heavy atom counts when all three hold: binding buries it (its SASA drops by at least 0.5 square angstrom), it has no accessible surface left in the complex (SASA of at most 0.1 square angstrom with a 1.4 angstrom probe and 960 points), and it takes part in no hydrogen bond anywhere in the complex (`structure.hbonds` on target and binder together, so intra-chain bonds satisfy it too). Candidate atoms are backbone N (not Pro), backbone O and OXT, and the side-chain donors and acceptors listed for `structure.hbonds` ([structure_api.md](structure_api.md)); Cys SG and Met SD are excluded. Both sides count, as in Rosetta's filter. Rosetta's BuriedUnsatHbonds is an energy-based count with a 1.1 angstrom probe and a difference between the complex and the separated chains. The two are not the same measure.
- **dSASA.** Binder SASA alone minus binder SASA in the complex, plus the same for the target: Shrake-Rupley with the Bondi-type radii of Biopython's table (`structure.interface_dsasa`), heavy atoms only.
- **`hyd_frac`.** The burial of carbon and sulfur atoms, on both sides, over the total buried area.
- **Hydrophobic patches.** Binder side-chain carbon and sulfur atoms of A, V, L, I, M, F, W, Y and C with more than 1.0 square angstrom exposed in the complex and less than 0.5 square angstrom buried by binding are joined when closer than 5.0 angstrom. The area of a patch is its exposed area. The largest three are kept, largest first.
- **Secondary structure.** `structure.assign_dssp` on target and binder together, with the binder part taken and reduced to H (H, G, I), E and L without bulge filling, because that is how BindCraft counts. Interface shares use `bc`; the BindCraft comparison uses `bindcraft_bc_heavy`.
- **Hotspots touched.** `hotspot_dist[h]` is the closest heavy-atom distance between any atom of residue h and the binder; `hot` lists the hotspots in `tc`, which is the same as being within 4.5 angstrom. A difference between the two (a distance on the boundary) is written to `meta.notes`.
- **Composition.** The classes in the table above, counted on the 4.5 angstrom interface for `comp` and on BindCraft's 4.0 angstrom set for `interface_aas`.

## Relation to BindCraft's columns

The recomputed numbers are not BindCraft's numbers. They come from the structure file with simpler, geometry-based definitions, so they track BindCraft's columns but may differ from them. `vs_bindcraft` sets each recomputed value beside the BindCraft value of the AlphaFold model whose pose is in the file (`DesignRecord.pose_metric`) and stores the average column as well. `same_definition` is true only where the rule is the same:

| BindCraft column | Recomputed as | Same definition |
|---|---|---|
| `n_InterfaceResidues` | binder residues within 4.0 angstrom of any target atom, hydrogens counted | yes |
| `Interface_Hydrophobicity` | percent of those residues in A C F I L M P V W Y | yes |
| `n_InterfaceHbonds` | target-binder hydrogen bonds from the file's hydrogens (geometry, no energy term) | no |
| `n_InterfaceUnsatHbonds` | buried polar atoms without a hydrogen bond in the complex, both sides | no |
| `dSASA` | buried area, Shrake-Rupley with Bondi radii, both sides | no |
| `Interface_SASA_%` | buried area of both sides over the binder's SASA in the complex (a reading identified by comparison with the CSV; BindCraft does not document it) | no |
| `Interface_Helix%`, `Interface_BetaSheet%`, `Interface_Loop%` | shares of the 4.0 angstrom heavy-atom interface residues in DSSP H, G, I, in E, and in other states | no |
| `Binder_Helix%`, `Binder_BetaSheet%`, `Binder_Loop%` | shares of binder residues in DSSP H, G, I, in E, and in other states | no |
| `Surface_Hydrophobicity` | an approximation of Rosetta's surface-layer fraction on the binder alone | no |
| `InterfaceHbondsPercentage`, `InterfaceUnsatHbondsPercentage` | the two counts per 4.0 angstrom interface residue, in percent | no |
| `InterfaceResidues` | the residue set itself, with `jaccard` and `exact`. BindCraft writes the column from one fixed model whatever file is saved, so the comparison is exact only when the saved file is that model | yes |

Five BindCraft columns cannot be recomputed and are copied: `Hotspot_RMSD`, `dG`, `ShapeComplementarity`, `PackStat` and `i_pAE`. They appear in `vs_bindcraft` with `recomputed` null.

The exporter compares every recomputed quantity with BindCraft's column for the accepted designs and writes the agreement per metric into `manifest.validation.metric_agreement`: `n`, `mean_abs_diff`, `max_abs_diff`, `mean_diff`, `r`, `rho` and `same_definition`. How close the two are depends on the data, so read that block for your own run rather than assuming agreement.

## Sequence liabilities

`liabilities.find_liabilities(seq, contact_residues=None, rsa=None, ssbonds=None, patches=None, include_global=False, disulfides=None) -> list[dict]`, `sequence_properties(seq)`, `global_flags(seq)`, `to_campaign(liabs)`, `to_inspector(liabs)` and `severity_counts(liabs)`.

- `contact_residues` are the 1-based interface residues; they enable the interface Lys and Met notes.
- `rsa` is the relative SASA per residue (0 to 1); it enables the exposure rules.
- `ssbonds` are disulfide pairs; they decide which cysteines are unpaired.
- `disulfides` is the result of `disulfides.assess` (`{basis, pairs, nearest}`) and is preferred to `ssbonds`: the entries it produces carry a `basis` (`recorded` or `inferred`) and say how the pairing was found. With basis `none` (no record and no sulfur coordinates) the cysteine check is off, and a Cys with no SG atom in an inferred structure is not judged.
- `patches` are the exposed hydrophobic patches `[{area, res}]` of the interface analysis.

Each entry has `kind`, `pos` (1-based, the first residue of the motif), `motif`, `sev` (`bad`, `warn` or `info`), `reason` (one line) and `scope` (`residue` or `global`). The list is sorted by position, then kind. `to_campaign` gives the campaign blob's short form `{k, p, m, s}`; `to_inspector` gives `{kind, pos, motif, sev, reason}`. The pages match substrings of the kind strings (for example "Unpaired", "N-glycosylation", "NG", "DP", "Hydrophobic"), so the wording is part of the contract. Non-standard residues take part in no motif, positions keep counting through them, and nothing raises on odd input.

| Kind | Rule | Severity |
|---|---|---|
| N-glycosylation sequon | Asn-X-Ser/Thr with X not Pro | warn |
| Deamidation (NG) / (NS) | Asn followed by Gly / Ser | warn / info |
| Isomerisation/cleavage (DG) / (DS) / (DP) | Asp followed by Gly / Ser / Pro | warn / info / info |
| Unpaired cysteine | with `disulfides` or `ssbonds`: any Cys outside a pair; without either: the last Cys when the count is odd. With `disulfides` the reason names the basis and, when inferred, the distance to the nearest other sulfur | bad |
| Disulfide geometry loose | `disulfides` only: a pair inferred at 3.0 to 3.4 angstrom, kept as a disulfide but looser than a closed bond | info |
| Met oxidation, Trp oxidation | relative SASA of 0.25 or more (not flagged when buried); without `rsa` they are reported as info with "exposure unknown" | warn for Met, info for Trp |
| Hydrophobic stretch | six or more consecutive V, I, L, M, F, W or Y | warn if the mean exposure is 0.25 or more or unknown, info if buried |
| Exposed hydrophobic patch | from `patches`, at the first residue of the patch | warn from 500 square angstrom, info from 300 (smaller patches are not reported) |
| Protease site (furin) | Arg-X-Lys/Arg-Arg, at the first Arg | warn if that Arg is exposed or its exposure is unknown, else info |
| Protease site (dibasic) | KK, KR, RK or RR with both residues at relative SASA of 0.40 or more (needs `rsa`) | info |
| Interface Lys, Interface Met | the residue is in `contact_residues` | info |
| Unusual charge | `global_flags`: net charge at pH 7.4 of at least 8 in magnitude (position 1, scope `global`) | info, warn from 12 |

`include_global=False` keeps the whole-sequence "Unusual charge" flag out of the per-residue list, because it has no residue position; `global_flags(seq)` returns it, and `DesignAnalysis.global_flags` stores it. `sequence_properties` returns `length`, `pI`, `charge` (Henderson-Hasselbalch at pH 7.4 with the pKa values of `structure.PKA`), `gravy` (Kyte-Doolittle) and the counts `n_cys`, `n_met`, `n_trp`, `n_lys`; only the 20 standard letters enter pI, charge and GRAVY.

**Disulfides** (`disulfides.py`). BindCraft 2 writes no `_struct_conn` records and Binderscope's placed PDB files carry no SSBOND, so the file is silent about every bond, and a predictor does not close the bond to its exact length. `DesignAnalysis` therefore takes the disulfides the file records for the binder chain (`Complex.recorded_disulfides`, from mmCIF `_struct_conn` or PDB SSBOND) and keeps them untouched, even where the geometry disagrees (a note is added when a recorded pair's sulfurs are more than 3.4 angstrom apart). Only when the file records none are pairs read from the SG-SG distances. A pair is kept up to 3.4 angstrom: "close" up to 3.0 angstrom, "loose" from 3.0 to 3.4, where the model is least sure. The module's docstring gives the reasoning: a closed bond is about 2.03 angstrom, non-interacting sulfurs sit at the 3.6 angstrom van der Waals contact or beyond, and in the models of a real scaffolded VHH campaign the framework pairs spread across the whole range from a closed bond to a loose one, so the earlier limit of 2.6 angstrom discarded many pairs that are bonds. Each sulfur is in at most one pair; among rival pairings the one with the largest summed margin inside 3.4 angstrom wins, so a tight bond beats two loose ones that need the same sulfurs. `ssbond_basis` says which of the two the pairs rest on, an inferred pair being a weaker claim than a recorded one, and it reaches the inspector blob (`ssbond_basis`, `disulfides`), the liability entries (`basis`; `b` in the campaign blob) and the pages. A Cys with no partner within 3.4 angstrom is still an `Unpaired cysteine`, severity bad.

The thresholds are judgement calls, kept as constants at the top of `liabilities.py` (`EXPOSED_RSA`, `DIBASIC_RSA`, `HYDROPHOBIC_STRETCH`, `PATCH_WARN_AREA`, `PATCH_INFO_AREA`, `CHARGE_INFO`, `CHARGE_WARN`, `PH`); the two disulfide cut-offs, `CLOSE_MAX` and `LOOSE_MAX`, are in `disulfides.py`.

Two behaviours to know. A hydrophobic run longer than the minimum is reported once for every start position that still leaves six or more residues, so a run of eight gives three overlapping entries, the motif being the rest of the run from that position. An Asn-Gly-Ser stretch is reported both as an NG deamidation site and as a sequon.

## Known approximations

Any page or report that shows these numbers should say so.

- `Surface_Hydrophobicity` as recomputed here (`structure.surface_hydrophobicity`) is an approximation of Rosetta's layer-based value and is not the same code. The pages show BindCraft's own value; the approximation only appears in the comparison.
- dSASA uses Bondi-type radii and a 1.4 angstrom probe, not Rosetta's radii, so it differs from BindCraft's `dSASA`.
- Hydrogen-bond counts are geometric. BindCraft's are energy-based. They are related but not equal.
- The unsatisfied polar count is a different measure from Rosetta's, as described above.
- Secondary-structure shares depend on `structure.assign_dssp`, a re-implementation of DSSP, so they agree with BindCraft's only approximately.
- `Interface_SASA_%` is reproduced only as a reading identified by comparison with the CSV.
- Hydrogen-bond and buried-unsatisfied results depend on whether the file has explicit hydrogens (`hbond_basis`).

## See also

[structure_api.md](structure_api.md) · [data_contract.md](data_contract.md) · [viewer_inspector.md](viewer_inspector.md) · [target_frame.md](target_frame.md) · [architecture.md](architecture.md)
