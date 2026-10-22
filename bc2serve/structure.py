"""Reading, judging and trimming target structures.

What a designer needs before choosing an epitope is not just a picture: which
residues are actually on the surface, whether the ones they picked form a single
patch, what the numbering really is, and how much GPU the complex will cost. All
of that is read here and handed to the page.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import biotite.structure as struc
from biotite.structure.io import load_structure

# Residues that dominate interface energetics: the aromatics and the charged pair
# set that hot-spot surveys find enriched at protein-protein interfaces. Identity
# alone is a weak signal, which is why nothing is suggested unless it is also
# solvent exposed -- a buried tryptophan is a folding nucleus, not an epitope.
FAVOURABLE = set("WYFRKDE")

# Tien et al. (2013) theoretical maximum solvent accessibility, for turning an
# absolute SASA into something comparable between residue types.
MAX_ASA = {
    "A": 129.0, "R": 274.0, "N": 195.0, "D": 193.0, "C": 167.0, "E": 223.0,
    "Q": 225.0, "G": 104.0, "H": 224.0, "I": 197.0, "L": 201.0, "K": 236.0,
    "M": 224.0, "F": 240.0, "P": 159.0, "S": 155.0, "T": 172.0, "W": 285.0,
    "Y": 263.0, "V": 174.0,
}

THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
    "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
    "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
    "TYR": "Y", "VAL": "V", "MSE": "M", "SEC": "U", "PYL": "O",
}

# 0.25 is the textbook line between buried and exposed, and is what the sequence
# track dims below. It is NOT the line for "a binder cannot reach this": the
# canonical PD-L1 hotspots Tyr56 and Met115 sit at 0.23 and 0.24, and a partly
# buried aromatic is often the best hotspot there is, because binding buries so
# much more of it. Only something essentially interior is worth warning about.
EXPOSED = 0.25
UNREACHABLE = 0.08
# A suggestion should be properly out in the open, not merely not-buried.
WELL_EXPOSED = 0.35

# BindCraft pads a complex to a multiple of this before compiling.
LENGTH_BUCKET = 32


class BadStructure(ValueError):
    pass


def _load(path: Path):
    try:
        structure = load_structure(str(path), model=1)
    except Exception as problem:
        raise BadStructure(f"Could not read {Path(path).name}: {problem}")
    return structure[struc.filter_amino_acids(structure)]


@lru_cache(maxsize=32)
def _analyse(path: str, mtime: float, chains: str) -> dict:
    """Per-residue facts about a target. Cached on the file's modification time."""
    structure = _load(Path(path))
    wanted = [c.strip() for c in chains.split(",") if c.strip()]
    if wanted:
        structure = structure[np.isin(structure.chain_id, wanted)]
    if structure.array_length() == 0:
        raise BadStructure("No protein residues in that selection.")

    # SASA of the selection as given: exposure depends on what else is present, and
    # the designer is choosing against the chains they picked, not the lone chain.
    try:
        atom_sasa = struc.sasa(structure, vdw_radii="Single")
    except Exception:
        atom_sasa = np.full(structure.array_length(), np.nan)
    residue_sasa = struc.apply_residue_wise(structure, np.nan_to_num(atom_sasa), np.sum)

    starts = struc.get_residue_starts(structure)
    residues = []
    for index, start in enumerate(starts):
        name = str(structure.res_name[start])
        letter = THREE_TO_ONE.get(name.upper(), "X")
        absolute = float(residue_sasa[index]) if index < len(residue_sasa) else 0.0
        relative = absolute / MAX_ASA.get(letter, 200.0)
        residues.append({
            "chain": str(structure.chain_id[start]),
            "number": int(structure.res_id[start]),
            "name": name,
            "letter": letter,
            "rel_sasa": round(min(relative, 1.5), 3),
            "exposed": relative >= EXPOSED,
            "suggested": letter in FAVOURABLE and relative >= WELL_EXPOSED,
        })

    by_chain: dict[str, list[dict]] = {}
    for residue in residues:
        by_chain.setdefault(residue["chain"], []).append(residue)

    chain_info = []
    for chain, members in sorted(by_chain.items()):
        numbers = [m["number"] for m in members]
        breaks = sum(1 for a, b in zip(numbers, numbers[1:]) if b - a > 1)
        chain_info.append({
            "chain": chain,
            "count": len(members),
            "first": min(numbers),
            "last": max(numbers),
            "gaps": breaks,
        })

    return {"residues": residues, "chains": chain_info, "total": len(residues)}


def analyse(path: str | Path, chains: str = "") -> dict:
    target = Path(path)
    if not target.is_file():
        raise BadStructure("No such structure.")
    return _analyse(str(target), target.stat().st_mtime, chains or "")


