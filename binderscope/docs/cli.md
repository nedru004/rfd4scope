# Command line

`binder-review` and its alias `binderscope` (the same program, installed by `pip install -e .`; `python -m binder_review` works too) have five commands: `app`, `import`, `chains`, `build-bundle` and `inspect`. `app` starts the local Binderscope app, which is the way most people use Binderscope ([app.md](app.md)); `import`, `chains`, `build-bundle` and `inspect` are what the app runs for you and what you use in scripts. [export.md](export.md) explains what they do; this page is the reference.

```
binder-review app [--port N] [--home DIR] [--window auto|app|browser|none] [--quit-when-closed] [-v]
binder-review import RUN_DIR [RUN_DIR ...] --target-pdb FILE [--reference-cif FILE] --name NAME --out PROJECT_DIR [--bundle BUNDLE_DIR]
binder-review chains REFERENCE [--target-pdb FILE]     # which chain is the target and which are partners, and why
binder-review build-bundle PROJECT_DIR --out BUNDLE_DIR
binder-review inspect PATH          # a run folder, a project folder or a bundle
binder-review --version
```

## One command from run folders to the viewer

```
binder-review import run01=/path/to/run01 run02=/path/to/run02 \
    --target-pdb construct.pdb --reference-cif full_length.cif \
    --name my-target --out work/my-target --bundle bundles/my-target
open bundles/my-target/index.html          # works from disk, offline, no server
```

