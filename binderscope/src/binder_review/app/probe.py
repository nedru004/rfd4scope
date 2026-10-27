"""Quick checks of what was chosen in the import page: a run folder, a structure file."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _friendly_reader(name: str, version: Any) -> str:
    base = {"bindcraft1": "BindCraft 1", "bindcraft2": "BindCraft 2", "rfd4": "RFdiffusion 4"}.get(name, name)
    v = str(version).split(" ")[0] if version and name == "bindcraft1" else ""
    return f"{base} {v.lstrip('v')}".strip() if v else base


def inspect_run(path: str) -> dict[str, Any]:
    """What a run folder holds, in a few numbers; reads the CSVs and the settings file, never opens the structures."""
    from ..cli import default_run_id
    from ..readers import detect_reader

    folder = Path(path).expanduser()
    if not folder.is_dir():
        return {"ok": False, "error": f"{folder} is not a folder."}
    reader = detect_reader(folder)
    if reader is None:
        kids = []                                            # the folder may hold several runs: offer them instead of an error
        try:
            for c in sorted(folder.iterdir()):
                if c.is_dir() and not c.name.startswith(".") and detect_reader(c) is not None:
                    kids.append(str(c.resolve()))
                if len(kids) >= 30:
                    break
        except OSError:
            kids = []
        return {"ok": False, "children": kids,
                "error": "This does not look like a known run folder. Choose the folder that holds the stats CSVs "
                         "and the Accepted folder (BindCraft 1), 1_Trajectories / 2_Refolded / 3_Ranked (BindCraft 2), "
                         "or inference_metrics/per_example_metrics.csv (RFdiffusion 4)."}
    rid = default_run_id(folder.resolve())
    try:
        run = reader.read_run(folder, rid, settings_path=None, construct_path=None, check_structures=False, hash_structures=False)
    except Exception as ex:                                  # a reader error is a message for the person, not a crash
        return {"ok": False, "error": f"Binderscope could not read this run folder: {ex}"}
    st = run.settings
    out: dict[str, Any] = {
        "ok": True, "id": rid, "path": str(folder.resolve()), "reader": _friendly_reader(reader.__name__.rsplit(".", 1)[-1], run.tool_version),
        "experimental": bool(run.experimental), "accepted": len(run.accepted()), "rejected": len(run.rejected()),
        "families": len(run.accepted_backbones()), "hotspots": [h.lstrip("?") for h in run.hotspot_labels()],
        "binder_length": [st.length_min, st.length_max] if st.length_min else None,
        "warnings": list(run.warnings)[:3], "settings_found": bool(st.settings_path),
    }
    out["target_candidates"] = target_candidates(folder, st.starting_pdb_name)
    return out


def target_candidates(folder: Path, recorded_name: str | None, limit: int = 6) -> list[dict[str, Any]]:
    """PDB files next to the run folder that may be the structure given to BindCraft; the one named in the settings file comes first."""
    seen: list[dict[str, Any]] = []
    for base in (folder, folder.parent):
        try:
            files = sorted(p for p in base.iterdir() if p.is_file() and p.suffix.lower() in (".pdb", ".ent"))
        except OSError:
            continue
        for p in files[:40]:
            seen.append({"path": str(p), "name": p.name, "match": bool(recorded_name and p.name == recorded_name)})
    seen.sort(key=lambda x: not x["match"])
    return seen[:limit]


def _kmers(s: str, k: int = 6) -> set[str]:
    return {s[i:i + k] for i in range(max(0, len(s) - k + 1))}


def _ca_by_number(path: str, chain: str | None) -> dict[int, tuple[str, list[float]]]:
    """number -> (one-letter code, CA xyz) of one protein chain (the first one without a chain id)."""
    import gemmi

    st = gemmi.read_structure(str(Path(path).expanduser()))
    st.setup_entities()
    model = st[0]
    ch = None
    for c in model:
        if len(c.get_polymer()) and (chain is None or c.name == chain):
            ch = c
            break
    out: dict[int, tuple[str, list[float]]] = {}
    if ch is None:
        return out
    for r in ch.get_polymer():
        a = r.find_atom("CA", "*")
        if a is None or r.seqid.num in out:
            continue
        info = gemmi.find_tabulated_residue(r.name)
        out[r.seqid.num] = ((info.one_letter_code.upper() if info and info.one_letter_code else "X"), [a.pos.x, a.pos.y, a.pos.z])
    return out


def fit_preview(construct: str, reference: str, chain: str) -> dict[str, Any]:
    """How well the structure given to BindCraft fits a chain of the full-length reference: the same numbering offset vote and CA fits the build uses.
    Shown on the import page before building, so a wrong chain, another target or a very different conformation is seen at once."""
    import numpy as np

    from .. import structure as S
    from .. import target_frame as TF

    try:
        cons, ref = _ca_by_number(construct, None), _ca_by_number(reference, chain)
        if not cons or not ref:
            return {"ok": False, "error": "No protein chain was found."}
        off, votes = TF._vote_offset([(n, v[0]) for n, v in cons.items()], {n: v[0] for n, v in ref.items()})
    except TF.FrameError as ex:
        msg = str(ex)
        if "ambiguous" in msg or "no residue in common" in msg:
            msg = "Binderscope cannot match the numbering of your target structure to this chain: the two sequences share too little to line up. Check the chain, or that this is the same protein."
        return {"ok": False, "error": msg}
    except Exception as ex:
        return {"ok": False, "error": f"The fit could not be checked: {ex}"}
    common = [n for n in sorted(cons) if (n + off) in ref]
    if len(common) < 10:
        return {"ok": True, "chain": chain, "offset": off, "n": len(common), "n_construct": len(cons), "identity": 0.0, "rmsd": None, "core_rmsd": None, "core_n": 0}
    P = np.array([cons[n][1] for n in common])
    Q = np.array([ref[n + off][1] for n in common])
    R, t, rms = S.kabsch(P, Q)
    Rc, tc, keep = TF.trimmed_fit(P, Q, 2.0, 5)
    core = float(np.sqrt(((P[keep] @ Rc.T + tc - Q[keep]) ** 2).sum(1).mean()))
    same = sum(1 for n in common if cons[n][0] == ref[n + off][0])
    return {"ok": True, "chain": chain, "offset": off, "n": len(common), "n_construct": len(cons), "identity": round(same / len(common), 3),
            "rmsd": round(float(rms), 2), "core_rmsd": round(core, 2), "core_n": int(keep.sum())}


def check_structure(path: str, against: str | None = None, chain: str | None = None) -> dict[str, Any]:
    """The chains of a PDB or mmCIF file; with ``against`` (the construct) the chain that best matches it is suggested and the fit onto it is previewed."""
    import gemmi

    p = Path(path).expanduser()
    if not p.is_file():
        return {"ok": False, "error": f"{p} is not a file."}
    try:
        st = gemmi.read_structure(str(p))
        st.setup_entities()
        model = st[0]
    except Exception as ex:
        return {"ok": False, "error": f"Binderscope cannot read this structure file: {ex}"}
    chains = []
    for ch in model:
        poly = ch.get_polymer()
        if len(poly) == 0:
            continue
        seq = gemmi.one_letter_code([r.name for r in poly])
        chains.append({"id": ch.name, "residues": len(poly), "seq": seq})
    if not chains:
        return {"ok": False, "error": "No protein chain was found in this file."}
    out: dict[str, Any] = {"ok": True, "name": p.name, "format": "mmCIF" if p.suffix.lower() in (".cif", ".mmcif") else "PDB",
                           "chains": [{"id": c["id"], "residues": c["residues"]} for c in chains], "suggest_chain": None, "partners": []}
    if against:
        ref = check_structure(against)
        if ref.get("ok"):
            cseq = max((c["seq"] for c in _chains_with_seq(against)), key=len, default="")
            km = _kmers(cseq)
            best, score = None, 0.0
            for c in chains:
                s = len(km & _kmers(c["seq"])) / max(1, len(km))
                if s > score:
                    best, score = c, s
            if best and score > 0.5:
                out["suggest_chain"] = best["id"]
                out["partners"] = [c["id"] for c in chains if c["id"] != best["id"] and c["seq"] == best["seq"]]
        use = chain or out["suggest_chain"] or (chains[0]["id"] if chains else None)
        if ref.get("ok") and use:
            out["fit"] = fit_preview(against, path, use)
    return out


def _chains_with_seq(path: str) -> list[dict[str, Any]]:
    import gemmi

    st = gemmi.read_structure(str(Path(path).expanduser()))
    st.setup_entities()
    res = []
    for ch in st[0]:
        poly = ch.get_polymer()
        if len(poly):
            res.append({"id": ch.name, "seq": gemmi.one_letter_code([r.name for r in poly])})
    return res