# ---------------------------------------------------------------- epitope checks

def _coordinates(path: Path, chains: str, numbers: set[int]) -> np.ndarray:
    structure = _load(path)
    wanted = [c.strip() for c in chains.split(",") if c.strip()]
    if wanted:
        structure = structure[np.isin(structure.chain_id, wanted)]
    alpha = structure[structure.atom_name == "CA"]
    keep = np.isin(alpha.res_id, list(numbers))
    return alpha.coord[keep]


def _split_key(key) -> tuple[str, int]:
    """"A67" -> ("A", 67); "67" -> ("", 67). A bare number means any chain."""
    text = str(key).strip()
    letters = "".join(c for c in text if c.isalpha())
    digits = "".join(c for c in text if c.isdigit() or c == "-")
    return letters, int(digits or 0)


def judge_epitope(path: str | Path, chains: str, hotspots: list) -> list[dict]:
    """What is worth saying out loud about a chosen set of hotspots.

    These are warnings, not refusals: a designer may well know something the
    geometry does not, and BindCraft will take whatever it is given.
    """
    notes: list[dict] = []
    if not hotspots:
        return notes

    facts = analyse(path, chains)
    # Keyed by chain as well as number: two chains of one assembly each number
    # from their own start, so a bare number can mean two different residues.
    index = {(r["chain"], r["number"]): r for r in facts["residues"]}
    by_number: dict[int, list] = {}
    for residue in facts["residues"]:
        by_number.setdefault(residue["number"], []).append(residue)

    def find(key):
        chain, number = _split_key(key)
        if chain:
            return index.get((chain, number))
        found = by_number.get(number) or []
        return found[0] if len(found) == 1 else (found[0] if found else None)

    missing = [n for n in hotspots if find(n) is None]
    if missing:
        first, last = facts["chains"][0]["first"], facts["chains"][-1]["last"]
        notes.append({
            "level": "bad",
            "text": f"{_list(missing)} {'is' if len(missing) == 1 else 'are'} not in this "
                    f"structure, which is numbered {first}-{last}. Check the numbering your "
                    "residues came from; BindCraft will ignore them.",
        })

    present = [n for n in hotspots if find(n) is not None]
    interior = [n for n in present if find(n)["rel_sasa"] < UNREACHABLE]
    if interior:
        notes.append({
            "level": "warn",
            "text": f"{_list(interior)} {'is' if len(interior) == 1 else 'are'} in the "
                    f"interior (under {UNREACHABLE:.0%} exposed), so a binder cannot reach "
                    f"{'it' if len(interior) == 1 else 'them'}.",
        })

    if len(present) > 12:
        notes.append({
            "level": "info",
            "text": f"{len(present)} hotspots is a lot. BindCraft aims at them as one region, "
                    "so a broad selection steers it less than a focused patch of about "
                    "4-8 residues on the face you care about.",
        })

    if len(present) >= 2:
        located = [(str(n), find(n)) for n in present]
        numbers, coordinates = _ordered_coordinates(
            Path(path), [(r["chain"], r["number"]) for _, r in located],
            [label for label, _ in located])
        if len(coordinates) >= 2:
            distances = np.linalg.norm(
                coordinates[:, None, :] - coordinates[None, :, :], axis=-1)
            spread = float(distances.max())
            patches = _patches(numbers, distances)

            if len(patches) > 1:
                described = "; ".join(_list(sorted(p, key=_sort_key)) for p in patches)
                apart = _nearest_between(distances, numbers, patches)
                notes.append({
                    "level": "warn",
                    "text": f"These are {len(patches)} separate patches ({described}), "
                            f"{apart:.0f} A apart at the closest. One binder will usually "
                            "settle on whichever it finds first, so check "
                            "Hotspot_Contact_Fraction in the results to see what it actually "
                            "touched, or run a campaign per patch.",
                })
            elif spread > 28 and len(present) <= 12:
                # Only meaningful for a focused selection: single-linkage chains a
                # broad one into "one patch" that is really half the surface, and
                # the count note above already covers that case.
                notes.append({
                    "level": "warn",
                    "text": f"One patch, but {spread:.0f} A across, which is a lot of surface "
                            "for a single binder to cover. Expect contacts at one end.",
                })
            elif spread < 6 and len(present) > 2:
                notes.append({
                    "level": "info",
                    "text": f"A tight patch, {spread:.0f} A across. That focuses the binder "
                            "nicely, though it gives it little to grip.",
                })

    reachable = len(present) - len(interior)
    if 0 < reachable < 3:
        notes.append({
            "level": "info",
            "text": "Only a couple of hotspots: BindCraft treats these as a hint, so a small "
                    "patch of 3-6 exposed residues usually steers it better than one or two.",
        })
    return notes


