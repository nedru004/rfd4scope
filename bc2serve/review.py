"""Reviewing a finished campaign with Binderscope.

Binderscope (the `binder_review` package) turns a campaign folder into a static
review site. This module is everything bc2serve knows about it: what to ask the
person, how to run it, what it left behind, and how to serve that.

Two ideas shape it.

The form is mostly already answered. `campaign_metadata.json` records the
construct BindCraft was given, its chains and its hotspots, so the one thing a
person is asked for is what to compare the designs with beyond the construct: a
full-length reference structure (offered as a short list of candidates found
without asking), or, said out loud, none. There are three situations and each is
reported as what it is: a measured reference, whose findings carry full weight; a
predicted one, whose findings are advisory however well it is placed; and none.

Everything a review needs lives in `<results root>/_reviews/<campaign>/`, beside
the campaign and never inside it, because Binderscope refuses to write into a
folder it reads. `review.json` there is the record of state; the job queue only
runs the process. Deleting the folder forgets the review without touching a
single design.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
import biotite.structure as struc
from biotite.structure.io import load_structure

from .structure import THREE_TO_ONE
# `design_counts` (and the CSV reader under it) lives in tables.py so that Results and
# Review share one definition of "passed" and cannot report different numbers for one
# campaign. One behaviour differs from the copy that used to be here: a half-written
# FINAL row of a table being appended to is dropped there, where a bare DictReader
# counted it as a design.
from .tables import design_counts, is_campaign_folder

# Where reviews live, beside the campaigns in their results root. Binderscope
# refuses to write inside a folder it was given to read ("input folders are never
# written to"), so a review cannot sit inside its campaign. A folder of its own in
# the same root keeps it on the same disk as the results it is built from, and out
# of the way of BindCraft: the leading underscore is not a valid campaign name here.
REVIEWS = "_reviews"
STRUCTURE_SUFFIXES = {".cif", ".mmcif", ".pdb", ".ent"}
PLACEMENTS = ("interface", "whole")

# A campaign name arrives in a URL, so it is shaped before it is ever joined to a
# path: no separators, no leading dot, nothing that can climb out of a results root.
# One definition, in the module that first has to decide: a name the form
# turns into a folder and a name these pages will serve must be the same set,
# or a campaign runs and then cannot be reviewed.
from .campaign import CAMPAIGN_NAME                                   # noqa: E402
CHAIN_ID = re.compile(r"[A-Za-z0-9]{1,4}")

# A review of a whole project, pooled across its campaigns, is addressed by `@<slug>` where a
# campaign review is addressed by its campaign's name, and the two can never be confused: a
# campaign name must begin with a letter or a digit (CAMPAIGN_NAME above, which the form
# enforces when it makes the folder), and this begins with "@". So a project review can never
# be found by `folder()`, a campaign can never be found by `project_folder()`, and the two
# kinds of review cannot share a `_reviews/` folder, a job row's folder, a URL or a name in
# the Storage page. The tail is the slug's own alphabet, lowercase with single dashes.
PROJECT_NAME = re.compile(r"@[a-z0-9][a-z0-9-]{0,47}")
COUNT_KEYS = ("trajectories", "refolded", "passed", "ranked")


def project_slug(label: str) -> str:
    """The address of a project's review: `@`, the label as a slug, and six hex digits of its hash.

    A project label is free text up to 64 characters and may hold "/", so it cannot be a path
    segment or a URL segment as it is. The slug keeps the readable part (so a folder on disk or
    a line in the log still says whose it is) and the hash keeps two labels that slug alike
    ("Run 1" and "run-1") from sharing a review. The hash is of the label as typed, so the same
    label always gets the same slug and a renamed project is, correctly, a different one.

    A label with no letter or digit in it ("???", or a name written in another script) leaves
    nothing to slug, and "@-abc123" would not be a valid project name; it is called "project".
    """
    if not (label or "").strip():
        raise ReviewError("Unfiled is not a project, so there is nothing to pool: "
                          "give these campaigns a project first.")
    stem = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "project"
    return "@" + stem + "-" + hashlib.sha1(label.encode()).hexdigest()[:6]


def pooled_counts(folders) -> dict:
    """design_counts summed over several campaigns: how much a pooled review was built from.

    Summed per key, not compared per campaign, because that is what staleness needs to
    notice: a design added to any member, or a member added or removed, changes the sum.
    (Swapping one member for another with the same four numbers would not, which is why the
    members themselves are compared as well; see `Reviews._drifted`.)
    """
    total = dict.fromkeys(COUNT_KEYS, 0)
    for folder in folders:
        found = design_counts(Path(folder))
        for key in COUNT_KEYS:
            total[key] += found.get(key, 0)
    return total


def members_left(folder: Path) -> int | None:
    """How many of the campaigns a project's review was built from still exist, or None if it is not one.

    The Storage page's question about a project review: it has no campaign folder to be
    missing, so what makes it an orphan is that nothing is left to rebuild it from. `folder`
    is the review's notional folder (`<root>/@<slug>`), which is only a key. Read from the
    record, not from the job table, so that it holds for a project whose rows were archived.
    """
    members = ((read_record(folder) or {}).get("inputs") or {}).get("members")
    if not isinstance(members, list):
        return None
    return sum(1 for m in members if isinstance(m, dict) and Path(str(m.get("folder") or "")).is_dir())


def run_ids(folders) -> list[str]:
    """One id per campaign folder, for the CLI's ID=RUN_DIR arguments, in the order given.

    The id is the folder's own name, because the bundle's run filter shows it to the reader
    and "denovo" says more than "run2". It is cut down to `[A-Za-z0-9_-]` since it also has
    to survive being an argument (no "/" and no leading "-", which the CLI would take for a
    path and an option) and a key in the bundle. Two folders that come to the same id are
    told apart with -2, -3, ... rather than refused, because both are the person's own.
    """
    used: set[str] = set()
    ids = []
    for folder in folders:
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(folder).name).lstrip("-") or "run"
        chosen, n = base, 1
        while chosen in used:
            n += 1
            chosen = f"{base}-{n}"
        used.add(chosen)
        ids.append(chosen)
    return ids

# The accession format UniProt publishes, so that a stray capitalised token in a
# filename is not mistaken for one.
UNIPROT = re.compile(
    r"[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2}")

# How much of the construct a candidate must contain before it is called a fit.
# Below this the candidate is still listed, because the person may know about a
# mutation or an engineered tag that this comparison does not, but it is said
# plainly that it does not look like the target.
FITS = 0.9
MAX_SUGGESTIONS = 10
MAX_EXAMINED = 16


class ReviewError(Exception):
    """Something a person can act on. `status` is the HTTP code it should carry."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------- small helpers

def _iso(moment: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(moment)) if moment else None


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict) -> None:
    """Write through a temporary file, so a page polling for state never reads half of it."""
    scratch = path.with_name(path.name + ".tmp")
    scratch.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(scratch, path)


def residue_ranges(numbers: list[int]) -> str:
    """[27, 28, 29, 40] as "27-29,40", which is how people write residues."""
    pieces: list[str] = []
    start = previous = None
    for number in sorted(set(numbers)):
        if start is None:
            start = previous = number
        elif number == previous + 1:
            previous = number
        else:
            pieces.append(f"{start}-{previous}" if previous > start else str(start))
            start = previous = number
    if start is not None:
        pieces.append(f"{start}-{previous}" if previous > start else str(start))
    return ",".join(pieces)


# ------------------------------------------------------------ reading structures

@lru_cache(maxsize=32)
def _read_chains(path: str, mtime: float) -> dict[str, tuple[list[int], str]]:
    """{chain: (residue numbers, one-letter sequence)} for the protein in a file.

    Cached on the modification time, since the same reference is read once to
    describe the form and again to start the build.
    """
    try:
        atoms = load_structure(path, model=1)
        atoms = atoms[struc.filter_amino_acids(atoms)]
        chains = {}
        for chain in sorted(set(atoms.chain_id.tolist())):
            numbers, names = struc.get_residues(atoms[atoms.chain_id == chain])
            chains[chain] = (numbers.tolist(),
                             "".join(THREE_TO_ONE.get(name, "X") for name in names.tolist()))
        return chains
    except Exception:  # a file nobody can read simply offers nothing to compare
        return {}


def read_chains(path: Path) -> dict[str, tuple[list[int], str]]:
    try:
        return _read_chains(str(path), path.stat().st_mtime)
    except OSError:
        return {}


def _shared(construct: str, chain: str) -> int:
    """How many residues of the construct appear, in order, in this chain."""
    if not construct or not chain:
        return 0
    if construct in chain:
        return len(construct)
    # autojunk off: it exists to speed up very long inputs by ignoring common
    # letters, and on a 150-residue construct it would quietly discard the very
    # residues being counted.
    blocks = difflib.SequenceMatcher(None, construct, chain, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks)


def best_chain(construct: str, chains: dict) -> tuple[str | None, float]:
    """The chain most like the construct and what fraction of it is found there."""
    best, score = None, 0
    for chain, (_, sequence) in chains.items():
        found = _shared(construct, sequence)
        if found > score:
            best, score = chain, found
    return best, (score / len(construct) if construct else 0.0)


# ------------------------------------------------------------ the chains of a reference

# What the review does with the chains of a multi-chain reference is decided by Binderscope's
# target_frame.analyse_reference, the same function the command line and the build use, so what the
# form shows before a build is what the build does. This module only asks it and passes the answer on.
PARTNER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9~-]{0,5}")


def partner_request(value) -> str:
    """The partner choice of a request as one string: "auto", "none", "all" or chain names joined by commas.

    A list (what the page sends once someone has ticked chains by hand) and a string both come here;
    an empty list is an explicit "none", a missing value is "auto".
    """
    if value is None:
        return "auto"
    if isinstance(value, (list, tuple)):
        names = [str(v).strip() for v in value if str(v).strip()]
        return ",".join(names) if names else "none"
    text = str(value).strip()
    low = text.lower()
    if low in ("", "auto", "none", "all"):
        return low or "auto"
    names = [n.strip() for n in text.split(",") if n.strip()]
    return ",".join(names) if names else "auto"


@lru_cache(maxsize=24)
def _composition(path: str, mtime: float, construct: str, construct_mtime: float, sequence: str, chain: str, partners: str,
                 assembly: str) -> dict:
    """What a reference holds and what would be done with each chain (cached: the page asks again at every tick of a box).

    Cached on the file's modification time, as the other readers here are.
    """
    from binder_review import target_frame as TF
    from binder_review import structure as BS

    try:
        atoms = TF._read_construct(construct)        # the construct as the build reads it: its sequence, and its coordinates for a tie
    except Exception:  # the build will say what is wrong with the construct; the sequence alone still tells the chains apart
        atoms = None
    try:
        return TF.describe_reference(path, None if atoms is not None else (sequence or None), chain=chain or None,
                                     partner_chains=partners, assembly_id=assembly or None, construct_atoms=atoms)
    except (TF.FrameError, BS.StructureError) as problem:
        return {"problem": str(problem), "chains": [], "target": {"chain": None}, "partners": {"chains": [], "mode": "auto"}}


def uniprot_in_name(name: str) -> str | None:
    """An accession implied by a filename such as AF-P69905-F1-model_v6.cif.

    AlphaFold names carry it by construction. Anything else is only trusted when a
    whole token of the stem has the shape of an accession.
    """
    stem = Path(name).stem
    named = re.match(r"AF-([A-Z0-9]+)-F\d+-model", stem)
    if named and UNIPROT.fullmatch(named.group(1)):
        return named.group(1)
    for token in re.split(r"[_.\-\s]+", stem):
        if len(token) >= 6 and UNIPROT.fullmatch(token):
            return token
    return None


