"""Toy BindCraft 2 campaign folders, built from the toy BindCraft 1 coordinates, in the shape of real BindCraft 2 output.

The shape is the one BindCraft 2 v1.0.3 writes, as seen on a real campaign (docs/bindcraft2_reader.md):

  1_Trajectories/!_Trajectories.csv     one row per trajectory started, with `hash`, `terminated` (blank = finished) and
                                        blank metrics for the ones that stopped early; rows in finish order, not in
                                        `trajectory` order
  2_Refolded/!_Refolded.csv             one row per refolded candidate: `<campaign>_denovo_l<len>_<hash>_candidate<N>`, `hash`,
                                        `outcome` (passed / rejected), comma-separated `failed_filters`, residue lists as
                                        `<amino acid><number>`; no `trajectory` column
  2_Refolded/Complexes/<id>.cif         chain A target, chain B binder
  2_Refolded/BinderMonomer/<id>_monomer.cif   the binder alone, chain A
  3_Ranked/!_Ranked.csv, <hash name>_seq0.cif the shortlist: `rank`, ids ending in `_seq0` (the same molecule as one
                                        candidate, found by hash and sequence), a `_bindcraft` block in the mmCIF
  campaign_metadata.json                version "BindCraft 2 v1.0.3", settings.filters as {higher, threshold, mandatory}
                                        with "Infinity" as a string, settings.targets[].target_path, ...
  summary.csv, .campaign_state.json, 1_Trajectories/<design>/ (only a losses CSV)

Heavy atoms only. Three campaigns:

  toya   v1.0.1 stamp (binder numbering zero-based, which is documented but has not been seen on a real campaign),
         six families of one to several candidates, passed designs that are not ranked, four ranked, rejected designs
         that fail on one validation model although the CSV mean passes, an unknown column in the trajectory table
  toyb   no metadata and no version anywhere, pLDDT written 0 to 100, one-based numbering, the gapped target of run02,
         no trajectory table, one unknown column in the ranked table
  toyc   v1.0.3, de novo, the verified shape: one-based, a metadata file in the real shape that records the construct
         file, trajectories that stopped early. It is written to <project>/bindcraft2_v103/ (not next to toya and toyb,
         whose folder some tests list) and is the one the reader does not flag as untested.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import runs as RN
from . import structures as ST

REFOLDED_COLUMNS = [
    "design", "length", "outcome", "Binder_Sequence", "Interface_Binder_Residues", "Interface_Target_Residues", "i_pDAE", "i_pTM",
    "pLDDT", "pTM", "i_pAE", "Unbound_Binder_pLDDT", "Target_pLDDT", "Interface_Residues", "Hotspot_Contact_Fraction", "Binder_RMSD",
    "Off_Epitope_Contact_Fraction", "bindcraft_version", "Backbone_Clashes", "All_Atom_Clashes", "Interface_BuriedArea",
    "Interface_BuriedArea_Fraction", "Surface_Hydrophobicity", "Interface_Hydrophobicity", "Binder_Helix_Fraction",
    "Binder_BetaSheet_Fraction", "Binder_Loop_Fraction", "SS_pLDDT", "Interface_Helix_Fraction", "Interface_BetaSheet_Fraction",
    "Interface_Loop_Fraction", "Target_RMSD", "Epitope_Residues_Contacted", "Binder_Disulfides", "Binder_Mass_kDa", "Binder_pI",
    "Binder_Net_Charge", "Binder_Cysteines", "Binder_Extinction", "Binder_Free_Cysteines", "Timing", "failed_filters", "hash"]
PLDDT_COLUMNS = ("pLDDT", "Unbound_Binder_pLDDT", "Target_pLDDT", "SS_pLDDT")
#: campaign filters of the toy metadata (the real shape); the toy numbers of a passed design respect them
FILTERS = {
    "i_pTM": {"higher": True, "threshold": 0.7}, "i_pAE": {"higher": False, "threshold": 0.3},
    "pTM": {"higher": True, "threshold": 0.55}, "Unbound_Binder_pLDDT": {"higher": True, "threshold": 0.7},
    "Binder_RMSD": {"higher": False, "threshold": 3.5}, "Interface_Residues": {"higher": True, "threshold": 3},
    "Surface_Hydrophobicity": {"higher": False, "mandatory": False, "threshold": "Infinity"},
    "Hotspot_Contact_Fraction": {"higher": True, "mandatory": False, "threshold": 0.0},
}


def hash16(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _binder_for(run: RN.RunOut, builder, d: RN.DesignOut, zero_based: bool):
    """Binder and target atom groups of one design in the construct frame, binder renumbered."""
    R = builder.R
    at = d.models[d.saved_model]["atoms"]
    bf = np.full(len(at), 100.0 * d.truth["vals"][d.saved_model]["pLDDT"])
    shift = -1 if zero_based else 0
    binder = at.copy(xyz=R.to_construct_frame(at.xyz), bfactor=bf, resnum=[n + shift for n in at.resnum])
    tgt = R.target_ref
    tgt = tgt.copy(xyz=R.to_construct_frame(tgt.xyz), chain="A", resnum=[n - builder.R.offset for n in tgt.resnum], bfactor=90.0)
    return tgt, binder


def _interface(tgt, binder):
    """Binder and target residue numbers within 4 A of the other partner (any heavy atom)."""
    tree = cKDTree(tgt.xyz)
    d, _ = tree.query(binder.xyz)
    bres = np.array(binder.resnum)
    b_if = sorted(set(int(x) for x in bres[d < 4.0]))
    tree_b = cKDTree(binder.xyz)
    d2, _ = tree_b.query(tgt.xyz)
    t_if = sorted(set(int(x) for x in np.array(tgt.resnum)[d2 < 4.0]))
    return b_if, t_if


def _residue_cell(letters: dict[int, str], numbers: list[int]) -> str:
    """BindCraft 2's residue list: the amino acid and the number in the written structure, "P1,A25,H26"."""
    return ",".join(f"{letters[n]}{n}" for n in numbers)


