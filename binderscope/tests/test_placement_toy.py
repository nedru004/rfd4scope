"""How a design is placed on the reference chain, and what that has to do with a chain called B and with a domain that hinges.

Each design of a BindCraft run carries its own copy of the target. The build places the design on the reference chain by superposing the shared
residues (matched by number). Two defects showed up on real data: a binder drawn off its epitope, and a target that the Inspector took for
the binder. These tests pin down what was found, on the toy project:

* the default fit is weighted around the binder ("interface"), the others are "all" (every shared residue counts the same, the CLI says whole) and
  "core" (a trimmed fit); every design says which fit it got and how well the target fits near the binder and over the whole chain;
* a reference that calls the target chain B builds the same project as one that calls it A, and the target of inspect.json is always chain A (the
  pages tell the binder, chain B, from the target by the chain id);
* in a reference where the domain that the binder sits on is turned against the rest of the chain, the whole-target fit leaves the epitope off by
  more than 2 A, the interface fit puts it back within 0.5 A;
* a residue number that two residues of the reference chain carry (an insertion code) is left out of the placement, with a warning;
* sugars that carry the chain id of the protein (newer entries number them as part of chain A) no longer turn the target chain into a sugar chain: the
  reference builds, the sugar is found from its asparagine and the Inspector's target keeps chain A for the protein.

The toy designs carry the same target as the reference chain (to 0.03 A), so the difference between the fits shows only where the reference is
changed: toydata/hinge.py makes such a reference from a design.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from toydata.testing import OFFSET

from binder_review import cli
from binder_review import export as E
from binder_review import pipeline as P
from binder_review import structure as S
from binder_review import target_frame as TF


def contacted_deviation(pl: TF.Placement, cx: S.Complex, cutoff: float = 4.5) -> np.ndarray:
    """The CA distance to the reference of every residue that the binder touches (heavy atoms within `cutoff`), under the transform of `pl`."""
    touched = {int(n) for n in S.residue_contacts(cx.target, cx.binder, cutoff).a_residues}
    dev = {int(n): float(d) for n, d in zip(pl.numbers, pl.deviation)}
    return np.array([dev[n] for n in sorted(touched) if n in dev])


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------------------------- the default fit and its records
def design_rows(blobs) -> list[dict]:
    return [d for d in blobs.campaign["designs"] if d["m"].get("place_rmsd") is not None]


def test_every_design_says_which_fit_placed_it_and_how_well_the_target_fits_near_the_binder(toy_blobs, toy_project):
    rows = design_rows(toy_blobs)
    assert len(rows) == 30
    fits = {d["name"]: d["prov"]["fit"] for d in rows}
    assert set(fits.values()) == {"interface", "all"}                                           # all is what the interface fit falls back on
    fell_back = [d for d in rows if d["prov"]["fit"] == "all"]
    assert 1 <= len(fell_back) <= 5 and len(fell_back) < len(rows) / 4
    for d in rows:
        m, pose = d["m"], toy_project.poses[(d["run"], d["name"])]["placement"]
        assert (m["place_iface_rmsd"], m["place_iface_n"], m["place_global_rmsd"]) == (pytest.approx(pose["iface_rmsd"], abs=0.006), pose["iface_n"],
                                                                                    pytest.approx(pose["global_rmsd"], abs=0.006))
        assert m["place_rmsd"] >= m["place_global_rmsd"] - 0.006                                # the whole-target fit is the best any rigid transform gives
        assert d["prov"]["fit"] == pose["fit"] and d["prov"].get("fit_note", "") == pose["fit_note"]
        if d["prov"]["fit"] == "interface":
            assert m["place_iface_n"] >= 12 and "fit_note" not in d["prov"]                    # enough residues near the binder to weight the fit around
            assert m["place_iface_rmsd"] < 0.1 and m["place_rmsd"] < 0.1                       # the toy designs carry the reference's own target
        else:
            assert m["place_iface_n"] < 12
            assert re.fullmatch(r"only \d+ target residues lie near the binder \(at least 12 are needed\): placed by the whole-target fit", d["prov"]["fit_note"])


def test_the_run_says_when_designs_were_placed_by_the_whole_target_fit(toy_blobs, toy_project):
    notes = {r["id"]: [n for n in r["notes"] if "whole-target fit" in n] for r in toy_blobs.campaign["runs"]}
    assert len(notes["run01"]) == 1 and notes["run02"] == []                                    # only the designs at the end of the construct have too few neighbours
    posed = sum(1 for (run, _) in toy_project.poses if run == "run01")
    assert re.fullmatch(rf"\d+ of {posed} designs were placed by the whole-target fit: fewer than 12 target residues lie near the binder, so there is nothing "
                        r"to weight the fit around \(prov\.fit and prov\.fit_note of each design say which\)", notes["run01"][0])
    n = int(notes["run01"][0].split()[0])
    assert n == sum(1 for (run, _), p in toy_project.poses.items() if run == "run01" and p["placement"]["fit_note"])
    assert n >= sum(1 for d in design_rows(toy_blobs) if d["run"] == "run01" and d["prov"]["fit"] == "all")                # the structures that are exported say it too


def test_the_manifest_records_how_the_designs_were_placed(toy_blobs):
    thr = toy_blobs.manifest["thresholds"]["placement"]
    assert thr == {"mode": "interface", "fit": "interface", "iface_full": 8.0, "iface_decay": 6.0, "iface_floor": 0.01, "iface_cutoff": 2.5, "iface_min_n": 12,
                   "near_weight": TF.IFACE_NEAR_WEIGHT, "report_weight": TF.IFACE_REPORT_WEIGHT, "warn_rmsd": thr["warn_rmsd"]}
    assert any("weighted around the binder" in n for n in toy_blobs.manifest["notes"])


def test_a_placed_structure_says_how_it_was_placed(toy_blobs, toy_project, toy_export_dir):
    seen = {"interface": 0, "all": 0}
    for d in design_rows(toy_blobs):
        pdb = toy_export_dir / "structures" / f"{d['name']}.pdb"
        if not pdb.is_file():
            continue
        line = next(l for l in pdb.read_text().splitlines() if l.startswith("REMARK 250 placement"))
        if d["prov"]["fit"] == "interface":
            assert re.fullmatch(r"REMARK 250 placement: fitted around the interface, CA RMSD [0-9.]+ A over \d+ residues \(whole target [0-9.]+ A over \d+\)", line), line
        else:
            assert re.fullmatch(r"REMARK 250 placement: whole-target fit, CA RMSD [0-9.]+ A over \d+ residues \(near the interface [0-9.]+ A over \d+ residues\); "
                                r"only \d+ target residues lie near the binder \(at least 12 are needed\): placed by the whole-target fit", line), line
        pl = toy_project.poses[(d["run"], d["name"])]["placement"]
        assert f"CA RMSD {pl['iface_rmsd'] if d['prov']['fit'] == 'interface' else pl['rmsd']} A over" in line and f" A over {pl['n']}" in line, line
        seen[d["prov"]["fit"]] += 1
    assert seen["interface"] > 10 and seen["all"] >= 1


# ---------------------------------------------------------------------------------------------------------------- the fits on identical construct and reference
def test_every_fit_gives_the_same_placement_when_the_construct_and_the_reference_agree(toy_project, toy_hinge):
    """The design's target is the reference chain (to 0.03 A): there is nothing to trade, so interface, all and core place alike."""
    cx = toy_hinge.cx
    placed = {fit: toy_project.frame.place(cx, "run01", fit=fit) for fit in TF.PLACEMENT_FITS}
    assert TF.PLACEMENT_FITS == ("interface", "all", "core") and {f: p.fit for f, p in placed.items()} == {f: f for f in TF.PLACEMENT_FITS}
    base = placed["all"]
    for fit, pl in placed.items():
        assert np.allclose(pl.R, base.R, atol=1e-3) and np.allclose(pl.t, base.t, atol=0.05), fit
        assert pl.rmsd < 0.1 and pl.iface_rmsd < 0.1 and pl.global_rmsd < 0.1 and pl.core_rmsd < 0.1 and pl.n == pl.core_n == 110, fit
        assert np.linalg.det(pl.R) == pytest.approx(1.0) and np.allclose(pl.R @ pl.R.T, np.eye(3), atol=1e-9)
    assert placed["interface"].iface_n >= 12 and placed["interface"].fit_note == ""
    assert np.allclose(placed["interface"].apply(cx.binder.xyz), base.apply(cx.binder.xyz), atol=0.05)           # the binder lands in the same place


