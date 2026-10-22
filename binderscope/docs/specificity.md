# Specificity panel

The specificity panel (`src/binder_review/specificity.py`) compares the target's epitope with the same protein in other species and with related proteins of the same organism. It answers two questions about a design: will the binder still recognise the target in the species one tests in (cross-reactivity, from orthologs), and does it sit on residues that the closest relatives share (off-target risk, from paralogs)? This page says how the panel is described and built, how identity at the contacted residues is computed, what the pages do with it, what it needs, and what the output holds. It is for developers and advanced users.

Both answers are sequence screens at the contacted residues. Neither is a binding measurement. The panel uses public UniProt sequences and AlphaFold DB models, and optionally ConSurf-DB grades; no binder sequence and no design id is ever used or sent.

## The panel

| Role | Members | Notes |
|---|---|---|
| Reference | the target | its UniProt sequence. The numbering of the reference structure must equal it ([target_frame.md](target_frame.md)) |
| Orthologs | the same protein in other species | for cross-reactivity. Each member is a UniProt entry. Members marked `optional` are tagged as optional on the pages, but they still count for `n_orth` and the grades. Most orthologs are unreviewed genome-annotation entries, so the panel is a sequence panel, not an annotated reference |
| Paralogs | related proteins of the same organism | for off-target risk. Members marked `optional` are more distant: they are kept in `par[]` (with `optional` and `distant` true) but left out of the paralog track |

Which members appear, their labels and their keys come from `profile.specificity` ([profile_spec.md](profile_spec.md)).

## What it needs

A panel is built when the target's UniProt accession is known and the profile names species or has a `paralogs` block. Otherwise the pipeline writes an empty blob (below) and says why in its log. The built-in default profile names no species and no paralogs, so a project gets a panel only with a profile that has a `specificity` block (`--profile FILE`, see [cli.md](cli.md) and [profile_spec.md](profile_spec.md)).

- **The accession of the target.** It is the reference sequence: `--uniprot`, else `target.uniprot` of the profile ([cli.md](cli.md)).
- **The species list**, in the profile: `specificity.species[]` with `match`, `label` and `key`, and optionally `optional`, `phrase` and more. `match` is `acc:<accession>`, which names one UniProt entry and needs nothing else, or an organism name as in UniProt, which is resolved against the panel file (below). `key` is what rules, `show` and `headline` refer to; it contains letters, digits and `_`.
- **The paralog list**, in the profile: `specificity.paralogs.members[]`, each `{acc, gene, optional}`. `order` lists gene symbols in display order, and `focus` names the paralog the pages single out.
- **Public records, by accession.** UniProt sequence and entry of the target and of every member; AlphaFold DB models of the target and of every paralog, for the structural alignment; ConSurf-DB grades only with `--consurf`. They are read from the cache, or fetched when the build runs with `--online`. `scripts/fetch_reference.py --profile PROFILE.json` fetches everything a profile names ahead of time ([reference_data.md](reference_data.md)).
- **A panel file, as an alternative to the lists.** `panel.json` in the reference cache, written by `scripts/fetch_reference.py --config`, holds a target, chosen orthologs and paralogs. It supplies the species of a profile that names none, the paralogs of a profile whose `paralogs` block has no `members`, and the details of an entry the profile names by accession. A species given by organism name is found only there.
- **The residues.** The panel covers the union of the runs' constructs in the reference numbering, so every per-residue array has one entry per entry of `resis`.

```json
"specificity": {
  "subject": "the target", "kind": "host",
  "species": [
    {"match": "acc:P12345", "label": "Mouse", "key": "mouse"},
    {"match": "acc:P23456", "label": "Pig", "key": "pig", "optional": true}],
  "show": ["mouse"],
  "paralogs": {"members": [{"acc": "P34567", "gene": "GENE2"},
                           {"acc": "P45678", "gene": "GENE3", "optional": true}],
               "focus": {"key": "GENE2", "label": "GENE2"}, "order": ["GENE2", "GENE3"]}
}
```

The accessions and gene symbols are placeholders. A species or paralog whose UniProt record cannot be obtained is skipped with a warning in `generated.warnings`. The target's own record is required: when it is missing the stage stops with an error that names it.

## Method

