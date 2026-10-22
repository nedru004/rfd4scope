"""The chains of a reference structure: which one is the target, which are partners, and why (docs/target_frame.md, "Chains of the reference").

A reference that holds more than the target chain is not one situation but several, and each is told apart from the others here:
copies of the target in the file, copies the assembly only describes (symmetry operators), a different molecule in the file, a chain
that the assembly does not hold (a neighbour in the crystal), a construct that is not in one chain, and everything that is not a polymer
(ligands, ions, additives, waters). The variants are made from the toy reference, so the right answer is known in advance."""
from __future__ import annotations

import json

import gemmi
import numpy as np
import pytest

from binder_review import cli
from binder_review import structure as S
from binder_review import target_frame as TF


# ---------------------------------------------------------------------------------------------------------------- making variants
def _read(path):
    st = gemmi.read_structure(str(path))
    st.setup_entities()
    return st


def _copy(chain, name, *, renumber=0, R=None, t=None, scramble=False):
    """A copy of a gemmi chain: renamed, renumbered, moved by x' = R x + t, or with every residue type changed (another molecule)."""
    R = np.eye(3) if R is None else np.asarray(R)
    t = np.zeros(3) if t is None else np.asarray(t)
    new = gemmi.Chain(name)
    names = sorted({r.name for r in chain})
    for r in chain:
        r2 = gemmi.Residue()
        r2.name = names[(names.index(r.name) + 7) % len(names)] if scramble else r.name
        r2.het_flag, r2.seqid = r.het_flag, gemmi.SeqId(r.seqid.num + renumber, " ")
        for a in r:
            a2 = gemmi.Atom()
            a2.name, a2.element, a2.b_iso, a2.occ = a.name, a.element, a.b_iso, 1.0
            a2.pos = gemmi.Position(*(R @ np.array([a.pos.x, a.pos.y, a.pos.z]) + t))
            r2.add_atom(a2)
        new.add_residue(r2)
    return new


def _hetero(name, atoms, number):
    r = gemmi.Residue()
    r.name, r.het_flag, r.seqid = name, "H", gemmi.SeqId(number, " ")
    for an, el, pos in atoms:
        a = gemmi.Atom()
        a.name, a.element, a.b_iso, a.occ = an, gemmi.Element(el), 30.0, 1.0
        a.pos = gemmi.Position(*pos)
        r.add_atom(a)
    return r


def _write(path, chains, assemblies=None):
    """An mmCIF of the chains. ``assemblies``: {id: (oligomeric details, operator expression, [chain names], {operator id: (R, t)})}."""
    st = gemmi.Structure()
    st.cell, st.spacegroup_hm, st.name = gemmi.UnitCell(300, 300, 300, 90, 90, 90), "P 1", "TOY1"
    model = gemmi.Model("1")
    for ch in chains:
        model.add_chain(ch)
    st.add_model(model)
    st.setup_entities()
    st.assign_label_seq_id()
    doc = st.make_mmcif_document()
    block = doc[0]
    block.set_pair("_exptl.method", "'X-RAY DIFFRACTION'")
    if assemblies:
        sub = {ch.name: ch.get_polymer().subchain_id() for ch in st[0] if len(ch.get_polymer())}
        loop = block.init_loop("_pdbx_struct_assembly.", ["id", "details", "method_details", "oligomeric_details", "oligomeric_count"])
        for aid, (details, *_rest) in assemblies.items():
            loop.add_row([aid, "software_defined_assembly", "PISA", details, "2"])
        loop = block.init_loop("_pdbx_struct_assembly_gen.", ["assembly_id", "oper_expression", "asym_id_list"])
        for aid, (_d, opers, names, _ops) in assemblies.items():
            loop.add_row([aid, opers, ",".join(sub[c] for c in names)])
        ops = {}
        for _d, _o, _n, o in assemblies.values():
            ops.update(o)
        loop = block.init_loop("_pdbx_struct_oper_list.", ["id", "type", "name", "symmetry_operation",
                                                          "matrix[1][1]", "matrix[1][2]", "matrix[1][3]", "vector[1]",
                                                          "matrix[2][1]", "matrix[2][2]", "matrix[2][3]", "vector[2]",
                                                          "matrix[3][1]", "matrix[3][2]", "matrix[3][3]", "vector[3]"])
        for oid, (R, t) in ops.items():
            ident = np.allclose(R, np.eye(3)) and np.allclose(t, 0)
            loop.add_row([oid, "'identity operation'" if ident else "'crystal symmetry operation'", "?", "?"] +
                         [f"{v:.6f}" for k in range(3) for v in (*R[k], t[k])])
    doc.write_file(str(path))
    return path