def test_an_unknown_fit_is_refused_and_the_fields_of_a_placement_are_plain_numbers(toy_project, toy_hinge):
    for fit in ("whole", "best", ""):
        with pytest.raises(ValueError, match="fit must be one of interface, all, core"):
            toy_project.frame.place(toy_hinge.cx, "run01", fit=fit)
    pl = toy_project.frame.place(toy_hinge.cx, "run01")
    f = pl.fit_fields()
    assert f == {"fit": "interface", "iface_rmsd": f["iface_rmsd"], "iface_n": pl.iface_n, "global_rmsd": f["global_rmsd"], "fit_note": ""}
    assert isinstance(f["iface_rmsd"], float) and isinstance(f["global_rmsd"], float) and json.dumps(pl.to_dict())              # plain numbers: nothing numpy, no NaN
    lonely = S.Complex("no binder", toy_hinge.cx.target, toy_hinge.cx.target, S.Atoms.empty(), toy_hinge.cx.hydrogens, "A", "B", [])
    alone = toy_project.frame.place(lonely, "run01")
    assert alone.fit == "all" and alone.fit_note == "the structure has no binder atoms: placed by the whole-target fit" and alone.iface_n == 0
    assert alone.fit_fields()["iface_rmsd"] is None                                                                          # NaN becomes null