**Alignment.** Every member is aligned globally to the reference with `Bio.Align.PairwiseAligner`: BLOSUM62, gap open -10, extend -0.5, end gaps penalised like internal ones. The panel is a star of pairwise alignments to the reference, not a multiple alignment. Insertions in a member are dropped, so every reference residue maps to one member residue or to a gap. The full-length map is kept in memory for `identity_at`; the blob carries only the cut to the target residues.

**Paralogs are matched by structure when they can be.** Distant paralogs, below about 30 percent identity, are too different for a sequence alignment to place loops reliably, and their identities move with the aligner settings. For every paralog whose AlphaFold DB model is in the cache (and with a cached model of the reference, in UniProt numbering) the per-residue map is the TM-align pairing (`tmtools`) of the paralog model onto the reference model. TM-align superposes the chains and gives a sequential pairing; a pair whose CA atoms are more than 4 Å apart after superposition does not superpose and is low confidence, and a reference residue without a partner is a gap. The sequence alignment stays in `Member.seq_map` and feeds the whole-protein figures (`stats.full`, `stats.observed`, `identity_of_member_length`, `ident_full`); the construct figures and every per-residue cell use the structural map (`stats.construct_sequence` repeats the construct identity for the sequence alignment). `par[].aligner` says which method a member used, `par[].struct` holds the TM-score, the RMSD and the number of paired residues, and `par_note` is the sentence the inspector prints under the paralog bars. Without a cached model, or when the structural alignment fails, the paralog falls back to the sequence alignment and a warning says so. Orthologs are always aligned by sequence. The structural pairing is a screen too: positions that do not superpose are flagged, not corrected.

**Identity and similarity.** Identity at a residue set is `identical / n` over the reference residues, with gaps counting as not identical. A member residue is similar when it differs but is in the same group (ILVM, FYW, KRH, DE, ST, NQ, AG), the viewer's own definition; similarity is `(identical + similar) / n`. Per member the blob carries `stats` for the whole protein (`full`), the region the reference structure observes (`observed`, when the build knows it) and the construct (`construct`, the panel's residues), plus `identity_of_member_length`, which divides by the member's own length and is the figure UniProt and BLAST-like tools quote. `orth[].ident` divides by the reference length. Paralog identities are reported to whole percent because they move by several points with the aligner settings.

**Low confidence.** For a structural alignment a position is flagged (`lowconf` lists the numbers) when its pair is more than 4 Å apart after superposition. For a sequence alignment a reference position that has an aligned residue is flagged when fewer than 25 percent of the positions in the 15-residue window around it are identical, or (paralogs only) when it lies within 7 residues of a gap of 8 residues or more in either sequence. The identity window alone misses a loop that is aligned in the wrong register, because the wrong register can also look similar. Gap positions are not flagged (they already show as a gap). The paralog track treats low-confidence positions as gaps. The flag is a screen for windows that are not much better than chance, not a verdict.

**Ortholog conservation and grades (the headline track).** For each position the non-gap residues of the orthologs (the reference itself is not in the column) give `cover` (the share of orthologs with a residue), `ident` (the share equal to the reference residue) and the Shannon entropy `H` of the residue counts (pseudocount 1e-9), `cons = 1 - H / log2(20)`. `grade` is the rank of `cons * max(cover, 0.3)` over the positions in nine bins, 1 variable to 9 conserved.

*Ties.* A small panel of close orthologs has many invariant columns, so their scores tie. Ranking ties by their average rank would put an invariant majority into a middle grade and make invariant residues look less conserved than residues with a single substitution. Here a tied block that is larger than its share of a bin takes the highest grade still free, and the remaining positions are ranked again into the remaining grades. Invariant columns therefore grade 9, some grades may stay empty, and the function is monotone: a higher score never gets a lower grade.

*How this differs from ConSurf.* ConSurf builds a large alignment of homologs (orthologs and paralogs), weights the sequences by a phylogenetic tree, scores each column with a Bayesian rate estimate and bins the normalised score. Here: only the orthologs of the panel, no weighting (two closely related species count as two sequences), a plain entropy and rank bins. The grade is therefore coarse and phylogenetically uneven. Use it to see which epitope residues vary between the species of this panel, not as a rate of evolution.

**Paralog track (the second track).** `par_cons`, `par_ident`, `par_cover`, `par_grade` and the scalars `n_par` and `par_keys` describe the paralogs that are not optional. They are computed like the ortholog track with two differences: the reference residue is included in the column (the question is whether the residue is shared across the family), and low-confidence or gap positions do not count. A high `par_grade` means the residue is shared across the family, which raises the off-target risk of a binder that relies on it; a high ortholog `grade` means it is shared across species, which favours cross-species reactivity. The two answer different questions and are never mixed: `grade` is orthologs only.

