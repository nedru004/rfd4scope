# The Binderscope app

The Binderscope app is the easiest way to use Binderscope. It is a small program on your own computer with a window in which you choose your BindCraft runs and the target, watch the analysis and open the review pages. You never need the terminal, except to troubleshoot an installation. This page is for the scientists who install and use the app and for the people who script it: what it is, how to install, update and remove it, every field of the import page, what stays on your machine, what to do when something goes wrong and the local API it offers. The review pages themselves are described in [GUIDE.md](GUIDE.md); the same build from a terminal is in [cli.md](cli.md).

Contents: [What it is](#what-it-is) · [Install, update, uninstall](#install-update-uninstall) · [Starting and stopping](#starting-and-stopping) · [Where things live](#where-things-live) · [The home page](#the-home-page) · [The import page](#the-import-page-field-by-field) · [The build](#the-build) · [The review pages in the app](#the-review-pages-in-the-app) · [Privacy and security](#privacy-and-security) · [Troubleshooting](#troubleshooting) · [The local API](#the-local-api-for-tool-builders) · [Environment variables](#environment-variables)

## What it is

Three parts:

- **A local server.** `binder-review app` (the same program as `binderscope app`) starts a small web server on `127.0.0.1`, port 51731. It listens on your computer only. It serves the app's own pages (the project list and the import page) and, for each project, the review pages that Binderscope writes.
- **A window.** The macOS app `Binderscope.app` starts the server and opens its address in its own window: an app window of Chrome, Edge, Brave or Chromium when one is installed (no tabs, no address bar), otherwise your default browser. The launcher has no Dock icon of its own; the window you see belongs to your browser.
- **An installer.** `install.sh`, or double-clicking `Install Binderscope.command`, sets everything up in your home folder and needs no administrator rights.

The app does not do the analysis differently from the command line. When you press Build, it starts `binder-review import` in a separate process with the choices you made, follows its log to draw the progress, and then serves the review pages that command wrote. A crash or a cancelled build never takes the app down. The review pages are the ordinary static pages of a bundle; the app only adds a link back to your projects (and keeps a copy of your decisions, see [The review pages in the app](#the-review-pages-in-the-app)).

Your BindCraft run folders are read where they are and never written to. Everything the app writes goes into the Binderscope folder (by default `~/Binderscope`), plus a log file and the program's own folders.

## Install, update, uninstall

### What you need

- macOS 11 or newer for the installer, the app and the native folder dialogs.
- An internet connection during the installation: the Python libraries are downloaded from PyPI. Nothing about your data is sent.
- Apple's Command Line Tools. The installer checks for them and, if they are missing, opens Apple's installer for them (see below).
- Python 3.12 or newer. The installer looks for one and, if there is none, offers to install Python 3.14 with Homebrew when Homebrew is present, or tells you to install it from python.org.

The Binderscope installer never asks for an administrator password and never writes outside the folders listed under [Where things live](#where-things-live).

### Install

1. Get the Binderscope folder: download this repository as a ZIP file and unzip it, or clone it. Keep the folder complete: the installer copies from it and checks that `pyproject.toml`, `src/binder_review`, `viewer/pages` and `docs/schema` are there. If something is missing it stops and says what.
2. Double-click `Install Binderscope.command` in that folder. Terminal opens, runs `install.sh`, shows its progress and waits for you to press Return at the end. Or open Terminal in the folder and run `./install.sh`.
3. The first time, if Apple's Command Line Tools are missing, a system window opens. Click Install, wait until it finishes, then run the installer again.
4. Wait. The first installation takes a few minutes: one of the libraries is compiled on your Mac.
5. At the end the installer starts Binderscope. From then on, open it from Spotlight (press Cmd+Space, type Binderscope), from Launchpad or from the `Applications` folder in your home folder.

If macOS refuses to open `Install Binderscope.command` because it came from the internet, allow it in System Settings, Privacy & Security (the wording differs between macOS versions), or skip the double-click and run `./install.sh` in Terminal.

### What the installer does

| Step | What happens |
|---|---|
| Before step 1 | Checks that the folder is complete. If a part is missing it stops, lists what is missing and what the folder holds, and says what to do: copy the whole Binderscope folder again as one archive (not file by file), and, when some files are still in iCloud and not on the Mac yet, right-click the folder in Finder and choose Download Now. |
| 1 of 5, Checking this Mac | Looks for Apple's Command Line Tools (if they are missing it opens Apple's installer for them and stops: run it again afterwards) and for Python 3.12 or newer (`python3.14`, `python3.13`, `python3.12`, the Homebrew folders, then `python3`). Without one it offers to run `brew install python@3.14`, or asks you to install Python from python.org. |
| 2 of 5, Copying Binderscope | Asks a running Binderscope to quit (it says so), so that the next start uses the new files. Copies the program files (without `.git`, the tests and the `work`, `bundles` and `data` folders) to `~/Library/Application Support/Binderscope/app`. After that you may delete the folder you installed from. |
| 3 of 5, Setting up Python | Creates a private Python environment in `~/Library/Application Support/Binderscope/venv`, then downloads and installs Binderscope and its libraries into it. Dots show that it is working. The output of the installation (pip) is written to `install.log` in that folder. |
| 4 of 5, Creating the app | Writes the launcher `~/Library/Application Support/Binderscope/bin/binderscope` (the `binderscope` command), links it as `~/.local/bin/binderscope` when that folder is already on your PATH, and creates `~/Applications/Binderscope.app` with its icon. |
| 5 of 5, Done | Says where things are and starts the app. |

Options of `./install.sh`:

| Option | Meaning |
|---|---|
| `--uninstall` | remove the app, the command and the environment (see below) |
| `--prefix DIR` | where the program lives (default `~/Library/Application Support/Binderscope`) |
| `--app-dir DIR` | where `Binderscope.app` goes (default `~/Applications`) |
| `--python PATH` | the Python 3.12 or newer to use (default: found automatically) |
| `--dev` | use the folder you run it from in place (an editable install with a `.venv` there) instead of a copy, for people who work on the code |
| `--no-app` | do not create the app |
| `--no-link` | do not add the `binderscope` command to `~/.local/bin` |
| `--no-open` | do not start the app at the end |
| `-y`, `--yes` | answer yes to the installer's questions |
| `-h`, `--help` | print the options |

On a system that is not macOS the installer sets up the environment and the command but creates no app. Start the app with `binderscope app` there; the page then uses its own folder browser instead of the macOS dialogs.

### Update

Download the new version of the folder (or `git pull`) and run the installer again. It replaces the program copy and the app, reuses the Python environment when it is still valid and installs into it again, and keeps all your projects.

The installer asks a running Binderscope to quit (and says so), so the new version starts the next time you open it. Projects that were built by an older version show the note "built with an older version" on the home page. Open the project's More menu and choose **Update the review pages** (seconds: it writes the pages of the new version from the saved analysis) or **Rebuild** (it analyses again, using the cache where it is still valid). An update that changes the analysis itself, such as how designs are placed on the full-length protein, needs the **Rebuild**: the card of a project built before it says "Built with an older analysis" and offers **Rebuild** as its main button, because updating the pages alone does not change the numbers.

### Uninstall

Run `./install.sh --uninstall` from the Binderscope folder. It stops a running app, removes `~/Applications/Binderscope.app`, the `binderscope` link and `~/Library/Application Support/Binderscope`. It keeps your projects in `~/Binderscope`: delete that folder yourself when you no longer need them. The log file `~/Library/Logs/Binderscope.log` is also left in place.

### Without the installer

The app is part of the Python package. After the command-line installation described in the [README](../README.md) (`pip install -e ".[dev]"` in a virtual environment), `binder-review app` starts it. That is also the route on Linux or whenever you want your own Python environment.

## Starting and stopping

**Start.** Open Binderscope from Spotlight, Launchpad or `~/Applications`. A window opens with your projects. If the app is already running, opening it again shows a window of the running app instead of starting a second server.

**Stop.** Press **Quit** in the header of the home page or the import page. Closing the window does not stop the app at once; started from `Binderscope.app` it stops by itself about a minute after the last window of it is gone (every open page checks in every 20 seconds, and the server quits when it has heard nothing for 75 seconds). A running build keeps the app alive, so you can close the window during a long build and open Binderscope again later: the home page shows the progress.

**The address.** The port is fixed on purpose. The review pages keep your decisions per address, so the address should not change (the app also keeps a copy of your decisions in the project folder, see below). If port 51731 is used by another program, the app takes the next free port (up to 24 above) and writes a note to its log.

**From a terminal.** `binder-review app` accepts `--port`, `--home`, `--window` and `--quit-when-closed`, and `-v` to log every request (see [cli.md](cli.md)). It prints the address and the projects folder, opens the window and runs until you press Ctrl+C.

## Where things live

| What | Where |
|---|---|
| Your projects | `~/Binderscope/<project>/` (move them with `--home` or `BINDERSCOPE_HOME`) |
| What you chose in the import page and how the last build went | `~/Binderscope/<project>/project.app.json` |
| The analysis (the project folder of `binder-review import`: caches, `export/` and, for a BindCraft 2 campaign whose binder numbers start at 0, `onebased/` with renumbered copies of its structure files; your run folders are never changed) | `~/Binderscope/<project>/work/` |
| The review pages (the bundle) | `~/Binderscope/<project>/bundle/` |
| The structure files that you dropped on the page or downloaded from the RCSB (moved here when the project is built, with `target-`, `reference-` or `profile-` in front of the name) | `~/Binderscope/<project>/inputs/` |
| The log of the last build | `~/Binderscope/<project>/build.log` |
| A copy of your decisions | `~/Binderscope/<project>/review-events.json` |
| First landing place of dropped and downloaded files (what no project used is deleted after a week, when the app starts) | `~/Binderscope/_uploads/` |
| The program, its Python environment, the installer log | `~/Library/Application Support/Binderscope/` (`app`, `venv`, `bin`, `install.log`) |
| The app | `~/Applications/Binderscope.app` |
| The app's log | `~/Library/Logs/Binderscope.log` |
| The `binderscope` command | `~/Library/Application Support/Binderscope/bin/binderscope`, linked from `~/.local/bin/binderscope` if that is on your PATH |

Run folders and the structure files you pick with the file chooser stay where they are: a project remembers their paths. A rebuild reads them again, so do not move or delete them while you still want to rebuild. A structure file that you dropped on the page or downloaded is moved into the project's `inputs` folder when the project is built (a file taken from another project's `inputs` by **Edit the inputs…** is copied, so deleting the old project never breaks the new one); `_uploads` only holds files on their way, and the app deletes what no project used after a week.

A project name becomes the folder name in lowercase letters, digits, dots, dashes and underscores (`My Target` becomes `my-target`, and a second project of the same name `my-target-2`). Do not put the Binderscope folder inside a BindCraft run folder: the app refuses to build then, because input folders are never written to.

## The home page

The home page lists your projects, newest first. With no project yet it shows a short welcome and **Start a new project**. The header shows the version and **Quit**; the footer shows the projects folder and **Show in Finder**.

A project card shows its name, chips (the number of runs, accepted designs and families, and "built with an older version" when that applies), a status line and the date of the last build. The status line is:

- while it builds: the current step and a progress bar;
- after a failed or cancelled build: the message;
- when the review pages have gone missing from the project folder: a note to rebuild;
- when the project was built with an older analysis (an update changed the analysis itself, for example how designs are placed on the full-length protein): a note that says so, followed by the first item of "What stands out", with **Rebuild** as the main button and **Open** next to it;
- otherwise: the first item of "What stands out" from the Overview.

Buttons: **Open** (when the pages exist), **Rebuild** (as the main button for a project built with an older analysis), **Cancel build** (while it builds) and **More**, which holds:

| Action | What it does |
|---|---|
| Edit the inputs… | opens the import page with this project's choices filled in. Change a run, add one, choose another structure or other options, then press Build project. If the last build of the project failed or was cancelled, it is built again in the same folder; otherwise the app makes a new project named like the old one with " 2" added and leaves the old project as it is. |
| Update the review pages | writes the review pages again from the saved analysis (seconds). Use it after updating Binderscope. |
| Rebuild | runs the analysis again with the saved choices. Cached stages are reused, so with unchanged inputs it takes seconds. New designs in a run folder are picked up. |
| Rebuild from scratch | the same without the cache (`--no-cache`): every stage is recomputed. |
| Show in Finder | opens the project folder. |
| Delete project | removes the project folder (the analysis, the review pages, the copy of your decisions and the structure files in `inputs`) after asking. Your run folders are not touched. Not possible while that project is building. |

Only one build runs at a time. The page refreshes by itself.

## The import page, field by field

**New project** opens a page with three cards and a bar at the bottom. **Build project** stays disabled, and the bar says what is still to do, until the runs and both structures are chosen and every run has its own short name. When everything is ready the bar says so.

### 1. BindCraft runs

- **Add run folders…** opens the macOS folder dialog; select one or several folders. Without a native dialog the page opens its own folder browser. Choose the folder of each run: the one that holds the CSV files, the Accepted folder and the settings file.
- **or paste a folder path** and **Add** do the same from a path you type or paste.
- If the folder you choose is not a run but holds run folders, the page adds those (up to 30).
- Each run appears as a row: its **name** (an editable field; the default comes from the folder name, for example `run01` for `250101_run01`; letters, digits, dot, dash and underscore, up to 31 characters, each name once), its path, **Remove**, and chips that say what was found: the reader (with its version for BindCraft 1, for example `BindCraft 1 1.5`; *experimental* is added after BindCraft 2), the numbers of accepted and rejected designs and of families, the hotspots, the binder length range, and notes: *hotspots read from the tables* (no settings file, but the result tables carry the hotspots), *no settings file: hotspots unknown*, or the first warning of the reader. A run that records no hotspot at all (no settings file and an empty hotspot column) can still be built: it has no intended epitope, the review pages say so, and its site view comes from where the designs bind (see *When data are missing* in [GUIDE.md](GUIDE.md)).
- A folder that Binderscope cannot read as a run shows a message instead. The page only reads the CSV files and the settings file at this step, not the structures.

### 2. The target

- **Structure you gave BindCraft**: **Choose file…**, or drop the file on the box below it. Under **Found next to your run** the page offers the `.pdb` and `.ent` files that lie in the run folder or beside it (the one named in the settings file first, marked *named in the settings*); click one to use it. After choosing, the page shows the file name, the format and the chains with their residue counts.
- **Full-length reference structure**: **Choose file…**, or drop a file (mmCIF or PDB format), or type a four-character PDB id in **or a PDB entry** and press **Download**. The download fetches that entry as mmCIF from the RCSB Protein Data Bank (only the id is sent; the file is kept in the project). The page lists the chains of the file and says which chain matches your target structure and which other chains will be treated as other subunits, or warns when no chain clearly matches.
- **How your structure fits the reference.** Once both files are chosen, the page checks, with the same numbering vote and the same CA fits the build uses, how well the structure you gave BindCraft fits the chosen chain of the reference, and says so under the reference. A green message gives the CA RMSD over the residues matched by number and, when it is clearly lower, the RMSD without the flexible parts (the residues a trimmed fit keeps). An amber one says the fit is loose (CA RMSD above 3 Å, or fewer than 90 percent of the matched residues the same amino acid): check that it is the same protein. A red one says it does not fit (fewer than 10 residues matched, fewer than 60 percent of your structure's residues matched by number, or fewer than 70 percent of the matched ones the same amino acid): usually the wrong chain, another protein or another numbering, so choose the chain in Options or another reference. The message follows the chain you choose in Options. It is advice and does not block **Build project**; the build itself stops for a structure that matches the reference chain at fewer than 90 percent of its residues.
- The reference supplies everything BindCraft never saw: other subunits, the rest of the chain, glycans. Without a reference there is no build (see [GUIDE.md](GUIDE.md), "When data are missing").

### 3. Options

Closed by default. None of these is required.

| Field | Meaning |
|---|---|
| Project name | the name of the project (default: taken from the folder that holds your first run, otherwise from the run folder; "My project" when nothing helps) |
| UniProt accession of the target | adds the domain strip, topology, membrane plane and, when your profile lists species and paralogs, the specificity panel |
| Allow the online lookup | lets the build fetch the public records it needs for that accession (and for the PDB id of the reference, when it has one) once and keep a copy in a local cache. Without the tick nothing is fetched: the build uses only records that are already cached and leaves out what it cannot find. |
| Chain of the target in the reference | the chain of the reference file that is your target (filled from the file, and set to the chain that matches your target structure) |
| Other subunits of the target in the reference | *Detect them* (chains with the same sequence, the default), *None*, or *Choose chains…* with chain ids such as `B,C` |
| How designs are placed on the full-length protein | *Fit around each design's interface* (recommended, the default), *Fit the whole target* or *Fit the rigid core of the target*. BindCraft predicts its own copy of the target, which differs a little from the reference. Fitting around the interface keeps the binder on the epitope; the whole-target fit can leave it 1 to 3 Å off where the model differs; the core fit uses only the residues that match best. The choice changes the distances to the other subunit, the rest of the protein and the sugars and the Context verdicts, not the contacts or the other interface numbers. Changing it with **Edit the inputs…** makes a new project, which is analysed from the start (a project keeps its own cache) |
| Profile file | a profile JSON file for your target (see [profile_spec.md](profile_spec.md)); *none* uses the neutral default profile |

These fields are the options `--name`, `--uniprot` with `--online`, `--reference-chain`, `--partner-chains`, `--placement` and `--profile` of `binder-review import` ([cli.md](cli.md)). The ConSurf overlay (`--consurf`, academic use only) is not offered on the page; the API accepts it.

## The build

**Build project** creates the project folder and starts the analysis. The page shows nine steps with a progress bar, the time spent, a line of detail for the step that is running (for example how many designs are done) and, under **Show details**, the log:

1. Reading the run folders
2. Placing the target on the full-length structure
3. Checking every design against the rest of the protein
4. Analysing the interfaces
5. Grouping designs into backbone families
6. Finding binding modes and near-misses
7. Screening specificity
8. Writing and validating the data
9. Writing the review pages

The first build of a project takes a minute or two for a typical project and grows with the number of designs; a rebuild with unchanged inputs reuses the cache and takes seconds. **Cancel build** stops it. If the app stops while a build is running (you quit it, for example), the project shows the build as interrupted the next time; **Rebuild** continues, because the finished steps are cached.

When it finishes, the page says **Your project is ready**, lists what stands out in the data and offers **Open the project** and **All projects**. If it does not finish, the page shows the reason in plain words, with a hint when the files do not fit together (check that the reference is the full-length version of the same target and that its chain is the right one). **Back to the inputs** returns to the cards with your choices; building again then reuses the same project folder instead of making another. The reasons fall into four kinds: the inputs do not fit (the command stopped with exit code 2), the command reported an error (exit code 1), the analysis crashed or ran out of memory (close other apps and build again, or build fewer runs at once) and cancelled.

## The review pages in the app

**Open** shows the project's pages at `http://127.0.0.1:51731/p/<project>/`. They are the pages of the bundle in `~/Binderscope/<project>/bundle/`. The app adds a **‹ Projects** link at the left of each page's header (a small pill at the bottom left of the Guide page, which has no such header), and the link turns into "Binderscope has stopped" if the app is no longer running. Everything else works as described in [GUIDE.md](GUIDE.md).

**Your decisions.** The pages keep your keep, maybe and reject decisions in the browser, per address and per bundle, as ever. In the app the pages also save a copy to `review-events.json` in the project folder a few seconds after every change and when you leave the page, and when a page opens in a browser that lacks some of them (another browser, another port, cleared site data) it merges the saved copy back in and reloads once. The merge is a union by event id, so nothing is overwritten. The copy belongs to the project: **Delete project** deletes it. **Save review file** and **Load review file** work as before, and are still the way to share decisions with a colleague.

**Opening the bundle outside the app.** `~/Binderscope/<project>/bundle/index.html` also opens from disk, and you can zip the `bundle` folder to send it. A page opened from disk has another address, so it keeps its own browser store and does not see the project copy of your decisions; move decisions between the two with a review file. Rebuilding or updating the pages rewrites the `bundle` folder, so keep nothing of your own in it.

## Privacy and security

**Nothing is uploaded.** The app has no account, no cloud service and no usage reporting. Your run folders, structures, sequences and decisions stay on your computer. The review pages load everything from the local server; the app's own pages use no outside address.

**Network requests happen only when you ask for them:**

- *Download* a PDB entry on the import page: the server fetches `https://files.rcsb.org/download/<id>.cif` and sends only that id.
- *Allow the online lookup* in Options: the build fetches public records by accession, PDB id, gene symbol and taxon id from UniProt, PDBe, AlphaFold DB and, for the optional overlay, ConSurf-DB. No binder sequence and no design id is ever sent (see [reference_data.md](reference_data.md)).
- The installer and the updater download the Python libraries from PyPI (and Python itself from Homebrew or python.org if you choose that).

**The local server.** It listens on `127.0.0.1` only, so other computers cannot reach it. It has no password, so any program that runs on your computer can talk to it, as with any local service; if other people are logged in to your Mac at the same time, quit the app when you step away. The server refuses what a web page in your browser could try:

- a request whose `Host` header is not `127.0.0.1`, `localhost` or `[::1]` with the app's port is answered with 403 (this blocks DNS-rebinding tricks);
- every request that changes something (`POST`) must carry the custom header `X-Binderscope`, must have an `Origin` that is the app's own address when it has one, and must not be marked cross-site by the browser (`Sec-Fetch-Site`). The server sends no CORS headers and does not answer pre-flight requests, so a page from another site can neither read its answers nor send such a request;
- the review pages of a project are served only from that project's `bundle` folder, and the app's own files only by their plain names; path tricks such as `..` give 403 or 404;
- an upload is limited to 300 MB and lands in `~/Binderscope/_uploads` under a random folder name with a cleaned file name.

**What the app writes.** Only the folders listed under [Where things live](#where-things-live). The project folder holds your designs in derived form (sequences, placed structures, scores). Treat it like your run folders when you share or back up your computer.

**macOS permissions.** The first time Binderscope reads a folder in Documents, Desktop or Downloads, or on an external drive, macOS may ask whether the app may access it. Choose Allow. If you chose Don't Allow, the folder cannot be read: change the setting in System Settings, Privacy & Security (Files and Folders), or choose a run folder elsewhere.

## Troubleshooting

**The installer says the Command Line Tools are missing.** A system window opened: click Install, wait until it finishes, then run the installer again.

**"The installation stopped: this folder is incomplete."** The installer lists what is missing (`pyproject.toml`, `src/binder_review`, `viewer/pages` or `docs/schema`) and what the folder holds. Copy the whole Binderscope folder again, as one archive and not file by file, and run the installer from the folder that holds `install.sh`, `pyproject.toml`, `src`, `viewer` and `docs`. If it mentions iCloud, some files are not on this Mac yet: right-click the folder in Finder, choose Download Now and run the installer again.

**"Python 3.12 or newer is needed."** Install Python from python.org (the macOS installer) or, with Homebrew, `brew install python@3.14`, then run the installer again. `./install.sh --python /path/to/python3.12` uses a particular Python.

**A compiler error while installing the libraries.** The last lines of the failure are printed and the whole log is `~/Library/Application Support/Binderscope/install.log`. If it mentions a compiler or `clang`, run `xcode-select --install` (or update the Command Line Tools in System Settings, General, Software Update) and run the installer again.

**macOS will not open the installer.** See [Install](#install). Running `./install.sh` in Terminal avoids the double-click.

**The app does not open.** Open the log: `tail -n 40 ~/Library/Logs/Binderscope.log` in Terminal (or `open -e ~/Library/Logs/Binderscope.log`). It shows the address and folder the app started with, port notes and unexpected errors. You can also start it in a terminal to see what it says: `~/Library/Application\ Support/Binderscope/bin/binderscope app -v`.

**"Port 51731 is in use."** Another program (or another copy of Binderscope, for example one you started from a terminal) uses the port. The app then takes the next free port and says so in its log. Decisions are kept per address, so the project copy in `review-events.json` brings them back when you open the project at the new address; you can also move them with a review file.

**The window shows "Binderscope has stopped".** The app ended: you pressed Quit, or about a minute passed with no open window and no build. Open Binderscope again; your projects and decisions are unchanged.

**Chrome, Edge, Brave and Chromium are not installed.** The app opens in your default browser instead, in an ordinary tab. The pages are tested in Chrome; the Inspector's 3D view needs WebGL.

**The folder dialog does not appear or fails.** Paste the folder path into the field under the run list instead. When the system has no native dialog (anything but macOS) the page uses its own folder browser.

**A build fails.** The message on the page names the problem in the page's own words (for example a chain that is not in the reference file, or a construct that does not match the reference). The full text is under **Show details** and in `~/Binderscope/<project>/build.log`. Fix the choice, press **Back to the inputs** and build again. A failure that mentions memory or a stopped worker means the computer ran short of memory: close other apps and build again.

**The binder looks misplaced on the full-length protein.** In the Inspector the binder sits inside the other subunit or the rest of the protein, or off the epitope, and the Target checks flag many designs of a run. Check, in this order:

- *The chain.* When you built the project, the import page said how your target structure fits the chain of the reference (the green, amber or red message under the reference, with the CA RMSD and the residues matched). A red message means the wrong chain, another protein or another numbering. Open the project's **More** menu, choose **Edit the inputs…**, set **Chain of the target in the reference** in Options and build again (the app makes a new project and keeps the old one).
- *The protein.* The reference must be the full-length version of the same protein, not a homolog or another construct.
- *The placement option.* **How designs are placed on the full-length protein** (Options) should be *Fit around each design's interface*, the default. The whole-target fit can leave the binder 1 to 3 Å off where BindCraft's copy of the target differs from the reference. A project whose card says "Built with an older analysis" was placed by the whole-target fit: press **Rebuild**.
- *The numbers.* In the Inspector's Target checks a binder fails the other-subunit or the rest-of-protein check by default from ten atoms within 3 Å. If nearly every design of a run does, suspect the placement before the designs, and build the project again with another placement option to see which verdicts depend on it.
- *The placement line.* Open `~/Binderscope/<project>/bundle/structures/<design id>.pdb` in a text editor. The line `REMARK 250 placement:` says which fit was used and gives the CA RMSD near the interface and over the whole target. More than 1.5 Å near the interface means that the design's own copy of the target differs from the reference chain there; the build records a warning for it in `data/manifest.json`, and the Overview counts the warnings. Chain A of that file is the design's own copy of the target, so you can compare it with the reference in any viewer. The same numbers are in `data/campaign.json` as `m.place_iface_rmsd`, `m.place_global_rmsd` and `prov.fit`.

**The app seems stuck.** Quit it with the Quit button. If the page does not respond, stop the process: in Activity Monitor quit the `Python` process that runs `binder_review app`, or in Terminal `pkill -f "binder_review app"`.

**Rebuild says a file is missing.** The project remembers the paths of your run folders and of structure files you chose in place. Put them back, or use **Edit the inputs…** to point the project at their new places.

## The local API for tool builders

These are the endpoints the app's own pages use; they are listed for tools that drive the app. The `version` in `/api/ping` tells a tool which Binderscope it is talking to. Requests and answers are JSON (UTF-8). Errors are `{"error": "message"}` with a status such as 400 (a bad request), 403 (a refused request), 404, 409 (another build is running), 413 (too large), 501 (no native dialog) or 502 (the RCSB could not be reached).

**Rules for every request.** The `Host` header must be `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`. Every `POST` also needs the header `X-Binderscope` (any non-empty value); if it sends an `Origin`, that must be `http://127.0.0.1:<port>` or `http://localhost:<port>`.

| Method and path | Purpose |
|---|---|
| `GET /api/ping` | `{app: "binderscope", version}` |
| `GET /api/state` | `{app, version, home, native_dialogs, platform, projects, job}`: the project summaries and the running job, if any |
| `POST /api/pick` | `{kind: "folder" or "file", multiple, prompt, default}`: opens the native dialog, answers `{paths}` or `{cancelled: true}`, or 501 without a native dialog |
| `GET /api/ls?path=` | `{path, parent, entries: [{name, dir, path}], home}`: a folder listing (hidden entries left out) for the page's own browser |
| `GET /api/inspect-run?path=` | what a run folder holds: `{ok, id, path, reader, experimental, accepted, rejected, families, hotspots, binder_length, warnings, settings_found, target_candidates}`, or `{ok: false, error, children}` where `children` lists run folders inside it |
| `GET /api/check-structure?path=&against=&chain=` | the chains of a PDB or mmCIF file `{ok, name, format, chains, suggest_chain, partners}`; with `against` (the construct) the chain that matches it is suggested, and `fit` says how the construct fits the chain `chain` (default: the suggested chain): `{ok, chain, offset, n, n_construct, identity, rmsd, core_rmsd, core_n}` (the numbering offset, the residues matched by number and the residues of the construct, the share of them with the same amino acid, the CA RMSD over them and after the trimmed refit with its residue count), or `{ok: false, error}` |
| `POST /api/upload?name=` | the request body is a file (up to 300 MB); saved under `_uploads`; answers `{path, name}` |
| `POST /api/fetch-reference` | `{id}`: downloads a PDB entry from the RCSB; answers `{path, name, id}` |
| `POST /api/projects` | `{name, inputs, retry}` creates a project and starts its build; answers 201 `{slug, job, inputs}`, where `inputs` holds the paths as the project keeps them (an uploaded structure file then lies in the project's `inputs` folder). The `inputs` you send is `{runs: [{id, path, label, settings}], target_pdb, reference, reference_id, reference_chain, partner_chains, uniprot, profile, online, consurf, placement}` (`placement` is `interface`, the default, `whole` or `core`); `retry` is the slug of a failed or cancelled project to build again |
| `GET /api/projects/<slug>` | the project summary, its inputs and the end of its log |
| `POST /api/projects/<slug>/rebuild` | `{no_cache}`: runs the analysis again; answers `{slug, job}` |
| `POST /api/projects/<slug>/update-pages` | writes the review pages again from the saved analysis |
| `POST /api/projects/<slug>/delete`, `/reveal` | deletes the project, or shows its folder in the Finder |
| `GET`, `POST /api/projects/<slug>/state` | the saved decisions: `GET` answers `{events}` (the text of `review-events.json`), `POST {events}` merges a JSON list of review events (each with an `id`) into it and answers `{count}` |
| `GET /api/jobs/<id>?since=N` | the job: `{id, kind, slug, status, stage, stages, stage_label, detail, done, progress, elapsed, error, error_kind, returncode, log_from, log, log_total}`. `status` is `running`, `done`, `failed` or `cancelled`; `log` holds the lines from `N` on |
| `POST /api/jobs/<id>/cancel` | stops a running build |
| `POST /api/reveal-home` | shows the Binderscope folder in the Finder |
| `POST /api/quit` | stops the app |
| `GET /p/<slug>/...` | the review pages of a project (a script that adds the **‹ Projects** link and the decision copy is appended to HTML pages) |
| `GET /_app/<file>` | the app's own pages and assets |

A build from a script, in two requests (the folders are examples; use full paths, because the build runs from the project folder):

```
curl -s -X POST http://127.0.0.1:51731/api/projects \
  -H "X-Binderscope: 1" -H "Content-Type: application/json" \
  -d '{"name": "my-target",
       "inputs": {"runs": [{"id": "run01", "path": "/path/to/run01"}],
                  "target_pdb": "/path/to/construct.pdb", "reference": "/path/to/full_length.cif"}}'
# {"slug": "my-target", "job": {"id": "…", "status": "running", …}}
# The app's import page and this request still ask for a reference structure. The command line does not: `binder-review import` without
# --reference-cif builds with the construct as the frame and switches off the checks that need the rest of the protein (cli.md).

curl -s http://127.0.0.1:51731/api/jobs/JOB_ID
```

For batch work the command line is simpler and has the same effect: `binder-review import` ([cli.md](cli.md)).

## Environment variables

| Variable | Meaning |
|---|---|
| `BINDERSCOPE_HOME` | the projects folder (default `~/Binderscope`); the `--home` option wins |
| `BINDERSCOPE_RCSB_URL` | the address the PDB download uses instead of `https://files.rcsb.org/download` (for a mirror or for tests) |
| `BINDERSCOPE_OFFLINE`, `BINDERSCOPE_REFDATA_DIR` | read by the build process the app starts, as for the command line ([cli.md](cli.md)) |
| `BINDERSCOPE_VIEWER` | a folder with the viewer sources (`pages/` and `js/bs.js`) used instead of the program's own |

## See also

[GUIDE.md](GUIDE.md) · [cli.md](cli.md) · [architecture.md](architecture.md) (why a local app) · [export.md](export.md) · [reference_data.md](reference_data.md) · [profile_spec.md](profile_spec.md)