# ---------------------------------------------------------------------------------------------------------------- a domain that hinges
def test_a_turned_domain_is_placed_by_the_interface_fit_where_the_binder_sits(toy_hinge):
    """The domain of the binder is turned by 12 degrees in the reference: the whole-target fit splits the difference and leaves the residues the binder
    touches more than 2 A off, the interface fit puts them back within 0.5 A (and gives up what it must over the rest of the chain)."""
    cx, frame = toy_hinge.cx, toy_hinge.project.frame
    assert toy_hinge.info["angle"] == 12.0 and 30 <= toy_hinge.info["n_turned"] <= 100 and toy_hinge.info["chain"] == "B"
    whole, near, core = (frame.place(cx, "run01", fit=f) for f in ("all", "interface", "core"))
    assert (whole.fit, near.fit, core.fit) == ("all", "interface", "core") and near.fit_note == ""
    touched = contacted_deviation(whole, cx), contacted_deviation(near, cx)
    assert len(touched[0]) >= 6 and touched[0].max() > 2.0, touched[0]
    assert touched[1].max() < 0.5, touched[1]
    assert near.iface_rmsd < 0.2 and whole.iface_rmsd > 2.0 and near.iface_n == whole.iface_n >= 30
    # what the interface fit gives up: over the whole chain it is worse than the whole-target fit, which is the best any rigid transform does there
    assert whole.rmsd == pytest.approx(whole.global_rmsd, abs=1e-9) and near.global_rmsd == pytest.approx(whole.rmsd, abs=1e-9) and near.rmsd > whole.rmsd + 0.5
    assert near.iface_rmsd < whole.iface_rmsd / 10
    # the transform is a rotation, and the binder follows the target: it sits on the placed target as it does in the design's own frame
    assert np.linalg.det(near.R) == pytest.approx(1.0) and np.allclose(near.R @ near.R.T, np.eye(3), atol=1e-9)
    gap = lambda t, b: np.linalg.norm(t[:, None] - b[None], axis=2).min()                                                       # noqa: E731
    assert gap(near.apply(cx.target.xyz), near.apply(cx.binder.xyz)) == pytest.approx(gap(cx.target.xyz, cx.binder.xyz), abs=1e-9)