@pytest.fixture(scope="module")
def toy_chains(toy_reference):
    """The toy reference's chains A and B as gemmi chains, and the rigid motion that takes A onto B."""
    st = _read(toy_reference)
    A, B = st[0]["A"], st[0]["B"]
    sf = S.read_structure(str(toy_reference))
    a, b = sf.groups["A"].ca_by_number(), sf.groups["B"].ca_by_number()
    common = sorted(set(a) & set(b))
    R, t, rms = S.kabsch(np.array([a[n] for n in common]), np.array([b[n] for n in common]))
    assert rms < 0.05                                                   # the toy dimer's second chain is a rigid copy of the first
    return A, B, R, t


@pytest.fixture(scope="module")
def construct_atoms(toy_construct):
    return TF._read_construct(str(toy_construct))


def _chains(data):
    return {r["chain"]: r for r in data["chains"]}


# ---------------------------------------------------------------------------------------------------------------- the toy dimer
def test_a_dimer_in_the_file_takes_the_first_chain_and_says_why(toy_reference, construct_atoms):
    comp = TF.analyse_reference(toy_reference, None, construct_atoms=construct_atoms)
    d = comp.data
    assert (comp.target, comp.partners, comp.problem) == ("A", ["B"], None)
    assert d["target"]["how"] == "first_of_equal" and "first in the file" in d["target"]["why"] and d["target"]["alternatives"] == ["B"]
    assert _chains(d)["B"]["role"] == "subunit" and _chains(d)["B"]["used"] and d["cases"] == ["subunits_in_file", "target_ambiguous"]
    assert d["assembly"]["records"] is False and "no assembly records" in "\n".join(TF.format_reference_chains(d))
    assert d["partners"]["mode"] == "auto" and "the same protein as the target" in d["partners"]["why"]


def test_naming_the_chain_is_obeyed_and_a_chain_that_does_not_carry_the_construct_is_flagged(toy_reference, construct_atoms):
    ok = TF.analyse_reference(toy_reference, None, chain="B", construct_atoms=construct_atoms)
    assert ok.target == "B" and ok.partners == ["A"] and ok.data["target"]["how"] == "given" and not ok.data["target"]["warning"]
    bad = TF.analyse_reference(toy_reference, None, chain="Q", construct_atoms=construct_atoms)
    assert bad.target is None and "chain 'Q' is not a polymer chain of the file" in bad.problem and bad.data["name_the_chain"]


# ---------------------------------------------------------------------------------------------------------------- the target is not chain A
@pytest.fixture(scope="module")
def decoy_first(tmp_path_factory, toy_chains):
    """A = another molecule (the target's coordinates with other residue types), B = the target, C = a second copy of the target."""
    A, B, R, t = toy_chains
    path = tmp_path_factory.mktemp("decoy") / "decoy_first.cif"
    return _write(path, [_copy(A, "A", scramble=True, t=[0, 0, 60.0]), _copy(A, "B"), _copy(B, "C")])


