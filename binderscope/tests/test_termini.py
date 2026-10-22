"""Terminus analysis (binder_review.termini): which end of a binder can carry a tag, and what it would cost.

The geometry is tested on hand-built chains where the answer is known by construction: a straight rod with a wall of target atoms beyond one
end must come out tagged at the other end, whichever end that is; an environment that closes one end must move the answer; a terminal residue
in the interface must block every tag size; a disordered tail is judged from its last ordered residue. The format rules (a cyclic peptide has
no free ends, a homo-oligomer tags every copy) and the reading of what BindCraft was asked to do about the ends are tested on settings dicts.
The export is tested on the toy project: every judged design carries a block that validates, the exit directions survive the placement, and a
project without a reference structure says that the environment was not part of the check instead of reporting it clear."""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from binder_review import export as E
from binder_review import pipeline as P
from binder_review import structure as S
from binder_review import termini as TM
from toydata import appkit


# ------------------------------------------------------------------------------------------------ hand-built chains
def _atoms(xyz, chain, resnum=None, name="CA", resname="ALA", element="C"):
    xyz = np.asarray(xyz, dtype=float).reshape(-1, 3)
    n = len(xyz)
    return S.Atoms(xyz, [name] * n, [element] * n, [resname] * n, np.arange(1, n + 1) if resnum is None else resnum, [chain] * n)


def rod(n=20, spacing=3.8, axis=(1.0, 0.0, 0.0), origin=(0.0, 0.0, 0.0)):
    """n CA atoms on a straight line (a strand): residue 1 at the origin."""
    axis = np.asarray(axis, dtype=float)
    axis /= np.linalg.norm(axis)
    return _atoms(np.asarray(origin, dtype=float) + np.arange(n)[:, None] * spacing * axis, "B")


def wall(x, half=14.0, step=2.0, chain="A"):
    """A square plate of atoms in the plane x = const, centred on the axis: the target."""
    g = np.arange(-half, half + 0.1, step)
    yy, zz = np.meshgrid(g, g)
    return _atoms(np.stack([np.full(yy.size, x), yy.ravel(), zz.ravel()], axis=1), chain)


def helix_ca(n=24):
    """CA positions of an ideal alpha helix along +x (radius 2.3 A, rise 1.5 A, 100 degrees per residue)."""
    t = np.arange(n)
    return np.stack([1.5 * t, 2.3 * np.cos(np.radians(100) * t), 2.3 * np.sin(np.radians(100) * t)], axis=1)


def analysis(n, *, ss=None, plddt=90.0, rsa=0.5, bc=(), center=(0.0, 0.0, 0.0)):
    ss = ss if ss is not None else "E" * n
    return SimpleNamespace(n=n, ss=ss, rsa_res=[rsa] * n, plddt_res=[plddt] * n if np.isscalar(plddt) else list(plddt), bc=list(bc), center=list(center))


FMT = TM.Format(kind="de novo", known=True)


def run_against_wall(*, wall_x=80.0, bc=(), env=None, n=20, flip=False, ss=None, plddt=90.0, centre="wall"):
    """The rod along +x with the plate wall_x along the axis beyond its last residue; flip reverses the chain, so that residue 1 faces the plate.
    ``centre`` is where the interface is: at the plate ("wall") or in the middle of the binder (None: the paratope has no side)."""
    b = rod(n)
    xyz = b.xyz[::-1] if flip else b.xyz
    b = _atoms(xyz, "B")
    c = [wall_x - 4.0, 0.0, 0.0] if centre == "wall" else [float(xyz[:, 0].mean()), 0.0, 0.0]
    return TM.assess(analysis(n, bc=bc, center=c, ss=ss, plddt=plddt), b, wall(wall_x), FMT, env)