def test_the_build_from_a_turned_reference_records_the_difference(toy_hinge):
    """The pipeline's own records: the design that defines the domain fits within 0.2 A near its binder, the whole chain does not, and everything says so."""
    d = toy_hinge.design
    pose = toy_hinge.project.poses[("run01", d.id)]["placement"]
    row = next(x for x in toy_hinge.blobs.campaign["designs"] if x["name"] == d.id)
    assert pose["fit"] == "interface" and pose["iface_rmsd"] < 0.2 and pose["global_rmsd"] > 3.0 and pose["rmsd"] > pose["global_rmsd"]
    assert row["prov"]["fit"] == "interface" and row["m"]["place_iface_rmsd"] < 0.2 and row["m"]["place_global_rmsd"] > 3.0 and row["m"]["place_iface_n"] >= 30
    pdb = (toy_hinge.export / "structures" / f"{d.id}.pdb").read_text().splitlines()
    remark = next(l for l in pdb if l.startswith("REMARK 250 placement"))
    assert remark == (f"REMARK 250 placement: fitted around the interface, CA RMSD {pose['iface_rmsd']} A over {pose['iface_n']} residues "
                      f"(whole target {pose['rmsd']} A over {pose['n']})")                                                         # what the record says, in the file
    assert pose["iface_rmsd"] < 0.2 < 3.0 < pose["rmsd"]                                                                          # near the binder it fits, over the chain it does not
    assert any(l.startswith("REMARK 250 frame:") and "chain B" in l for l in pdb)                                                # the frame says it is chain B
    assert any(re.fullmatch(r"run01: placement RMSD near the binder above [0-9.]+ \u00c5 for \d+ of 12 accepted and \d+ of 8 rejected designs: .*", w, flags=re.S)
               for w in toy_hinge.blobs.manifest["warnings"]), toy_hinge.blobs.manifest["warnings"]                         # the designs on other parts of the chain


# ---------------------------------------------------------------------------------------------------------------- a reference that calls the target chain B
@pytest.fixture(scope="module")
def swapped(toy_dir, tmp_path_factory):
    """Both toy runs built by the command line from the reference whose target chain is called B: --reference-chain B."""
    work = tmp_path_factory.mktemp("swapped")
    out, bundle = work / "project", work / "bundle"
    args = ["import", f"run01={toy_dir / 'runs' / 'run01'}", f"run02={toy_dir / 'runs' / 'run02'}", "--target-pdb", str(toy_dir / "construct" / "toy_construct.pdb"),
            "--reference-cif", str(toy_dir / "reference" / "toy_target_chain_b.cif"), "--reference-chain", "B", "--reference-id", "toy_target", "--name", "toy", "--out", str(out),
            "--bundle", str(bundle), "--quiet", "--no-guide", "--jobs", "2"]
    code = cli.main(args)
    return SimpleNamespace(code=code, out=out, bundle=bundle, export=out / "export", info=read_json(out / "project.json"))


def test_the_command_line_builds_with_a_target_chain_called_b(swapped):
    assert swapped.code == 0 and (swapped.bundle / "index.html").is_file()
    spec = swapped.info["spec"]
    assert spec["reference_chain"] == "B" and spec["partner_chains"] is None and spec["placement"] == "interface"           # the partner is found: chain A has the same sequence
    assert swapped.info["counts"]["accepted"] == 22 and swapped.info["validation"]["counts_match_csv"] and swapped.info["warnings"] == []


def test_the_target_of_inspect_json_is_chain_a_whatever_the_reference_calls_its_chain(swapped, toy_blobs):
    inspect = read_json(swapped.export / "inspect.json")
    lines = inspect["target_pdb"].splitlines()
    assert {l[21] for l in lines if l.startswith("ATOM")} == {"A"} and all(l[19] == "A" and l[31] == "A" for l in lines if l.startswith("HELIX"))
    assert not [l for l in lines if l.startswith(("ATOM", "HETATM")) and l[21] == "B"]                                       # chain B is the binder's, nowhere else
    assert inspect["target_pdb"] == toy_blobs.inspect["target_pdb"]                                                          # and it is the same target as with chain A


