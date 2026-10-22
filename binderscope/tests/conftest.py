"""Fixtures of the synthetic tests: the toy project is generated once per session into pytest's temp folder.

This folder is self-contained. It is exported as `tests/` of the public repository, so nothing here may refer to
files outside it except the package under test: every path comes from Path(__file__), this folder is put on
sys.path (for the `toydata` package) and the `src` folder of the repository is added when the package is not
installed. No network, no private data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:
    import binder_review  # noqa: F401
except ImportError:                                  # not installed: look for src next to or above this folder
    for _root in (HERE.parent, HERE.parents[1]):
        if (_root / "src" / "binder_review").is_dir():
            sys.path.insert(0, str(_root / "src"))
            break

#: The public name of the reference offset: construct number + OFFSET = reference number.
OFFSET = 59


@pytest.fixture(scope="session")
def toy_dir(tmp_path_factory) -> Path:
    """The whole toy project (reference, construct, runs, BindCraft 2 campaigns), built once.

    BINDERSCOPE_TOY_DIR=/some/folder reuses a folder written earlier by `python toydata/make.py` (a shortcut for
    working on the tests; the tests only read it)."""
    import os

    from toydata.project import make_project

    reuse = os.environ.get("BINDERSCOPE_TOY_DIR")
    if reuse and (Path(reuse) / "toy_truth.json").is_file():
        return Path(reuse)
    out = tmp_path_factory.mktemp("toy_project")
    make_project(out)
    return out


@pytest.fixture(scope="session")
def toy_truth(toy_dir) -> dict:
    """What the generator did (frame, hotspots, the goal of every pose, deleted loops, ...)."""
    return json.loads((toy_dir / "toy_truth.json").read_text())


@pytest.fixture(scope="session")
def toy_construct(toy_dir) -> Path:
    return toy_dir / "construct" / "toy_construct.pdb"


@pytest.fixture(scope="session")
def toy_construct_gapped(toy_dir) -> Path:
    return toy_dir / "construct" / "toy_construct_gapped.pdb"


@pytest.fixture(scope="session")
def toy_reference(toy_dir) -> Path:
    return toy_dir / "reference" / "toy_target.cif"


@pytest.fixture(scope="session")
def toy_run1(toy_dir, toy_construct):
    from binder_review.readers import bindcraft1

    return bindcraft1.read_run(toy_dir / "runs" / "run01", "run01", construct_path=toy_construct)


@pytest.fixture(scope="session")
def toy_run2(toy_dir, toy_construct):
    from binder_review.readers import bindcraft1

    return bindcraft1.read_run(toy_dir / "runs" / "run02", "run02", construct_path=toy_construct)


@pytest.fixture(scope="session")
def toy_bc2_a(toy_dir, toy_construct):
    from binder_review.readers import bindcraft2

    return bindcraft2.read_run(toy_dir / "bindcraft2" / "toya", "toya", construct_path=toy_construct)


@pytest.fixture(scope="session")
def toy_bc2_b(toy_dir, toy_construct):
    from binder_review.readers import bindcraft2

    return bindcraft2.read_run(toy_dir / "bindcraft2" / "toyb", "toyb", construct_path=toy_construct)


# ------------------------------------------------------------------------------------------------ the whole pipeline on the toy project
def _spec(toy_dir: Path, profile="default", uniprot=None, jobs=2):
    from binder_review import pipeline as P

    return P.ProjectSpec(
        name="toy", runs=[P.RunSpec(path=toy_dir / "runs" / "run01", run_id="run01"), P.RunSpec(path=toy_dir / "runs" / "run02", run_id="run02")],
        target_pdb=toy_dir / "construct" / "toy_construct.pdb", reference_cif=toy_dir / "reference" / "toy_target.cif",
        profile=profile, uniprot=uniprot, jobs=jobs)


@pytest.fixture(scope="session")
def toy_spec(toy_dir):
    """The project specification used by the pipeline fixtures (default profile, no accession: nothing is looked up anywhere)."""
    return _spec(toy_dir)


@pytest.fixture(scope="session")
def toy_project(toy_spec, tmp_path_factory):
    """Both toy runs through every stage of the pipeline (default profile, no UniProt accession, no network)."""
    from binder_review import pipeline as P

    return P.run_pipeline(toy_spec, tmp_path_factory.mktemp("toy_work"), log=P.Log(quiet=True))


@pytest.fixture(scope="session")
def toy_blobs(toy_project):
    from binder_review import export as E

    return E.build_blobs(toy_project)


@pytest.fixture(scope="session")
def toy_export_dir(toy_blobs, tmp_path_factory) -> Path:
    from binder_review import export as E

    out = tmp_path_factory.mktemp("toy_export") / "export"
    E.write_export(toy_blobs, out)
    return out


@pytest.fixture(scope="session")
def toy_bundle(toy_export_dir, tmp_path_factory) -> Path:
    from binder_review import bundle as B

    out = tmp_path_factory.mktemp("toy_bundle") / "bundle"
    B.write_bundle(toy_export_dir, out, name="toy", guide=False)
    return out


@pytest.fixture(scope="session")
def toy_hinge(toy_dir, toy_project, tmp_path_factory):
    """A reference in which the domain that the first accepted design of run01 sits on is turned by 12 degrees against the rest of the chain (toydata.hinge),
    with the target called B, and run01 built from it with the default (interface) placement: the reference file, what was turned, the design that defines the
    domain and its complex, the project, its blobs, its export folder and its bundle."""
    from types import SimpleNamespace

    from binder_review import bundle as B
    from binder_review import export as E
    from binder_review import pipeline as P
    from binder_review import structure as S
    from toydata.hinge import hinge_reference

    design = toy_project.runs["run01"].accepted()[0]
    cx = S.load_complex(design.structure_path)
    placed = toy_project.frame.place(cx, "run01")
    work = tmp_path_factory.mktemp("toy_hinge")
    reference = work / "toy_target_hinge.cif"
    info = hinge_reference(toy_dir / "reference" / "toy_target.cif", placed.apply(cx.binder.heavy().xyz), reference, swap_chains=True)
    spec = P.ProjectSpec(name="hinge", runs=[P.RunSpec(path=toy_dir / "runs" / "run01", run_id="run01")], target_pdb=toy_dir / "construct" / "toy_construct.pdb",
                         reference_cif=reference, reference_chain=info["chain"], jobs=2)
    project = P.run_pipeline(spec, work / "project", log=P.Log(quiet=True))
    blobs = E.build_blobs(project)
    export = work / "export"
    E.write_export(blobs, export)
    bundle = work / "bundle"
    B.write_bundle(export, bundle, name="hinge", guide=False)
    return SimpleNamespace(reference=reference, info=info, design=design, cx=cx, project=project, blobs=blobs, export=export, bundle=bundle)


@pytest.fixture(scope="session")
def toy_refdata(toy_dir) -> Path:
    """The toy offline reference cache (UniProt-style records of the toy accessions, a panel table, toy models of the paralogs)."""
    return toy_dir / "reference" / "refdata"


@pytest.fixture(scope="session")
def toy_panel_project(toy_dir, toy_refdata, tmp_path_factory):
    """The same project with the toy accession and a toy profile that names three orthologs and two paralogs: a specificity panel."""
    from binder_review import pipeline as P
    from toydata.testing import toy_profile

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BINDERSCOPE_REFDATA_DIR", str(toy_refdata))
        mp.setenv("BINDERSCOPE_OFFLINE", "1")
        return P.run_pipeline(_spec(toy_dir, profile=toy_profile(), uniprot="Z0TOY1"), tmp_path_factory.mktemp("toy_panel_work"),
                              log=P.Log(quiet=True))


@pytest.fixture(scope="session")
def toy_panel_blobs(toy_panel_project, toy_refdata):
    from binder_review import export as E

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BINDERSCOPE_REFDATA_DIR", str(toy_refdata))
        mp.setenv("BINDERSCOPE_OFFLINE", "1")
        return E.build_blobs(toy_panel_project)


@pytest.fixture(scope="session")
def toy_panel_bundle(toy_panel_blobs, tmp_path_factory) -> Path:
    from binder_review import bundle as B
    from binder_review import export as E

    base = tmp_path_factory.mktemp("toy_panel_bundle")
    E.write_export(toy_panel_blobs, base / "export")
    B.write_bundle(base / "export", base / "bundle", name="toy-panel", guide=False)
    return base / "bundle"