# ------------------------------------------------------------------------------------------------ geometry
def test_the_exit_direction_follows_a_strand_and_a_helix_axis():
    b = rod(20)
    ca = b.ca_xyz()
    assert np.allclose(TM.exit_direction(ca, 0, "n"), [-1, 0, 0], atol=1e-9) and np.allclose(TM.exit_direction(ca, 19, "c"), [1, 0, 0], atol=1e-9)
    h = helix_ca(24)
    for idx, end, sign in ((0, "n", -1.0), (23, "c", 1.0)):
        u = TM.exit_direction(h, idx, end)
        assert math.degrees(math.acos(float(u @ np.array([sign, 0, 0])))) < 30.0     # the mean of the next residues sits near the axis, so the vector follows it
    assert TM.exit_direction(rod(3).ca_xyz(), 0, "n") is None                         # fewer than three inward residues: no direction, not a guess


def test_the_cone_has_the_stated_opening():
    u = np.array([0.0, 0.0, 1.0])
    d = TM.cone_directions(u)
    assert len(d) == 1 + TM.CONE_RINGS * TM.CONE_AZIMUTHS and np.allclose(np.linalg.norm(d, axis=1), 1.0)
    ang = np.degrees(np.arccos(np.clip(d @ u, -1, 1)))
    assert ang.max() == pytest.approx(TM.CONE_DEG) and ang.min() == pytest.approx(0.0)


def test_the_end_facing_the_target_is_out_and_the_free_end_is_picked():
    t = run_against_wall(wall_x=80.0)             # the rod ends at x = 72.2: the C-terminus is 7.8 A from the plate
    n, c = t["n"], t["c"]
    assert c["d"] == pytest.approx(7.8, abs=0.1) and n["d"] > 70
    assert c["side"] == "paratope" and n["side"] == "far"                # inside 10 A of the target; the other end lies on the far side of the binder
    # the folded fusions cannot get past the plate; the flexible tags route round it, which is the weak condition that is being tested for them
    assert all(c["g"][k]["s"] == "blocked" for k in ("domain", "large")) and all(n["g"][k]["s"] == "free" for k in TM.TAG_KEYS)
    assert c["g"]["his6"]["s"] == "free" and c["g"]["strep"]["s"] == "free" and c["g"]["strep"]["esc"] > c["g"]["his6"]["esc"]      # open solvent for a Twin-Strep is farther out than for a His6
    assert c["room"] < TM.TAGS[2][2] and n["room"] == TM.ROOM_CAP         # no folded fusion fits against the end that faces the plate; the free end is open to the cap
    assert c["limit"] == "target" and n["limit"] is None
    assert all(v == "N" for v in t["pick"].values()) and t["immob"] == "N"
    assert c["aim"] < 10 and n["aim"] > 170                              # the C-terminus points at the plate, the N-terminus away from it


def test_the_answer_does_not_depend_on_which_end_faces_the_target():
    a, b = run_against_wall(), run_against_wall(flip=True)
    assert all(v == "N" for v in a["pick"].values()) and all(v == "C" for v in b["pick"].values())
    assert a["immob"] == "N" and b["immob"] == "C"
    assert a["c"]["d"] == pytest.approx(b["n"]["d"], abs=1e-6) and a["n"]["room"] == b["c"]["room"]      # the mirror image gives the mirrored numbers


def test_a_target_far_away_leaves_both_ends_free():
    t = run_against_wall(wall_x=400.0, bc=(), n=20, centre=None)
    assert all(t[e]["g"][k]["s"] == "free" for e in "nc" for k in TM.TAG_KEYS)
    assert all(v == "both" for v in t["pick"].values())
    assert t["nc_d"] == pytest.approx(3.8 * 19, abs=0.01)


def test_a_wall_nearer_than_a_tag_needs_room_for_costs_a_linker_or_everything():
    # 30 A of free chain beyond the tip: a His6 fits as it is, a folded fusion the size of a GFP is stopped
    t = run_against_wall(wall_x=72.2 + 30.0)
    g = t["c"]["g"]
    assert g["his6"]["s"] == "free" and g["domain"]["s"] != "free" and g["large"]["s"] != "free"
    order = [TM.STATUS_RANK[g[k]["s"]] for k in TM.TAG_KEYS]
    assert order == sorted(order)                                         # a larger tag is never easier than a smaller one at the same end
    assert 0 < t["c"]["room"] < 24


