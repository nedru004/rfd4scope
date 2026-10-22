# From a finished campaign to a shortlist you can defend.

Binderscope takes the designs a campaign produced, groups them by backbone, checks each one on the full-length target, says which end can carry a tag, and keeps your decisions with their reasons. This guide gets you productive in about ten minutes; the rest is reference.

**Binderscope is the program; BindCraft 2 is the design engine it drives.** BindCraft is named below wherever it is the thing that acted — it designed the backbones, it chose its own filters, and its scores are carried over as its own. The families, the full-length checks, the paratope and tag analyses, the verdicts and the review are Binderscope's. This guide covers the review: the pages you read once a campaign has finished, whether Binderscope launched it for you or you ran BindCraft yourself and point Binderscope at the run folders.

This guide is also shipped inside every bundle as a page that reads offline. It has no screenshots and no example data: every name, id and command in it is generic. Page names (Overview, Board, Inspector, Map, Query) are the five pages of a bundle.

**Contents**

1. [Quick start](#1-quick-start)
2. [Five ideas that make the pages click](#2-five-ideas-that-make-the-pages-click)
3. [The pages](#3-the-pages)
4. [A 15-minute review recipe](#4-a-15-minute-review-recipe)
5. [More workflows](#5-more-workflows)
6. [Reading the checks](#6-reading-the-checks)
7. [When data are missing](#7-when-data-are-missing)
8. [Keyboard shortcuts](#8-keyboard-shortcuts)
9. [Saving, sharing and exporting](#9-saving-sharing-and-exporting)
10. [Good practice and pitfalls](#10-good-practice-and-pitfalls)
11. [FAQ](#11-faq)
12. [Glossary](#12-glossary)

## 1. Quick start

Binderscope is an app on your own computer. You choose your BindCraft runs and the target, it analyses them, and it opens the review pages in its own window. Everything stays on your machine and your designs are not uploaded.

1. **Install Binderscope (once).** On a Mac, open the Binderscope folder you downloaded and double-click **Install Binderscope.command**. A Terminal window shows the progress for a few minutes. It checks Apple's Command Line Tools (it asks you to install them once if they are missing), finds or installs Python, and creates the app Binderscope in the Applications folder of your home folder. It never asks for an administrator password. When it is done, Binderscope opens.

   Not on a Mac, or happier in a terminal? See [the command line](#prefer-the-command-line) below. If the installer stops with a message, [the app guide](app.md) explains each one.

   *Once. To update, run the installer again.*

2. **Open Binderscope.** Press Cmd+Space, type Binderscope and press Return. It opens its own window with the list of your projects, empty at first. Your projects are kept in the folder `Binderscope` in your home folder. The app closes itself about a minute after you close its window; your projects stay.

3. **Choose your runs.** Click **New project**. On the first card press **Add run folders…** and choose one or more BindCraft run folders: the folder of each run, the one that holds the CSV files, the Accepted folder and the settings file. You can also paste a path.

   Each run shows what was found: the BindCraft version, the accepted and rejected designs, the families, the hotspots and the binder lengths. Give each run a short name (`run01`, `run02`). Your folders stay where they are and are never changed. Folders you pruned are fine: a missing part switches off the features that need it, and the Overview says which.

   *New project · card 1*

4. **Add the target.** On the second card choose the structure you gave BindCraft (the page suggests files it found next to your run) and the full-length reference structure of the target: a file, a file dropped on the box, or a PDB id that the page downloads from the RCSB when you press Download. Once both files are chosen the page says how well your structure fits the chain of the reference (a green, amber or red message with the CA RMSD and the residues matched); red usually means the wrong chain, another protein or another numbering.

   The reference supplies everything BindCraft never saw: other subunits, the rest of the chain, glycans. See [the target frame](#2-five-ideas-that-make-the-pages-click) and [when data are missing](#7-when-data-are-missing). The third card, Options, is closed and optional: project name, UniProt accession (with an opt-in online lookup), the chain of the target in the reference, the other subunits, how designs are placed on the full-length protein and a profile file. If the reference is an AlphaFold model of a protein with several domains, read the warning about how well it places them under [when data are missing](#7-when-data-are-missing).

   *New project · cards 2 and 3*

5. **Build.** Press **Build project**. The page shows the nine steps of the analysis as they run. The first build takes a minute or two for a typical project and grows with the number of designs; a rebuild with unchanged inputs reuses a cache and takes seconds. When the page says your project is ready, press **Open the project**.

   If the build stops, the page says why in plain words, for example that a chain is not in the reference file. Fix the choice with **Back to the inputs** and build again.

   *Build page*

6. **Read the Overview.** The first page lists your runs with their counts, what stands out in the data (each item links to the page or design it is about), what the project carries and which checks are switched off for lack of data. Read the switched-off list: it tells you what the build could not do with your inputs. A **‹ Projects** link at the left of every page's header leads back to your projects.

   *Overview*

7. **Start on the Board.** Read the funnel and the diversity panel for each run, then walk the tiles with the arrow keys and press `K` to keep, `M` for maybe, `X` to reject (the same key again clears it). Your decisions are saved in this browser as you go, and the app keeps a copy in the project folder.

   *Board*

> **Note: Where things are.** Each project is a folder in `Binderscope` in your home folder: the analysis in `work`, the review pages in `bundle`. In a project's **More** menu you find **Edit the inputs…** (change or add a run, then build), **Update the review pages** (after you update Binderscope), **Rebuild**, **Show in Finder** and **Delete project**. Deleting a project never touches your BindCraft run folders. [The app guide](app.md) lists every file and field.

> **Note: Sharing a bundle.** Choose **More** and **Show in Finder** on the project, zip its `bundle` folder and send it. Whoever opens `index.html` sees the same pages and data. It holds your binder sequences, and its build record (`data/manifest.json`) names your input files and folders (names only: the absolute paths of your machine are cut off) and copies the settings of each run, so check both before it leaves your lab. Decisions travel separately as a small review file (see [Saving, sharing and exporting](#9-saving-sharing-and-exporting)).

### Prefer the command line?

The app runs the same build that the command line offers, so you can script it or use it where the installer does not apply. You need Python 3.12 or newer; `pip install -e ".[dev]"` in a virtual environment installs the package (the `dev` extra adds pytest and can be left out unless you run the tests). Then point the command at the run folders (give each an id before the equals sign), the construct and the reference, and open the bundle it writes:

```bash
binder-review import run01=/path/to/run01 run02=/path/to/run02 \
  --target-pdb construct.pdb --reference-cif full_length.cif \
  --name my-target --out work/my-target --bundle bundles/my-target
```

```bash
open bundles/my-target/index.html
```

`binder-review app` starts the app from a terminal. Input folders are never written to. Every option and exit code is in [the command-line reference](cli.md).

## 2. Five ideas that make the pages click

- **Family. One backbone, several sequences.** BindCraft designs a backbone (one trajectory), then proposes several sequences for it. Binderscope counts **families**, never sequences, because sequences of one backbone share a fold, a pose and most of their risk: they are not independent bets. The funnel, the diversity estimate, the tiles and the order list all work on families. You see it as the tiles on the Board, the sequence table in the focus panel and the Siblings tab in the Inspector.
- **Target frame. The construct is not the protein.** BindCraft only sees the piece of the target you gave it. Binderscope puts every design back on the full-length protein, with the other subunits and the rest of the chain taken from your reference structure, so it can ask what the construct hid. A binder that crashes into the part BindCraft never saw gets flagged. You see it as the Target frame strip and the Target checks in the Inspector. BindCraft predicts and relaxes its own copy of the target, which differs a little from your reference, so how a design is placed matters: each design is fitted onto the reference by a superposition weighted around its own interface, which keeps the binder where it sits against the epitope. A fit over the whole target can leave it 1 to 3 Å off where the two copies differ (it is the option *Fit the whole target*, `--placement whole`). The interface numbers (contacts, hydrogen bonds, buried area) come from the design's own structure and do not depend on this; the distances to the other subunit, the rest of the protein and the sugars, and the Context verdict, do.
- **Intended site. Hotspots are the plan.** The hotspots in your run settings define the site you meant to hit. A design that touches none of them is marked off-site ("wrong site" in the Inspector), however good its scores. The hotspots come from the settings JSON of the run or, without it, from the hotspot column of the CSVs. A run recorded without any hotspot has no intended site, and the pages say so (see [when data are missing](#7-when-data-are-missing)).
- **Runs. Never pool the estimates.** Different hotspots, lengths or targets make different experiments. On the Board the funnel, the diversity count and the "Do you need more designs?" estimate are made per run; tiles from all runs can still sit in one list, and the other pages show per-run counts next to the pooled view. Give every run its own name when you add it (`run01=...` on the command line), and pick one run in the header to see its own numbers.
- **Screens. Every score is a screen.** Checks flag what deserves a look. They are not verdicts, and a design that passes them is a predicted binder, not a measured one. The Inspector's Target checks and Specificity rules say which experiment would confirm or refute each flag.

## 3. The pages

The Overview is the landing page and the header links every page. With more than one run, **RUNS** in the header picks one run or all, and the choice follows you from page to page. **What to try** (on the Board, Inspector, Map and Query pages) gives hints for the page you are on. A design id on the Map and Query pages or in the Board's order list opens that design in the Inspector. In the app, a **‹ Projects** link at the left of each page's header leads back to your projects. The pages follow your system's light or dark setting; add `#dark` or `#light` to a page's address to force one (this guide has its own Theme button). Everything is drawn from files in the bundle: no page needs a network.

Each page below has a table of its features: the feature, the question it answers and how to use it. Order of reading for a first review: Overview, Board, Query, Inspector, Map.

### Overview

The first page of a bundle. It counts what stands out in your data and links to every page. Its counts, cards and highlights always cover all runs and were fixed when the bundle was built; the **RUNS** buttons in its header do not change them, but the choice you make there applies to the other pages.

| Feature | The question it answers | How to use it |
|---|---|---|
| **Target and chips** | What is this bundle built on? | The target name (from the UniProt record when you gave an accession; otherwise a generic "Target") with chips for the reference structure and chain, the UniProt entry (when you gave an accession), the construct and the hotspots. |
| **Page cards** | Where do I go next? | One card per page, each with a count from your data (families, designs to inspect, binding modes, designs to query; they count different things: accepted designs, plus near-misses, plus all rejected designs that have a structure). The last card opens this guide. |
| **What stands out** | What in my data deserves attention before I start? | Facts counted from your runs, each with a link to the page or design where you can look at it: designs that fail a context check, which hotspots the designs touch, how the runs differ, how diverse each run is, how a scaffolded run's interface divides between its designed segments and its framework, how many rejected designs are near-misses, how conserved the contacted residues are. They are screens, not verdicts, and they are fixed text from build time: they ignore the run filter and any threshold you change later in the pages. |
| **Runs** | What did each run contain, and how do the runs differ? | One row per run: accepted and rejected counts (the rejected ones marked *with structures* or *scores only, no structures*), families, hotspots, the construct as that run saw it (with the stretches it removed) and its length range and settings. An *experimental reader* tag marks a BindCraft 2 run that the reader has not been verified on (see [when data are missing](#7-when-data-are-missing)); the Build panel's *Warnings* row says why. Runs with different settings are never pooled in an estimate. |
| **Funnel** | How many trajectories survived each stage? | Per run: trajectories attempted (a count for a BindCraft 2 run, which lists every trajectory it started; *unknown* for a BindCraft 1 run, because the files Binderscope reads do not record it, with an estimate from BindCraft's counters marked *est.*), completed and scored, backbones with a scored sequence, sequences scored, accepted designs, accepted backbones. Hatched bars are estimates, dashed bars are unknown; unknown is never drawn as zero. |
| **What this bundle carries** | Which checks are on, and which are switched off? | A row of chips for what the data contain: backbone families, generation order, fold similarity, rejected designs with structures, other-subunit and rest-of-protein context, glycan sites, membrane plane, species and paralog panels, lab outcomes, PAE matrices, ipSAE, independent co-fold. A struck-through chip means the checks that need it are off. Under the chips the panel says why: no reference structure was given (so nothing outside the construct was tested), or the reference held nothing to test against (the checks ran on nothing). Neither is a pass. Checks that need data a bundle does not carry are switched off, never guessed. |
| **Your review** | How far am I? | The number of decisions the current reviewer has made, the reviewer name, and **Save review file** and **Load review file**. Loading here shows no message; use the Board's Share dialog for feedback. The count is for the reviewer whose name is in the field. |
| **Build** | Where did this bundle come from? | Tool version, build date, the bundle id (review files refer to it), the number of input files with their hashes in the manifest, whether the counts match the run CSVs (compared for BindCraft 1 runs only; with none the row is left out), how many recomputed metrics were compared with the values the design tool wrote, warnings, and a privacy line that says no binder sequence or design id left the machine. |

### Family board

One tile per backbone family. It answers: *how diverse is what BindCraft gave me, do I need more designs, and which families deserve a closer look?*

Top to bottom the page has three summary panels (**Run funnel**, **Backbone diversity**, **Do you need more designs?**; for a campaign built on a fixed scaffold, **What the designs bind with** takes the place of Backbone diversity), then **Where a tag can go** where the bundle carries the terminus analysis, a toolbar with a progress counter, the grid of tiles on the left, and on the right the focus panel for the selected tile, the reviewer model, the review queue and the order list. A hint bar at the bottom lists the keys.

| Feature | The question it answers | How to use it |
|---|---|---|
| **Run funnel** | What did each run produce, and where did designs drop out? | Read one column per run, never pooled: trajectories, scored sequences, accepted designs, families. "unknown" means the run folder does not say; "est." marks an estimate. Pick one run under **RUNS** for a bar chart and the filters that failed most. |
| **Backbone diversity** | How many different ideas do I have? | Counts design clusters per run. **Count by** (Backbone, Both or Binding site) and **Same cluster if** (0.30 to 0.70, default 0.50) set what counts as alike. Effective number of clusters, singletons and the largest cluster's share describe their sizes; *Modes* counts the binding modes. A campaign built on a fixed scaffold does not get this panel: every design is the same fold, so the count would measure the scaffold (next row). |
| **What the designs bind with** | On a scaffolded campaign (a VHH, scFv, Fab or ankyrin repeat): which parts of the scaffold touch the target, and what varies between designs? | Shown instead of Backbone diversity, and its first line says why: every design is the same fold, so fold similarity would measure the scaffold, not the designs. Two bars divide the interface residues between the designed segments (CDR1 to CDR3, H1 to L3, or *designed segment 1, 2...* where there are no CDRs) and the framework, accepted designs over rejected ones, with the framework positions that still make contact. Where the campaign set an off-paratope filter, a framework share that is small in the accepted designs and large in the rejected ones is that filter at work. *What varies between designs* follows: how many region-use profiles the accepted designs show, the length classes of the loop the campaign let vary (usually CDR3, one dot per family), and how much the epitopes of the families overlap and which target residues all of them touch. An axis the data cannot support reads *not measured* or *not shown*, with the reason. Regions are the campaign's own edit spec, not IMGT or Kabat numbering, and the residues are BindCraft's own interface list. The percentages count designs, and siblings of one backbone are near copies in the paratope, so read the family counts beside them. |
| **Do you need more designs?** | Will more designs from these settings add new ideas? | Gives the chance that the next passing family is a new cluster (one minus coverage) with a plain label: *Too early to judge* (fewer than 10 families), *Still discovering* (coverage below 0.7), *Diminishing returns* (below 0.9) or *Saturated for this setup*. Below 30 families it is rough. Pick one run for the accumulation curve, the forecast and the **Replay** slider, which shows what the tool would have said earlier in the run (this needs the generation order of the run). For a scaffolded campaign the clusters are counted by binding site alone, because fold is the same for every design (a line under the panel says so), so its coverage is not comparable with a de novo run's. |
| **Where a tag can go** | Which end of each family can carry a tag or a fusion, and would that hold the binder by its binding face? | One block per run. A row per tag size (His6, Twin-Strep, a GFP-sized domain, an Fc) splits the run's families into C, either, N and neither; click a row, or the **Tag** buttons in the toolbar, to make that size the one the tiles, the focus panel and the order list use. The notes below say what the modality's convention is and how many families the geometry agrees with it on, what the campaign was asked to do about the ends, and whether the ends were checked against the full-length protein or against each design's own target only (the latter without a reference structure). Counts are of families; where a family's sequences disagree it is judged by what most of them say and marked `*`. A prediction from one predicted conformation, not a measurement. |
| **Tiles** | Which families deserve a look first? | Each tile shows the family's best design on the target with the hotspots in orange, its binding mode (M1...), cluster (C1...), verdict (PASS, REVIEW or NO PASS), i_pTM, hotspots touched (3/4), BindCraft's scores and *p 98%*, the reviewer model's chance that you keep it. The small boxes are the family's sequences, coloured by verdict. An *off-site* mark means it touches none of its run's hotspots, and a *tag C-term* pill (or *tag N or C*, *no tag site*) gives the end to tag for the tag size in force. The muted line mixes BindCraft's scores with values Binderscope computed (marked *calc.*). |
| **Sort, Group by, Colour, Unit** | In what order and grouping do I want to walk the tiles? | **Sort**: Learned (by the model's keep chance), Quality, Generation order, Group. **Group by**: Cluster or Mode (switches to Group sort). **Colour** of the binder in the tile thumbnails: Chain, pLDDT, Footprint. **Unit**: Families or Sequences (decide per backbone or per sequence; sequence decisions are kept separately from family decisions and the model starts again). |
| **Filters and find** | How do I see only what is left, or one design? | **Undecided only** and **Hide rejected** (rejected by you; on by default) narrow the grid; `/` focuses the find box, which matches design and family ids; `Esc` clears it. |
| **Focus panel** | Why does this family have its verdict? | Click a tile. The panel gives the verdict with a *because* list, a 3D view (drag to rotate, wheel to zoom), the K, M and X buttons, the sequences on this backbone (with the **Order** checkbox), one row per check (Fold, Interface, Pose, Chemistry, Epitope, Sequence, Context, Specificity) with the reason in words, a **Tag and fusion** section with each end's grade and distance, and the reason tags. Specificity is information only and never changes the verdict. **Inspect**, `Enter` or a double-click opens the Inspector. |
| **Keep, maybe, reject, reasons** | How do I record a call and why? | `K`, `M`, `X` decide for the focused family; the same key again clears it; the focus moves to the next undecided tile. `1` to `7` tag a reason (the list comes from the profile; with the default profile: wrong site, small interface, floppy or loopy, sticky surface, low confidence, clashes with the other subunit, sequence liability). `U` undoes the last decision. |
| **Reviewer model** | Can the page learn what I like and surface the families I would keep? | A small model learns from your keeps and rejects (maybes do not train it) and reorders the board while Sort is on Learned. The panel shows how many decisions it has used and its largest weights. `L` or the **Learning** switch freezes the ranking; decisions are always recorded. |
| **Review next, Worth a look** | What should I look at next? | Two short lists: the undecided families the model expects you to keep, and the ones it is unsure about. *Review next* lists the four undecided families with the highest expected keep, ranked from the start by a quality prior. *Worth a look* lists up to three the model is unsure about; it is empty while learning is off and says it appears after a few decisions. Both ignore the toolbar filters. |
| **Compare** | How do two or three candidates differ side by side? | `C` adds the focused item, then press **Compare** (up to five items on one target, a table in which the best value in each row is underlined). |
| **Order list** | Which sequences am I taking to the lab, and how well do they cover the field? | One or two sequences per kept family (the best by default; tick **Order** in the focus panel to change it). Only kept families are on it, not maybes, and it ignores the run filter. A note under the heading counts the families, clusters and binding modes covered; if you rescued near-misses on the Query page, a dashed line under that note counts them and links back there. A sentence after that note counts how the list divides between C-terminal, N-terminal, either and no tag site for the tag size in force. **Copy FASTA**, **FASTA file**, **CSV**, **Tag plan CSV** (one row per design with the end to tag for each tag size and the evidence behind it), **Save review file** and **Load review file** are here. |
| **Share** | How do I keep, move or hand over my decisions? | **Share** (header) and **Share review** (order list) open a dialog with Save and Load review file, the reviewer name, a reviewer picker and a note on whether decisions persist. |
| **Clear decisions** | Can I start over? | Asks first (a bar under the toolbar), then clears all of yours; nothing is deleted from the log. Press `U` at once to restore them, or load a saved review file later. |
| **How to import a run** | How does a run get into this? | Explains the build command and lists what the build read. It does not import anything itself. |
| **Help and What to try** | What are the keys, and what should I try on this page? | `?` lists the shortcuts; **What to try** in the header gives hints for the page. |

> **Warning: Easy to miss on the Board.** **Hide rejected** is on by default, so a tile you reject disappears at once (the counters still count it). *Rejected* here means rejected by you; BindCraft's rejected sequences and the verdict NO PASS are different things. With Sort on Learned, tiles you have decided come first and the rest follow by the model's keep chance, so the order changes as it learns; *p 98%* is the model's guess of *your* decision, not a binding probability. The number keys tag the focused item if it is decided, otherwise the one you decided last, and tags are not undone by `U`. Undo works only until the page reloads or you load a review file. The toolbar settings, the cluster slider and the find text are not stored. A link such as `board.html#f=<family id>` can change the run filter and untick the filters without asking. The verdicts are Binderscope's own checks: a design BindCraft rejected can read PASS, an accepted one can read NO PASS, and an off-site design reads REVIEW, not NO PASS.

> **Tip: Decisions belong to families.** `K`, `M` and `X` decide for the whole backbone family; the sequences you actually order are chosen with the **Order** boxes in the focus panel. Switch **Unit** to Sequences if you prefer to decide sequence by sequence.

### Design inspector

One design in depth, with findings linked to the 3D view. Open it with `Enter` or **Inspect** on the Board, or from a design id on another page. The address ends in `#d=<design id>` (for example `inspector.html#d=binder_l68_s123456_mpnn3`), so a link opens the same design. Links such as `board.html#f=<family id>` and `map.html#m=M3` do the same for a family and a mode.

| Feature | The question it answers | How to use it |
|---|---|---|
| **Design list** | Which design do I look at next? | **Prev**, **Next** or `←` and `→` step through the list; `/` focuses the filter box (id, family, run, mode, status or verdict words) and the chips narrow it: Clean, Issues, Fix first, Accepted, Near miss, Undecided. It holds accepted designs and BindCraft's near-miss rejects. With a handful of designs you get one button each instead. |
| **Verdict strip** | What is the bottom line, and what should I fix first? | Two pills. The first is the Board's PASS, REVIEW or NO PASS for this design (a BindCraft near-miss has none). The second is the Inspector's finer reading: *Clean*, *Promising, with n issues*, or a fix-first label such as *Wrong site for this campaign*. The finer reading also names single warnings that the Board tolerates, so a PASS can come with "Promising, with 2 issues". A line says why; when several findings are bad, the label follows the first one in the page's order (wrong site first), not the worst. Read the findings, not the label. |
| **Meters** | How does each number compare with its limit? | One tile per score, with the value, a bar and a tick at the limit: BindCraft's own scores first, then the gaps to the other subunit and the rest of the protein, shape complementarity, ΔG, buried area and unsatisfied polars. A tick passes, a triangle is a warning (it passes close to the limit), a cross fails. The limits are those of the threshold preset in force, except that BindCraft's own scores use BindCraft's filter limits, which for a BindCraft 2 campaign are the ones it recorded (the tooltip says *campaign*). |
| **Decision bar** | How do I record my call here? | **Keep**, **Maybe**, **Reject** (keys `K`, `M`, `X`) decide for the design's family, as on the Board; `U` undoes. On a kept family, `K` adds or removes this sequence from the order list. Links lead to the family on the Board, the near-miss list on the Query page and the mode on the Map. |
| **Structure** | What does the pose look like, and where is the evidence? | A 3D view: drag to rotate, wheel to zoom, click a residue to fly to it, hover for its details. The target is the design's own copy of it, as BindCraft modelled it and placed on the full-length protein, so the binder, the contacts and the hydrogen bonds are drawn exactly against it; the other subunit, the rest of the protein, the removed regions and the sugars come from your reference structure. **Colour by** Chain, pLDDT, Interface, Hydrophobicity or Conservation (needs ortholog data); a design on a scaffold also offers **CDRs** (**Segments** where the scaffold has no CDRs), which colours each designed segment and the framework (on a VHH, the FR2 face apart) and is how it opens. Layers: interface residues, H-bonds, salt bridges, hotspots, unsatisfied polars, hydrophobic patch, the target surface, and, when the data exist, glycans, the other subunit and the rest of the protein (drawn as ghosts; the rest of the protein starts on, glycans and the other subunit start off). **Pull apart** slides the binder away from the target. Click a finding and its residues light up. Without WebGL you get a simplified view, which draws the reference chain as the target. |
| **Findings tab** | What did the checks find, and what would settle it? | A list sorted *Fix or explain*, *Worth a look*, *Context*, *Looks good*. Click one: the 3D view flies to the evidence and a suggested next step opens. Above the list: the **Target frame** strip and the **Target checks** (below). For a design on a scaffold the list also says which designed segments touch the target, how many framework residues sit in the interface (numbered as in the scaffold) and, for a VHH, what became of the *FR2 face*, the exposed face of framework 2 that sat against the light chain in a conventional antibody. A changed face with no contacts is more likely the design making that surface less hydrophobic than part of the paratope. |
| **Tag and fusion** | Which end of this design can carry a tag, and what would it cost? | A card at the top of the Findings tab. The headline names the end to use for the tag size in force (**His6**, **Twin-Strep**, **GFP / SNAP**, **Fc**; the buttons switch size, and the choice follows you to the Board). Under it a column per end with its grade — *fits as it is*, *needs a linker of about n residues*, or *no room, even with a 15-residue linker* (or *the end itself touches the target*) — a chip per tag size, and the evidence: distance to the target, which way the chain leaves the fold, whether the end sits on the paratope side (a tag there holds the binder by its binding face, which matters for SPR and BLI), burial, confidence and the room around it. Hover an end to see it in 3D as a sphere with an arrow for the exit direction; click to fly there. The two unstructured tags are judged by whether a chain of their length can route to open solvent, the two folded fusions by whether a sphere of their size fits in the cone the chain can leave in. A design whose molecule has no free end (a cyclic peptide) says so instead. |
| **Target frame strip** | Where does the construct sit in the full-length protein? | A strip of the full-length protein: its domains (when the profile lists them), your construct, the stretches this run removed (hatched), the cut ends, the hotspots, the contacted residues, the interface with the other subunit and glycan sites. |
| **Target checks** | Does the binder fit on the full-length target? | Cards for the other subunit, the rest of the protein (nearest atom, atoms within 3, 4 and 6 Å, residues it would bury), cut ends, glycan distance and membrane side, as far as the data exist. Each card shows its numbers; click it for a suggested next step and what would confirm or refute it, and the 3D view flies to the residues involved. |
| **Interface tab** | How is the interface built? | A contact diagram (target left, binder right; H-bonds, salt bridges, other contacts), the buried area per residue and the ten largest contacts. For a scaffolded design, *Paratope by segment* sits above the diagram: one bar per segment with its interface residues (BindCraft's list); click a segment to see its residues in 3D. |
| **Confidence tab** | Where is the prediction trustworthy? | pLDDT along the chain and the predictors block. A PAE matrix appears only when the data carry one; BindCraft writes none. |
| **Specificity tab** | Would it bind other species or related proteins? | A *Reading* of the specificity rules (cross-species reactivity and off-target risk, each with the assay that would settle it; it appears only when your profile defines such rules), epitope conservation, a species table with the residues that differ, paralog bars, the residue-by-residue alignment and four stickiness proxies (exposed hydrophobic patch, charge, interface hydrophobicity, aromatic and Arg share). A sequence screen, not a measurement. Needs species and paralog data (see [when data are missing](#7-when-data-are-missing)). |
| **Siblings tab** | What do the other sequences on this backbone look like? | The sequences on the same backbone with their scores and a bar of the positions that differ from the first one. A rejected sequence is listed but never suggested as better. |
| **Sequence tracks** | Which residues carry the issue? | Per residue: sequence with interface residues underlined, structure, segments (for a scaffolded design), pLDDT, buried area and flags (unpaired Cys, liabilities, sticky patches), then for the target the numbering, hotspots, contacts, conservation and paralog identity. Hover a residue to see it in 3D; click to fly there. Residues the run removed are hatched. |
| **Rejected near-misses** | Why did BindCraft reject this one? | A near-miss design has a finding that lists every filter it failed, with value and limit. It is decided per design, not per family. |

> **Warning: Easy to miss in the Inspector.** Two numbers carry the name *unsatisfied polars*: the meter *Unsat. (mean)* is BindCraft's own Rosetta count, while the findings, the Siblings tab and the Board use the count Binderscope recomputes from the structure, so they can disagree. For a BindCraft 1 run the limits on the meters are BindCraft's *default* filter values as Binderscope reconstructs them (BindCraft 1 does not copy its filter file into the run folder), so ticks do not follow custom filters you ran with; a BindCraft 2 campaign records its thresholds and the meters use them. The meter named *Binder pLDDT* is the complex pLDDT; BindCraft's binder-alone pLDDT is a different number. Contacts and H-bonds are drawn on the reference chain, not on your construct, so they can differ slightly from the numbers in the findings. Filters, chips, the tab, colour and layers reset on reload; `U` undoes only decisions made since the page loaded; a run filter in the header silently hides other runs' designs from the list and from arrow stepping.

### Binding-mode map

Where on the target the designs bind, and whether you are covering one site many times or several sites once. With several runs, pick one under **RUNS** to see its own hotspots, removed regions and counts; with all runs the view is pooled and descriptive, and per-run counts sit under the funnel.

| Feature | The question it answers | How to use it |
|---|---|---|
| **Funnel and Thresholds** | Which limits take designs away, and how many are left? | One button per stage, from the accepted designs to Diverse picks, with the count that passes and the limits in words (Fold confident, Interface confident, Pose-consistent when the data allow, Fits its surroundings when there is full-length context, On the intended epitope, which a run without hotspots passes by default). Click a step to fade the designs that fail it. The **Thresholds** select holds the built-in presets (Literature-informed, BindCraft-style, Strict) and any preset in the profile; keep it on the default unless you mean to change the screen. Changing it recounts the funnel and the picks. |
| **Show rejected** | Where do BindCraft's rejected designs sit? | Draws them as hollow marks you can hover and click. They never count in the funnel, the picks or the stats. Disabled when the runs in view have none with a structure. Key `R`. |
| **Epitope map** | Which target residues do designs contact, and where are the hotspots? | The target in 3D coloured by contacts per residue (**Contacts**) or by conservation (**Conservation**, needs ortholog data). **Binders** overlays up to seven of the best designs in view; **Surface** shows the target alone. Each run's hotspots are ringed and tagged with the run when only some runs have them; glycan sites are diamonds; residues a run removed fade out. Drag to rotate, wheel to zoom, double-click to reset. |
| **Contact strip** | Which stretch of the sequence is popular? | Contact frequency by residue along the target, below the 3D view. Drag across it to keep only designs that touch that region (a chip clears it). Residues a run lacks are hatched. |
| **Binding modes** | Do my designs bind alike? | Designs placed by the target residues they contact, so close marks bind alike. Each shaded group is a mode (M1, M2... by size, at most seven), named from the up to three residues that set it apart (for example `K48/D52/T55 site`, invented) and, when none of them is a hotspot but one lies near, that hotspot. Views: **Map** (key `M`), **Fingerprints** (`F`: one row per design, one column per residue), **By mode** (`B`: a heat map, one row per mode) and **Table** (`T`: the best 80 designs in view). Marker size is the headline score; small and faint means NO PASS; a dashed ring marks a design that touches none of its run's hotspots. Drag over empty space on the map to select a custom group. |
| **Legend** | Which modes are in view? | One button per mode with its count (in view / all). Click to select it; keys `1` to `9` pick the nth mode (again to clear), `←` and `→` step through them. |
| **Mode detail** | What is this mode, and is it a good bet? | For the chosen mode, a custom selection or all modes: counts (in view, PASS or REVIEW, median headline score, on the epitope), the residues touched most, **Hotspots touched** per hotspot, **Context** (closest approach to the other subunit and to the rest of the protein), **Specificity** (conservation and identity bars, when the data exist), and the four best designs. Design ids link to the Inspector. |
| **Quality-diversity picks** | If I can only take N designs, which N cover the most ground? | Set **Budget** (4 to 24), **Diversity** (0 to 1) and **Pool** (designs on the intended epitope, or any confident design that fits its surroundings). The page picks designs that are good *and* different from each other, treating sequences of one backbone as near-duplicates. **Add picks to shortlist**, **Show only picks** and **Copy FASTA** are the buttons. |
| **Star shortlist** | Can I mark candidates while I look around? | A star adds a design to a shortlist that lives in the page only: it is not saved, it is lost on reload and it does not feed the Board's order list. **Copy FASTA** is the way out; record decisions on the Board or the Query page. |

> **Warning: Easy to miss on the Map.** The first mode is preselected when the page opens, so the 3D colours, the strip and the mode detail describe that mode only: click its legend chip (or press its number) again to see all modes. A funnel step and a strip region both fade designs everywhere (counts, table, by-mode heat map) and only the region chip and the "N of M designs in full colour" note remind you. The quality-diversity picks ignore the selected mode, brush, region and funnel step; they depend only on Thresholds, the run filter, Pool, Budget and Diversity. Residue numbers are those of the full-length reference, not of your construct, and the 3D view draws the construct only. Nothing on this page is saved except the run choice: the selected mode, view and preset are lost on reload.

### Structure query

Rules instead of clicking: stack conditions, watch the funnel shrink, rescue near-misses. The page answers *which designs satisfy these conditions, and which ones miss by only one?* With several runs, pick one under **RUNS** to scope the counts and the hotspot chips to it.

| Feature | The question it answers | How to use it |
|---|---|---|
| **Ask** | Can I say what I want in words? | Type it, for example *"on the hotspots, clear of the rest of the protein"*, and press **Compile to rules** (the chips under **Try** are examples). It matches keywords to the same rules as the stack; it is not a language model and sends nothing anywhere. A new sentence replaces your rules except the base confidence screen and BindCraft's filters unless you tick **Add to current rules**. Naming a residue (`A45` requires it, `A45 or G60` requires one of them, `avoid A45` forbids it) replaces the required set, and a note says so, with a **Restore the default hotspots** button. **Clear all rules** turns every rule off, BindCraft's filters included, and has no Undo; **Reset to defaults** resets all rules and the residues you required or forbade (not the pool, the tab or the run filter); **Undo** appears after a compile or a rule-set load. |
| **Rule stack** | What does each condition cost me? | Rules sit in groups (Confidence, Epitope and geometry, context, Specificity, Interface chemistry, Sequence, Run and family, BindCraft filters). Tick a rule, drag its limit, or click its name for a histogram and what relaxing or tightening would change. The count beside it is how many designs pass and how many only it removes ("kills 4 alone"). A design with no value for a rule passes it. The **preset** select applies the limits of a preset (Literature-informed, Relaxed, Strict, and any the profile adds) and turns the matching rules on. |
| **BindCraft filters** | Can I reproduce BindCraft's own accept and reject? | Each filter BindCraft applied becomes a rule with its own number. With only these on, the stack reproduces BindCraft's decision, except where BindCraft judged a design on a single validation model (see the FAQ). Loosen one to see which designs it was holding back. Each rule's note says how well the limit is known or, for a BindCraft 2 campaign, that it is the limit the campaign recorded. |
| **Funnel** | Where do the designs go? | The pool, then one row per group of rules with the designs left after it and the loss. Under it, **Rules blocking the most near-misses**: the rules that alone remove the most designs; click one to see them. **Copy recipe**, **Save** and **Load** sit in the header of this panel: Copy recipe puts the rule set on the clipboard as JSON (a format Load does not read back). |
| **Pool** | Do I include designs BindCraft rejected? | Switch between *Accepted* (default) and *+ BindCraft-rejected* (the rejected designs that have a structure). The funnel, the counts and the lists follow the pool. |
| **Results** | Which designs are left, and which just miss? | Three tabs: **Survivors** (fail no rule), **Near-misses** (fail exactly one rule) and **Failing 2+ rules**. Survivors are tiles; the others are tables. Click a tile or row to draw the design on the target. Design ids in the two tables and in the panel under the 3D view link to the Inspector; Survivors tiles are not links. The lists are cut at 60 tiles, 40 near-miss rows and 60 failing rows, while the tab counts are not. |
| **Send to board** | How do the results become decisions? | Tick designs (or leave none ticked for the whole tab), choose Maybe or Keep and press **Send n to board**. Decisions you already have are left alone, and the note records the rules that were on. A Keep sent from here counts like a Keep on the Board: it trains the reviewer model and fixes that family's order-list sequences. |
| **Rescue as maybe** | Which rejected designs deserve a second look? | A BindCraft-rejected design tagged *near-miss* missed exactly one of BindCraft's filters by at most 25% of its threshold. **Rescue as maybe** records it with the missed filters as the reason. **BindCraft near-misses only** narrows the tab. Rejected designs are not tiles on the Board, so a rescued design lives in your review file and is exported from this page, not from the order list. |
| **CSV and FASTA** | How do I take a list away? | **CSV (n)** and **FASTA (n)** act on the ticked designs or, with nothing ticked, the whole tab. The CSV carries ids, run, status, family, rank (the page's own quality rank, not BindCraft's), mode, length, quality, hotspots touched, the metrics, liabilities, the rules failed here and BindCraft's failed filters; it never contains a sequence. FASTA does. Both are made in your browser. |
| **Pick residues on the target** | Can I choose residues by pointing at them? | The target in 3D. **Require** makes a click mark a residue that designs must touch (orange; it feeds the Hotspots rule); **Forbid** marks one they must stay off (red). The best survivors are drawn as thin lines. |
| **Save, Load, Copy recipe** | How do I keep or share a rule set? | In the Funnel panel header. **Save** writes `binderscope-rules-<profile id>.json` (numbers and ids, no sequences and no design ids); **Load** applies the rules it knows, clamps values to each rule's range, and counts what it skipped. **Copy recipe** copies the rule set as JSON for pasting elsewhere; Load does not read that format. |

> **Warning: Easy to miss on the Query page.** A sentence the Ask box does not recognise, with **Add to current rules** unticked, still resets and switches off every rule outside the Confidence group and the BindCraft filters, says *Nothing recognised* and offers no Undo. Residues you name stay the required set until you press **Restore the default hotspots** or **Reset to defaults**; names must be a capital letter and a number in the numbering shown on the page (lowercase or wrong letters are ignored). Several rules are on from the start and silently hide designs (the header says how many rules are on), and choosing a preset switches rules on without an Undo. The word *near-miss* has two meanings: a design that fails exactly one rule of *your* stack, and BindCraft's own tag (one filter, at most 25%); a tagged near-miss can sit in *Failing 2+ rules*. **Send to board**, **CSV** and **FASTA** take the whole tab when nothing is ticked, and your ticks persist across tabs. Nothing on the page persists across a reload.

> **Note: The Query page has no letter shortcuts.** Everything is reachable with `Tab` and used with `Enter` or `Space`. Each list (rule names, tiles, rows) is one Tab stop; the arrow keys, `Home` and `End` move inside it. `Enter` in the Ask box compiles. Opening `query.html#pool=all&tab=near` starts on the all-designs pool and the Near-misses tab.

## 4. A 15-minute review recipe

The order that wastes the least of your time. It assumes a bundle with one or two runs and a few dozen families.

1. **Judge the campaign before the designs.** On the Board, read **Do you need more designs?** for each run. *Still discovering* means more designs will pay off; *Diminishing returns* or *Saturated* means your time is better spent choosing. Treat the estimate as rough below about 30 families. On a scaffolded campaign read *What the designs bind with* too: fold is not counted there, so coverage describes binding sites only.

   *Board · funnel and diversity*

2. **Remove what cannot work.** In Query, switch on the rules that matter for your target: touches a hotspot, clear of the other subunit, clear of the rest of the protein. Look at what disappears and why, and open **Near-misses** to see what a single rule costs you. To mark the survivors on the Board, **Send to board** as Maybe.

   *Query · rule stack*

3. **Walk the families.** Back on the Board, step through the tiles with the arrow keys. Press `K`, `M` or `X`, and right after a reject tag a reason with `1` to `7`. Keep it quick: families you cannot decide on become maybes.

   *Board · tiles*

4. **Look hard at the maybes.** Open each in the Inspector. Read the headline, the Target checks, the interface findings and the liabilities under Worth a look. Decide.

   *Inspector*

5. **Check specificity.** For every keep, open the Specificity tab. If you need cross-species reactivity, read the orthologs; if off-target binding worries you, read the paralogs. This needs species and paralog data (see [when data are missing](#7-when-data-are-missing)).

   *Inspector · Specificity*

6. **Spread your bets.** The note under the Order list says how many families, clusters and binding modes your keeps cover. If they pile up in one mode, look at the Map's Quality-diversity picks and swap one or two keeps for picks from other modes.

   *Board · order list, then Map · Mode detail*

7. **Export and save.** Copy the order list as FASTA or export it as CSV or a FASTA file, and save the review file so you can reload it later.

   *Board · order list*

## 5. More workflows

Five common jobs, each as a short sequence of steps. They use the same pages as the recipe above.

### Choosing designs to order

When lab slots are limited, the question is not "which designs score highest?" but "which set of designs gives me the best chance that at least one of them binds?"

- **Diversify. Spend slots on different ideas.** Pick across families and binding modes. Two designs from one backbone share most of their risk; two from different modes share very little.
- **Context. Remove what cannot bind.** Designs that collide with the part of the target BindCraft never saw, or sit on the wrong face, cost a slot and teach you little.
- **Hedge. Keep near-misses in play.** A design BindCraft rejected by a hair, on the right site, can be a better bet than a polished one on the wrong site.
- **Record. Write down why.** Reasons on every keep and reject, plus the saved review file and the bundle, are the rationale you will want after the results come back.
- **Decide the tag with the design, not after it.** The end a construct is tagged at is part of the order. **Where a tag can go** on the Board, and the **Tag and fusion** card in the Inspector, say which end each design can carry a His6, a Twin-Strep or a larger fusion at, and whether that end is on the binding face; **Tag plan CSV** takes the answers with you. It is a prediction, so read the evidence before you commit a construct to it.

1. **Fix the budget and the risks.** Write down how many sequences you can order, and which risks matter most to you: the wrong site, a collision with the rest of the protein, cross-species reactivity, off-target binding, sequence liabilities.

   *Before you open a page*

2. **Screen with rules.** On Query switch on the rules that express those risks (hotspots touched, the context rules, specificity, sequence). Read the funnel for what each group costs, use "kills n alone" to see which rule holds designs back, and open the histogram of a rule to see how narrow the margin is. **Send to board** the survivors as Maybe.

   *Query · rule stack and funnel*

3. **Decide per family.** On the Board keep the families you would defend, reject with a reason, leave the rest as maybes. One strong sequence from many families beats several sequences from one.

   *Board · tiles*

4. **Check the spread.** Read the note under the Order list: how many families, clusters and binding modes your keeps cover. Open the Map, set **Budget** to your number of slots and compare the quality-diversity picks with your keeps. Swap keeps that repeat a mode for picks from modes you do not yet cover.

   *Board · order list, Map · Quality-diversity picks*

5. **Choose the sequence.** In the focus panel tick **Order** for the sequences you want. The best sequence of a kept family is on the list by default; add a second only when you have slots to spare.

   *Board · focus panel*

6. **Hedge with near-misses.** Add one or two near-misses on the right site, following [Rescuing near-misses](#rescuing-near-misses). They are exported from the Query page, not from the order list.

   *Query*

7. **Check specificity.** For the final list open the Specificity tab of each design, if you have species and paralog data.

   *Inspector · Specificity*

8. **Export, then freeze.** Copy the order list as FASTA or CSV for the submission, save the review file, and archive the project folder next to it (in the app `Binderscope/<project>` in your home folder; it holds the review pages and the analysis for a rebuild). When results arrive you can reopen the same project and see what the checks said about each design. Binderscope does not import lab results for BindCraft runs: join your results to the order-list CSV by design id.

   *Board · order list*

### Checking one design in depth

1. **Open it.** Press `Enter` on a Board tile, click a design id on the Map or Query pages, or open `inspector.html#d=<design id>`.

2. **Read the strip and the meters.** The first pill is the Board's verdict, the second the Inspector's finer reading. In the meters look for the crosses first, then the triangles that sit close to their limit.

   *Inspector · verdict strip and meters*

3. **Work through the findings.** Start with *Fix or explain*, then *Worth a look*. Click each: the 3D view flies to the evidence and a suggested next step names the experiment that would settle it.

   *Inspector · Findings tab*

4. **Check the fit on the full-length target.** Under the Target frame strip open each Target check card. Look at the nearest atom, the atoms within 3, 4 and 6 Å and what the check says would confirm it. A rest-of-protein clash or a contact on a cut end is the most common reason a good-looking design is not what it seems.

   *Inspector · Target frame and Target checks*

5. **Look at the pose.** Colour by Interface, switch on H-bonds and hotspots, rotate, and use **Pull apart** to see what the binder sits on. Does the interface rest on a few residues? Do the hotspots sit in it?

   *Inspector · Structure*

6. **Read the other tabs.** **Interface** for the contact diagram and the largest contacts, **Confidence** for pLDDT along the chain, **Specificity** for the reading of cross-species and off-target rules, **Siblings** to see whether another sequence on the backbone is better.

   *Inspector · tabs*

7. **Find the residues behind a flag.** In the sequence tracks hover a flagged residue to see it in 3D. A liability in the interface is a different problem from one in a distant loop.

   *Inspector · tracks*

8. **Decide, and say why.** `K`, `M` or `X` decide for the family. Reasons are tagged on the Board with `1` to `7` right after the decision.

   *Inspector · decision bar*

### Rescuing near-misses

A near-miss is a BindCraft-rejected design that missed exactly one of BindCraft's filters by at most 25% of its threshold. BindCraft applies its AlphaFold2 confidence pre-filters before a sequence gets a row, so designs lost there leave no row and no structure and cannot be rescued; near-miss lists contain only failures of the filters that act on scored sequences.

1. **Add the rejected designs.** On Query set **Pool** to *+ BindCraft-rejected*. Only rejected designs that have a structure are in it; the Overview's Runs table says which runs kept them.

   *Query · Pool*

2. **Say what you cannot compromise on.** Switch on the rules for the site and the context (hotspots touched, clear of the other subunit and the rest of the protein). Leave the BindCraft filters on: they define what "near" means.

   *Query · rule stack*

3. **Read the near-misses.** Open **Near-misses** and tick **BindCraft near-misses only**. Each row names the rule that blocks it here and the BindCraft filters it missed, with value, limit and gap. A miss on a filter you do not mind (a surface hydrophobicity a hair over) on the right site is the classic rescue; a miss on interface confidence is a bigger risk.

   *Query · Near-misses*

4. **Look at a few.** Open the best candidates in the Inspector (the **Near miss** chip lists them) and check the context, the pose and the findings as for any design. The Inspector has no export; `K`, `M` or `X` there record a per-design rescue only.

   *Inspector*

5. **Rescue and export.** Press **Rescue as maybe** to record the design with the missed filters as the reason, then **CSV** or **FASTA** to take the list away. Rejected designs are not tiles on the Board, so rescued designs do not enter the Board's order list: a dashed line under it counts them and links back. Add them to your submission by hand.

   *Query · Rescue as maybe, CSV, FASTA*

### Comparing runs

1. **See how the runs differ.** On the Overview read the Runs table and the *What stands out* item about differences: hotspots, the construct as each run saw it, length range and settings.

   *Overview*

2. **Read each run on its own.** On the Board pick each run under **RUNS** in turn and read its funnel, its diversity and its *Do you need more designs?* label. Do not compare pooled counts; compare what each run found for what it was asked.

   *Board · RUNS*

3. **Compare where they bind.** On the Map pick a run and look at its hotspots, the residues it lacks (hatched) and the by-run counts in the mode detail. The By mode view shows which modes each run covers.

   *Map · RUNS, By mode*

4. **Compare what survives.** On Query scope a rule to one run (open the rule's histogram and choose the run) and read *Survivors by run* under the funnel.

   *Query · rule scope*

5. **Decide where to spend the next compute.** A run that is still discovering new clusters pays off with more designs. A saturated run needs different hotspots, lengths or a different construct, not more of the same.

### Sharing a review and merging

1. **Start from the same bundle.** Send the project's `bundle` folder (zipped), or let every reviewer build from the same inputs (the same inputs give the same bundle id). Review files carry that id.

2. **Set your name first.** In the **Share** dialog set the reviewer name before you decide anything. The default is "you", and two people with the same name cannot be told apart.

   *Board · Share*

3. **Review and save.** Each reviewer works through the Board and presses **Save review file**. The file is named after the reviewer (`anna.jsonl`) and holds that reviewer's events only. Keep a copy next to the bundle (a rebuild into the same folder deletes its `reviews/` folder). In the app the project folder also keeps a copy of your decisions.

   *Board · Share · Save review file*

4. **Collect and load.** The lead collects the files and presses **Load review file** for each (on the Board; the Overview's button shows no message). Events already present are skipped, so loading twice is safe, and events from another bundle are counted so you notice. The pages never read the `reviews/` folder by themselves.

   *Board · Share · Load review file*

5. **Look at one reviewer at a time.** The **Share** dialog shows a reviewer picker once the log holds more than one reviewer. The Board, the Inspector and the order list show the picked reviewer's calls. Binderscope does not merge calls into a consensus.

   *Share · reviewer picker*

6. **Merge by hand, then record the outcome.** To hand everyone one file, concatenate the files (`cat reviews/*.jsonl > combined.jsonl`): the union of events by id is the merge, and loading it brings in every reviewer. To agree a final set, compare the order lists (switch reviewer, export the CSV), discuss, then record the agreed calls under a new reviewer name such as `final` and save that file as the record.

   *Outside the page, then Board*

## 6. Reading the checks

A check is a question, not an answer. Here is what each asks and what would settle it.

The Board's verdict comes from these checks. **NO PASS** when a hard check fails (Fold, Interface, Pose, and Context only when the reference is an experimental structure; see below). **REVIEW** when a soft check fails (Chemistry, Epitope, Sequence, and Context otherwise) or two or more checks warn (soft checks, Pose and Context all count). Otherwise **PASS**. A check that was not run, for want of a reference structure, is not a pass: the Board's Context row reads *not checked* and says why. Specificity never counts. The limits are those of the threshold preset in force; with the default profile that is BindCraft's own default filter set, quoted below. The preset is set in the profile: the Board and the Inspector have no control for it, while the Map's **Thresholds** and the Query page's preset select apply another preset (Literature-informed, Relaxed, Strict) to their own counts only. If you write your own profile, set its default preset: a profile that names none falls back to the stricter literature-informed set (i_pTM at least 0.70, i_pAE at most 0.30, pTM at least 0.75, three hotspots), under which designs BindCraft accepted can read NO PASS.

| Check | What it asks | What would confirm it |
|---|---|---|
| **hard** Fold | Is the binder confidently predicted in the complex? Its mean pLDDT in the complex model at least 0.80 and pTM at least 0.55 (BindCraft's averaged scores). | A second predictor, or more seeds. |
| **hard** Interface | Is the interface confidently predicted? i_pTM at least 0.50 and i_pAE at most 0.35 (BindCraft writes PAE divided by 31; the pages show it in Å, where 0.35 reads about 11 Å). With a co-folding re-score, ipSAE and minimum iPAE are used instead. | A second predictor, or more seeds. |
| **hard** Pose (called Refold in some profiles) | Does the binder keep its shape when predicted alone? Binder RMSD against its complex shape at most 3.5 Å. With BindCraft data alone this check can only warn, so it cannot give NO PASS by itself; it fails when a re-score of the complex by another predictor disagrees with the design. | An independent re-score of the complex. |
| **advisory** Context: other subunit | Do binder atoms sit inside or touch the other subunits of the assembly? Fails at 10 or more binder atoms within 3 Å. Warns for any atom within 3 Å, a closest approach under 4 Å, or three or more contacted residues buried at the other subunit. | The binder predicted on the whole assembly, or binding to the purified assembly. |
| **advisory** Context: rest of the protein | Do binder atoms run into parts of the full-length protein that BindCraft never saw? Same limits as above. | Predicting the complex with the neighbouring stretch added, or binding to a longer construct. |
| **advisory** Context: why | Context places the binder against the reference as one static model. Against an experimental structure that is a measurement and the check is hard (it can give NO PASS). Against a predicted model, or a file that does not say, it is advisory: it appears with its reasons and can lead to REVIEW, never to NO PASS on its own, because a region the predictor cannot place lands at an arbitrary position. Binderscope reads the method record in the reference file to decide, and the Board and the Overview say which it concluded. `--context-check hard` or `advisory` (or `checks.context.hard` in the profile) overrides it. | The PAE plot of the model, or an experimental structure of the same region. |
| **warns** Context: cut ends | Are two or more contacted residues within four residues of a cut end, where a neighbour was removed? | Binding to a construct with longer ends. |
| **warns** Context: buried epitope | Does the rest of the full-length protein bury (more than 10 Å² each) two or more contacted residues? | Binding to the full-length protein. |
| **soft** Epitope | Does it touch at least the required number of hotspots of its own run (two by default, or all of them when the run has fewer)? Some but not enough is a warning; none at all is *off-site* and fails (a soft failure: REVIEW, not NO PASS). | A competition or mutation experiment at the hotspots. |
| **soft** Chemistry | Shape complementarity at least 0.60 and ΔG at most −15 (both BindCraft's own values), at most 4 buried unsatisfied polar atoms and an exposed hydrophobic patch of at most 650 Å² (both recomputed by Binderscope). One or two misses warn; three or four fail (a soft failure: REVIEW). | A second interface prediction; an aggregation test for a sticky patch. |
| **soft** Sequence | Liabilities in the binder sequence. An unpaired cysteine fails (a soft failure: REVIEW). Which cysteines are paired comes from the structure file's own disulfide records; BindCraft 2 writes none, and a placed structure carries none either, so the pairs are then read from the distance between the sulfur atoms (up to 3.4 Å, and a pair over 3.0 Å is kept but noted as loose, where the model is least sure). Without that, the conserved framework disulfide of every nanobody would read as two unpaired cysteines. The Inspector says which of the two the answer rests on. These warn: an N-glycosylation sequon (N-X-S/T, X not P), NG deamidation, DG isomerisation, an exposed methionine, an exposed hydrophobic stretch of six or more residues, an exposed hydrophobic patch of 500 Å² or more, an exposed furin site. Weaker notes (NS, DS and DP motifs, buried motifs, interface Lys and Met, unusual charge) are shown but do not count. | Expression, stability and aggregation tests. |
| **info** Specificity | How much of the contacted surface is identical in other species and in related proteins? Information only; never part of the verdict. | Binding assays against those species and proteins. |

> **Warning: A passing design is a predicted binder, not a confirmed one.** In published benchmarks, confidence scores are only weak predictors of lab success on designs that already passed the filters. Use agreement between checks as a flag, never as a gate. The context checks rest on a placement from your reference structure, and the Sequence check rests on sequence patterns, not on measurements.

#### Things that surprise people

- A tile shows the verdict of the family's best design (the highest quality score); its sibling sequences may read differently, and the Board counts a family as passing when its best sequence is not NO PASS.
- A design BindCraft accepted can read NO PASS (these checks are Binderscope's own), and a BindCraft-rejected one can read PASS. Membrane side, glycan distance and specificity never change the verdict, even where they show as amber or red elsewhere.
- The Interface and Fold checks use BindCraft's own averaged scores. For a BindCraft 1 run the limits in BindCraft's *filters* are reconstructed defaults, not your filter file (see the FAQ).
- The Inspector's *Promising, with n issues* counts every warning finding, so a PASS can come with issues. One warning alone never changes the Board's verdict.
- If every accepted design fails Context, or they all hit the same residues, suspect the reference before the designs (see the warning under [when data are missing](#7-when-data-are-missing)).

#### What the checks need

- **Fold, Interface, Pose, Chemistry, Epitope, Sequence** run on every bundle. A metric a run does not report (for example from a BindCraft 2 run) removes the part of the check that needs it, and a missing value never fails a design.
- **Context** is not run without a reference structure (see below). With one, it needs the reference structure to contain the other subunits (the build finds them, or you name them with `--partner-chains`) and the parts of the chain outside your construct. Without other chains there is no other-subunit part; without extra residues in the reference there is no rest-of-the-protein part. The Overview lists which parts are on. Its distances depend on how the design is placed on the reference (see *Target frame* under [concepts](#2-five-ideas-that-make-the-pages-click)) and on how well the reference places the rest of the chain (see the warning under [when data are missing](#7-when-data-are-missing)).
- **Epitope** needs the hotspots of each run (settings JSON or CSV column). A run without hotspots has nothing to test and the check passes; its row then reads *0 of 0 hotspots touched*, which is not a result (see [when data are missing](#7-when-data-are-missing)).
- **Specificity** needs species and paralog data (see [when data are missing](#7-when-data-are-missing)).

## 7. When data are missing

Binderscope never guesses. A check that needs data your inputs do not carry is switched off, the Overview's *What this bundle carries* panel says which, and a missing value never fails a design. Here is what depends on what.

| What you give it | What it switches on | Without it |
|---|---|---|
| **A reference structure of the full-length target** (the second card of the import page, or `--reference-cif`; the command line does not need one, the import page asks for one) | The whole target frame: placement of every design on the full-length protein, the other subunits, the rest of the chain, cut ends, buried epitope, glycans, the Target frame strip and Target checks, and the residue numbering the pages use. | Without one the construct is the frame: designs are compared on it, and the other-subunit, rest-of-protein, cut-end, glycan and membrane checks are not run. They are absent, not passed: the Overview says no reference structure was given, the Board's Context row reads *not checked*, and the Inspector shows its Target checks as not run. Everything computed from the designs' own structures (interface, families, modes, paratope, near-miss rescue) is the same as with a reference. A reference with no other chains has no other-subunit check, and one that holds nothing beyond your construct has no rest-of-the-protein check; the Overview words these two situations differently, because a check that ran on nothing is not the same as a check that could not run. If the reference does not match the construct by sequence the build stops with a message naming the disagreement. A residue number that two residues of the reference chain share (an insertion code) is left out of the placement, and the build warns. |
| **Hotspots** (the run's settings JSON, or the hotspot column of the CSVs) | The Epitope check, *off-site* marks, hotspots touched on tiles and in mode detail, the *On the intended epitope* step on the Map. | A run with no hotspots at all (no settings file and an empty hotspot column) still imports and builds. It has no intended epitope, so there is nothing to test: the Epitope check passes, no design is off-site, no hotspot rings are drawn and the epitope numbers of its designs are empty. The Overview's *What stands out* says so, and so does a note in the run's record in `data/manifest.json`. Counts and rules that divide by the hotspots are not meaningful for that run: *0 of 0 hotspots touched* on tiles and in the Epitope row, the Map's *On the intended epitope* step, the *Hotspots touched* rule on Query and the Query examples that mention hotspots or the epitope. They pass everything or find nothing, so ignore them. The binding-site view is built from the residues the designs contact most, or from the whole construct when there is nothing to go by, so it shows where the designs bind and not where you meant them to. |
| **Species and paralogs** in the profile, and their public records (fetched when you allow the online lookup, or already cached) | Specificity: the Inspector's Specificity tab, the Board's Specificity row, the Map's specificity block and conservation colouring, and the specificity rules of the Query page. See [the profile specification](profile_spec.md). | With the built-in default profile there is no species or paralog list, so these parts are absent. Specificity is a sequence screen over the contacted residues, never a measurement. The optional ConSurf-DB overlay (`--consurf`) is academic and non-commercial use only. |
| **A UniProt accession** (Options on the import page, or `--uniprot`; optional. Tick *Allow the online lookup*, or use `--online`, so that its records are fetched) | Domain strip, topology, membrane plane and annotation of the target, and the sequence cross-check of the reference. | Without one no UniProt, PDBe or AlphaFold record is read: no domain strip, topology, membrane plane or specificity panel. |
| **The trajectory table** (`trajectory_stats.csv`) | Generation order, the accumulation curve, the forecast and the Replay slider on the Board. | Without it there is no Generation order sort, no curve and no replay; the coverage estimate and the diversity count remain. |
| **Counts of attempted trajectories** | Yield per attempted trajectory and forecasts in trajectories. | BindCraft 1 does not record every attempt in the files Binderscope reads, so attempted trajectories read "unknown", with an estimate from BindCraft's counters marked "est." where they allow one. The yield is then per passed trajectory. A BindCraft 2 campaign lists every trajectory it started, so its count is known. |
| **Rejected structures** (the `rejected` folder) | Near-miss rescue, the *+ BindCraft-rejected* pool on Query, *Show rejected* on the Map, the *Near miss* chip in the Inspector. | Rejected sequences stay in the funnel counts as scores only. They cannot be placed, inspected or queried. |
| **The settings JSON of each run** | Hotspots, binder length range, the target number of designs. | Hotspots come from the CSV column and lengths from the observed range; a note in the run's record in `data/manifest.json` says so. |
| **More than one run** | The RUNS filter, per-run estimates side by side, run tags. | With one run there is no run filter and no run wording. |
| **A BindCraft 2 run** | The BindCraft 2 reader. | Verified against one real campaign: de novo design (the `binder` modality) written by BindCraft 2 v1.0.3, with its metadata file; such a run is read without a warning. Every other campaign is read on the same assumptions but is not verified: another version, a VHH, scFv, Fab or cyclic-peptide campaign, several targets, or tables or metadata that lack what the reader relies on. The Overview marks such a run "experimental reader" and the Build panel's *Warnings* row names the reason; compare a few numbers with the campaign's own CSVs before relying on it. A real VHH campaign was read without error and its residue letters matched its sequences; nothing more was checked. Checks that need scores BindCraft 2 does not report are switched off. A campaign that numbers its binder residues from 0 (BindCraft 2 versions 1.0.0 and 1.0.1; documented, not yet seen on a real campaign) is analysed through renumbered copies of its structure files, kept in the project's `work/onebased` folder, and a note in the run's record in `data/manifest.json` says so; your files are not changed. A campaign without its metadata file has no hotspots (see above). Only BindCraft 1 runs are compared with their CSVs, so a project of BindCraft 2 runs has no *Counts check* row in the Overview's Build panel. |
| **The scaffold record of a scaffolded campaign** (a VHH, scFv, Fab or ankyrin-repeat campaign: BindCraft 2 records in `campaign_metadata.json` which positions it may change; the scaffold file is looked for at the recorded path, else beside the campaign) | The paratope analysis: *What the designs bind with* on the Board, *Paratope by segment* and the CDRs or Segments colouring in the Inspector, and the framework and FR2-face findings. Fold diversity is switched off. | A campaign without the record is not treated as scaffolded, and its fold diversity stays on. With the record but without a usable scaffold file (a campaign moved off the machine that ran it has often left the file behind), or if the positions outside the edit spec were not held fixed, fold diversity stays off and the paratope axes read *not measured*, with the reason. A design that cannot be placed on the scaffold is left out and counted, never guessed at. Only a VHH campaign has been tried on real output: the scFv, Fab and ankyrin-repeat paths are tested on synthetic scaffolds only. A binder of several chains is read whole, and what that changes is said where it applies: fold diversity is not reported for it, and the terminus readout says the molecule has two ends per chain while it answers for one pair. |
| **Structures of the judged designs** (they are in every bundle the current version writes) | The terminus analysis: **Where a tag can go** on the Board, the tag pills on the tiles, the **Tag and fusion** card in the Inspector and the **Tag plan CSV**. | Accepted designs and rescued near-misses are judged; the other rejected designs are not, and an older bundle built before the analysis existed carries none of it, so the pages leave the panel and the card out rather than showing them empty. Without a reference structure the ends are judged against each design's own target only, and the panel says so: a tag site that the rest of the full-length protein or another subunit would block is then not seen. The run's modality decides which tagging convention is quoted; a run that did not record one is read as a de novo binder and the card says so. |
| **WebGL and local storage in the browser** | The Inspector's 3D view, and autosave of decisions. | Without WebGL the Inspector draws a simplified view and says so (the other pages draw their 3D views without it). Without storage decisions live in memory only: save a review file. |

> **Note: The reference decides how good the full-length checks are.** The checks that use the reference (context, cut ends, buried epitope) are only as good as it is: a model of a different construct, a reference with missing loops or one that lacks the other subunit gives fewer or weaker checks. They are flags that say what to confirm, not measurements.

> **Warning: Check how well the reference places the rest of the protein.** An AlphaFold model of a protein with several domains places them relative to each other only as well as its predicted aligned error (PAE) says. Where the PAE between your construct and a stretch is near 30 Å (the scale tops out near 32 Å, where AlphaFold is saying it has no idea), that stretch is placed essentially at random. A clash, cut-end or buried-epitope finding that involves it means nothing in either direction: a design reported as colliding there may not, and one reported clear is not shown clear. This is why Context is advisory unless the reference is experimental: its findings still appear, with their reasons, but cannot give NO PASS on their own. Two signs that the reference is the problem, not the designs: every accepted design fails the rest-of-protein check, and they all hit the same residues. Left unchecked, this makes accepted designs read as clashing, all on the same residues of a region at the top of the PAE scale, and families read NO PASS on that check, which also hides the sequence liabilities that are real; Context is therefore advisory for a predicted reference, so such designs read REVIEW with the liabilities listed. Binderscope does not read the model's PAE itself, so look at the PAE plot of the model before you rely on a clash, or on a NO PASS that rests on Context (Binderscope's campaign interface, where you launch runs, checks it on the Review tab when you choose the reference). If the reference is poor, build against one that stops at the domain you designed against, or an experimental structure of it. The interface numbers (contacts, hydrogen bonds, buried area) come from the design's own structure and do not depend on the reference.

## 8. Keyboard shortcuts

Press `?` on the Board for its own list. Shortcuts do not fire while you type in a field or hold Ctrl, Alt or Cmd.

**Board**

| Action | Key |
|---|---|
| Move between tiles | `←` `↑` `↓` `→` |
| Keep · maybe · reject (again to clear) | `K` `M` `X` |
| Tag a reason on what you just decided | `1` `–` `7` |
| Undo the last decision | `U` |
| Learning on or off | `L` |
| Add to compare, then press Compare | `C` |
| Step through a family's sequences | `[` `]` |
| Open in the Inspector | `Enter` |
| Find an id · all shortcuts | `/` `?` |
| Close a dialog, the clear bar or the search | `Esc` |

**Inspector**

| Action | Key |
|---|---|
| Previous · next design | `←` `→` |
| Keep · maybe · reject the family | `K` `M` `X` |
| Undo the last decision | `U` |
| Focus the filter box (list mode) | `/` |
| In the filter box: pick the first match · move to the list · clear | `Enter` `↓` `Esc` |
| In the list: next · previous · first · last | `↓` `↑` `Home` `End` |
| Rotate · zoom the 3D view | drag · wheel |
| Fly to a residue | click it in the tracks or 3D |

**Map**

| Action | Key |
|---|---|
| Pick a binding mode (again to clear) | `1` `–` `9` |
| Step through the modes | `←` `→` |
| Map · Fingerprints · By mode · Table | `M` `F` `B` `T` |
| Show or hide rejected designs | `R` |
| Clear the brush, the residue region and the focus | `Esc` |
| Rotate · zoom the 3D view | drag · wheel |

**Query**

| Action | Key |
|---|---|
| Move between controls · activate | `Tab` `Enter` `Space` |
| Inside a list (rules, tiles, rows): move | `↑` `↓` `←` `→` `Home` `End` |
| Compile the Ask box | `Enter` |
| Close What to try | `Esc` |

**Every page**

| Action | Key |
|---|---|
| Skip the header (a link that appears on the first Tab) | `Tab` `Enter` |
| Close the open dialog or the What to try box | `Esc` |

## 9. Saving, sharing and exporting

#### Where your decisions live

- **Autosave.** Each decision is saved in this browser's local storage under the bundle's id (the same inputs give the same id in any folder), together with the reviewer name and the learning switch. Clearing site data erases it, so save a review file. If the browser blocks storage the page says so and decisions live in memory until you close the tab. In the app a copy is also saved in the project folder (`review-events.json`) a few seconds after each change and when you leave the page, and brought back when a page opens in a browser that lacks some decisions (another browser, another port, cleared site data): the merge is a union by event id, so nothing is overwritten. Deleting the project deletes the copy.
- **Review file.** **Save review file** writes `<reviewer>.jsonl`, a small text file with one line per event: a keep, maybe or reject, a reason tag, a clear. Each event carries the reviewer, a time stamp, the family or design id, the run, the sequences chosen for the order list, the reasons, the learning switch and the bundle id. It holds the events of the current reviewer only, and the save button is available as long as the log holds anything, so a file can come out empty if the log holds only a colleague's events. Browsers cannot write into the bundle themselves: move the downloaded file into the bundle's `reviews/` folder if you want to keep them together, but note that the pages never read that folder by themselves and that writing a bundle into an existing folder (`import --bundle`, or `build-bundle` without `--keep`; in the app **Rebuild** and **Update the review pages**) deletes it, `reviews/` included. Keep a second copy elsewhere.
- **Load review file.** Reads one back and merges it: events whose id is already present are skipped, new events are added, nothing is overwritten, and a decision in the file that a later **Clear decisions** removed here is brought back as a new event. Invalid lines are reported with their line number; events written for a different bundle are counted. The current decision on an item is the latest event of that reviewer.
- **Reviewer name.** The default is "you". Set your name in the **Share** dialog before you start; the file is named after it. If two people use the same name their events cannot be told apart, and a typo in the name makes your decisions look empty (the events are still in the log: typing the old name brings them back).
- **Share.** **Share** in the header (also **Share review** in the order list) opens a dialog with Save and Load review file, the reviewer name, a picker to switch between the reviewers found in the log, and a note on whether decisions persist. The Board shows one reviewer at a time and does not merge their calls. Nothing is sent anywhere.

#### Lists you take away

- **Order list (Board).** **Copy FASTA** puts the sequences on the clipboard for a submission form. **FASTA file** and **CSV** download `order-list.fasta` and `order-list.csv` (columns: design id, run, family, rank, verdict, BindCraft rank, reasons). **Tag plan CSV** downloads `tag-plan.csv`: one row per design in the list with the end to tag for each of the four tag sizes, what to immobilise through, and the distance, side and room behind each answer. FASTA needs the sequences to be in the bundle; the exporter can leave them out.
- **Query results.** **CSV (n)** and **FASTA (n)** act on the ticked designs or the whole tab; the CSV never contains a sequence. **Save** writes a rule-set file (`binderscope-rules-<profile id>.json`) and **Load** applies one; the file holds numbers and ids, no sequences and no design ids.
- **Map.** **Copy FASTA** on the quality-diversity picks. The star shortlist is not saved.

#### What is in a bundle

A bundle is a folder. In the app it is the `bundle` folder of the project (`Binderscope/<project>/bundle` in your home folder); on the command line it is the folder you name with `--bundle`.

```
bundle/
  index.html  board.html  inspector.html  map.html  query.html  guide.html
  data/        campaign, inspect, homolog, profile, manifest: .json for tools, .js for the pages
  structures/  <design id>.pdb and .js for each exported design
  assets/      shared css and js, vendored 3Dmol.js and its licence
  reviews/     a place for saved review files (the pages do not read it)
  README.txt
```

`data/manifest.json` records the tool version and the command you ran (file and folder names only, no absolute paths), the platform and library versions, the build date, the bundle id, the names and hashes of every input and output file, the run settings, the thresholds, the checks made at build time, what stands out in the data and a privacy record of what (if anything) was fetched. Run `binder-review inspect` on the bundle folder for a summary.

> **Tip: Export, then freeze.** Copy the order list as FASTA or CSV for the submission, save the review file, and archive the project folder next to it (in the app `Binderscope/<project>` in your home folder: the `bundle` folder holds the review pages and `work` the analysis for a rebuild). When results arrive you can reopen the same project and see what the checks said about each design.

## 10. Good practice and pitfalls

- **Do. Count families.** Ten sequences from one backbone are one idea. Spread across families and binding modes.
- **Do. Write the reason.** A reason beside every reject makes the next campaign's rules obvious.
- **Do. Look at the maybes in 3D.** Numbers hide pose problems that take seconds to see.
- **Do. Save a review file.** Browser storage is a convenience, not an archive. Save the file when you stop, and keep a copy outside the bundle folder (a rebuild into the same folder deletes it).
- **Avoid. Comparing runs by pooled rates.** If the hotspots, lengths or target construct changed, it is a different experiment.
- **Avoid. Trusting one score.** Several weak signals that agree are worth more than one strong number.
- **Avoid. Reading flags as vetoes.** Only a failed Fold, Interface or Pose check gives NO PASS (and Context, when the reference is an experimental structure), and even that is a screen: the context checks rest on a placement from the reference structure and on how well it places the rest of the protein.

## 11. FAQ

**Does anything leave my computer?**

No design data does. The app runs a small server on your own computer (it listens on 127.0.0.1 only, so other computers cannot reach it) and the pages load from it or from files on disk; no binder sequence or design id is sent anywhere. The only network requests are public lookups that you ask for: pressing Download for a PDB id (the id goes to the RCSB) and ticking *Allow the online lookup* (public records fetched by accession or PDB id at build time; `--online` on the command line). By default a build reads a local cache and makes no requests; the Overview's Build panel records it. A bundle makes no network request when you open it (it uses the fonts of your system; `--fonts google` adds an optional typeface link). The learning ranking on the Board runs in your browser. The installer downloads the Python libraries from PyPI once.

**Why are some numbers marked "unknown" or "est."?**

BindCraft 1 does not record every attempt in the files Binderscope reads, so the number of attempted trajectories reads "unknown", with an estimate from BindCraft's failure counters marked "est." where the counters allow one. A BindCraft 2 campaign lists every trajectory it started, so its number is known. Unknown is never drawn as zero.

**Why do the two runs have different targets or hotspots?**

Each run is read with its own settings. If one run deleted loops from the construct or used other hotspots, the pages follow that run. The Overview lists the differences, and the Inspector's target frame strip hatches what a run removed.

**My run has no hotspots. Can I still use it?**

Yes. A run with no hotspots at all (no settings file and an empty hotspot column, or a BindCraft 2 campaign without its metadata file) imports and builds. It has no intended epitope, so the Epitope check passes, no design is off-site and counts such as *0 of 0 hotspots touched* mean nothing. The Overview says *No hotspots recorded for run01*, and the binding-site view is built from the residues the designs contact most. See [when data are missing](#7-when-data-are-missing).

**Why "PASS · Promising, with 2 issues" in the Inspector?**

The first word is the Board's verdict. The second part is the Inspector's finer reading: it counts every warning finding, such as a Met oxidation or a contact near a cut end, while the Board leaves PASS only after a failed soft check or two warnings. Read the findings, not the label.

**My decisions disappeared.**

They live in the browser's local storage under the bundle's id and the page's address. In the app a copy is also kept in the project folder and brought back when the page opens, so another browser, another port or cleared site data does not lose them. A bundle built from other inputs, a project you deleted, or a bundle opened from disk (it has another address) starts fresh: load your saved review file. In Firefox the store for a page opened from disk may be kept separately for each page, so use the app, Chrome or Edge, the browsers the pages are tested in. **Clear decisions** asks first; `U` restores the cleared decisions at once, and loading a review file you saved earlier brings them back as new events.

**Can I add a run later?**

Open the project's **More** menu and choose **Edit the inputs…**: the import page opens with the project's choices filled in. Add the run and press Build project. The app makes a new project (named like the old one with " 2" added) and leaves the old one as it is. The bundle id changes with the inputs, so load your review file in the new project to carry decisions over (they refer to family and design ids, which stay the same); delete the old project when you no longer need it. On the command line, build again with all the run folders, into a new bundle folder or after copying your review files out of the old one (a rebuild into the same folder replaces its contents). The Board's **How to import a run** button only explains the command and lists what the build read; it does not import anything itself.

**Why do the BindCraft filters not match the ones I ran with?**

BindCraft 1 does not copy its filter file into the run folder. For a BindCraft 1 run Binderscope attaches BindCraft's default filter set, reconstructed from the data, and says how firmly each limit is known (*counter-verified*, *bounded* or *memory*). If you ran custom filters, the *BindCraft filters* group on the Query page and the limits on the Inspector's meters are not yours: edit the limits there, or write them into your profile. BindCraft 2 does record its thresholds in `campaign_metadata.json`, so a BindCraft 2 campaign is judged by the thresholds it used (each rule's note says it is the limit the campaign recorded, and so does the tooltip of the Inspector's meters): that is a stronger claim than a reconstruction, but still a screen, not a verdict. A design BindCraft rejected stays rejected even when its CSV values pass a threshold (BindCraft checks confidence filters on each validation model, the CSV holds the mean), and thresholds on metrics Binderscope does not carry are listed as not re-checked.

**The binder looks misplaced on the full-length protein. What do I check?**

Start with the chain. The import page says how your target structure fits the chain of the reference (green, amber or red, with the CA RMSD and the residues matched); red means the wrong chain, another protein or another numbering. Set **Chain of the target in the reference** in Options (for an existing project, More and **Edit the inputs…**) and build again. Then the protein: the reference must be the full-length version of the same protein. Then **How designs are placed on the full-length protein** in Options: the default fits around each design's interface, while the whole-target fit can leave the binder 1 to 3 Å off where BindCraft's copy of the target differs from your reference (a project whose card says "Built with an older analysis" used it: press **Rebuild**). Then the numbers: in the Inspector's Target checks a design fails the other-subunit or the rest-of-protein check by default from 10 atoms within 3 Å, and if nearly every design of a run does, suspect the placement, then the reference (see the warning under [when data are missing](#7-when-data-are-missing)), before the designs. Last, open `structures/<design id>.pdb` of the bundle in a text editor: the line `REMARK 250 placement:` gives the fit used and the CA RMSD near the interface and over the whole target, and more than 1.5 Å near the interface means that the design's own copy of the target differs from the reference there (the build records a warning for it). On the command line the option is `--placement interface|whole|core`. See [the app guide](app.md).

**Why does a design BindCraft accepted read NO PASS?**

The Board's verdict is Binderscope's own set of checks, not BindCraft's decision. The usual reasons are a Fold, Interface or Pose limit in your profile that is stricter than BindCraft's, or a Context failure against an experimental reference (binder atoms within 3 Å of the other subunit or of the part of the protein BindCraft never saw); against a predicted reference a Context failure only adds a reason to a REVIEW. The Board's *because* list ends with a line saying BindCraft accepted the design and that the verdict is Binderscope's own screen. Open the design: the *because* list and the Target checks say which. If every accepted design fails Context on the same residues, suspect the reference before the designs (see the warning under [when data are missing](#7-when-data-are-missing)).

**How do I change the limits behind PASS, REVIEW and NO PASS?**

In your profile (`checks.presets` and `checks.preset_default`, and `checks.context` for the context limits). See [the profile specification](profile_spec.md). The Board and the Inspector have no control for it.

**Where are my projects, and how do I update or remove Binderscope?**

Projects are folders in `Binderscope` in your home folder (More, then Show in Finder, opens one). To update, run the installer again (it asks a running Binderscope to quit); projects built by an older version say so on the home page, and **Update the review pages** brings them up to date in seconds. A card that says "Built with an older analysis" needs **Rebuild** instead: the update changed how designs are placed on the full-length protein, and new pages alone do not recompute that. To remove it, run `./install.sh --uninstall`: it removes the app and keeps your projects. [The app guide](app.md) has the details.

**The window says "Binderscope has stopped".**

The app ended: you pressed Quit, or about a minute passed with no open window and no build running. Open Binderscope again from Spotlight. Your projects and decisions are unchanged.

**Can I use BindCraft 2 output?**

Yes. The reader was verified against a real de novo campaign (the `binder` modality) written by BindCraft 2 v1.0.3, and a campaign of that version and modality with its metadata file is read without a warning. Every other BindCraft 2 campaign is read on the same assumptions but is not verified: another version, a VHH, scFv, Fab or cyclic-peptide campaign, several targets, a missing metadata file, or tables that lack a column the reader relies on. The Overview marks such a run "experimental reader" and the Build panel's *Warnings* row says why, so compare a few numbers with your own CSVs. Checks that need scores BindCraft 2 does not report are switched off. A campaign that numbers its binder residues from 0 is analysed through renumbered copies of its structure files (your files are not changed). See [when data are missing](#7-when-data-are-missing).

## 12. Glossary

| Term | Meaning |
|---|---|
| Family | One BindCraft backbone (trajectory) and the sequences designed on it. |
| Sibling | Another sequence on the same backbone. |
| Design cluster (C1...) | Families whose backbones, binding sites or both are alike enough; the Board's *Count by* and *Same cluster if* set how alike. |
| Binding mode (M1...) | Designs that contact much the same target residues, named from those residues. At most seven per bundle. |
| Scaffolded campaign | A campaign whose designs share one fixed framework (a VHH, scFv, Fab or ankyrin repeat) and change only chosen positions. Fold similarity is switched off for it. |
| Paratope, segment, FR2 face | The residues of a scaffolded binder that touch the target, and the designed stretches they fall in (CDR1 to CDR3, H1 to L3, or numbered segments); the rest is framework. The regions are the campaign's own edit spec, not IMGT or Kabat numbering. On a VHH the FR2 face is the exposed face of framework 2 that sat against the light chain in a conventional antibody. |
| Construct | The piece of the target you gave BindCraft, usually a trimmed domain. |
| Reference structure | A full-length structure of the target that you give the build; designs are placed on it. |
| Target frame | The numbering map and context that place the construct on the full-length protein. |
| Placement | How a design's own copy of the target is fitted onto the reference structure. The default fit is weighted around the design's interface; `--placement whole` fits the whole target and `core` the rigid core. |
| Cut end | A place where the construct stops although the protein continues (or where a loop was removed). |
| Buried epitope | A contacted residue that the rest of the full-length protein would cover. |
| Hotspot | A target residue you asked BindCraft to contact. |
| Off-site | A design that touches none of its run's hotspots. |
| Verdict | PASS, REVIEW or NO PASS on the Board, from the checks. |
| i_pTM, pLDDT, pTM, i_pAE | AlphaFold2 confidence in the interface, in the fold of the design and in the aligned error across the interface. BindCraft writes i_pAE as PAE divided by 31. |
| Shape complementarity, ΔG | How well the surfaces fit, and BindCraft's Rosetta interface energy. |
| Unsatisfied polars | Buried polar atoms at the interface with no hydrogen-bond partner. |
| Hydrophobic patch | An exposed hydrophobic area near the interface that can make a binder sticky; given in square angstrom. |
| Liability | A sequence pattern that often causes trouble: unpaired cysteine, glycosylation sequon, deamidation or isomerisation motif, exposed methionine, furin site. |
| Tag and fusion (terminus analysis) | Which end of a design can carry a tag or a fusion, judged per tag size from the predicted complex: for His6 and Twin-Strep whether a flexible chain of that length can reach open solvent, for a GFP-sized domain or an Fc whether a sphere of that size fits against the end. |
| Immobilised by the binding face | A tag at an end that sits on the paratope side holds the binder by the surface it binds with, which can lose the signal in an SPR or BLI assay. The Tag and fusion card says which side each end is on. |
| Disulfide basis | Whether the binder's cysteine pairing was read from the structure file's own records (*recorded*) or from the distance between the sulfur atoms because the file holds none (*inferred*). |
| Coverage | Estimated chance that the next passing family falls in a cluster you have already seen. One minus coverage is the chance it is new. |
| Yield | Families per trajectory (passed trajectories unless the attempted number is known). |
| Near-miss | On the Query page, a design that fails exactly one rule you have on. Among BindCraft's rejected designs, one that missed exactly one filter by at most 25% of its threshold. |
| Pool | The set of designs the Query page works on: accepted designs, or accepted plus BindCraft-rejected. |
| Ortholog, paralog | The same protein in another species, and a related protein in the same species. |
| Review file | A JSONL file of one reviewer's decisions, one line per event. |
| App | The Binderscope program on your computer: a local server and a window with the project list, the import page and the review pages. |
| Project (in the app) | A folder in `Binderscope` in your home folder: the analysis (`work`), the review pages (`bundle`), the structure files you dropped or downloaded, the log of the last build and a copy of your decisions. |
| Bundle | The folder of review pages, data and structures that Binderscope writes (in the app the `bundle` folder of a project). |
| Project folder | The folder written by `import` (caches, `export/`, `project.json`); `build-bundle` rewrites a bundle from it. In the app it is the `work` folder of a project. |
| Profile | A JSON file that describes your target and project to the pages: wording, limits, species and paralogs, which checks apply. See [the profile specification](profile_spec.md). |

---

Binderscope is the program; BindCraft 2 is the design engine it drives. BindCraft is a separate project and Binderscope is not affiliated with it. This page is part of the bundle and works offline.