def uniprot_in_file(path: Path) -> str | None:
    """An accession the structure file itself declares, if it does."""
    try:
        with open(path, errors="ignore") as handle:
            text = handle.read(4 << 20)
    except OSError:
        return None
    patterns = (
        r"^_ma_target_ref_db_details\.db_accession\s+(\S+)",
        r"^_struct_ref\.pdbx_db_accession\s+(\S+)",
        r"^DBREF1?\s+\w{4}\s+\w\s+-?\d+\s+-?\d+\s+UNP\s+(\S+)",
    )
    for pattern in patterns:
        found = re.search(pattern, text, re.MULTILINE)
        if found and UNIPROT.fullmatch(found.group(1)):
            return found.group(1)
    return None


# ------------------------------------- AlphaFold references: how far to trust them
#
# A reference taken from the AlphaFold database is a prediction of one chain, and
# for a multi-domain protein it is two kinds of claim at once: what each domain
# looks like (pLDDT, which is local) and where the domains sit relative to one
# another (PAE, the predicted aligned error). Review's full-length checks (clashes,
# buried epitopes, cut ends) are statements about the second kind. A domain
# AlphaFold placed essentially at random will "collide" with every binder that
# happens to sit where it landed, the same way for every design, and that says
# nothing about the designs. So the review is annotated with how well the model
# knows where the rest of the protein is.
#
# The thresholds, and why:
#
# PAE(i, j) is the expected error, in angstroms, in the position of residue j when
# the model is superposed on residue i. The AlphaFold database draws it on a 0 to
# 30 A scale and its documentation reads low values between two parts as a
# confident relative placement and high values as one that should not be
# interpreted; the matrix tops out near 31.75 A (a saturated value means "no
# idea", not "31 A of error").
#
#   below  5 A  confident. The error is about one residue-to-residue contact. A
#               3 A clash call is still finer than this, so a finding that sits
#               near its threshold is soft even here; findings that are clearly
#               over or under it are about as good as the model.
#   above 15 A  independent. The error is the size of a small domain (EGF, Ig and
#               fibronectin domains are 20 to 40 A across), so the model is
#               not saying where one is relative to the other.
#   between     uncertain. The line from one to the other is gradual and nothing
#               here pretends otherwise; 10 A is where AF2-based binder design
#               (Bennett et al. 2023, Nat. Commun. 14:2625,
#               doi:10.1038/s41467-023-38328-5, pae_interaction < 10) puts its interface
#               pAE cut-off, which is a reason to call this band "weak", not wrong.
#
# These are AlphaFold's own reading plus a rule of thumb, not a calibrated error
# model, and the copy says "about" accordingly.
PAE_CONFIDENT = 5.0
PAE_INDEPENDENT = 15.0

# What "region" means. Rather than one number for everything outside the construct,
# which would hide a well-placed neighbour behind a floppy one, the outside is cut
# into contiguous stretches that AlphaFold itself treats as units: average-linkage
# clustering of the symmetrised PAE, cut where the mean PAE between members is
# 10 A. That number only decides how finely things are cut (a tighter cut gives more,
# smaller regions); the verdict for each region is made with the thresholds above.
DOMAIN_CUT = 10.0
# Runs shorter than this are folded into the neighbour they are best placed
# against: a six-residue sliver is a loop, not something anyone can act on.
MIN_REGION = 10
# Below this pLDDT AlphaFold does not predict a fixed shape at all.
DISORDERED = 50.0

ALPHAFOLD = "https://alphafold.ebi.ac.uk"
# Their file host turns away some default client identifiers (curl's, for one).
_AGENT = {"User-Agent": "bc2serve (BindCraft2 web service)"}
MAX_PAE_BYTES = 64 << 20
# A name AlphaFold gives an entry: AF-P69905-F1, or AF-P69905-2-F1 for an isoform,
# with -model_v6 on the file.
AF_ENTRY = re.compile(r"AF-([A-Z0-9]+?)(?:-(\d+))?-F(\d+)(?:-model_v(\d+))?")


class PaeUnavailable(Exception):
    """The PAE could not be had. `transient` says whether trying later could help."""

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@lru_cache(maxsize=16)
def _read_confidence(path: str, mtime: float) -> dict[str, tuple[list[int], str, tuple[float, ...], bool, float]]:
    """{chain: (residue numbers, sequence, per-residue B-factor, looks like pLDDT, varies)}.

    An AlphaFold file stores pLDDT in the B-factor column, one value per residue
    (every atom of a residue carries the same number). A measured structure's
    B-factors differ from atom to atom, so "one value per residue, between 0 and
    100" is what tells a prediction from a measurement when nothing else does.
    `varies` is the fraction of residues whose atoms do differ, which is the
    measured structure's signature when the file keeps no record of its method.
    """
    try:
        atoms = load_structure(path, model=1, extra_fields=["b_factor"])
        atoms = atoms[struc.filter_amino_acids(atoms)]
        found = {}
        for chain in sorted(set(atoms.chain_id.tolist())):
            part = atoms[atoms.chain_id == chain]
            starts = struc.get_residue_starts(part)
            numbers, names = struc.get_residues(part)
            ends = list(starts[1:]) + [len(part)]
            b = [part.b_factor[a:z] for a, z in zip(starts, ends)]
            mean = tuple(float(x.mean()) for x in b)
            flat = sum(1 for x in b if float(x.max() - x.min()) < 0.011) / max(1, len(b))
            looks = (len(b) >= 20 and flat >= 0.98 and min(mean) >= 0 and max(mean) <= 100
                     and max(mean) - min(mean) > 1)
            found[chain] = (numbers.tolist(),
                            "".join(THREE_TO_ONE.get(n, "X") for n in names.tolist()), mean, looks,
                            1.0 - flat)
        return found
    except Exception:
        return {}


def read_confidence(path: Path) -> dict:
    try:
        return _read_confidence(str(path), path.stat().st_mtime)
    except OSError:
        return {}


def provenance(path: Path) -> dict:
    """What a structure file says about being an AlphaFold model, from its content.

    The filename is only ever corroboration: people rename files, and a file named
    AF-... that is a crystal structure must not be treated as a prediction.
    """
    try:
        with open(path, errors="ignore") as handle:
            text = handle.read(4 << 20)
    except OSError:
        text = ""
    entry = None
    for pattern in (r"^data_(\S+)", r"^_entry\.id\s+(\S+)"):
        found = re.search(pattern, text, re.MULTILINE)
        entry = AF_ENTRY.fullmatch(found.group(1)) if found else None
        if entry:
            break
    modelcif = bool(re.search(r"^_ma_qa_metric\.|^_ma_model_list\.model_group_name\s+\"?AlphaFold",
                              text, re.MULTILINE | re.IGNORECASE))
    pdb_title = bool(re.search(r"^TITLE\s.*ALPHAFOLD", text, re.MULTILINE | re.IGNORECASE))
    named = AF_ENTRY.search(Path(path).stem)
    declared = uniprot_in_file(path)
    accession, where = None, None
    if declared:
        accession, where = declared, "the file's own record"
    elif entry and UNIPROT.fullmatch(entry.group(1)):
        accession, where = entry.group(1), "its entry name"
    elif named and UNIPROT.fullmatch(named.group(1)):
        accession, where = named.group(1), "its file name"
    shown = entry or named
    return {
        "recorded": bool(entry or modelcif or pdb_title),
        "entry_id": (f"AF-{shown.group(1)}" + (f"-{shown.group(2)}" if shown.group(2) else "")
                     + f"-F{shown.group(3)}") if shown else None,
        "version": int(named.group(4)) if named and named.group(4)
                   else int(entry.group(4)) if entry and entry.group(4) else None,
        "accession": accession, "accession_from": where,
        "named_like_af": bool(named),
        "evidence": [s for s, ok in (
            ("the entry name inside the file", bool(entry)),
            ("the AlphaFold model records inside the file", modelcif or pdb_title)) if ok],
    }


# ------------------------------- what kind of structure a reference is, from the file

# Why this matters: a reference is read differently according to how it was made.
# A measured structure (X-ray, cryo-EM, NMR) is evidence about where atoms were. A
# predicted one is a hypothesis about the fold, and even a confident one is a single
# conformation of a protein that moves, so a clash with it is a hypothesis to test
# and not a measurement. Whichever it is, a review must say so rather than leave a
# person to guess from a filename.
#
# The file's own records come first (the experiment's method, an AlphaFold model's
# provenance). The B-factor column corroborates: a prediction stores one pLDDT per
# residue, a measurement stores a value per atom. The filename is never evidence.
MEASURED_WORDS = ("X-RAY", "ELECTRON", "NMR", "NEUTRON", "DIFFRACTION", "SCATTERING",
                  "EPR", "INFRARED", "FLUORESCENCE")
_RESOLUTION = re.compile(
    r"^_(?:refine\.ls_d_res_high|em_3d_reconstruction\.resolution|reflns\.d_resolution_high)"
    r"\s+([0-9]+(?:\.[0-9]+)?)\s*$", re.MULTILINE)


@lru_cache(maxsize=32)
def _read_experiment(path: str, mtime: float) -> dict:
    """The experimental method a structure file records, if it records one.

    {"method": "X-RAY DIFFRACTION" or None, "measured": bool, "modelled": bool,
     "resolution": angstroms or None}. `modelled` is a method of THEORETICAL MODEL,
    which is how the PDB and ModelArchive label a prediction.
    """
    found = {"method": None, "measured": False, "modelled": False, "resolution": None}
    try:
        with open(path, errors="ignore") as handle:
            text = handle.read(4 << 20)
    except OSError:
        return found
    method = None
    for pattern in (r"^_exptl\.method\s+'([^']*)'\s*$", r'^_exptl\.method\s+"([^"]*)"\s*$',
                    r"^_exptl\.method\s+([^\s'\"].*?)\s*$", r"^EXPDTA\s+(.+?)\s*$"):
        said = re.search(pattern, text, re.MULTILINE)
        if said:
            method = said.group(1)
            break
    if method is None and re.search(r"^_exptl\.method\s*$", text, re.MULTILINE):
        try:  # the loop form, which an entry with several experiments uses
            from biotite.structure.io.pdbx import CIFFile
            column = CIFFile.read(path).block["exptl"]["method"].as_array()
            method = "; ".join(str(m) for m in column)
        except Exception:
            method = None
    if method:
        method = method.strip().strip(";").upper()
        found["method"] = method
        found["modelled"] = "THEORETICAL" in method
        found["measured"] = not found["modelled"] and any(w in method for w in MEASURED_WORDS)
    resolved = _RESOLUTION.search(text) or re.search(
        r"^REMARK\s+2\s+RESOLUTION\.\s+([0-9]+\.[0-9]+)", text, re.MULTILINE)
    if resolved:
        found["resolution"] = float(resolved.group(1))
    return found


def read_experiment(path: Path) -> dict:
    try:
        return _read_experiment(str(path), path.stat().st_mtime)
    except OSError:
        return {"method": None, "measured": False, "modelled": False, "resolution": None}


METHOD_WORDS = {"X-RAY DIFFRACTION": "X-ray crystallography", "ELECTRON MICROSCOPY": "cryo-EM",
                "SOLUTION NMR": "solution NMR", "SOLID-STATE NMR": "solid-state NMR",
                "ELECTRON CRYSTALLOGRAPHY": "electron crystallography",
                "NEUTRON DIFFRACTION": "neutron diffraction"}