def test_a_terminal_residue_in_the_interface_blocks_the_folded_fusions_and_puts_the_end_on_the_paratope_side():
    t = run_against_wall(wall_x=400.0, bc=(1, 2), centre=None)
    n = t["n"]
    assert n["contact"] is True and n["iface_n"] == 2 and n["side"] == "paratope"
    assert all(n["g"][k] == {"s": "blocked", "l": None, "why": "contact"} for k in ("domain", "large")) and n["room"] == 0.0
    assert all(n["g"][k]["s"] == "free" for k in ("his6", "strep"))          # an unstructured chain leaves the end wherever it lies and routes into open solvent
    assert all(v == "C" for v in t["pick"].values())                           # but the end on the paratope side loses to the one that is not


def test_an_environment_that_closes_an_end_moves_the_answer_and_is_named():
    def env(points):
        pts = np.asarray(points)
        return {"olig": None, "rest": 0.5 if (pts[:, 0] < 1).any() else 99.0, "glycan": None}      # the rest of the protein fills everything beyond the N-terminus
    with_env = run_against_wall(wall_x=400.0, env=env, centre=None)
    without = run_against_wall(wall_x=400.0, centre=None)
    assert with_env["env"] is True and without["env"] is False
    assert all(v == "both" for v in without["pick"].values()) and all(v == "C" for v in with_env["pick"].values())
    assert with_env["n"]["limit"] == "rest" and with_env["n"]["g"]["his6"]["s"] == "blocked" and with_env["n"]["g"]["strep"]["s"] == "blocked"
    assert with_env["c"]["g"]["large"]["s"] == "free"
    assert with_env["n"]["d_env"] == 0.5 and with_env["n"]["env_what"] == "rest" and without["n"]["d_env"] is None


def test_a_disordered_tail_is_judged_from_its_last_ordered_residue_with_the_tail_as_linker():
    n = 20
    ss = "LLLLLL" + "E" * (n - 6)
    pl = [45.0] * 6 + [92.0] * (n - 6)
    t = run_against_wall(wall_x=400.0, ss=ss, plddt=pl)
    e = t["n"]
    assert e["via_tail"] is True and e["conf"] == "low" and e["tail"] == 6 and e["ss"] == "E" and e["at"] == 7
    assert t["c"]["via_tail"] is False and t["c"]["conf"] == "ok" and t["c"]["at"] == n
    assert e["g"]["his6"]["s"] == "free"
    # a loop that is confident is a loop, not a floating tail: the tip is used as it is
    ok = run_against_wall(wall_x=400.0, ss=ss)["n"]
    assert ok["via_tail"] is False and ok["conf"] == "ok" and ok["at"] == 1


def test_a_floppy_tail_touching_the_target_does_not_block_but_a_confident_one_does():
    n = 20
    ss = "LLLLLL" + "E" * (n - 6)
    floppy = run_against_wall(wall_x=400.0, ss=ss, plddt=[45.0] * 6 + [92.0] * (n - 6), bc=(1, 2, 3))
    assert floppy["n"]["contact"] is True and floppy["n"]["iface_n"] == 3                 # the model has the tail on the target: that is the evidence ...
    assert floppy["n"]["via_tail"] is True and floppy["n"]["g"]["domain"]["s"] != "blocked"      # ... but where the tip lies is a guess, so the verdict is read at residue 7
    firm = run_against_wall(wall_x=400.0, ss=ss, plddt=92.0, bc=(1, 2, 3))
    assert firm["n"]["via_tail"] is False and firm["n"]["g"]["domain"] == {"s": "blocked", "l": None, "why": "contact"}
    anchored = run_against_wall(wall_x=400.0, ss=ss, plddt=[45.0] * 6 + [92.0] * (n - 6), bc=(7,))
    assert anchored["n"]["g"]["domain"]["why"] == "contact"                                  # the last ordered residue itself in the interface blocks it


