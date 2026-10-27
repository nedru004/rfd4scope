"""RFdiffusion 4 (RFD4) reader.

Reads a finished RFD4 run folder whose AlphaFold 3 scores live in
``inference_metrics/per_example_metrics.csv`` (or ``per_example_metrics_reeval.csv``).
Structures are under ``inference_samples/<example_id>/``. Contig, hotspots and the
designed chain come from ``inputs/*.yaml`` and the CSV's ``chain_of_interest``.

Acceptance is decided by user cutoffs (defaults in ``config.RFD4_DEFAULT_CUTOFFS``),
applied per required target chain for ipTM and min iPAE. RFD4's own
``motif_scaffold.pass`` is recorded beside the verdict, never as the sole gate.

The reader is experimental: it was written against the measured output of the
runs under ``rfd4/out`` and carries no published RFD4 version stamp.
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import ReaderError
from ..config import (
    PROVENANCE_USER, RFD4_DEFAULT_CUTOFFS, FilterFailure, FilterRule, FilterSet,
    check_filters, parse_cutoff_arg,
)
from ..model import (
    BackboneRecord, DesignRecord, FunnelCounts, InputFile, RunData, RunSettings,
    TargetConstruct,
)
from .bindcraft1 import _gaps, _missing_ranges, _one_letter, _reference_construct, sha256_file

NAME = "rfd4"

METRICS_DIR = "inference_metrics"
SAMPLES_DIR = "inference_samples"
INPUTS_DIR = "inputs"

#: Prefer a re-evaluation CSV when both exist.
_METRICS_PREFERRED = (
    "per_example_metrics_reeval.csv",
    "per_example_metrics.csv",
)

_COI_RE = re.compile(r"^([A-Za-z0-9]+)_(\d+)$")
_CONTIG_FIXED_RE = re.compile(r"([A-Za-z])(\d+)(?:-(\d+))?")
_HOT_SEL_RE = re.compile(
    r"sel\(\s*['\"]([A-Za-z])/\*/\[([^\]]+)\]['\"]\s*\)"
)
_BSFINAL_RE = re.compile(r"^(?P<bb>.+)_bsfinal_(?P<k>\d+)$")
_TASK_REP_RE = re.compile(r"^(?P<task>[A-Za-z0-9_]+?)_(?P<rep>\d+)(?:_bsfinal_(?P<k>\d+))?$")

#: AF3 scores that map onto BindCraft stems / page metrics (CSV scale after conversion).
# Oracle column suffix (without namespace) -> (stored stem, scale factor applied to the raw value).
_ORACLE_MAP: dict[str, tuple[str, float]] = {
    "plddt_coi": ("pLDDT", 0.01),           # 0-100 -> 0-1
    "overall_ptm": ("pTM", 0.01),
    "iptm_coi": ("i_pTM", 0.01),
    "ipae_coi": ("i_pAE", 1.0 / 31.0),      # angstrom -> PAE/31
    "min_ipae_coi": ("min_ipae", 1.0),       # angstrom, page metric
    "overall_plddt": ("overall_plddt", 0.01),
    "overall_iptm": ("overall_iptm", 0.01),
    "overall_pae": ("overall_pae", 1.0),
    "ranking_score": ("ranking_score", 1.0),
    "fraction_disordered": ("fraction_disordered", 1.0),
    "has_clash": ("has_clash", 1.0),
    "ptm_coi": ("ptm_coi", 0.01),
    "pae_coi": ("pae_coi", 1.0),
}

_RMSD_MAP: dict[str, str] = {
    "refolded_backbone_rmsd.rmsd": "complex_rmsd",
    "refolded_motif_ca_rmsd.rmsd": "motif_ca_rmsd",
    "refolded_motif_allatom_rmsd.rmsd": "motif_allatom_rmsd",
}

# Pair columns look like oracle.iptm_A_1_B_1 / oracle.min_ipae_A_1_C_1 (chain_of_interest ids).
_PAIR_IPTM_RE = re.compile(
    r"^(?P<ns>[^.]+)\.iptm_(?P<a>[A-Za-z](?:_[0-9]+)?|[A-Za-z0-9]+)_(?P<b>[A-Za-z](?:_[0-9]+)?|[A-Za-z0-9]+)$"
)
_PAIR_MIN_RE = re.compile(
    r"^(?P<ns>[^.]+)\.min_ipae_(?P<a>[A-Za-z](?:_[0-9]+)?|[A-Za-z0-9]+)_(?P<b>[A-Za-z](?:_[0-9]+)?|[A-Za-z0-9]+)$"
)


def detect(path: str | Path) -> bool:
    """True when the folder holds an RFD4 per-example metrics CSV."""
    root = Path(path)
    if not root.is_dir():
        return False
    metrics = root / METRICS_DIR
    if not metrics.is_dir():
        return False
    return any(metrics.glob("per_example_metrics*.csv"))


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return None
        return float(x)
    s = str(x).strip()
    if not s or s.lower() in ("nan", "none", "null", ""):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _truthy(x: Any) -> bool:
    v = _f(x)
    if v is not None:
        return v != 0.0
    s = str(x or "").strip().lower()
    return s in ("1", "true", "yes", "pass", "passed")


def chain_letter(coi: str | None) -> Optional[str]:
    """``A_1`` -> ``A``; bare ``A`` stays ``A``."""
    if not coi:
        return None
    s = str(coi).strip()
    m = _COI_RE.match(s)
    if m:
        return m.group(1)
    if len(s) == 1 and s.isalpha():
        return s.upper()
    if s and s[0].isalpha():
        return s[0].upper()
    return None


def backbone_id_of(example_id: str) -> str:
    """``scaffold_1_0_bsfinal_1`` -> ``scaffold_1_0``; otherwise the example id itself."""
    m = _BSFINAL_RE.match(example_id)
    return m.group("bb") if m else example_id


def parse_example_id(example_id: str) -> dict[str, Any]:
    m = _TASK_REP_RE.match(example_id)
    if not m:
        return {"task": None, "replicate": None, "bsfinal": None, "backbone": example_id}
    return {
        "task": m.group("task"),
        "replicate": int(m.group("rep")),
        "bsfinal": int(m.group("k")) if m.group("k") is not None else None,
        "backbone": backbone_id_of(example_id),
    }


def find_metrics_csv(root: Path) -> Path:
    metrics = root / METRICS_DIR
    for name in _METRICS_PREFERRED:
        p = metrics / name
        if p.is_file():
            return p
    found = sorted(metrics.glob("per_example_metrics*.csv"))
    if not found:
        raise ReaderError(f"{root}: no per_example_metrics CSV under {METRICS_DIR}/")
    return found[0]


def read_metrics_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = [{k: (v if v is not None else "") for k, v in row.items()} for row in reader]
    return fields, rows


def discover_oracles(fields: Iterable[str]) -> list[str]:
    """Oracle namespaces present as ``<ns>.plddt_coi`` or ``<ns>.overall_plddt``, primary ``oracle`` first."""
    found: set[str] = set()
    for col in fields:
        if "." not in col:
            continue
        ns, _, rest = col.partition(".")
        if rest in _ORACLE_MAP or rest.startswith("iptm_") or rest.startswith("min_ipae_"):
            found.add(ns)
    ordered = sorted(found, key=lambda n: (0 if n == "oracle" else 1 if n == "oracle_ppi" else 2, n))
    return ordered


def local_sample_path(root: Path, example_id: str, suffix: str) -> Optional[Path]:
    """Resolve ``inference_samples/<id>/<id>_<suffix>`` next to the run, ignoring absolute CSV paths."""
    folder = root / SAMPLES_DIR / example_id
    if not folder.is_dir():
        return None
    for name in (f"{example_id}_{suffix}", f"{example_id}{suffix}"):
        p = folder / name
        if p.is_file():
            return p
    # Allow a slightly different stem if the folder holds exactly one match.
    matches = sorted(folder.glob(f"*_{suffix}")) + sorted(folder.glob(f"*{suffix}"))
    return matches[0] if len(matches) == 1 else (matches[0] if matches else None)


def _load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError as ex:
        raise ReaderError(f"reading {path} needs PyYAML ({ex})") from ex
    with path.open() as fh:
        return yaml.safe_load(fh)


def find_input_yaml(root: Path, settings_path: Optional[str | Path] = None) -> Optional[Path]:
    if settings_path:
        p = Path(settings_path)
        return p if p.is_file() else None
    inputs = root / INPUTS_DIR
    if not inputs.is_dir():
        return None
    yamls = sorted(p for p in inputs.iterdir() if p.suffix.lower() in (".yaml", ".yml"))
    return yamls[0] if yamls else None


def parse_contig_fixed(contig: str) -> dict[str, list[tuple[int, int]]]:
    """Fixed motif / target stretches from an RFD4 contig, keyed by chain letter.

    Linker ranges (``L1:3-7``) and chain breaks (``/0``) are skipped. ``A16-26`` and
    ``E83`` become motif segments on the designed chain; ``B1-465`` is the target span.
    """
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for part in str(contig or "").split(","):
        bit = part.strip()
        if not bit or bit.startswith("/") or bit.upper().startswith("L"):
            continue
        # Skip length-range linkers written as L1:3-7 already handled; also "L2:25-37".
        if re.match(r"^L\d+", bit, re.I):
            continue
        m = _CONTIG_FIXED_RE.match(bit)
        if not m:
            continue
        chain, a, b = m.group(1), int(m.group(2)), m.group(3)
        lo, hi = a, int(b) if b is not None else a
        out[chain].append((lo, hi))
    return dict(out)


def parse_hotspots_from_yaml(spec: Mapping[str, Any]) -> dict[str, list[int]]:
    """{chain: [residue numbers]} from every ``C_HOT`` select in the yaml."""
    out: dict[str, list[int]] = defaultdict(list)
    for _task, body in spec.items():
        if not isinstance(body, Mapping):
            continue
        cond = body.get("conditions") or {}
        if not isinstance(cond, Mapping):
            continue
        hot = cond.get("C_HOT") or cond.get("c_hot") or {}
        if not isinstance(hot, Mapping):
            continue
        for _flag, selects in hot.items():
            if not isinstance(selects, list):
                continue
            for item in selects:
                text = item.get("select") if isinstance(item, Mapping) else str(item)
                for m in _HOT_SEL_RE.finditer(str(text or "")):
                    chain = m.group(1)
                    for tok in m.group(2).split(","):
                        tok = tok.strip()
                        if not tok:
                            continue
                        if "-" in tok:
                            a, b = tok.split("-", 1)
                            try:
                                lo, hi = int(a), int(b)
                            except ValueError:
                                continue
                            out[chain].extend(range(lo, hi + 1))
                        else:
                            try:
                                out[chain].append(int(tok))
                            except ValueError:
                                continue
    return {c: sorted(set(ns)) for c, ns in out.items()}


def motif_segments_for_coi(contig_fixed: Mapping[str, list[tuple[int, int]]],
                           coi: str, target_chains: Iterable[str]) -> list[dict[str, Any]]:
    """Motif stretches on the designed chain (contig fixed residues that are not a target chain)."""
    targets = set(target_chains)
    segs = contig_fixed.get(coi) or []
    # If the designed chain also appears as a target donor in the contig (TPO: A is motif source),
    # every fixed A stretch is motif. If the designed chain is E and E83 is listed, that is motif.
    out = []
    for i, (lo, hi) in enumerate(segs, start=1):
        if coi in targets and len(contig_fixed) > 1:
            # Designed chain equals a target letter only when the motif was grafted from that chain
            # into a new chain of the same letter (common for TPO). Keep them as motif.
            pass
        out.append({"name": f"motif {i}", "chain": coi, "start": lo, "end": hi, "length": hi - lo + 1})
    return out


def scaffold_edits_string(segments: list[dict[str, Any]]) -> str:
    """Paratope-style edit string naming the motif (fixed) stretches on the designed chain."""
    # Invert the usual scaffold edit: these are the FIXED motif positions. Encode as chain+range
    # so paratope / annotations can show them; a single residue is A83 not A83-83.
    parts = []
    for seg in segments:
        c, a, b = seg["chain"], int(seg["start"]), int(seg["end"])
        parts.append(f"{c}{a}" if a == b else f"{c}{a}-{b}")
    return ",".join(parts)


def _oracle_metrics(row: Mapping[str, str], ns: str) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for suffix, (stem, factor) in _ORACLE_MAP.items():
        raw = _f(row.get(f"{ns}.{suffix}"))
        if raw is None:
            continue
        out[stem] = raw * factor
    return out


def _split_pair_ids(rest: str) -> Optional[tuple[str, str]]:
    """Split ``A_1_B_1`` / ``A_B`` / ``A_1_C`` into two chain-of-interest ids."""
    # Prefer the common ``X_N_Y_N`` shape, then ``X_Y``.
    m = re.match(r"^([A-Za-z]_\d+)_([A-Za-z]_\d+)$", rest)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^([A-Za-z])_([A-Za-z])$", rest)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^([A-Za-z]_\d+)_([A-Za-z])$", rest) or re.match(r"^([A-Za-z])_([A-Za-z]_\d+)$", rest)
    if m:
        return m.group(1), m.group(2)
    return None


def _pair_scores(row: Mapping[str, str], ns: str, coi: str
                 ) -> dict[str, dict[str, Optional[float]]]:
    """Per partner chain scores involving the designed chain only."""
    per: dict[str, dict[str, Optional[float]]] = defaultdict(dict)
    prefix_iptm = f"{ns}.iptm_"
    prefix_min = f"{ns}.min_ipae_"
    for col, val in row.items():
        if col.startswith(prefix_iptm):
            ids = _split_pair_ids(col[len(prefix_iptm):])
            if not ids:
                continue
            a, b = chain_letter(ids[0]), chain_letter(ids[1])
            v = _f(val)
            if a and b and coi in (a, b) and a != b and v is not None:
                partner = b if a == coi else a
                per[partner]["iptm"] = v * 0.01
                per[partner]["i_pTM"] = v * 0.01
            continue
        if col.startswith(prefix_min):
            ids = _split_pair_ids(col[len(prefix_min):])
            if not ids:
                continue
            a, b = chain_letter(ids[0]), chain_letter(ids[1])
            v = _f(val)
            if a and b and coi in (a, b) and a != b and v is not None:
                partner = b if a == coi else a
                per[partner]["min_ipae"] = v
    return dict(per)


def _rmsd_metrics(row: Mapping[str, str]) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for col, stem in _RMSD_MAP.items():
        v = _f(row.get(col))
        if v is not None:
            out[stem] = v
    return out


def _sequence_from_cif(path: Path, chain: str) -> str:
    try:
        from .. import structure as S
        cx = S.load_complex(str(path), target_chain=chain, binder_chain=None, strict=False)
        # With binder_chain=None the target table is that chain.
        return cx.target.sequence or ""
    except Exception:
        return ""


def _inspect_chains(path: Path) -> list[str]:
    try:
        import gemmi
        st = gemmi.read_structure(str(path))
        st.setup_entities()
        return [ch.name for ch in st[0] if any(True for _ in ch)]
    except Exception:
        return []


def default_chain_roles(target_chains: Iterable[str],
                        roles_opt: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    out = {c: "require" for c in target_chains}
    if roles_opt:
        for c, role in roles_opt.items():
            r = str(role).lower()
            if c in out and r in ("require", "ignore", "avoid"):
                out[c] = r
    return out


def build_filter_annotations(
    cutoffs: Optional[Iterable[Mapping[str, Any]]] = None,
    cutoff_args: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """Merge default RFD4 cutoffs with any ``--cutoff`` overrides."""
    by_key: dict[tuple[str, tuple[str, ...] | None], dict[str, Any]] = {}
    for f in RFD4_DEFAULT_CUTOFFS:
        chains = tuple(f["chains"]) if f.get("chains") else None
        by_key[(str(f["name"]), chains)] = dict(f)
    for f in (cutoffs or []):
        chains = tuple(f["chains"]) if f.get("chains") else None
        by_key[(str(f["name"]), chains)] = dict(f)
    for arg in (cutoff_args or []):
        f = parse_cutoff_arg(arg)
        chains = tuple(f["chains"]) if f.get("chains") else None
        by_key[(str(f["name"]), chains)] = f
    return list(by_key.values())


def _filters_to_rules(annotations: list[Mapping[str, Any]]) -> tuple[FilterRule, ...]:
    rules: list[FilterRule] = []
    for f in annotations:
        name = str(f["name"])
        higher = bool(f.get("higher", str(f.get("op", ">=")).strip() in (">=", ">")))
        thr = float(f["threshold"])
        per_chain = bool(f.get("per_chain"))
        chains = tuple(str(c) for c in f["chains"]) if f.get("chains") else None
        # Map aliases to stored stems for evaluation against DesignRecord.metrics / per_chain.
        from ..config import _user_storage
        stem, _factor, _why = _user_storage(name)
        metric = stem if stem and stem != name else None
        rules.append(FilterRule(
            name, thr, higher, provenance=PROVENANCE_USER,
            note="RFD4 import cutoff",
            metric=metric, mandatory=bool(f.get("mandatory", True)),
            source=str(f.get("source") or "user"),
            per_chain=per_chain, chains=chains,
        ))
    return tuple(rules)


def read_run(
    path: str | Path,
    run_id: str,
    label: Optional[str] = None,
    settings_path: Optional[str | Path] = None,
    construct_path: Optional[str | Path] = None,
    check_structures: bool = True,
    hash_structures: bool = True,
    oracle: Optional[str] = None,
    cutoffs: Optional[Iterable[Mapping[str, Any]]] = None,
    cutoff_args: Optional[Iterable[str]] = None,
    chain_roles: Optional[Mapping[str, str]] = None,
    **options: Any,
) -> RunData:
    """Turn one RFD4 run folder into a :class:`RunData`.

    ``oracle`` selects the primary AF3 namespace (default ``oracle``). ``cutoff_args`` are
    ``--cutoff`` strings; ``cutoffs`` are already-parsed annotation dicts. ``chain_roles``
    maps target chain letters to require|ignore|avoid.
    """
    root = Path(path).expanduser().resolve()
    if not detect(root):
        raise ReaderError(f"{root} is not an RFD4 run folder (no {METRICS_DIR}/per_example_metrics*.csv)")

    warnings: list[str] = []
    notes: list[str] = []
    files: list[InputFile] = []

    metrics_path = find_metrics_csv(root)
    fields, rows = read_metrics_table(metrics_path)
    files.append(InputFile("csv", str(metrics_path), sha256_file(metrics_path), metrics_path.stat().st_size))
    notes.append(f"Metrics from {metrics_path.name} ({len(rows)} designs).")

    oracles = discover_oracles(fields)
    primary = oracle or ("oracle" if "oracle" in oracles else (oracles[0] if oracles else "oracle"))
    if primary not in oracles and oracles:
        warnings.append(f"Requested oracle {primary!r} is not in the CSV; using {oracles[0]!r}.")
        primary = oracles[0]
    secondary = [n for n in oracles if n != primary]
    if secondary:
        notes.append(f"Secondary oracle namespace(s): {', '.join(secondary)} (kept on design.extras['oracles']).")

    yaml_path = find_input_yaml(root, settings_path)
    spec: dict[str, Any] = {}
    contig_by_task: dict[str, str] = {}
    hotspots_by_chain: dict[str, list[int]] = {}
    input_cif: Optional[Path] = None
    if yaml_path and yaml_path.is_file():
        try:
            raw = _load_yaml(yaml_path) or {}
            if isinstance(raw, Mapping):
                spec = dict(raw)
            files.append(InputFile("settings", str(yaml_path), sha256_file(yaml_path), yaml_path.stat().st_size))
        except Exception as ex:
            warnings.append(f"Could not read {yaml_path.name}: {ex}")
        for key, body in spec.items():
            if key == "global_args" and isinstance(body, Mapping):
                inp = body.get("input")
                if inp:
                    cand = root / INPUTS_DIR / Path(str(inp)).name
                    if cand.is_file():
                        input_cif = cand
            if isinstance(body, Mapping) and "contig" in body:
                contig_by_task[str(key)] = str(body["contig"])
        hotspots_by_chain = parse_hotspots_from_yaml(spec)

    if construct_path:
        cp = Path(construct_path)
        if cp.is_file():
            input_cif = cp
    if input_cif and input_cif.is_file():
        files.append(InputFile("construct", str(input_cif), sha256_file(input_cif), input_cif.stat().st_size))

    # Determine designed chain and target chains from the first scored row + yaml.
    sample_coi = None
    for row in rows:
        sample_coi = chain_letter(row.get("chain_of_interest"))
        if sample_coi:
            break
    if not sample_coi:
        warnings.append("No chain_of_interest in the metrics CSV; assuming designed chain A.")
        sample_coi = "A"

    # Target chains: from a sample structure's polymer chains minus the designed chain.
    target_chain_list: list[str] = []
    for row in rows:
        eid = row.get("example_id") or ""
        refold = local_sample_path(root, eid, "af3_refolded.cif")
        if refold:
            chains = _inspect_chains(refold)
            target_chain_list = [c for c in chains if c != sample_coi]
            break
    if not target_chain_list:
        # Fall back to contig fixed chains that are not the designed chain / not linkers.
        for contig in contig_by_task.values():
            fixed = parse_contig_fixed(contig)
            target_chain_list = [c for c in fixed if c != sample_coi]
            if target_chain_list:
                break
    if not target_chain_list:
        target_chain_list = ["B"] if sample_coi == "A" else ["A"]
        warnings.append(f"Could not discover target chains; assuming {target_chain_list}.")

    roles = default_chain_roles(target_chain_list, chain_roles)
    primary_target = target_chain_list[0]
    # Prefer a hotspot chain as the numbered construct when present.
    for c in target_chain_list:
        if hotspots_by_chain.get(c):
            primary_target = c
            break
    # Reorder so primary_target is first (target frame numbering).
    target_chain_list = [primary_target] + [c for c in target_chain_list if c != primary_target]

    # Motif segments from the first task contig that mentions the designed chain letter as fixed.
    motif_segments: list[dict[str, Any]] = []
    scaffold_edits = ""
    for contig in contig_by_task.values():
        fixed = parse_contig_fixed(contig)
        # Motif = fixed residues on the designed chain letter in the contig (TPO: A16-26 etc.).
        # The designed chain in the *output* may still be A.
        segs = []
        for chain, spans in fixed.items():
            if chain in target_chain_list and chain != sample_coi:
                continue
            # Fixed stretches whose chain letter is not a pure target-only chain, or equals coi.
            if chain == sample_coi or chain not in target_chain_list:
                for lo, hi in spans:
                    segs.append({"name": f"motif {len(segs) + 1}", "chain": chain, "start": lo, "end": hi,
                                 "length": hi - lo + 1})
        if segs:
            motif_segments = segs
            scaffold_edits = scaffold_edits_string(segs)
            break

    filter_ann = build_filter_annotations(cutoffs=cutoffs, cutoff_args=cutoff_args)
    filter_rules = _filters_to_rules(filter_ann)

    # Hotspots for RunSettings: primary target chain numbers (contract is number-only).
    hotspot_nums = list(hotspots_by_chain.get(primary_target) or [])
    hotspot_string = ",".join(str(n) for n in hotspot_nums) if hotspot_nums else None
    other_hot_labels = []
    for c, nums in hotspots_by_chain.items():
        if c == primary_target:
            continue
        other_hot_labels.extend(f"{c}{n}" for n in nums)
    if other_hot_labels:
        warnings.append(
            f"Hotspots on non-primary target chains are kept as labels only ({', '.join(other_hot_labels[:12])}"
            f"{'…' if len(other_hot_labels) > 12 else ''}); the epitope list uses chain {primary_target}."
        )

    designs: list[DesignRecord] = []
    backbones: dict[str, BackboneRecord] = {}
    bb_order = 0
    n_pre_oracle = 0
    n_scored = 0
    n_tool_pass = 0
    lengths: list[int] = []

    for row in rows:
        eid = (row.get("example_id") or "").strip()
        if not eid:
            continue
        info = parse_example_id(eid)
        bb_id = info["backbone"]
        coi = chain_letter(row.get("chain_of_interest")) or sample_coi
        seq = (row.get("chain_of_interest_sequence") or "").strip()

        refold = local_sample_path(root, eid, "af3_refolded.cif")
        generated = local_sample_path(root, eid, "generated.cif")
        conf_json = local_sample_path(root, eid, "af3_confidences.json")

        if not seq and refold:
            seq = _sequence_from_cif(refold, coi)
        if not seq and generated:
            seq = _sequence_from_cif(generated, coi)
        length = len(seq) if seq else int(_f(row.get("amino_acid_composition.n_standard_residues")) or 0)
        if length:
            lengths.append(length)

        metrics = _oracle_metrics(row, primary)
        metrics.update(_rmsd_metrics(row))
        # Composition diagnostics stay under their own names.
        for col in ("amino_acid_composition.ala_fraction", "amino_acid_composition.val_fraction",
                    "amino_acid_composition.gly_fraction"):
            v = _f(row.get(col))
            if v is not None:
                metrics[col.split(".", 1)[1]] = v

        per_chain = _pair_scores(row, primary, coi)
        # Ensure every target chain has a slot even when pairwise columns are absent.
        for c in target_chain_list:
            per_chain.setdefault(c, {})

        extras: dict[str, Any] = {
            "n_binder_chains": 1,
            "n_target_chains": len(target_chain_list),
            "binder_chains": [coi],
            "target_chains": list(target_chain_list),
            "chain_roles": dict(roles),
            "chain_of_interest": row.get("chain_of_interest") or coi,
            "task": row.get("task") or info["task"],
            "modality": "motif scaffold",
            "oracle": primary,
            "pre_oracle_pass": _truthy(row.get("pre_oracle_pass")),
            "tool_pass": {},
            "oracles": {},
        }
        if generated:
            extras["designed_structure_path"] = str(generated)
        if conf_json:
            extras["confidence_json_path"] = str(conf_json)
        if motif_segments:
            extras["motif_segments"] = motif_segments
        if other_hot_labels:
            extras["hotspots_other_chains"] = other_hot_labels

        for gate_col in ("motif_scaffold.pass", "ppi.pass"):
            if gate_col in row:
                extras["tool_pass"][gate_col] = _truthy(row.get(gate_col))
                if gate_col == "motif_scaffold.pass" and extras["tool_pass"][gate_col]:
                    n_tool_pass += 1

        for ns in oracles:
            om = _oracle_metrics(row, ns)
            op = _pair_scores(row, ns, coi)
            extras["oracles"][ns] = {"metrics": om, "per_chain": op}
            if ns != primary:
                alt = None
                if "ppi" in ns:
                    alt = local_sample_path(root, eid, "af3_ppi_refolded.cif")
                if alt:
                    extras["oracles"][ns]["refolded_structure_path"] = str(alt)

        if _truthy(row.get("pre_oracle_pass")):
            n_pre_oracle += 1
        if metrics.get("pLDDT") is not None or metrics.get("i_pTM") is not None:
            n_scored += 1

        seed = int(_f(row.get("seed")) or 0)
        structure_path = str(refold) if refold else None
        if not refold and generated:
            notes.append(f"{eid}: AF3 refold missing; design is metric-only (generated CIF not analysed).")

        # Acceptance from user cutoffs (recomputed), not from RFD4's pass boolean.
        failures = check_filters(
            metrics, {}, None, filter_rules, per_chain=per_chain, chain_roles=roles,
        )
        status = "accepted" if not failures else "rejected"

        if bb_id not in backbones:
            backbones[bb_id] = BackboneRecord(
                id=bb_id, run_id=run_id, binder_name=bb_id, length=length or 0, seed=seed,
                order=bb_order, start_sequence=seq or None,
                extras={"task": extras["task"]},
            )
            bb_order += 1
        bb = backbones[bb_id]
        bb.design_ids.append(eid)
        if status == "accepted":
            bb.accepted_ids.append(eid)

        mpnn_index = info["bsfinal"]
        designs.append(DesignRecord(
            id=eid, run_id=run_id, backbone_id=bb_id, status=status,
            binder_name=eid, length=length or 0, seed=seed, mpnn_index=mpnn_index,
            sequence=seq or "", target_hotspot=hotspot_string,
            structure_path=structure_path, metrics=metrics, per_chain=per_chain,
            failed_filters=failures, extras=extras,
        ))

    # Rank accepted by ranking_score (higher better), then by id.
    accepted = [d for d in designs if d.status == "accepted"]
    accepted.sort(key=lambda d: (-(d.metrics.get("ranking_score") or -1.0), d.id))
    for i, d in enumerate(accepted, start=1):
        d.rank = i

    if not accepted and designs:
        warnings.append(
            "No design passed the import cutoffs; every design is rejected. "
            "RFD4's own motif_scaffold.pass is recorded on each design under extras['tool_pass']."
        )

    # Target construct from the primary target chain of checked structures.
    target = TargetConstruct(run_id=run_id, chain_id=primary_target, binder_chain_id=sample_coi)
    if check_structures:
        from .. import structure as S
        residue_sets: list[tuple[tuple[int, ...], str]] = []
        for d in designs:
            if not d.structure_path:
                continue
            try:
                cx = S.load_complex(
                    d.structure_path,
                    target_chains=target_chain_list,
                    binder_chain=[sample_coi],
                    strict=False,
                )
                # Numbers for the primary target chain only (contract is number-keyed).
                primary_atoms = cx.target.select(cx.target.chain == primary_target) if len(cx.target) else cx.target
                if not len(primary_atoms) and len(cx.target):
                    primary_atoms = cx.target
                nums = tuple(int(n) for n in primary_atoms.residues.number)
                seq = primary_atoms.sequence
                residue_sets.append((nums, seq))
                target.n_structures_checked += 1
            except Exception as ex:
                warnings.append(f"{d.id}: could not open structure ({ex})")
            if hash_structures and d.structure_path:
                p = Path(d.structure_path)
                if p.is_file():
                    files.append(InputFile("accepted_structure" if d.status == "accepted" else "rejected_structure",
                                           str(p), sha256_file(p), p.stat().st_size))
        if residue_sets:
            counts = Counter(residue_sets)
            (nums, seq), _n = counts.most_common(1)[0]
            target.numbers = list(nums)
            target.sequence = seq
            target.first = nums[0] if nums else None
            target.last = nums[-1] if nums else None
            target.consistent = len(counts) == 1
            if not target.consistent:
                warnings.append(
                    f"Target residue sets differ across {len(counts)} variants; "
                    f"using the majority ({len(nums)} residues on chain {primary_target})."
                )
            if target.numbers:
                target.deleted_ranges = _missing_ranges(target.numbers)
        if input_cif and input_cif.is_file() and target.numbers:
            try:
                ref = _reference_construct(input_cif)
                # Best-effort: only compare when sequences align on the primary chain letter.
            except Exception:
                ref = None

    for n in hotspot_nums:
        if target.numbers and n in target.numbers and target.sequence:
            try:
                idx = target.numbers.index(n)
                target.hotspot_residues[n] = target.sequence[idx]
            except (ValueError, IndexError):
                target.hotspot_residues[n] = "?"
        else:
            target.hotspot_residues[n] = target.hotspot_residues.get(n, "?")

    settings = RunSettings(
        run_id=run_id,
        label=label or root.name,
        binder_name=root.name,
        protocol="rfd4",
        chains=",".join(target_chain_list),
        hotspots=hotspot_nums,
        hotspot_string=hotspot_string,
        hotspot_source="yaml" if hotspot_nums else "unknown",
        length_min=min(lengths) if lengths else None,
        length_max=max(lengths) if lengths else None,
        length_source="csv" if lengths else "unknown",
        settings_path=str(yaml_path) if yaml_path else None,
        settings_raw={"contig_by_task": contig_by_task, "hotspots_by_chain": hotspots_by_chain,
                      "oracle": primary, "chain_roles": roles},
        notes=[f"Designed chain {sample_coi}; target chains {', '.join(target_chain_list)}."],
    )

    funnel = FunnelCounts(
        trajectories_attempted=len(rows),
        trajectory_rows=n_pre_oracle,
        mpnn_rows=n_scored,
        accepted_designs=len(accepted),
        accepted_backbones=sum(1 for b in backbones.values() if b.accepted_ids),
        final_designs_target=None,
        status={
            "trajectories_attempted": "observed",
            "trajectory_rows": "observed",
            "mpnn_rows": "observed",
            "accepted_designs": "observed",
            "accepted_backbones": "observed",
        },
        notes={
            "trajectories_attempted": "RFD4 rows in per_example_metrics (designs generated)",
            "trajectory_rows": "rows with pre_oracle_pass",
            "mpnn_rows": "rows with AF3 oracle scores",
            "accepted_designs": "designs that pass the import cutoffs",
        },
    )

    # Cross-check aggregated metrics when present.
    agg = metrics_path.parent / metrics_path.name.replace("per_example_metrics", "aggregated_metrics")
    if agg.is_file():
        files.append(InputFile("csv", str(agg), sha256_file(agg), agg.stat().st_size))
        try:
            with agg.open(newline="") as fh:
                for arow in csv.DictReader(fh):
                    if arow.get("metric") == "motif_scaffold.pass" and arow.get("reduction") == "mean":
                        notes.append(f"aggregated motif_scaffold.pass mean={arow.get('value')} (n={arow.get('n')}).")
        except Exception:
            pass

    annotations: dict[str, Any] = {
        "filters": filter_ann,
        "modality": "motif scaffold",
        "modalities": {"motif scaffold": len(designs)},
        "oracle": primary,
        "oracles": oracles,
        "chain_roles": roles,
        "target_chains": target_chain_list,
        "binder_chain": sample_coi,
        "tool_pass_count": n_tool_pass,
        "capabilities": {
            "min_ipae": any(d.metrics.get("min_ipae") is not None for d in designs),
            "complex_rmsd": any(d.metrics.get("complex_rmsd") is not None for d in designs),
            "per_chain": True,
            "motif": bool(motif_segments),
        },
        "funnel_labels": {
            "trajectories_attempted": "Designs generated",
            "trajectory_rows": "Passed pre-oracle checks",
            "mpnn_rows": "Scored by AlphaFold 3",
            "accepted_designs": "Passed import cutoffs",
            "accepted_backbones": "Accepted backbones (families)",
        },
    }
    if scaffold_edits:
        annotations["scaffold"] = {
            "kind": "motif",
            "scaffold": str(input_cif) if input_cif else None,
            "scaffold_edits": scaffold_edits,
            "motif_segments": motif_segments,
            "source": "rfd4.contig",
            "note": "Fixed motif stretches from the RFD4 contig (not designed scaffold edits).",
        }

    warnings.insert(
        0,
        "UNTESTED campaign: the RFD4 reader was written against measured per_example_metrics "
        "output and carries no published RFD4 version stamp. Compare a few scores with the CSV "
        "before trusting a result.",
    )

    return RunData(
        run_id=run_id,
        label=label or root.name,
        reader=NAME,
        source_dir=str(root),
        settings=settings,
        target=target,
        designs=designs,
        backbones=sorted(backbones.values(), key=lambda b: b.order),
        funnel=funnel,
        files=files,
        warnings=warnings,
        notes=notes,
        annotations=annotations,
        tool_version="RFD4",
        experimental=True,
    )