def classify_reference(path: Path, confidence: dict) -> dict:
    """{"kind", "evidence", "method", "resolution", "reason", "why_unclear"} for a reference file.

    kind is one of
      alphafold      the file's own records say it is an AlphaFold model
      predicted      a prediction from elsewhere: it records a theoretical model, or
                     keeps one pLDDT-like value per residue
      experimental   it records a measured method, or, with no record, its B-factors
                     differ from atom to atom as a measurement's do
      unclear        the evidence is missing or contradicts itself; `reason` says how, and
                     `why_unclear` is "conflict", "named" (called AlphaFold but does not
                     look like it) or "silent" (nothing to go on)
    """
    shown = provenance(path)
    made = read_experiment(path)
    chains = list(confidence.values())
    plddt = any(c[3] for c in chains)
    varies = any(c[4] >= 0.5 and len(c[0]) >= 5 for c in chains)
    out = {"kind": "unclear", "evidence": [], "reason": None, "method": None, "resolution": None,
           "why_unclear": None}

    if made["measured"] and (shown["recorded"] or made["modelled"] or plddt):
        out["reason"] = (f"{path.name} records a measured method ({made['method'].title()}) but also "
                         "looks like a prediction (the records of a model, or one pLDDT-like "
                         "B-factor per residue), so it cannot be said which it is.")
        out["why_unclear"] = "conflict"
    elif shown["recorded"]:
        out["kind"] = "alphafold"
        out["evidence"] = list(shown["evidence"]) + (["per-residue pLDDT in the B-factor column"] if plddt else [])
    elif made["modelled"] or plddt:
        out["kind"] = "predicted"
        if made["modelled"]:
            out["evidence"].append("the file records its method as a theoretical model")
        if plddt:
            out["evidence"].append("per-residue pLDDT in the B-factor column")
    elif made["measured"]:
        out["kind"] = "experimental"
        out["evidence"].append(f"the file records its method: {made['method'].lower()}")
    elif shown["named_like_af"]:
        out["reason"] = (f"{path.name} is named like an AlphaFold model, but the file does not look "
                         "like one: its B-factors vary within residues, as a measured structure's do. "
                         "If it is an AlphaFold model, give its UniProt accession under Advanced.")
        out["why_unclear"] = "named"
    elif varies:
        out["kind"] = "experimental"
        out["evidence"].append("B-factors that differ from atom to atom, as a measurement's do "
                               "(the file does not record its method)")
    else:
        out["reason"] = (f"{path.name} does not say whether it was measured or predicted, and its "
                         "B-factors do not settle it.")
        out["why_unclear"] = "silent"
    if out["kind"] == "experimental":
        out["method"] = METHOD_WORDS.get(made["method"]) or (made["method"] or "").lower() or None
        out["resolution"] = made["resolution"]
    return out


# ---- fetching and caching the PAE

def _fetch(url: str, timeout: float, limit: int) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_AGENT), timeout=timeout) as reply:
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise PaeUnavailable("AlphaFold's PAE file is larger than this service will read.")
    return data


def _pae_matrix(doc) -> "np.ndarray":
    """The N by N matrix from AlphaFold's PAE document, in either of its two layouts."""
    if isinstance(doc, list) and doc:
        doc = doc[0]
    if not isinstance(doc, dict):
        raise ValueError("unexpected layout")
    if "predicted_aligned_error" in doc or "pae" in doc:
        matrix = np.asarray(doc.get("predicted_aligned_error", doc.get("pae")), dtype=float)
    else:
        # The older layout: three parallel lists of residue pairs.
        first, second, error = (np.asarray(doc[k]) for k in ("residue1", "residue2", "distance"))
        size = int(max(first.max(), second.max()))
        matrix = np.zeros((size, size))
        matrix[first - 1, second - 1] = error
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or matrix.shape[0] < 2:
        raise ValueError("not a square matrix")
    return matrix


_failed: dict[str, tuple[float, str]] = {}
FAILURE_MEMORY = 300  # seconds an unreachable AlphaFold is not asked again


def _cache_name(cache_dir: Path, structure: Path) -> Path:
    return Path(cache_dir) / f"{structure.stem}.pae.json"


@lru_cache(maxsize=4)
def _cached_matrix(path: str, mtime: float):
    with open(path) as handle:
        stored = json.load(handle)
    return stored, np.asarray(stored["predicted_aligned_error"], dtype=float)


def load_pae(structure: Path, cache_dir: Path, accession: str, entry_id: str | None,
             version: int | None, chain_sequence: str, label: str, fetch: bool = True,
             timeout: float = 15.0) -> tuple[dict, "np.ndarray"]:
    """(what AlphaFold says the entry is, its PAE matrix), from the cache or the network.

    The document is kept beside the structure in uploads/, so a rebuild, or a reload
    of the page, never asks twice. It is stored with the entry's sequence and name
    because both are needed to decide whether it applies to the file being reviewed.
    """
    cached = _cache_name(cache_dir, structure)
    try:
        stored, matrix = _cached_matrix(str(cached), cached.stat().st_mtime)
        if stored.get("accession") == accession and (not entry_id or stored.get("entry_id") == entry_id) \
                and (not version or stored.get("version") == version):
            return stored, matrix
    except (OSError, ValueError, KeyError):
        pass
    if not fetch:
        raise PaeUnavailable("AlphaFold's PAE for this model has not been fetched yet.", True)
    recent = _failed.get(accession)
    if recent and time.time() - recent[0] < FAILURE_MEMORY:
        raise PaeUnavailable(recent[1], True)

    try:
        try:
            models = json.loads(_fetch(f"{ALPHAFOLD}/api/prediction/{accession}", timeout, 1 << 22))
        except urllib.error.HTTPError as problem:
            if problem.code == 404:
                raise PaeUnavailable(f"AlphaFold has no entry for {accession}, so there is no PAE to check against.")
            raise
        if not isinstance(models, list) or not models:
            raise PaeUnavailable(f"AlphaFold has no entry for {accession}, so there is no PAE to check against.")
        # An entry can have isoforms and fragments. Take the one the file names; when it
        # does not say, take the one whose sequence the chain most resembles.
        pool = [m for m in models if entry_id and m.get("entryId") == entry_id] or models
        model = max(pool, key=lambda m: _shared(chain_sequence, str(m.get("sequence") or m.get("uniprotSequence") or "")))
        # Before anything about versions: if this is not the same protein, the rest
        # of the answer would only mislead (a different entry has its own latest version).
        found = _shared(chain_sequence, str(model.get("sequence") or model.get("uniprotSequence") or ""))
        if found < 0.9 * len(chain_sequence):
            raise PaeUnavailable(
                f"The sequence of {label} matches AlphaFold's entry for {accession} at only {found} "
                f"of {len(chain_sequence)} residues, so its PAE does not apply to it. "
                "Is it a different protein, an isoform or a mutant?")
        address = str(model.get("paeDocUrl") or "")
        if version and model.get("latestVersion") and int(model["latestVersion"]) != version:
            # The file is an older model than the one AlphaFold now serves. Its PAE
            # belongs to its own version; applying the newer one would describe a
            # different prediction.
            address = re.sub(r"_v\d+\.json$", f"_v{version}.json", address)
        if not address.startswith(f"{ALPHAFOLD}/files/"):
            raise PaeUnavailable(f"AlphaFold lists no PAE document for {accession}.")
        try:
            doc = json.loads(_fetch(address, timeout * 2, MAX_PAE_BYTES))
        except urllib.error.HTTPError as problem:
            if problem.code == 404 and version and int(model.get("latestVersion") or 0) != version:
                raise PaeUnavailable(
                    f"This is version {version} of the AlphaFold model and AlphaFold now serves "
                    f"version {model.get('latestVersion')}; the PAE of the older one is no longer "
                    "available, and the newer one describes a different prediction.")
            raise
        matrix = _pae_matrix(doc)
        entry = {
            "accession": accession, "entry_id": model.get("entryId"),
            "version": int(model.get("latestVersion") or 0) if not version else version,
            "sequence": str(model.get("sequence") or model.get("uniprotSequence") or ""),
            "source": address, "fetched_at": time.time(),
        }
    except PaeUnavailable:
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as problem:
        reason = getattr(problem, "reason", problem)
        message = f"Could not reach AlphaFold to get the PAE ({reason})."
        _failed[accession] = (time.time(), message)
        raise PaeUnavailable(message, True)
    except (ValueError, KeyError, TypeError, IndexError):
        raise PaeUnavailable(f"AlphaFold's PAE document for {accession} was not in a layout this service reads.")

    stored = {**entry, "predicted_aligned_error": matrix.astype(int).tolist()
              if np.allclose(matrix, np.rint(matrix)) else matrix.tolist()}
    try:
        scratch = cached.with_name(f"{cached.name}.{os.getpid()}.{threading.get_ident()}.part")
        scratch.write_text(json.dumps(stored))
        os.replace(scratch, cached)
    except OSError:
        pass  # a read-only uploads folder costs a refetch next time, nothing more
    return stored, matrix


# ---- turning the matrix into the figure that matters

def _matched(small: str, large: str) -> list[tuple[int, int]]:
    """Positions (in small, in large) that line up, in order: how two numberings are joined.

    Joined by sequence rather than by residue number, because a renumbered file, a
    trimmed model and a tagged construct all carry numbers that mean something else
    to the file next to them.
    """
    if not small or not large:
        return []
    if small == large:
        return [(i, i) for i in range(len(small))]
    at = large.find(small)
    if at >= 0:
        return [(i, at + i) for i in range(len(small))]
    pairs = []
    for block in difflib.SequenceMatcher(None, small, large, autojunk=False).get_matching_blocks():
        pairs += [(block.a + k, block.b + k) for k in range(block.size)]
    return pairs


def _class_of(value: float) -> str:
    if value < PAE_CONFIDENT:
        return "confident"
    if value >= PAE_INDEPENDENT:
        return "independent"
    return "uncertain"


def _cut_regions(numbers: list[int], pae: "np.ndarray", inside: "np.ndarray") -> list[list[int]]:
    """Contiguous stretches (as lists of positions) of the residues outside the construct.

    `pae` is the matrix over the residues in chain order. A stretch ends where the
    clustering changes its mind, where the construct begins, or where the numbering
    jumps (a gap in the model is not something to treat as continuous).
    """
    count = len(numbers)
    try:
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import squareform
        sym = (pae + pae.T) / 2
        np.fill_diagonal(sym, 0)
        labels = fcluster(linkage(squareform(sym, checks=False), "average"), DOMAIN_CUT, "distance")
    except Exception:
        # No clustering available: fall back to the coarsest honest answer, the
        # stretch before the construct and the one after it, which still keeps the
        # two sides apart.
        labels = np.zeros(count, dtype=int)
    runs: list[list[int]] = []
    for k in range(count):
        if inside[k]:
            continue
        if runs and runs[-1][-1] == k - 1 and numbers[k] == numbers[k - 1] + 1 \
                and labels[k] == labels[k - 1]:
            runs[-1].append(k)
        else:
            runs.append([k])
    # Fold slivers into whichever neighbour they sit best against. One with no
    # neighbour (walled in by the construct or a gap in the numbering) stays as it
    # is, since merging across either would invent a region that is not contiguous.
    def adjoins(first: list[int], second: list[int]) -> bool:
        return first[-1] + 1 == second[0] and numbers[second[0]] == numbers[first[-1]] + 1

    while True:
        choice = None
        for i, run in enumerate(runs):
            if len(run) >= MIN_REGION:
                continue
            options = []
            if i > 0 and adjoins(runs[i - 1], run):
                options.append((float(pae[np.ix_(run, runs[i - 1])].mean()), i - 1))
            if i + 1 < len(runs) and adjoins(run, runs[i + 1]):
                options.append((float(pae[np.ix_(run, runs[i + 1])].mean()), i + 1))
            if options:
                choice = (i, min(options)[1])
                break
        if choice is None:
            break
        low, high = sorted(choice)
        runs[low:high + 1] = [runs[low] + runs[high]]
    return runs


def _ranges(numbers: list[int]) -> str:
    return residue_ranges(numbers).replace(",", ", ")