def tube(x0, x1, half=7.0, step=2.0):
    """A square tube of target atoms along x, inner width 2 * half: open space inside it is never more than `half` from an atom."""
    g = np.arange(-half, half + 0.1, step)
    pts = [(x, a, s * half) for x in np.arange(x0, x1 + 0.1, step) for a in g for s in (-1, 1)] + [(x, s * half, a) for x in np.arange(x0, x1 + 0.1, step) for a in g for s in (-1, 1)]
    return _atoms(np.unique(np.array(pts), axis=0), "A")


def test_the_same_end_can_carry_a_his6_and_not_a_twin_strep():
    """A binder lying in a narrow channel: 7 A from every wall at best. Open solvent for a His6 (5 A) is there; for a Twin-Strep (10 A, so that
    Strep-Tactin can bind it) it is not, however flexible the chain is, because every way out is longer than the search or the reach."""
    t = TM.assess(analysis(20, center=[36.0, 0.0, 0.0]), rod(20), tube(-120.0, 300.0), FMT, None)
    for e in ("n", "c"):
        g = t[e]["g"]
        assert g["his6"]["s"] == "free" and g["strep"] == {"s": "blocked", "l": None, "why": "room", "esc": None}
        assert g["domain"]["s"] == "blocked" and g["large"]["s"] == "blocked"
    assert t["pick"]["his6"] == "both" and t["pick"]["strep"] == "none"


def test_a_flexible_chain_routes_round_an_obstacle_a_cone_would_not_clear():
    """The plate in front of the C-terminus blocks every direction of a cone, yet a chain of the length of a Twin-Strep gets round its edge: the
    weak condition is the one asked for a flexible tag, and the folded fusions stay blocked."""
    t = run_against_wall(wall_x=80.0, centre=None)
    c = t["c"]["g"]
    assert c["strep"]["s"] == "free" and c["strep"]["esc"] is not None and c["strep"]["esc"] > 7.8       # the path is longer than the straight 7.8 A to the plate
    assert c["domain"]["s"] == "blocked" and c["large"]["s"] == "blocked"


def test_a_closed_pocket_is_the_one_thing_that_stops_a_flexible_chain():
    n_pts = 600
    k = np.arange(n_pts) + 0.5
    phi, theta = np.arccos(1 - 2 * k / n_pts), np.pi * (1 + 5 ** 0.5) * k
    shell = 4.5 * np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1) + np.array([-1.0, 0.0, 0.0])
    t = TM.assess(analysis(20, center=[36.0, 0.0, 0.0]), rod(20), _atoms(shell, "A"), FMT, None)
    assert t["n"]["g"]["his6"]["s"] == "blocked" and t["n"]["g"]["strep"]["s"] == "blocked"
    assert t["c"]["g"]["his6"]["s"] == "free"                                                         # the other end is in the open


def test_the_tag_is_not_part_of_the_design_and_never_enters_its_liabilities():
    """A Twin-Strep carries two tryptophans. None of the tag's residues may reach the design's own liability counts, so the analysis neither reads
    nor writes a sequence."""
    a = analysis(20, center=[76.0, 0.0, 0.0])
    a.liab = [{"kind": "Trp oxidation", "pos": 7}]
    a.seq = "A" * 20
    before = (list(a.liab), a.seq)
    t = TM.assess(a, rod(20), wall(80.0), FMT, None)
    assert (a.liab, a.seq) == before
    text = json.dumps(t)
    assert "WSHPQFEK" not in text and "seq" not in t and "liab" not in t and not any(len(str(v)) > 20 and str(v).isalpha() for v in t.values())
    assert all("WSHPQFEK" not in json.dumps(x) for x in TM.tag_table())


def test_the_four_tag_sizes_are_ordered_and_the_constants_are_written_down():
    r = [t[2] for t in TM.TAGS]
    assert r == sorted(r) and TM.TAG_KEYS == ("his6", "strep", "domain", "large")
    c = TM.constants()
    assert c["near"] == 10.0 and c["cone_deg"] == TM.CONE_DEG and c["room_cap"] == TM.ROOM_CAP and "tags" not in c
    assert [t["key"] for t in TM.tag_table()] == list(TM.TAG_KEYS)


