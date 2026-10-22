# Antibody and nanobody profiles

`binder_review.antibody` (`src/binder_review/antibody.py`) is an optional module for projects whose designs are antibody-like: nanobody (VHH) and VH or VL domains, or scFv. For one sequence it numbers the V domain (IMGT), marks framework and CDR regions, calls VHH-like or VH-like hallmarks, checks the cysteines, measures how much of the interface the CDRs make up and tags sequence liabilities with their region. This page is for developers and advanced users.

Everything here is a screen, not a verdict. The default build does not use the module: the exporter writes every design with `modality` set to `minibinder` and never calls it. The module is a hook for an exporter extension or your own script, and the inspector page can display what it returns.

## The exporter hook

The snippet adds the antibody fields to the inspector record of one design (an entry of the inspect blob, see [data_contract.md](data_contract.md)).

```python
from binder_review import antibody

prof = antibody.profile_sequence(seq, contact_residues=b_contact, sg_xyz=None, rsa=rsa_res)
if prof["ok"]:
    inspector["regions"] = prof["regions"]           # per residue: FR1 CDR1 FR2 CDR2 FR3 CDR3 FR4, or "tag"
    inspector["cdrs"] = prof["cdrs"]                 # single domain: [cdr1, cdr2, cdr3]
    if "interface" in prof:                          # present when contacts were given
        inspector["region_counts"] = prof["interface"]["counts"]
    if "nH" in prof:
        inspector["nH"] = prof["nH"]                 # scFv only: residues in the VH domain
```

- `contact_residues` are 1-based binder residues in contact with the target (`DesignAnalysis.bc` has this form). `rsa` is the relative SASA per residue (`rsa_res`). `sg_xyz` is an optional `{position: (x, y, z)}` of the Cys SG atoms, for distance-based disulfide pairing.
- All positions are 1-based indices into the sequence that was passed in. Numbering is stored beside the design, and coordinates are never renumbered. Non-letter characters are dropped before numbering. Residues outside the V domain (leader, tag, linker) get the region `tag`.
- Nothing raises on odd input. A failure is `{"ok": False, "error": ..., "n": ..., "backend": None}`.

What the inspector reads is in `schema/inspect.schema.json` ([viewer_inspector.md](viewer_inspector.md)). Three points to check when wiring the hook:

- The page's paratope finding reads `region_counts` as the number of interface residues per region and divides the CDR counts by the number of interface residues. `profile_sequence` returns its own `region_counts` for the whole sequence and the interface counts in `interface["counts"]`, so the snippet uses the latter.
- The page detects an scFv from `chain_of` (the chain letter per residue) and measures the VH to VL gap from it. The module does not return `chain_of`; for an scFv it returns `order`, `nH` and `linker` to derive it.
- The schema declares `cdrs` as a list of strings, `nH` as an integer and `modality` as one of `minibinder`, `nanobody`, `scFv` or `peptide`. For an scFv the module returns `cdrs` as `{"H": [...], "L": [...]}`, and for a single light-chain domain it returns the modality `Fv-chain`; reshape both before writing the record.

## Backends

`backend` is `"auto"` (the default), `"abnumber"` or `"fallback"`. The result's `backend` says which one produced the numbering.

- **abnumber** gives real IMGT numbering and is the reference. It comes with the optional `antibody` extra (`pip install -e ".[antibody]"`). abnumber calls ANARCI to number a sequence, which needs the `anarci` Python package and the HMMER program `hmmscan` on the `PATH`. If either is missing, the abnumber numbering fails: with `"auto"` the fallback is used silently, and with `"abnumber"` the result is `ok: False`. abnumber's own requirements may also install `anarcii` and, through it, `torch`, which is large.
- **fallback** needs nothing beyond the standard library. It anchors on Cys23, Trp41, Cys104 (a Cys preceded by Y, F, H, W or V, 50 to 63 residues past Trp41, so extra CDR3 cysteines are not mistaken for it) and the J motif `WGxG` or `FGxG`, then places the IMGT limits at fixed distances: CDR1 from Cys23 + 4 to Trp41 - 3, CDR2 from Trp41 + 15 to 38 (heavy) or 36 (light) residues before Cys104, CDR3 from Cys104 + 1 to the residue before the J motif. FR1 starts 21 or 22 residues before Cys23. It was calibrated against abnumber on a small set of germline-like sequences and not validated more widely.

