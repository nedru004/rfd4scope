# BindCraft 2 reader

This page describes the reader for BindCraft 2 campaign folders (`src/binder_review/readers/bindcraft2.py`) and the registry that finds the right reader for a folder (`src/binder_review/readers/__init__.py`). It is for developers and advanced users who point Binderscope at BindCraft 2 output.

**Status: verified for one case, untested elsewhere.** The reader was first written from BindCraft 2's documentation and checked only on synthetic fixtures. It has since been checked, number by number, against a real campaign: de novo design (the `binder` modality) written by BindCraft 2 v1.0.3. The counts and values measured on it are not reproduced on this page: they come from unpublished work, and what is kept here is the property each check established. A campaign of that version and modality, whose tables and metadata have the layout described below, is read without a warning and `RunData.experimental` is false. Every other campaign (another version, a VHH, scFv, Fab or cyclic-peptide campaign, several targets, a table or metadata file that lacks what the reader relies on) is still read on the same assumptions, keeps `experimental = True` and gets one warning that names the reason (see [How the pages mark such a run](#how-the-pages-mark-such-a-run)). For those, compare a few numbers with the campaign's own CSV files before trusting a result, and report what differs.

## How a folder gets its reader

`readers/__init__.py` keeps an ordered registry of reader modules (`bindcraft1`, then `bindcraft2`).

| Function | What it does |
|---|---|
| `available_readers()` | imports every registered module and returns `{name: module}`; a module that fails to import is skipped, so a broken optional reader never blocks the others |
| `detect_reader(path)` | returns the first registered reader whose `detect(path)` is true, or `None` |
| `get_reader(name)` | returns one reader by name, or raises `KeyError` with the available names |
| `register_reader(name, module)` | adds a reader at run time (after the built-in ones) or replaces one of the same name |

A reader module provides `NAME`, `detect(path) -> bool` and `read_run(path, run_id, label=None, settings_path=None, **options) -> RunData`. Every run folder is detected on its own, by the layout of its files and not by a flag, so `import` and `inspect` (see [cli.md](cli.md)) need no option to name the tool. A folder that no reader recognises is an error.

The BindCraft 1 reader detects a folder that holds its three statistics CSV files (`final_design_stats.csv`, `mpnn_design_stats.csv`, `trajectory_stats.csv`). The BindCraft 2 reader detects:

- two of the three stage folders `1_Trajectories`, `2_Refolded` and `3_Ranked`, or
- one stage folder together with a stage table (`!_*.csv`) or a `campaign_metadata*.json` file.

Stage folder and table names are matched without regard to case. A folder that holds the three BindCraft 1 statistics files is never taken for BindCraft 2, and the BindCraft 1 reader does not detect a BindCraft 2 folder, so the two cannot claim the same folder.

## The layout of a campaign (measured)

```
<campaign>/
  campaign.json                   what was asked for         campaign_metadata.json   what BindCraft resolved
  campaign.log   summary.csv   .campaign_state.json   .redesigned_sequences.txt
  1_Trajectories/!_Trajectories.csv        one row per trajectory started
  1_Trajectories/<design>/<design>_losses.csv     only this, unless archive_trajectories is true
  2_Refolded/!_Refolded.csv                one row per refolded candidate
  2_Refolded/Complexes/<design>_candidate<N>.cif            bound prediction: chain A target, the binder in the chains after it
                                                            (one for most campaigns; an scFv's VH and VL and a homo-oligomer's
                                                            copies are separate chains, and Binder_Sequence separates them with "/")
  2_Refolded/BinderMonomer/<design>_candidate<N>_monomer.cif    the binder predicted alone: chain A, in the frame of the bound binder
  3_Ranked/!_Ranked.csv, 3_Ranked/<design>_seq0.cif         the final shortlist
```

The three tables are three tiers, not two. On a real campaign the trajectory table lists every trajectory that was started, and only some of them finished every stage and were refolded; the refolded table holds one or more candidates for each of those backbones; a smaller set of the candidates has `outcome == passed`; and `!_Ranked.csv` holds a still smaller one. BindCraft's own counters (`summary.csv`, `.campaign_state.json`) call the ranked designs "accepted", because the campaign keeps one sequence per backbone (`kept_sequences`); Binderscope's accepted set is every design that passed.