def check_reference(reference: Path, chain: str | None, construct: Path, construct_chains: str,
                    accession: str | None, cache_dir: Path, fetch: bool = True) -> dict:
    """What the full-length reference is, and how far to trust it outside the construct.

    Always returns an answer. Two independent things are reported.

    `kind` is what the reference is (see classify_reference): experimental, alphafold,
    predicted or unclear. It decides how much weight context findings carry: full for
    a measured structure, advisory for a prediction however well placed, because one
    predicted model is a single conformation of a protein that moves.

    `status` is the PAE check, which only an AlphaFold model has. It is about how
    confidently each region was placed, a separate matter from conformation:
      not_applicable   no PAE check applies (a measured structure, or one nobody can tell)
      nothing_outside  it is a prediction, but the construct is all of it; nothing to place
      checked          the figures below are real
      could_not_check  it is a prediction, and the check could not be made; `reason` says
                       why, and `transient` whether a later try could work.
    "Could not check" is never worded as a pass: with high PAE the absence of
    warnings tells you nothing, and without a PAE nobody knows.
    """
    result = {"name": reference.name, "chain": chain, "kind": "unclear", "status": "not_applicable",
              "evidence": [], "accession": None, "reason": None, "transient": False,
              "method": None, "resolution": None,
              "construct": None, "regions": [], "groups": [], "level": None,
              "checked_at": time.time()}

    chains = read_chains(reference)
    confidence = read_confidence(reference)
    wanted = [c for c in re.split(r"[,\s]+", construct_chains or "")]
    own = read_chains(construct)
    construct_sequence = next((own[c][1] for c in wanted if c in own), None) \
        or next((seq for _, seq in own.values()), "")
    if not chain or chain not in chains:
        guess, fraction = best_chain(construct_sequence, chains)
        chain = guess if guess and fraction >= 0.5 else (next(iter(chains), None) if len(chains) == 1 else None)
    result["chain"] = chain

    # What it is is a property of the file, not of the chain chosen from it.
    said = classify_reference(reference, confidence)
    result.update(kind=said["kind"], evidence=said["evidence"], method=said["method"],
                  resolution=said["resolution"])
    if said["kind"] == "experimental" or said["why_unclear"] == "silent":
        result["reason"] = said["reason"]
        return result                      # nothing here for a PAE check to look at
    if chain is None or chain not in confidence:
        return result  # nothing readable to judge, and Binderscope will say what is wrong
    numbers, sequence, plddt, looks_like_plddt, _ = confidence[chain]

    shown = provenance(reference)
    result["status"] = "could_not_check"
    uniprot = accession or shown["accession"]
    result["accession"] = uniprot

    def stop(reason: str, transient: bool = False) -> dict:
        result.update(status="could_not_check", reason=reason, transient=transient)
        return result

    if result["kind"] == "unclear":
        return stop(said["reason"])

    # Where the construct sits in this reference, found by sequence.
    pairs = _matched(construct_sequence, sequence) if construct_sequence else []
    in_construct = np.zeros(len(sequence), dtype=bool)
    for _, k in pairs:
        in_construct[k] = True
    if not construct_sequence or len(pairs) < 0.5 * len(construct_sequence):
        return stop(f"The construct matches {reference.name} at only {len(pairs)} of "
                    f"{len(construct_sequence)} residues, so it cannot be said which part lies outside it.")
    result["construct"] = {"residues": _ranges([numbers[k] for k in range(len(numbers)) if in_construct[k]]),
                           "count": int(in_construct.sum())}
    if in_construct.all():
        result["status"] = "nothing_outside"
        return result
    if not uniprot:
        if result["kind"] == "alphafold":
            return stop(f"{reference.name} looks like an AlphaFold model but does not say which UniProt "
                        "entry it is, so its PAE cannot be looked up. Enter the accession under Advanced.")
        return stop(f"{reference.name} looks like a predicted model but does not say which program "
                    "made it. If it is an AlphaFold model, enter its UniProt accession under Advanced "
                    "and its PAE can be looked up; a model from another program has no PAE to check.")

    try:
        entry, matrix = load_pae(reference, cache_dir, uniprot, shown["entry_id"], shown["version"],
                                 sequence, f"{reference.name} chain {chain}", fetch=fetch)
    except PaeUnavailable as problem:
        return stop(str(problem), problem.transient)

    # The PAE belongs to AlphaFold's sequence. It applies to this file only if the
    # file's chain is that sequence, so compare them instead of trusting the name.
    model_sequence = entry.get("sequence") or ""
    if matrix.shape[0] != len(model_sequence):
        return stop(f"AlphaFold's PAE for {uniprot} covers {matrix.shape[0]} residues but its sequence "
                    f"has {len(model_sequence)}; the two do not belong together, so the check cannot be made.")
    joined = _matched(sequence, model_sequence)
    if len(joined) < 0.9 * len(sequence):
        return stop(f"The sequence of {reference.name} chain {chain} matches AlphaFold's entry for {uniprot} "
                    f"at only {len(joined)} of {len(sequence)} residues, so its PAE does not apply to it. "
                    "Is it a different protein, an isoform or a mutant?")
    position = {k: m for k, m in joined}
    kept = [k for k in range(len(sequence)) if k in position]
    local = np.array([position[k] for k in kept])
    pae = matrix[np.ix_(local, local)]
    nums = [numbers[k] for k in kept]
    inside = in_construct[kept]
    score = np.array([plddt[k] for k in kept])
    if not inside.any():
        return stop("None of the construct's residues are in the part of this model that AlphaFold's PAE covers.")

    cols = np.flatnonzero(inside)
    regions = []
    for run in _cut_regions(nums, pae, inside):
        toward = float(pae[np.ix_(cols, run)].mean())    # error in the region, aligned on the construct
        back = float(pae[np.ix_(run, cols)].mean())      # error in the construct, aligned on the region
        mean = (toward + back) / 2
        within = float(pae[np.ix_(run, run)].mean()) if len(run) > 1 else None
        regions.append({
            "start": nums[run[0]], "end": nums[run[-1]], "count": len(run),
            "pae": round(mean, 1), "pae_toward": round(toward, 1), "pae_back": round(back, 1),
            "internal_pae": None if within is None else round(within, 1),
            "plddt": round(float(score[run].mean()), 1) if looks_like_plddt else None,
            # Rounded first, so what is shown (50) and what is said (not disordered) agree.
            "disordered": bool(looks_like_plddt and round(float(score[run].mean())) < DISORDERED),
            "class": _class_of(mean),
        })
    if not regions:
        result["status"] = "nothing_outside"
        return result

    # Neighbouring regions that earn the same verdict are one thing to say, so the
    # headline can name "residues 180-618" and the table can still show every piece.
    groups = []
    for region in regions:
        last = groups[-1] if groups else None
        if last and last["class"] == region["class"] and last["_next"] == region["start"]:
            last["end"] = region["end"]; last["count"] += region["count"]
            last["regions"] += 1; last["_sum"] += region["pae"] * region["count"]
            last["pae_min"] = min(last["pae_min"], region["pae"])
            last["pae_max"] = max(last["pae_max"], region["pae"])
        else:
            groups.append({"start": region["start"], "end": region["end"], "count": region["count"],
                           "class": region["class"], "regions": 1, "_sum": region["pae"] * region["count"],
                           "pae_min": region["pae"], "pae_max": region["pae"]})
        groups[-1]["_next"] = region["end"] + 1
    for group in groups:
        group["pae"] = round(group.pop("_sum") / group["count"], 1)
        group.pop("_next")

    kinds = {r["class"] for r in regions}
    result.update(
        status="checked", regions=regions, groups=groups, reason=None, entry_id=entry.get("entry_id"),
        level="warn" if "independent" in kinds else "note" if "uncertain" in kinds else "good",
        overall_pae=round(float(pae[np.ix_(cols, np.flatnonzero(~inside))].mean()), 1),
        thresholds={"confident": PAE_CONFIDENT, "independent": PAE_INDEPENDENT},
    )
    return result


def no_reference_check() -> dict:
    """The record kept for a review built with no full-length reference."""
    return {"name": None, "chain": None, "kind": "none", "status": "none", "evidence": [],
            "accession": None, "reason": None, "transient": False, "method": None,
            "resolution": None, "construct": None, "regions": [], "groups": [], "level": None,
            "checked_at": time.time()}


# ------------------------------------------------------------- what Binderscope is

def _package_files() -> list[Path]:
    """The Python and profile files of the installed `binder_review`, or nothing."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("binder_review")
        root = Path(spec.origin).parent if spec and spec.origin else None
    except (ImportError, ValueError):
        root = None
    if root is None:
        return []
    return [p for p in root.rglob("*") if p.suffix in (".py", ".json")
            and "__pycache__" not in p.parts]


def _viewer_files() -> list[Path]:
    """Every file of the viewer sources the build would read, or nothing.

    The pages are as much of a review as the code that fills them: a changed
    template or stylesheet gives a reader a different page from the one the
    bundle holds. The folder is the one `viewer_build` itself would use, so a
    BINDERSCOPE_VIEWER override is fingerprinted rather than the checkout it
    replaces. Every file counts, not only the ones with known suffixes: a new
    font or image belongs to the page as much as its HTML does.
    """
    try:
        from binder_review.viewer_build import viewer_dir
        root = viewer_dir()
    except Exception:          # not installed, or no viewer to find: the build will say so
        return []
    return [p for p in root.rglob("*")
            if p.is_file() and not any(part.startswith(".") for part in p.parts[len(root.parts):])]


def _code_signature() -> tuple:
    # A package that bundles its own viewer/ would otherwise offer the same file twice.
    seen, files = set(), []
    for p in _package_files() + _viewer_files():
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            files.append(p)
    files.sort(key=str)
    return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)


_hash_cache: dict[tuple, str] = {}


def code_hash() -> str:
    """A fingerprint of the Binderscope that would run now: its code and its pages.

    A review built by an older Binderscope is not wrong for being old, but it may
    not have what a newer one reads (a reader fixed, a column mapped) or show what
    a newer one shows (a page relaid out, a rule group reordered), so the
    fingerprint is kept with the build and a changed one marks the review stale.
    The pages count because a bundle is a copy of them: nothing about a built
    review changes when a template does, which is exactly why it has gone stale.
    """
    try:
        signature = _code_signature()
    except OSError:
        return ""
    if not signature:
        return ""
    if signature not in _hash_cache:
        digest = hashlib.sha256()
        for path, _, _ in signature:
            try:
                digest.update(Path(path).read_bytes())
            except OSError:
                pass
        _hash_cache.clear()
        _hash_cache[signature] = digest.hexdigest()[:16]
    return _hash_cache[signature]


# -------------------------------------------------------- the record on disk

def home(folder: Path) -> Path:
    """The folder holding a campaign's review: project/, bundle/, build.log, review.json."""
    folder = Path(folder)
    return folder.parent / REVIEWS / folder.name


def record_path(folder: Path) -> Path:
    return home(folder) / "review.json"


def read_record(folder: Path) -> dict | None:
    return _read_json(record_path(folder))


def _update_record(folder: Path, **changes) -> dict | None:
    """Change fields of review.json, and only if the review still exists.

    A review can be deleted while its job is ending; writing then would bring
    the folder back from the dead, so a missing record is left missing.
    """
    record = read_record(folder)
    if record is None:
        return None
    record.update(changes)
    try:
        _write_json(record_path(folder), record)
    except OSError:
        return None
    return record


def log_file(folder: Path) -> Path:
    return home(folder) / "build.log"


def _exit_file(folder: Path) -> Path:
    return home(folder) / ".exit"


# ------------------------------------------------- hooks the job queue calls

