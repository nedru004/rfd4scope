"""Shared constants: BindCraft 1 default filter thresholds and the folder names the readers look for.

DEFAULT_FILTERS was reconstructed from two real BindCraft 1 runs (see docs/data_inventory.md, section 6),
because the filter JSON that BindCraft used is not copied into its output folders (the CSVs carry
only its basename, "default_filters"). Each rule says how well it is supported:

  counter-verified  Applying the rule to mpnn_design_stats.csv reproduces BindCraft's own failure
                    counter in failure_csv.csv exactly, in every run it was checked on, and every accepted
                    design passes. The counters pin the per-model thresholds and the SC Average threshold
                    to one value. They only bound the other Average-column thresholds, because the
                    per-model rule always fires first; those values are BindCraft's documented defaults
                    and consistent with the counters.
  bounded           The rule acts before a sequence gets a row in mpnn_design_stats.csv (the counters
                    are far larger than the number of rows), so the rows only bound the threshold:
                    every row satisfies it and the extreme row sits on it or inside it.
  memory            Value from the BindCraft documentation as recorded in the research note; the
                    data neither confirm nor contradict it.

A fourth kind exists for runs whose tool records its own thresholds (BindCraft 2 writes them to
campaign_metadata.json):

  campaign          The threshold is the one the campaign itself used, read from its metadata. That is
                    better evidence than any of the three kinds above, which reconstruct BindCraft 1's
                    defaults. `filters_for_run` builds such a set; a run that records nothing is judged
                    by DEFAULT_FILTERS.

A fifth kind is chosen by the person at import, not by the design tool:

  user              Thresholds the person set (or accepted as defaults) when importing an RFD4 run.
                    RFD4 writes only a pass boolean, never the thresholds behind it, so Binderscope
                    cannot reconstruct them. `filters_for_run` builds a FilterSet of source "user"
                    and re-evaluates every design from the metrics; the tool's own pass is recorded
                    beside the verdict, never merged into it.

Thresholds are inclusive: a value equal to the threshold passes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

# Columns BindCraft writes per metric: Average_<stem>, then 1_<stem> .. 5_<stem>.
MODEL_NUMBERS = (1, 2, 3, 4, 5)

# Folder names, matched case-insensitively (BindCraft 1.5 writes Accepted/ and Rejected/, other runs
# have lower-case folders).
ACCEPTED_DIR = "accepted"
REJECTED_DIR = "rejected"
RANKED_DIR = "Ranked"
CSV_FINAL = "final_design_stats.csv"
CSV_MPNN = "mpnn_design_stats.csv"
CSV_TRAJECTORY = "trajectory_stats.csv"
CSV_FAILURE = "failure_csv.csv"

#: Provenance of a rule whose threshold the run recorded itself (see the module docstring).
PROVENANCE_CAMPAIGN = "campaign"
#: Provenance of a rule the person chose at import (RFD4 and similar; see the module docstring).
PROVENANCE_USER = "user"

# Floating point slack for comparisons against thresholds; CSV values are rounded to 2 decimals.
EPS = 1e-9


@dataclass(frozen=True)
class FilterRule:
    """One BindCraft filter.

    stem            metric stem as in the CSV columns without the Average_/<k>_ prefix. Interface
                    amino-acid counts use "InterfaceAAs_<letter>" and are read from the
                    InterfaceAAs dict column.
    threshold       value for the Average_<stem> column (CSV scale).
    higher_is_better  True: the value must be >= threshold; False: it must be <= threshold.
    per_model       threshold applied to the 1_<stem> .. 5_<stem> columns that have a value, or None
                    when BindCraft does not filter single models on this metric.
    provenance      "counter-verified", "bounded" or "memory" (reconstructed BindCraft 1 defaults),
                    "campaign" (the run recorded this threshold itself), or "user" (chosen at import);
                    see the module docstring.
    note            what the data say about the rule.

    The fields below are used by rules of provenance "campaign" or "user"; the reconstructed defaults leave
    them at their defaults and are unchanged.

    metric          the stem the values are stored under, when it is not `stem`. For a campaign rule `stem`
                    is the campaign's own filter name (what failed_filters, the failure counters and every
                    FilterFailure call it), for example Interface_Residues, whose values are stored as
                    n_InterfaceResidues.
    mandatory       BindCraft's flag: a mandatory filter fails a design whose metric could not be measured.
    judged_per_model  BindCraft judges the filter on each validation model while the CSV carries the rounded
                    mean (PER_MODEL_JUDGED), so a design can fail it with a CSV value that passes.
    source          where a campaign threshold was read ("campaign_metadata" or "settings.min_plddt_final"),
                    or "user" / "rfd4.defaults" for an import cutoff.
    per_chain       when True, the rule is applied to each target chain in DesignRecord.per_chain (scope is
                    the chain letter), not to Average alone. Used by RFD4 so a design must clear the bar on
                    every chain it must engage.
    chains          when set with per_chain, only these chain letters are checked; otherwise every chain in
                    the per_chain dict (minus chains whose role is "ignore") is checked.
    """

    stem: str
    threshold: float
    higher_is_better: bool
    per_model: Optional[float] = None
    provenance: str = "memory"
    note: str = ""
    metric: Optional[str] = None
    mandatory: Optional[bool] = None
    judged_per_model: bool = False
    source: str = ""
    per_chain: bool = False
    chains: Optional[tuple[str, ...]] = None

    @property
    def value_stem(self) -> str:
        """The stem the values of this rule are stored under."""
        return self.metric or self.stem

    @property
    def from_campaign(self) -> bool:
        return self.provenance == PROVENANCE_CAMPAIGN

    @property
    def from_user(self) -> bool:
        return self.provenance == PROVENANCE_USER

    @property
    def op(self) -> str:
        return ">=" if self.higher_is_better else "<="


@dataclass(frozen=True)
class FilterFailure:
    """One failed check of a design against one rule.

    scope   "Average", a model number as a string ("1", "2"), or a chain letter for a per-chain rule.
    value   the design's value in the CSV scale.
    gap     how far the value misses the threshold, always positive and in the metric's own unit
            (threshold - value for ">=" rules, value - threshold for "<=" rules).
    """

    stem: str
    scope: str
    value: float
    threshold: float
    higher_is_better: bool
    gap: float


DEFAULT_FILTERS: tuple[FilterRule, ...] = (
    FilterRule(
        "pLDDT", 0.80, True, per_model=0.80, provenance="bounded",
        note="The failure counter far exceeds the number of rows; the lowest per-model value in any row sits on the threshold.",
    ),
    FilterRule(
        "pTM", 0.55, True, per_model=0.55, provenance="memory",
        note="BindCraft's documented default: no row is close enough to the threshold for the data to confirm the value.",
    ),
    FilterRule(
        "i_pTM", 0.50, True, per_model=0.50, provenance="bounded",
        note="The failure counter far exceeds the number of rows; the lowest per-model value in any row lies inside the threshold.",
    ),
    FilterRule(
        "i_pAE", 0.35, False, per_model=0.35, provenance="bounded",
        note="The failure counter far exceeds the number of rows; the highest per-model value in any row sits on the threshold. "
             "Values are PAE divided by 31.",
    ),
    FilterRule(
        "ShapeComplementarity", 0.60, True, per_model=0.55, provenance="counter-verified",
        note="Average >= 0.60 and each model >= 0.55 reproduce BindCraft's failure counters; "
             "with 0.60 on the models some accepted designs would fail.",
    ),
    FilterRule(
        "n_InterfaceHbonds", 3, True, per_model=3, provenance="counter-verified",
        note="Reproduces BindCraft's failure counters.",
    ),
    FilterRule(
        "n_InterfaceUnsatHbonds", 4, False, per_model=4, provenance="counter-verified",
        note="Reproduces BindCraft's failure counters. A raw count, so it penalises large interfaces.",
    ),
    FilterRule(
        "Surface_Hydrophobicity", 0.35, False, per_model=0.35, provenance="counter-verified",
        note="Reproduces BindCraft's failure counters.",
    ),
    FilterRule(
        "Binder_pLDDT", 0.80, True, per_model=0.80, provenance="counter-verified",
        note="Reproduces BindCraft's failure counters. Binder-alone prediction, not the complex pLDDT.",
    ),
    FilterRule(
        "Binder_RMSD", 3.5, False, per_model=3.5, provenance="counter-verified",
        note="Reproduces BindCraft's failure counters (angstrom, binder alone versus binder in complex).",
    ),
    FilterRule(
        "InterfaceAAs_K", 3, False, per_model=None, provenance="counter-verified",
        note="At most 3 lysines at the interface, on the average column only (reproduces BindCraft's failure counters).",
    ),
    FilterRule(
        "InterfaceAAs_M", 3, False, per_model=None, provenance="counter-verified",
        note="At most 3 methionines at the interface, on the average column only (reproduces BindCraft's failure counters).",
    ),
)

# Metrics that look like filters in BindCraft's default file but are NOT enforced in the runs this was reconstructed from:
# Hotspot_RMSD, Unrelaxed_Clashes, Relaxed_Clashes, Binder_Energy_Score, dG, dSASA, n_InterfaceResidues, Interface_*.
NOT_ENFORCED = (
    "Hotspot_RMSD", "Target_RMSD", "Unrelaxed_Clashes", "Relaxed_Clashes", "Binder_Energy_Score",
    "dG", "dSASA", "dG/dSASA", "n_InterfaceResidues", "Interface_SASA_%", "Interface_Hydrophobicity",
    "PackStat", "pAE", "i_pLDDT", "ss_pLDDT", "MPNN_score", "MPNN_seq_recovery",
)

# Names of the filter-failure counters in failure_csv.csv that belong to the trajectory stage.
TRAJECTORY_COUNTERS = (
    "Trajectory_logits_pLDDT", "Trajectory_softmax_pLDDT", "Trajectory_one-hot_pLDDT",
    "Trajectory_final_pLDDT", "Trajectory_Contacts", "Trajectory_Clashes", "Trajectory_WrongHotspot",
)


def rules_by_stem(rules: Iterable[FilterRule] = DEFAULT_FILTERS) -> dict[str, FilterRule]:
    return {r.stem: r for r in rules}


def _violates(value: float, threshold: float, higher_is_better: bool) -> bool:
    if higher_is_better:
        return value < threshold - EPS
    return value > threshold + EPS


def check_filters(
    average: Mapping[str, Optional[float]],
    models: Mapping[int, Mapping[str, Optional[float]]],
    interface_aas: Optional[Mapping[str, Mapping[str, float]]] = None,
    rules: Iterable[FilterRule] = DEFAULT_FILTERS,
    per_chain: Optional[Mapping[str, Mapping[str, Optional[float]]]] = None,
    chain_roles: Optional[Mapping[str, str]] = None,
) -> list[FilterFailure]:
    """Apply BindCraft-style filters to one design.

    average         metric stem -> Average_<stem> value (CSV scale).
    models          model number -> {metric stem -> value}; models without values may be absent.
    interface_aas   {"avg": {"K": 1.0, ...}, "1": {...}, "2": {...}} from the InterfaceAAs columns;
                    only the "avg" entry is used (the amino-acid rules are average-only).
    per_chain       chain letter -> {metric stem -> value}; used when a rule has per_chain=True.
    chain_roles     chain letter -> "require" | "ignore" | "avoid". ignore chains are not gated;
                    avoid chains invert the rule (engaging them fails a "higher is better" cut-off).
    Metrics that are missing are skipped, never failed, so a run that lacks a metric simply is not
    checked on it. Returns one FilterFailure per failed (rule, scope) pair, in rule order.
    """
    failures: list[FilterFailure] = []
    roles = {str(k): str(v).lower() for k, v in (chain_roles or {}).items()}
    for rule in rules:
        stem = rule.stem
        if rule.per_chain:
            chains = list(rule.chains) if rule.chains is not None else list((per_chain or {}).keys())
            for chain in chains:
                role = roles.get(chain, "require")
                if role == "ignore":
                    continue
                vals = (per_chain or {}).get(chain) or {}
                v = vals.get(rule.value_stem)
                if v is None:
                    continue
                higher = rule.higher_is_better if role != "avoid" else (not rule.higher_is_better)
                if _violates(v, rule.threshold, higher):
                    failures.append(_failure_raw(rule.stem, chain, float(v), rule.threshold, higher))
            continue
        if stem.startswith("InterfaceAAs_"):
            letter = stem.split("_", 1)[1]
            avg_aas = (interface_aas or {}).get("avg") or {}
            val = avg_aas.get(letter)
        else:
            val = average.get(rule.value_stem)
        if val is not None and _violates(val, rule.threshold, rule.higher_is_better):
            failures.append(_failure(rule, "Average", val, rule.threshold))
        if rule.per_model is not None:
            for k in sorted(models):
                v = models[k].get(rule.value_stem)
                if v is not None and _violates(v, rule.per_model, rule.higher_is_better):
                    failures.append(_failure(rule, str(k), v, rule.per_model))
    return failures


def _failure(rule: FilterRule, scope: str, value: float, threshold: float) -> FilterFailure:
    gap = (threshold - value) if rule.higher_is_better else (value - threshold)
    return FilterFailure(rule.stem, scope, float(value), float(threshold), rule.higher_is_better, float(gap))


def _failure_raw(stem: str, scope: str, value: float, threshold: float, higher_is_better: bool) -> FilterFailure:
    gap = (threshold - value) if higher_is_better else (value - threshold)
    return FilterFailure(stem, scope, float(value), float(threshold), higher_is_better, float(gap))


def failed_stems(failures: Iterable[FilterFailure]) -> list[str]:
    """Unique metric stems among the failures, in first-seen order."""
    seen: dict[str, None] = {}
    for f in failures:
        seen.setdefault(f.stem, None)
    return list(seen)


# ---------------------------------------------------------------------------------------------
# The filter set of a run
# ---------------------------------------------------------------------------------------------
#: Confidence filters that BindCraft 2 judges on each validation model in turn, while its CSV carries the mean of the
#: models that ran, rounded to two decimals (docs/bindcraft2_reader.md, "The verdict is BindCraft's, not the CSV's").
PER_MODEL_JUDGED = ("pLDDT", "pTM", "i_pTM", "Unbound_Binder_pLDDT", "Target_pLDDT")


@dataclass(frozen=True)
class UnevaluatedFilter:
    """A threshold the campaign recorded for a metric Binderscope cannot evaluate.

    It is kept, not dropped: the run was judged by it, the pages just cannot re-check it. A design BindCraft rejected on it
    still carries BindCraft's own verdict (failed_filters); only a re-evaluation against the metrics is impossible.
    `mandatory` tells how much that matters (a mandatory threshold is binding; an optional one is the campaign's own
    way of saying a metric must not be missing, and several are vacuous, such as ">= 0").
    """

    name: str
    op: str
    threshold: float
    mandatory: Optional[bool]
    source: str
    reason: str


@dataclass(frozen=True)
class FilterSet:
    """The filters one run is judged by.

    rules        what can be evaluated against the metrics, in the order the run lists them
    source       "default": DEFAULT_FILTERS, BindCraft 1's reconstructed thresholds (a run that records none);
                 "campaign": the thresholds the run recorded itself, which are judged first (a design BindCraft
                 failed has failed) and re-evaluated only for what BindCraft did not report;
                 "user": thresholds the person chose at import (RFD4); re-evaluated from the metrics;
                 "explicit": rules a caller passed in (recomputed from the metrics, nothing else)
    unevaluated  campaign thresholds on metrics Binderscope does not carry
    unlimited    campaign filters whose threshold is infinite (no limit); never rules
    """

    rules: tuple[FilterRule, ...]
    source: str = "default"
    label: str = ""
    unevaluated: tuple[UnevaluatedFilter, ...] = ()
    unlimited: tuple[str, ...] = ()

    @property
    def from_campaign(self) -> bool:
        return self.source == "campaign"

    @property
    def from_user(self) -> bool:
        return self.source == "user"


DEFAULT_FILTER_SET = FilterSet(DEFAULT_FILTERS, "default", "BindCraft 1 default filters (reconstructed)")


def as_filter_set(rules: "Iterable[FilterRule] | FilterSet") -> FilterSet:
    """A FilterSet from rules a caller passed in (a FilterSet is returned as it is)."""
    if isinstance(rules, FilterSet):
        return rules
    rules = tuple(rules)
    return DEFAULT_FILTER_SET if rules == DEFAULT_FILTERS else FilterSet(rules, "explicit", "rules given by the caller")


def _recorded_filters(run: Any) -> tuple[list[Mapping[str, Any]], list[str], bool]:
    """(recorded filters, unlimited names, whether the run recorded its own thresholds at all).

    BindCraft 2's reader leaves them in RunData.annotations["filters"] (a list of {name, op, threshold, higher,
    mandatory, source}) and ["filters_unlimited"]. BindCraft 1 records nothing, so it has neither."""
    ann = getattr(run, "annotations", None) or {}
    raw = ann.get("filters")
    recorded = [f for f in raw if isinstance(f, Mapping) and "name" in f and "threshold" in f] if isinstance(raw, list) else []
    unlimited = [str(n) for n in (ann.get("filters_unlimited") or [])]
    return recorded, unlimited, bool(recorded or unlimited)


#: Stem aliases a reader may store under its own name or a BindCraft stem (RFD4 cutoffs use both).
_USER_METRIC_ALIASES: dict[str, str] = {
    "plddt_coi": "pLDDT", "plddt": "pLDDT",
    "iptm_coi": "i_pTM", "iptm": "i_pTM", "i_ptm": "i_pTM",
    "min_ipae_coi": "min_ipae", "min_ipae": "min_ipae",
    "ipae_coi": "i_pAE", "i_pae": "i_pAE",
    "refolded_backbone_rmsd": "complex_rmsd", "complex_rmsd": "complex_rmsd",
    "refolded_motif_ca_rmsd": "motif_ca_rmsd", "motif_ca_rmsd": "motif_ca_rmsd",
    "refolded_motif_allatom_rmsd": "motif_allatom_rmsd", "motif_allatom_rmsd": "motif_allatom_rmsd",
    "has_clash": "has_clash", "ranking_score": "ranking_score",
}


def _storage(name: str) -> tuple[Optional[str], Optional[float], str]:
    """(stem the campaign's metric is stored under, scale factor, why there is none) for a BindCraft 2 filter name.

    The mapping is the BindCraft 2 reader's own (METRIC_COLUMNS), so a stem is mapped here exactly when the reader
    stored the column. Imported when needed: the reader imports this module."""
    try:
        from .readers import bindcraft2 as bc2
    except Exception:                                    # the reader is optional; without it nothing can be mapped
        return None, None, "the BindCraft 2 reader is not available to map the name"
    low = name.lower()
    for col, targets in bc2.METRIC_COLUMNS.items():
        if col.lower() == low:
            return targets[0][0], targets[0][1], ""
    for stem, (cols, why) in bc2.UNMAPPED_STEMS.items():
        if any(c.strip().lower() == low for c in cols.split(",")):
            return None, None, f"Binderscope does not map this column to a metric: {why}"
    return None, None, "Binderscope carries no metric of this name (the column stays in the design's extras)"


def _user_storage(name: str) -> tuple[Optional[str], Optional[float], str]:
    """(stem, factor, why) for a user / RFD4 cutoff name."""
    low = name.lower().replace(".", "_")
    if low in _USER_METRIC_ALIASES:
        return _USER_METRIC_ALIASES[low], 1.0, ""
    stem, factor, why = _storage(name)
    if stem is not None:
        return stem, factor, why
    # Allow a stem that is already the stored name.
    return name, 1.0, ""


def _design_has_stem(designs: list[Any], stem: str, per_chain: bool) -> bool:
    if per_chain:
        return any((getattr(d, "per_chain", None) or {}) and
                   any(vals.get(stem) is not None for vals in (d.per_chain or {}).values())
                   for d in designs) or any((d.metrics or {}).get(stem) is not None for d in designs)
    return any((d.metrics or {}).get(stem) is not None for d in designs)


def filters_for_run(run: Any) -> FilterSet:
    """The filters `run` was judged by.

    A run that recorded its own thresholds (BindCraft 2: campaign_metadata.json, or RFD4: user cutoffs read into
    annotations["filters"]) is judged by them. BindCraft 2 rules carry provenance "campaign"; RFD4 / user cutoffs
    carry provenance "user" and FilterSet source "user". A run that recorded nothing (BindCraft 1) keeps
    DEFAULT_FILTERS. A recorded threshold on a metric Binderscope does not carry, or that no design carries, or
    whose scale differs from the stored one, cannot be evaluated: it goes to `unevaluated` and is never silently
    dropped. An infinite threshold means no limit and is neither a rule nor unevaluated.
    """
    recorded, unlimited, has = _recorded_filters(run)
    if not has:
        return DEFAULT_FILTER_SET
    designs = list(getattr(run, "designs", None) or [])
    # User cutoffs win the set's source when any recorded filter says so (RFD4); otherwise campaign.
    is_user = any(str(f.get("provenance") or f.get("source") or "").lower() in ("user", "rfd4.defaults")
                  for f in recorded)
    rules: list[FilterRule] = []
    skipped: list[UnevaluatedFilter] = []
    for f in recorded:
        name = str(f["name"])
        higher = bool(f.get("higher", str(f.get("op", ">=")).strip() in (">=", ">")))
        thr = float(f["threshold"])
        mand = f.get("mandatory")
        mand = bool(mand) if isinstance(mand, bool) else None
        src = str(f.get("source") or ("rfd4.defaults" if is_user else "campaign_metadata"))
        op = ">=" if higher else "<="
        per_chain = bool(f.get("per_chain"))
        chain_list = f.get("chains")
        chains = tuple(str(c) for c in chain_list) if isinstance(chain_list, (list, tuple)) and chain_list else None
        stem, factor, why = (_user_storage(name) if is_user else _storage(name))
        if stem is not None and factor != 1.0:
            why = f"Binderscope stores this metric multiplied by {factor:g}, so its value is not on the scale of the recorded threshold"
            stem = None
        if stem is not None and not _design_has_stem(designs, stem, per_chain):
            why = f"no design of this run carries the metric {stem}"
            stem = None
        if stem is None:
            skipped.append(UnevaluatedFilter(name, op, thr, mand, src, why))
            continue
        if is_user:
            bits = [f"The threshold chosen at import ({src})."]
            if per_chain:
                bits.append("Applied to every required target chain; a design must clear the bar on each.")
            if stem != name:
                bits.append(f"Evaluated on {stem}.")
            rules.append(FilterRule(
                name, thr, higher, per_model=None, provenance=PROVENANCE_USER, note=" ".join(bits),
                metric=stem if stem != name else None, mandatory=mand, judged_per_model=False, source=src,
                per_chain=per_chain, chains=chains))
            continue
        per_model = name in PER_MODEL_JUDGED
        bits = [f"The threshold this campaign recorded ({'mandatory' if mand else 'optional'}; {src})."]
        if stem != name:
            bits.append(f"Evaluated on {stem}.")
        if per_model:
            bits.append("BindCraft judges it on each validation model; the CSV holds the rounded mean, so a design can fail it "
                        "with a CSV value that passes, and BindCraft's own failed_filters decides.")
        rules.append(FilterRule(
            name, thr, higher, per_model=None, provenance=PROVENANCE_CAMPAIGN, note=" ".join(bits),
            metric=stem if stem != name else None, mandatory=mand, judged_per_model=per_model, source=src))
    if is_user:
        return FilterSet(tuple(rules), "user", "the thresholds chosen at import", tuple(skipped), tuple(unlimited))
    return FilterSet(tuple(rules), "campaign", "the thresholds this campaign recorded", tuple(skipped), tuple(unlimited))


def parse_cutoff_arg(text: str) -> dict[str, Any]:
    """Parse one ``--cutoff NAME=OP:VALUE`` or ``NAME@CHAIN=OP:VALUE`` into a filter annotation dict.

    OP is ``ge``, ``le``, ``>=``, ``<=``, ``gt``, ``lt``. NAME may use a dotted RFD4 column (``oracle.iptm_coi``)
    or a short alias (``iptm``, ``min_ipae``). ``@CHAIN`` makes the rule per-chain for that letter only.
    """
    raw = str(text).strip()
    if "=" not in raw:
        raise ValueError(f"cutoff {raw!r}: expected NAME=OP:VALUE")
    left, right = raw.split("=", 1)
    left, right = left.strip(), right.strip()
    chain: Optional[str] = None
    if "@" in left:
        name, chain = left.split("@", 1)
        name, chain = name.strip(), chain.strip()
        if not chain:
            raise ValueError(f"cutoff {raw!r}: empty chain after @")
    else:
        name = left
    if ":" in right:
        op_s, thr_s = right.split(":", 1)
    else:
        # Bare number: infer direction from common names.
        op_s, thr_s = "ge", right
    op_s = op_s.strip().lower()
    op_map = {">=": "ge", ">": "gt", "<=": "le", "<": "lt", "ge": "ge", "le": "le", "gt": "gt", "lt": "lt"}
    if op_s not in op_map:
        raise ValueError(f"cutoff {raw!r}: unknown op {op_s!r}")
    op_key = op_map[op_s]
    higher = op_key in ("ge", "gt")
    try:
        thr = float(thr_s.strip())
    except ValueError as ex:
        raise ValueError(f"cutoff {raw!r}: threshold is not a number") from ex
    # Strip a leading oracle. / oracle_ppi. prefix for the stored name.
    short = name
    for prefix in ("oracle_ppi.", "oracle.", "refolded_", "motif_scaffold.", "ppi."):
        if short.lower().startswith(prefix):
            short = short[len(prefix):]
            break
    out: dict[str, Any] = {
        "name": short, "op": ">=" if higher else "<=", "threshold": thr, "higher": higher,
        "mandatory": True, "source": "user", "provenance": PROVENANCE_USER, "per_chain": bool(chain),
    }
    if chain:
        out["chains"] = [chain]
        # Per-chain short names: iptm / min_ipae rather than iptm_coi.
        if out["name"].lower() in ("iptm_coi", "i_ptm"):
            out["name"] = "iptm"
        if out["name"].lower() in ("min_ipae_coi",):
            out["name"] = "min_ipae"
    return out


#: Default cutoffs for an RFD4 import when the person does not pass --cutoff.
RFD4_DEFAULT_CUTOFFS: tuple[dict[str, Any], ...] = (
    {"name": "pLDDT", "op": ">=", "threshold": 0.80, "higher": True, "mandatory": True,
     "source": "rfd4.defaults", "provenance": PROVENANCE_USER, "per_chain": False},
    {"name": "has_clash", "op": "<=", "threshold": 0.0, "higher": False, "mandatory": True,
     "source": "rfd4.defaults", "provenance": PROVENANCE_USER, "per_chain": False},
    {"name": "motif_ca_rmsd", "op": "<=", "threshold": 1.0, "higher": False, "mandatory": False,
     "source": "rfd4.defaults", "provenance": PROVENANCE_USER, "per_chain": False},
    {"name": "complex_rmsd", "op": "<=", "threshold": 2.5, "higher": False, "mandatory": False,
     "source": "rfd4.defaults", "provenance": PROVENANCE_USER, "per_chain": False},
    {"name": "iptm", "op": ">=", "threshold": 0.50, "higher": True, "mandatory": True,
     "source": "rfd4.defaults", "provenance": PROVENANCE_USER, "per_chain": True},
    {"name": "min_ipae", "op": "<=", "threshold": 2.0, "higher": False, "mandatory": True,
     "source": "rfd4.defaults", "provenance": PROVENANCE_USER, "per_chain": True},
)
