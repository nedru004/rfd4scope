"""Runs BindCraft wrote somewhere else, brought in to be reviewed.

Plenty of people run BindCraft themselves and want nothing from the campaign
starter; what they want is the review. This is how such a run reaches the
Review tab.

**The run is not copied, and nothing in it is written to.** It is linked into
the results root under a campaign name, and the real path is written down in
``<state>/imports.json``. A 150 MB run therefore costs a symlink, the person
keeps one copy of their data, and the review itself lands in the results root's
``_reviews/`` beside every other, so ``/review/<name>/`` works unchanged.

Two things follow from the link, and both are handled here rather than being
discovered later:

* the file endpoints resolve a path before deciding whether to serve it, so a
  linked run's structures resolve outside the results roots and would be
  refused. ``paths()`` is what the app adds to the places it reads from.
* BindCraft does not copy the target structure into the campaign; it records
  where it was. A run carried from another machine names a path that is not
  here, and without the target there is nothing to review against, so an import
  may be given one. It is copied into the uploads folder (it is one small file)
  and remembered against the run.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from .campaign import CAMPAIGN_NAME
from .tables import design_counts, is_campaign_folder

REGISTRY = "imports.json"
#: What may be copied in as a target structure. The same suffixes the upload
#: endpoint takes, minus the sequence formats: a construct must be a structure.
STRUCTURE_SUFFIXES = {".pdb", ".ent", ".cif", ".mmcif"}


class BadImport(ValueError):
    """Something about the folder, or the name, that the person has to fix."""


def shaped(typed: str) -> str:
    """A campaign name made from a folder's name, by the rule campaigns are named by.

    The same shaping the design form does, so a run folder called "Spike run 2"
    or ".hidden" becomes a name the review pages will serve rather than one they
    404 on.
    """
    name = re.sub(r"[^\w.-]+", "_", typed or "").lstrip("._-")[:128]
    return name if CAMPAIGN_NAME.fullmatch(name or "") else ""


def why_not(folder: Path) -> str | None:
    """Why this folder cannot be imported, or None when it can.

    Said in the words the person needs, because this is the only place that
    knows what they typed: a path that does not exist, a folder that is not a
    run, and a run with nothing finished are three different fixes.
    """
    folder = Path(folder)
    if not folder.is_absolute():
        return "Give the whole path to the run folder, starting at /."
    if not folder.exists():
        return f"There is nothing at {folder}."
    if not folder.is_dir():
        return f"{folder.name} is a file. The run folder is the one that holds 2_Refolded and 3_Ranked."
    if not is_campaign_folder(folder):
        return (f"{folder.name} does not look like a BindCraft run: it has no campaign_metadata.json. "
                "The run folder is the one holding 1_Trajectories, 2_Refolded and 3_Ranked.")
    counts = design_counts(folder)
    if not (counts["passed"] or counts["ranked"]):
        return (f"{folder.name} has no finished designs yet "
                f"({counts['refolded']} refolded, none passed), so there is nothing to review.")
    return None


class Imports:
    """The registry, and the links it stands behind.

    One lock around the file: two people importing at once must not lose one of
    the two rows, and the name check and the link must not race.
    """

    def __init__(self, state_dir: Path, root: Path, uploads: Path):
        self.file = Path(state_dir) / REGISTRY
        self.root = Path(root)
        self.uploads = Path(uploads)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ reading

    def rows(self) -> list[dict]:
        """Every imported run, newest first. A row whose link has gone is dropped."""
        try:
            data = json.loads(self.file.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        out = []
        for row in data:
            if not isinstance(row, dict) or not row.get("name") or not row.get("path"):
                continue
            link = self.root / str(row["name"])
            row = dict(row, linked=link.is_symlink() or link.is_dir(), here=Path(str(row["path"])).is_dir())
            out.append(row)
        return sorted(out, key=lambda r: -(r.get("added") or 0))

    def paths(self) -> list[Path]:
        """The real folders, for the file endpoints to count as places they read from."""
        return [Path(str(r["path"])) for r in self.rows() if r.get("here")]

    def construct_for(self, folder: Path) -> str | None:
        """The target structure given at import for this run, if one was.

        Keyed on the real path, because the caller holds the campaign folder as
        it was reached (the link), and the link's name can be anything.
        """
        try:
            real = Path(folder).resolve()
        except OSError:
            return None
        for row in self.rows():
            if row.get("construct") and Path(str(row["path"])).resolve(strict=False) == real:
                return str(row["construct"])
        return None

    # ------------------------------------------------------------------ writing

    def add(self, path: str, name: str = "", construct: str = "") -> dict:
        """Link a run into the results root under a campaign name. Returns its row."""
        folder = Path(str(path).strip()).expanduser()
        problem = why_not(folder)
        if problem:
            raise BadImport(problem)
        folder = folder.resolve()
        if self._already_served(folder):
            raise BadImport("This run is already in the results folder, so it needs no import. "
                               "It is on the Review tab under its own name.")
        wanted = shaped(name or folder.name)
        if not wanted:
            raise BadImport("That name cannot be used. A campaign name starts with a letter or "
                               "a digit, then letters, digits, dots or dashes.")
        kept = self._copy_construct(construct) if construct else ""
        with self._lock:
            if (self.root / wanted).exists() or any(r["name"] == wanted for r in self.rows()):
                raise BadImport(f"There is already a campaign called {wanted} here. Give this run another name.")
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                (self.root / wanted).symlink_to(folder, target_is_directory=True)
            except OSError as problem:
                raise BadImport(f"The run could not be linked into the results folder: {problem}")
            row = {"name": wanted, "path": str(folder), "construct": kept, "added": int(time.time())}
            self._write([r for r in self._raw() if r.get("name") != wanted] + [row])
        return dict(row, linked=True, here=True)

    def remove(self, name: str) -> dict:
        """Forget an imported run: the link goes, the run itself is not touched.

        The review built from it is left where it is, in the results root's
        `_reviews/`, because it is a thing the person made and this is not a
        delete button. The Storage tab is where reviews are removed.
        """
        with self._lock:
            rows = [r for r in self._raw() if r.get("name") == str(name)]
            if not rows:
                raise BadImport(f"No run called {name} was imported here.")
            link = self.root / str(name)
            if link.is_symlink():
                link.unlink()
            elif link.exists():
                raise BadImport(f"{name} in the results folder is not a link to an imported run, "
                                   "so it is left alone.")
            self._write([r for r in self._raw() if r.get("name") != str(name)])
        return {"name": str(name), "removed": True}

    # ------------------------------------------------------------------ inside

    def _raw(self) -> list[dict]:
        try:
            data = json.loads(self.file.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    def _write(self, rows: list[dict]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_name(self.file.name + ".tmp")
        tmp.write_text(json.dumps(rows, indent=1))
        tmp.replace(self.file)

    def _already_served(self, folder: Path) -> bool:
        """True when the run is already a child of the results root under its own name."""
        try:
            return folder.parent.resolve() == self.root.resolve()
        except OSError:
            return False

    def _copy_construct(self, given: str) -> str:
        """Keep the target structure where every other structure the service was given lives."""
        source = Path(str(given).strip()).expanduser()
        if not source.is_absolute():
            raise BadImport("Give the whole path to the target structure, starting at /.")
        if source.suffix.lower() not in STRUCTURE_SUFFIXES:
            raise BadImport("The target should be a structure file: PDB or mmCIF.")
        if not source.is_file():
            raise BadImport(f"There is no structure at {source}.")
        self.uploads.mkdir(parents=True, exist_ok=True)
        dest = self.uploads / source.name
        n = 2
        while dest.exists() and dest.stat().st_size != source.stat().st_size:
            dest = self.uploads / f"{source.stem}_{n}{source.suffix}"
            n += 1
        if not dest.exists():
            shutil.copy2(source, dest)
        return str(dest)
