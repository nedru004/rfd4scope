"""Interface analysis (binder_review.interface): a hand-built complex with known geometry, the toy runs (recomputed values stay within
plausible bounds and agree with the table columns that use the same definition), the cache and the process pool."""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import numpy as np
import pytest

from binder_review import interface as I
from binder_review import structure as S
from binder_review.model import CONTACT_CUTOFF


# ------------------------------------------------------------------------------------------------ hand-built complex
def _pdb(residues) -> str:
    """residues: [(chain, number, resname, [(atom name, element, x, y, z)])] -> PDB text."""
    lines, serial = [], 1
    for chain, num, resn, atoms in residues:
        for name, el, x, y, z in atoms:
            nm = (" " + name).ljust(4) if len(name) < 4 else name
            lines.append("ATOM  %5d %4s %3s %1s%4d    %8.3f%8.3f%8.3f%6.2f%6.2f          %2s" % (serial, nm, resn, chain, num, x, y, z, 1.0, 80.0, el))
            serial += 1
        lines.append("TER")
    return "\n".join(lines) + "\nEND\n"


def _bb(x, y0, z=0.0, cb=None, extra=()):
    """Backbone atoms in a column at x (y0..y0+3), an optional CB and extra atoms."""
    atoms = [("N", "N", x, y0, z), ("CA", "C", x, y0 + 1, z), ("C", "C", x, y0 + 2, z), ("O", "O", x, y0 + 3, z)]
    if cb is not None:
        atoms.append(("CB", "C", *cb))
    return atoms + list(extra)


@pytest.fixture()
def hand(tmp_path):
    """Target A10 ALA, A11 SER with a hydrogen, A40 ASP (numbers 12 to 39 missing); binder B1 ALA, B2 ASP, B3 GLY.

    By construction: A10 CB to B1 CB 4.4 A; A11 OG to B2 OD1 3.0 A with H pointing at it (H bond); A40 CB to B3 CA 4.6 A (outside 4.5 A)."""
    target = [
        ("A", 10, "ALA", _bb(0.0, 5.0, cb=(0.0, 0.0, 0.0))),
        ("A", 11, "SER", _bb(20.0, 5.0, cb=(20.0, 3.0, 0.0), extra=[("OG", "O", 20.0, 0.0, 0.0), ("HG", "H", 20.96, 0.0, 0.0)])),
        ("A", 40, "ASP", _bb(0.0, -28.0, cb=(0.0, -30.0, 0.0))),
    ]
    binder = [
        ("B", 1, "ALA", _bb(4.4, -8.0, cb=(4.4, 0.0, 0.0))),
        ("B", 2, "ASP", _bb(23.0, -8.0, cb=(23.0, -3.0, 0.0), extra=[("CG", "C", 23.9, -0.8, 0.0), ("OD1", "O", 23.0, 0.0, 0.0)])),
        ("B", 3, "GLY", [("N", "N", 0.0, -37.0, 0.0), ("CA", "C", 0.0, -34.6, 0.0), ("C", "C", 0.0, -36.0, 0.0), ("O", "O", 0.0, -38.0, 0.0)]),
    ]
    p = tmp_path / "hand.pdb"
    p.write_text(_pdb(target + binder))
    return S.load_complex(str(p), strip_h=False)


def test_hand_contacts_use_the_4_5_cutoff_and_residue_numbers(hand):
    a = I.analyze_design(hand, None, [10, 40, 25], n_points="default")
    assert a.n == 3 and a.n_target == 3 and a.tc == [10, 11] and a.bc == [1, 2]            # A40 / B3 are 4.6 A apart
    assert a.close_tc == [11] and a.close_bc == [2]
    pairs = {(c["t"], c["b"]): c for c in a.contacts}
    assert set(pairs) >= {(10, 1), (11, 2)} and (40, 3) not in pairs and pairs[(10, 1)]["d"] == pytest.approx(4.4, abs=0.01)
    assert a.nc == sum(c["n"] for c in a.contacts)
    cm = a.contact_map
    assert cm["t"] == [10, 11] and cm["b"] == [1, 2] and cm["d"][0][0] == pytest.approx(4.4, abs=0.05) and cm["d"][0][1] is None