**`hash` is the key.** It is the 16-hex trajectory hash and it is in all three tables and at the end of every design id (`<campaign>_<modality>_l<length>_<hash>`, then `_candidate<N>` in the refolded table and `_seq0` in the ranked one). `!_Refolded.csv` and `!_Ranked.csv` have **no `trajectory` column**: the hash is the only link from a design to its trajectory. A ranked design and its refolded candidate have different ids and are the same molecule.

## What it reads

| Item | How |
|---|---|
| Tables | `1_Trajectories/!_Trajectories.csv`, `2_Refolded/!_Refolded.csv` and `3_Ranked/!_Ranked.csv`. If the expected name is missing, the first `!_*.csv` of the stage folder is used. Every cell is read as text and column names are matched without regard to case |
| Structures | mmCIF through gemmi. A design's structure is `2_Refolded/Complexes/<id>.cif`, matched on the exact file stem (or behind a rank prefix such as `1_`); never on a substring, because `..._candidate1` is a prefix of `..._candidate10`. Chains are lettered by position with the targets first: the last *n* chains are the binder, where *n* is the number of `/`-separated parts of `Binder_Sequence`. The ranked file `3_Ranked/<id>_seq0.cif` is the same prediction (atom for atom, as checked on a real campaign); it is kept as `ranked_path` and stands in for the complex only when there is none. A trajectory structure is another sequence and is never used |
| Unbound binder | `2_Refolded/BinderMonomer/<id>_monomer.cif` is the prediction of the binder alone (chain A, superposed on the bound binder), which is the only record of the free state. The path goes to `design.extras["unbound_binder_path"]`. `DesignRecord` has no field for it, so there is no proper field; the file is not opened or hashed |
| Ranked structures | carry a `_bindcraft` block with the design's identity, its settings and every metric, also per validation model. It is kept verbatim in `design.extras["cif_bindcraft"]` and nothing is interpreted |
| Metadata | `campaign_metadata*.json` of the folder, or the file given as `settings_path`; see below |
| Also hashed | `summary.csv` and `.campaign_state.json`. `.campaign_state.json` (`attempted`: the trajectory hashes) is compared with the trajectory table and the result is a note |

The reader opens every structure once (`check_structures`, on by default) to find the chains, the target residue numbers and sequence, the first binder residue number and hydrogens. `hash_structures` adds the structure files to `RunData.files` with their sha256. Unknown keyword options are ignored.

### The metadata

`campaign_metadata.json` is the truth about what ran; `campaign.json` is the request. Keys the reader uses (each is optional and found by name, depth first):

| Key | Used for |
|---|---|
| `version` (`"BindCraft 2 v1.0.3"`), `revision` | `RunData.tool_version` (`2 v1.0.3 (revision ...)`). The stamp already carries the tool's name; only the number is kept. Without it the `bindcraft_version` column of the tables is used |
| `settings.campaign_name`, `settings.modality`, `metadata.settings_modality` | the campaign name; the modality (`binder` is de novo) |
| `settings.targets[].hotspots` (`"10-12,30,31"`), `.chains`, `.target_path` | the hotspots (construct numbers, ranges allowed), the target chains, and the construct file |
| `settings.binder_lengths`, `settings.number_of_final_designs` | the length range and the number of final designs |
| `settings.filters` | the campaign's own filters, below |
| `settings.min_plddt_final` | the threshold of the `pLDDT` filter, below |
| `settings.archive_trajectories` | whether trajectory structures should exist (a note only) |
| `settings.filters.Scaffold_Sequence_Retained_Fraction.params` (`scaffold`, `scaffold_edits`) | a campaign built on a fixed scaffold: kept, with the filter's `threshold`, `higher` and `mandatory`, as `annotations["scaffold"]` (nothing parsed here; [paratope.md](paratope.md) reads it). The filter is still read as a rule |

`settings.targets[0].target_path` is the construct file BindCraft was given; it is not copied into the campaign. When `read_run` gets no `construct_path` and that absolute path exists on this machine, the reader uses it (a note says so): deleted regions are then reported against the real construct and its sequence is compared with the structures. Where the path does not exist a note says so, and `construct_path` still works.

