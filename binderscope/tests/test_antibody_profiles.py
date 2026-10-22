"""Antibody-style profiles (binder_review.antibody): CDR spans, the heuristic fallback against abnumber (when installed), hallmarks,
pair-aware cysteines, CDR share, CDR liabilities and scFv splitting.

The domains are built here from germline-like frameworks and invented CDRs; no sequence of a real antibody or of a real target is used.
The expected CDR strings are the IMGT definition applied to those frameworks (the fallback and abnumber agree on them)."""
from __future__ import annotations

import numpy as np
import pytest

from binder_review import antibody as A

needs_abnumber = pytest.mark.skipif(not A.HAVE_ABNUMBER, reason="abnumber/anarci not installed")
BACKENDS = ["fallback"] + (["abnumber"] if A.HAVE_ABNUMBER else [])

# framework 1 + CDR1 + framework 2 + CDR2 + framework 3 + CDR3 + framework 4 (the pieces are not the IMGT limits)
VH = ("EVQLVESGGGLVQPGGSLRLSCAAS" "GFTFSSYA" "MSWVRQAPGKGLEWVS" "ISGSGGST" "YYADSVKGRFTISRDNSKNTLYLQMNSLRAEDTAVYYC"
      "AKDRLGYSSGWFDY" "WGQGTLVTVSS")
VHH = ("QVQLQESGGGLVQAGGSLRLSCAAS" "GRTFSSYA" "MGWFRQAPGKEREFVA" "AISWSGGS" "TYYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYC"
       "AADLRGTYYSSDYEYDY" "WGQGTQVTVSS")
VK = ("DIQMTQSPSSLSASVGDRVTITC" "RASQSISSYLN" "WYQQKPGKAPKLLIY" "AASSLQS" "GVPSRFSGSGSGTDFTLTISSLQPEDFATYYC" "QQSYSTPLT" "FGGGTKVEIK")
VL = ("QSALTQPASVSGSPGQSITISC" "TGTSSDVGGYNYVS" "WYQQHPGKAPKLMIY" "DVSNRPS" "GVSNRFSGSKSGNTASLTISGLQAEDEADYYC" "SSYTSSSTRV" "FGGGTKLTVL")
LINKER = "GGGGSGGGGSGGGGS"
VHH_CYS = VHH.replace("GRTFSSYA", "GRTFSSCA").replace("AADLRGTYYSSDYEYDY", "AADLRGCYYSSDYEYDY")     # an extra Cys in CDR1 and CDR3

CDRS = {
    "VH": (VH, "H", ["GFTFSSYA", "SGSGGST", "AKDRLGYSSGWFDY"], {"CDR1": (26, 33), "CDR2": (51, 57), "CDR3": (96, 109)}),
    "VHH": (VHH, "H", ["GRTFSSYA", "ISWSGGST", "AADLRGTYYSSDYEYDY"], {"CDR1": (26, 33), "CDR2": (51, 58), "CDR3": (97, 113)}),
    "VK": (VK, "K", ["QSISSY", "AAS", "QQSYSTPLT"], {"CDR1": (27, 32), "CDR2": (50, 52), "CDR3": (89, 97)}),
    "VL": (VL, "L", ["SSDVGGYNY", "DVS", "SSYTSSSTRV"], {"CDR1": (26, 34), "CDR2": (52, 54), "CDR3": (91, 100)}),
}


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", list(CDRS))
def test_cdrs_follow_the_imgt_definition(name, backend):
    seq, chain, cdrs, spans = CDRS[name]
    num = A.number_chain(seq, backend)
    assert num["ok"] and num["chain_type"] == chain and [num["cdrs"][c] for c in A.CDR_NAMES] == cdrs
    assert {c: tuple(num["spans"][c]) for c in A.CDR_NAMES} == spans
    assert [num["regions"][i] for i in range(len(seq))].count("CDR3") == len(cdrs[2]) and num["backend"] == backend
    assert sum(1 for r in num["regions"] if r in ("FR1", "FR2", "FR3", "FR4")) > 70


