#!/usr/bin/env python3
"""Write the example data of docs/schema/examples from a small toy project (deterministic, offline).

    python tests/toydata/make_schema_examples.py [--out docs/schema/examples] [--toy-dir DIR] [--check]

It generates a small toy project (two BindCraft 1 runs, a few backbones of each kind), runs the whole pipeline on it with the toy profile
(toy accession Z0TOY1, three orthologs and two paralogs from the toy reference cache) and writes next to the other examples

    toy.campaign.json  toy.inspect.json  toy.homolog.json  toy.manifest.json  toy.profile.json  toy.reviews.jsonl

then validates them with scripts/validate_schema.py. Everything is invented: coordinates, scores, sequences and review events. The machine
that made the files is not recorded (the manifest carries placeholders for the tool details). --check writes into a temporary folder and
compares with the files in --out instead of overwriting them (exit 1 when they differ).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

CREATED = "2026-01-01T09:00:00Z"
REVIEWER = "example reviewer"


def _repo_root() -> Path:
    for p in HERE.parents:
        if (p / "docs" / "schema").is_dir() and (p / "src" / "binder_review").is_dir():
            return p
    raise SystemExit("repository root (docs/schema and src/binder_review) not found above " + str(HERE))


def _event(unit: str, design: str, run: str, decision: str, ts: str, bundle: str, *, sequences=(), reasons=(), learning=False, note=None) -> dict:
    ident = "ev" + hashlib.sha1(f"{REVIEWER}|{unit}|{design}|{decision}|{ts}".encode()).hexdigest()[:13]
    return {"v": 1, "type": "decision", "id": ident, "reviewer": REVIEWER, "ts": ts, "unit": unit, "design": design, "run": run, "decision": decision,
            "sequences": list(sequences), "reasons": list(reasons), "learning": learning, "note": note, "bundle": bundle, "app": "binderscope-viewer 0.1.0"}


def build(work: Path, toy_dir: Path | None, jobs: int = 2):
    """Run the pipeline on the small toy project and return (project, blobs)."""
    from toydata.plans import RUN1_EXAMPLE, RUN2_EXAMPLE
    from toydata.project import make_project
    from toydata.testing import toy_profile

    if toy_dir is None:
        toy_dir = work / "toy"
        make_project(toy_dir, plans=(RUN1_EXAMPLE, RUN2_EXAMPLE))
    os.environ["BINDERSCOPE_REFDATA_DIR"] = str(toy_dir / "reference" / "refdata")
    os.environ["BINDERSCOPE_OFFLINE"] = "1"
    from binder_review import export as E
    from binder_review import pipeline as P

    spec = P.ProjectSpec(
        name="toy", runs=[P.RunSpec(toy_dir / "runs" / "run01", "run01"), P.RunSpec(toy_dir / "runs" / "run02", "run02")],
        target_pdb=toy_dir / "construct" / "toy_construct.pdb", reference_cif=toy_dir / "reference" / "toy_target.cif",
        profile=toy_profile(), uniprot="Z0TOY1", jobs=jobs,
        command="binder-review import run01=runs/run01 run02=runs/run02 --target-pdb construct/toy_construct.pdb "
                "--reference-cif reference/toy_target.cif --uniprot Z0TOY1 --profile reference/toy.profile.json --name toy")
    project = P.run_pipeline(spec, work / "project", log=P.Log(quiet=True))
    blobs = E.build_blobs(project, created=CREATED)
    return project, blobs


def example_files(project, blobs) -> dict[str, str]:
    """{file name: text} of the example set."""
    from binder_review import export as E

    manifest = json.loads(E.dumps(blobs.manifest))
    manifest["tool"] = {"name": "binderscope", "package": "binder-review", "version": manifest["tool"]["version"], "git_commit": None,
                        "python": "3", "platform": "any", "command": manifest["tool"]["command"], "libraries": {}}
    designs = blobs.campaign["designs"]
    run = lambda d: d["run"]                                           # noqa: E731
    fams = {}
    for d in designs:
        if d["status"] == "accepted":
            fams.setdefault((d["run"], d["fam"]), []).append(d)
    keys = sorted(fams, key=lambda k: min(x["traj"] for x in fams[k]))
    bundle = manifest["bundle"]["id"]
    ev = []
    if len(keys) >= 4:
        a, b, c, d = keys[:4]
        ev.append(_event("family", a[1], a[0], "keep", "2026-01-01T09:10:01Z", bundle, sequences=[fams[a][0]["id"]],
                         note="Two hotspots contacted, clear of the dimer interface.", learning=True))
        ev.append(_event("family", b[1], b[0], "maybe", "2026-01-01T09:11:20Z", bundle, reasons=["small interface"]))
        ev.append(_event("family", c[1], c[0], "reject", "2026-01-01T09:12:45Z", bundle, reasons=["wrong site"]))
        ev.append(_event("design", fams[d][0]["id"], d[0], "reject", "2026-01-01T09:13:30Z", bundle, reasons=["oligomer clash", "low confidence"]))
        ev.append(_event("family", a[1], a[0], "clear", "2026-01-01T09:14:02Z", bundle))
    files = {
        "toy.campaign.json": E.dumps(blobs.campaign), "toy.inspect.json": E.dumps(blobs.inspect), "toy.homolog.json": E.dumps(blobs.homolog),
        "toy.manifest.json": E.dumps(manifest), "toy.profile.json": json.dumps(blobs.profile, indent=2) + "\n",
        "toy.reviews.jsonl": "".join(json.dumps(e) + "\n" for e in ev),
    }
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="folder of the examples (default docs/schema/examples of the repository)")
    ap.add_argument("--toy-dir", help="a small toy project written earlier by make_project(plans=(RUN1_EXAMPLE, RUN2_EXAMPLE))")
    ap.add_argument("--check", action="store_true", help="compare with the files in --out instead of writing them")
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args(argv)
    root = _repo_root()
    out = Path(a.out) if a.out else root / "docs" / "schema" / "examples"
    sys.path.insert(0, str(root / "src"))
    with tempfile.TemporaryDirectory() as td:
        project, blobs = build(Path(td), Path(a.toy_dir) if a.toy_dir else None, a.jobs)
        files = example_files(project, blobs)
        if a.check:
            bad = [n for n, t in files.items() if not (out / n).is_file() or (out / n).read_text(encoding="utf-8") != t]
            print("examples are current" if not bad else "differ: " + ", ".join(bad))
            return 1 if bad else 0
        out.mkdir(parents=True, exist_ok=True)
        for n, t in files.items():
            (out / n).write_text(t, encoding="utf-8")
        print(f"wrote {len(files)} files to {out} ({sum(len(t) for t in files.values()) / 1e6:.2f} MB)")
    from toydata.testing import load_validator

    rep = load_validator().check_examples(out)
    for e in rep.errors:
        print("ERROR", e)
    for w in rep.warnings[:20]:
        print("warning", w)
    print("validation:", "ok" if rep.ok else f"{len(rep.errors)} errors")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