def test_hand_hotspots_by_number_and_missing_ones(hand):
    a = I.analyze_design(hand, None, [10, 40, 25], n_points="default")
    assert a.hotspots == [10, 40] and a.meta["hotspots_missing"] == [25]
    assert a.hotspot_dist[10] == pytest.approx(4.4, abs=0.01) and a.hotspot_dist[40] == pytest.approx(4.6, abs=0.01)
    assert a.hot == [10] and a.hot == sorted(set(a.tc) & set(a.hotspots)) and 25 not in a.hotspot_dist


def test_hand_explicit_hydrogen_bond(hand):
    a = I.analyze_design(hand, None, [], n_points="default")
    assert a.hbond_basis == "partial" and len(a.hbonds) == 1 and a.n_hbonds == 1
    h = a.hbonds[0]
    assert (h["t"], h["ta"], h["b"], h["ba"], h["k"]) == (11, "OG", 2, "OD1", "D>A") and h["d"] == pytest.approx(3.0, abs=0.01)
    assert (h["tn"], h["bn"]) == ("SER", "ASP")


def test_hand_per_residue_arrays_follow_the_sequence(hand):
    a = I.analyze_design(hand, None, [], n_points="default")
    for key in ("dsasa_res", "rsa_res", "plddt_res"):
        assert len(getattr(a, key)) == 3, key
    assert len(a.ss) == 3 and set(a.ss) <= set("HEL")
    assert all(len(a.tracks[k]) == 3 for k in ("plddt", "rsa", "dsasa", "iface", "n_target_res", "n_atom_pairs", "dist", "kd"))
    assert a.tracks["iface"] == [1, 1, 0] and set(a.t_dsasa) == {10, 11, 40}                  # keyed by residue number: nothing at 12 to 39
    assert a.t_dsasa[10] > 0 and all(v >= 0 for v in a.t_dsasa.values())
    assert a.plddt_res == [80.0, 80.0, 80.0] and a.plddt_t[10] == 80.0 and a.seq_props["length"] == 3
    assert a.dsasa == pytest.approx(sum(a.dsasa_res) + sum(a.t_dsasa.values()), rel=1e-6)


def test_hand_termini_and_center(hand):
    a = I.analyze_design(hand, None, [], n_points="default")
    assert a.nterm_d == pytest.approx(min(np.hypot(4.4, 7.0), np.hypot(4.4, 12.0)), abs=0.2)
    assert a.termini["n"]["contact"] is True and a.termini["c"]["contact"] is False and len(a.center) == 3 and all(np.isfinite(a.center))


def test_json_round_trip_keeps_integer_keys(hand):
    a = I.analyze_design(hand, None, [10], n_points="default")
    b = I.DesignAnalysis.from_dict(json.loads(json.dumps(a.to_dict())))
    assert b.t_dsasa == a.t_dsasa and set(b.hotspot_dist) == {10} and b.to_dict() == a.to_dict()


def test_hand_without_contact_and_without_design(tmp_path):
    far = [("A", 5, "ALA", _bb(0.0, 0.0, cb=(0.0, -2.0, 0.0))), ("B", 1, "ALA", _bb(40.0, 0.0, cb=(40.0, -2.0, 0.0)))]
    p = tmp_path / "far.pdb"
    p.write_text(_pdb(far))
    a = I.analyze_design(S.load_complex(str(p), strip_h=False), None, [5])
    assert a.tc == [] and a.bc == [] and a.nc == 0 and a.hot == [] and a.n_hbonds == 0 and a.contact_map == {"t": [], "b": [], "d": []}
    assert a.loop_frac == 0.0 and a.iface_helix == 0.0 and a.dsasa == pytest.approx(0.0, abs=1e-6)
    assert a.hotspot_dist[5] > 30 and a.vs_bindcraft == {} and a.hbond_basis == "computed" and a.unsat_density == 0.0