@needs_abnumber
@pytest.mark.parametrize("seq", [VH, VHH, VL])
def test_fallback_reproduces_abnumber_spans(seq):
    a, b = A.number_chain(seq, "abnumber"), A.number_chain(seq, "fallback")
    assert b["ok"] and a["spans"] == b["spans"] and a["chain_type"] == b["chain_type"]


@needs_abnumber
def test_fallback_kappa_within_one_residue_of_abnumber():
    a, b = A.number_chain(VK, "abnumber"), A.number_chain(VK, "fallback")
    assert a["spans"] == b["spans"] and b["chain_type"] == "K"


def test_fallback_gives_up_on_non_antibody():
    rng = np.random.default_rng(3)
    junk = "".join(rng.choice(list("ACDEFGHIKLMNPQRSTVWY"), size=300))
    assert A.number_chain(junk, "fallback")["ok"] is False
    assert A.profile_sequence("AAAA")["ok"] is False and A.profile_sequence("")["ok"] is False


@pytest.mark.parametrize("backend", BACKENDS)
def test_leader_and_tag_are_labelled_tag_and_positions_shift(backend):
    seq = "TGQ" + VHH + "GSMDPGGSHHHHHHHH"
    num = A.number_chain(seq, backend)
    assert num["ok"] and num["regions"][:3] == [A.FLANK] * 3 and num["regions"][-8:] == [A.FLANK] * 8
    assert num["cdrs"]["CDR3"] == "AADLRGTYYSSDYEYDY" and num["spans"]["CDR1"][0] == 26 + 3


@pytest.mark.parametrize("backend", BACKENDS)
def test_hallmarks_vhh_versus_vh(backend):
    h = A.hallmarks(VHH, A.number_chain(VHH, backend))
    assert h["string"] == "FERF" and h["call"] == "VHH-like"
    h = A.hallmarks(VH, A.number_chain(VH, backend))
    assert h["string"] == "VGLW" and h["call"] == "VH-like"
    assert A.hallmarks(VL, A.number_chain(VL, backend))["call"] is None


@pytest.mark.parametrize("backend", BACKENDS)
def test_cysteines_extra_cdr_disulfide_is_not_free(backend):
    rep = A.cysteine_report(VHH_CYS, A.number_chain(VHH_CYS, backend))
    assert rep["status"] == "extra disulfide" and rep["free"] == []
    extra = [p for p in rep["pairs"] if not p["canonical"]][0]
    assert set(extra["regions"]) == {"CDR1", "CDR3"} and [p for p in rep["pairs"] if p["canonical"]][0]["regions"] == ("FR1", "FR3")


@pytest.mark.parametrize("backend", BACKENDS)
def test_cysteines_single_extra_is_free_and_missing_canonical_is_flagged(backend):
    seq = VHH.replace("AISWSGGS", "AISWCGGS")
    rep = A.cysteine_report(seq, A.number_chain(seq, backend))
    assert rep["status"] == "free cysteine" and len(rep["free"]) == 1
    no_c = VH.replace("SCAAS", "SSAAS")
    assert A.cysteine_report(no_c, A.number_chain(no_c, backend))["status"] == "canonical pair broken"
    assert A.cysteine_report(VH, A.number_chain(VH, backend))["status"] == "ok"


def test_cysteine_distance_overrides_parity():
    num = A.number_chain(VHH_CYS, "fallback")
    cys = [i + 1 for i, c in enumerate(VHH_CYS) if c == "C"]
    extra = [c for c in cys if c not in (num["imgt"][23], num["imgt"][104])]
    far = {extra[0]: (0, 0, 0), extra[1]: (9, 0, 0)}
    rep = A.cysteine_report(VHH_CYS, num, far)
    assert rep["status"] == "free cysteine" and sorted(rep["free"]) == extra
    rep = A.cysteine_report(VHH_CYS, num, {extra[0]: (0, 0, 0), extra[1]: (2.05, 0, 0)})
    assert rep["status"] == "extra disulfide" and [p for p in rep["pairs"] if not p["canonical"]][0]["source"] == "distance"


