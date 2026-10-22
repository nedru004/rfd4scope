"""Small helpers shared by the toy tests (kept in the package so test files never import a conftest)."""
from __future__ import annotations

import dataclasses

OFFSET = 59        # construct number + OFFSET = reference number


def with_offset(run, offset: int = OFFSET):
    """Copy of a run whose target carries the reference offset (the target frame sets this in the pipeline)."""
    return dataclasses.replace(run, target=dataclasses.replace(run.target, uniprot_offset=offset))


def repo_root():
    """The repository root (the folder with docs/schema and src/binder_review), found from this file in the private layout
    (tests/toy/toydata) and in the exported one (tests/toydata)."""
    from pathlib import Path

    for p in Path(__file__).resolve().parents:
        if (p / "docs" / "schema").is_dir() and (p / "src" / "binder_review").is_dir():
            return p
    raise FileNotFoundError("repository root not found above " + __file__)


def load_validator():
    """scripts/validate_schema.py as a module (it is a script, not part of the package)."""
    import importlib.util
    import sys

    if "validate_schema" in sys.modules:
        return sys.modules["validate_schema"]
    path = repo_root() / "scripts" / "validate_schema.py"
    spec = importlib.util.spec_from_file_location("validate_schema", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_schema"] = mod
    spec.loader.exec_module(mod)
    return mod


def toy_profile(base: dict | None = None) -> dict:
    """The built-in default profile completed for the toy target: its accession, three orthologs and two paralogs (all in the toy
    reference cache that `make.py` writes to reference/refdata; set BINDERSCOPE_REFDATA_DIR to that folder)."""
    import copy
    import json

    from binder_review import pipeline

    prof = copy.deepcopy(base) if base is not None else json.loads(
        (pipeline.profiles_dir() / "default.profile.json").read_text(encoding="utf-8"))
    prof["id"] = "toy"
    prof.setdefault("target", {})
    prof["target"].update({"name": "Toy target", "short": "TOY", "uniprot": "Z0TOY1"})
    prof["specificity"] = {
        "subject": "Toy target", "kind": "host", "panel_noun": "orthologs", "show": ["orth1", "orth2"],
        "species": [{"match": "acc:Z0TOY2", "label": "Ortholog one", "key": "orth1"},
                    {"match": "acc:Z0TOY3", "label": "Ortholog two", "key": "orth2"},
                    {"match": "acc:Z0TOY4", "label": "Ortholog three", "key": "orth3", "optional": True}],
        "paralogs": {"members": [{"acc": "Z0TOY5", "gene": "PARA1"}, {"acc": "Z0TOY6", "gene": "PARA2"}], "order": ["PARA1", "PARA2"],
                     "focus": {"key": "PARA1", "label": "Paralog one", "table": "PARA1", "card": "PARA1"}, "family_label": "toy family"},
        "headline": [], "detail": [], "decoy_species": [], "rules": [],
    }
    return prof