def test_hand_disulfide_is_found_and_not_called_unpaired(tmp_path):
    t = [("A", 1, "ALA", _bb(0.0, 0.0, cb=(0.0, -2.0, 0.0)))]
    b = [("B", 1, "CYS", _bb(3.0, 0.0, cb=(3.0, -1.0, 0.0), extra=[("SG", "S", 3.0, -3.0, 0.0)])),
         ("B", 2, "ALA", _bb(10.0, 0.0, cb=(10.0, -2.0, 0.0))),
         ("B", 3, "CYS", _bb(3.0, 8.0, cb=(3.0, 6.0, 0.0), extra=[("SG", "S", 3.0, -5.05, 0.0)]))]
    p = tmp_path / "ss.pdb"
    p.write_text(_pdb(t + b))
    a = I.analyze_design(S.load_complex(str(p), strip_h=False), None, [], n_points="default")
    assert a.ssbonds == [[1, 3]] and [e for e in a.liab if e["kind"] == "Unpaired cysteine"] == [] and a.seq_props["n_cys"] == 2


def test_composition_counts():
    c = I.composition(list("AKRDEFWYLS"))
    assert c["n"] == 10 and c["hydrophobic"] == 5 and c["aromatic"] == 3 and c["positive"] == 2 and c["negative"] == 2
    assert c["charged"] == 4 and c["polar"] == 1 and c["aa"]["K"] == 1 and sum(c["aa"].values()) == 10 and I.composition([])["hydrophobic_frac"] == 0.0


# ------------------------------------------------------------------------------------------------ the toy runs
def _designs(run):
    return run.accepted() + run.rejected_with_structure()


@pytest.fixture(scope="module")
def toy_results(toy_run1, toy_run2):
    ds = _designs(toy_run1) + _designs(toy_run2)
    hs = {toy_run1.run_id: toy_run1.settings.hotspots, toy_run2.run_id: toy_run2.settings.hotspots}
    return ds, hs, I.analyze_run(ds, n_jobs=1, hotspots=hs)


def test_hot_equals_contacts_intersect_hotspots_and_matches_the_truth(toy_results, toy_truth):
    ds, hs, res = toy_results
    assert len(res) == len(ds) == 12 + 8 + 10
    kinds = {rid: toy_truth["design_kind"][rid] for rid in ("run01", "run02")}
    n_hot = n_away = 0
    for d in ds:
        a = res[d.id]
        assert a.hot == sorted(set(a.tc) & set(hs[d.run_id])) and a.run_id == d.run_id and a.id == d.id
        assert set(a.hotspots) == set(hs[d.run_id])                          # every hotspot exists in both targets
        kind = kinds[d.run_id][d.id]
        if kind == "clean_hot":
            n_hot += 1
            assert len(a.hot) >= 1, d.id
        if kind == "clean_away":
            n_away += 1
            assert a.hot == [], d.id
    assert n_hot >= 4 and n_away >= 4


def test_run2_has_no_residue_in_the_deleted_loops(toy_results, toy_run2, toy_truth):
    ds, hs, res = toy_results
    gaps = [tuple(g) for g in toy_truth["deleted_construct"]]
    for d in _designs(toy_run2):
        a = res[d.id]
        assert a.n_target == 102
        for lo, hi in gaps:
            assert not any(lo <= t <= hi for t in a.tc) and not any(lo <= t <= hi for t in a.t_dsasa)
            assert not any(lo <= t <= hi for t in a.plddt_t) and not any(lo <= c["t"] <= hi for c in a.contacts)
            assert not any(lo <= b["resi"] <= hi for b in a.buns if b["side"] == "t")