def _families(run: RN.RunOut, n: int) -> list[tuple[str, list[RN.DesignOut]]]:
    """The first n backbones (specs) of a toy run that have designs with structures, designs in generation order."""
    fams: dict[str, list[RN.DesignOut]] = {}
    for d in run.designs:
        if d.models:
            fams.setdefault(d.spec, []).append(d)
    return list(fams.items())[:n]


def _metrics(rng: np.random.Generator, passed: bool, b_if: list[int], t_if: list[int], plddt100: bool) -> dict:
    """The metric columns of one candidate on the CSV scales of BindCraft 2 (fractions 0-1, two decimals)."""
    p = lambda lo, hi: float(np.round(rng.uniform(lo, hi), 2))  # noqa: E731
    helix, sheet, ih, isheet = p(0.4, 0.8), p(0.0, 0.2), p(0.3, 0.7), p(0.0, 0.2)
    row = {
        "i_pDAE": p(0.15, 0.35), "i_pTM": p(0.74, 0.84) if passed else p(0.62, 0.68), "pLDDT": p(0.88, 0.93), "pTM": p(0.84, 0.88),
        "i_pAE": p(0.18, 0.26) if passed else p(0.33, 0.37), "Unbound_Binder_pLDDT": p(0.86, 0.92), "Target_pLDDT": 0.92,
        "Interface_Residues": float(len(b_if)), "Hotspot_Contact_Fraction": p(0.2, 0.6), "Binder_RMSD": p(0.2, 1.5),
        "Off_Epitope_Contact_Fraction": p(0.1, 0.5), "Backbone_Clashes": 0.0, "All_Atom_Clashes": float(rng.integers(0, 12)),
        "Interface_BuriedArea": p(600, 1100), "Interface_BuriedArea_Fraction": p(0.08, 0.18), "Surface_Hydrophobicity": p(0.25, 0.4),
        "Interface_Hydrophobicity": p(0.3, 0.7), "Binder_Helix_Fraction": helix, "Binder_BetaSheet_Fraction": sheet,
        "Binder_Loop_Fraction": float(np.round(1 - helix - sheet, 2)), "SS_pLDDT": p(0.88, 0.93), "Interface_Helix_Fraction": ih,
        "Interface_BetaSheet_Fraction": isheet, "Interface_Loop_Fraction": float(np.round(1 - ih - isheet, 2)), "Target_RMSD": p(0.2, 0.7),
        "Epitope_Residues_Contacted": float(len(t_if) // 2), "Binder_Disulfides": 0.0, "Binder_Mass_kDa": p(8, 14), "Binder_pI": p(4.5, 10),
        "Binder_Net_Charge": p(-8, 4), "Binder_Cysteines": 0.0, "Binder_Extinction": float(rng.integers(12000, 30000)), "Binder_Free_Cysteines": 0.0,
    }
    if plddt100:
        row.update({k: round(100 * row[k], 1) for k in PLDDT_COLUMNS})
    return row


def _write_campaign(camp: Path, name: str, run: RN.RunOut, builder, construct: Path, rng: np.random.Generator, *, n_families: int,
                    n_ranked: int, zero_based: bool, version: str | None, plddt100: bool, trajectories: bool, metadata: bool,
                    n_terminated: int = 2) -> dict:
    """Write one campaign folder and return what was written (the truth the reader is tested against)."""
    if camp.exists():
        shutil.rmtree(camp)
    version_cell = {"bindcraft_version": f"BindCraft 2 v{version}"} if version else {}
    fams = _families(run, n_families)
    ref_rows: list[dict] = []
    fam_rows: list[dict] = []
    structures: list[tuple] = []
    info_fam: dict[str, list[str]] = {}
    rejected: dict[str, list[str]] = {}
    n_rej = 0
    for fi, (spec, designs) in enumerate(fams):
        _t0, binder0 = _binder_for(run, builder, designs[0], zero_based)
        length = len(set(binder0.resnum))
        h = hash16(f"{name}:{spec}")
        traj_id = f"{name}_denovo_l{length}_{h}"
        info_fam[traj_id] = []
        first = len(ref_rows)
        for j, d in enumerate(designs, start=1):
            did = f"{traj_id}_candidate{j}"
            tgt, binder = _binder_for(run, builder, d, zero_based)
            b_if, t_if = _interface(tgt, binder)
            passed = d.status == "accepted"
            seq = ST.sequence_of(binder)
            row = {"design": did, "length": length, "outcome": "passed" if passed else "rejected", "Binder_Sequence": seq,
                   "Interface_Binder_Residues": _residue_cell(ST.residue_letters(binder), b_if),
                   "Interface_Target_Residues": _residue_cell(ST.residue_letters(tgt), t_if),
                   **_metrics(rng, passed, b_if, t_if, plddt100), **version_cell,
                   "Timing": f"worker={fi % 3};start={1.79e9 + 10 * fi + j:.2f};reprediction={30 + 3 * j:.2f}",
                   "failed_filters": "", "hash": h}
            if not passed:
                # three ways to fail: the CSV mean misses both thresholds; the mean misses one and passes the other (a validation
                # model failed it); the mean passes the only filter named (again one validation model)
                kind = n_rej % 3
                n_rej += 1
                names = [["i_pTM", "i_pAE"], ["i_pTM", "pTM"], ["Unbound_Binder_pLDDT"]][kind]
                if kind == 2:
                    row["i_pTM"], row["i_pAE"] = 0.76, 0.22
                row["failed_filters"] = ",".join(names)
                rejected[did] = names
            ref_rows.append(row)
            structures.append((did, tgt, binder, passed, d.rank, h, traj_id, seq))
            info_fam[traj_id].append(did)
        fam_rows.append({"traj_id": traj_id, "hash": h, "length": length, "first": first})

    # ranked: the best passed design of each family, the best few families (best = BindCraft 1's rank in the toy run)
    best: dict[str, tuple] = {}
    for s in structures:
        if s[3] and (s[6] not in best or (s[4] or 999) < (best[s[6]][4] or 999)):
            best[s[6]] = s
    shortlist = sorted(best.values(), key=lambda s: (s[4] or 999, s[0]))[:n_ranked]
    ranked: dict[str, int] = {}
    ranked_id: dict[str, str] = {}
    rank_rows: list[dict] = []
    ref_by_id = {r["design"]: r for r in ref_rows}
    for k, (did, tgt, binder, _p, _r, h, traj_id, _seq) in enumerate(shortlist, start=1):
        rid = f"{traj_id}_seq0"                    # another id than the candidate's: the two are matched on hash and sequence
        ranked[did], ranked_id[did] = k, rid
        r = {c: v for c, v in ref_by_id[did].items() if c not in ("design", "outcome", "bindcraft_version", "Timing", "failed_filters")}
        rank_rows.append({"rank": k, "design": rid, **r, "settings_core": "default", "settings_modality": "binder",
                          **({"Brand_New_Column": "kept as an extra"} if name == "toyb" else {})})
        ST.write_cif(camp / "3_Ranked" / f"{rid}.cif", [tgt, binder], rid, {
            "_bindcraft.stamp_format_version": "3", "_bindcraft.binder_chains": "B", "_bindcraft.target_chains": "A",
            "_bindcraft.campaign": name, "_bindcraft.design": rid, "_bindcraft.rank": str(k)})

    for did, tgt, binder, passed, _r, _h, _t, _s in structures:
        ST.write_cif(camp / "2_Refolded" / "Complexes" / f"{did}.cif", [tgt, binder], did,
                     {"_bindcraft.design": did, "_bindcraft.outcome": "passed" if passed else "rejected"})
        ST.write_cif(camp / "2_Refolded" / "BinderMonomer" / f"{did}_monomer.cif", [binder.copy(chain="A")], did,
                     {"_bindcraft.binder_chains": "A", "_bindcraft.design": did})
    pd.DataFrame(ref_rows, columns=[c for c in REFOLDED_COLUMNS if c in ref_rows[0]]).to_csv(camp / "2_Refolded" / "!_Refolded.csv", index=False)
    pd.DataFrame(rank_rows).to_csv(camp / "3_Ranked" / "!_Ranked.csv", index=False)

    info = {"families": info_fam, "passed": [r["design"] for r in ref_rows if r["outcome"] == "passed"], "rejected": rejected,
            "ranked": ranked, "ranked_id": ranked_id, "zero_based": zero_based, "version": version, "n_trajectories": len(fams)}
    if trajectories:
        # every trajectory that was started has a row; the ones that stopped early carry no metrics. Rows are in the order they
        # finished, so the `trajectory` numbers are not sorted
        n_all = len(fams) + n_terminated
        numbers = [int(x) for x in rng.permutation(np.arange(1, n_all + 1))]
        rows = []
        for fam, num in zip(fam_rows, numbers):
            src = ref_rows[fam["first"]]
            cols = {c: src[c] for c in ("Binder_Sequence", "i_pDAE", "i_pTM", "pLDDT", "pTM", "i_pAE", "Target_pLDDT", "Interface_Residues",
                                        "Hotspot_Contact_Fraction", "Off_Epitope_Contact_Fraction", "Surface_Hydrophobicity", "SS_pLDDT")}
            rows.append({"trajectory": num, "design": fam["traj_id"], "length": fam["length"], **cols, **version_cell,
                         "Timing": f"worker=1;start={1.79e9 + num:.2f};design={300 + num:.2f};compiled=1", "hash": fam["hash"],
                         "terminated": "", "autotuned": "", "settings_core": "default", "settings_modality": "binder",
                         "Mystery_Score": round(0.1 * num, 2)})
        stages = ["anneal", "final", "harden"]
        for k in range(n_terminated):
            h = hash16(f"{name}:lost{k}")
            rows.append({"trajectory": numbers[len(fams) + k], "design": f"{name}_denovo_l{70 + k}_{h}", "length": 70 + k, **version_cell, "hash": h,
                         "terminated": stages[k % 3], "autotuned": "", "settings_core": "default", "settings_modality": "binder",
                         "Timing": f"worker=2;start={1.79e9 + k:.2f};design=40.00;compiled=1"})
        df = pd.DataFrame(rows)
        if name != "toya":
            df = df.drop(columns=["Mystery_Score"])
        (camp / "1_Trajectories").mkdir(parents=True, exist_ok=True)
        df.to_csv(camp / "1_Trajectories" / "!_Trajectories.csv", index=False)
        for r in rows:                                  # trajectory folders hold only the losses (archive_trajectories is false)
            (camp / "1_Trajectories" / r["design"]).mkdir(exist_ok=True)
            (camp / "1_Trajectories" / r["design"] / f"{r['design']}_losses.csv").write_text("step,loss\n0,1.0\n")
        info.update(n_trajectories=n_all, n_completed=len(fams), n_terminated=n_terminated, trajectory_numbers=sorted(numbers))
        (camp / ".campaign_state.json").write_text(json.dumps({"accepted": len(shortlist), "attempted": [r["hash"] for r in rows]}))
    if metadata:
        lo, hi = 60, 110
        meta = {
            "checkpoints": {}, "metadata": {"settings_core": "default", "settings_modality": "binder"},
            "resolved": {"binder_chains": ["binder"], "campaign_seed": 0, "validation_models": ["model_1_ptm", "model_2_ptm"]},
            "revision": "a8d0f20" if version == "1.0.1" else "0123456789abcdef0123456789abcdef01234567-dirty",
            "sampled_binder_lengths": f"{lo} to {hi}", "settings_digest": hash16(name)[:12],
            "settings": {"archive_trajectories": False, "binder_lengths": [lo, hi], "campaign_name": name, "filters": FILTERS,
                         "min_plddt_final": 0.7, "modality": "binder", "number_of_final_designs": len(shortlist),
                         "targets": [{"chains": "A", "hotspots": ",".join(str(h) for h in builder.hot_c), "name": "toy_construct",
                                      "target_path": str(construct.resolve())}]},
            "version": f"BindCraft 2 v{version}",
        }
        (camp / "campaign_metadata.json").write_text(json.dumps(meta, indent=1))
    n = len(ref_rows)
    (camp / "summary.csv").write_text(
        "campaign,scope,metric,samples,mean,std,min,max\n"
        f"{name},campaign,redesign_candidates,1,{n}.0,0.0,{n}.0,{n}.0\n"
        f"{name},campaign,accepted_designs,1,{len(shortlist)}.0,0.0,{len(shortlist)}.0,{len(shortlist)}.0\n")
    return info


def write_campaigns(out: Path, r1: RN.RunOut, r2: RN.RunOut, b1, b2, T, seed: int) -> dict:
    root = out / "bindcraft2"
    verified = out / "bindcraft2_v103"
    rng = np.random.default_rng(seed + 700)
    construct = out / "construct" / "toy_construct.pdb"
    deleted = [[a - 59, b - 59] for a, b in ((79, 82), (139, 142))]
    info: dict = {}
    info["toya"] = _write_campaign(root / "toya", "toya", r1, b1, construct, rng, n_families=6, n_ranked=4, zero_based=True,
                                   version="1.0.1", plddt100=False, trajectories=True, metadata=True)
    info["toya"]["hotspots_construct"] = b1.hot_c
    info["toyb"] = _write_campaign(root / "toyb", "toyb", r2, b2, construct, rng, n_families=3, n_ranked=3, zero_based=False,
                                   version=None, plddt100=True, trajectories=False, metadata=False)
    info["toyb"].update(plddt_scale="0-100", deleted_construct=deleted)
    info["toyc"] = _write_campaign(verified / "toyc", "toyc", r2, b2, construct, rng, n_families=5, n_ranked=3, zero_based=False,
                                   version="1.0.3", plddt100=False, trajectories=True, metadata=True)
    info["toyc"].update(hotspots_construct=b2.hot_c, path="bindcraft2_v103/toyc", deleted_construct=deleted)
    return info
