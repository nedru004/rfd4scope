"""Paratope analysis of scaffolded campaigns (binder_review.paratope, and its use in families, export and the BindCraft 2 reader).

The fixtures are synthetic: a 127-residue VHH-like scaffold written to a file, designs built from it with the designable positions
of an edit spec written in the form BindCraft 2 records, and an interface placed region by region, so every number asserted here is
known by construction and not read back from the code under test. A real scaffolded campaign was also checked by hand against the
same rules (docs/paratope.md). Nothing here uses the network."""
from __future__ import annotations

import copy
import dataclasses
import json
import random
import shutil
from pathlib import Path
from types import SimpleNamespace

import gemmi
import pytest

from binder_review import export, families as F, paratope as P
from binder_review.model import DesignRecord, RunData, RunSettings, TargetConstruct
from binder_review.readers import bindcraft2
from toydata.testing import load_validator, repo_root

SC = ("EVQLLESGGGLVQPGGSLRLSCAASGEYANPASMSWVRQAPGKGLEAVSTIFSWPKRTYY"
      "ADSVKGRFTISRDNSKNTLYLQMNSLRAEDTAVYYCAASPIPMNAEEMIENPERAYWGQGTLVTVSS")
SPEC = "A27-33,A35*,A37*,A45*,A47*,A50*,A52-57(5-7),A59,A97-99*,A100-114(3-25),A115,A116"
AA = "ACDEFGHIKLMNPQRSTVWY"


# ------------------------------------------------------------------------------------------------ builders
THREE = {"A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE", "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS", "L": "LEU", "M": "MET",
         "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG", "S": "SER", "T": "THR", "V": "VAL", "W": "TRP", "Y": "TYR", "X": "UNK"}


def write_scaffold(path: Path, chains: dict[str, str]) -> Path:
    """A structure file with one CA per residue for each chain, numbered from 1 (enough for the module, which reads sequences)."""
    st = gemmi.Structure()
    model = gemmi.Model("1")
    for name, seq in chains.items():
        ch = gemmi.Chain(name)
        for i, letter in enumerate(seq, 1):
            res = gemmi.Residue()
            res.name = THREE[letter]
            res.seqid = gemmi.SeqId(i, " ")
            atom = gemmi.Atom()
            atom.name, atom.element, atom.pos = "CA", gemmi.Element("C"), gemmi.Position(float(i), 0.0, 0.0)
            res.add_atom(atom)
            ch.add_residue(res)
        model.add_chain(ch)
    st.add_model(model)
    st.setup_entities()
    path.parent.mkdir(parents=True, exist_ok=True)
    st.make_mmcif_document().write_file(str(path))
    return path


def oracle_region(p: int) -> str:
    """The region of VHH scaffold position p, written out by hand from the designed positions of SPEC."""
    if p in (37, 44, 45, 47):
        return "FR2-face"
    if 27 <= p <= 35:
        return "CDR1"
    if 50 <= p <= 59:
        return "CDR2"
    if 97 <= p <= 116:
        return "CDR3"
    return "FR1" if p < 27 else "FR2" if p < 50 else "FR3" if p < 97 else "FR4"


def make_design(rng: random.Random, l2: int, l3: int) -> tuple[str, list[tuple[str, int | None]]]:
    """A design of the scaffold: (sequence, per residue (region, scaffold position or None inside a variable stretch))."""
    seq: list[str] = []
    info: list[tuple[str, int | None]] = []

    def keep(a: int, b: int) -> None:
        for p in range(a, b + 1):
            seq.append(SC[p - 1])
            info.append((oracle_region(p), p))

    def design(a: int, b: int) -> None:
        for p in range(a, b + 1):
            seq.append(rng.choice([x for x in AA if x != SC[p - 1]]))
            info.append((oracle_region(p), p))

    def var(n: int, region: str) -> None:
        for _ in range(n):
            seq.append(rng.choice(AA))
            info.append((region, None))

    keep(1, 26); design(27, 33); keep(34, 34); design(35, 35); keep(36, 36); design(37, 37); keep(38, 44); design(45, 45); keep(46, 46)
    design(47, 47); keep(48, 49); design(50, 50); keep(51, 51); var(l2, "CDR2"); keep(58, 58); design(59, 59); keep(60, 96)
    design(97, 99); var(l3, "CDR3"); design(115, 116); keep(117, 127)
    return "".join(seq), info


def pick(info: list[tuple[str, int | None]], region: str, k: int, rng: random.Random, *, position: int | None = None) -> list[int]:
    """1-based design indices of k residues of a region (or of one scaffold position)."""
    idx = [i for i, (r, p) in enumerate(info, 1) if (r == region if position is None else p == position)]
    return rng.sample(idx, k)


def to_cell(idx: list[int], chain: str = "B") -> list[str]:
    return [f"{chain}{i}" for i in sorted(idx)]


def design_record(did: str, seq: str, interface: list[str], status: str, backbone: str, extras: dict | None = None) -> DesignRecord:
    return DesignRecord(id=did, run_id="r1", backbone_id=backbone, status=status, binder_name="c", length=len(seq), seed=0, mpnn_index=None,
                        sequence=seq, rank=None, interface_residues=interface, extras=extras or {})


def make_run(root: Path, designs: list[DesignRecord], *, modality: str = "VHH", spec: str = SPEC, scaffold: Path | None = None,
             threshold: float | None = 1.0, mandatory: bool = True, higher: bool = True) -> RunData:
    camp = root / "campaign"
    camp.mkdir(parents=True, exist_ok=True)
    ann = {"modality": modality}
    if spec is not None:
        ann["scaffold"] = {"scaffold": str(scaffold) if scaffold else None, "scaffold_edits": spec, "threshold": threshold, "higher": higher,
                           "mandatory": mandatory, "source": "settings.filters.Scaffold_Sequence_Retained_Fraction.params"}
    return RunData(run_id="r1", label="r1", reader="bindcraft2", source_dir=str(camp), settings=RunSettings("r1", "r1"),
                   target=TargetConstruct("r1", uniprot_offset=0), designs=designs, annotations=ann)