def test_the_target_is_found_by_its_sequence_wherever_it_sits(decoy_first, construct_atoms):
    comp = TF.analyse_reference(decoy_first, None, construct_atoms=construct_atoms)
    d = comp.data
    assert (comp.target, comp.partners) == ("B", ["C"]) and d["target"]["how"] == "first_of_equal"
    rows = _chains(d)
    assert rows["A"]["role"] == "other_molecule" and not rows["A"]["used"] and rows["C"]["role"] == "subunit" and rows["C"]["used"]
    assert "other_molecules" in d["cases"] and "Not counted: chain A (another molecule" in d["summary"]


def test_another_molecule_is_counted_only_when_it_is_named_or_all_is_asked(decoy_first, construct_atoms):
    asked = TF.analyse_reference(decoy_first, None, partner_chains="all", construct_atoms=construct_atoms)
    assert asked.partners == ["C", "A"]                                         # subunits first: the first partner is the one the two-fold operator is read from
    named = TF.analyse_reference(decoy_first, None, partner_chains=["A"], construct_atoms=construct_atoms)
    assert named.partners == ["A"] and named.data["partners"]["mode"] == "given" and "Counted as a partner because it was asked for" in _chains(named.data)["A"]["why"]
    none = TF.analyse_reference(decoy_first, None, partner_chains=(), construct_atoms=construct_atoms)
    assert none.partners == [] and none.data["partners"]["mode"] == "none" and "as asked" in none.data["partners"]["why"]
    bad = TF.analyse_reference(decoy_first, None, partner_chains=["B"], construct_atoms=construct_atoms)
    assert "cannot also be its partner" in bad.problem
    assert "is not a polymer chain" in TF.analyse_reference(decoy_first, None, partner_chains=["Z"], construct_atoms=construct_atoms).problem


def test_a_chain_asked_for_that_does_not_hold_the_construct_names_the_one_that_does(decoy_first, construct_atoms):
    comp = TF.analyse_reference(decoy_first, None, chain="A", construct_atoms=construct_atoms)
    assert comp.target == "A" and "holds only" in comp.data["target"]["warning"] and "chain B holds 110" in comp.data["target"]["warning"]


def test_the_frame_is_built_on_the_chain_the_sequence_found(decoy_first, toy_construct, toy_run1):
    frame = TF.build_frame(decoy_first, toy_construct, {"run01": toy_run1}, None)
    assert frame.meta["chain"] == "B" and frame.partner_chains == ["C"] and frame.composition["target"]["chain"] == "B"
    assert frame.partner_role("C") == "subunit" and frame.meta["reference"]["assembly"] is None and frame.meta["reference"]["label"] == "decoy_first chain B"
    assert any(n.startswith("reference chains: Target: chain B") for n in frame.meta["notes"])
    olig = frame.inspector_block(["run01"])["olig"]
    assert olig["roles"] == {"C": "subunit"} and olig["labels"]["C"] == "other subunit (chain C of decoy_first)" and olig["source"] == "decoy_first"
    with pytest.raises(TF.FrameError, match="name the chain of the target with --reference-chain"):
        TF.build_frame(decoy_first, toy_construct, {"run01": toy_run1}, None, chain="Q")
    with pytest.raises(TF.FrameError, match="chain B carries it"):
        TF.build_frame(decoy_first, toy_construct, {"run01": toy_run1}, None, chain="A")


def test_partners_other_than_subunits_have_no_two_fold_operator(decoy_first, toy_construct, toy_run1):
    """The operator that takes the target onto its first partner is a statement about copies of one molecule. Another molecule has none."""
    other = TF.build_frame(decoy_first, toy_construct, {"run01": toy_run1}, None, partner_chains=["A"])
    assert other.partner_role("A") == "other_molecule" and "two_fold" not in other.meta["dimer"]
    both = TF.build_frame(decoy_first, toy_construct, {"run01": toy_run1}, None, partner_chains="all")
    assert both.partner_chains == ["C", "A"] and both.meta["dimer"]["two_fold"]["rmsd"] < 0.1
    assert both.meta["composition"]["partners"]["mode"] == "all"