def test_a_reference_with_the_chains_swapped_gives_the_same_designs(swapped, toy_blobs, toy_export_dir):
    campaign, inspect = read_json(swapped.export / "campaign.json"), read_json(swapped.export / "inspect.json")
    ref_campaign, ref_inspect = json.loads(json.dumps(toy_blobs.campaign)), json.loads(json.dumps(toy_blobs.inspect))
    assert [d["name"] for d in campaign["designs"]] == [d["name"] for d in ref_campaign["designs"]]
    for a, b in zip(campaign["designs"], ref_campaign["designs"]):
        assert a == b, a["name"]                                                           # every number, the placement fit included
    assert inspect["designs"] == ref_inspect["designs"] and inspect["target_pdb"] == ref_inspect["target_pdb"]
    files = sorted(p.name for p in (swapped.export / "structures").glob("*.pdb"))
    assert files == sorted(p.name for p in (toy_export_dir / "structures").glob("*.pdb")) and len(files) == 26
    strip = lambda text: [l for l in text.splitlines() if not l.startswith("REMARK 250 frame:")]                               # noqa: E731
    for name in files:
        assert strip((swapped.export / "structures" / name).read_text()) == strip((toy_export_dir / "structures" / name).read_text()), name
    frame = next(l for l in (swapped.export / "structures" / files[0]).read_text().splitlines() if l.startswith("REMARK 250 frame:"))
    assert "chain B" in frame and "chain A is this design's own target" in frame


# ---------------------------------------------------------------------------------------------------------------- the placement option
def test_the_placement_option_of_the_command_line(toy_dir, tmp_path, capsys):
    parser = cli.build_parser()
    for name in ("interface", "whole", "core"):
        assert parser.parse_args(["import", "x", "--placement", name]).placement == name
    assert parser.parse_args(["import", "x"]).placement == "interface"
    with pytest.raises(SystemExit) as ex:
        parser.parse_args(["import", "x", "--placement", "best"])
    assert ex.value.code == 2 and "invalid choice" in capsys.readouterr().err
    out = tmp_path / "project"
    args = ["import", f"run02={toy_dir / 'runs' / 'run02'}", "--target-pdb", str(toy_dir / "construct" / "toy_construct.pdb"), "--reference-cif",
            str(toy_dir / "reference" / "toy_target.cif"), "--placement", "whole", "--name", "toy", "--out", str(out), "--quiet", "--jobs", "2"]
    assert cli.main(args) == 0
    capsys.readouterr()
    assert read_json(out / "project.json")["spec"]["placement"] == "whole"
    campaign = read_json(out / "export" / "campaign.json")
    assert {d["prov"]["fit"] for d in campaign["designs"]} == {"all"} and "fit_note" not in json.dumps(campaign["designs"])
    thr = read_json(out / "export" / "manifest.json")["thresholds"]["placement"]
    assert (thr["mode"], thr["fit"]) == ("whole", "all")
    assert all(d["m"]["place_iface_n"] is not None and d["m"]["place_global_rmsd"] is not None for d in campaign["designs"])


def test_the_placement_is_part_of_what_is_cached_and_a_wrong_one_is_refused(toy_dir, tmp_path):
    """One work folder, three placements one after the other: nothing is taken from the cache of another placement."""
    spec = P.ProjectSpec(name="toy", runs=[P.RunSpec(path=toy_dir / "runs" / "run02", run_id="run02")], target_pdb=toy_dir / "construct" / "toy_construct.pdb",
                         reference_cif=toy_dir / "reference" / "toy_target.cif", jobs=2)
    work = tmp_path / "work"
    seen = {}
    for placement in ("interface", "core", "whole", "interface"):
        project = P.run_pipeline(dataclasses.replace(spec, placement=placement), work, log=P.Log(quiet=True))
        fits = {p["placement"]["fit"] for p in project.poses.values()}
        seen[placement] = fits
        mode = E.build_blobs(project).manifest["thresholds"]["placement"]
        assert (mode["mode"], mode["fit"]) == (placement, P.PLACEMENTS[placement])
    assert seen["core"] == {"core"} and seen["whole"] == {"all"} and seen["interface"] <= {"interface", "all"} and "interface" in seen["interface"]
    with pytest.raises(P.PipelineError, match="placement must be one of interface, whole, core, not 'best'"):
        P.run_pipeline(dataclasses.replace(spec, placement="best"), tmp_path / "other", log=P.Log(quiet=True))


