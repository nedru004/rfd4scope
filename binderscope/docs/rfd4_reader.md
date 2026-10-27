# RFdiffusion 4 reader

Reads a finished RFdiffusion 4 (RFD4) run folder into Binderscope's `RunData`. Review-only: it never launches RFD4.

The reader is **experimental**. It was written against the measured output of the runs under `rfd4/out` and carries no published RFD4 version stamp. Every campaign it reads keeps `experimental = True` and a warning that says so.

## Layout it detects

```
<run>/
  inference_metrics/per_example_metrics.csv        or per_example_metrics_reeval.csv
  inference_metrics/aggregated_metrics.csv         run-level medians (cross-check)
  inference_samples/<example_id>/<example_id>_generated.cif
  inference_samples/<example_id>/<example_id>_af3_refolded.cif
  inference_samples/<example_id>/<example_id>_af3_confidences.json
  inputs/<spec>.yaml, inputs/<target>.cif
```

Detection is the presence of `inference_metrics/per_example_metrics*.csv`. Absolute paths inside the CSV are ignored; files are resolved next to the CSV.

## Scales

AF3 values in these files are 0–100 for pLDDT / pTM / ipTM, and ångström for PAE. Binderscope stores BindCraft stems on 0–1 and `i_pAE` as ångström / 31:

| CSV column | Stored stem | Scale |
|---|---|---|
| `plddt_coi` | `pLDDT` | / 100 |
| `overall_ptm` | `pTM` | / 100 |
| `iptm_coi` | `i_pTM` | / 100 |
| `ipae_coi` | `i_pAE` | / 31 |
| `min_ipae_coi` | `min_ipae` | Å (scalar; no PAE matrix is written) |
| `refolded_backbone_rmsd.rmsd` | `complex_rmsd` | Å |
| `refolded_motif_ca_rmsd.rmsd` | `motif_ca_rmsd` | Å |

Pairwise columns `iptm_<a>_<b>` and `min_ipae_<a>_<b>` that involve the designed chain become `design.per_chain[partner]`.

## Acceptance: user cutoffs, not RFD4's pass

`motif_scaffold.pass` is often zero for every row of a run that is still worth reading. Acceptance is therefore a cutoff set the person supplies at import (`--cutoff NAME=OP:VALUE`, or the Options fields on the import page). Defaults:

- whole design: `pLDDT >= 0.80`, `has_clash <= 0`, and when present `motif_ca_rmsd <= 1.0`, `complex_rmsd <= 2.5`
- per required target chain: `iptm >= 0.5`, `min_ipae <= 2.0`

A design is accepted only if it clears the bar on **every** chain whose role is `require` (the default for every target chain). `ignore` is scored and shown, never gating; `avoid` inverts the rule. RFD4's own `motif_scaffold.pass` / `ppi.pass` stay on `design.extras["tool_pass"]` and are never merged into the Binderscope verdict.

Filter provenance is `user` and `FilterSet.source` is `user`, so near-miss and rescue re-evaluate from the metrics normally.

## Structures: design / re-score pair

The AF3 refold is the analysed structure (scores describe it; B-factors are pLDDT). The generated CIF is loaded only to compute `design.rf`, the rigid transform that maps the analysed binder CA onto the generated one after both are placed. The board draws that as a dashed ghost when `complex_rmsd > 2.5`. A design whose refold is missing is metric-only: the generated file's designed chain has NaN B-factors and is never analysed as primary.

`min_ipae` is filled from the scalar column even though the schema describes it as PAE-derived; the confidences JSON holds no PAE matrix, so `has_pae` and ipSAE stay off.

## Motif

Fixed stretches from the contig (for example `A16-26,A53-60`) become `annotations["scaffold"]` with `kind: "motif"`. They name the motif RMSD columns and are exported for colouring; they are not BindCraft scaffold edits, so the paratope alignment does not run on them. `C_HOT` selects become the epitope / hotspot list on the primary target chain.

## What switches off

Through `caps()`: the Rosetta block, MPNN scores, per-validation-model columns, PAE / ipSAE, trajectory stage, `xlsx_marked`. Deliberately off: BindCraft 1's `DEFAULT_FILTERS` and the antibody / CDR vocabulary. Kept: termini, specificity, hotspot / epitope machinery, recomputed interface analysis.

## Profile

`--profile rfd4` (auto-selected when every imported run is RFD4 and the person left the default). Badge, credit, AF3 predictors, cutoff-oriented presets, and re-score wording that names the oracle.