**The campaign's filters.** `settings.filters` is `{name: {higher, threshold, mandatory}}`. `higher` says which side passes (`>=` when true, `<=` when false; the threshold itself passes). `"Infinity"` arrives as a string and means no limit: such a filter is dropped (its name goes to `annotations["filters_unlimited"]`) and is not read as a threshold. `mandatory` is BindCraft's flag (a mandatory filter fails a design whose metric could not be measured); a missing key means true, which is BindCraft's own default (`bindcraft/filters.py`) and how the file is written: only the non-binding filters carry `"mandatory": false`. The result is `annotations["filters"]`, a list of `{name, op, threshold, higher, mandatory, source}`. On the real campaign many of the filters listed have an infinite threshold; of the rest, some are mandatory (`i_pTM >= 0.7`, `pTM >= 0.55`, `i_pAE <= 0.35`, `Unbound_Binder_pLDDT >= 0.7`, `Binder_RMSD <= 3.5`, `Interface_Residues >= 7`, `Backbone_Clashes <= 0`) and the others cannot fail (`>= 0` on a value that is never negative, and the like). The `pLDDT` filter below is added to the mandatory ones.

**The scaffold spec.** `params` of a filter entry are not part of its rule, and the reader used to drop them. For `Scaffold_Sequence_Retained_Fraction` they hold which scaffold positions BindCraft was allowed to change, so `annotations["scaffold"]` = `{scaffold, scaffold_edits, threshold, higher, mandatory, source}` is written whenever that filter carries a `scaffold_edits` string. A campaign without it has no such annotation and is not scaffolded. Region boundaries, naming and the alignment of designs are the job of `paratope.py`.

`pLDDT` is also judged, but it is not an entry of `settings.filters`: BindCraft takes its threshold from the setting `min_plddt_final` (`bindcraft/filters.py`, `design_stage_filters`). The reader adds it as a filter with `source: "settings.min_plddt_final"`. The 0.7 it reads agrees with the rows: no row has a CSV pLDDT below 0.7 without naming `pLDDT` in `failed_filters`.

## Mapping to the shared model

- **Accepted designs.** A refolded row whose `outcome` is `passed` (and that names no failed filter). Ranking is a rank on those designs and not the definition of acceptance: the ranked row is matched to its refolded row on `(hash, Binder_Sequence)` and gives that design `rank`, `ranked_path` and `extras["ranked_id"]`. No second design is made. Every refolded row was matched on the real campaign, and each ranked row matched exactly one. A ranked row that matches no refolded row is read from its own row and a warning says so; a matched design whose outcome is not `passed` is not accepted and a warning says so. Other `outcome` values are read by their wording and noted. With no `outcome` column only the ranked designs count as accepted, and a warning says so.
- **Families.** One `BackboneRecord` per row of `!_Trajectories.csv`, finished or not. A design belongs to the backbone with its `hash`. The backbone id is the trajectory's design name; a design whose hash has no trajectory row gets that name from its own id (`_candidate<N>` and `_seq<N>` removed) and a warning lists it. A table with no hash column falls back to a `trajectory` column, and a design with neither is its own family, which is reported as a warning ("neither a hash nor a trajectory value") so that it cannot go unnoticed again. `BackboneRecord.order` is the rank of the `trajectory` number when every row has a distinct one (rows are written as trajectories finish, so file order is not generation order), else the row position.
- **Design ids.** The `design` column; `seed` is 0 and `mpnn_index` is `None`, because BindCraft 2 has neither. `parse_name` splits `<campaign>_<modality>_l<length>_<hash>[_candidate<N> | _seq<N>]`. The hash and the candidate number go to `extras`.
- **Failed filters.** `failed_filters` is comma separated (`i_pTM,pLDDT,Unbound_Binder_pLDDT`; empty for a passed design). The names go to `extras["failed_filter_names"]` and `RunData.failure_counters` counts the not-accepted designs per name. On the real campaign the count for every name equals the one in the campaign's own file, and `inspect` agrees. A name is never renamed: `Unbound_Binder_pLDDT` and `pLDDT` are two filters (and BindCraft 1's `Binder_pLDDT` is a third name for a metric, not a filter of this campaign). A `FilterFailure` is made for every name whose threshold is known (above) and whose value the row carries; its stem is the filter's name.

  *The verdict is BindCraft's, not the CSV's.* The confidence filters (`pLDDT`, `pTM`, `i_pTM`, `Unbound_Binder_pLDDT`, `Target_pLDDT`) are judged on each validation model in turn, and the table holds the mean over the models that ran, rounded to two decimals. A candidate can therefore fail a filter on one model with a CSV value that passes it: on the real campaign some candidates fail `i_pTM`, `pLDDT` or `Unbound_Binder_pLDDT` with a CSV value at or above the threshold, and every candidate that fails `pTM` has a CSV value above its threshold. Counting from the CSV values (what the first version did, through `FilterFailure`) gives far fewer failures for the first two and none at all for `pTM`, which is why those counts disagreed with the file. A failure the row's value does show has scope `Average` and its gap; one it does not show has scope `Model` and gap 0.0, which is a lower bound, not a measured gap, and its name is in `extras["failed_filter_not_in_mean"]`. Because BindCraft stops at the first validation model that fails, `failed_filters` also lists only what it evaluated: candidates whose `Binder_RMSD` is above 3.5 can fail to name it, because they failed a confidence filter first. The reader does not add failures that BindCraft did not report.