Fallback limits: it needs at least 85 residues and all four anchors, otherwise it returns `ok: False`. It does not number insertions, so there are no IMGT labels beyond the anchors and the hallmark positions (`labels` is `None`). A leader before the domain can shift the start of FR1 by a residue, and the CDR boundaries of insertion-rich domains are not guaranteed. Use abnumber when it is installed.

## What it computes

- **Regions.** FR1 to FR4 and CDR1 to CDR3 (IMGT definition): spans (1-based, inclusive) and the CDR sequences, with the chain type H, K or L.
- **Hallmarks** at IMGT 42, 49, 50 and 52 (Kabat 37, 44, 45 and 47), for heavy chains only. The call is `VHH-like` when at least three of the four residues are in F or Y, E or Q, R or C, and G, L or F respectively, `VH-like` when at least three are V, G, L and W respectively, and `mixed` otherwise.
- **Cysteines** (`cysteine_report`). Cys23 and Cys104 are the conserved pair. Every other Cys is extra. Extra Cys that can pair are not free thiols: with `sg_xyz`, a pair has SG to SG within 2.5 angstrom; otherwise an even number of extra Cys is paired by parity, a CDR3 partner first, and the rest in sequence order (VHH often carry a second disulfide between CDR3 and CDR1, CDR2 or FR2). Extra Cys that have coordinates but no partner are free, and with an odd number of unresolved extra Cys all of them are reported free. The `status` is `ok` (only the conserved pair), `extra disulfide`, `free cysteine` or `canonical pair broken` (Cys23 or Cys104 is missing). Parity pairing is an assumption, and the note says so. The pairs go to `liabilities.find_liabilities` as `ssbonds`, so paired Cys are not reported as unpaired.
- **CDR share of the interface** (`cdr_interface_share`): the fraction of contacting V-domain residues that lie in CDR1 to CDR3, with counts per region and per CDR. Tag and linker residues are left out of the share, and it is `None` when no V-domain residue is in contact. The inspector page flags a share below 0.6.
- **CDR liabilities** (`cdr_liabilities`): the sequence liabilities of [interface.md](interface.md) with `region` and `in_cdr` added. An N-glycosylation sequon, an NG deamidation or a DG isomerisation site inside a CDR is raised to `bad`, because it cannot be fixed outside the paratope and may change binding. Other liabilities keep their severity.
- **scFv** (`split_scfv`): a Gly and Ser run of 10 or more residues is the linker. The module tries each such run, numbers both halves (each at least 90 residues) and accepts the first split into one heavy and one light chain, in the order VH-VL or VL-VH. Up to three closing Ser of FR4 are given back from the linker. `nH` is the length of the VH domain. For an scFv the cysteines are reported per chain by parity only, because `sg_xyz` is used for a single domain only.

## Result keys of `profile_sequence`

`ok`, `n`, `modality` (`nanobody` for any single heavy-chain domain, `Fv-chain` for a single light-chain domain, `scFv`), `backend`, `chain_types`, `regions` (per residue), `region_counts` (residues per region over the whole sequence), `spans` (`H.CDR3` style keys for an scFv), `cdrs` (a list of three sequences, or `{"H": [...], "L": [...]}` for an scFv), `hallmarks` (`residues`, `string`, `call`), `cysteines` (a report, or one per chain for an scFv), `cysteine_status`, `interface` (when contacts are given), `liabilities` and `cdr_liabilities` (the subset in CDRs). An scFv result adds `nH`, `order` and `linker`.

The other functions are `number_chain(seq, backend)`, `hallmarks`, `cysteine_report`, `cdr_interface_share`, `cdr_liabilities` and `split_scfv`.

## Limits

- The module reads a sequence, not a structure. It cannot tell whether a single heavy-chain domain is a VH or a VHH except through the hallmark call, and `modality` says `nanobody` for both.
- A heavy and a light chain given as separate sequences are profiled one by one; they are not combined into an Fv.
- Parity pairing of cysteines is a guess. Confirm it with the structure when it matters.
- Only an scFv with a Gly and Ser linker of 10 or more residues is split into two domains; other multi-domain sequences are not supported.
- The IMGT numbering and the thresholds of the liabilities are screens: they say where to look, not what will fail.

## See also

[interface.md](interface.md) · [viewer_inspector.md](viewer_inspector.md) · [data_contract.md](data_contract.md) · [architecture.md](architecture.md)