# ---------------------------------------------------------------------------------------------------------------- numbers that two residues share
TWIN = 150                                                    # reference number of the residue that is given a twin (construct number 91)


@pytest.fixture(scope="module")
def twinned(toy_dir, tmp_path_factory):
    """The toy reference whose chain A has a second residue with number 150 (150A), moved 20 A away from the first."""
    from toydata.variants import twinned_residue

    return twinned_residue(toy_dir / "reference" / "toy_target.cif", tmp_path_factory.mktemp("twin") / "toy_target_twin.cif", number=TWIN, shift=20.0)


def test_a_residue_number_that_two_reference_residues_carry_is_left_out_of_the_placement(toy_dir, toy_project, toy_hinge, twinned):
    """Designs are matched to the reference by residue number. Number 150 carried by 150 and 150A cannot say which of the two a design residue belongs to: the
    frame warns and leaves the number out, so the placement never pairs a design residue with the wrong one. (The twin is 20 A from its original: a pair
    with the wrong residue would show as an RMSD of about 2 A.)"""
    frame = TF.build_frame(twinned, toy_dir / "construct" / "toy_construct.pdb", dict(toy_project.runs), None, chain="A", partner_chains=("B",))
    assert frame.ambiguous_numbers == {TWIN} and TWIN not in frame.ref_ca and TWIN - 1 in frame.ref_ca and TWIN + 1 in frame.ref_ca
    assert [w for w in frame.meta["warnings"] if "carried by more than one residue" in w] == [
        "reference chain A: 1 residue numbers are carried by more than one residue (insertion codes?), first [150]; designs are matched to the reference by "
        "residue number, so these residues are left out of the placement"]
    cx, plain = toy_hinge.cx, toy_project.frame.place(toy_hinge.cx, "run01")
    assert TWIN - OFFSET in set(cx.target.residues.number.tolist()) and not toy_project.frame.ambiguous_numbers
    pl = frame.place(cx, "run01")
    assert pl.n == plain.n - 1 and TWIN - OFFSET not in set(pl.numbers.tolist()) and TWIN - 1 - OFFSET in set(pl.numbers.tolist())
    assert pl.rmsd < 0.1 and pl.iface_rmsd < 0.1 and pl.fit == "interface"                                                  # not 20 / sqrt(110) = 1.9 A
    assert np.allclose(pl.R, plain.R, atol=1e-3) and np.allclose(pl.t, plain.t, atol=0.05)
    assert any(re.search(r"lacks 1 residues? of run run01's target", w) for w in pl.warnings)                                # the design's residue has no partner
    assert not any("more than one residue" in w for w in toy_project.frame.meta["warnings"])


def test_a_reference_with_an_insertion_code_builds_to_the_end(toy_dir, twinned, tmp_path):
    """The frame and the placement cope with it (the test above), and so does the export: the build finishes (its validation used to stop it, because both residues that
    carry the number were written into the Inspector's target while the pages index the target by number), the frame's warning reaches the manifest and the
    Inspector's target holds one residue per number."""
    spec = P.ProjectSpec(name="twin", runs=[P.RunSpec(path=toy_dir / "runs" / "run02", run_id="run02")], target_pdb=toy_dir / "construct" / "toy_construct.pdb",
                         reference_cif=twinned, jobs=2)
    project = P.run_pipeline(spec, tmp_path / "work", log=P.Log(quiet=True))
    blobs = E.build_blobs(project)                                                                       # validated against the schemas on the way
    assert any("carried by more than one residue" in w and f"[{TWIN}]" in w for w in blobs.manifest["warnings"]), blobs.manifest["warnings"]
    lines = blobs.inspect["target_pdb"].splitlines()
    ca = [int(l[22:26]) for l in lines if l.startswith("ATOM") and l[21] == "A" and l[12:16] == " CA "]
    assert ca == blobs.inspect["target_resi"] and ca.count(TWIN) <= 1 and TWIN - 1 in ca and TWIN + 1 in ca