def launch_spec(repo: Path, row) -> tuple[list[str], Path] | None:
    """The command for a queued review job and where its output goes.

    The build is wrapped so that its exit status lands in a file. A review that
    outlives a service restart is no longer our child, and waitpid then has
    nothing to say about how it ended; the file is how a restarted service finds
    out whether it worked.

    A campaign's review reads one run folder, the campaign's own. A project's reads
    several, and the record says which (`inputs.members`): each is passed as `id=path`,
    which is how Binderscope is told the runs apart, and the id is the one the build
    was recorded with so that the viewer's run filter and this record agree. The
    project's own folder, `row["project_folder"]`, is only a name and does not exist,
    so it must never reach the command line as a place to read. One `--target-pdb` and
    one `--reference-cif` serve every run, which is Binderscope's constraint and the
    reason a project whose members disagree about the construct is refused earlier.
    """
    folder = Path(row["project_folder"])
    record = read_record(folder)
    if record is None:
        return None
    given = record.get("inputs") or {}
    root = home(folder)
    members = given.get("members")
    runs = [f"{m['id']}={m['folder']}" for m in members] if members else [str(folder)]
    command = [
        str(Path(repo) / ".venv" / "bin" / "binder-review"), "import", *runs,
        "--target-pdb", given["construct"],
        # The bundle's own title. For a project that is the label the person
        # typed, not the slug: "@spike-ab12cd" is a key for the filesystem and a
        # URL, and nobody should have to read it on the page.
        "--name", (record.get("project") or folder.name) if members else folder.name,
        "--out", str(root / "project"),
        "--bundle", str(root / "bundle"),
    ]
    if given.get("reference"):
        command += ["--reference-cif", given["reference"],
                    "--placement", given.get("placement") or "interface"]
        if given.get("reference_chain"):
            command += ["--reference-chain", given["reference_chain"]]
        # Always said, even when it is the default, so that the command in the build log is the whole of what was asked.
        command += ["--partner-chains", given.get("partner_chains") or "auto"]
        if given.get("assembly"):
            command += ["--assembly", str(given["assembly"])]
        if given.get("uniprot"):
            command += ["--uniprot", given["uniprot"]]
        if given.get("online"):
            command.append("--online")
    # With no reference there is simply no --reference-cif: that is how Binderscope is
    # told to switch its context checks off. Everything that only means something
    # against a reference (its chain, the placement, the accession, the online lookups
    # that need one) is left off with it rather than passed to something that cannot use it.
    wrapped = ["/bin/sh", "-c", '"$@"; code=$?; echo "$code" > "$0"; exit "$code"',
               str(_exit_file(folder))] + command
    _exit_file(folder).unlink(missing_ok=True)
    return wrapped, log_file(folder)


def on_start(row) -> None:
    folder = Path(row["project_folder"])
    # What the build is made from. For a project that is every member's tables summed:
    # the project's own folder does not exist, and counting it would record zeros and
    # read as "out of date" the moment the build finished.
    members = (((read_record(folder) or {}).get("inputs") or {}).get("members"))
    counts = pooled_counts(m["folder"] for m in members) if members else design_counts(folder)
    _update_record(folder, state="running", started_at=time.time(), finished_at=None,
                   error=None, job_id=row["id"], counts=counts,
                   code_hash=code_hash())


def read_exit(row) -> int | None:
    try:
        return int(_exit_file(Path(row["project_folder"])).read_text().strip())
    except (OSError, ValueError):
        return None


