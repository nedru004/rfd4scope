"""What the two toy runs contain: backbones in trajectory order, their goals and their sequences.

A backbone is one BindCraft trajectory. `accepted` lists the ProteinMPNN sequence numbers that passed every filter,
`rejected` maps a sequence number to the filter stems it fails (BindCraft keeps a row in mpnn_design_stats.csv for
such sequences). `kind` is the docking goal (see poses.py); None means the trajectory never reached sequence design
and only has a row in trajectory_stats.csv. `family_of` makes the backbone a neighbour of an earlier one (the same
fold docked at nearly the same place, so that the family code puts both in one cluster).

Three optional fields say what the plan promises beyond the goal, and the generator holds every variant to it (project.py,
selfcheck.py): `site` docks the backbone at an end of the construct, so that fewer than 12 target residues lie near it and
the placement of its designs falls back on the whole-target fit; `partner_gap` fixes the closest approach of a variant to
chain B (A) so that exactly one accepted design lies within 3 A of it; `near_miss` names the rejected sequences whose
Binder_RMSD miss is within a quarter of the threshold (the other Binder_RMSD misses are certainly over it).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Spec:
    name: str
    length: int
    helices: int
    kind: str | None
    accepted: list[int] = field(default_factory=list)
    rejected: dict[int, list[str]] = field(default_factory=dict)
    family_of: str | None = None
    quirks: dict[int, str] = field(default_factory=dict)       # sequence number -> sequence quirk (see runs.py)
    structure: bool = True                                     # False: metric-only rows (no pose, no files)
    site: str | None = None                                    # "terminus": docked at an end of the construct, mostly beyond it (see project._candidates)
    partner_gap: dict[int, tuple[float, float]] = field(default_factory=dict)   # sequence number -> window (A) for the closest approach to chain B
    near_miss: list[int] = field(default_factory=list)         # rejected sequence numbers whose Binder_RMSD miss is within a quarter of the threshold


# Run 1: the whole construct, hotspots in a settings file, a Rejected folder next to Accepted.
RUN1 = [
    Spec("a1", 68, 3, "clean_hot", accepted=[3, 7], rejected={12: ["ShapeComplementarity"]}, quirks={7: "sequon"}),
    Spec("t1", 66, 3, None),
    Spec("a2", 68, 3, "clean_hot", accepted=[2, 9], family_of="a1", quirks={2: "odd_cys"}),
    Spec("x1", 70, 3, "clean_away", rejected={4: ["Binder_RMSD"], 9: ["ShapeComplementarity", "n_InterfaceHbonds"],
                                               15: ["Binder_pLDDT"]}),
    Spec("a3", 76, 3, "clean_away", accepted=[5], rejected={8: ["n_InterfaceUnsatHbonds"], 14: ["Surface_Hydrophobicity"]}),
    Spec("a4", 82, 2, "clean_away", accepted=[4, 11], quirks={4: "ng_dg"}),
    Spec("t2", 74, 3, None),
    Spec("a5", 64, 2, "collide_out", accepted=[6]),
    Spec("a6", 88, 3, "touch_other", accepted=[1, 10], partner_gap={1: (2.45, 2.65), 10: (3.4, 3.8)}),
    Spec("x2", 85, 3, "clean_away", rejected={3: ["InterfaceAAs_K"], 14: ["Binder_RMSD"]}, near_miss=[14]),
    Spec("a7", 79, 3, "clean_hot", accepted=[13]),
    Spec("t3", 90, 3, None),
    Spec("a8", 93, 3, "clean_away", accepted=[2], site="terminus"),
]

# Run 2: a target without two loops, no settings file (hotspots only in the CSV), no Rejected folder.
RUN2 = [
    Spec("c1", 66, 3, "clean_hot", accepted=[3]),
    Spec("y1", 72, 3, None, rejected={2: ["Surface_Hydrophobicity"], 6: ["ShapeComplementarity"], 10: ["n_InterfaceUnsatHbonds"]},
         structure=False),
    Spec("c2", 66, 3, "clean_hot", accepted=[1, 8], rejected={14: ["Binder_pLDDT"]}, family_of="c1"),
    Spec("t4", 80, 3, None),
    Spec("c3", 100, 3, "clean_away", accepted=[6], rejected={12: ["n_InterfaceHbonds"]}),
    Spec("c4", 84, 3, "collide_gap", accepted=[5]),
    Spec("y2", 78, 3, None, rejected={5: ["Binder_RMSD"], 9: ["ShapeComplementarity"]}, structure=False),
    Spec("c5", 77, 3, "clean_away", accepted=[2, 9], rejected={11: ["Surface_Hydrophobicity"]}),
    Spec("c6", 92, 3, "collide_out", accepted=[4]),
    Spec("t5", 64, 2, None),
    Spec("c7", 108, 3, "clean_away", accepted=[7]),
    Spec("c8", 72, 3, "clean_away", accepted=[3]),
]

CONSTRUCT_DELETED_REF = [(79, 82), (139, 142)]     # reference numbers of the two loops run 2 lacks


# A small project for the schema examples (docs/schema/examples): a few backbones of each kind, shorter binders.
RUN1_EXAMPLE = [
    Spec("a1", 62, 3, "clean_hot", accepted=[3, 7], rejected={12: ["ShapeComplementarity"]}),
    Spec("t1", 60, 3, None),
    Spec("a2", 62, 3, "clean_hot", accepted=[2], family_of="a1"),
    Spec("x1", 66, 3, "clean_away", rejected={4: ["Binder_pLDDT"], 9: ["ShapeComplementarity", "n_InterfaceHbonds"]}),
    Spec("a5", 60, 2, "collide_out", accepted=[6]),
    Spec("a6", 68, 3, "touch_other", accepted=[1]),
]
RUN2_EXAMPLE = [
    Spec("c1", 62, 3, "clean_hot", accepted=[3]),
    Spec("y1", 64, 3, None, rejected={2: ["Surface_Hydrophobicity"], 6: ["n_InterfaceUnsatHbonds"]}, structure=False),
    Spec("c2", 62, 3, "clean_hot", accepted=[1], family_of="c1"),
    Spec("c4", 66, 3, "collide_gap", accepted=[5]),
]
