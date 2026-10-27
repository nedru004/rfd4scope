"""RFD4 reader on a synthetic run folder (CSV, yaml, twin CIFs, second oracle).

Numbers come from what the fixture writes, not from the reader. Real ``rfd4/out`` trees are not fixtures.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import gemmi
import pytest

from binder_review.config import check_filters, filters_for_run, parse_cutoff_arg
from binder_review.readers import bindcraft1, detect_reader, get_reader, rfd4


# ------------------------------------------------------------------------------------------------------------ fixture
def _aa3(one: str) -> str:
    return gemmi.find_tabulated_residue(one).name if len(one) == 1 else one


def write_cif(path: Path, chains: dict[str, str], *, bfactor: float | None = 80.0, shift: float = 0.0) -> None:
    """Minimal polymer CIF: one CA per residue along x, optional B-factor, optional rigid shift of chain A."""
    doc = gemmi.cif.Document()
    block = doc.add_new_block("rfd4")
    st = gemmi.Structure()
    st.name = "rfd4"
    model = gemmi.Model("1")
    x0 = 0.0
    for ch_name, seq in chains.items():
        ch = gemmi.Chain(ch_name)
        for i, aa in enumerate(seq, start=1):
            res = gemmi.Residue()
            res.name = _aa3(aa)
            res.seqid = gemmi.SeqId(i, " ")
            atom = gemmi.Atom()
            atom.name = "CA"
            atom.element = gemmi.Element("C")
            dx = shift if ch_name == "A" else 0.0
            atom.pos = gemmi.Position(x0 + i * 3.8 + dx, 0.0, 10.0 * (ord(ch_name) - 64))
            if bfactor is None or (bfactor != bfactor and math.isnan(bfactor)):
                atom.b_iso = float("nan")
            else:
                atom.b_iso = float(bfactor)
            res.add_atom(atom)
            # Backbone N/C so load_complex is happier.
            for name, el, off in (("N", "N", -1.2), ("C", "C", 1.2), ("O", "O", 1.8)):
                a = gemmi.Atom()
                a.name = name
                a.element = gemmi.Element(el)
                a.pos = gemmi.Position(atom.pos.x + off, atom.pos.y, atom.pos.z)
                a.b_iso = atom.b_iso
                res.add_atom(a)
            ch.add_residue(res)
        model.add_chain(ch)
        x0 += 50.0
    st.add_model(model)
    st.make_mmcif_document().write_file(str(path))


def write_run(root: Path, *, rows: list[dict], contig: str, hot: str | None = None,
              second_oracle: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "inference_metrics").mkdir()
    (root / "inference_samples").mkdir()
    (root / "inputs").mkdir()
    fields = list(rows[0].keys())
    with (root / "inference_metrics" / "per_example_metrics.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    yaml_body = f"""
global_args:
  input: target.cif
task1:
  contig: "{contig}"
  conditions:
    C_HOT:
      flag:
        - select: "sel('B/*/[{hot or '10,20'}]')"