def explain_failure(folder: Path, code: int | None) -> str:
    """The sentence to show for a build that did not finish.

    Binderscope ends a refused run with one line, "binder-review import: <why>",
    and exits 2. That line is already written for a person (for instance that the
    construct matches the reference at only 13 of 153 residues), so it is passed
    through untouched rather than replaced with something more generic.
    """
    try:
        with open(log_file(folder), "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 20_000))
            lines = [l.strip() for l in handle.read().decode(errors="ignore").splitlines()
                     if l.strip()]
    except OSError:
        lines = []
    for line in reversed(lines):
        said = re.match(r"binder-review[\w -]*:\s*(.+)", line)
        if said:
            return said.group(1)
    if code == 2:
        return "Binderscope refused these inputs; the build log says why."
    last = lines[-1][:200] if lines else "it left no output"
    return f"Binderscope stopped unexpectedly ({last}). The build log has the detail."


def outcome(row, code: int | None) -> tuple[str, str]:
    """What to record for a review whose process has ended. Mirrors JobStore._outcome."""
    folder = Path(row["project_folder"])
    if code is None:
        code = read_exit(row)
    if code is None:
        return "failed", "The build was interrupted before it finished. Build it again."
    if code == 0:
        if (home(folder) / "bundle" / "index.html").is_file():
            return "done", ""
        return "failed", "Binderscope finished without writing the review pages; the build log may say why."
    return "failed", explain_failure(folder, code)


def on_finish(row, state: str, note: str = "") -> None:
    folder = Path(row["project_folder"])
    if state == "cancelled":
        state, note = "failed", "The build was stopped before it finished."
    _update_record(folder, state=state if state in ("done", "failed") else "failed",
                   finished_at=time.time(), error=note or None)


def compose(event: str, job: dict) -> tuple[str, str, str, str]:
    """Returns (title, message, priority, tags), as notify.compose does for a campaign."""
    folder = Path(job["project_folder"])
    state = job.get("state", "done")
    record = read_record(folder) or {}
    # A project's folder name is a slug with a hash on the end; its label is what the person knows it by.
    name = record.get("project") or folder.name
    if state == "done":
        counts = record.get("counts") or design_counts(folder)
        return (f"{name}: review ready",
                f"{counts.get('passed', 0)} designs that passed, placed on the full-length "
                "target and checked. Open Review to look through them.",
                "default", "mag")
    if state == "cancelled":
        return f"{name}: review stopped", "The build was stopped before it finished.", "low", "octagonal_sign"
    return (f"{name}: review failed",
            job.get("message") or record.get("error") or "See the build log.",
            "high", "rotating_light")


# -------------------------------------------------------------- progress

# Binderscope prints "[  31.2s] <stage> ..." as a stage begins, "<stage> done in"
# as it ends, and "<what>: 88/352" inside the slow ones. The weights are roughly
# each stage's share of a real run, so the bar moves at about the speed of the work.
STAGES = {
    "read runs": ("reading", 3),
    "target frame": ("target", 4),
    "poses and context checks": ("poses", 3),
    "interface analysis": ("interface", 60),
    "families and diversity": ("families", 18),
    "modes and near-miss records": ("modes", 2),
    "specificity panel": ("specificity", 1),
    "export and validation": ("export", 6),
    "bundle": ("bundle", 3),
}
_BEGIN = re.compile(r"^\[\s*[\d.]+s\]\s+(.+?)\s+\.\.\.\s*$")
_END = re.compile(r"^\[\s*[\d.]+s\]\s+(.+?)\s+done in\b")
_WITHIN = re.compile(r"^\[\s*[\d.]+s\]\s+[\w -]+?:\s+(\d+)/(\d+)\s*$")


def parse_progress(text: str) -> tuple[str | None, int | None]:
    """(step, percent) from a build log. Best effort: a newer Binderscope may
    rename its stages, and then this says less rather than something wrong."""
    finished: set[str] = set()
    current, fraction = None, 0.0
    for line in text.splitlines():
        begun = _BEGIN.match(line)
        if begun:
            current, fraction = begun.group(1), 0.0
            continue
        ended = _END.match(line)
        if ended:
            finished.add(ended.group(1))
            continue
        inside = _WITHIN.match(line)
        if inside and current and int(inside.group(2)):
            fraction = min(1.0, int(inside.group(1)) / int(inside.group(2)))
    known = [name for name in STAGES if name in finished]
    if current is None and not known:
        return None, None
    percent = sum(STAGES[name][1] for name in known)
    if current in STAGES and current not in finished:
        percent += STAGES[current][1] * fraction
    step = STAGES[current][0] if current in STAGES else (current if current else None)
    return step, min(99, int(round(percent)))


# ============================================================== the service side

class Reviews:
    """What the routes need: find campaigns, describe them, start and remove reviews."""

    def __init__(self, store, roots: list[Path], uploads: Path, repo: Path, constructs=None):
        self.store = store
        self.roots = []
        for root in roots:
            if Path(root) not in self.roots:
                self.roots.append(Path(root))
        self.uploads = Path(uploads)
        self.repo = Path(repo)
        # Where to find a target structure for a run whose own is not on this
        # machine (imports.Imports.construct_for). Optional: without it a run
        # that names a missing target simply cannot be reviewed, which is what
        # happened before anything could be imported.
        self.constructs = constructs
        # One person pressing Build twice, or two people pressing it at once, must
        # not leave two builds writing into the same folder.
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- finding

    def folder(self, name: str) -> Path:
        """The campaign folder a URL names, or a refusal.

        The name is untrusted. It is shaped first, then looked for as a direct
        child of a known results root, and it must contain the file BindCraft or
        this service writes into every campaign, so that a folder which merely
        happens to have that name is not mistaken for one.
        """
        if not CAMPAIGN_NAME.fullmatch(name or ""):
            raise ReviewError("That is not a campaign name.", 404)
        for root in self.roots:
            candidate = root / name
            if candidate.is_dir() and self._is_campaign(candidate):
                return candidate
        raise ReviewError(f"There is no campaign called {name}.", 404)

    @staticmethod
    def _is_campaign(folder: Path) -> bool:
        return is_campaign_folder(folder)

    # ---------------------------------------------------------------- reading a campaign

    def _settings(self, folder: Path) -> tuple[dict, dict]:
        """(the whole metadata, the settings inside it).

        campaign_metadata.json is what BindCraft resolved and so is preferred;
        campaign.json is what this service asked for, and has the same fields
        flat, which covers a campaign that never got as far as writing metadata.
        """
        metadata = _read_json(folder / "campaign_metadata.json") or {}
        settings = metadata.get("settings")
        if not isinstance(settings, dict):
            settings = _read_json(folder / "campaign.json") or {}
        return metadata, settings

    def _construct(self, folder: Path, settings: dict) -> tuple[dict | None, str | None]:
        """(the construct as the form shows it, or None, why a review cannot go ahead)."""
        targets = [t for t in settings.get("targets") or []
                   if isinstance(t, dict) and t.get("objective") != "detarget"]
        if not targets or not targets[0].get("target_path"):
            return None, "This campaign does not record which structure it was given."
        if len(targets) > 1:
            return None, ("This campaign designed against several targets at once; "
                          "Review looks at one target.")
        target = targets[0]
        raw = Path(str(target["target_path"])).expanduser()
        path = raw if raw.is_absolute() else next(
            (c for c in (self.repo / raw, folder / raw) if c.is_file()), self.repo / raw)
        if not path.is_file() and self.constructs:
            # BindCraft records where the target was, and does not copy it into the
            # campaign, so a run carried here from another machine names a path that
            # is not on this one. An import may be given the structure; it stands in
            # only when the recorded one is missing, so a run whose own target is
            # here is never second-guessed.
            given = self.constructs(folder)
            if given and Path(given).is_file():
                path = Path(given)
        construct = {"path": str(path), "exists": path.is_file(),
                     "chains": str(target.get("chains") or ""), "residues": "",
                     "name": str(target.get("name") or path.stem)}
        if not path.is_file():
            return construct, (f"The construct file this campaign was given ({path.name}) "
                               "is no longer where it was. Put it back, or this campaign "
                               "cannot be reviewed.")
        chains = read_chains(path)
        declared = [c for c in re.split(r"[,\s]+", construct["chains"]) if c]
        wanted = [c for c in declared if c in chains] or list(chains)
        if not construct["chains"]:
            construct["chains"] = ",".join(wanted)
        if len(declared) > 1:
            # Binderscope reads every design as target chain A and binder chain B. A second target chain would be read as the
            # binder, and every number after it would be about the wrong molecule, so this is refused rather than reviewed.
            return construct, (f"This campaign's target has {len(declared)} chains ({', '.join(declared)}). Review reads "
                               "the target from one chain, so it would take the second one for the binder; "
                               "a target of several chains cannot be reviewed yet.")
        if len(declared) == 1 and declared[0] != "A":
            return construct, (f"This campaign was designed against chain {declared[0]} of its structure, and Review reads "
                               "the target from chain A of the construct file. Trim the target to that one chain, "
                               "rename it A, and design against the trimmed file to review it.")
        if len(wanted) == 1:
            construct["residues"] = residue_ranges(chains[wanted[0]][0])
        else:
            construct["residues"] = ";".join(
                f"{c}:{residue_ranges(chains[c][0])}" for c in wanted)
        return construct, None

    def _construct_sequence(self, construct: dict) -> str:
        chains = read_chains(Path(construct["path"]))
        for chain in re.split(r"[,\s]+", construct["chains"]):
            if chain in chains:
                return chains[chain][1]
        return next((seq for _, seq in chains.values()), "")

    # ---------------------------------------------------------------- the review's state

    def _review(self, folder: Path, counts: dict | None = None, members: list | None = None) -> dict | None:
        """The `review` object of the contract, or None when none was ever started.

        `members` is for a project's review only: the project's current members, when the
        caller already has them, so that they are not looked up twice.
        """
        record = read_record(folder)
        if record is None:
            return None
        state = record.get("state") or "failed"
        error = record.get("error")
        bundle = home(folder) / "bundle" / "index.html"
        if state == "done" and not bundle.is_file():
            state, error = "failed", "The review's pages are missing. Build it again."
        stale, why = False, None
        if state == "done":
            if (record.get("inputs") or {}).get("members"):
                changed = self._drifted(record, counts, members)
            else:
                now = counts if counts is not None else design_counts(folder)
                changed = record.get("counts") != now
            if changed:
                stale, why = True, "designs"
            elif record.get("code_hash") and record["code_hash"] != code_hash():
                stale, why = True, "code"
        live = self.store.find("analysis", folder, live=True)
        return {
            "state": state,
            "built_at": _iso(record.get("finished_at")) if state == "done" else None,
            "stale": stale,
            "stale_reason": why,
            "bundle_url": f"/review/{folder.name}/" if state == "done" else None,
            "error": error,
            "started_at": _iso(record.get("started_at")),
            "job_id": (live or {}).get("id") or record.get("job_id"),
            # Which chain was the target, which were partners and why, as the build did it.
            "composition": self._built_composition(folder, record),
            # What was known about the reference's reliability when it was built.
            # Kept with the build, so a review opened a week later is read with the
            # caveat it had, even if AlphaFold cannot be reached now.
            "reference_check": record.get("reference_check"),
        }

    def status(self, folder: Path, counts: dict | None = None) -> dict | None:
        """One campaign's review state, or None when no review was ever started.

        This is `_review` made callable from outside the module. The Results page
        already knows which folders it is showing and needs only their review
        state; the alternative, `campaigns()`, scans every results root and reads
        three CSVs per campaign, which is far too much work to answer a question
        about two or three folders. Pass `counts` when the caller has them already
        (staleness compares against them) so the tables are not read twice.
        """
        return self._review(Path(folder), counts)

    def serves(self, folder: Path) -> bool:
        """Whether /review/<name>/ would open this very folder.

        The review routes find a campaign by name, as a direct child of a known
        results root. A folder anywhere else (or one the name resolves past, because
        another root holds a campaign of the same name) would get a link that 404s
        or opens somebody else's review, so callers ask before they emit one.
        """
        folder = Path(folder)
        try:
            return self.folder(folder.name).resolve() == folder.resolve()
        except (ReviewError, OSError, RuntimeError):  # RuntimeError: a symlink loop, before 3.13
            return False

    @staticmethod
    def _built_composition(folder: Path, record: dict) -> dict | None:
        """The chain composition the build used: the frame's own record when the build has made one, else what was decided at the request."""
        frame = home(folder) / "project" / "target" / "frame.json"
        if record.get("state") == "done" and frame.is_file():
            try:
                found = json.loads(frame.read_text()).get("composition")        # frame.json is the frame's meta
                if found:
                    return found
            except (OSError, ValueError):
                pass
        return record.get("composition")

    @staticmethod
    def _unwritable(folder: Path) -> str | None:
        """A sentence if the review's folder cannot be made, else None."""
        spot = home(folder)
        while not spot.exists() and spot != spot.parent:
            spot = spot.parent
        if os.access(spot, os.W_OK):
            return None
        return f"This service cannot write into {spot}, which is where reviews are kept."

    def _design_job(self, folder: Path) -> dict | None:
        return self.store.find("design", folder)

    # ---------------------------------------------------------------- GET /api/review

    def campaigns(self) -> list[dict]:
        found, seen = [], set()
        for root in self.roots:
            try:
                entries = sorted(root.iterdir())
            except OSError:
                continue
            for folder in entries:
                if folder.name in seen or not CAMPAIGN_NAME.fullmatch(folder.name):
                    continue
                if not folder.is_dir() or not self._is_campaign(folder):
                    continue
                seen.add(folder.name)
                counts = design_counts(folder)
                if not (counts["passed"] or counts["ranked"]):
                    continue  # nothing finished to review yet
                started = max((p.stat().st_mtime for p in
                               (folder / "campaign.json", folder / "campaign_metadata.json")
                               if p.is_file()), default=0)
                job = self._design_job(folder)
                found.append((started, {
                    "campaign": folder.name,
                    "project_folder": str(folder),
                    "job_id": job["id"] if job else None,
                    "designs": counts,
                    "review": self._review(folder, counts),
                }))
        return [entry for _, entry in sorted(found, key=lambda pair: -pair[0])]

    # ---------------------------------------------------------------- GET /api/review/{c}

    def describe(self, name: str) -> dict:
        folder = self.folder(name)
        metadata, settings = self._settings(folder)
        construct, why_not = self._construct(folder, settings)
        counts = design_counts(folder)
        if why_not is None and not (counts["passed"] or counts["ranked"]):
            why_not = "This campaign has no finished designs yet."
        if why_not is None:
            why_not = self._unwritable(folder)
        self._backfill_check(folder, construct)
        review = self._review(folder, counts)
        given = ((read_record(folder) or {}).get("inputs") or {})
        job = self._design_job(folder)
        return {
            "campaign": folder.name,
            "project_folder": str(folder),
            "job_id": job["id"] if job else None,
            "construct": construct or {"path": None, "exists": False, "chains": "",
                                       "residues": "", "name": folder.name},
            "hotspots": next((str(t.get("hotspots") or "") for t in settings.get("targets") or []
                              if isinstance(t, dict) and t.get("objective") != "detarget"), ""),
            "modality": settings.get("modality"),
            "version": metadata.get("version"),
            "designs": counts,
            # `none` is true for a review built with no reference, and for an older one
            # built against the construct itself, which came to the same thing.
            "reference": {"path": given.get("reference"), "chain": given.get("reference_chain"),
                          "partner_chains": given.get("partner_chains") or ("auto" if given.get("reference") else None),
                          "assembly": given.get("assembly"),
                          "uniprot": given.get("uniprot"),
                          "none": self._was_none(given, construct)},
            # Without the construct there is nothing to compare a candidate with,
            # and nothing to review, so there is nothing worth suggesting.
            "suggestions": (self._suggestions(folder, construct)
                            if construct and construct["exists"] else []),
            "review": review,
            "can_build": why_not is None,
            "why_not": why_not,
        }

    def _construct_of(self, name: str) -> dict:
        """The construct a reference is to be compared with, for a campaign's name or a project's `@slug`, or a refusal.

        The two reference questions (`/reference` and `/chains`) are the same for both: they
        ask about a file against the construct a review would be built on. For a campaign that
        is its own. For a project it is the one every buildable member names, which is what
        `_agreed` finds, and it refuses in the words `describe_project` uses when the members
        disagree or too few can be reviewed, since in either case no single construct exists
        to compare with.
        """
        if name.startswith("@"):
            label = self._label_for(name)
            construct, why = self._agreed(label, self._members(label))
            if construct is None:
                raise ReviewError(why or "This project has no construct to compare with.")
            return construct
        folder = self.folder(name)
        _, settings = self._settings(folder)
        construct, _ = self._construct(folder, settings)
        if not construct or not construct["exists"]:
            raise ReviewError("This campaign's construct file is missing, so nothing can be compared with it.")
        return construct

    # ---------------------------------------------------------------- reference confidence

    def check_reference(self, name: str, path: str, chain: str = "", uniprot: str = "",
                        fetch: bool = True) -> dict:
        """GET /api/review/{campaign}/reference (a campaign or a project's @slug): is the chosen reference one to trust?

        Never refuses a reference; it only describes. An unreadable or missing file
        is the one thing it cannot say anything about.
        """
        construct = self._construct_of(name)
        reference = Path(str(path or "").strip()).expanduser()
        if not reference.is_absolute() or reference.suffix.lower() not in STRUCTURE_SUFFIXES \
                or not reference.is_file():
            raise ReviewError("There is no reference structure at that path.", 404)
        accession = str(uniprot or "").strip().upper()
        if accession and not UNIPROT.fullmatch(accession):
            accession = ""
        return self._assess(reference, str(chain or "").strip(), construct, accession, fetch)

    def _chains_of(self, reference: Path, construct: dict, chain: str, partners: str, assembly: str) -> dict:
        """The composition of a reference for this campaign's construct (see _composition)."""
        sequence = self._construct_sequence(construct)
        try:
            mtime = reference.stat().st_mtime
            made = Path(construct["path"]).stat().st_mtime
        except OSError:
            mtime = made = 0.0
        return copy.deepcopy(_composition(str(reference), mtime, str(construct["path"]), made, sequence, chain, partners, assembly))

    def chains(self, name: str, path: str, chain: str = "", partners: str = "auto", assembly: str = "") -> dict:
        """GET /api/review/{campaign}/chains (a campaign or a project's @slug): which chain of a reference is the target, which are partners, and why.

        Never refuses a readable reference: the answer may itself say that no chain carries the construct
        (`problem`), which is the page's to show, not an error of the request.
        """
        construct = self._construct_of(name)
        reference = Path(str(path or "").strip()).expanduser()
        if not reference.is_absolute() or reference.suffix.lower() not in STRUCTURE_SUFFIXES \
                or not reference.is_file():
            raise ReviewError("There is no reference structure at that path.", 404)
        chain = str(chain or "").strip()
        if chain and not CHAIN_ID.fullmatch(chain):
            raise ReviewError("A chain name is a letter or a digit, like A.")
        wanted = partner_request(partners)
        if any(not PARTNER_NAME.fullmatch(n) for n in wanted.split(",") if wanted not in ("auto", "none", "all")):
            raise ReviewError("Partner chains are named by their chain letters, like B or B,C.")
        return self._chains_of(reference, construct, chain, wanted, str(assembly or "").strip())

    def _assess(self, reference: Path, chain: str, construct: dict, accession: str,
                fetch: bool) -> dict:
        try:
            return check_reference(reference, chain or None, Path(construct["path"]),
                                   construct["chains"], accession or None, self.uploads, fetch)
        except Exception as problem:  # a check that fails must not stand in the way of a review
            return {"name": reference.name, "kind": "unknown", "status": "could_not_check",
                    "reason": f"The check of the reference's confidence failed ({problem}).",
                    "transient": True, "regions": [], "groups": [], "checked_at": time.time()}

    @staticmethod
    def _was_none(given: dict, construct: dict | None) -> bool:
        """Whether a recorded review used no full-length reference.

        Before "no reference" was a choice, the way to get it was to hand over the
        construct itself as the reference; such a review is read as what it was.
        """
        if given.get("no_reference"):
            return True
        used, own = given.get("reference"), (construct or {}).get("path")
        if not used or not own:
            return False
        try:
            return Path(used).resolve() == Path(own).resolve()
        except OSError:
            return False

    def _backfill_check(self, folder: Path, construct: dict | None) -> None:
        """Give a finished review that was built before this check existed its caveat.

        Only a settled answer is kept. "AlphaFold could not be reached" is not one,
        and recording it would hide the real answer once the network is back. An
        answer from before reference kinds existed (kind "other") is redone, since it
        cannot say whether the reference was measured or predicted.
        """
        record = read_record(folder)
        earlier = (record or {}).get("reference_check")
        settled = earlier is not None and not earlier.get("transient") and earlier.get("kind") != "other"
        if not record or record.get("state") != "done" or settled \
                or not construct or not construct["exists"]:
            return
        given = record.get("inputs") or {}
        if self._was_none(given, construct):
            _update_record(folder, reference_check=no_reference_check())
            return
        reference = Path(str(given.get("reference") or ""))
        if not reference.is_file():
            return
        found = self._assess(reference, given.get("reference_chain") or "", construct,
                             given.get("uniprot") or "", fetch=True)
        if not found.get("transient"):
            _update_record(folder, reference_check=found)

    def _suggestions(self, folder: Path, construct: dict, more: tuple | list = ()) -> list[dict]:
        """Full-length references that turned up without anyone being asked.

        `more` is for a project: the other campaigns' folders, looked in as well as
        `folder`, so a reference left in any member's folder is offered.

        Each is compared with the construct, so the list can say which ones look
        like the same protein. The accession is read from the filename or the
        file's own header and is passed along as a suggestion only: a filename can
        lie, and Binderscope will not trust it either without being told to.
        """
        own = Path(construct["path"])
        sequence = self._construct_sequence(construct) if construct["exists"] else ""
        candidates: list[tuple[Path, str]] = []
        try:
            uploads = sorted((p for p in self.uploads.iterdir()
                              if p.is_file() and p.suffix.lower() in STRUCTURE_SUFFIXES),
                             key=lambda p: -p.stat().st_mtime)
        except OSError:
            uploads = []
        # Each source is capped on its own, so a crowded uploads folder cannot push
        # the files sitting in the campaign itself out of the list.
        candidates += [(p, "uploaded here") for p in uploads[:MAX_EXAMINED]]
        for where_from in (folder, *more):
            try:
                candidates += [(p, "in the campaign folder")
                               for p in sorted(Path(where_from).iterdir())[:MAX_EXAMINED]
                               if p.is_file() and p.suffix.lower() in STRUCTURE_SUFFIXES]
            except OSError:
                pass

        listed, seen = [], {own.resolve()}
        for path, where in candidates:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            chain, fraction = best_chain(sequence, read_chains(path)) if sequence else (None, 0.0)
            if sequence and chain is None:
                continue  # no protein to compare against, so no use as a reference
            fits = (fraction >= FITS) if sequence else None
            if fits:
                why = (f"{where}; contains the construct ({round(fraction * len(sequence))} "
                       f"of {len(sequence)} residues found on chain {chain})")
            elif sequence:
                why = (f"{where}; does not look like this target "
                       f"({round(fraction * len(sequence))} of {len(sequence)} residues match "
                       f"on its best chain)")
            else:
                why = where
            accession = uniprot_in_name(path.name) or uniprot_in_file(path)
            listed.append({"path": str(path), "name": path.name, "why": why,
                           "uniprot": accession, "chain": chain if fits else None,
                           "fits": fits, "kind": classify_reference(path, read_confidence(path))["kind"]})
        listed.sort(key=lambda s: (s["fits"] is not True, s["uniprot"] is None))
        # The construct itself is not offered: reviewing against it is the same as
        # having no reference, which is its own choice on the page.
        return listed[:MAX_SUGGESTIONS]

    # ---------------------------------------------------------------- POST

    def _asked_reference(self, construct: dict, form: dict) -> dict:
        """What a build request says about the reference, checked, or a refusal.

        The same questions for a campaign and for a project, so the same function: whether
        the person chose a full-length reference or chose none out loud, whether the file is
        readable and is not the construct itself, which chain carries the construct (decided
        here, from the sequence, by the function the build uses) and what AlphaFold's PAE
        says about trusting it. `construct` is the one construct the build will be given.
        """
        asked = form.get("no_reference")
        nothing = asked is True or str(asked).strip().lower() in ("true", "1", "yes")
        given = str(form.get("reference_path") or "").strip()
        if nothing and given:
            raise ReviewError("Choose a full-length reference or review without one, not both.")
        if not nothing and not given:
            raise ReviewError("Choose the full-length reference structure to compare against, "
                              "or choose to review without one.")

        reference, chain, accession, placement, online = None, "", "", "interface", False
        partners, assembly, composition = "auto", "", None
        if nothing:
            confidence = no_reference_check()
        else:
            reference = Path(given).expanduser()
            if not reference.is_absolute() or reference.suffix.lower() not in STRUCTURE_SUFFIXES:
                raise ReviewError("The reference should be a structure file: PDB or mmCIF.")
            if not reference.is_file():
                raise ReviewError(f"There is no reference structure at {reference}.")
            if reference.resolve() == Path(construct["path"]).resolve():
                raise ReviewError("That file is the construct itself, which holds nothing the construct "
                                  "left out. To review without a full-length reference, choose that instead.")
            chains = read_chains(reference)
            if not chains:
                raise ReviewError(f"No protein could be read from {reference.name}. "
                                  "Is it a PDB or mmCIF structure?")

            chain = str(form.get("reference_chain") or "").strip()
            if chain:
                if not CHAIN_ID.fullmatch(chain):
                    raise ReviewError("A chain name is a letter or a digit, like A.")
                if chain not in chains:
                    raise ReviewError(
                        f"{reference.name} has no chain {chain}; it has {', '.join(chains)}.")
            partners = partner_request(form.get("partner_chains"))
            if any(not PARTNER_NAME.fullmatch(n) for n in partners.split(",") if partners not in ("auto", "none", "all")):
                raise ReviewError("Partner chains are named by their chain letters, like B or B,C.")
            assembly = str(form.get("assembly") or "").strip()
            if assembly and not re.fullmatch(r"[A-Za-z0-9]{1,6}", assembly):
                raise ReviewError("An assembly is named by its number, like 1.")
            # The chain is decided here, from the sequence, by the same function the build uses, and passed on by name:
            # what the page showed is what is built, and the reason is kept with the review. Nothing is left to a default.
            composition = self._chains_of(reference, construct, chain, partners, assembly)
            if composition.get("problem"):
                raise ReviewError(composition["problem"])
            if composition["target"].get("warning"):
                # a chain that does not hold the construct cannot be built on; the build would refuse it a few seconds in
                raise ReviewError(composition["target"]["warning"][0].upper() + composition["target"]["warning"][1:]
                                  + ". Choose the chain that carries the construct.")
            chain = str(composition["target"]["chain"])

            accession = str(form.get("uniprot") or "").strip().upper()
            if accession and not UNIPROT.fullmatch(accession):
                raise ReviewError("A UniProt accession looks like P69905 or P0DTC2.")

            placement = str(form.get("placement") or "interface").strip().lower()
            if placement not in PLACEMENTS:
                raise ReviewError("Placement is either interface or whole.")
            online = form.get("online")
            online = online is True or str(online).strip().lower() in ("true", "1", "yes")

            # Advice, never a gate: whatever this finds, the build goes ahead.
            confidence = self._assess(reference, chain, construct, accession, fetch=True)
        return {"reference": reference, "chain": chain, "accession": accession,
                "placement": placement, "online": online, "partners": partners,
                "assembly": assembly, "composition": composition, "confidence": confidence}

    def _enqueue(self, folder: Path, construct: dict, counts: dict, asked: dict, *, topic: str,
                 name: str, owner: str, project: str, members: list[dict] | None = None,
                 label: str | None = None) -> dict:
        """Write the record of a review and queue its job: the half that campaigns and projects share.

        `folder` is only ever a key here: the review's own folder is `home(folder)`, and
        for a project `folder` is a name that is never created. `members` and `label` are a
        project's: who was pooled, and what the project was called when it was.
        """
        reference = asked["reference"]
        with self._lock:
            self._discard(folder)
            root = home(folder)
            try:
                root.mkdir(parents=True)
                record = {
                    "state": "queued", "started_at": None, "finished_at": None,
                    "inputs": {"construct": construct["path"],
                               "reference": str(reference) if reference else None,
                               "no_reference": reference is None,
                               "reference_chain": asked["chain"] or None,
                               "uniprot": asked["accession"] or None,
                               "partner_chains": asked["partners"] if reference else None,
                               "assembly": asked["assembly"] or None,
                               "placement": asked["placement"], "online": asked["online"]},
                    "counts": counts, "error": None, "code_hash": code_hash(),
                    "queued_at": time.time(), "reference_check": asked["confidence"],
                    "composition": asked["composition"],
                }
                if members is not None:
                    record["inputs"]["members"] = members
                    record["project"] = label
                _write_json(record_path(folder), record)
            except OSError as problem:
                raise ReviewError(f"Could not make {root}: {problem}", 500)
            job_id = self.store.submit(
                name=name, owner=owner, project=project,
                campaign_path=record_path(folder), project_folder=folder, gpus_wanted=0,
                notify_topic=topic, kind="analysis")
        return {"id": job_id, "state": "queued", "reference": "none" if reference is None else "given"}

    def start(self, name: str, form: dict) -> dict:
        folder = self.folder(name)
        metadata, settings = self._settings(folder)
        construct, why_not = self._construct(folder, settings)
        counts = design_counts(folder)
        if why_not is None and not (counts["passed"] or counts["ranked"]):
            why_not = "This campaign has no finished designs yet."
        if why_not is None:
            why_not = self._unwritable(folder)
        if why_not:
            raise ReviewError(why_not)

        asked = self._asked_reference(construct, form)
        topic = (self._design_job(folder) or {}).get("notify_topic", "")
        return self._enqueue(folder, construct, counts, asked, topic=topic,
                             name=f"{folder.name} (review)",
                             owner=str(form.get("owner") or "").strip()[:64],
                             project=str(form.get("project") or "").strip()[:64])

    # ---------------------------------------------------------------- a project's review
    #
    # One review across every campaign in a project. Where it lives:
    #
    #   job row's project_folder   <primary root>/@<slug>            a NAME. Never created.
    #   review.json, project/,     <primary root>/_reviews/@<slug>/  what home() makes of that name
    #   bundle/, build.log
    #
    # `home()` only needs a folder's parent and name, so a path that does not exist works
    # as the key of a review exactly as a campaign folder does, and every reader of a
    # review by its home (the job queue's log and exit-file lookups, `_review`, `_discard`,
    # the Storage page) keeps working unchanged. What must never happen is something
    # treating `<root>/@<slug>` as a directory: it is not one, and it is not made.
    # The review goes in the *primary* root because it belongs to no one campaign's
    # root, and a root that disappears must not take a project's review with it unless it
    # was the primary one, which is the one a person has to have.

    def project_folder(self, slug: str) -> Path:
        """The notional folder a project slug names, or a refusal. Nothing is looked for on disk.

        The slug is untrusted (it arrives in a URL) and is shaped before it is joined to a
        path: `@`, lowercase letters, digits and dashes, no separator and nothing that can
        climb. Unlike `folder()` it does not ask whether anything is there, because the
        folder is there by convention only.
        """
        if not PROJECT_NAME.fullmatch(slug or ""):
            raise ReviewError("That is not a project name.", 404)
        return self.roots[0] / slug

    def _label_for(self, slug: str) -> str:
        """The label of the project a slug names, or a refusal.

        A slug cannot be turned back into a label (it holds a hash), so labels are asked for
        and slugged until one matches: first the live ones, then the label an existing review
        recorded, which is how a project whose campaigns have all been archived can still be
        looked at and discarded.
        """
        folder = self.project_folder(slug)
        for entry in self.store.projects():
            label = entry["name"]
            if label.strip() and project_slug(label) == slug:
                return label
        label = (read_record(folder) or {}).get("project")
        if label and project_slug(label) == slug:
            return label
        raise ReviewError("There is no project by that name.", 404)

    def _members(self, label: str, entries: list[dict] | None = None) -> list[dict]:
        """Every live campaign filed under a project, oldest first, each with whether it can be pooled.

        Oldest first so that the runs read in the order the work was done and an id suffix
        (-2) goes to the later one, however many campaigns are added after. `entries` is
        for a caller that already holds the store's answer for every project.
        """
        if entries is None:
            entries = self.store.campaigns_by_folder(project=label, archived="live")
        found = []
        for entry in sorted(entries, key=lambda e: e["job_id"]):
            folder = Path(entry["folder"])
            counts = design_counts(folder)
            construct, why = None, None
            if not folder.is_dir() or not self._is_campaign(folder):
                why = f"The folder of {entry['name']} is gone."
            else:
                _, settings = self._settings(folder)
                construct, why = self._construct(folder, settings)
                if why is None and not (counts["passed"] or counts["ranked"]):
                    why = "This campaign has no finished designs yet."
            found.append({"name": entry["name"], "folder": str(folder), "job_id": entry["job_id"],
                          "state": entry["state"], "designs": counts, "construct": construct,
                          "buildable": why is None, "why_not": why})
        return found

    @staticmethod
    def _agreed(label: str, members: list[dict]) -> tuple[dict | None, str | None]:
        """(the construct every buildable member names, or None, why a pooled review cannot go ahead).

        A pooled review is only meaningful if every campaign was designing against the
        same thing: Binderscope is given one `--target-pdb` for the whole build and reads
        every design as a binder on it, so a member designed against another construct would
        be placed on the wrong molecule and every number after that would be about it. So
        the members must name the same construct file (compared as resolved paths), and when
        they do not, the refusal says which members name which file.
        """
        usable = [m for m in members if m["buildable"]]
        if len(usable) < 2:
            left_out = [f"{m['name']}: {m['why_not']}" for m in members if not m["buildable"]]
            why = (f"A review across a project needs at least two of its campaigns to have finished "
                   f"designs, and {label} has {len(usable)} of {len(members)}.")
            return None, why + (" " + " ".join(left_out) if left_out else "")
        by_file: dict[str, list[dict]] = {}
        for m in usable:
            path = Path(m["construct"]["path"])
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            by_file.setdefault(key, []).append(m)
        if len(by_file) > 1:
            # File names read better than paths, until two different files share one.
            names = [Path(k).name for k in by_file]
            says = [f"{', '.join(m['name'] for m in group)} use{'s' if len(group) == 1 else ''} "
                    f"{Path(k).name if len(set(names)) == len(names) else k}"
                    for k, group in by_file.items()]
            return None, ("The campaigns of this project were not designed against the same construct, "
                          "and one review is built on one construct: " + "; ".join(says) + ".")
        return usable[0]["construct"], None

    def _drifted(self, record: dict, counts: dict | None, members: list | None) -> bool:
        """Whether the campaigns a project's review was built from have changed since.

        Compared two ways, because each misses something the other catches. The summed
        counts notice a design arriving in any member. The members themselves notice one
        being added or removed or filed elsewhere, including the case the sums cannot see:
        a campaign swapped for another that happens to have the same four numbers.
        """
        label = record.get("project") or ""
        if members is None:
            members = self._members(label) if label.strip() else []
        usable = [m for m in members if m["buildable"]]
        now = counts if counts is not None else pooled_counts(m["folder"] for m in usable)
        built = {str(Path(m["folder"]).resolve()) for m in (record.get("inputs") or {}).get("members") or []}
        here = {str(Path(m["folder"]).resolve()) for m in usable}
        return record.get("counts") != now or built != here

    def describe_project(self, label: str, members: list[dict] | None = None) -> dict:
        """GET /api/review/project/{slug}: everything the form needs to review a project, and whether it can.

        Refuses, through `can_build` and `why_not` rather than an error (the page shows the
        reason beside the project), when the label is empty, when fewer than two members
        have a campaign that can be reviewed, and when the members were not designed against
        the same construct. Otherwise it answers as `describe` does for one campaign: the
        construct, the counts, the reference this review used and the references that
        turned up unasked, so the form offers the same choices. `members` is for
        a caller that holds them already.
        """
        label = label or ""
        if not label.strip():
            return {"project": "", "slug": None, "project_folder": None, "members": [],
                    "construct": None, "designs": pooled_counts([]), "reference": None,
                    "suggestions": [], "review": None, "can_build": False,
                    "why_not": "Unfiled is not a project, so there is nothing to pool: "
                               "give these campaigns a project first."}
        slug = project_slug(label)
        folder = self.project_folder(slug)
        if members is None:
            members = self._members(label)
        construct, why_not = self._agreed(label, members)
        usable = [m for m in members if m["buildable"]]
        counts = pooled_counts(m["folder"] for m in usable)
        if why_not is None:
            why_not = self._unwritable(folder)
        if construct:
            self._backfill_check(folder, construct)
        given = ((read_record(folder) or {}).get("inputs") or {})
        return {
            "project": label, "slug": slug, "project_folder": str(folder),
            "members": [{k: m[k] for k in ("name", "folder", "job_id", "state", "designs",
                                           "buildable", "why_not")} for m in members],
            "construct": construct,
            "designs": counts,
            "reference": {"path": given.get("reference"), "chain": given.get("reference_chain"),
                          "partner_chains": given.get("partner_chains") or ("auto" if given.get("reference") else None),
                          "assembly": given.get("assembly"),
                          "uniprot": given.get("uniprot"),
                          "none": self._was_none(given, construct)},
            "suggestions": (self._suggestions(Path(usable[0]["folder"]), construct,
                                              [Path(m["folder"]) for m in usable[1:]])
                            if construct and construct["exists"] else []),
            "review": self._review(folder, counts, members),
            "can_build": why_not is None,
            "why_not": why_not,
        }

    def project_form(self, slug: str) -> dict:
        return self.describe_project(self._label_for(slug))

    def projects(self) -> list[dict]:
        """GET /api/review/projects: the projects that could be reviewed as one, and where each stands.

        A project with fewer than two live campaigns is not listed: there is nothing to pool,
        and that campaign's own review is the review. One that is listed but cannot be built
        (members disagree about the construct, or too few have finished) says why in
        `why_not`, so the page can show it greyed rather than leave a person wondering.
        One read of the job table serves every project.
        """
        grouped: dict[str, list[dict]] = {}
        for entry in self.store.campaigns_by_folder(archived="live"):
            if entry["project"].strip():
                grouped.setdefault(entry["project"], []).append(entry)
        found = []
        for label in sorted(grouped, key=str.lower):
            if len(grouped[label]) < 2:
                continue
            members = self._members(label, grouped[label])
            construct, why_not = self._agreed(label, members)
            usable = [m for m in members if m["buildable"]]
            counts = pooled_counts(m["folder"] for m in usable)
            slug = project_slug(label)
            folder = self.project_folder(slug)
            if why_not is None:
                why_not = self._unwritable(folder)
            found.append({"project": label, "slug": slug, "members": len(members),
                          "buildable": len(usable), "designs": counts,
                          "can_build": why_not is None, "why_not": why_not,
                          "review": self._review(folder, counts, members)})
        return found

    def project_start(self, slug: str, form: dict) -> dict:
        """POST /api/review/project/{slug}: build one review across a project's campaigns.

        The members are those the project has at this moment and that can be reviewed, and
        they are recorded in review.json as `{id, folder}` so that the build, the staleness
        check and the log all name the same runs. Everything about the reference is asked
        and checked exactly as for one campaign.
        """
        label = self._label_for(slug)
        folder = self.project_folder(slug)
        members = self._members(label)
        construct, why_not = self._agreed(label, members)
        if why_not is None:
            why_not = self._unwritable(folder)
        if why_not:
            raise ReviewError(why_not)
        usable = [m for m in members if m["buildable"]]
        asked = self._asked_reference(construct, form)
        pooled = [{"id": rid, "folder": m["folder"]}
                  for rid, m in zip(run_ids(m["folder"] for m in usable), usable)]
        # The newest member's notification topic: the campaign the person most recently launched
        # is the one they are most likely still listening for.
        newest = max(usable, key=lambda m: m["job_id"])
        topic = (self.store.get(newest["job_id"]) or {}).get("notify_topic", "")
        return self._enqueue(folder, construct, pooled_counts(m["folder"] for m in usable), asked,
                             topic=topic, name=f"{label} (project review)",
                             owner=str(form.get("owner") or "").strip()[:64], project=label[:64],
                             members=pooled, label=label)

    def project_remove(self, slug: str) -> dict:
        """DELETE /api/review/project/{slug}: discard a project's review.

        Needs no campaign and no label: a project whose campaigns are all gone, which is
        exactly the review someone will want to discard, has neither. It asks only that
        the project name something, so that a typo is a 404 and not a quiet "ok".
        """
        folder = self.project_folder(slug)
        if not home(folder).exists():
            self._label_for(slug)
        with self._lock:
            self._discard(folder)
        return {"ok": True}

    def project_log(self, slug: str, tail: int = 400) -> dict:
        return self._log(self.project_folder(slug), tail)

    # ---------------------------------------------------------------- DELETE

    def _discard(self, folder: Path) -> None:
        """Stop any build for this campaign and remove everything it wrote.

        Quietly: replacing a review is not news worth a notification. The wait is
        what makes removal safe, since a build still writing into a folder being
        deleted leaves behind exactly the half-state this is meant to clear.
        """
        live = self.store.find("analysis", folder, live=True)
        if live:
            self.store.stop_and_wait(live["id"])
        root = home(folder)
        if root.is_symlink():
            root.unlink()
            return
        for attempt in (1, 2):
            try:
                if root.exists():
                    shutil.rmtree(root)
                return
            except OSError as problem:
                if attempt == 2:
                    raise ReviewError(f"Could not remove {root}: {problem}", 409)
                time.sleep(1)

    def remove(self, name: str) -> dict:
        folder = self.folder(name)
        with self._lock:
            self._discard(folder)
        return {"ok": True}

    # ---------------------------------------------------------------- log

    def log(self, name: str, tail: int = 400) -> dict:
        return self._log(self.folder(name), tail)

    def _log(self, folder: Path, tail: int) -> dict:
        review = self._review(folder)
        if review is None:
            raise ReviewError(f"No review has been started for {folder.name}.", 404)
        text = ""
        try:
            with open(log_file(folder), "rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 1_000_000))
                text = handle.read().decode(errors="ignore")
        except OSError:
            pass
        step, percent = (None, None)
        if review["state"] == "done":
            step, percent = "done", 100
        elif review["state"] == "running":
            step, percent = parse_progress(text)
        # Binderscope's warnings can name every backbone in a campaign on one line.
        shown = [line if len(line) <= 600 else line[:600] + " ..."
                 for line in text.splitlines()[-max(1, min(int(tail), 5000)):]]
        return {"text": "\n".join(shown), "state": review["state"], "step": step,
                "percent": percent, "error": review["error"]}

    # ---------------------------------------------------------------- serving

    def bundle(self, name: str) -> Path:
        """The folder of a finished review's static pages, or a refusal.

        A name beginning with "@" is a project's review, anything else a campaign's. The
        two are told apart by that character alone (see PROJECT_NAME), so a campaign
        cannot be served as a project nor the reverse.
        """
        if name.startswith("@"):
            return self._project_bundle(name)
        folder = self.folder(name)
        review = self._review(folder)
        if review is None:
            raise ReviewError(f"No review has been built for {folder.name} yet.", 404)
        if review["state"] != "done":
            raise ReviewError(
                f"The review of {folder.name} is not ready: it is {review['state']}.", 404)
        return home(folder) / "bundle"

    def _project_bundle(self, name: str) -> Path:
        """A project review's pages. Asked for every file of every page, so it reads only the record.

        `_review` would also work out whether the review is stale, which for a project means
        listing its campaigns and reading their tables; staleness is not wanted here and a
        page of the review fetches dozens of files.
        """
        folder = self.project_folder(name)
        record = read_record(folder)
        if record is None:
            raise ReviewError(f"No review has been built for {name} yet.", 404)
        state = record.get("state") or "failed"
        if state == "done" and not (home(folder) / "bundle" / "index.html").is_file():
            state = "failed"
        if state != "done":
            raise ReviewError(f"The review of {name} is not ready: it is {state}.", 404)
        return home(folder) / "bundle"