def test_the_pick_puts_room_first_then_the_side_then_the_linker():
    def end(s, l=0, side="far"):
        return {"g": {"his6": {"s": s, "l": l}}, "side": side}
    pick = lambda n, c: TM._pick(n, c, "his6")  # noqa: E731
    assert pick(end("blocked", None), end("blocked", None)) == "none"
    assert pick(end("free"), end("blocked", None)) == "N" and pick(end("blocked", None), end("free")) == "C"
    assert pick(end("free", side="paratope"), end("linker", 4, "far")) == "C"          # an end on the paratope side loses to one that is not, even when it needs a linker
    assert pick(end("free", side="paratope"), end("free", side="paratope")) == "both"
    assert pick(end("linker", 1), end("linker", 5)) == "N" and pick(end("linker", 2), end("linker", 4)) == "both"   # a gap under three residues is not told apart
    assert pick(end("free"), end("free")) == "both"


def test_immobilisation_holds_the_binder_by_the_end_away_from_the_paratope():
    def end(away, s="free"):
        return {"away": away, "g": {"his6": {"s": s, "l": 0}}}
    assert TM._immobilise(end(-0.5), end(0.9)) == "C" and TM._immobilise(end(0.9), end(-0.5)) == "N"
    assert TM._immobilise(end(0.9), end(0.8)) == "either" and TM._immobilise(end(-0.5), end(-0.6)) is None
    assert TM._immobilise(end(0.9, "blocked"), end(-0.5)) is None                   # the far end has no room and the other holds it by its binding face


# ------------------------------------------------------------------------------------------------ format and what BindCraft was asked
def _run(modality=None, raw=None, lengths=(80, 120), scaffold=None):
    ann = {}
    if modality is not None:
        ann["modality"] = modality
    if scaffold:
        ann["scaffold"] = scaffold
    return SimpleNamespace(settings=SimpleNamespace(settings_raw=raw or {}, protocol="4stage"), annotations=ann,
                           designs=[SimpleNamespace(length=n) for n in lengths])


def test_a_cyclic_peptide_has_no_free_ends_and_no_numbers():
    f = TM.format_of(_run("cyclic peptide", lengths=(12,)))
    assert f.kind == "cyclic peptide" and f.applies is False and "no free ends" in f.why
    out = TM.assess(analysis(12), None, None, f)
    assert out["applies"] is False and out["why"] == f.why and "n" not in out and "c" not in out and "pick" not in out
    assert TM.format_of(_run(None, raw={"settings": {"cyclize_peptide": True}})).applies is False


def test_a_vhh_carries_its_convention_and_it_is_not_an_input():
    f = TM.format_of(_run("VHH", scaffold={"scaffold": "VHH.cif"}))
    assert f.kind == "VHH" and f.convention == "C" and f.scaffold is True and f.known is True and f.applies is True
    assert "N-terminus" in f.convention_why
    # the geometry decides: the same chain gives the same answer whatever the format says, so agreement with the convention is a check and not a result of it
    b, t, a = rod(20), wall(80.0), analysis(20, center=[76.0, 0.0, 0.0])
    flipped = _atoms(b.xyz[::-1], "B")
    assert TM.assess(a, flipped, t, f)["pick"] == TM.assess(a, flipped, t, FMT)["pick"]
    assert all(v == "C" for v in TM.assess(a, flipped, t, f)["pick"].values())          # residue 1 faces the target here, so the C-terminus is the one that is free


def test_the_modality_is_read_from_the_run_and_assumed_only_when_the_run_is_silent():
    assert TM.format_of(_run("de novo")).known is True and TM.format_of(_run(None)).known is False and TM.format_of(_run(None)).kind == "de novo"
    assert TM.format_of(_run("scFv")).kind == "scFv" and TM.format_of(_run("scFv")).convention is None
    assert TM.format_of(_run(None, raw={"metadata": {"settings_modality": "VHH"}})).kind == "VHH"
    assert TM.format_of(_run("mystery format")).kind == "mystery format"