**ConSurf-DB overlay (optional).** `consurf.grade` holds the ConSurf-DB grades of the reference chain at the panel's residues (null where there is none), `consurf.insufficient` the residues ConSurf marks with a star, and `consurf.licence` the academic, non-commercial note. A residue whose letter disagrees with the reference is dropped with a warning. It exists only with `--consurf`, when the reference structure is a PDB entry that ConSurf-DB covers, and it never feeds the ortholog grades.

## How the pages use it

For a design, the contacted residues are its heavy-atom contacts within 4.5 Å (`design.tc`), reference numbers, kept when they are in the panel's residues (`resis`). For each member the pages count, over those residues: identical (the member's letter equals the reference letter), similar (different, same group) and gaps (the member has `-`). The share is `identical / n` with `n` the number of contacted residues, so a gap counts as not identical. The residues that differ are listed as reference letter, number, member letter (`A45G`, an invented example); gaps are not listed there. `identity_at(panel, key, residues)` in Python gives the same numbers (`n`, `identical`, `similar`, `gap`, `identity`, `similarity`, `differing`, `gaps`, `lowconf`).

**Reading rules.** The Specificity tab of the inspector has a "Reading" block built from `profile.specificity.rules[]`. Each rule has an `expr` that says what is measured, and every number in it comes from the profile:

| `expr` | Value of the rule |
|---|---|
| `{species: key}` | the identity share in one species of the panel |
| `{min_species: [keys]}` | the smallest identity share among those species |
| `{paralog: gene}` | the identity share in one paralog |
| `{paralog_max_excl: gene}` | the highest identity share among all paralogs except the named one (usually the focus paralog) |

The rule compares the value with `val` using `op` (`>=` or `<=`); `label`, `note` and `rescue` are the words shown, and the species rules and the paralog rules are shown under their own headings (cross-species reactivity, off-target risk) with the assay that would settle each. The Query page offers the same rules as filters (with `lo`, `hi`, `step`, `unit` and `on` from the profile) when their species or paralogs are in the panel; a species rule reads 1 when the design has no contacted residue in the panel. Nothing in the Reading block is hard-coded except that the Specificity tab needs at least three contacted residues in the panel.

**Findings.** The inspector's specificity findings use thresholds of the page itself. They need at least four contacted residues. The ortholog finding counts the contacted residues with grade 7 to 9 as strongly conserved. The paralog finding warns when a paralog other than the focus one is identical at half or more of the contacted residues, and informs when the focus paralog is identical at 40 percent or more. See [viewer_inspector.md](viewer_inspector.md), [viewer_map.md](viewer_map.md), [viewer_query.md](viewer_query.md) and [viewer_board.md](viewer_board.md) for the rest.

## When data is missing

The exporter writes `caps.species_panel` true only when the blob has orthologs and `caps.paralog_panel` true only when it has paralogs. A profile can switch a flag off but never on.

| Situation | Result |
|---|---|
| no accession, or the profile names no species and no `paralogs` block | `empty_homolog_json`: the blob has the reference residues, no orthologs, no paralogs and flat tracks; `note` says why. Both flags are false and the specificity features are hidden: no Specificity tab, conservation colour or specificity findings in the inspector, no specificity rules on the Query page, no Specificity row on the Board, no Specificity block, species columns or paralog bars on the Map |
| orthologs but no paralogs | `paralog_panel` is false: no paralog bars, columns, rules or findings |
| a species or paralog record is not in the cache and the build is offline | that member is skipped with a warning. A species rule whose species is absent is not offered |
| no AlphaFold DB model for a paralog or for the reference | the paralog is compared by sequence alignment and marked `aligner: "sequence"`; a warning says so |
| ConSurf-DB data absent, or `--consurf` not given | no `consurf` object, no overlay toggle |
| an ortholog below 70 percent identity to the reference | kept, with a warning that the accession may be wrong |

## Output

The blob is `data/homolog.json` (`HOM`), schema `binderscope.homolog/1` ([schema/homolog.schema.json](schema/homolog.schema.json); [data_contract.md](data_contract.md) has the field table). Every per-position array and every aligned sequence has one entry per entry of `resis`.