- **Metrics** keep the BindCraft 1 stem and scale where the quantity is the same. `DesignRecord.metrics` and `BackboneRecord.trajectory_metrics` hold:

  | BindCraft 2 column | stored as | factor | note |
  |---|---|---|---|
  | `i_pTM`, `pLDDT`, `pTM`, `i_pAE` | the same stem | 1 | `i_pAE` is the inter-chain PAE divided by 31, as in BindCraft 1 |
  | `Unbound_Binder_pLDDT` | `Unbound_Binder_pLDDT` and `Binder_pLDDT` | 1 | BindCraft 1's `Binder_pLDDT` is "binder predicted alone". Only the value is shared; the filter keeps its own name |
  | `SS_pLDDT` | `ss_pLDDT` | 1 | binder pLDDT over helix and strand residues |
  | `Interface_Residues` | `n_InterfaceResidues` | 1 | binder residues within 4 Å of the target; equals the length of `Interface_Binder_Residues` in every row |
  | `Surface_Hydrophobicity` | `Surface_Hydrophobicity` | 1 | fraction of exposed binder residues that are hydrophobic. The definition of "exposed" (BindCraft 2: relative SASA of the free binder >= 0.2) is BindCraft 2's, see the known gaps |
  | `Interface_Hydrophobicity` | `Interface_Hydrophobicity` | 100 | BindCraft 2 writes a fraction, the BindCraft 1 stem is a percent |
  | `Binder_Helix_Fraction`, `Binder_BetaSheet_Fraction`, `Binder_Loop_Fraction` | `Binder_Helix%`, `Binder_BetaSheet%`, `Binder_Loop%` | 100 | the share of binder residues in each class; the assigner is another one (BindCraft 2 uses biotite's P-SEA, BindCraft 1 DSSP), so values differ by a few points |
  | `Interface_Helix_Fraction`, `Interface_BetaSheet_Fraction`, `Interface_Loop_Fraction` | `Interface_Helix%`, `Interface_BetaSheet%`, `Interface_Loop%` | 100 | the same over the interface residues |
  | `Target_RMSD`, `Binder_RMSD` | the same stem | 1 | predicted target against the input target; binder alone against binder in the complex |
  | `i_pDAE`, `Target_pLDDT` | their own names | 1 | no BindCraft 1 equivalent. `i_pDAE` is a confidence (higher is better, unlike `i_pAE`), BindCraft 2's ranking value |
  | `Off_Paratope_Contact_Fraction`, `Scaffold_Framework_RMSD`, `Scaffold_Sequence_Retained_Fraction`, `Interdomain_Contact_Fraction` | their own names | 1 | antibody modalities. These occur as filter *names* in `settings.filters` on a real scaffolded campaign, but have not been seen as table *columns*: the mapping here is written from the documentation and is unverified against real output |

  A blank cell is a missing value (`None`); the trajectories that stopped early have blank metrics. If any pLDDT column of a table has a value above 1.5, the pLDDT columns of that table are divided by 100 and a note says so; this has not been seen on real output (v1.0.3 writes 0 to 1).

- **Columns that are not mapped, and why.** BindCraft 2 has a column near each of these BindCraft 1 stems, but not the same quantity, so nothing is mapped and the column stays in `extras` under its own name (`annotations["capabilities"]["unmapped_bindcraft1_stems"]` says it again):

  | BindCraft 1 stem | BindCraft 2 column | why not |
  |---|---|---|
  | `dSASA` | `Interface_BuriedArea` | BindCraft 2 documents it as the binder-side loss of area, "not the sum of both partners' buried areas"; BindCraft 1's `dSASA` is the buried area of both sides (see [interface.md](interface.md)). The two differ by about a factor of two |
  | `Interface_SASA_%` | `Interface_BuriedArea_Fraction` | BindCraft 2 divides the binder-side area by the free binder's area; BindCraft 1 divides the buried area of both sides by the binder's area in the complex ([interface.md](interface.md)). Besides, BindCraft 2's value is a fraction and the stem a percent |
  | `Unrelaxed_Clashes` | `Backbone_Clashes`, `All_Atom_Clashes` | BindCraft 2 counts interchain pairs only: CA atoms within 2.5 Å (`Backbone_Clashes`), or any atoms within 2.5 Å (`All_Atom_Clashes`). One stem cannot hold two counts, and neither is BindCraft 1's |

  The other BindCraft 2 columns without a BindCraft 1 stem (`Hotspot_Contact_Fraction`, `Off_Epitope_Contact_Fraction`, `Epitope_Residues_Contacted`, `Binder_Mass_kDa`, `Binder_pI`, `Binder_Net_Charge`, `Binder_Cysteines`, `Binder_Free_Cysteines`, `Binder_Disulfides`, `Binder_Extinction`, `Timing`, ...) stay in `extras` as numbers or text. Columns the reader has not seen at all are listed in `annotations["unknown_columns"]` and in the notes.

- **Capabilities.** The BindCraft 1 stems BindCraft 2 does not report are absent, so `RunData.caps()` is false for them: `dG`, `dG/dSASA`, `ShapeComplementarity`, `PackStat`, `Binder_Energy_Score`, `n_InterfaceHbonds`, `n_InterfaceUnsatHbonds`, `InterfaceHbondsPercentage`, `InterfaceUnsatHbondsPercentage`, `Hotspot_RMSD`, `i_pLDDT`, `Relaxed_Clashes`, `MPNN_score`, `MPNN_seq_recovery`, `pAE`, `Binder_pTM`, `Binder_pAE`. Checks that need them switch off; they never fail a design. Together with the three unmapped stems and the 17 present ones this accounts for all 37 stems. The reader reports 20 metrics (17 stems and `i_pDAE`, `Target_pLDDT`, `Unbound_Binder_pLDDT`).
- **Interface residues.** `Interface_Binder_Residues` is `"P1,A25,H26"`: the **amino acid** and the residue number in the written structure, not a chain and a number (`Interface_Target_Residues` is the same on the target, `"R10,A12,L30,W45"`). A `/` separates the chains of a multi-chain binder. The reader puts `<chain><number>` (`B25`, chain from the structure, number 1-based) in `design.interface_residues` and checks every letter against `Binder_Sequence` at that position: on the real campaign every letter of every refolded row agrees, and so does every letter of the ranked rows. If any letter of a design disagrees, or a token does not parse, the format has changed: that design's residues are not read (the cell is kept in `extras`) and one warning says how many letters and designs. The target letters are checked the same way against the target of the structure (`Interface_Target_Residues` stays in `extras` as written). The interface module recomputes the interface from the structure in any case.
- **Modality.** Per design (`extras["modality"]`) and for the run (`annotations["modality"]`, counts in `annotations["modalities"]`; also `settings.protocol`). The source, in order: the name token (de novo, VHH, scFv, Fab, cyclic peptide, peptide, ARP), then the metadata, then the columns (`Interdomain_Contact_Fraction` or two binder chains mean scFv, any other antibody column means VHH), then de novo. A campaign with several modalities is `mixed`.
- **Funnel.** The table lists every trajectory that was started; `terminated` names the stage a trajectory stopped in and is empty for one that finished. So `trajectories_attempted` is the number of rows (observed), `trajectory_rows` ("completed and scored") the rows with an empty `terminated` and `trajectories_terminated` the others; all three agree with `summary.csv`. Sequences scored are the refolded rows, accepted designs those that passed, and families with an accepted design the backbones they belong to. For a table without a `terminated` column the number of rows is only an estimate of the attempts, marked derived.
- **Target.** The first target chain (`A`) of the structures: the residue numbers present, the sequence, the deleted ranges and whether every structure carries the same residue set (`consistent`). The majority residue set wins and a warning names the structures that differ. `uniprot_offset` is left to the target frame ([target_frame.md](target_frame.md)). Structures with several target chains are noted; only the first chain is described.
- **Trajectory folders.** `1_Trajectories/<design>/` holds only `<design>_losses.csv` unless the setting `archive_trajectories` is true. A missing trajectory structure is therefore normal and is not a warning: a note counts the folders. Trajectory structures are not read in any case; a design's structure is its refolded complex.

## Numbering

The target keeps construct numbering, as for BindCraft 1: the numbers of the file that was given to BindCraft (a campaign run on a trimmed target keeps the numbers of the trimmed file, not of the full protein). Without a construct file the deleted ranges are the gaps in the target numbering of the structures; with one (given, or found through the metadata) they are the stretches of the construct that the structures lack.

Binder residues are numbered from 1 in v1.0.3, verified on every complex of the real campaign, and the residue lists of the tables use the same numbers. Earlier releases are documented to write them zero-based (1.0.0 and 1.0.1); that has never been seen on a real campaign, and the toy campaign `toya` is built that way only to keep the code path tested. The reader decides from the first binder residue number found in the structures (0 or 1). Only when no structure was checked does it fall back on the version stamp (1.0.1 or older means zero-based), and without either it assumes one-based and says so. Stamps can lag the code, so a disagreement between stamp and structures is noted and the structures win. The result is in `annotations["binder_numbering"]` (`source_zero_based`, `basis`, `offset_to_one_based`). Interface residues in the records are shifted to one-based, and the structure files are never rewritten. `bindcraft2.one_based_copy(run, design, out_path)` writes a copy of a structure as mmCIF with the binder chains shifted (target numbers untouched).

The import pipeline uses it. In its read stage, `pipeline.one_based_structures` writes such a copy of every design structure of a zero-based campaign under `work/onebased/<run>/` and points the design at the copy, so the interface analysis, the exported structures and the inspector all see binder residues numbered 1 to n. The original files are not modified: `design.extras["source_structure"]` and the provenance (`design.prov.model_file`) name them, the run gets a note that says so and the log tells how many structures were copied. A one-based campaign is left as it is, and so are the runs of the BindCraft 1 reader. [export.md](export.md), section 2, has the file naming and the caching.

## Unknown input

Nothing raises on an unknown column or metadata key, because campaign folders change between releases and people prune them by hand. Columns the reader does not map are kept in `DesignRecord.extras` (and `BackboneRecord.extras` for the trajectory table) with numbers converted to floats. The names it does not know at all are listed in `annotations["unknown_columns"]` and in `notes`. A missing table, an unparsable metadata file, a table without a `design` column and a structure that cannot be read each produce a warning, and the reader continues. Other oddities become warnings or notes too: structures whose binder length or sequence differs from the CSV, accepted designs without a structure, structures in `3_Ranked` without a CSV row, ranked designs without a file, residue letters that disagree with the sequence, hydrogens in the files, table rows that carry another version than the metadata, and a target file whose hash differs from the recorded one.

## How the pages mark such a run

- `RunData.experimental` is false for a campaign the reader has been verified on and true otherwise. It is decided from the tables and the metadata alone (so it is the same with `check_structures` off, as the import page does): the version (from `campaign_metadata.json`, else from the `bindcraft_version` column) must be one of `VERIFIED_VERSIONS` (1.0.3), the run's modality must be de novo and the metadata's `settings_modality` `binder`, `!_Refolded.csv` must have `design`, `hash`, `outcome`, `failed_filters` and `Binder_Sequence`, `!_Trajectories.csv` must have `hash`, and the metadata file must be readable.
- For any other campaign the first warning is `UNTESTED campaign: this reader was verified on de novo (binder modality) campaigns written by BindCraft 2 v1.0.3 only; here <reasons>. VHH, scFv, Fab, cyclic-peptide and multi-target campaigns and other versions are read on the same assumptions but are unverified ...`. `import` prints it as `WARNING <run id>: ...` unless `--quiet` is given, and it is copied into the manifest's warnings with the run id in front.
- `experimental` is exported on the run, in the campaign blob (`runs[]`) and in the manifest. The start page lists such a run with "experimental reader" after its id, and `binder-review inspect` prints `[experimental]` after the reader's name. A verified run has neither.

The other pages do not change their wording. A metric that BindCraft 2 does not report has its capability flag off, so the pages draw no meter or check for it, as for any run that lacks it.

## Known gaps

- **Untested**: other versions (the zero-based numbering of 1.0.0 and 1.0.1 is documented but unseen); VHH, scFv, Fab and cyclic-peptide modalities (`/` in residue lists and sequences, several binder chains, the antibody columns, the `Interdomain_Contact_Fraction`-based modality guess); campaigns with several targets (target residue lists with `/`, semicolon-separated per-target metric cells, `i_pTM.<state>` filter names); `archive_trajectories = true`. A real VHH campaign was read without an error and the residue letters of its tables match its sequences; it is flagged untested for its modality and was not checked further.
- **Downstream judges a run by the thresholds it recorded.** `config.filters_for_run` turns `annotations["filters"]` into the run's filter set (rules with provenance `campaign`), and the near-miss analysis, the accepted-design check, the filter list the exporter writes (`runs[].filters`) and the epoch hash use that set. A campaign without recorded thresholds (no `campaign_metadata.json`) keeps BindCraft 1's reconstructed defaults, which for BindCraft 2 are not its filters. Before this, every BindCraft 2 run was judged by BindCraft 1's defaults: on the real campaign some passed designs exceeded `Surface_Hydrophobicity <= 0.35`, a rule the campaign never applied, and some near-miss rescues rested on that rule. Binderscope's own rules for a campaign are the names BindCraft uses (`Unbound_Binder_pLDDT`, `Interface_Residues`), stored under the stems the reader maps them to (`value_stem`); a recorded threshold on a metric that is not mapped (`Backbone_Clashes`, `Hotspot_Contact_Fraction`, ...) or that no design carries is listed as unevaluated in `runs[].filter_set` and is never re-checked. Its verdict is still BindCraft's own (`failed_filters`).
- `Surface_Hydrophobicity` is mapped because both tools define it as the hydrophobic share of exposed binder residues, but each decides what is exposed in its own way (BindCraft 2: relative SASA of the free binder >= 0.2) and the secondary-structure shares come from another assigner. Their values are comparable, not identical.
- A `FilterFailure` of scope `Model` carries gap 0.0, because the CSV cannot say by how much one validation model missed. The near-miss logic does not read it as a measured distance: such a failure is exported with `measured: false` and `rel: null`, and a design that has one is never `near` (see [modes.md](modes.md)).
- The sibling cap of a family that the families page shows ("BindCraft keeps at most N accepted sequences per backbone") is inferred from the data for BindCraft 2. BindCraft 2's own settings are `enough_passing_sequences` and `kept_sequences`, which the reader does not pass on.
- Campaigns with several target chains are described by their first chain only; a note says so. The pipeline loads every design with target chain `A` and binder chain `B`, so designs with several binder chains (scFv, Fab) are read correctly by the reader, but the analysis after it sees only chain `B` as the binder. `design.extras["n_binder_chains"]` and `["n_target_chains"]` hold the counts.
- Cyclic peptides are recognised only by name or metadata; their numbering and peptide-specific thresholds are not handled.
- A campaign without `campaign_metadata.json` has no recorded hotspots, version, filters or target file; the reader says so in its notes and the run is untested. Such a run is built as a run without hotspots: it has no intended epitope, its epitope numbers are empty and its site A1 comes from where the designs bind ([export.md](export.md), section 10; [target_frame.md](target_frame.md)).

## Tests and the fixture

`tests/toydata/bc2.py` writes three campaigns in the shape of real output (the `hash` column, `_candidate<N>` and `_seq0` ids, `outcome`, amino-acid residue lists, comma-separated `failed_filters`, the real metadata, a `BinderMonomer` folder, trajectories that stopped early): `toya` (stamped 1.0.1, zero-based), `toyb` (no metadata, pLDDT written 0 to 100, gapped target) and `toyc` (1.0.3, the verified case, written to `bindcraft2_v103/` next to the toy project). `tests/test_reader_bindcraft2.py` starts with a test that asserts that shape, so the fixture cannot drift from the real format unnoticed, and compares the reader with what the generator wrote, not with what the reader itself reports.

## See also

[architecture.md](architecture.md) · [cli.md](cli.md) · [data_contract.md](data_contract.md) · [target_frame.md](target_frame.md) · [antibody.md](antibody.md) · [GUIDE.md](GUIDE.md)