def test_a_homo_oligomer_tags_every_copy_and_several_chains_switch_the_question_off():
    f = TM.format_of(_run("de novo", raw={"settings": {"copies": 3}}))
    assert f.copies == 3 and f.applies and any("every copy" in n for n in f.notes)
    g = TM.format_of(_run("Fab", raw={"resolved": {"binder_chains": ["H", "L"]}}))
    assert g.applies is False and "2 binder chains" in g.why


def test_a_short_binder_is_called_a_peptide_in_one_note():
    f = TM.format_of(_run("de novo", lengths=(14, 25)))
    assert f.peptide is True and any("large share" in n for n in f.notes)
    assert TM.format_of(_run("de novo", lengths=(80, 120))).peptide is False


def test_what_bindcraft_was_asked_about_the_ends_is_read_from_its_settings():
    off = TM.designed_for(_run("VHH", raw={"settings": {"weights_binder_contacts": 1.0, "min_plddt_final": 0.7}}))
    assert off == {"known": True, "accessible": False, "together": False, "buried": False, "items": []}
    on = TM.designed_for(_run("de novo", raw={"settings": {"weights_termini_angle": 0.1, "weights_n_terminus_away": 0.5, "min_plddt_final": 0.7}}))
    assert on["accessible"] and not on["together"] and not on["buried"] and "termini_accessible" in on["items"][0]
    tog = TM.designed_for(_run("de novo", raw={"settings": {"weights_termini_distance": 1.0, "min_plddt_final": 0.7}}))
    assert tog["together"] and not tog["accessible"]
    bur = TM.designed_for(_run("de novo", raw={"settings": {"weights_exposed_termini": 0.4, "min_plddt_final": 0.7}}))
    assert bur["buried"] and "opposite" in bur["items"][0]
    assert TM.designed_for(_run("de novo", raw={"settings": {"weights_termini_angle": 0.0, "min_plddt_final": 0.7}}))["accessible"] is False   # a zero weight is not asked for
    unk = TM.designed_for(_run("de novo", raw={}))
    assert unk["known"] is False and unk["items"] == []                                   # no settings recorded: nothing claimed either way


def test_a_block_is_plain_json():
    t = run_against_wall(wall_x=80.0)
    assert json.loads(json.dumps(t)) == t


# ------------------------------------------------------------------------------------------------ export
def _ca_of(pdb_text):
    out = {}
    for ln in pdb_text.splitlines():
        if ln.startswith("ATOM") and ln[12:16] == " CA ":
            out[int(ln[22:26])] = [float(ln[30:38]), float(ln[38:46]), float(ln[46:54])]
    return out


def test_every_judged_design_carries_a_block_that_validates(toy_blobs):
    b = toy_blobs
    assert b.validation is not None and not b.validation.errors
    ds = b.campaign["designs"]
    acc = [d for d in ds if d["status"] == "accepted"]
    assert acc and all("term" in d for d in acc)
    assert {d["id"] for d in ds if "term" in d} == set(b.inspect["designs"])                 # the designs a person can reach carry it, the rest of the rejected ones carry none
    for d in ds:
        if "term" not in d:
            continue
        t = d["term"]
        assert t["applies"] is True and t["v"] == TM.SCHEMA and set(t["pick"]) == set(TM.TAG_KEYS) and t["env"] is True
        for e in ("n", "c"):
            end = t[e]
            assert set(end["g"]) == set(TM.TAG_KEYS) and end["side"] in ("paratope", "lateral", "far") and 0 <= end["room"] <= TM.ROOM_CAP
            assert end["d"] == pytest.approx(d["m"]["nterm_d" if e == "n" else "cterm_d"], abs=0.05)      # the distance is the one the interface module has always exported
            assert abs(np.linalg.norm(end["dir"]) - 1.0) < 2e-3
        assert b.inspect["designs"][d["id"]]["term"] == t