| Field | Meaning |
|---|---|
| `resis`, `seq` | the panel's residues and the reference letter at each |
| `cons`, `ident`, `cover`, `grade`, `n_orth` | the ortholog track |
| `orth[]` | `acc`, `org`, `ident`, `map` (aligned letter per residue, `-` for a gap), `gene`, `label`, `key`, `taxon`, `reviewed`, `length`, `optional`, `extra`, `lowconf`, `stats` |
| `par[]` | `acc`, `name`, `gene`, `cov` (share of the panel's residues that have a partner), `ident_ref` (identity over those residues), `ident_full` (identity over the whole protein, from the sequence alignment), `map`, `label`, `key`, `length`, `optional`, `distant`, `lowconf`, `stats`, `aligner`, `struct`, `taxon`, `reviewed` |
| `par_cons`, `par_ident`, `par_cover`, `par_grade`, `n_par`, `par_keys`, `par_note` | the paralog track and one sentence on how the paralogs were matched |
| `species`, `show`, `subject`, `kind` | mirrors of `profile.specificity`, written for older pages; species that are not in the blob are left out |
| `note` | where the sequences came from |
| `consurf` | the optional overlay |
| `generated` | `tool`, `version`, `date`, `params` (the alignment settings, the low-confidence settings and, for structural alignments, the method and the TM-score and RMSD of each paralog) and `warnings` |

`extra` marks an entry added by hand through the API; it is kept out of `n_orth` and the ortholog track.

## API

```python
from binder_review import specificity as sp

panel = sp.build_panel(resis, profile, accession="P12345", include_consurf=False)   # resis: reference numbers
hom = sp.to_homolog_json(panel)                  # a dict valid against homolog.schema.json
sp.identity_at(panel, "mouse", [45, 61, 62])     # invented residue numbers
sp.region_identity(panel, "GENE2", 20, 80)
sp.hotspot_identity_table(panel, {"run01": [45, 61]})   # rows per run and member
sp.empty_homolog_json(resis, letters)            # the blob without a panel
sp.build_panel_from_sequences(reference, orthologs, paralogs, resis)   # sequences in, no reference data
```

`build_panel(resis, profile=None, *, include_consurf=True, accession=None, panel=None, observed=None, pdb=None, chain="A")` takes the accession (default: `target.uniprot` of the profile, else the target of `panel`), an optional panel dictionary in the format of `panel.json`, the residues the reference structure observes, and the PDB id and chain for the ConSurf-DB overlay. `panel_requested(profile)` tells whether a profile asks for a panel. `SpecificityPanel.member(key)` finds a member by key or accession. `hotspot_identity_table` takes hotspots in reference numbers.

From the command line, `python -m binder_review.specificity --uniprot ACC --residues FIRST-LAST [--observed FIRST-LAST] [--reference-id PDBID] [--profile FILE] [--out FILE] [--hotspots RUN=R1,R2] [--print-tables]` builds a panel from the cache and writes the blob; `--print-tables` and `--update-doc FILE` render markdown tables of the identities, optionally with the aligned residues at hotspots.

## Caveats

- A sequence screen cannot tell whether a shared fold gives the same surface, nor whether the binder's contacts survive a substitution. Identity at the epitope says whether residues are the same, not whether binding is. Confirm any hit with a binding assay against the purified protein of that species or against the purified relative.
- UniProt sequences only. Splice forms, polymorphisms and glycosylation differences are not modelled.
- Orthologs are mostly unreviewed genome-annotation entries. A wrong annotation shows as an unexpectedly low identity; the build warns when an ortholog is below 70 percent identical to the reference.
- Orthologs use a pairwise sequence alignment to the reference, which is reliable at high identity. Distant paralogs use a structural alignment of AlphaFold DB models, which are predictions, not experimental structures; the asterisks in the pages and the low-confidence lists mark pairs that do not superpose, and loops of distant members match least well.
- Conservation grades from a handful of sequences are coarse; they are not ConSurf grades.
- The panel does not change a design's verdict.

## See also

[target_frame.md](target_frame.md) · [reference_data.md](reference_data.md) · [profile_spec.md](profile_spec.md) · [data_contract.md](data_contract.md) · [viewer_inspector.md](viewer_inspector.md) · [viewer_map.md](viewer_map.md) · [viewer_query.md](viewer_query.md) · [export.md](export.md)
