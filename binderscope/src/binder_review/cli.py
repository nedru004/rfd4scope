"""Command line entry point: import, chains, inspect, build-bundle (docs/cli.md)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from . import __version__

if TYPE_CHECKING:
    from .model import RunData

NOT_IMPLEMENTED = 2


def _relpath(arg: str) -> str:
    """An argument that names an existing path, relative to the working directory when it lies below it."""
    try:
        p = Path(arg)
        if p.exists():
            rel = os.path.relpath(p.resolve(), Path.cwd())
            return rel if not rel.startswith("..") else str(p.resolve())
    except (OSError, ValueError):
        pass
    return arg


def command_line(argv: list[str]) -> str:
    """The command that produced a project, paths relative to the working directory (manifest tool.command)."""
    parts = []
    for a in argv:
        k, eq, v = a.partition("=")
        if eq and not k.startswith("-") and "/" not in k:      # ID=PATH
            parts.append(f"{k}={_relpath(v)}")
        else:
            parts.append(_relpath(a))
    return "binder-review " + " ".join(parts)


def _parse_run_args(args: argparse.Namespace):
    """RUN_DIR or ID=RUN_DIR arguments, --settings ID=FILE and --label ID=TEXT into RunSpecs."""
    from .pipeline import RunSpec

    settings = dict(x.split("=", 1) for x in (args.settings or []))
    labels = dict(x.split("=", 1) for x in (args.label or []))
    specs, seen = [], set()
    for item in args.run_dirs:
        rid, eq, path = item.partition("=")
        if not eq or "/" in rid or Path(item).exists():
            rid, path = None, item
        folder = Path(path)
        rid = rid or default_run_id(folder.resolve())
        if rid in seen:
            raise ValueError(f"run id {rid!r} is used twice; give the runs ids with ID=RUN_DIR")
        seen.add(rid)
        specs.append(RunSpec(path=folder, run_id=rid, label=labels.get(rid), settings=Path(settings[rid]) if rid in settings else None))
    unknown = (set(settings) | set(labels)) - seen
    if unknown:
        raise ValueError(f"--settings/--label name runs that are not given: {', '.join(sorted(unknown))}")
    return specs


def _cmd_import(args: argparse.Namespace) -> int:
    from . import bundle as B
    from . import export as E
    from . import pipeline as P
    from . import readers as R
    from . import structure as S
    from . import target_frame as TF

    out = Path(args.out or args.project or "work/project")
    try:
        runs = _parse_run_args(args)
    except ValueError as ex:
        print(f"binder-review import: {ex}", file=sys.stderr)
        return 2
    if not args.target_pdb:
        print("binder-review import: --target-pdb is required", file=sys.stderr)
        return 2
    if not args.reference_cif:
        # these name parts of the reference structure; without one they would be ignored without a word, so say so
        named = [flag for flag, given in (("--reference-id", args.reference_id), ("--reference-chain", args.reference_chain not in ("auto", "A")),
                                          ("--partner-chains", args.partner_chains not in ("auto", "none")), ("--assembly", args.assembly))
                 if given]
        if named:
            print(f"binder-review import: {', '.join(named)} describe the reference structure, and none was given (--reference-cif). "
                  "Without a reference the construct is the frame: its target chain is A and there is no other subunit.", file=sys.stderr)
            return 2
    inside = _inside_an_input(runs, [out] + ([Path(args.bundle)] if args.bundle else []))
    if inside:
        print(f"binder-review import: {inside}", file=sys.stderr)
        return 2
    name = args.name or out.name
    partners = _partner_arg(args.partner_chains)
    spec = P.ProjectSpec(name=name, runs=runs, target_pdb=Path(args.target_pdb), reference_cif=Path(args.reference_cif) if args.reference_cif else None,
                         uniprot=args.uniprot, profile=args.profile, consurf=args.consurf, online=args.online, jobs=args.jobs,
                         use_cache=not args.no_cache, command=command_line(sys.argv[1:]), reference_id=args.reference_id,
                         reference_chain=None if args.reference_chain == "auto" else args.reference_chain, partner_chains=partners,
                         assembly=args.assembly, placement=args.placement, context_hard=args.context_check)
    log = P.Log(quiet=args.quiet)
    t0 = time.perf_counter()
    try:
        project = P.run_pipeline(spec, out, log=log)
        with log.stage("export and validation"):
            blobs = E.build_blobs(project)
            E.write_export(blobs, out / "export")
        write_project_json(project, blobs, out, time.perf_counter() - t0)
        if args.bundle:
            with log.stage("bundle"):
                index = B.write_bundle(out / "export", Path(args.bundle), name=Path(args.bundle).name, fonts=args.fonts, csp=args.csp,
                                       guide=False if args.no_guide else None, structure_scripts=not args.no_structure_scripts,
                                       embed_structures=getattr(args, "embed_structures", False), log=log)
    except TF.FrameError as ex:       # the inputs do not fit together (numbering, chain, hotspots): a problem of the arguments
        print(f"binder-review import: {ex}", file=sys.stderr)
        return 2
    except R.ReaderError as ex:       # the run folder cannot be read: a problem of the input, not a bug
        print(f"binder-review import: {ex}", file=sys.stderr)
        return 2
    except (P.PipelineError, E.ExportError, B.BundleError, S.StructureError) as ex:
        print(f"binder-review import: {ex}", file=sys.stderr)
        return 1
    total = time.perf_counter() - t0
    log(f"finished in {total:.1f} s")
    print(format_project_summary(out))
    if args.bundle:
        print(f"\nbundle: {index}")
    return 0


def _inside_an_input(runs, outputs) -> Optional[str]:
    """A message when an output folder lies inside (or is) an input run folder: inputs are read-only, nothing is ever written there."""
    for o in outputs:
        ro = Path(o).resolve()
        for r in runs:
            rr = Path(r.path).resolve()
            if ro == rr or rr in ro.parents:
                return f"the output folder {o} is inside the run folder {r.path}: input folders are never written to"
    return None


def write_project_json(project, blobs, out: Path, seconds: float) -> None:
    """work/<project>/project.json: what was imported and how long it took (read by ``inspect`` and ``build-bundle``)."""
    from .pipeline import utc_now

    spec = project.spec
    m = blobs.manifest
    info = {
        "schema": "binderscope.project/1", "name": spec.name, "created": utc_now(), "command": spec.command, "version": m["tool"]["version"],
        "spec": {"runs": [{"id": r.run_id, "path": str(r.path), "label": r.label, "settings": str(r.settings) if r.settings else None} for r in spec.runs],
                 "target_pdb": str(spec.target_pdb), "reference_cif": str(spec.reference_cif) if spec.reference_cif else None, "uniprot": spec.uniprot,
                 "reference_given": spec.reference_cif is not None, "reference_id": spec.reference_id, "reference_chain": spec.reference_chain,
                 "partner_chains": spec.partner_chains if spec.partner_chains is None or isinstance(spec.partner_chains, str) else list(spec.partner_chains),
                 "assembly": spec.assembly, "placement": spec.placement,
                 "profile": spec.profile if isinstance(spec.profile, str) else "custom", "consurf": spec.consurf},
        "bundle_id": m["bundle"]["id"], "counts": m["counts"], "validation": m["validation"],
        "timings": {**project.timings, "total": round(seconds, 1)}, "warnings": m["warnings"], "highlights": m["highlights"],
        "export": "export",
    }
    (out / "project.json").write_text(json.dumps(info, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def format_project_summary(project_dir: Path) -> str:
    info = json.loads((Path(project_dir) / "project.json").read_text(encoding="utf-8"))
    c, v = info["counts"], info["validation"]
    out = [f"Project {info['name']}  (bundle id {info['bundle_id']}, binder-review {info['version']})",
           f"  {c['accepted']} accepted designs in {c['families']} families, {c['rejected']} rejected designs with structures, {c['inspected']} inspected, {c['runs']} runs"]
    ok = v.get("counts_match_csv")
    out.append("  counts against the CSVs: " + ("all match" if ok else "MISMATCH" if ok is False else "not checked"))
    for mm in v.get("mismatches", [])[:10]:
        out.append(f"    - {mm}")
    out.append("  timings: " + ", ".join(f"{k} {t:.1f} s" for k, t in info["timings"].items()))
    if info["highlights"]:
        out.append("  what stands out:")
        out += [f"    [{h['severity']}] {h['text']}" for h in info["highlights"]]
    if info["warnings"]:
        out.append(f"  {len(info['warnings'])} warnings (see data/manifest.json)")
    return "\n".join(out)


def _partner_arg(text: str):
    """``--partner-chains``: None for auto, () for none, the word for all, else the chain names."""
    low = text.strip().lower()
    if low in ("", "auto"):
        return None
    if low == "none":
        return ()
    if low == "all":
        return "all"
    return tuple(c.strip() for c in text.split(",") if c.strip())


def _cmd_chains(args: argparse.Namespace) -> int:
    from . import structure as S
    from . import target_frame as TF

    construct = None
    if args.target_pdb:
        try:
            construct = TF._read_construct(args.target_pdb)
        except (TF.FrameError, S.StructureError) as ex:
            print(f"binder-review chains: {ex}", file=sys.stderr)
            return 2
    try:
        comp = TF.analyse_reference(args.reference, None, chain=None if args.reference_chain == "auto" else args.reference_chain,
                                    partner_chains=_partner_arg(args.partner_chains), assembly_id=args.assembly,
                                    construct_atoms=construct)
    except (TF.FrameError, S.StructureError) as ex:
        print(f"binder-review chains: {ex}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(comp.data, indent=1))
    else:
        print(f"{comp.data['file']}" + (f", {comp.data['resolution']:g} A" if comp.data.get("resolution") else ""))
        for line in TF.format_reference_chains(comp.data):
            print(line)
    return 2 if comp.problem else 0


def _cmd_app(args: argparse.Namespace) -> int:
    from .app import server

    return server.run(port=args.port, home=args.home, open_mode=args.window, quit_when_closed=args.quit_when_closed, verbose=args.verbose)


def _cmd_build_bundle(args: argparse.Namespace) -> int:
    from .bundle import run_build_bundle

    args.project = args.project_dir or args.project_opt or "work/project"
    return run_build_bundle(args)


def default_run_id(folder: Path) -> str:
    """Short run id from a folder name: campaign_run01 -> run01, otherwise the folder name."""
    m = re.search(r"(run\d+)", folder.name, re.IGNORECASE)
    return m.group(1).lower() if m else folder.name


def _cmd_inspect(args: argparse.Namespace) -> int:
    from .readers import available_readers, detect_reader

    folder = Path(args.run_dir)
    if not folder.is_dir():
        print(f"binder-review inspect: {folder} is not a folder", file=sys.stderr)
        return 1
    if (folder / "project.json").is_file():
        print(format_project_summary(folder))
        return 0
    if (folder / "data" / "manifest.json").is_file() and (folder / "index.html").is_file():
        from .bundle import bundle_summary

        info = bundle_summary(folder)
        if args.json:
            print(json.dumps(info, indent=1, ensure_ascii=False))
        else:
            print(format_bundle_summary(info))
        return 0
    reader = detect_reader(folder)
    if reader is None:
        print(f"binder-review inspect: {folder} is not a run folder of a known reader "
              f"({', '.join(sorted(available_readers()))}), a project folder or a bundle", file=sys.stderr)
        return 1
    run = reader.read_run(
        folder, args.run_id or default_run_id(folder.resolve()), settings_path=args.settings,
        construct_path=args.construct, check_structures=not args.no_structure_check, hash_structures=False,
    )
    if args.json:
        print(json.dumps(run.to_dict(), indent=1))
    else:
        print(format_run_summary(run))
    return 0


def format_bundle_summary(info: dict) -> str:
    c, v = info["counts"], info["validation"]
    out = [f"Bundle {info['name']}  (id {info['id']}, built {info['created']}, binder-review {info['tool']['version']})",
           f"  {c.get('accepted')} accepted designs in {c.get('families')} families, {c.get('rejected')} rejected with structures, "
           f"{c.get('inspected')} inspected, {c.get('runs')} runs",
           f"  {info['n_files']} files, {info['bytes'] / 1e6:.1f} MB; pages: {', '.join(info['pages'])}",
           "  counts against the CSVs: " + ("all match" if v.get("counts_match_csv") else "MISMATCH" if v.get("counts_match_csv") is False else "not checked")]
    out += [f"    - {m}" for m in v.get("mismatches", [])[:10]]
    if info["highlights"]:
        out.append("  what stands out:")
        out += [f"    [{h['severity']}] {h['text']}" for h in info["highlights"]]
    return "\n".join(out)


def format_run_summary(run: "RunData") -> str:
    """Readable summary of a RunData: counts, families, funnel, hotspots, warnings."""
    from collections import Counter

    from .model import SCALE_NOTE

    st, tg, fn = run.settings, run.target, run.funnel
    acc, rej = run.accepted(), run.rejected()
    out: list[str] = []
    w = out.append

    def count(name: str, n: Optional[int]) -> str:
        return f"{name:<46s}{'unknown' if n is None else n}"

    w(f"Run {run.run_id}  ({run.label})   reader: {run.reader}" + ("  [experimental]" if run.experimental else ""))
    w(f"  folder   {run.source_dir}")
    if run.tool_version:
        w(f"  tool     BindCraft {run.tool_version}")
    w("")
    w("Designs")
    per_bb = Counter(len(b.accepted_ids) for b in run.accepted_backbones())
    w(f"  accepted designs                            {len(acc)}")
    per = ", ".join(f"{n} with {k}" for k, n in sorted(per_bb.items()))
    w(f"  families (accepted backbones)               {len(run.accepted_backbones())}   (accepted sequences per family: {per})")
    with_struct = len(run.rejected_with_structure())
    w(f"  not-accepted sequences (kept in CSV)        {len(rej)}   ({with_struct} with a structure, {len(rej) - with_struct} metric-only)")
    if rej:
        fails = Counter(s for d in rej for s in d.failed_stems)
        top = ", ".join(f"{s} {n}" for s, n in fails.most_common(6))
        w(f"  filters failed by not-accepted sequences    {top}  (a sequence can fail several)")
    w("")
    w("Target as BindCraft saw it (construct numbering)")
    if tg.numbers:
        extra = f", deleted {', '.join(f'{a}-{b}' for a, b in tg.deleted_ranges)}" if tg.deleted_ranges else ", no deleted regions"
        w(f"  chain {tg.chain_id}: {tg.n_present} residues, numbers {tg.first}-{tg.last}{extra}")
        if tg.reference_path:
            w(f"  construct file {Path(tg.reference_path).name}: {tg.reference_length} residues, sequence "
              f"{'matches' if tg.sequence_matches_reference else 'DIFFERS'} at the shared numbers")
        w(f"  structures checked {tg.n_structures_checked}; identical residue set in all: {tg.consistent}")
    else:
        w("  (structures not checked)")
    hs = ", ".join(run.hotspot_labels()) or "none"
    w(f"  hotspots (construct numbers): {hs}   [{st.hotspot_source}]")
    w("")
    w("Settings")
    w(f"  protocol {st.protocol}, advanced settings {st.advanced_settings_name}, filters {st.filters_name}, target settings {st.target_settings_name}")
    w(f"  binder lengths {st.length_min}-{st.length_max}  [{st.length_source}]")
    w(f"  final designs target {st.final_designs_target if st.final_designs_target is not None else 'unknown'}  [{st.final_designs_target_source}]")
    w("")
    w("Funnel (observed = read from the files, derived = estimated, unknown = not recoverable)")
    for row in fn.stage_rows():
        val = "unknown" if row["value"] is None else f"{row['value']}"
        w(f"  {row['label']:<56s}{val:>8s}  [{row['status']}]")
    w("")
    avail = sorted(run.available_metrics())
    w(f"Metrics present on accepted designs: {len(avail)} of {len(run.caps())} BindCraft stems")
    missing = sorted(set(run.caps()) - set(avail))
    if missing:
        w(f"  absent: {', '.join(missing)}")
    w(f"  {SCALE_NOTE}")
    ann = run.annotations
    if ann.get("xlsx_highlight"):
        h = ann["xlsx_highlight"][0]
        w(f"xlsx: {len(h['designs'])} rows highlighted ({', '.join(h['colours'])}); meaning not recorded in the file")
    for nf in ann.get("note_files", []):
        w(f"note file {Path(nf['path']).name}: mentions {len(nf['ids'])} design or backbone ids")
    w("")
    w(f"Input files hashed: {len(run.files)}")
    if run.notes:
        w("Notes")
        out.extend(f"  - {n}" for n in run.notes)
    w("Warnings" if run.warnings else "Warnings: none")
    out.extend(f"  - {x}" for x in run.warnings)
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="binder-review",
        description="Binderscope: triage BindCraft binder designs from run folders into a static bundle that opens from disk.",
    )
    parser.add_argument("--version", action="version", version=f"binder-review {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="{app,import,chains,inspect,build-bundle}")

    from .app import DEFAULT_PORT

    p_app = sub.add_parser("app", help="open the Binderscope app: import runs and review them in a window, no command line needed",
                           description="Start the local Binderscope app. It listens on this computer only, opens in its own window (a Chromium-based browser) or "
                                       "in your browser, lets you choose run folders and structures, builds the review pages and shows them. Projects are kept in "
                                       "~/Binderscope (or $BINDERSCOPE_HOME). Press Ctrl+C to stop it.")
    p_app.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default {DEFAULT_PORT}; keep it fixed: the review pages save your decisions per address)")
    p_app.add_argument("--home", metavar="DIR", help="folder for the projects (default ~/Binderscope, or $BINDERSCOPE_HOME)")
    p_app.add_argument("--window", choices=("auto", "app", "browser", "none"), default="auto",
                       help="auto (default): an app window when Chrome, Edge, Brave or Chromium is installed, else your browser; none: do not open anything")
    p_app.add_argument("--quit-when-closed", action="store_true", help="stop the server about a minute after the last page was closed")
    p_app.add_argument("-v", "--verbose", action="store_true", help="log every request")
    p_app.set_defaults(func=_cmd_app)

    p_imp = sub.add_parser(
        "import", help="analyse run folders and write a project (and optionally the bundle)",
        description="Read BindCraft run folders and the target files, run the whole analysis (placement on the reference structure when one is given, "
                    "context checks, interface analysis, families, modes, specificity), validate the blobs against docs/schema and write "
                    "the project folder (OUT/export). With --bundle the static bundle is written as well: one command from run folders to a "
                    "viewer that opens from disk.")
    p_imp.add_argument("run_dirs", nargs="+", metavar="RUN_DIR", help="BindCraft run folders, as PATH or ID=PATH (the id defaults to run01 from a folder named like campaign_run01)")
    p_imp.add_argument("--target-pdb", help="the structure that was given to BindCraft (numbering 1 to N); required")
    p_imp.add_argument("--reference-cif", "--reference", dest="reference_cif",
                       help="full-length reference structure of the target (mmCIF or PDB). Optional: without it the construct is the frame, designs are "
                            "analysed as they are, and the checks that need the rest of the protein (other subunits, rest of the chain, cut ends, glycan "
                            "sites, membrane plane) are switched off and the pages say they were not run")
    p_imp.add_argument("--reference-id", metavar="ID", help="name of the reference structure in labels, e.g. a PDB id such as 1abc (default: the file name without its extension)")
    p_imp.add_argument("--reference-chain", default="auto", metavar="CHAIN",
                       help="chain of the target in the reference structure. auto (default): the chain that carries the construct's sequence; when "
                            "several carry it equally (a homodimer) the first in the file is taken and the log says so. "
                            "'binder-review chains' shows the choice before a build")
    p_imp.add_argument("--partner-chains", default="auto", metavar="CHAINS",
                       help="what the binder is checked against besides the target chain. auto (default): the other subunits, that is chains of the same "
                            "protein in the target's assembly, plus the copies the assembly's symmetry operators make when the file holds only part of "
                            "it; none; all (every other polymer chain of the assembly); or chain names, comma separated. Another molecule (an antibody, a "
                            "ligand protein) is never counted unless it is named or all is given; ligands, ions, additives and waters never are")
    p_imp.add_argument("--assembly", metavar="ID", help="the assembly of the reference to take the subunits from (default: the first one that holds the target chain)")
    p_imp.add_argument("--context-check", choices=("auto", "hard", "advisory"), default=None,
                       help="whether the Context check (other subunits, rest of the protein, cut ends) can by itself turn a verdict to NO PASS. auto (default): "
                            "only when the reference is an experimental structure, judged from the file's own method record; a predicted reference, or one "
                            "whose file does not say, gives an advisory check that is shown with its reasons but does not decide. Overrides profile checks.context.hard")
    p_imp.add_argument("--placement", choices=("interface", "whole", "core"), default="interface",
                       help="how each design is placed on the reference chain: interface (default: the superposition of the target is weighted around the "
                            "binder, so the epitope sits where it does on the reference even when other parts of the target moved), whole (every shared "
                            "residue counts the same) or core (only the residues a trimmed fit keeps)")
    p_imp.add_argument("--name", help="project name (default: the name of the output folder)")
    p_imp.add_argument("--out", help="project folder for the analysis, its caches and the export (default work/project)")
    p_imp.add_argument("--project", dest="project", help=argparse.SUPPRESS)
    p_imp.add_argument("--settings", action="append", metavar="ID=FILE", help="settings JSON of a run that is not inside its folder")
    p_imp.add_argument("--label", action="append", metavar="ID=TEXT", help="display label of a run (default: the folder name)")
    p_imp.add_argument("--uniprot", metavar="ACCESSION", help="UniProt accession of the target (optional; default: target.uniprot of the profile). Without one no UniProt, "
                       "PDBe or AlphaFold record is read: no domain strip, topology, membrane plane or specificity panel")
    p_imp.add_argument("--profile", default="default", help="built-in profile id (the only one is default: a neutral profile that names no target) or the path of a profile JSON (default: default)")
    p_imp.add_argument("--consurf", action="store_true", help="include the optional ConSurf-DB overlay (academic use, off by default)")
    p_imp.add_argument("--online", action="store_true", help="allow fetching missing public reference data by accession (default: cache only)")
    p_imp.add_argument("--jobs", type=int, help="worker processes (default: CPU count minus one, at most 8)")
    p_imp.add_argument("--no-cache", action="store_true", help="recompute every stage (the caches under the project folder are dropped and rewritten)")
    p_imp.add_argument("--quiet", action="store_true", help="no progress lines")
    p_imp.add_argument("--bundle", metavar="BUNDLE_DIR", help="also write the static bundle here (combined mode)")
    p_imp.add_argument("--fonts", choices=("google", "system"), default="system", help="bundle fonts: system (default: the bundle never contacts a server) or google (the optional Google Fonts link, with a system fallback)")
    p_imp.add_argument("--csp", action="store_true", help="bundle: add a Content-Security-Policy meta tag to every page")
    p_imp.add_argument("--no-guide", dest="no_guide", action="store_true", help="bundle: leave the guide out")
    p_imp.add_argument("--no-tutorial", dest="no_guide", action="store_true", help=argparse.SUPPRESS)   # the flag before the page was renamed Guide; kept so existing scripts still work
    p_imp.add_argument("--no-structure-scripts", action="store_true", help="bundle: write structures/<id>.pdb only, not the lazy-loadable .js")
    p_imp.add_argument("--embed-structures", action="store_true", help="bundle: keep the binder structures inside data/inspect.js (larger) instead of loading structures/<id>.js on demand")
    p_imp.set_defaults(func=_cmd_import)

    p_ch = sub.add_parser(
        "chains", help="show which chain of a reference is the target, which chains are partners, and why",
        description="Read a reference structure and print what 'import' would do with each of its chains: which one is the target (found by "
                    "the construct's sequence unless named), which are subunits of the same protein in its assembly (counted as partners), which are "
                    "other molecules or neighbours in the crystal (not counted unless named), the copies the assembly's symmetry operators would add, "
                    "and the ligands, ions and waters that are never counted. Nothing is written.")
    p_ch.add_argument("reference", metavar="REFERENCE", help="the reference structure (mmCIF or PDB)")
    p_ch.add_argument("--target-pdb", metavar="FILE", help="the construct BindCraft was given; with it the target chain is found by sequence")
    p_ch.add_argument("--reference-chain", default="auto", metavar="CHAIN", help="the chain of the target (default auto: by the construct's sequence)")
    p_ch.add_argument("--partner-chains", default="auto", metavar="CHAINS", help="auto (default), none, all or chain names, as for 'import'")
    p_ch.add_argument("--assembly", metavar="ID", help="the assembly to take the subunits from (default: the first one that holds the target chain)")
    p_ch.add_argument("--json", action="store_true", help="print the whole answer as JSON")
    p_ch.set_defaults(func=_cmd_chains)

    p_ins = sub.add_parser("inspect", help="print a summary of a run folder, a project folder or a bundle")
    p_ins.add_argument("run_dir", metavar="PATH", help="a BindCraft run folder, a project folder written by 'import' or a bundle folder")
    p_ins.add_argument("--run-id", help="short id for the run (default: derived from the folder name)")
    p_ins.add_argument("--settings", help="settings JSON when it is not inside the run folder")
    p_ins.add_argument("--construct", help="PDB that was given to BindCraft (compares residue numbers and sequence)")
    p_ins.add_argument("--no-structure-check", action="store_true", help="skip opening the PDB files")
    p_ins.add_argument("--json", action="store_true", help="print the full RunData as JSON (binder sequences omitted)")
    p_ins.set_defaults(func=_cmd_inspect)

    p_bb = sub.add_parser("build-bundle", help="write the static review bundle from a project folder",
                          description="Write the bundle (pages, data, structures, 3Dmol.js) from the export folder of a project written by 'import'. "
                                      "Fast: run it again whenever the viewer pages change.")
    p_bb.add_argument("project_dir", nargs="?", metavar="PROJECT_DIR", help="project folder written by 'import' (default work/project)")
    p_bb.add_argument("--project", dest="project_opt", help=argparse.SUPPRESS)
    p_bb.add_argument("--out", default="bundles/out", help="output bundle folder (default bundles/out)")
    p_bb.add_argument("--name", help="bundle name recorded in the manifest (default: the name of the output folder)")
    p_bb.add_argument("--fonts", choices=("google", "system"), default="system", help="system (default: no network reference at all) or google (the optional Google Fonts link, with a system fallback)")
    p_bb.add_argument("--csp", action="store_true", help="add a Content-Security-Policy meta tag to every page")
    p_bb.add_argument("--no-guide", dest="no_guide", action="store_true", help="leave the guide out")
    p_bb.add_argument("--no-tutorial", dest="no_guide", action="store_true", help=argparse.SUPPRESS)   # the flag before the page was renamed Guide; kept so existing scripts still work
    p_bb.add_argument("--no-structure-scripts", action="store_true", help="write structures/<id>.pdb only, not the lazy-loadable .js")
    p_bb.add_argument("--embed-structures", action="store_true", help="keep the binder structures inside data/inspect.js (larger) instead of loading structures/<id>.js on demand")
    p_bb.add_argument("--keep", action="store_true", help="keep the existing files of the output folder instead of cleaning it")
    p_bb.add_argument("--no-validate", action="store_true", help="skip the bundle validator")
    p_bb.set_defaults(func=_cmd_build_bundle)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