# ---------------------------------------------------------------------------------------------------------------- numbering of the other subunit
def test_a_subunit_numbered_differently_is_still_found_and_its_operator_read(tmp_path, toy_chains, toy_construct, toy_run1, toy_reference):
    A, B, R, t = toy_chains
    path = _write(tmp_path / "renumbered.cif", [_copy(A, "A"), _copy(B, "B", renumber=300)])
    frame = TF.build_frame(path, toy_construct, {"run01": toy_run1}, None)
    plain = TF.build_frame(toy_reference, toy_construct, {"run01": toy_run1}, None)
    assert frame.partner_chains == ["B"] and frame.partner_role("B") == "subunit"
    tf, tp = frame.meta["dimer"]["two_fold"], plain.meta["dimer"]["two_fold"]
    assert tf["n"] == tp["n"] and tf["rmsd"] == pytest.approx(tp["rmsd"], abs=1e-3) and tf["angle"] == pytest.approx(tp["angle"], abs=0.05)
    assert frame.meta["dimer"]["bsa_total"] == pytest.approx(plain.meta["dimer"]["bsa_total"], abs=0.5)


# ---------------------------------------------------------------------------------------------------------------- assemblies
@pytest.fixture(scope="module")
def by_symmetry(tmp_path_factory, toy_chains):
    """One chain in the file; assembly 1 is that chain and its copy under the operator that takes A onto the toy's B."""
    A, B, R, t = toy_chains
    ops = {"1": (np.eye(3), np.zeros(3)), "2": (R, t)}
    return _write(tmp_path_factory.mktemp("sym") / "one_chain.cif", [_copy(A, "A")], {"1": ("dimeric", "1,2", ["A"], ops)}), R, t


def test_the_copies_an_assembly_describes_are_made_and_are_where_the_operator_puts_them(by_symmetry, construct_atoms, toy_chains):
    path, R, t = by_symmetry
    comp = TF.analyse_reference(path, None, construct_atoms=construct_atoms)
    d = comp.data
    assert (comp.target, comp.partners) == ("A", ["A-2"]) and d["assembly"]["generated"] == ["A-2"] and d["cases"] == ["subunits_by_symmetry"]
    row = _chains(d)["A-2"]
    assert row["generated"] and row["copy_of"] == "A" and row["operator"] == "2" and row["role"] == "subunit" and "makes with operator 2" in row["why"]
    moved = comp.atoms("A-2")
    sf = S.read_structure(str(path))
    assert np.allclose(moved.xyz, sf.groups["A"].heavy().xyz @ R.T + t, atol=1e-3) and set(moved.chain) == {"A-2"}
    assert "made 1 copy from its symmetry operators: A-2" in "\n".join(TF.format_reference_chains(d))


def test_a_frame_with_the_assembly_made_copy_measures_what_a_frame_with_the_chain_in_the_file_measures(by_symmetry, toy_reference, toy_construct, toy_run1):
    path, R, t = by_symmetry
    made = TF.build_frame(path, toy_construct, {"run01": toy_run1}, None)
    held = TF.build_frame(toy_reference, toy_construct, {"run01": toy_run1}, None)
    assert made.partner_chains == ["A-2"] and held.partner_chains == ["B"]
    assert made.meta["reference"]["assembly"] == "1" and made.meta["reference"]["label"] == "one_chain chain A, assembly 1 (dimer)"
    for design in toy_run1.accepted()[:4]:
        a, b = TF.check_design(made, design.structure_path, "run01").to_dict(), TF.check_design(held, design.structure_path, "run01").to_dict()
        for k in ("olig_min", "olig_n3", "olig_n4", "olig_n", "rest_min", "rest_n3"):
            assert a["metrics"][k] == pytest.approx(b["metrics"][k], abs=0.05), (design.id, k)
    assert made.meta["dimer"]["two_fold"]["rmsd"] == pytest.approx(held.meta["dimer"]["two_fold"]["rmsd"], abs=0.02)