def test_the_run_block_says_what_the_run_is_and_what_it_asked_for(toy_blobs):
    for r in toy_blobs.campaign["runs"]:
        t = r["term"]
        assert [x["key"] for x in t["tags"]] == list(TM.TAG_KEYS) and t["env"] is True and t["const"]["near"] == 10.0
        assert t["format"]["kind"] == "de novo" and t["format"]["known"] is False and t["format"]["applies"] is True       # BindCraft 1 records no modality
        assert t["designed_for"]["known"] is False
    assert {r["id"]: r["term"] for r in toy_blobs.inspect["runs"]} == {r["id"]: r["term"] for r in toy_blobs.campaign["runs"]}


def test_the_exit_directions_survive_the_placement(toy_blobs):
    """The direction is computed in the design's frame and written in the placed frame: recomputed from the placed binder it must agree."""
    checked = 0
    for tag, X in toy_blobs.inspect["designs"].items():
        ca = _ca_of(X["pdb_b"])
        arr = np.array([ca[i] for i in sorted(ca)])
        for e, end in (("n", "n"), ("c", "c")):
            blk = X["term"][e]
            if blk["dir"] is None:
                continue
            u = TM.exit_direction(arr, blk["at"] - 1, end)
            assert u is not None and float(u @ np.array(blk["dir"])) > 0.995, (tag, e)
            checked += 1
    assert checked >= 10


def test_a_project_without_a_reference_says_the_environment_was_not_checked(toy_dir, tmp_path_factory):
    spec = P.ProjectSpec(name="noref", runs=[P.RunSpec(path=toy_dir / "runs" / "run01", run_id="run01")], target_pdb=toy_dir / "construct" / "toy_construct.pdb",
                         reference_cif=None, jobs=2)
    blobs = E.build_blobs(P.run_pipeline(spec, tmp_path_factory.mktemp("noref_term"), log=P.Log(quiet=True)))
    ds = [d for d in blobs.campaign["designs"] if "term" in d]
    assert ds and all(d["term"]["env"] is False for d in ds)
    assert all(d["term"][e]["d_env"] is None and d["term"][e]["env_what"] is None for d in ds for e in ("n", "c"))      # absent, not a distance
    assert blobs.campaign["runs"][0]["term"]["env"] is False


def test_a_family_is_judged_by_most_of_its_sequences_and_ties_go_in_a_fixed_order():
    fp = TM.family_pick
    assert fp(["C", "C", "N"]) == "C" and fp(["N", "N", "C"]) == "N" and fp(["C", "N"]) == "C" and fp(["N", "both"]) == "N" and fp([]) is None
    assert fp(["none", "both"]) == "both" and fp(["none", "none", "N", "N"]) == "N" and fp(["none"]) == "none"      # no quality score enters, so pages and exporter agree


def test_the_overview_counts_families_that_can_take_a_tag(toy_blobs):
    hl = {h["id"]: h for h in toy_blobs.campaign["highlights"] if h["id"].startswith("termini")}
    for r in toy_blobs.campaign["runs"]:
        text = hl[f"termini_{r['id']}"]["text"]
        fams = {d["bb"] for d in toy_blobs.campaign["designs"] if d["run"] == r["id"] and d["status"] == "accepted" and d.get("term")}
        m = re.search(r"\((\d+) families? of \S+, predicted from one conformation\)\. His6: (\d+) at the C-terminus, (\d+) at the N-terminus, (\d+) at either end, (\d+) at neither\. "
                      r"Twin-Strep: (\d+) at the C-terminus, (\d+) at the N-terminus, (\d+) at either end, (\d+) at neither\.", text)
        assert m and int(m.group(1)) == len(fams)
        g = [int(x) for x in m.groups()[1:]]
        assert sum(g[:4]) == len(fams) and sum(g[4:]) == len(fams)                                   # counts of families, for each of the two tags
        assert "not asked to keep the ends free" not in text or r["term"]["designed_for"]["known"]         # BindCraft 1 records no settings: nothing is claimed either way