`--reference-cif` is optional. Without it nothing is looked up outside the construct and the checks that need the rest of the protein do not run; see [Without a reference structure](#without-a-reference-structure) below. The summary then says so under *what stands out*.

It prints progress lines with elapsed seconds, then a summary: counts, whether they match the CSVs (`all match`, `MISMATCH`, or `not checked` when no run is in the BindCraft 1 layout, because only those can be compared), the timings and what stands out in the data.

## app

`binder-review app` starts the local Binderscope app: a small web server on `127.0.0.1` with an import page, a project list and the review pages, and a window that shows them. It is what `Binderscope.app` (made by the installer) runs. [app.md](app.md) describes the app, its import page, where it keeps files, its privacy and security model and its local API.

| Argument | Meaning |
|---|---|
| `--port N` | the port on `127.0.0.1` (default 51731). Keep it fixed: the review pages keep your decisions per address. If the port is taken the app tries the next ones (up to 24 above) and says so; if another Binderscope app already answers on the port it just shows a window of that one |
| `--home DIR` | the folder for the projects (default `~/Binderscope`, or `$BINDERSCOPE_HOME`) |
| `--window auto\|app\|browser\|none` | `auto` (the default) opens an app window of Chrome, Edge, Brave or Chromium when one is installed and your default browser otherwise; `app` is the same; `browser` always uses your default browser; `none` opens nothing |
| `--quit-when-closed` | stop the server about a minute (75 seconds) after the last request, when no build is running; every open page checks in every 20 seconds |
| `-v`, `--verbose` | log every request to the terminal |

It prints the address and the projects folder and runs until you press Ctrl+C (or press Quit in the app). Exit codes: 0 on a normal stop and when the app is already running; 1 when no free port is found from `--port` to `--port` + 24.

Each project the app builds is `binder-review import` with the choices of the import page, run as a separate process: the runs as `ID=PATH`, `--target-pdb`, `--reference-cif`, `--reference-id`, `--reference-chain`, `--partner-chains` (the three reference options only when a reference was chosen; the app's import page still asks for one), `--settings` and `--label` per run when given, `--uniprot`, `--profile`, `--online` and `--consurf` when chosen, `--placement` when it is not `interface`, `--name`, `--out <project>/work` and `--bundle <project>/bundle`. A rebuild runs it again (with `--no-cache` for a rebuild from scratch); **Update the review pages** runs `build-bundle <project>/work --out <project>/bundle`.

## import

Reads the run folders, runs the whole analysis, validates the blobs against [docs/schema](schema/README.md), writes the project folder and optionally the bundle. A violation of the schemas stops the build with exit code 1 and lists the errors.

| Argument | Meaning |
|---|---|
| `RUN_DIR` (one or more) | a BindCraft run folder as `PATH` or `ID=PATH`; without an id the short id comes from the folder name (`250101_run01` becomes `run01`; a folder name without `runNN` is used as it is). The reader is detected from the layout (BindCraft 1; BindCraft 2 is experimental). Ids must be unique. A run that records no hotspot at all is built too, as a run without an intended epitope ([export.md](export.md), section 10) |
| `--target-pdb FILE` | the structure that was given to BindCraft (numbering 1 to N); required; checked against every design and against the reference by sequence |
| `--reference-cif FILE` | the full-length reference structure, in mmCIF or PDB format (alias `--reference`); optional, see [Without a reference structure](#without-a-reference-structure) (a PDB-format file carries its assemblies in `REMARK 350`, which are used the same way; only the assembly summary of the build record stays empty); designs are placed on its target chain, and the other subunits, the rest of the chain, the glycans and the cut ends come from it. The chains are chosen by sequence and by assembly, and the log says how: see `--reference-chain`, `--partner-chains` and [chains](#chains) |
| `--reference-id ID` | (with a reference only) the name of the reference structure in labels and in the manifest, for example a PDB id such as `1abc` (default: the file name without its extension) |
| `--reference-chain CHAIN` | (with a reference only) the chain of the target in the reference structure. `auto` (the default) takes the chain that carries the construct's sequence; when several carry it equally, as in a homodimer, the first in the file is taken and the build log says so, with the reason (including whether the construct's coordinates follow another more closely). A named chain is taken as named and must hold at least 90 percent of the construct |
| `--partner-chains CHAINS` | (with a reference only; `none` and `auto` are accepted without one) what the binder is checked against besides the target chain. `auto` (the default): the other subunits, that is chains of the same protein in the target's assembly, plus the copies the assembly's symmetry operators make when the file holds only part of it (the usual case for an entry that holds one subunit of a crystallographic oligomer). `none`: nothing. `all`: every other polymer chain of the assembly. Or chain names, comma separated (the names of generated copies, such as `A-2`, are listed by `chains`). Another molecule, such as an antibody or a ligand protein, is never counted unless it is named or `all` is given, because the file cannot say whether it belongs to the complex; ligands, ions, additives and waters never are. See [target_frame.md](target_frame.md), Chains of the reference |
| `--assembly ID` | (with a reference only) the assembly of the reference the subunits are taken from (default: the first one that holds the target chain) |
| `--placement interface\|whole\|core` | how each design is placed on the reference chain (default `interface`). BindCraft predicts its own copy of the target, which differs a little from the reference, so a fit over the whole target can leave the binder 1 to 3 Å off at the epitope. `interface` weights the fit around the binder (residues near it count most and outliers near it are left out), so the epitope sits where it does on the reference even when other parts of the target moved; `whole` counts every shared residue the same; `core` fits only the residues a trimmed fit keeps. A design with fewer than 12 target residues near the binder is placed by the whole fit under `interface`, and a note on its run says so. Contacts, hydrogen bonds and the other interface numbers do not depend on it; the distances to the other subunit, the rest of the protein and the sugars, and the Context verdict, do. See [target_frame.md](target_frame.md), Placement. Changing it for an existing project folder (`--out`) recomputes the target frame and the placements; the interface analysis and the families come from the cache |
| `--context-check auto\|hard\|advisory` | whether the Context check (other subunit, rest of the protein, cut ends) can by itself turn a design to NO PASS. `auto` (the default): only when the reference is an experimental structure, judged from the file's own method record; a predicted reference, or one whose file does not say, gives an advisory check that is shown with its reasons and can lead to REVIEW but not to NO PASS on its own. `hard` and `advisory` force it. Overrides `checks.context.hard` of the profile ([profile_spec.md](profile_spec.md)). See [target_frame.md](target_frame.md), Context check standing |
| `--name NAME` | project name (default: the name of `--out`) |
| `--out DIR` | project folder: caches, `export/`, `project.json` and, for a BindCraft 2 campaign whose binder numbers start at 0, `onebased/` with one-based copies of its structure files (default `work/project`) |
| `--settings ID=FILE` | the settings JSON of a run when it is not inside its folder (repeatable) |
| `--label ID=TEXT` | display label of a run (default: the folder name; repeatable) |
| `--uniprot ACC` | UniProt accession of the target (optional; default: `target.uniprot` of the profile). It is used to look up the sequence and annotation. Without one, no UniProt, PDBe or AlphaFold record is read: there is no domain strip, topology, membrane plane or specificity panel |
| `--profile ID\|FILE` | the profile: the built-in id `default` (the default) or the path of a profile JSON file. The exporter completes it from the data (capability flags, construct, reference). See [profile_spec.md](profile_spec.md) |
| `--consurf` | include the optional ConSurf-DB overlay (academic, non-commercial licence; off by default) |
| `--online` | allow fetching missing public reference data by accession (UniProt, SIFTS, AlphaFold DB); the default reads the cache only and never touches the network |
| `--jobs N` | worker processes (default CPU count minus one, at most 8) |
| `--no-cache` | recompute every stage; the caches are dropped and rewritten |
| `--quiet` | no progress lines |
| `--bundle DIR` | also write the bundle here (the combined mode) |
| `--fonts system\|google` | bundle: system fonts only, no network reference (default), or the optional Google Fonts link with a system fallback (an opened page then contacts fonts.googleapis.com) |
| `--csp` | bundle: a Content-Security-Policy meta tag on every page |
| `--no-guide` | bundle: leave the guide page out. `--no-tutorial`, the name before the page was called Guide, still works as a hidden alias and is left out of `--help` |
| `--no-structure-scripts` | bundle: write `structures/<id>.pdb` only, not the lazy-loadable `.js` (the binder structures then stay inside `data/inspect.js`, and the Inspector draws the reference chain as the target instead of each design's own copy of it) |
| `--embed-structures` | bundle: keep the binder structures inside `data/inspect.js` (larger) instead of loading `structures/<id>.js` when the inspector opens a design (the default). The Inspector then draws the reference chain as the target, not each design's own copy of it, because that copy comes with the structure file |

Only accessions, PDB ids, gene symbols and taxon ids ever reach the reference data layer, and with the default offline mode nothing leaves the machine. No binder sequence and no design id is ever sent anywhere. `manifest.privacy` records it.

Exit codes: 0 ok; 1 an error (an input not found, a structure file that cannot be read, a schema violation, a bundle error); 2 a usage error or inputs that do not fit together (a missing required option, a reference option (`--reference-id`, `--reference-chain`, `--partner-chains`, `--assembly`) without `--reference-cif`, a repeated run id, `--settings` or `--label` for a run that was not given, an output folder inside a run folder, a construct that does not match the reference chain, hotspots that are not in the construct, a reference chain that is not a polymer). Every error is one line on stderr, without a traceback.

### Without a reference structure

Leave out `--reference-cif` when you have no full-length structure or do not want the review it brings. The construct (the file given to `--target-pdb`) is then the frame: designs are superposed on it so that they all share one frame, numbering stays the construct's, and nothing outside it is known. These checks are **not run**, and the build says so in four places (the Overview's *What this bundle carries* panel, its *what stands out* list, the manifest and the run record):

- the other subunits of the assembly, the rest of the protein and the cut ends of the construct, the glycan sites and the membrane plane. Their values are absent in the data, not zero, so no page can show a design as clear of them.

Everything that is computed from the designs' own structures is the same as in a build with a reference: interface analysis, families, binding modes, the paratope analysis of a scaffolded campaign, near-miss rescue and specificity (which needs `--uniprot`, and then needs the construct's numbering to equal UniProt numbering). The design-level numbers of two builds of one campaign, with and without a reference, are identical; only the context checks differ. A stretch that a run deleted inside the construct is still recorded (the run block's `removed`), but it is not "the rest of the protein": the construct file holds it, and the check is not run for it.

`--reference-id`, `--reference-chain` (other than `auto`), `--partner-chains` (other than `auto` and `none`) and `--assembly` describe the reference structure, so they are refused without one rather than ignored.

This is not the same as a reference that holds nothing outside the construct (for example the construct given again as the reference). That build runs the checks on nothing, and `profile.caps_off` records the reason as `reference_lacks` instead of `no_reference`. See [target_frame.md](target_frame.md), Without a reference.

## chains

`binder-review chains REFERENCE [--target-pdb FILE] [--reference-chain CHAIN] [--partner-chains CHAINS] [--assembly ID] [--json]` reads a reference structure and prints what `import` would do with each of its chains, without building anything: which chain is the target and why, which chains are partners and why, the copies the assembly's symmetry operators would add, the chains that are not counted (another molecule, a neighbour in the crystal, with the hint a name in the file gives) and the ligands, ions and waters that never are. With `--target-pdb` the target is found by the construct's sequence; without it a file with one protein chain gives that chain and a file with several needs `--reference-chain`. `--json` prints the whole answer (`frame.meta["composition"]` is the same object). Exit code 0, or 2 when no target could be found (the construct is in no chain, or split over two) or the options do not fit the file. The build log of `import` prints the same lines, prefixed `reference chains:`.

## build-bundle

`binder-review build-bundle PROJECT_DIR --out BUNDLE_DIR` writes the bundle from `PROJECT_DIR/export` (the folder `import` wrote). It does no analysis, so it takes seconds and can be repeated whenever the viewer pages change. The pages come from `viewer/pages` through `viewer_build.render_site`; `viewer/pages/guide.html` is included when it exists.

| Argument | Meaning |
|---|---|
| `PROJECT_DIR` | a project folder (or its `export/` folder); default `work/project` |
| `--out DIR` | the bundle folder (default `bundles/out`); it is cleaned first unless `--keep` |
| `--name NAME` | bundle name recorded in the manifest (default: the name of `--out`) |
| `--fonts`, `--csp`, `--no-guide`, `--no-structure-scripts`, `--embed-structures` | as for `import` |
| `--keep` | keep the existing files of the output folder instead of cleaning it |
| `--no-validate` | skip the bundle validator (file hashes, links, review files) |

The layout of the bundle is in [export.md](export.md). The validator runs at the end and fails the build on an error (exit 1).

## inspect

`binder-review inspect PATH` prints a summary and chooses what to show from the folder:

- a **run folder** (BindCraft 1 or 2): accepted designs and families, not-accepted sequences, the target as BindCraft saw it, settings, the funnel with observed, derived and unknown stages, the metrics present, warnings. Options `--run-id ID`, `--settings FILE`, `--construct FILE` (the PDB given to BindCraft: compares residue numbers and sequence), `--no-structure-check`, `--json` (the full run record, binder sequences omitted);
- a **project folder** (it has `project.json`): counts, whether they match the CSVs (as above), timings and what stands out;
- a **bundle** (`data/manifest.json` and `index.html`): id, build date, counts, file count and size, validation and highlights (`--json` for the manifest facts).

Use it on a run folder before the first build: the warnings say what the reader could not find.

## Where the files go

```
work/my-target/          project: runs/, target/, poses/, analysis/, stages/, export/, project.json
work/my-target/export/   campaign.json inspect.json homolog.json profile.json manifest.json structures/*.pdb
bundles/my-target/       the bundle: open index.html
```

Nothing is ever written into the BindCraft run folders. The only other place a build writes is the reference cache (`data/reference` next to the source, or the folder named by `BINDERSCOPE_REFDATA_DIR`), and only when `--online` fetches a missing record.

## Environment variables

| Variable | Meaning |
|---|---|
| `BINDERSCOPE_OFFLINE=1` | a reference cache miss becomes a clear error instead of a request (a build without `--online` already behaves this way) |
| `BINDERSCOPE_REFDATA_DIR` | folder of the reference cache (default `data/reference` next to the source) |
| `BINDERSCOPE_HOME` | the projects folder of the app (default `~/Binderscope`); `app --home` wins. See [app.md](app.md) for the variables that only the app reads |
| `BINDERSCOPE_VIEWER` | folder with the viewer sources (`pages/` and `js/bs.js`), instead of the `viewer/` folder of the checkout |

## Safety

- Input folders are read-only: `import` refuses an `--out` or `--bundle` that is, or lies inside, one of the run folders (exit 2).
- A bundle folder is cleaned before it is written, so `build-bundle` and `import --bundle` refuse an existing, non-empty `--out` that is not an earlier Binderscope bundle (it has `index.html` and `data/manifest.json`): a mistyped path cannot delete anything else. `--keep` writes into such a folder without cleaning it.
- `--no-cache` deletes only cache files (`*.json` under `analysis/`, `poses/`, `stages/` and `target/frame.key`) of the project folder, never whole folders.

## Troubleshooting

- *A reference record is missing and the build says it is offline*: run once with `--online`, or run `scripts/fetch_reference.py`; the fetched records are cached by accession. See [reference_data.md](reference_data.md).
- *The schema validator names a field*: the message is `<json path>: <what is wrong>`; [data_contract.md](data_contract.md) says which rule comes from which field. The build never writes a bundle from invalid blobs.
- *The first run is slow*: the interface analysis and the families stage dominate; they are cached per design and per run, so the second run takes seconds.
- *A page logs an error in the browser console*: open the browser's developer console on that page and report the message with the output of `binder-review inspect` on the bundle.

## See also

[app.md](app.md) · [export.md](export.md) · [profile_spec.md](profile_spec.md) · [reference_data.md](reference_data.md) · [GUIDE.md](GUIDE.md)