def _ordered_coordinates(path: Path, places: list[tuple[str, int]], labels: list[str]):
    """CA coordinates alongside the labels they belong to, in one order."""
    structure = _load(path)
    alpha = structure[structure.atom_name == "CA"]
    found, points = [], []
    for (chain, number), label in zip(places, labels):
        hit = np.where((alpha.res_id == number) & (alpha.chain_id == chain))[0]
        if len(hit):
            found.append(label)
            points.append(alpha.coord[hit[0]])
    return found, (np.array(points) if points else np.empty((0, 3)))


def _patches(numbers: list, distances: np.ndarray, cutoff: float = 14.0) -> list[list]:
    """Single-linkage grouping: residues within reach of each other are one site.

    14 A between alpha carbons is about as far apart as two residues can be and
    still be gripped by the same face of a binder.
    """
    unassigned = set(range(len(numbers)))
    groups = []
    while unassigned:
        seed = unassigned.pop()
        group = {seed}
        frontier = [seed]
        while frontier:
            here = frontier.pop()
            for other in list(unassigned):
                if distances[here, other] <= cutoff:
                    unassigned.discard(other)
                    group.add(other)
                    frontier.append(other)
        groups.append(sorted((numbers[i] for i in group), key=_sort_key))
    return sorted(groups, key=lambda g: _sort_key(g[0]))


def _sort_key(label):
    chain, number = _split_key(label)
    return (chain, number)


def _nearest_between(distances: np.ndarray, numbers: list,
                     patches: list[list]) -> float:
    """The shortest gap between any two patches, which is what a binder must span."""
    place = {number: index for index, number in enumerate(numbers)}
    shortest = float("inf")
    for first in range(len(patches)):
        for second in range(first + 1, len(patches)):
            for a in patches[first]:
                for b in patches[second]:
                    shortest = min(shortest, float(distances[place[a], place[b]]))
    return shortest


def _list(items) -> str:
    text = ", ".join(str(n) for n in items[:6])
    return text + ("..." if len(items) > 6 else "")


# ---------------------------------------------------------------- what it costs

def estimate(target_residues: int, binder_low: int, binder_high: int,
             card_memory_gb: float) -> dict:
    """GPU memory for the largest complex a campaign will build.

    BindCraft budgets 2.0 x (3.4 GB + 38 kB x N^2) per worker for a padded complex
    of N residues, keeping 4 GB of the card back. Cost grows with the square of the
    complex, which is why trimming a target is the cheapest speed-up there is.
    """
    largest = target_residues + max(binder_low, binder_high)
    padded = int(math.ceil(largest / LENGTH_BUCKET) * LENGTH_BUCKET)
    per_worker = 2.0 * (3.4 + 38e-6 * padded ** 2)
    usable = max(0.0, card_memory_gb - 4)
    workers = int(usable // per_worker) if per_worker else 0
    return {
        "target_residues": target_residues,
        "largest_complex": largest,
        "padded": padded,
        "per_worker_gb": round(per_worker, 1),
        "usable_gb": round(usable, 1),
        "workers_per_card": max(0, min(7, workers)),
        "fits": workers >= 1,
    }


# ---------------------------------------------------------------- trimming

def trim(path: str | Path, chains: str, keep: list[int], destination: Path) -> dict:
    """Write a structure holding only the chosen chains and residues.

    Residue numbering is carried over untouched. That is the whole point: hotspots
    are residue numbers, and renumbering a trimmed target would silently move the
    epitope somewhere else.
    """
    source = Path(path)
    structure = _load(source)
    wanted = [c.strip() for c in chains.split(",") if c.strip()]
    if wanted:
        structure = structure[np.isin(structure.chain_id, wanted)]
    if keep:
        structure = structure[np.isin(structure.res_id, keep)]
    if structure.array_length() == 0:
        raise BadStructure("That selection keeps no residues.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    # PDB, because every downstream tool reads it and the numbering survives.
    from biotite.structure.io.pdb import PDBFile
    output = PDBFile()
    output.set_structure(structure)
    output.write(str(destination))

    alpha = structure[structure.atom_name == "CA"]
    numbers = sorted({int(n) for n in alpha.res_id})
    return {
        "path": str(destination),
        "residues": len(numbers),
        "first": numbers[0] if numbers else 0,
        "last": numbers[-1] if numbers else 0,
        "chains": sorted({str(c) for c in alpha.chain_id}),
    }
