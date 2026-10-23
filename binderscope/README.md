# Binderscope

Binderscope helps you decide which designed protein binders to take into the lab. It reads BindCraft run folders, groups the designs by backbone family, checks each design on the full-length target, keeps your keep, maybe and reject decisions with reasons, and exports an order list. You use it through a small app on your own computer: choose your runs and the target, press Build, and review the results in a window. The review pages it writes are static: a folder of web pages that also opens straight from disk in a browser. There is no account, and nothing is uploaded.

> **Beta.** The review has been run on real BindCraft 2 campaigns of four kinds: **de novo binders, VHH nanobodies, scFvs and linear peptides**. It has not yet reviewed a real campaign of the other kinds BindCraft 2 designs: **Fab, ankyrin repeats, cyclic peptides, homo-oligomers, multidomain and large binders**. Those are read on the same assumptions and labelled *untested* in the review, as is any BindCraft 2 version other than v1.0.3. Every count is still compared with the campaign's own tables, which catches a table read wrongly but not a column that means something different for that kind of binder, so check their numbers yourself. BindCraft 1 run folders are covered by the test suite, on synthetic runs.

**You do not need BindCraft installed, or a GPU.** Binderscope reads finished
run folders — your own, or ones a colleague sends you — and never runs a
design itself. Python 3.12 or later is all it asks for, and the installer makes
its own environment.

Scores are screens, not verdicts. Counts are of backbone families, never of sequences. A design that passes the checks is a predicted binder, not a measured one.

The Python package is `binder_review`; the console commands `binder-review` and `binderscope` are the same program.

## What it does

- Reads BindCraft 1 run folders (the CSVs, the accepted and rejected structures, the settings file) and BindCraft 2 campaign folders. The BindCraft 2 reader has been checked number by number against a de novo (`binder`) campaign of v1.0.3; any other campaign is read on the same assumptions and is marked experimental, with the reason named.
- Groups designs into backbone families and design clusters, and estimates per run how much of the diversity you have seen and whether more designs would pay off.
- Places every design on the full-length target using a reference structure you supply, and checks what the construct hid from BindCraft: clashes with other subunits and with the rest of the chain, cut ends, buried epitopes, glycans.
- Recomputes from the structures, without PyRosetta, the contacts, buried area, hydrogen bonds, buried unsatisfied polar atoms, hydrophobic patches and sequence liabilities, so every design is judged on the same footing. BindCraft's own scores (confidence, shape complementarity, ΔG) are carried over from its CSVs.
- Finds binding modes from the contact footprints and shows where on the target your designs land, and whether you cover one site many times or several sites once.
- Screens specificity: identity at the contacted residues in orthologs (cross-species reactivity) and paralogs (off-target risk), when you give it species and paralog data.
- Judges, for a campaign built on a fixed scaffold (VHH, scFv, Fab, ankyrin repeat), what the designs actually bind with: which designed segments and which framework positions touch the target, from the campaign's own edit spec rather than any antibody numbering.
- Says which end of each design can carry a tag or a fusion (His6, Twin-Strep, a GFP-sized domain, an Fc), with the evidence behind the answer.
- Gives you four review pages (family board, design inspector, binding-mode map, structure query) and an overview, with decisions, reasons, order list, CSV and FASTA export, and review files you can share.

## Install (macOS)

1. Download this repository as a ZIP file and unzip it (or clone it).
2. Double-click `Install Binderscope.command` in the folder. A Terminal window opens and shows the progress.
3. Wait a few minutes. At the end Binderscope opens.

The installer checks that Apple's Command Line Tools are present (it asks you to install them once if they are not), finds or installs Python 3.12 or newer, copies the program to `~/Library/Application Support/Binderscope` (on Linux, `~/.local/share/binderscope`), sets up a private Python environment there with the libraries from PyPI, and creates the app `~/Applications/Binderscope.app`. It needs no administrator rights and writes nowhere else. Installing again updates; `./install.sh --uninstall` removes the app and keeps your projects. Details and troubleshooting (including what to do if macOS refuses to open the installer file) are in [docs/app.md](docs/app.md).