@pytest.fixture(scope="module")
def vhh_file(tmp_path_factory) -> Path:
    return write_scaffold(tmp_path_factory.mktemp("scaffold") / "VHH.cif", {"A": SC})


@pytest.fixture(scope="module")
def vhh_campaign(tmp_path_factory, vhh_file):
    """Eight accepted designs in four families and twelve rejected ones, with interfaces placed region by region.

    What was put where (the truth the analysis is checked against):
      accepted  every design touches CDR1, CDR2 and CDR3; design 0 also touches framework 3 position 74 and the face position 47;
      rejected  designs 0-5 touch CDR3 only, 6-8 touch framework only, 9-11 touch all three CDRs
    """
    rng = random.Random(7)
    root = tmp_path_factory.mktemp("vhh")
    designs: list[DesignRecord] = []
    truth: dict = {"accepted": {}, "rejected": {}}
    lengths = [(5, 10), (6, 10), (6, 12), (7, 20)]          # (CDR2, CDR3) per backbone: a family has one backbone
    for k in range(8):
        l2, l3 = lengths[k // 2]
        seq, info = make_design(rng, l2, l3)
        idx = pick(info, "CDR1", 3, rng) + pick(info, "CDR2", 2, rng) + pick(info, "CDR3", 4, rng)
        if k == 0:
            idx += pick(info, "FR3", 1, rng, position=74) + pick(info, "FR2-face", 1, rng, position=47)
        did = f"r1_vhh_l{len(seq)}_{k // 2:016x}_candidate{k}"
        designs.append(design_record(did, seq, to_cell(idx), "accepted", f"bb{k // 2}"))
        truth["accepted"][did] = {"l2": l2, "l3": l3, "n": len(idx)}
    for k in range(12):
        l2, l3 = 6, 14
        seq, info = make_design(rng, l2, l3)
        if k < 6:
            idx = pick(info, "CDR3", 5, rng)
        elif k < 9:
            idx = pick(info, "FR3", 3, rng) + pick(info, "FR1", 2, rng)
        else:
            idx = pick(info, "CDR1", 2, rng) + pick(info, "CDR2", 2, rng) + pick(info, "CDR3", 2, rng)
        did = f"r1_vhh_l{len(seq)}_{100 + k:016x}_candidate1"
        designs.append(design_record(did, seq, to_cell(idx), "rejected", f"rb{k}"))
        truth["rejected"][did] = {"n": len(idx)}
    run = make_run(root, designs, scaffold=vhh_file)
    return SimpleNamespace(run=run, truth=truth, root=root)


@pytest.fixture(scope="module")
def layout(vhh_file):
    return P.build_layout(P.parse_edits(SPEC), P.read_scaffold_file(vhh_file), "VHH", "VHH.cif")


@pytest.fixture(scope="module")
def result(vhh_campaign):
    res, why = P.analyse_run(vhh_campaign.run)
    assert why is None
    return res


# ------------------------------------------------------------------------------------------------ the spec
class TestSpec:
    def test_the_spec_parses(self):
        edits = P.parse_edits(SPEC)
        assert len(edits) == 12
        var = [e for e in edits if e.variable]
        assert [(e.start, e.end, e.lo, e.hi) for e in var] == [(52, 57, 5, 7), (100, 114, 3, 25)]
        assert (edits[0].chain, edits[0].start, edits[0].end) == ("A", 27, 33)

    def test_whitespace_and_line_breaks_are_ignored(self):
        assert P.parse_edits("A27-33,A35*,\n  A37*") == P.parse_edits("A27-33,A35*,A37*")

    @pytest.mark.parametrize("bad", ["", "   ", "A27-", "27-33", "A27-33(5)", "A27-33(7-5)", "A33-27", "A27-33,A30-35", "A12,B", "A1;A2", None])
    def test_a_token_that_does_not_parse_is_an_error_never_a_guess(self, bad):
        with pytest.raises(P.ParatopeError):
            P.parse_edits(bad)

    def test_the_reason_is_readable(self):
        with pytest.raises(P.ParatopeError, match="A27-"):
            P.parse_edits("A27-33,A27-")


# ------------------------------------------------------------------------------------------------ layout and alignment
class TestLayout:
    def test_segments_are_the_specs_designed_stretches(self, layout):
        got = [(s.name, s.first, s.last, s.designed, s.length) for s in layout.segments]
        assert got == [("CDR1", 27, 35, [(27, 33), (35, 35)], (9, 9)),
                       ("CDR2", 50, 59, [(50, 50), (52, 57), (59, 59)], (5, 7)),
                       ("CDR3", 97, 116, [(97, 99), (100, 114), (115, 116)], (3, 25))]

    def test_every_scaffold_position_has_the_region_written_out_by_hand(self, layout):
        for p in range(1, 128):
            assert layout.region_of[("A", p)] == oracle_region(p), p

    def test_the_face_is_set_apart_and_only_its_designed_positions_are_listed(self, layout):
        assert layout.naming == "VHH" and layout.face == (37, 44, 45, 47)
        assert layout.framework_designed == [("A", 37), ("A", 45), ("A", 47)]

    def test_the_scaffold_edge_residue_26_is_framework_because_the_spec_does_not_design_it(self, layout):
        # the campaign's own boundary, not the usual CDR-H1 convention (26-35): docs/paratope.md
        assert layout.region_of[("A", 26)] == "FR1" and layout.region_of[("A", 34)] == "CDR1"

    def test_a_scaffold_without_the_vhh_landmarks_reports_no_face(self, tmp_path):
        odd = SC[:35] + "A" + SC[36:]
        lay = P.build_layout(P.parse_edits(SPEC), P.read_scaffold_file(write_scaffold(tmp_path / "odd.cif", {"A": odd})), "VHH")
        assert lay.face == () and any("framework-2 face" in n for n in lay.notes)

    def test_a_vhh_with_other_than_three_segments_is_numbered_not_named(self, vhh_file):
        three = P.build_layout(P.parse_edits("A27-33,A50-55,A97-116"), P.read_scaffold_file(vhh_file), "VHH")
        assert three.naming == "VHH" and [s.name for s in three.segments] == ["CDR1", "CDR2", "CDR3"]
        two = P.build_layout(P.parse_edits("A27-33,A97-116"), P.read_scaffold_file(vhh_file), "VHH")
        assert two.naming == "generic" and [s.name for s in two.segments] == ["designed segment 1", "designed segment 2"]
        assert any("CDR1 to CDR3" in n for n in two.notes)

    def test_an_edit_outside_the_scaffold_is_an_error(self, vhh_file):
        with pytest.raises(P.ParatopeError, match="outside the scaffold"):
            P.build_layout(P.parse_edits("A27-33,A120-140"), P.read_scaffold_file(vhh_file), "VHH")
        with pytest.raises(P.ParatopeError, match="chain B"):
            P.build_layout(P.parse_edits("B3-5"), P.read_scaffold_file(vhh_file), "VHH")


class TestAlignment:
    @pytest.mark.parametrize("l2", [5, 6, 7])
    @pytest.mark.parametrize("l3", [3, 4, 15, 16, 25])
    def test_every_length_combination_aligns_and_recovers_the_lengths_and_regions(self, layout, l2, l3):
        rng = random.Random(l2 * 100 + l3)
        seq, info = make_design(rng, l2, l3)
        everything = [f"B{i}" for i in range(1, len(seq) + 1)]
        d = P.place_design(layout, "x", seq, everything, {"B": 0})
        assert d.lengths == {"CDR2": l2, "CDR3": l3}
        want: dict[str, int] = {}
        for region, _p in info:
            want[region] = want.get(region, 0) + 1
        assert d.regions == {k: want[k] for k in d.regions} and sum(d.regions.values()) == len(seq)
        assert d.loops == ["CDR1", "CDR2", "CDR3"]

    def test_a_mutated_invariant_stretch_is_unaligned_with_a_reason(self, layout):
        seq, _ = make_design(random.Random(1), 6, 10)
        bad = seq[:70] + ("A" if seq[70] != "A" else "C") + seq[71:]          # inside framework 3
        with pytest.raises(P.ParatopeError, match="invariant"):
            P.place_design(layout, "x", bad, [], {"B": 0})

    def test_a_length_outside_what_the_spec_allows_is_unaligned(self, layout):
        seq, _ = make_design(random.Random(1), 6, 10)
        with pytest.raises(P.ParatopeError, match="outside"):
            P.place_design(layout, "x", seq + "G" * 30, [], {"B": 0})

    def test_an_ambiguous_placement_is_unaligned_not_guessed(self):
        # two adjacent variable stretches with nothing invariant between them: lengths 1+2 and 2+1 both fit a design of seven residues
        lay = P.build_layout(P.parse_edits("A3(1-2),A4(1-2)"), {"A": dict(enumerate("GSTTAG", 1))}, "other")
        with pytest.raises(P.ParatopeError, match="more than one"):
            P.place_design(lay, "x", "GSXXXAG", [], {"B": 0})

    def test_an_interface_residue_outside_the_sequence_is_an_error(self, layout):
        seq, _ = make_design(random.Random(1), 6, 10)
        with pytest.raises(P.ParatopeError, match="outside the sequence"):
            P.place_design(layout, "x", seq, [f"B{len(seq) + 1}"], {"B": 0})
        with pytest.raises(P.ParatopeError, match="does not name"):
            P.place_design(layout, "x", seq, ["Bx"], {"B": 0})


# ------------------------------------------------------------------------------------------------ the run
class TestRunAnalysis:
    def test_everything_aligns_and_nothing_is_dropped(self, result, vhh_campaign):
        assert len(result.designs) == 20 and result.unaligned == {}
        blk = result.block("accepted")
        assert (blk["aligned"], blk["unaligned"], blk["n"], blk["group"]) == (20, 0, 8, "accepted")

    def test_accepted_shares_are_of_interface_residues_and_counted_by_hand(self, result):
        blk = result.block("accepted")
        # 8 designs x (3 + 2 + 4) interface residues in segments, plus the two framework residues of design 0: 74 in all
        assert blk["share"] == {"CDR3": 43.2, "CDR1": 32.4, "CDR2": 21.6, "FR2-face": 1.4, "FR3": 1.4}
        assert list(blk["share"]) == ["CDR3", "CDR1", "CDR2", "FR2-face", "FR3"]
        assert blk["framework_share"] == 2.7
        assert blk["loop_use"] == [{"loops": ["CDR1", "CDR2", "CDR3"], "n": 8, "share": 100.0}]
        assert sorted((p["region"], p["scaffold"], p["aa"], p["n"]) for p in blk["framework_positions"]) == \
            [("FR2-face", 47, SC[46], 1), ("FR3", 74, SC[73], 1)]
        assert blk["framework_designed"] == [37, 45, 47]

    def test_rejected_designs_are_a_block_of_their_own_with_the_contrast(self, result):
        acc, rej = result.block("accepted"), result.block("rejected")
        assert (rej["group"], rej["n"]) == ("rejected", 12)
        assert rej["framework_share"] > acc["framework_share"]
        uses = {tuple(u["loops"]): u["n"] for u in rej["loop_use"]}
        assert uses == {("CDR3",): 6, (): 3, ("CDR1", "CDR2", "CDR3"): 3}
        assert rej["framework_share"] == 23.8                      # 15 of 63 interface residues

        assert next(u for u in rej["loop_use"] if u["loops"] == [])["share"] == 25.0

    def test_per_design_blocks(self, result, vhh_campaign):
        blobs = result.design_blobs()
        assert set(blobs) == {d.id for d in vhh_campaign.run.designs}
        did = next(iter(vhh_campaign.truth["accepted"]))              # design 0: the one with framework contacts
        b = blobs[did]
        assert b["loops"] == ["CDR1", "CDR2", "CDR3"] and b["framework"] == 2
        assert (b["segments"]["CDR1"], b["segments"]["CDR2"], b["segments"]["CDR3"]) == (3, 2, 4)
        assert b["lengths"] == {"CDR2": vhh_campaign.truth["accepted"][did]["l2"], "CDR3": vhh_campaign.truth["accepted"][did]["l3"]}
        assert set(b["face"]) == {"37", "45", "47"} and set(b["face_mutated"]) <= {"37", "45", "47"}
        assert sorted(b["framework_at"]) == [["FR2-face", 47], ["FR3", 74]]
        other = blobs[list(vhh_campaign.truth["accepted"])[1]]
        assert other["framework"] == 0 and "framework_at" not in other

    def test_the_face_is_read_from_the_design_and_compared_with_the_scaffold(self, vhh_file, tmp_path):
        rng = random.Random(3)
        seq, info = make_design(rng, 6, 10)
        lst = list(seq)
        lst[36], lst[44], lst[46] = "Y", SC[44], "K"              # positions 37 and 47 mutated, 45 kept
        seq = "".join(lst)
        run = make_run(tmp_path, [design_record("d1", seq, ["B1"], "accepted", "bb")], scaffold=vhh_file)
        res, why = P.analyse_run(run)
        b = res.design_blobs()["d1"]
        assert b["face"] == {"37": "Y", "45": SC[44], "47": "K"} and b["face_mutated"] == ["37", "47"]

    def test_unaligned_designs_are_reported_and_left_out(self, vhh_campaign, vhh_file, tmp_path):
        designs = list(vhh_campaign.run.designs)
        broken = dataclasses.replace(designs[0], id="broken", sequence=designs[0].sequence[:80] + "WWWW" + designs[0].sequence[84:])
        run = make_run(tmp_path, [*designs, broken], scaffold=vhh_file)
        res, why = P.analyse_run(run)
        assert why is None and list(res.unaligned) == ["broken"] and "broken" not in res.designs
        blk = res.block("accepted")
        assert (blk["aligned"], blk["unaligned"]) == (20, 1)
        assert blk["unaligned_designs"][0]["id"] == "broken" and "invariant" in blk["unaligned_designs"][0]["why"]
        assert blk["n"] == 8

    def test_a_design_without_interface_residues_counts_as_a_design_with_no_loops(self, vhh_file, tmp_path):
        seq, _ = make_design(random.Random(5), 6, 10)
        res, _ = P.analyse_run(make_run(tmp_path, [design_record("d", seq, [], "accepted", "b")], scaffold=vhh_file))
        blk = res.block("accepted")
        assert blk["share"] == {} and blk["loop_use"] == [{"loops": [], "n": 1, "share": 100.0}] and blk["no_interface"] == 1

    def test_no_rejected_designs_means_no_rejected_block(self, vhh_file, tmp_path):
        seq, info = make_design(random.Random(5), 6, 10)
        res, _ = P.analyse_run(make_run(tmp_path, [design_record("d", seq, ["B1"], "accepted", "b")], scaffold=vhh_file))
        assert res.block("rejected") is None


# ------------------------------------------------------------------------------------------------ off, and never guessed
class TestOff:
    def test_a_run_that_is_not_scaffolded_has_no_analysis_and_no_reason(self, tmp_path):
        run = make_run(tmp_path, [], spec=None)
        assert not P.has_scaffold(run) and P.analyse_run(run) == (None, None)

    def test_a_spec_that_will_not_parse_switches_the_analysis_off_with_the_reason(self, vhh_file, tmp_path):
        run = make_run(tmp_path, [], spec="A27-33,Q#", scaffold=vhh_file)
        res, why = P.analyse_run(run)
        assert P.has_scaffold(run) and res is None and "Q#" in why

    def test_a_scaffold_file_that_is_not_here_is_a_reason_not_a_crash(self, tmp_path):
        seq, _ = make_design(random.Random(1), 6, 10)
        run = make_run(tmp_path, [design_record("d", seq, [], "accepted", "b")], scaffold=tmp_path / "nowhere" / "VHH.cif")
        res, why = P.analyse_run(run)
        assert res is None and "VHH.cif" in why and "not on this machine" in why

    def test_the_scaffold_is_found_next_to_the_campaign_when_the_recorded_path_is_gone(self, vhh_file, tmp_path):
        seq, info = make_design(random.Random(1), 6, 10)
        run = make_run(tmp_path, [design_record("d", seq, ["B1"], "accepted", "b")], scaffold=Path("/cluster/scaffolds/VHH.cif"))
        shutil.copy(vhh_file, Path(run.source_dir).parent / "VHH.cif")
        res, why = P.analyse_run(run)
        assert why is None and res is not None

    @pytest.mark.parametrize("kw", [dict(threshold=0.9), dict(mandatory=False), dict(threshold=None), dict(higher=False)])
    def test_without_the_mandatory_retained_fraction_of_1_the_invariant_stretches_are_not_exact_anchors(self, vhh_file, tmp_path, kw):
        res, why = P.analyse_run(make_run(tmp_path, [], scaffold=vhh_file, **kw))
        assert res is None and "Scaffold_Sequence_Retained_Fraction" in why

    def test_a_run_where_nothing_aligns_is_off_not_empty(self, vhh_file, tmp_path):
        run = make_run(tmp_path, [design_record("d", "M" * 120, [], "accepted", "b")], scaffold=vhh_file)
        res, why = P.analyse_run(run)
        assert res is None and "no design aligns" in why

    def test_a_damaged_scaffold_file_is_a_reason(self, tmp_path):
        bad = tmp_path / "VHH.cif"
        bad.write_text("this is not a structure")
        res, why = P.analyse_run(make_run(tmp_path, [design_record("d", "M" * 120, [], "accepted", "b")], scaffold=bad))
        assert res is None and "scaffold" in why


# ------------------------------------------------------------------------------------------------ naming by modality
class TestNaming:
    def test_an_ankyrin_repeat_scaffold_gets_numbered_segments_and_no_cdr_language(self, tmp_path):
        arp = "DLGKKLLEAARAGQDDEVRILMANGADVNA" * 3
        f = write_scaffold(tmp_path / "ARP.cif", {"A": arp})
        spec = "A3-6*,A8*,A33-36*,A38*,A63-66*"
        lay = P.build_layout(P.parse_edits(spec), P.read_scaffold_file(f), "ARP", "ARP.cif")
        assert lay.naming == "generic"
        assert [s.name for s in lay.segments] == ["designed segment 1", "designed segment 2", "designed segment 3"]
        assert set(lay.region_of.values()) == {"framework", "designed segment 1", "designed segment 2", "designed segment 3"}
        assert not any("CDR" in r or "FR" in r for r in lay.region_order)
        # designs of a fixed-length scaffold: every length is fixed, and the block says so
        seq = arp[:2] + "WWWW" + arp[6:]
        run = make_run(tmp_path, [design_record("d", seq, ["B3", "B4", "B20"], "accepted", "b")], modality="ARP", spec=spec, scaffold=f)
        res, why = P.analyse_run(run)
        assert why is None
        blk = res.block("accepted")
        assert blk["naming"] == "generic" and blk["share"] == {"designed segment 1": 66.7, "framework": 33.3} and "lengths" not in res.design_blobs()["d"]
        assert all("CDR" not in k for k in blk["share"])

    def test_vhh_naming_is_for_the_vhh_modality_only(self, vhh_file):
        lay = P.build_layout(P.parse_edits(SPEC), P.read_scaffold_file(vhh_file), "de novo", "VHH.cif")
        assert lay.naming == "generic" and lay.face == ()

    SCFV_H = "A" * 10 + "XXX" + "G" * 7 + "YYY" + "S" * 7 + "QQQ" + "WGQGTLV"          # H1 11-13, H2 21-23, H3 31-33, J 34-40
    SCFV_L = "T" * 10 + "XXX" + "N" * 7 + "YYY" + "K" * 7 + "QQQ" + "FGGGTKV"          # L1 56-58, L2 66-68, L3 76-78, J 79-85

    def _scfv(self, tmp_path):
        seq = self.SCFV_H + "GGGGS" + self.SCFV_L
        return seq, write_scaffold(tmp_path / "scFv.cif", {"A": seq})

    def test_an_scfv_gets_h1_to_l3_and_per_domain_framework(self, tmp_path):
        seq, f = self._scfv(tmp_path)
        spec = "A11-13*,A21-23*,A31-33*,A56-58*,A66-68*,A76-78*"
        lay = P.build_layout(P.parse_edits(spec), P.read_scaffold_file(f), "scFv", "scFv.cif")
        assert lay.naming == "antibody" and [s.name for s in lay.segments] == ["H1", "H2", "H3", "L1", "L2", "L3"]
        regions = {p: lay.region_of[("A", p)] for p in range(1, len(seq) + 1)}
        assert regions[1] == "H-FR1" and regions[15] == "H-FR2" and regions[25] == "H-FR3"
        assert regions[36] == "inter-domain" and regions[43] == "inter-domain" and regions[60] == "L-FR2" and regions[70] == "L-FR3"
        assert regions[80] == "L-FR4" and regions[12] == "H1" and regions[77] == "L3"
        assert not any(r.startswith("CDR") for r in regions.values())

    def test_an_scfv_whose_domains_cannot_be_told_apart_is_numbered(self, tmp_path):
        seq = "A" * 10 + "XXX" + "G" * 7 + "YYY" + "S" * 7 + "QQQ" + "PPPPPPP"
        f = write_scaffold(tmp_path / "x.cif", {"A": seq + seq})
        lay = P.build_layout(P.parse_edits("A11-13*,A21-23*,A31-33*,A51-53*,A61-63*,A71-73*"), P.read_scaffold_file(f), "scFv")
        assert lay.naming == "generic" and any("heavy and light" in n for n in lay.notes)

    def test_a_fab_with_two_chains_is_read_chain_by_chain(self, tmp_path):
        f = write_scaffold(tmp_path / "Fab.cif", {"A": self.SCFV_H, "B": self.SCFV_L})
        spec = "A11-13*,A21-23*,A31-33*,B11-13*,B21-23*,B31-33*"
        edits = P.parse_edits(spec)
        lay = P.build_layout(edits, P.read_scaffold_file(f), "Fab", "Fab.cif")
        assert lay.naming == "antibody" and lay.chains == ["A", "B"] and [s.name for s in lay.segments] == ["H1", "H2", "H3", "L1", "L2", "L3"]
        light = self.SCFV_L
        h = list(self.SCFV_H)
        h[11] = "R"                                             # H1 residue 12, designed
        heavy_seq = "".join(h)
        run = make_run(tmp_path, [design_record("d", heavy_seq + "/" + light, ["B12", "B13", "C22", "C26"], "accepted", "b")],
                       modality="Fab", spec=spec, scaffold=f)
        res, why = P.analyse_run(run)
        assert why is None, why
        assert res.designs["d"].regions == {"H1": 2, "L2": 1, "L-FR3": 1} and res.designs["d"].loops == ["H1", "L2"]
        assert res.block("accepted")["chains"] == ["A", "B"] and any("Several binder chains" in n for n in res.block("accepted")["notes"])


# ------------------------------------------------------------------------------------------------ the reader surfaces the spec
class TestReader:
    META_FILTER = {"higher": True, "mandatory": True, "threshold": 1.0,
                   "params": {"scaffold": "/cluster/scaffolds/VHH.cif", "scaffold_edits": SPEC}}

    def test_the_spec_is_surfaced_on_the_run(self, toy_dir, tmp_path):
        camp = tmp_path / "toyc"
        shutil.copytree(toy_dir / "bindcraft2_v103" / "toyc", camp)
        meta = json.loads((camp / "campaign_metadata.json").read_text())
        meta["settings"]["filters"]["Scaffold_Sequence_Retained_Fraction"] = self.META_FILTER
        (camp / "campaign_metadata.json").write_text(json.dumps(meta))
        run = bindcraft2.read_run(camp, "toyc")
        assert run.annotations["scaffold"] == {
            "scaffold": "/cluster/scaffolds/VHH.cif", "scaffold_edits": SPEC, "threshold": 1.0, "higher": True, "mandatory": True,
            "source": "settings.filters.Scaffold_Sequence_Retained_Fraction.params"}
        assert P.has_scaffold(run)
        # the filter is still read as a rule: its params did not replace anything
        assert any(f["name"] == "Scaffold_Sequence_Retained_Fraction" and f["threshold"] == 1.0 for f in run.annotations["filters"])

    def test_a_campaign_without_the_filter_has_no_scaffold_annotation(self, toy_dir):
        run = bindcraft2.read_run(toy_dir / "bindcraft2_v103" / "toyc", "toyc")
        assert "scaffold" not in run.annotations and not P.has_scaffold(run)

    @pytest.mark.parametrize("params", [{}, {"scaffold": "x.cif"}, {"scaffold_edits": ""}, None])
    def test_a_filter_without_an_edit_spec_is_not_a_scaffolded_run(self, params):
        assert bindcraft2._scaffold_spec({"settings": {"filters": {"Scaffold_Sequence_Retained_Fraction": {"threshold": 1.0, "params": params}}}}) is None

    def test_the_filter_name_is_matched_without_regard_to_case_and_a_missing_mandatory_flag_means_mandatory(self):
        got = bindcraft2._scaffold_spec({"settings": {"filters": {"scaffold_sequence_retained_fraction": {
            "threshold": 1.0, "higher": True, "params": {"scaffold_edits": "A1-3*"}}}}})
        assert got["mandatory"] is True and got["scaffold"] is None and got["scaffold_edits"] == "A1-3*"


# ------------------------------------------------------------------------------------------------ the diversity axes
class TestDiversity:
    def test_loop_length_classes_of_a_spread_with_two_clusters(self):
        got = F.loop_length_classes([9, 9, 11, 12, 12, 21, 24])
        assert got == [[9, 9, 11, 12, 12], [21, 24]]

    def test_class_gap_is_inclusive_and_a_chain_of_lengths_is_one_class(self):
        assert F.loop_length_classes([10, 13]) == [[10, 13]] and F.loop_length_classes([10, 14]) == [[10], [14]]
        assert F.loop_length_classes([10, 13, 16, 19]) == [[10, 13, 16, 19]] and F.loop_length_classes([]) == []

    def test_the_axes_of_a_scaffolded_run(self, result, vhh_campaign):
        fams = {f.id: f.accepted for f in F.build_families(vhh_campaign.run)}
        epi = {"status": "observed", "n_families": 4}
        dp = F.diversity_paratope(result, fams, epi)
        assert dp["fold"] == {"status": "off", "why": F.FOLD_OFF_WHY}
        assert dp["paratope"]["status"] == "observed" and dp["paratope"]["profiles"] == 1 and dp["paratope"]["n"] == 8
        assert dp["paratope"]["n_families"] == 4 and dp["paratope"]["profile_list"] == [["CDR1", "CDR2", "CDR3"]]
        ll = dp["loop_length"]
        assert (ll["status"], ll["segment"], ll["values"], ll["distinct"]) == ("observed", "CDR3", [10, 10, 12, 20], 3)
        assert ll["classes"] == 2 and ll["class_ranges"] == [[10, 12], [20, 20]] and ll["class_families"] == [3, 1] and ll["class_gap"] == F.LOOP_CLASS_GAP
        assert dp["epitope"] is epi and dp["low_confidence"] is True and "too few to call a trend" in dp["note"]

    def test_a_spec_that_fixes_every_length_has_no_loop_length_axis(self, tmp_path):
        arp = "DLGKKLLEAARAGQDDEVRILMANGADVNA" * 3
        f = write_scaffold(tmp_path / "ARP.cif", {"A": arp})
        run = make_run(tmp_path, [design_record("a", arp, ["B3"], "accepted", "bb1"), design_record("b", arp, ["B4"], "accepted", "bb2")],
                       modality="ARP", spec="A3-6*,A33-36*", scaffold=f)
        res, why = P.analyse_run(run)
        dp = F.diversity_paratope(res, {"bb1": ["a"], "bb2": ["b"]}, None)
        assert dp["loop_length"]["status"] == "off" and "fixes the length" in dp["loop_length"]["why"]
        assert dp["epitope"]["status"] == "unknown" and dp["paratope"]["status"] == "observed"

    def test_siblings_that_disagree_on_length_make_the_axis_unknown(self, result, vhh_campaign):
        ids = [d.id for d in vhh_campaign.run.accepted()]
        fams = {"one": ids[:4], "two": ids[4:]}                  # families that straddle backbones of different lengths
        ll = F.diversity_paratope(result, fams, None)["loop_length"]
        assert ll["status"] == "unknown" and "differ" in ll["why"]

    def test_one_family_cannot_have_a_spread(self, result, vhh_campaign):
        ids = [d.id for d in vhh_campaign.run.accepted()]
        ll = F.diversity_paratope(result, {"only": ids[:2]}, None)["loop_length"]
        assert ll["status"] == "unknown" and "fewer than two" in ll["why"]


class TestEpitope:
    def _run(self, tmp_path, cells):
        designs = []
        for i, (bb, cell) in enumerate(cells):
            designs.append(design_record(f"d{i}", "AAAA", [], "accepted", bb, {"Interface_Target_Residues": cell}))
        return make_run(tmp_path, designs, spec=None)

    def test_pairwise_jaccard_and_the_residues_every_family_touches(self, tmp_path):
        run = self._run(tmp_path, [("a", "A1,L2,K3,P4"), ("a", "A1,L2,K3,P5"), ("b", "A1,L2,Y9"), ("c", "A1,K3,Y9,G10")])
        e = F.epitope_diversity(run, F.build_families(run))
        # a = {1,2,3,4,5}, b = {1,2,9}, c = {1,3,9,10}: J(a,b)=2/6, J(a,c)=2/7, J(b,c)=2/5
        assert e["status"] == "observed" and e["n_families"] == 3
        assert e["pairwise_jaccard"] == [round(2 / 7, 2), 0.4] and e["shared_by_all"] == [1]
        assert e["sizes"] == {"a": 5, "b": 3, "c": 4} and e["source"] == "BindCraft Interface_Target_Residues" and e["numbering"] == "uniprot"
        assert e["low_confidence"] is True and "too few to call a trend" in e["note"]

    def test_the_epitope_is_in_reference_numbering(self, tmp_path):
        run = self._run(tmp_path, [("a", "A1,L2"), ("b", "A1,L5")])
        run = dataclasses.replace(run, target=dataclasses.replace(run.target, uniprot_offset=100))
        e = F.epitope_diversity(run, F.build_families(run))
        assert e["shared_by_all"] == [101] and e["numbering"] == "uniprot"

    def test_siblings_count_once_a_family_is_one_epitope(self, tmp_path):
        run = self._run(tmp_path, [("a", "A1"), ("a", "A2"), ("a", "A3"), ("b", "A1,L2,K3")])
        e = F.epitope_diversity(run, F.build_families(run))
        assert e["pairwise_jaccard"] == [1.0, 1.0] and e["n_families"] == 2

    def test_fewer_than_two_families_is_unknown_with_a_reason_never_zero(self, tmp_path):
        run = self._run(tmp_path, [("a", "A1,L2")])
        e = F.epitope_diversity(run, F.build_families(run))
        assert e["status"] == "unknown" and "fewer than two" in e["why"] and "pairwise_jaccard" not in e

    def test_a_run_that_lists_no_target_residues_and_has_no_structure_is_unknown(self, tmp_path):
        run = self._run(tmp_path, [("a", ""), ("b", "")])
        e = F.epitope_diversity(run, F.build_families(run))
        assert e["status"] == "unknown" and "no structure" in e["why"]

    def test_a_column_the_reader_cannot_use_is_not_guessed_at(self, tmp_path):
        run = self._run(tmp_path, [("a", "A1/L2"), ("b", "A1")])
        assert F.epitope_diversity(run, F.build_families(run))["status"] == "unknown"
        run = self._run(tmp_path, [("a", "A1,notaresidue"), ("b", "A1")])
        assert F.epitope_diversity(run, F.build_families(run))["status"] == "unknown"

    def test_the_epitope_is_computed_for_a_run_that_is_not_scaffolded(self, tmp_path):
        run = self._run(tmp_path, [("a", "A1,L2"), ("b", "A1,L5")])
        assert not P.has_scaffold(run) and F.epitope_diversity(run, F.build_families(run))["status"] == "observed"


@pytest.fixture(scope="module")
def toy_pair(toy_run2):
    """The same toy run twice, once as a de novo campaign and once recorded as scaffolded, analysed by the families module."""
    from toydata.testing import with_offset

    plain = with_offset(toy_run2)
    scaf = dataclasses.replace(plain, annotations={**plain.annotations, "scaffold": {
        "scaffold": "VHH.cif", "scaffold_edits": SPEC, "threshold": 1.0, "higher": True, "mandatory": True}})
    one, two = F.analyze_families([plain], n_jobs=1).diversity, F.analyze_families([scaf], n_jobs=1).diversity
    return one["runs"][plain.run_id], two["runs"][scaf.run_id], two["pooled"], one["pooled"]


class TestFoldSwitch:
    def test_an_unscaffolded_run_keeps_its_fold_figures_and_gains_only_the_epitope_block(self, toy_pair):
        plain = toy_pair[0]
        assert set(plain["counts"]) == {"fold", "site", "both"} and set(plain["advice"]) == {"fold", "site", "both"} \
            and set(plain["clusters"]) == {"fold", "site", "both"}
        assert "fold" not in plain and "basis_off" not in plain and "basis_default" not in plain
        assert "epitope" in plain and plain["epitope"]["status"] in ("observed", "unknown")
        assert all(f["code"] != "fold_off" for f in plain["flags"])

    def test_a_scaffolded_run_switches_every_fold_based_basis_off_and_says_why(self, toy_pair):
        plain, scaf = toy_pair[0], toy_pair[1]
        assert scaf["fold"] == {"status": "off", "why": F.FOLD_OFF_WHY} and scaf["basis_off"] == ["fold", "both"] and scaf["basis_default"] == "site"
        for key in ("clusters", "counts", "sweep", "advice", "replay_by_basis"):
            assert set(scaf[key]) == {"site"}, key
            assert scaf[key]["site"] == plain[key]["site"], key          # the site basis is exactly what it was
        assert scaf["replay"] == plain["replay_by_basis"]["site"]
        assert scaf["flags"][0]["code"] == "fold_off" and "scaffold" in scaf["flags"][0]["text"]
        assert "threshold_artefact" not in [f["code"] for f in scaf["flags"]]

    def test_everything_else_in_the_block_is_untouched(self, toy_pair):
        plain, scaf = toy_pair[0], toy_pair[1]
        for key in ("run", "n_designs", "n_families", "estimates_valid", "distance", "order", "epoch", "epitope"):
            assert scaf[key] == plain[key], key

    def test_the_pooled_block_follows_when_every_run_is_scaffolded(self, toy_pair):
        pooled = toy_pair[2]
        assert pooled["fold"]["status"] == "off" and set(pooled["counts"]) == {"site"} and pooled["flags"][0]["code"] == "fold_off"
        assert "fold" not in toy_pair[3] and set(toy_pair[3]["counts"]) == {"fold", "site", "both"}

    def test_a_pool_of_a_scaffolded_and_a_plain_run_says_so_and_keeps_the_numbers(self, toy_run1, toy_run2):
        from toydata.testing import with_offset

        a, b = with_offset(toy_run1), with_offset(toy_run2)
        b = dataclasses.replace(b, annotations={**b.annotations, "scaffold": {"scaffold": "x.cif", "scaffold_edits": SPEC, "threshold": 1.0,
                                                                              "higher": True, "mandatory": True}})
        div = F.analyze_families([a, b], n_jobs=1).diversity
        assert "fold" in div["runs"][b.run_id] and "fold" not in div["runs"][a.run_id]
        assert set(div["pooled"]["counts"]) == {"fold", "site", "both"}
        assert div["pooled"]["flags"][0]["code"] == "fold_scaffolded"


# ------------------------------------------------------------------------------------------------ export
class TestExport:
    def _project(self, vhh_campaign, families_dict):
        run = vhh_campaign.run
        return SimpleNamespace(runs={"r1": run}, families={"diversity": {"runs": {"r1": {"epitope": {"status": "observed", "n_families": 4}}}},
                                                           "families": {"r1": families_dict}})

    def _fams(self, run):
        return [{"id": f.id, "accepted": f.accepted} for f in F.build_families(run)]

    def test_a_scaffolded_run_carries_the_three_blocks(self, vhh_campaign):
        proj = self._project(vhh_campaign, self._fams(vhh_campaign.run))
        items, notes = export.paratope_items(proj, "r1", P.analyse_run(vhh_campaign.run))
        assert set(items) == {"paratope", "paratope_rejected", "diversity_paratope"} and notes == []
        assert items["paratope"]["group"] == "accepted" and items["paratope_rejected"]["group"] == "rejected"
        assert items["diversity_paratope"]["fold"]["status"] == "off"

    def test_a_run_that_is_not_scaffolded_carries_nothing(self, vhh_campaign, tmp_path):
        run = make_run(tmp_path, [], spec=None)
        proj = SimpleNamespace(runs={"r1": run}, families={"diversity": {"runs": {"r1": {}}}, "families": {}})
        assert export.paratope_items(proj, "r1", P.analyse_run(run)) == ({}, [])

    def test_a_scaffolded_run_that_cannot_be_analysed_has_no_paratope_block_and_says_why(self, vhh_file, tmp_path):
        run = make_run(tmp_path, [], spec="A27-33,Q#", scaffold=vhh_file)
        proj = SimpleNamespace(runs={"r1": run}, families={"diversity": {"runs": {"r1": {"epitope": {"status": "observed"}}}}, "families": {}})
        items, notes = export.paratope_items(proj, "r1", P.analyse_run(run))
        assert "paratope" not in items and "paratope_rejected" not in items
        dp = items["diversity_paratope"]
        assert dp["fold"]["status"] == "off" and dp["paratope"]["status"] == "unknown" and "Q#" in dp["paratope"]["why"]
        assert dp["loop_length"]["status"] == "unknown" and dp["epitope"]["status"] == "observed"
        assert len(notes) == 1 and "Q#" in notes[0] and "off" in notes[0]

    def test_unaligned_designs_are_named_in_the_run_notes(self, vhh_campaign, vhh_file, tmp_path):
        designs = list(vhh_campaign.run.designs)
        broken = dataclasses.replace(designs[0], id="broken", sequence="M" * 120)
        run = make_run(tmp_path, [*designs, broken], scaffold=vhh_file)
        proj = SimpleNamespace(runs={"r1": run}, families={"diversity": {"runs": {"r1": {}}}, "families": {"r1": self._fams(vhh_campaign.run)}})
        items, notes = export.paratope_items(proj, "r1", P.analyse_run(run))
        assert any("1 of 21 designs could not be placed" in n for n in notes) and items["paratope"]["unaligned"] == 1

    def test_the_blocks_validate_against_the_schema(self, vhh_campaign):
        jsonschema = pytest.importorskip("jsonschema")
        schema = json.loads((repo_root() / "docs" / "schema" / "campaign.schema.json").read_text())
        proj = self._project(vhh_campaign, self._fams(vhh_campaign.run))
        items, _ = export.paratope_items(proj, "r1", P.analyse_run(vhh_campaign.run))
        res, _ = P.analyse_run(vhh_campaign.run)

        def check(name: str, obj) -> None:
            sub = {"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}
            reg = {"common.schema.json": json.loads((repo_root() / "docs" / "schema" / "common.schema.json").read_text())}
            try:
                from referencing import Registry, Resource

                registry = Registry().with_resources([(k, Resource.from_contents(v)) for k, v in reg.items()])
                jsonschema.Draft202012Validator(sub, registry=registry).validate(obj)
            except ImportError:                                # an older jsonschema: resolve the file reference by hand
                resolver = jsonschema.RefResolver("", sub, store=dict(reg))
                jsonschema.Draft7Validator(sub, resolver=resolver).validate(obj)

        check("paratope_block", items["paratope"])
        check("paratope_block", items["paratope_rejected"])
        check("diversity_paratope", items["diversity_paratope"])
        for b in list(res.design_blobs().values())[:3]:
            check("design_para", b)
        # the schema is not decoration: a block that breaks it is refused
        broken = copy.deepcopy(items["paratope"])
        broken["share"]["CDR3"] = "a lot"
        with pytest.raises(jsonschema.ValidationError):
            check("paratope_block", broken)
        del items["diversity_paratope"]["loop_length"]
        with pytest.raises(jsonschema.ValidationError):
            check("diversity_paratope", items["diversity_paratope"])

    def test_the_highlight_is_counted_from_the_block(self, vhh_campaign):
        proj = self._project(vhh_campaign, self._fams(vhh_campaign.run))
        items, _ = export.paratope_items(proj, "r1", P.analyse_run(vhh_campaign.run))
        text = export._paratope_highlight("r1", items)
        acc, rej = items["paratope"], items["paratope_rejected"]
        assert f"{acc['share']['CDR3']:.1f}%" in text and "8 of 8 use all 3 designed segments" in text
        assert f"framework {acc['framework_share']:.1f}%" in text and f"{rej['framework_share']:.1f}%" in text and "fold diversity is switched off" in text
        assert export._paratope_highlight("r1", {}) is None

    def test_the_manifest_records_the_spec_without_the_cluster_path(self, vhh_campaign):
        ann = export._annotations(vhh_campaign.run)
        assert ann["scaffold"]["scaffold_edits"] == SPEC and ann["scaffold"]["file"] == "VHH.cif"
        assert ann["scaffold"]["retained_fraction"] == {"threshold": 1.0, "mandatory": True}
        assert "/" not in json.dumps(ann["scaffold"]["file"])
        assert "scaffold" not in export._annotations(dataclasses.replace(vhh_campaign.run, annotations={}))
