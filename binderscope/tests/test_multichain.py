"""A binder of several chains: an scFv's VH and VL, a homo-oligomer's copies.

`load_complex` took exactly one binder chain, so on a real scFv the second half
of the molecule was never loaded -- the CSV's 238 residues came back as 131 --
and everything measured from the binder would have been measured on half of it.

The fixture splits a toy campaign's own complex into two binder chains, so the
answer is known: whatever is read from the two chains must equal what was read
from the one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from binder_review import structure as S


@pytest.fixture(scope="module")
def one_chain_complex(toy_dir):
    folder = toy_dir / "bindcraft2_v103" / "toyc" / "2_Refolded" / "Complexes"
    return sorted(folder.glob("*.cif"))[0]


@pytest.fixture(scope="module")
def toyc_run(toy_dir):
    from binder_review.readers import bindcraft2

    return bindcraft2.read_run(toy_dir / "bindcraft2_v103" / "toyc", "toyc")


@pytest.fixture(scope="module")
def split_complex(one_chain_complex, tmp_path_factory):
    """The same complex with its binder written as two chains, B and C."""
    import gemmi

    st = gemmi.read_structure(str(one_chain_complex))
    st.setup_entities()
    model = st[0]
    binder = next(ch for ch in model if ch.name == "B")
    half = len(binder) // 2
    tail = [binder[i] for i in range(half, len(binder))]
    new_chain = gemmi.Chain("C")
    for res in tail:
        new_chain.add_residue(res)
    for i in range(len(binder) - 1, half - 1, -1):
        del binder[i]
    model.add_chain(new_chain)
    st.setup_entities()
    out = tmp_path_factory.mktemp("split") / "two_chain.cif"
    st.make_mmcif_document().write_file(str(out))
    return out


def test_the_fixture_really_has_three_chains(split_complex):
    import gemmi

    names = [ch.name for ch in gemmi.read_structure(str(split_complex))[0]]
    assert names == ["A", "B", "C"], names


def test_one_binder_chain_still_loads_as_before(one_chain_complex):
    cx = S.load_complex(str(one_chain_complex))
    assert cx.binder_chains == ("B",) and cx.binder_chain == "B"
    assert len(cx.binder.sequence) > 0


def test_both_binder_chains_are_loaded_by_count(one_chain_complex, split_complex):
    whole = S.load_complex(str(one_chain_complex))
    cx = S.load_complex(str(split_complex), n_binder_chains=2)
    assert cx.binder_chains == ("B", "C")
    assert cx.binder.sequence == whole.binder.sequence          # the same molecule, written differently
    assert len(cx.binder.residues.number) == len(whole.binder.residues.number)
    assert cx.target.sequence == whole.target.sequence          # and the target is untouched


def test_both_binder_chains_are_loaded_by_name(split_complex):
    by_name = S.load_complex(str(split_complex), binder_chain=["B", "C"])
    by_count = S.load_complex(str(split_complex), n_binder_chains=2)
    assert by_name.binder.sequence == by_count.binder.sequence
    assert by_name.binder_chains == by_count.binder_chains


def test_asking_for_one_chain_of_a_two_chain_binder_leaves_the_rest_out(split_complex):
    """The old behaviour, which is why this had to be found: it does not fail, it under-reads."""
    half = S.load_complex(str(split_complex))
    both = S.load_complex(str(split_complex), n_binder_chains=2)
    assert len(half.binder.sequence) < len(both.binder.sequence)


def test_the_chains_keep_their_own_ids_inside_the_one_table(split_complex):
    cx = S.load_complex(str(split_complex), n_binder_chains=2)
    assert set(cx.binder.chain) == {"B", "C"}                   # a consumer can still tell them apart


def test_more_chains_than_the_file_has_is_an_error(one_chain_complex):
    with pytest.raises(S.StructureError):
        S.load_complex(str(one_chain_complex), n_binder_chains=3)


def test_a_target_only_load_is_unaffected(one_chain_complex):
    cx = S.load_complex(str(one_chain_complex), binder_chain=None)
    assert len(cx.binder.residues.number) == 0 and cx.binder_chains == ()


# ----------------------------------------------------- a whole campaign, read twice

@pytest.fixture(scope="module")
def split_campaign(toy_dir, tmp_path_factory):
    """toyc with every binder written as two chains, and its CSV saying so.

    The same molecules as toyc, described differently, so whatever the review
    reads from them has a known right answer: toyc's.
    """
    import shutil

    import gemmi
    import pandas as pd

    src = toy_dir / "bindcraft2_v103" / "toyc"
    root = tmp_path_factory.mktemp("multichain") / "toym"
    shutil.copytree(src, root)

    splits: dict[str, int] = {}
    for folder in (root / "2_Refolded" / "Complexes", root / "3_Ranked"):
        for cif in sorted(folder.glob("*.cif")):
            st = gemmi.read_structure(str(cif))
            st.setup_entities()
            model = st[0]
            binder = next((ch for ch in model if ch.name == "B"), None)
            if binder is None:
                continue
            half = len(binder) // 2
            splits[cif.stem] = half
            tail = [binder[i] for i in range(half, len(binder))]
            new = gemmi.Chain("C")
            for res in tail:
                new.add_residue(res)
            for i in range(len(binder) - 1, half - 1, -1):
                del binder[i]
            model.add_chain(new)
            st.setup_entities()
            st.make_mmcif_document().write_file(str(cif))

    for rel in (Path("2_Refolded") / "!_Refolded.csv", Path("3_Ranked") / "!_Ranked.csv"):
        df = pd.read_csv(root / rel)
        key = "design"
        def cut(row):
            at = splits.get(str(row[key]))
            seq = str(row["Binder_Sequence"])
            return seq if at is None else seq[:at] + "/" + seq[at:]
        df["Binder_Sequence"] = df.apply(cut, axis=1)
        df.to_csv(root / rel, index=False)
    return root


def test_the_campaign_is_read_as_a_two_chain_binder(split_campaign):
    from binder_review.readers import bindcraft2

    run = bindcraft2.read_run(split_campaign, "toym")
    counts = {d.id: (d.extras or {}).get("n_binder_chains") for d in run.designs}
    assert set(counts.values()) == {2}, counts
    # Still de novo: the design names say so, and a name is better evidence than
    # the chain-count heuristic, which only answers when nothing else does.
    assert run.annotations.get("modality") == "de novo"


def test_the_same_molecule_gives_the_same_designs(split_campaign, toyc_run):
    from binder_review.readers import bindcraft2

    two = bindcraft2.read_run(split_campaign, "toyc")
    one = toyc_run
    assert len(two.designs) == len(one.designs)
    assert len(two.accepted()) == len(one.accepted())
    for a, b in zip(sorted(one.designs, key=lambda d: d.id), sorted(two.designs, key=lambda d: d.id)):
        assert a.id == b.id
        assert b.sequence.replace("/", "") == a.sequence.replace("/", "")
        assert b.length == a.length


def test_the_contacts_are_the_same_as_the_one_chain_writing(split_campaign, toyc_run):
    """The interface is geometry: writing the binder as two chains must not move an atom."""
    from binder_review import families
    from binder_review.readers import bindcraft2

    two = bindcraft2.read_run(split_campaign, "toyc")
    by_id = {d.id: d for d in two.designs}
    checked = 0
    for d in sorted(one_of(toyc_run), key=lambda d: d.id)[:4]:
        g1 = families.design_geometry(d)
        g2 = families.design_geometry(by_id[d.id])
        assert g2.contacts == g1.contacts, d.id
        assert len(g2.ca) == len(g1.ca), d.id
        checked += 1
    assert checked, "no design with a structure to compare"


def one_of(run):
    return [d for d in run.accepted() if d.structure_path]


def test_fold_distance_is_not_reported_for_it(split_campaign):
    from binder_review import families
    from binder_review.readers import bindcraft2

    run = bindcraft2.read_run(split_campaign, "toym")
    why = families.fold_unreliable(run)
    assert why == families.FOLD_MULTICHAIN_WHY


def test_the_terminus_readout_says_how_many_ends_there_are(split_campaign):
    from binder_review import termini
    from binder_review.readers import bindcraft2

    run = bindcraft2.read_run(split_campaign, "toym")
    fmt = termini.format_of(run)
    assert fmt.chains == 2
    assert not fmt.applies and "4 ends" in fmt.why