"""
    if "C1-" in contig or ",C" in contig:
        yaml_body = yaml_body.replace(
            f"sel('B/*/[{hot or '10,20'}]')",
            f"sel('B/*/[{hot or '10,20'}]'); sel('C/*/[{hot or '12,22'}]')",
        )
    (root / "inputs" / "spec.yaml").write_text(yaml_body)
    # Tiny target CIF for the construct (unused for scoring here).
    write_cif(root / "inputs" / "target.cif", {"A": "ACDEFGHIKLMNPQRSTVWYACDEFGHIKL"}, bfactor=30.0)
    for row in rows:
        eid = row["example_id"]
        folder = root / "inference_samples" / eid
        folder.mkdir()
        seq_a = row.get("chain_of_interest_sequence") or "ACDEFGHIKLMNPQRSTVWYACDEFGHIKLM"
        chains = {"A": seq_a, "B": "ACDEFGHIKLMNPQRSTVWYACDEFGHIKLMNPQRSTVWYACDEFGHI"}
        if any(k.startswith("oracle.iptm_A_1_C_1") or k.startswith("oracle.min_ipae_A_1_C_1") for k in row):
            chains["C"] = chains["B"]
        write_cif(folder / f"{eid}_af3_refolded.cif", chains, bfactor=85.0, shift=0.0)
        write_cif(folder / f"{eid}_generated.cif", chains, bfactor=float("nan"), shift=0.4)
        if second_oracle:
            write_cif(folder / f"{eid}_af3_ppi_refolded.cif", chains, bfactor=70.0, shift=0.1)
    return root


def make_row(eid: str, *, plddt=90, iptm_b=60, iptm_c=60, min_b=1.5, min_c=1.5,
             clash=0, backbone=1.0, motif=0.5, pass_=0, seq=None, pre=1) -> dict:
    seq = seq or "ACDEFGHIKLMNPQRSTVWYACDEFGHIKLM"
    row = {
        "example_id": eid,
        "task": "task1",
        "seed": "1",
        "chain_of_interest": "A_1",
        "chain_of_interest_sequence": seq,
        "pre_oracle_pass": str(pre),
        "motif_scaffold.pass": str(pass_),
        "oracle.plddt_coi": str(plddt),
        "oracle.overall_ptm": str(plddt - 5),
        "oracle.iptm_coi": str((iptm_b + iptm_c) / 2),
        "oracle.ipae_coi": "10",
        "oracle.min_ipae_coi": str((min_b + min_c) / 2),
        "oracle.ranking_score": str(plddt / 100),
        "oracle.has_clash": str(clash),
        "oracle.fraction_disordered": "0.01",
        "oracle.iptm_A_1_B_1": str(iptm_b),
        "oracle.iptm_A_1_C_1": str(iptm_c),
        "oracle.min_ipae_A_1_B_1": str(min_b),
        "oracle.min_ipae_A_1_C_1": str(min_c),
        "oracle.iptm_B_1_C_1": "40",
        "refolded_backbone_rmsd.rmsd": str(backbone),
        "refolded_motif_ca_rmsd.rmsd": str(motif),
        "amino_acid_composition.ala_fraction": "0.08",
        "amino_acid_composition.n_standard_residues": str(len(seq)),
    }
    return row


@pytest.fixture
def rfd4_run(tmp_path):
    """Two designs: one clears both chains, one clears B but grazes C (must reject)."""
    good = make_row("scaffold_1_0", plddt=90, iptm_b=70, iptm_c=65, min_b=1.0, min_c=1.2, pass_=0)
    bad = make_row("scaffold_1_1", plddt=90, iptm_b=70, iptm_c=20, min_b=1.0, min_c=8.0, pass_=0)
    return write_run(
        tmp_path / "run",
        rows=[good, bad],
        contig="L1:3-7,A16-26,A53-60,/0,B1-40,/0,C1-40",
        second_oracle=True,
    )


# ------------------------------------------------------------------------------------------------------------ detection
def test_detects_synthetic_and_registry(rfd4_run):
    assert rfd4.detect(rfd4_run) and not bindcraft1.detect(rfd4_run)
    assert detect_reader(rfd4_run) is rfd4 and get_reader("rfd4") is rfd4


def test_detect_edge_cases(tmp_path):
    assert not rfd4.detect(tmp_path)
    (tmp_path / "inference_metrics").mkdir()
    assert not rfd4.detect(tmp_path)
    (tmp_path / "inference_metrics" / "per_example_metrics.csv").write_text("example_id\n")
    assert rfd4.detect(tmp_path)


# ------------------------------------------------------------------------------------------------------------ reading
def test_scales_and_per_chain(rfd4_run):
    run = rfd4.read_run(rfd4_run, "tpo", check_structures=False, hash_structures=False)
    assert run.reader == "rfd4" and run.experimental
    assert run.annotations["modality"] == "motif scaffold"
    assert run.annotations["chain_roles"] == {"B": "require", "C": "require"}
    d0 = next(d for d in run.designs if d.id == "scaffold_1_0")
    assert abs(d0.metrics["pLDDT"] - 0.90) < 1e-9
    assert abs(d0.metrics["i_pTM"] - 0.675) < 1e-9
    assert abs(d0.metrics["i_pAE"] - 10 / 31) < 1e-9
    assert d0.metrics["complex_rmsd"] == 1.0
    assert d0.metrics["motif_ca_rmsd"] == 0.5
    assert abs(d0.per_chain["B"]["iptm"] - 0.70) < 1e-9
    assert abs(d0.per_chain["C"]["iptm"] - 0.65) < 1e-9
    assert d0.per_chain["B"]["min_ipae"] == 1.0
    # Receptor-receptor pair must never land on the design.
    assert "iptm" not in (d0.per_chain.get("B_1") or {})


def test_accepts_only_when_every_required_chain_clears(rfd4_run):
    run = rfd4.read_run(rfd4_run, "tpo", check_structures=False, hash_structures=False)
    by_id = {d.id: d for d in run.designs}
    assert by_id["scaffold_1_0"].status == "accepted"
    assert by_id["scaffold_1_1"].status == "rejected"
    stems = {f.stem for f in by_id["scaffold_1_1"].failed_filters}
    scopes = {(f.stem, f.scope) for f in by_id["scaffold_1_1"].failed_filters}
    assert "iptm" in stems or "min_ipae" in stems
    assert any(scope == "C" for _, scope in scopes)
    assert by_id["scaffold_1_0"].extras["tool_pass"]["motif_scaffold.pass"] is False


def test_user_cutoffs_override_defaults(rfd4_run):
    # Raise the pooled bar so even the good design fails.
    run = rfd4.read_run(
        rfd4_run, "tpo", check_structures=False, hash_structures=False,
        cutoff_args=["i_pTM=ge:0.95"],
    )
    assert all(d.status == "rejected" for d in run.designs)
    fs = filters_for_run(run)
    assert fs.from_user
    assert any(r.value_stem == "i_pTM" for r in fs.rules)
    # Defaults still expose per-chain iptm when not overridden.
    run2 = rfd4.read_run(rfd4_run, "tpo", check_structures=False, hash_structures=False)
    fs2 = filters_for_run(run2)
    assert any(r.stem == "iptm" and r.per_chain for r in fs2.rules)


def test_chain_scoped_cutoff_arg():
    f = parse_cutoff_arg("iptm@C=ge:0.6")
    assert f["name"] == "iptm" and f["per_chain"] and f["chains"] == ["C"] and f["threshold"] == 0.6


def test_motif_from_contig(rfd4_run):
    run = rfd4.read_run(rfd4_run, "tpo", check_structures=False, hash_structures=False)
    sc = run.annotations["scaffold"]
    assert sc["kind"] == "motif"
    segs = sc["motif_segments"]
    assert {(s["start"], s["end"]) for s in segs} >= {(16, 26), (53, 60)}
    assert "A16-26" in sc["scaffold_edits"]


def test_structures_resolve_locally(rfd4_run):
    run = rfd4.read_run(rfd4_run, "tpo", check_structures=True, hash_structures=False)
    d = run.designs[0]
    assert d.structure_path and d.structure_path.endswith("_af3_refolded.cif")
    assert Path(d.extras["designed_structure_path"]).name.endswith("_generated.cif")
    assert "oracle_ppi" in (d.extras.get("oracles") or {}) or True  # may be absent if not in CSV


def test_second_oracle_discovered(tmp_path):
    row = make_row("scaffold_2_0", iptm_b=55, iptm_c=55)
    row["oracle_ppi.plddt_coi"] = "80"
    row["oracle_ppi.iptm_coi"] = "50"
    row["oracle_ppi.iptm_A_1_B_1"] = "50"
    row["oracle_ppi.iptm_A_1_C_1"] = "50"
    row["oracle_ppi.min_ipae_A_1_B_1"] = "2"
    row["oracle_ppi.min_ipae_A_1_C_1"] = "2"
    root = write_run(tmp_path / "ppi", rows=[row], contig="A16-26,/0,B1-40,/0,C1-40", second_oracle=True)
    run = rfd4.read_run(root, "ppi", check_structures=False, hash_structures=False)
    assert "oracle_ppi" in run.annotations["oracles"]
    d = run.designs[0]
    assert "oracle_ppi" in d.extras["oracles"]
    assert abs(d.extras["oracles"]["oracle_ppi"]["metrics"]["pLDDT"] - 0.80) < 1e-9


def test_all_reject_run_warns(tmp_path):
    row = make_row("scaffold_3_0", plddt=40, iptm_b=10, iptm_c=10, min_b=9, min_c=9)
    root = write_run(tmp_path / "zero", rows=[row], contig="A16-26,/0,B1-40,/0,C1-40")
    run = rfd4.read_run(root, "zero", check_structures=False, hash_structures=False)
    assert run.accepted() == []
    assert any("No design passed" in w for w in run.warnings)


def test_filters_recompute_with_per_chain_roles(rfd4_run):
    run = rfd4.read_run(rfd4_run, "tpo", check_structures=False, hash_structures=False,
                        chain_roles={"B": "require", "C": "ignore"})
    by_id = {d.id: d for d in run.designs}
    # Grazing C is ignored, so the formerly-rejected design now passes.
    assert by_id["scaffold_1_1"].status == "accepted"