def test_cdr_interface_share_counts_regions_and_ignores_tags():
    num = A.number_chain("TGQ" + VHH, "fallback")
    reg = num["regions"]
    cdr3, fr = num["spans"]["CDR3"][0], num["spans"]["FR2"][0]
    res = A.cdr_interface_share(reg, [cdr3, cdr3 + 1, cdr3 + 2, fr, 1])
    assert res["cdr"] == 3 and res["framework"] == 1 and res["other"] == 1 and res["share"] == 0.75 and res["by_cdr"]["CDR3"] == 0.75
    assert A.cdr_interface_share(reg, [])["share"] is None and A.cdr_interface_share(reg, [10_000])["n"] == 0


def test_cdr_liabilities_escalate_inside_a_cdr_only():
    seq = VH.replace("GFTFSSYA", "GFNGSSYA")                   # NG inside CDR1
    liab = A.cdr_liabilities(seq, A.number_chain(seq, "fallback")["regions"])
    ng = [e for e in liab if e["kind"] == "Deamidation (NG)" and e["in_cdr"]]
    assert ng and ng[0]["sev"] == "bad" and ng[0]["region"] == "CDR1"
    fw = A.cdr_liabilities(VH, A.number_chain(VH, "fallback")["regions"])
    assert all(not e["in_cdr"] or e["sev"] != "bad" for e in fw)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("order", ["VH-VL", "VL-VH"])
def test_scfv_split_profile(backend, order):
    seq = VH + LINKER + VL if order == "VH-VL" else VL + LINKER + VH + "HHHHHH"
    p = A.profile_sequence(seq, contact_residues=range(26, 40), backend=backend)
    assert p["ok"] and p["modality"] == "scFv" and p["order"] == order and p["nH"] == len(VH) and p["chain_types"] == ["H", "L"]
    assert p["cdrs"]["H"] == CDRS["VH"][2] and p["cdrs"]["L"] == CDRS["VL"][2]
    assert len(p["regions"]) == len(seq) and p["regions"].count(A.FLANK) >= len(LINKER)
    assert p["cysteine_status"] == "ok" and p["interface"]["n"] == 14


@pytest.mark.parametrize("backend", BACKENDS)
def test_profile_single_domain_shape_matches_inspector_fields(backend):
    p = A.profile_sequence(VHH_CYS, contact_residues=[28, 30, 97, 99, 105, 60], backend=backend)
    assert p["ok"] and p["modality"] == "nanobody" and p["n"] == len(VHH_CYS) and len(p["regions"]) == len(VHH_CYS)
    assert sum(p["region_counts"].values()) == len(VHH_CYS) and p["region_counts"]["CDR3"] == 17
    assert p["cdrs"] == ["GRTFSSCA", "ISWSGGST", "AADLRGCYYSSDYEYDY"] and p["interface"]["cdr"] >= 4
    assert p["cysteine_status"] == "extra disulfide" and not [e for e in p["liabilities"] if e["kind"].startswith("Unpaired")]


def test_backend_name_reported_and_auto_fallback(monkeypatch):
    assert A.number_chain(VHH, "fallback")["backend"] == "fallback"
    if A.HAVE_ABNUMBER:
        assert A.number_chain(VHH)["backend"] == "abnumber"
    monkeypatch.setattr(A, "HAVE_ABNUMBER", False)
    num = A.number_chain(VHH)
    assert num["ok"] and num["backend"] == "fallback" and num["cdrs"]["CDR1"] == "GRTFSSYA"
    p = A.profile_sequence(VH + LINKER + VL)
    assert p["ok"] and p["backend"] == "fallback" and p["modality"] == "scFv"


def test_toy_binders_are_not_antibodies(toy_run1):
    """The generic profile of a toy helical bundle: the heuristic does not find a V domain."""
    assert all(A.profile_sequence(d.sequence)["ok"] is False for d in toy_run1.designs)