@pytest.fixture(scope="module")
def two_assemblies(tmp_path_factory, toy_chains):
    """Chains A, B, C: A and B are assembly 1, A and C assembly 2 (C is the same protein, a neighbour in the crystal), D is another molecule in assembly 1."""
    A, B, R, t = toy_chains
    ident = {"1": (np.eye(3), np.zeros(3))}
    chains = [_copy(A, "A"), _copy(B, "B"), _copy(B, "C", t=[40.0, 0, 0]), _copy(A, "D", scramble=True, t=[0, 0, 70.0])]
    return _write(tmp_path_factory.mktemp("asm") / "two_assemblies.cif", chains,
                  {"1": ("trimeric", "1", ["A", "B", "D"], ident), "2": ("dimeric", "1", ["A", "C"], ident)})


def test_a_copy_of_the_target_outside_its_assembly_is_a_crystal_neighbour_and_not_a_partner(two_assemblies, construct_atoms):
    comp = TF.analyse_reference(two_assemblies, None, construct_atoms=construct_atoms)
    rows = _chains(comp.data)
    assert (comp.target, comp.partners) == ("A", ["B"]) and comp.data["assembly"]["id"] == "1"
    assert rows["C"]["role"] == "outside_assembly" and not rows["C"]["used"] and "neighbour in the crystal" in rows["C"]["why"]
    assert rows["D"]["role"] == "other_molecule" and not rows["D"]["used"]
    assert comp.data["cases"] == ["subunits_in_file", "other_molecules", "crystal_neighbours", "target_ambiguous"]
    second = TF.analyse_reference(two_assemblies, None, assembly_id="2", construct_atoms=construct_atoms)
    assert second.partners == ["C"] and _chains(second.data)["B"]["role"] == "outside_assembly"
    assert "has no assembly 9" in TF.analyse_reference(two_assemblies, None, assembly_id="9", construct_atoms=construct_atoms).problem
    every = TF.analyse_reference(two_assemblies, None, partner_chains="all", construct_atoms=construct_atoms)
    assert every.partners == ["B", "D"]                                             # the crystal neighbour is not part of "all": it is not in the assembly
    named = TF.analyse_reference(two_assemblies, None, partner_chains=["C"], construct_atoms=construct_atoms)
    assert named.partners == ["C"] and any("not part of assembly 1, but it was asked for" in n for n in named.data["notes"])


# ---------------------------------------------------------------------------------------------------------------- what is not a chain
@pytest.fixture(scope="module")
def with_additives(tmp_path_factory, toy_chains, construct_atoms):
    """The toy dimer with a glycerol lying on the target's surface, a zinc ion beside it and waters. All are on the target chain's own residue numbers' side."""
    A, B, R, t = toy_chains
    a = _copy(A, "A")
    surface = np.array([[x.pos.x, x.pos.y, x.pos.z] for r in a for x in r if x.name == "CA"])
    hot = surface[40] + 1.5 * (surface[40] - surface.mean(axis=0)) / np.linalg.norm(surface[40] - surface.mean(axis=0))
    a.add_residue(_hetero("GOL", [("C1", "C", hot), ("O1", "O", hot + [1.2, 0, 0]), ("C2", "C", hot + [0, 1.4, 0]), ("O2", "O", hot + [0, 1.4, 1.3])], 901))
    a.add_residue(_hetero("ZN", [("ZN", "Zn", hot + [2.0, 2.0, 1.0])], 902))
    for k in range(4):
        a.add_residue(_hetero("HOH", [("O", "O", hot + [0, 0, 5.0 + k])], 950 + k))
    return _write(tmp_path_factory.mktemp("het") / "additives.cif", [a, _copy(B, "B")])