# ---------------------------------------------------------------------------------------------------------------- sugars that carry the chain id of the protein
@pytest.fixture(scope="module")
def glycosylated(toy_dir, tmp_path_factory):
    """The toy reference with an N-acetylglucosamine on an asparagine of the target, written as newer PDB entries do: the sugar is a branched entity with the chain id
    of the protein (chain A), linked to the asparagine (ND2 to C1)."""
    from toydata.variants import glycosylated_in_chain

    path, site = glycosylated_in_chain(toy_dir / "reference" / "toy_target.cif", tmp_path_factory.mktemp("glycan") / "toy_target_glycan.cif")
    return SimpleNamespace(path=path, site=site)


def test_a_sugar_with_the_chain_id_of_the_protein_does_not_turn_the_chain_into_a_sugar_chain(glycosylated):
    sf = S.read_structure(str(glycosylated.path))
    assert sf.kinds == {"A": "polymer", "A:branched": "branched", "B": "polymer"}                       # the sugar is its own group, the chain stays a polymer
    assert sf.polymer_chains() == ["A", "B"] and sf.glycan_groups() == ["A:branched"]
    assert len(sf.groups["A"].residues) == 250 and len(sf.groups["A:branched"].residues) == 1
    assert [(ln.kind, ln.a.chain, ln.a.resnum, ln.a.atom, ln.b.resnum, ln.b.atom) for ln in sf.links] == [("covale", "A", glycosylated.site, "ND2", 901, "C1")]


def test_a_reference_with_a_sugar_on_the_target_builds_and_the_sugar_is_found_and_exported(glycosylated, toy_dir, tmp_path):
    """The build used to stop at the frame (the chain of the target was a sugar chain); now the sugar is followed from its asparagine, the Inspector's target keeps
    chain A for the protein and carries the sugar as a hetero group of its own chain."""
    spec = P.ProjectSpec(name="glycan", runs=[P.RunSpec(path=toy_dir / "runs" / "run02", run_id="run02")], target_pdb=toy_dir / "construct" / "toy_construct.pdb",
                         reference_cif=glycosylated.path, jobs=2)
    project = P.run_pipeline(spec, tmp_path / "work", log=P.Log(quiet=True))
    frame = project.frame
    assert frame.meta["chain"] == "A" and frame.partner_chains == ["B"] and frame.meta["warnings"] == []
    assert [(g.owner, g.site, g.resnames) for g in frame.glycans] == [("A", glycosylated.site, ["NAG"])] and frame.glycans[0].link_distance == pytest.approx(1.45)
    blobs = E.build_blobs(project)                                                                       # validated against the schemas on the way
    ins = blobs.inspect
    assert ins["glycan"] == [glycosylated.site] and blobs.campaign["target"]["glycan"] == [glycosylated.site] and str(glycosylated.site) in ins["glycan_atoms"]
    lines = ins["target_pdb"].splitlines()
    protein = {l[21] for l in lines if l.startswith("ATOM")}
    sugar = [l for l in lines if l.startswith("HETATM")]
    assert protein == {"A"} and sugar and {l[21] for l in sugar} == {"C"} and {l[17:20] for l in sugar} == {"NAG"}          # the protein is chain A, the sugar another chain
    assert blobs.manifest["warnings"] == []
    d = project.runs["run02"].accepted()[0]
    out = tmp_path / "export"
    E.write_export(blobs, out)
    pdb = (out / "structures" / f"{d.id}.pdb").read_text().splitlines()
    assert {l[21] for l in pdb if l.startswith(("ATOM", "HETATM"))} == {"A", "B"}                       # a design's file holds its own target and the binder only
