"""The toy reference: the full-length homodimer as mmCIF and PDB, its sequence and a few related sequences."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import structures as ST
from .target import ToyTarget


def _mutate(seq: str, identity: float, rng: np.random.Generator, indel: bool = False) -> str:
    """A related sequence: about `identity` of the positions kept (substitutions only, or one short indel)."""
    aas = "ACDEFGHIKLMNPQRSTVWY"
    s = list(seq)
    for i in range(len(s)):
        if rng.random() > identity:
            s[i] = str(rng.choice([a for a in aas if a != s[i]]))
    if indel:
        i = int(rng.integers(40, len(s) - 40))
        s[i:i + 3] = []
        j = int(rng.integers(40, len(s) - 40))
        s[j:j] = list("GSA")
    return "".join(s)


def _fasta(entries: list[tuple[str, str]]) -> str:
    out = []
    for name, seq in entries:
        out.append(f">{name}")
        out += [seq[i:i + 60] for i in range(0, len(seq), 60)]
    return "\n".join(out) + "\n"


TOY_ACCESSIONS = {  # key -> (accession, gene, organism, taxon id, role)
    "target": ("Z0TOY1", "TOYA", "Toyus primus", 9000001, "target"),
    "orth1": ("Z0TOY2", "TOYA", "Toyus secundus", 9000002, "default"),
    "orth2": ("Z0TOY3", "TOYA", "Toyus tertius", 9000003, "default"),
    "orth3": ("Z0TOY4", "TOYA", "Toyus quartus", 9000004, "optional"),
    "PARA1": ("Z0TOY5", "PARA1", "Toyus primus", 9000001, "default"),
    "PARA2": ("Z0TOY6", "PARA2", "Toyus primus", 9000001, "default"),
}


def _uniprot_entry(acc: str, gene: str, organism: str, taxon: int, seq: str) -> dict:
    """A UniProt-style JSON record (only the fields refdata reads)."""
    return {"primaryAccession": acc, "uniProtkbId": f"{gene}_{acc}", "entryType": "UniProtKB unreviewed (TrEMBL)",
            "genes": [{"geneName": {"value": gene}}], "organism": {"scientificName": organism, "taxonId": taxon},
            "sequence": {"length": len(seq), "value": seq}}


def _write_af_model(d: Path, acc: str, seq: str, ca: np.ndarray, rng: np.random.Generator, noise: float) -> None:
    """A toy 'AlphaFold DB' record: prediction.json and a CA-only model file in the cache layout of refdata.get_alphafold."""
    import json

    from . import geometry as G

    folder = d / "alphafold" / acc
    folder.mkdir(parents=True, exist_ok=True)
    name = f"AF-{acc}-F1-model_v4.cif"
    (folder / "prediction.json").write_text(json.dumps([{"entryId": f"AF-{acc}-F1", "uniprotAccession": acc,
                                                         "cifUrl": f"https://example.invalid/files/{name}", "paeDocUrl": None}]))
    at = G.Atoms()
    R = G.random_rotation(rng) if noise else np.eye(3)
    xyz = ca @ R.T + (rng.normal(size=3) * 5 if noise else 0.0) + rng.normal(size=ca.shape) * noise
    for i, (aa, p) in enumerate(zip(seq, xyz), start=1):
        at.name.append("CA")
        at.element.append("C")
        at.resname.append(G.AA3[aa])
        at.resnum.append(i)
        at.chain.append("A")
        at.bfactor.append(90.0)
        at.xyz_list.append(p)
    ST.write_cif(folder / name, [at], f"AF-{acc}-F1")


def write_refdata_cache(d: Path, seq: str, related: list[tuple[str, str]], ca: np.ndarray | None = None) -> None:
    """A toy offline reference cache (the layout of data/reference): UniProt-style records for the toy accessions and a panel
    file that names the orthologs and paralogs. Point BINDERSCOPE_REFDATA_DIR at it. Everything is invented."""
    import json

    (d / "uniprot").mkdir(parents=True, exist_ok=True)
    seqs = {"target": seq, "orth1": related[0][1], "orth2": related[1][1], "orth3": related[2][1], "PARA1": related[3][1], "PARA2": related[4][1]}
    for key, (acc, gene, organism, taxon, role) in TOY_ACCESSIONS.items():
        s = seqs[key]
        (d / "uniprot" / f"{acc}.json").write_text(json.dumps(_uniprot_entry(acc, gene, organism, taxon, s), indent=1))
        (d / "uniprot" / f"{acc}.fasta").write_text(_fasta([(f"tr|{acc}|{gene}_{acc} {organism}", s)]))

    def entry(key: str, kind: str) -> dict:
        acc, gene, organism, taxon, role = TOY_ACCESSIONS[key]
        e = {"accession": acc, "gene": gene, "species": organism, "organism": organism, "common_name": key.replace("orth", "ortholog "),
             "taxon_id": taxon, "length": len(seqs[key]), "reviewed": False, "role": "optional" if role == "optional" else "default",
             "reason": f"toy {kind}"}
        return e

    panel = {"target": {"accession": TOY_ACCESSIONS["target"][0], "gene": "TOYA", "organism": "Toyus primus", "length": len(seq)},
             "orthologs": [entry(k, "ortholog") for k in ("orth1", "orth2", "orth3")],
             "paralogs": [entry(k, "paralog") for k in ("PARA1", "PARA2")]}
    (d / "panel.json").write_text(json.dumps(panel, indent=1))
    if ca is not None:                       # models of the target and of the paralogs: the backbone of the target with their sequences
        rng = np.random.default_rng(31)
        _write_af_model(d, TOY_ACCESSIONS["target"][0], seq, ca, rng, 0.0)
        for key in ("PARA1", "PARA2"):
            _write_af_model(d, TOY_ACCESSIONS[key][0], seqs[key], ca, rng, 0.4)


def write_reference(out: Path, tg: ToyTarget, seed: int) -> None:
    d = out / "reference"
    d.mkdir(parents=True, exist_ok=True)
    groups = [tg.atoms_a, tg.atoms_b]
    helices = []
    ss = tg.chain_a.ss
    i = 0
    while i < len(ss):
        if ss[i] == "H":
            j = i
            while j + 1 < len(ss) and ss[j + 1] == "H":
                j += 1
            helices.append((i + 1, j + 1))
            i = j + 1
        else:
            i += 1
    ST.write_cif(d / "toy_target.cif", groups, "toy_target")
    ST.write_pdb(d / "toy_target.pdb", groups, "toy_target")
    # the same structure with the chain ids swapped: the target (the chain the construct was cut from) is chain B, its partner chain A
    ST.write_cif(d / "toy_target_chain_b.cif", [tg.atoms_b.copy(chain="A"), tg.atoms_a.copy(chain="B")], "toy_target_chain_b")
    seq = tg.sequence
    (d / "toy_target_chain_A.fasta").write_text(_fasta([("toy_target chain A (reference numbering 1 to %d)" % len(seq), seq)]))
    rng = np.random.default_rng(seed + 500)
    related = [("ortholog_1 identity about 92 percent", _mutate(seq, 0.92, rng)),
               ("ortholog_2 identity about 80 percent", _mutate(seq, 0.80, rng, indel=True)),
               ("ortholog_3 identity about 76 percent", _mutate(seq, 0.76, rng)),
               ("paralog_1 identity about 45 percent", _mutate(seq, 0.45, rng)),
               ("paralog_2 identity about 32 percent", _mutate(seq, 0.32, rng))]
    (d / "related_sequences.fasta").write_text(_fasta(related))
    write_refdata_cache(d / "refdata", seq, related, tg.chain_a.CA)
