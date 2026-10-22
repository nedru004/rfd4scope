"""Whether the runs of a project bind the same patch of the target.

A project review pools runs that often cannot be compared -- different
modalities, lengths, filters -- and it says so. Binding site is the exception:
it is measured on the target's residues, so what bound them does not matter.
Without this the review listed everything that could not be compared and said
nothing about the one thing that could.
"""
from __future__ import annotations

import pytest

from binder_review import export


def camp(clusters, designs):
    return {"diversity": {"pooled": {"clusters": {"site": clusters}}}, "designs": designs}


def designs(*pairs):
    return [{"fam": f, "run": r, "status": "accepted"} for f, r in pairs]


def test_nothing_is_said_about_a_project_of_one_run():
    text = export._shared_sites_text(camp([{"id": 1, "fams": ["f1"]}], designs(("f1", "a"))), ["a"])
    assert text == ""


def test_nothing_is_said_when_there_are_no_site_clusters():
    assert export._shared_sites_text(camp([], designs(("f1", "a"))), ["a", "b"]) == ""


def test_runs_that_converge_on_one_site_are_told_so():
    text = export._shared_sites_text(
        camp([{"id": 1, "fams": ["f1", "f2", "f3"]}], designs(("f1", "a"), ("f2", "b"), ("f3", "b"))),
        ["a", "b"])
    assert "converge on the same patch" in text and "a and b" in text


def test_runs_that_share_some_sites_get_the_counts():
    clusters = [{"id": 1, "fams": ["f1", "f2"]}, {"id": 2, "fams": ["f3"]}, {"id": 3, "fams": ["f4", "f5"]}]
    text = export._shared_sites_text(
        camp(clusters, designs(("f1", "a"), ("f2", "b"), ("f3", "a"), ("f4", "b"), ("f5", "b"))),
        ["a", "b"])
    assert "1 of the 3 site groups" in text and "2 of 5 families" in text


def test_runs_that_land_apart_are_told_that_too():
    """The more important answer of the two: a decision in one run then says nothing about the other."""
    clusters = [{"id": 1, "fams": ["f1", "f2"]}, {"id": 2, "fams": ["f3"]}]
    text = export._shared_sites_text(camp(clusters, designs(("f1", "a"), ("f2", "a"), ("f3", "b"))), ["a", "b"])
    assert "do not share a binding site" in text
    assert "says nothing about the other" in text


def test_a_family_with_no_accepted_design_does_not_invent_a_run():
    # Only accepted designs carry a family into the pooled clusters; an id that
    # matches nothing must not be read as a third run.
    clusters = [{"id": 1, "fams": ["f1", "ghost"]}]
    text = export._shared_sites_text(camp(clusters, designs(("f1", "a"))), ["a", "b"])
    assert "do not share" in text          # one real run in the group, not two


@pytest.mark.parametrize("basis", ["site"])
def test_it_reads_the_site_basis_whatever_the_pooled_block_calls_it(basis):
    text = export._shared_sites_text(
        {"diversity": {"pooled": {"clusters": {basis: [{"id": 1, "fams": ["f1", "f2"]}]}}},
         "designs": designs(("f1", "a"), ("f2", "b"))}, ["a", "b"])
    assert "converge" in text