# ------------------------------------------------------------------------------------------------ the words the pages use (needs Node)
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not appkit.NODE, reason="needs Node 22+")
def test_the_page_helpers_read_the_blocks_in_plain_words_and_agree_with_the_python_family_rule(tmp_path):
    block = run_against_wall(flip=True)                      # residue 1 faces the plate: the C-terminus is the one to tag
    run = {"id": "r1", "term": {"tags": TM.tag_table(), "const": {"near": 10, "room_cap": TM.ROOM_CAP}, "env": True, "partner": False,
                                "format": {"kind": "VHH", "known": True, "applies": True, "convention": "C", "convention_why": "A VHH's N-terminus lies at the CDR end.", "notes": []},
                                "designed_for": {"known": True, "accessible": False, "together": False, "buried": False, "items": []}}}
    design = {"id": "d1", "run": "r1", "bb": "f1", "fam": "f1", "term": block}
    combos = [["C", "C", "N"], ["C", "N"], ["N", "C"], ["none", "both"], ["none", "none", "N", "N"], ["both"], ["N", "both"]]
    js = rf"""
const fs = require('fs');
global.window = global; global.document = {{ head: null, getElementById: () => null, documentElement: {{ getAttribute: () => null }} }};
global.Mini3D = {{ hex2rgb: (x) => x, mix: (a) => a, lighten: (a) => a }};
(0, eval)(fs.readFileSync({json.dumps(str(ROOT / "viewer" / "js" / "common.js"))}, 'utf8'));
const T = Common.term, d = {json.dumps(design)}; T.use([{json.dumps(run)}]);
const mk = (p) => ({{ term: {{ applies: true, n: {{ g: {{ his6: {{ s: 'free', l: 0 }} }} }}, c: {{ g: {{ his6: {{ s: 'free', l: 0 }} }} }}, pick: {{ his6: p }} }} }});
const fam = {json.dumps(combos)}.map((ps) => {{ const r = T.famPick(mk(ps[0]), ps.map(mk), 'his6'); return r && r.pick; }});
const csv = T.csv([d]).split('\n'), csv2 = T.csv([{{ id: 'a', run: 'r', bb: 'f' }}, {{ id: 'b', run: 'r', bb: 'f', term: {{ applies: false, format: 'cyclic peptide', why: 'x' }} }}]).split('\n');
console.log(JSON.stringify({{ has: T.has(d), head: T.headline(d, 'his6'), headLarge: T.headline(d, 'large'), evN: T.evidence(d, 'n').map((x) => x.t), evC: T.evidence(d, 'c').map((x) => x.t),
  down: T.downstream(d).map((x) => x.t), conv: T.convention(d), df: T.designedFor(d), fam: fam, csvHead: csv[0], csvRow: csv[1], csv2: csv2.slice(0, 3), basis: T.basis(d) }}));
"""
    f = tmp_path / "drive.js"
    f.write_text(js)
    out = subprocess.run([appkit.NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["has"] is True and r["head"]["title"] == "Tag the C-terminus for His6" and r["head"]["sev"] == "ok" and "the N-terminus cannot take it" in r["head"]["detail"]
    assert r["conv"] == {"conv": "C", "why": "A VHH's N-terminus lies at the CDR end.", "agree": True, "pick": "C"}
    assert any("from the nearest target atom" in x for x in r["evN"]) and any("Sits on the far side from the paratope" in x for x in r["evN"] + r["evC"])
    assert any(x.startswith("Immobilise through the C-terminus") for x in r["down"])
    assert r["fam"] == [TM.family_pick(ps) for ps in combos]                       # the pages and the exporter judge a family the same way
    assert r["csvHead"].startswith("design_id,run,family,format,pick_his6") and r["csvRow"].startswith("d1,r1,f1,de novo,C,C,C,C,C,")
    assert len(r["csv2"][1].split(",")) == len(r["csvHead"].split(",")) == len(r["csv2"][2].split(","))           # a design that was not judged and one with no free ends keep the columns
    assert "not asked to keep the ends free" in r["df"]["t"] and "own target only" in r["basis"]                 # this block was made without an environment, and says so