def test_ligands_ions_and_waters_are_listed_and_never_partners(with_additives, construct_atoms):
    comp = TF.analyse_reference(with_additives, None, partner_chains="all", construct_atoms=construct_atoms)
    d = comp.data
    names = {r["name"]: r for r in d["non_polymers"]}
    assert comp.partners == ["B"] and set(names) == {"GOL", "ZN"} and d["waters"] == 4
    assert names["GOL"]["kind"] == "ligand" and names["GOL"]["hint"] and names["GOL"]["n_touch"] >= 1 and names["ZN"]["kind"] == "ion"
    assert "never counted as partners" in "\n".join(TF.format_reference_chains(d)) and "4 waters" in "\n".join(TF.format_reference_chains(d))


def test_additives_change_nothing_the_frame_measures(with_additives, toy_chains, tmp_path, toy_construct, toy_run1):
    A, B, R, t = toy_chains
    clean = _write(tmp_path / "clean.cif", [_copy(A, "A"), _copy(B, "B")])
    a = TF.build_frame(with_additives, toy_construct, {"run01": toy_run1}, None)
    b = TF.build_frame(clean, toy_construct, {"run01": toy_run1}, None)
    for design in toy_run1.accepted()[:3]:
        x, y = TF.check_design(a, design.structure_path, "run01").to_dict(), TF.check_design(b, design.structure_path, "run01").to_dict()
        assert x["metrics"] == y["metrics"] and x["verdicts"] == y["verdicts"], design.id
    called_out = [n for n in a.meta["notes"] if "bound to the target and not measured" in n]
    assert len(called_out) == 1 and "ZN" in called_out[0] and "GOL" not in called_out[0]                         # an ion at the target is said; a glycerol is named like an additive

# ---------------------------------------------------------------------------------------------------------------- a construct that is not in one chain
def test_a_construct_that_no_single_chain_holds_is_said_so(tmp_path, toy_chains, construct_atoms, toy_construct, toy_run1):
    A, B, R, t = toy_chains
    first, second = gemmi.Chain("A"), gemmi.Chain("B")
    for r in A:
        (first if r.seqid.num < 100 else second).add_residue(r)
    split = _write(tmp_path / "split.cif", [first, second])
    comp = TF.analyse_reference(split, None, construct_atoms=construct_atoms)
    assert comp.target is None and "split over chains A and B" in comp.problem and {r["role"] for r in comp.data["chains"]} == {"unassigned"}
    with pytest.raises(TF.FrameError, match="split over chains A and B"):
        TF.build_frame(split, toy_construct, {"run01": toy_run1}, None)
    wrong = _write(tmp_path / "wrong.cif", [_copy(A, "A", scramble=True)])
    assert "no chain of wrong.cif carries the construct" in TF.analyse_reference(wrong, None, construct_atoms=construct_atoms).problem


def test_without_a_construct_a_single_protein_chain_is_the_target_and_several_need_a_name(toy_reference, toy_construct):
    assert TF.analyse_reference(toy_construct).target == "A"
    comp = TF.analyse_reference(toy_reference, None)
    assert comp.target is None and "name the chain of the target" in comp.problem


# ---------------------------------------------------------------------------------------------------------------- what a finding says
def test_a_clash_with_another_subunit_is_a_different_claim_from_a_clash_with_the_rest_of_the_chain():
    metrics = {"olig_min": 2.0, "olig_n3": 12, "olig_n4": 30, "olig_n": 0, "rest_min": 2.0, "rest_n3": 12, "rest_n4": 20}
    pair = {"partner": ["B", 7, "ALA", "CB"], "binder": [1, "ALA", "CB"], "d": 2.0}
    sub = TF.verdicts(metrics, {"partner_role": "subunit", "partner_roles": ["subunit"], "partner_pair": pair})
    oth = TF.verdicts(metrics, {"partner_role": "other_molecule", "partner_roles": ["other_molecule"], "partner_pair": pair})
    old = TF.verdicts(metrics)
    assert sub["partner"]["reason"] == ("12 binder atoms within 3 A of another subunit of the assembly (closest 2.0 A, chain B): "
                                        "the binder would not fit on the assembled protein")
    assert oth["partner"]["reason"].startswith("12 binder atoms within 3 A of a different molecule of the complex (closest 2.0 A, chain B)")
    assert oth["partner"]["reason"].endswith("would compete with it for this site")
    assert old["partner"]["reason"] == "12 binder atoms within 3 A of the other subunit (closest 2.0 A)"           # a frame that records no roles says what it always said
    assert sub["rest"]["reason"].startswith("12 binder atoms within 3 A of the rest of the target chain, the part the construct left out") and "full-length protein" in sub["rest"]["reason"]
    assert "other subunit" not in sub["rest"]["reason"] and "assembled protein" not in sub["rest"]["reason"]