def test_internal_consistency_and_plausible_bounds(toy_results):
    ds, hs, res = toy_results
    for d in ds:
        a = res[d.id]
        assert a.n == d.length and len(a.ss) == a.n == len(a.rsa_res) == len(a.dsasa_res)
        assert set(a.tc) == {c["t"] for c in a.contacts} and set(a.bc) == {c["b"] for c in a.contacts}
        assert set(a.close_bc) <= set(a.bc) and set(a.close_tc) <= set(a.tc)
        assert a.n_hbonds == len(a.hbonds) and a.n_salt == len(a.salt) and a.n_unsat == len(a.buns) == a.n_unsat_binder + a.n_unsat_target
        assert 0.0 <= a.loop_frac <= 1.0 and 0.0 <= a.helix_frac <= 1.0 and a.helix_frac + a.strand_frac <= 1.0 and 0.0 <= a.hyd_frac <= 1.0
        assert 100 < a.dsasa < 4000 and a.hphob_patch >= 0 and len(a.patches) <= 3
        assert all(p["area"] > 0 and p["res"] for p in a.patches) and [p["area"] for p in a.patches] == sorted((p["area"] for p in a.patches), reverse=True)
        assert all(1 <= b <= a.n for b in a.bc) and all(1 <= h["b"] <= a.n for h in a.hbonds) and all(s["d"] <= 4.0 for s in a.salt)
        assert all(0 <= x <= 100 for x in a.plddt_res if x is not None) and len(a.center) == 3 and a.seq_props["length"] == a.n
        assert 0 <= a.n_hbonds <= len(a.bc) * 6 and 0 <= a.nterm_d < 200 and 0 <= a.cterm_d < 200
        assert a.helix_frac > 0.7                                            # the toy binders are helical bundles
        json.dumps(a.to_dict())


def test_contacts_agree_with_the_structure_module(toy_results):
    ds, hs, res = toy_results
    for d in ds[:6]:
        cx = S.load_complex(d.structure_path)
        c = S.residue_contacts(cx.target, cx.binder, CONTACT_CUTOFF)
        a = res[d.id]
        assert a.tc == c.a_residues and a.bc == c.b_residues and a.nc == c.n_atom_contacts and a.bindcraft_bc == S.bindcraft_interface_residues(cx)


def test_bindcraft_interface_equals_the_csv_for_model2_files(toy_results):
    ds, hs, res = toy_results
    checked = 0
    for d in ds:
        if d.structure_model == 2 and d.interface_residues:
            checked += 1
            assert res[d.id].vs_bindcraft["InterfaceResidues"]["exact"] and res[d.id].bindcraft_bc == sorted(int(x[1:]) for x in d.interface_residues)
    assert checked >= 2


def test_vs_bindcraft_block_agrees_where_the_definition_is_the_same(toy_results):
    ds, hs, res = toy_results
    for d in ds:
        v = res[d.id].vs_bindcraft
        for stem in ("n_InterfaceResidues", "Interface_Hydrophobicity", "n_InterfaceHbonds", "n_InterfaceUnsatHbonds", "dSASA", "Binder_Helix%",
                     "Surface_Hydrophobicity", "InterfaceResidues"):
            assert stem in v, stem
        assert v["n_InterfaceResidues"]["recomputed"] == len(res[d.id].bindcraft_bc) and v["n_InterfaceResidues"]["diff"] == 0.0     # the CSV is per model
        assert v["Interface_Hydrophobicity"]["diff"] == pytest.approx(0.0, abs=0.01)
        for stem in ("Hotspot_RMSD", "dG", "ShapeComplementarity", "PackStat", "i_pAE"):
            assert v[stem]["recomputed"] is None and v[stem]["note"].startswith("not recomputable") and v[stem]["bindcraft"] is not None


def test_dsasa_and_helix_content_track_the_csv(toy_results):
    ds, hs, res = toy_results
    mine, ref, hx, hxref = [], [], [], []
    for d in ds:
        v = res[d.id].vs_bindcraft
        mine.append(v["dSASA"]["recomputed"])
        ref.append(v["dSASA"]["bindcraft"])
        hx.append(v["Binder_Helix%"]["recomputed"])
        hxref.append(v["Binder_Helix%"]["bindcraft"])
    assert np.corrcoef(mine, ref)[0, 1] > 0.9 and 0.8 < np.median(np.array(mine) / np.array(ref)) < 1.25
    assert np.abs(np.array(hx) - np.array(hxref)).mean() < 10.0                  # percent; the CSV counts the designed helices