**Linux.** `./install.sh` runs there too. It makes no app — there is nothing to
put in Launchpad — but it sets up the same private environment and gives you
the `binderscope` command, which is the same program as `binder-review`. It
needs Python 3.12 or newer, which Rocky, RHEL and Fedora install with
`sudo dnf install python3.12` and Debian and Ubuntu with
`sudo apt install python3.12`. Where nobody has root:

```bash
pip install --user uv && uv python install 3.12
./install.sh --python "$(uv python find 3.12)"
```

Checked on Rocky Linux 9.8 with no BindCraft and no root: installed, built a
review of a BindCraft 2 campaign and opened its pages. If you would rather make
the environment yourself, or you are scripting, see
[Command line](#command-line-advanced-and-scripting) below.

## Open the app

Open Binderscope from Spotlight (press Cmd+Space, type Binderscope), Launchpad or your `Applications` folder. It opens in its own window (an app window of Chrome, Edge, Brave or Chromium when one is installed, otherwise your default browser) and lists your projects. They are kept in `~/Binderscope`. The launcher has no Dock icon of its own, and the app quits about a minute after you close its window; your projects stay.

## Import and review

1. **New project.** The page has three cards. *BindCraft runs*: choose the run folders (a folder dialog, several at once, or paste a path); each run shows what was found. *The target*: the structure you gave BindCraft (the page suggests files found next to your run) and the full-length reference structure, as a file, by dropping it, or by a PDB id that it downloads from the RCSB when you press Download; the page then says how well your structure fits the chain of the reference. *Options*: project name, UniProt accession (with an opt-in online lookup), the chain of the target in the reference, the other subunits, how designs are placed on the full-length protein (the default fits around each design's interface), a profile file.
2. **Build project.** The page shows the nine steps of the analysis live. The first build takes a minute or two for a typical project; a rebuild with unchanged inputs takes seconds.
3. **Review.** Open the project. The Overview comes first, then the family board: walk the tiles with the arrow keys and press `K` to keep, `M` for maybe, `X` to reject. A **‹ Projects** link in each page's header leads back to your projects. Decisions are saved in the browser as you go, and the app keeps a copy in the project folder.

Your run folders are read where they are and never written to. In each project's menu you find **Edit the inputs…**, **Update the review pages**, **Rebuild**, **Show in Finder** and **Delete project**. A project built before an update that changed the analysis itself (for example the placement of designs) says "Built with an older analysis" and offers **Rebuild**. The whole walk-through, with a 15-minute review recipe, is in [docs/GUIDE.md](docs/GUIDE.md); the same guide is the Guide page inside every project.

## Command line (advanced and scripting)

The command line does the same build and is the route for scripts, for Linux and for people who prefer their own Python environment. You need Python 3.12 or newer.

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The `dev` extra adds pytest and can be left out unless you run the tests. The build validates everything it writes against the JSON Schemas; jsonschema, which does that, is installed with the package. The optional `antibody` extra adds support for nanobody and scFv profiles (`pip install -e ".[antibody]"`).

You need one or more BindCraft run folders and the structure you gave BindCraft as the target (the construct). A full-length reference structure of the target (mmCIF or PDB format: an experimental entry or a predicted model) is optional but recommended: it supplies the other subunits, the rest of the chain, the cut ends, glycans and membrane for the context checks. Without it those checks are not run, the pages say so, and everything computed from the designs themselves is unchanged.

```
binder-review import run01=/path/to/run01 run02=/path/to/run02 \
  --target-pdb construct.pdb --reference-cif full_length.cif \
  --name my-target --out work/my-target --bundle bundles/my-target

open bundles/my-target/index.html      # macOS; use xdg-open on Linux or double-click the file
```

`binder-review app` starts the app from a terminal. Decisions made in a page opened from disk are saved in the browser; "Save review file" writes them to a small file. Keep a copy outside the bundle folder: writing a bundle into a folder that already holds one replaces its contents, `reviews/` included.

The first build takes minutes and caches every stage; a second build with the same inputs takes seconds. Input folders are never written to.

## The commands

```
binder-review app [--port N] [--home DIR]
binder-review import RUN_DIR [RUN_DIR ...] --target-pdb FILE [--reference-cif FILE] --name NAME --out PROJECT_DIR [--bundle BUNDLE_DIR]
binder-review build-bundle PROJECT_DIR --out BUNDLE_DIR
binder-review chains REFERENCE [--target-pdb FILE]
binder-review inspect PATH
```

- `app` starts the local app described above (the installer's `Binderscope.app` runs it for you).
- `import` reads the run folders (`ID=PATH` or just `PATH`), runs the whole analysis (target frame, interface, families, binding modes, specificity), validates the data against the schemas in `docs/schema` and writes a project folder and, with `--bundle`, the bundle. A schema violation stops the build.
- `build-bundle` rewrites the bundle from an imported project without any analysis (seconds). Use it after the viewer pages change.
- `chains` reads a reference structure and prints what `import` would do with each of its chains: which one is the target, which are partners, and why. Run it before an import when the reference holds several chains.
- `inspect` prints a summary of a run folder (counts, families, funnel, hotspots, warnings), of a project folder or of a bundle.

Every option, the exit codes and the safety rules are in [docs/cli.md](docs/cli.md).

## What is in a bundle

The review pages of a project are a bundle: in the app it is the `bundle` folder of the project (`~/Binderscope/<project>/bundle`), on the command line the folder you name with `--bundle`.

```
bundles/my-target/
  index.html  board.html  inspector.html  map.html  query.html  guide.html
  data/        campaign, inspect, homolog, profile, manifest: .json for tools, .js for the pages (file:// cannot fetch)
  structures/  <design id>.pdb and .js for each exported design, placed on the reference chain
  assets/      shared css and js, vendored 3Dmol.js and its licence
  reviews/     a place for saved review files (the pages do not read it; a rebuild into the same folder deletes it)
  README.txt
```

`data/manifest.json` records the tool version, input file hashes, run settings, thresholds, the checks made at build time and what stands out in the data. The layout is described in [docs/export.md](docs/export.md).

## The pages

**Overview (`index.html`)** lists the runs with their counts and funnel, a "What stands out" card drawn from your data (each item links to the page or design it is about), what the bundle carries, which checks are switched off for lack of data, a summary of your decisions so far and the build record.

**Family board (`board.html`)** is the home screen. One tile per backbone family, with the family's best design on the target, its hotspots, a PASS, REVIEW or NO PASS verdict with the reasons in words, and BindCraft's own scores. Per run it shows the funnel, the diversity of the designs and an estimate of whether you need more designs. You keep, maybe or reject families from the keyboard, tag a reason, let a small model learn from your calls to rank what to look at next, and build the order list with CSV and FASTA export.

**Design inspector (`inspector.html`)** shows one design in depth: a searchable list of designs, BindCraft's own scores against its default filters, findings linked to the 3D view, a strip of the full-length target (construct, cut ends, glycans, hotspots, other subunits), target-check cards that say what would confirm or refute each result, the interface and confidence tabs, per-residue tracks, the sequences on the same backbone, and the specificity tab.

**Binding-mode map (`map.html`)** shows where on the target the designs bind: an epitope map with each run's hotspots, binding modes found from contact footprints (named from the residues they use) with hotspot coverage and closest approach to other subunits, a heat map by mode, a table, and quality-diversity picks that are good and different from each other.

**Structure query (`query.html`)** is a rule stack with a funnel: BindCraft's own filters as editable rules, site, context and specificity rules, plain-words rules typed into the Ask box, a pool switch that adds BindCraft's rejected designs, near-miss rescue, and results you can send to the board, export as CSV or FASTA or save as a rule-set file.

**Guide (`guide.html`)** is the guide in this repository's `docs/GUIDE.md`, readable offline from the bundle.

## Requirements

- For the app and its installer: macOS 11 or newer, an internet connection during the installation (the libraries come from PyPI) and Apple's Command Line Tools, which the installer asks for. The installer finds or installs Python 3.12 or newer itself.
- Python 3.12 or newer for the command line route. Libraries (minimum versions in `pyproject.toml`): numpy, scipy, pandas, scikit-learn, biopython, gemmi, tmtools, Jinja2, openpyxl and jsonschema (the build validates its output with it). Optional extras: `antibody` (abnumber) and `dev` (pytest).
- A browser to view the pages. In the app they open in a Chrome, Edge, Brave or Chromium app window when one is installed, otherwise in your default browser. Chrome is the one the pages are tested in; Edge uses the same engine. The Inspector's 3D view needs WebGL (the other pages draw their small 3D views on a plain canvas); without it the Inspector shows a simplified view and says so. Firefox and Safari should open the pages but are not tested, and local storage for files opened from disk may be kept separately for each page there, so decisions may not be shared between pages (see the FAQ in the guide).
- Not required: BindCraft, PyRosetta, a GPU, a hosted service or any network access at run time (the app's server runs on your computer).
- For the full-length checks, a reference structure of your target. For the specificity screen, species and paralog data (see [docs/specificity.md](docs/specificity.md)).

## Privacy

Nothing leaves your machine except public reference lookups at build time, and those only when you ask for them (in the app: the Download button for a PDB entry and the option Allow the online lookup). The app's server listens on `127.0.0.1` only, has no account and sends nothing anywhere; its details, including the checks that keep web pages in your browser from using it, are in [docs/app.md](docs/app.md). The installer downloads the Python libraries from PyPI. By default a build reads reference data from a local cache and does not touch the network; `import --online` fetches missing public records. Only accessions, PDB ids, gene symbols and taxon ids are put in a request, each checked against a pattern and against a list of allowed hosts (UniProt, PDBe, AlphaFold DB, ConSurf-DB, RCSB, Ensembl). No binder sequence and no design id is ever sent; `data/manifest.json` records what was used. The bundle uses system fonts and makes no network request when opened (`--fonts google` adds an optional typeface link).

A bundle does contain your designs, including binder sequences and the order-list exports. The manifest records file and folder names only (the absolute paths of your machine are cut off), the command you ran in the same form, and a copy of the settings of each run. Check both before you share a bundle.

## Licences of third-party parts

- 3Dmol.js 2.5.5 is vendored in `viewer/vendor` and copied into each bundle under the BSD-3-Clause licence; the licence text is in `assets/3Dmol-LICENSE.txt` of the bundle.
- ConSurf-DB conservation grades are for academic, non-commercial use. The overlay is optional and off by default; `import --consurf` includes it.
- UniProt sequences and annotation, and PDB entries, are public reference data under their own terms (see [docs/reference_data.md](docs/reference_data.md)).
- BindCraft and PyRosetta are not required and not bundled. Binderscope reads the output of your own BindCraft runs. Interface metrics are recomputed here without PyRosetta, so they can differ from BindCraft's columns.
- The BindCraft 2 reader is experimental: it follows the documented layout and has been tested only on synthetic fixtures ([docs/bindcraft2_reader.md](docs/bindcraft2_reader.md)).

## Troubleshooting

The app: the installer, the Command Line Tools, a compiler error, a busy port, Chrome not installed, a build that fails, macOS asking for permission to read a folder and how to read `~/Library/Logs/Binderscope.log` are covered in [docs/app.md](docs/app.md). The command line:

- *A reference record is missing and the build says it is offline*: run once with `--online`, or see [docs/reference_data.md](docs/reference_data.md) for the cache.
- *The schema validator names a field*: the message is `<json path>: <what is wrong>`; [docs/data_contract.md](docs/data_contract.md) says where each rule comes from. No bundle is written from invalid data.
- *The reference structure does not fit the construct*: the build stops with a one-line message and exit code 2 that names the disagreement (for example how few residues of the construct match the reference chain, or that a chain is not a polymer). Check that `--reference-cif` holds the same protein, that `--reference-chain` names the chain of your target and that you passed the same construct file that BindCraft used.
- *The first build is slow*: the interface analysis and the family clustering dominate. Both are cached per design and run, so the second build takes seconds. `--no-cache` recomputes everything.
- *Changed a page under `viewer/`*: run `binder-review build-bundle work/my-target --out bundles/my-target` (seconds). A change to a profile needs a full `import`.
- *Decisions did not persist*: the page says so when the browser blocks storage. Use "Save review file" and keep a copy outside the bundle (a rebuild into the same folder deletes `reviews/`); "Load review file" brings the decisions back, also after "Clear decisions". In the app a copy is also kept in the project folder and restored when the page opens.
- *The binder looks misplaced on the full-length protein*: check that the chain of the target in the reference (`--reference-chain`, or Options in the app) is the right one and that the reference is the full-length version of the same protein, keep the default `--placement interface` (a fit around each design's interface; `whole` can leave the binder 1 to 3 Å off where BindCraft's copy of the target differs from the reference), and read the `REMARK 250 placement:` line in `structures/<id>.pdb`. [docs/app.md](docs/app.md) has the full checklist.
- *The 3D view is empty*: check that `structures/` was copied with the bundle (the inspector loads one `structures/<id>.js` per design when it opens) and that WebGL is enabled.
- *Something looks wrong in a run*: `binder-review inspect /path/to/run01` prints what the reader found: counts, funnel, hotspots, warnings.

## Documentation

| | |
|---|---|
| [docs/GUIDE.md](docs/GUIDE.md) | the user guide: quick start, the pages, workflows, how to read each check, shortcuts, FAQ, glossary |
| [docs/app.md](docs/app.md) | the app and the installer: install, update, uninstall, the import page, privacy and security, troubleshooting, the local API |
| [docs/cli.md](docs/cli.md) | commands, options, exit codes, safety rules |
| [docs/architecture.md](docs/architecture.md) (including why a local app), [docs/data_contract.md](docs/data_contract.md), [docs/schema/](docs/schema/) | data flow, module map, the JSON files the pages read and their schemas |
| [docs/profile_spec.md](docs/profile_spec.md) | how to write a profile for your own target |
| [docs/export.md](docs/export.md), [docs/viewer.md](docs/viewer.md) | the pipeline, the exporter and the bundle layout; the viewer core |
| [docs/viewer_board.md](docs/viewer_board.md), [docs/viewer_inspector.md](docs/viewer_inspector.md), [docs/viewer_map.md](docs/viewer_map.md), [docs/viewer_query.md](docs/viewer_query.md) | what each page reads and does |
| [docs/target_frame.md](docs/target_frame.md), [docs/families.md](docs/families.md), [docs/modes.md](docs/modes.md), [docs/specificity.md](docs/specificity.md), [docs/interface.md](docs/interface.md) | the analysis modules |
| [docs/reference_data.md](docs/reference_data.md), [docs/bindcraft2_reader.md](docs/bindcraft2_reader.md), [docs/antibody.md](docs/antibody.md), [docs/structure_api.md](docs/structure_api.md) | reference records, the BindCraft 2 reader, antibody profiles, the structure primitives |

## Tests

```
pytest tests
```

The tests use synthetic data only: a generated toy target, toy BindCraft runs and a toy reference structure. They need no network and no BindCraft output.