def test_the_findings_of_a_design_carry_the_kind_of_partner_it_ran_into(toy_project):
    """Every pose record of the toy build says what the nearest partner chain is."""
    rec = next(r for r in toy_project.poses.values() if r["metrics"].get("olig_min") is not None)
    assert rec["diag"]["partner_role"] == "subunit" and rec["diag"]["partner_roles"] and set(rec["diag"]["partner_roles"]) <= {"subunit"}
    assert toy_project.frame.composition["target"]["chain"] == "A"


# ---------------------------------------------------------------------------------------------------------------- the command line
def test_the_chains_command_prints_the_choice_and_its_reason(toy_reference, toy_construct, capsys):
    assert cli.main(["chains", str(toy_reference), "--target-pdb", str(toy_construct)]) == 0
    out = capsys.readouterr().out
    assert "target: chain A: chains A and B carry the construct equally" in out and "partners: chain B (the same protein as the target" in out
    assert cli.main(["chains", str(toy_reference), "--target-pdb", str(toy_construct), "--json", "--partner-chains", "none"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["partners"]["chains"] == [] and d["target"]["chain"] == "A"
    assert cli.main(["chains", str(toy_reference)]) == 2                      # two protein chains and no construct: it will not guess
    assert "name the chain of the target" in capsys.readouterr().out


def test_the_import_options_for_chains(toy_dir, capsys):
    parser = cli.build_parser()
    imp = parser.parse_args(["import", "x"])
    assert imp.reference_chain == "auto" and imp.partner_chains == "auto" and imp.assembly is None
    assert cli._partner_arg("auto") is None and cli._partner_arg("none") == () and cli._partner_arg("all") == "all" and cli._partner_arg("B, C") == ("B", "C")
    base = ["import", f"run01={toy_dir / 'runs' / 'run01'}", "--target-pdb", str(toy_dir / "construct" / "toy_construct.pdb"), "--quiet"]
    assert cli.main(base + ["--partner-chains", "all", "--out", str(toy_dir / "unused_out")]) == 2
    assert "--partner-chains describe the reference structure" in capsys.readouterr().err
    assert cli.main(base + ["--assembly", "1", "--out", str(toy_dir / "unused_out")]) == 2
    assert "--assembly describe the reference structure" in capsys.readouterr().err


def test_the_first_chain_is_taken_even_when_the_construct_follows_another_more_closely_and_the_reason_says_so(tmp_path, toy_chains, construct_atoms):
    """Copies that differ: chain A has a stretch moved away. The default stays predictable (the first chain); the reason points at the better fit."""
    A, B, R, t = toy_chains
    bent = _copy(A, "A")
    for r in bent:
        if 100 <= r.seqid.num <= 130:
            for a in r:
                a.pos = gemmi.Position(a.pos.x + 8.0, a.pos.y, a.pos.z)
    path = _write(tmp_path / "bent.cif", [bent, _copy(B, "B")])
    comp = TF.analyse_reference(path, None, construct_atoms=construct_atoms)
    d = comp.data["target"]
    assert comp.target == "A" and d["how"] == "first_of_equal" and "follow chain B more closely" in d["why"] and "choose B if the construct was cut from it" in d["why"]
    assert _chains(comp.data)["B"]["construct"]["rmsd"] < _chains(comp.data)["A"]["construct"]["rmsd"]