def test_liabilities_are_attached_and_consistent(toy_results):
    ds, hs, res = toy_results
    for d in ds:
        a = res[d.id]
        assert a.seq_props["n_cys"] == d.sequence.count("C")
        for e in a.liab:
            assert 1 <= e["pos"] <= a.n and e["sev"] in ("bad", "warn", "info")
        assert [t["pos"] for t in a.tracks["liab"]] == sorted({e["pos"] for e in a.liab})


def test_analysis_is_deterministic_and_fast(toy_run1):
    d = toy_run1.accepted()[0]
    a = I.analyze_design(I.load_for_analysis(d.structure_path), d, toy_run1.settings.hotspots).to_dict()
    t0 = time.process_time()
    b = I.analyze_design(I.load_for_analysis(d.structure_path), d, toy_run1.settings.hotspots).to_dict()
    assert a == b and time.process_time() - t0 < 3.0                              # CPU time: a busy machine stretches wall-clock time


def test_heavy_atom_file_works_and_uses_computed_hydrogens(toy_run1):
    d = toy_run1.accepted()[0]
    a = I.analyze_design(I.load_for_analysis(d.structure_path), d, toy_run1.settings.hotspots)
    assert a.hbond_basis == "computed" and a.meta["hydrogens"] is False          # the toy files carry heavy atoms only


# ------------------------------------------------------------------------------------------------ cache and pool
def test_cache_hit_skips_the_analysis_and_a_changed_input_misses(toy_run1, tmp_path, monkeypatch):
    ds = toy_run1.accepted()[:2]
    hs = {toy_run1.run_id: toy_run1.settings.hotspots}
    cache = tmp_path / "cache"
    first = I.analyze_run(ds, n_jobs=1, hotspots=hs, cache_dir=cache)
    assert sorted(p.name for p in (cache / toy_run1.run_id).glob("*.json")) == sorted(f"{d.id}.json" for d in ds)

    def boom(*a, **k):
        raise AssertionError("analysis ran on a cache hit")

    monkeypatch.setattr(I, "analyze_design", boom)
    warm = I.analyze_run(ds, n_jobs=1, hotspots=hs, cache_dir=cache)
    assert {k: v.to_dict() for k, v in warm.items()} == {k: v.to_dict() for k, v in first.items()}
    with pytest.raises(AssertionError):                                           # other hotspots: the key changes
        I.analyze_run(ds, n_jobs=1, hotspots={toy_run1.run_id: toy_run1.settings.hotspots[:1]}, cache_dir=cache)


def test_pool_equals_serial(toy_run1):
    ds = toy_run1.accepted()[:4]
    hs = {toy_run1.run_id: toy_run1.settings.hotspots}
    serial = I.analyze_run(ds, n_jobs=1, hotspots=hs)
    pooled = I.analyze_run(ds, n_jobs=2, hotspots=hs)
    assert {k: v.to_dict() for k, v in serial.items()} == {k: v.to_dict() for k, v in pooled.items()}


def test_run_skips_designs_without_structure_and_prefixes_clashing_ids(toy_run1, toy_run2):
    d1, d2 = toy_run1.accepted()[0], toy_run2.accepted()[0]
    d2c = dataclasses.replace(d2, id=d1.id)
    res = I.analyze_run([d1, d2c, dataclasses.replace(d1, id="nostruct", structure_path=None)], n_jobs=1,
                        hotspots={toy_run1.run_id: toy_run1.settings.hotspots, toy_run2.run_id: toy_run2.settings.hotspots})
    assert set(res) == {f"{toy_run1.run_id}/{d1.id}", f"{toy_run2.run_id}/{d1.id}"}


def test_analyze_run_progress_and_callable_hotspots(toy_run1):
    ds = toy_run1.accepted()[:2]
    seen = []
    res = I.analyze_run(ds, n_jobs=1, hotspots=lambda d: [20], progress=lambda a, b: seen.append((a, b)))
    assert seen[-1] == (2, 2) and all(a.hotspots == [20] for a in res.values())
    assert all(a.hotspots == [20, 21] for a in I.analyze_run(ds, n_jobs=1, hotspots=[20, 21]).values())
