#!/usr/bin/env python3
"""Write the whole toy project (reference, construct, BindCraft 1 runs, BindCraft 2 campaigns) into OUT_DIR.

    python tests/toydata/make.py OUT_DIR [--seed N] [--no-bindcraft2] [--quiet]

Deterministic: the same seed gives the same files. Nothing is written outside OUT_DIR, nothing is read from
anywhere, no network. Then, from the repository root, for example:

    binder-review inspect OUT_DIR/runs/run01
    binder-review import run01=OUT_DIR/runs/run01 run02=OUT_DIR/runs/run02 \\
        --target-pdb OUT_DIR/construct/toy_construct.pdb --reference-cif OUT_DIR/reference/toy_target.cif \\
        --name toy --out OUT_DIR/work --bundle OUT_DIR/bundle

With a specificity panel (toy accession and a toy reference cache, offline):

    BINDERSCOPE_REFDATA_DIR=OUT_DIR/reference/refdata BINDERSCOPE_OFFLINE=1 binder-review import run01=... run02=... \\
        --target-pdb ... --reference-cif ... --uniprot Z0TOY1 --profile OUT_DIR/reference/toy.profile.json --name toy --out ...
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))        # the folder that contains the toydata package

from toydata.project import DEFAULT_SEED, make_project  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out_dir")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--no-bindcraft2", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    log = (lambda *x: None) if a.quiet else (lambda *x: print(f"[{time.time() - t0:5.1f}s]", *x, flush=True))
    truth = make_project(a.out_dir, a.seed, not a.no_bindcraft2, log)
    n = sum(1 for p in Path(a.out_dir).rglob("*") if p.is_file())
    print(f"wrote {n} files to {a.out_dir} in {time.time() - t0:.1f} s "
          f"({len(truth['accepted']['run01'])} + {len(truth['accepted']['run02'])} accepted designs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
