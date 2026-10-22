#!/usr/bin/env python
"""Populate the reference cache (data/reference/) with the public records a project needs, and viewer/vendor/ with 3Dmol.js.

Run with network access. Cached files are not fetched again. Only accessions, PDB ids, gene symbols and taxon ids are sent to
remote servers; no binder sequence and no design id is involved.

    python scripts/fetch_reference.py --uniprot ACCESSION --reference-id PDBID
        the target: its UniProt entry and sequence, the PDBe/SIFTS mappings of the reference structure, the AlphaFold DB model
    python scripts/fetch_reference.py --profile PROFILE.json
        everything a profile names: target.uniprot, target.reference.pdb, the specificity.species entries given as acc:<accession>
        and the specificity.paralogs.members (UniProt entry, sequence, AlphaFold DB model of each)
    python scripts/fetch_reference.py --config PANEL.json
        a panel chosen by search (below); writes data/reference/panel.json, which the exporter reads for the orthologs and paralogs
        that a profile names without details. Add --uniprot / --reference-id to override the target of the file.

PANEL.json (all keys but target optional):
    {"target": {"accession": "P12345", "pdb": "1abc"},
     "orthologs": {"search_gene": "GENE",                     # UniProt gene symbol searched in every taxon
                   "fallback_protein_name": "word",           # protein-name word searched when the gene symbol finds nothing
                   "paralog_reference": "P23456",             # closest paralog: a candidate must be closer to the target than to it
                   "min_identity": 0.7, "min_length": 700, "max_length": 800,
                   "species": [["Mus musculus", "mouse", 10090, "default"], ["Sus scrofa", "pig", 9823, "optional"]]},
     "paralogs": [["P23456", "GENE2", 9606, "default"]]}     # accession, expected gene, expected taxon, role

--no-vendor skips viewer/vendor/3Dmol-min.js; --vendor-only fetches nothing else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from binder_review import refdata as rd  # noqa: E402

VENDOR_3DMOL = "https://cdnjs.cloudflare.com/ajax/libs/3Dmol/2.5.5/3Dmol-min.js"
VENDOR_3DMOL_LICENSE = "https://raw.githubusercontent.com/3dmol/3Dmol.js/2.5.5/LICENSE"


def fetch_target(accession: str | None, pdb: str | None, chain: str = "A", alphafold: bool = True, consurf: bool = False) -> dict | None:
    """The records of the target itself; returns its UniProt summary (None without an accession)."""
    summary = None
    if accession:
        print(f"target {accession}")
        summary = rd.uniprot_summary(rd.get_uniprot(accession))
        rd.get_uniprot_fasta(accession)
        if alphafold:
            af = rd.get_alphafold(accession)
            print(f"  AlphaFold DB model {af['model_path'].name}, PAE {'cached' if af['pae'] else 'skipped'}")
    if pdb and accession:
        print(f"PDBe / SIFTS {pdb}")
        for kind in rd.SIFTS_KINDS:
            rd.get_sifts(pdb, kind)
        unobs = rd.get_unobserved(pdb)
        print(f"  unobserved residues source: {unobs['source']}")
    if pdb and consurf:
        print("ConSurf-DB (optional, academic use)")
        grades = rd.get_consurfdb(pdb, chain)
        print(f"  {'no data' if grades is None else f'{len(grades)} grades'}")
    return summary


def fetch_from_profile(profile: dict, alphafold: bool = True) -> None:
    """Every accession the profile names: species given as acc:<accession> and the members of the paralog panel."""
    sp = profile.get("specificity") or {}
    accs = [s["match"][4:] for s in sp.get("species") or [] if str(s.get("match", "")).startswith("acc:")]
    accs += [m["acc"] for m in (sp.get("paralogs") or {}).get("members") or []]
    print(f"{len(accs)} ortholog and paralog accessions")
    for acc in accs:
        summary = rd.uniprot_summary(rd.get_uniprot(acc))
        rd.get_uniprot_fasta(acc)
        print(f"  {acc} {summary['gene'] or '-'} {summary['organism']} {summary['length']} aa")
    if alphafold:
        for m in (sp.get("paralogs") or {}).get("members") or []:
            rd.get_alphafold(m["acc"], with_pae=False)


def choose_orthologs(cfg: dict, ref_seq: str, paralog_seq: str | None, paralog_gene: str | None) -> list[dict]:
    """One entry per species of the config: the best-ranked UniProt entry (``rd.rank_ortholog_candidates``) of that taxon."""
    gene = cfg.get("search_gene")
    kw = {k: cfg[k] for k in ("min_identity", "min_length", "max_length") if k in cfg}
    panel = []
    for species, common, taxon, role in cfg.get("species", []):
        rows = rd.search_uniprot(gene, taxon) if gene else []
        ranked = rd.rank_ortholog_candidates(rows, ref_seq, paralog_seq, gene=gene, **kw)
        basis = f"gene name {gene}"
        if not ranked and cfg.get("fallback_protein_name"):
            # some proteomes carry no gene name on their entries of the target
            rows = rd.search_uniprot(None, taxon, protein_name=cfg["fallback_protein_name"])
            ranked = rd.rank_ortholog_candidates(rows, ref_seq, paralog_seq, gene=gene, **kw)
            basis = f"no entry with gene name {gene}; chosen by sequence among entries named {cfg['fallback_protein_name']}"
        if not ranked:
            print(f"  no candidate for {species}", file=sys.stderr)
            continue
        best = ranked[0]
        near = f" ({paralog_gene} {best['identity_to_paralog']:.1%})" if "identity_to_paralog" in best else ""
        reason = (f"{basis}; {'reviewed' if best['reviewed'] else 'unreviewed'}, {best['length']} aa, "
                  f"{best['identity_to_reference']:.1%} identical to the reference{near}; "
                  f"{len(ranked)} eligible entries, ranked reviewed first, then length closest to the reference, "
                  f"then identity to the reference")
        rd.get_uniprot(best["accession"])
        rd.get_uniprot_fasta(best["accession"])
        entry = {
            "accession": best["accession"], "entry_name": best["entry_name"],
            "gene": (best["gene_names"] or [None])[0], "species": species, "common_name": common,
            "organism": best["organism"], "taxon_id": taxon, "length": best["length"],
            "reviewed": best["reviewed"], "role": role, "reason": reason, "identity_to_reference": best["identity_to_reference"],
            "candidates": [{k: c[k] for k in ("accession", "reviewed", "length", "gene_matches", "identity_to_reference") if k in c} for c in ranked],
        }
        panel.append(entry)
        print(f"  {common:11s} {best['accession']:11s} {best['length']} aa id={best['identity_to_reference']:.3f} ({len(ranked)} candidates)")
    return panel


def fetch_paralogs(items: list, alphafold: bool = True) -> list[dict]:
    out = []
    for acc, gene, taxon, role in items:
        summary = rd.uniprot_summary(rd.get_uniprot(acc))
        rd.get_uniprot_fasta(acc)
        if summary["gene"] != gene or summary["taxon_id"] != taxon:
            raise SystemExit(f"{acc}: expected {gene}/{taxon}, got {summary['gene']}/{summary['taxon_id']}")
        if alphafold:
            rd.get_alphafold(acc, with_pae=False)
        out.append({**summary, "role": role, "species": summary["organism"], "common_name": "human" if taxon == 9606 else summary["organism"],
                    "reason": f"related protein ({gene}), verified gene name and organism"})
        print(f"  {acc} {summary['gene']} {summary['organism']} {summary['length']} aa")
    return out


def vendor_3dmol(vendor_dir: Path, force: bool) -> None:
    vendor_dir.mkdir(parents=True, exist_ok=True)
    js = vendor_dir / "3Dmol-min.js"
    lic = vendor_dir / "3Dmol-LICENSE.txt"
    for dest, url in ((js, VENDOR_3DMOL), (lic, VENDOR_3DMOL_LICENSE)):
        if dest.exists() and not force:
            continue
        req = urllib.request.Request(url, headers={"User-Agent": rd.USER_AGENT})
        with urllib.request.urlopen(req, timeout=rd.TIMEOUT_S) as resp:
            dest.write_bytes(resp.read())
    digest = hashlib.sha256(js.read_bytes()).hexdigest()
    print(f"  3Dmol-min.js sha256 {digest} ({js.stat().st_size} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uniprot", metavar="ACCESSION", help="UniProt accession of the target")
    ap.add_argument("--reference-id", metavar="PDBID", help="PDB id of the reference structure (SIFTS mappings, ConSurf-DB)")
    ap.add_argument("--reference-chain", default="A", metavar="CHAIN")
    ap.add_argument("--profile", type=Path, metavar="PROFILE.json", help="fetch everything this profile names")
    ap.add_argument("--config", type=Path, metavar="PANEL.json", help="choose a panel by search and write data/reference/panel.json")
    ap.add_argument("--consurf", action="store_true", help="also fetch the optional ConSurf-DB grades (academic, non-commercial use)")
    ap.add_argument("--no-alphafold", action="store_true", help="skip the AlphaFold DB models")
    ap.add_argument("--no-vendor", action="store_true", help="skip viewer/vendor/3Dmol-min.js")
    ap.add_argument("--force-vendor", action="store_true")
    ap.add_argument("--vendor-only", action="store_true", help="only vendor 3Dmol.js")
    args = ap.parse_args()
    af = not args.no_alphafold

    if not args.vendor_only:
        profile = json.loads(args.profile.read_text(encoding="utf-8")) if args.profile else {}
        cfg = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
        tgt = profile.get("target") or {}
        cfgt = cfg.get("target") or {}
        acc = args.uniprot or cfgt.get("accession") or tgt.get("uniprot") or None
        pdb = args.reference_id or cfgt.get("pdb") or (tgt.get("reference") or {}).get("pdb") or None
        if pdb and not (len(pdb) == 4 and pdb[0].isdigit() and pdb.isalnum()):
            print(f"reference {pdb} is not a PDB id: no SIFTS or ConSurf-DB records")
            pdb = None
        if not (acc or args.profile or args.config):
            ap.error("give --uniprot, --profile or --config (or --vendor-only)")
        summary = fetch_target(acc, pdb, args.reference_chain, alphafold=af, consurf=args.consurf)
        if args.profile:
            fetch_from_profile(profile, alphafold=af)
        if args.config:
            if not acc:
                ap.error("the panel config names no target accession (target.accession) and --uniprot is not given")
            ref_seq = rd.fasta_sequence(rd.get_uniprot_fasta(acc))
            ocfg = cfg.get("orthologs") or {}
            print("paralogs")
            paralogs = fetch_paralogs(cfg.get("paralogs") or [], alphafold=af)
            par_acc = ocfg.get("paralog_reference")
            par_seq = rd.fasta_sequence(rd.get_uniprot_fasta(par_acc)) if par_acc else None
            par_gene = next((p["gene"] for p in paralogs if p["accession"] == par_acc), par_acc)
            print("orthologs")
            orthologs = choose_orthologs(ocfg, ref_seq, par_seq, par_gene)
            panel = {"target": summary, "orthologs": orthologs, "paralogs": paralogs}
            rd.cache_dir().mkdir(parents=True, exist_ok=True)
            (rd.cache_dir() / rd.PANEL_FILE).write_text(json.dumps(panel, indent=1) + "\n")
        problems = rd.verify_cache()
        for p in problems:
            print("PROBLEM:", p, file=sys.stderr)
    else:
        problems = []

    if not args.no_vendor:
        print("vendored libraries")
        vendor_3dmol(ROOT / "viewer" / "vendor", args.force_vendor)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
