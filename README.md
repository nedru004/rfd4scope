# Binderscope

Binderscope is a web application for running protein binder design campaigns on a GPU machine and deciding which designs to order. It is the launcher, the GPU queue and the review: it starts campaigns, shares the cards between them, watches them, and turns a finished campaign into an offline review site.

**BindCraft 2 is the engine it drives.** Binderscope does no design itself. BindCraft 2 chooses the binding sites, generates the binders and applies the filters that decide which designs passed. The two names are not interchangeable in this document or in the program.

> **Beta.** Binderscope has been tested, but not on everything it reads. Its review has been run on real BindCraft 2 campaigns of four kinds: **de novo binders, VHH nanobodies, scFvs and linear peptides**. In every build its counts are compared with the campaign's own tables. It has **not** yet reviewed a real campaign of the other kinds BindCraft 2 designs: **Fab, ankyrin repeats, cyclic peptides, homo-oligomers, multidomain and large binders**. Those are read on the same assumptions, and the review labels them *untested*, as it does for any BindCraft 2 version other than the v1.0.3 it was checked against. Check those numbers yourself, and report anything that looks wrong. BindCraft 1 run folders are read too; that path is covered by the test suite, on synthetic runs. What each install has been run on is under [Install](#install).

---

## Read this before you install anything: do not expose it

**There is no authentication anywhere in this application, and none is planned.** Anyone who can reach the port can start GPU campaigns, read your results and cancel your jobs.

The service binds `127.0.0.1` only. The one intended route in is an SSH tunnel from your own machine. Do not change that to `0.0.0.0`, do not put it behind a reverse proxy, and do not open the port in a firewall. If you do, you have published an unauthenticated job launcher on a GPU machine. Someone will be tempted to do this so that colleagues can reach it without a tunnel. Do not let them.

Two further facts, so that you can judge the tunnel for yourself.

**Other accounts on the same machine can reach it.** Loopback is not private to one user. On a shared workstation, anyone with a login there can connect to your port. This is one reason the design is one instance per person (see below), but it does not stop another person connecting to yours.

**Your browser is a second way in, and it is guarded.** While the tunnel is up, `localhost:PORT` is reachable from every page open in that browser, and the port is derived as `8080 + uid % 900`, so it is easy to guess. A form post and a multipart upload are both "simple" requests, which a browser sends to another origin without asking the receiving service first — so without a guard, a page on any site could have started a GPU campaign on your machine.

Binderscope refuses a state-changing request that another site's page made. It checks `Sec-Fetch-Site`, which the browser sets and the page cannot forge, falls back to `Origin` for browsers that do not send it, and requires `application/json` on the routes that take JSON. Anything that is not its own page is refused with 403. Requests that carry neither header are not from a browser — `curl`, a script, the tests — and are left alone.

**This is not authentication, and it does not make the service safe to expose.** It closes one specific route in, and it is still true that anyone who can reach the port can use the service. Keep it on loopback, keep it behind the tunnel, and close the tunnel when you are not using it.

---

## License: you must not host this for other labs

Binderscope's own code is MIT (see `LICENSE`).

BindCraft 2 is not. It is published under a source-available licence with a hosting restriction. You may redistribute it for others to run themselves. You may **not** offer it as a hosted web or API service without written permission from the copyright holders, who include the University of Zurich.

Binderscope is a web application whose material functionality is BindCraft 2. So if you install Binderscope and open it to other labs, you are the party breaking BindCraft 2's licence, and nothing in the software will tell you so. The MIT grant on Binderscope gives you no rights over BindCraft 2 to do this.

Running it for yourself, or for people in your own organisation, is what the `LICENSE` file describes as fine. The restricted case is exposing it to people outside. Read BindCraft 2's own licence before you rely on this summary; the wording there is what binds you.

One factual distinction, not legal advice: the review-only install described below contains no BindCraft 2 code and never runs it. It reads folders BindCraft has already written. What that means for you is a question for the licences themselves.

---

## Install

One installer, three choices:

```bash
./install.sh
```

It asks which one you want. To say it up front, or to script it:

| | `./install.sh --review` | `./install.sh --local` | `./install.sh --sge` |
|---|---|---|---|
| **What you get** | The review: read finished BindCraft 1 or 2 runs and review them in the browser | Everything, on this machine: run BindCraft 2 campaigns on its GPUs, queue them, review them | Everything, with campaigns sent to an SGE cluster as Apptainer jobs |
| **Run it on** | macOS or Linux | Linux with NVIDIA GPUs | A Linux host that can `qsub`, usually a login node |
| **Needs** | Python 3.12 or later (on macOS it finds or installs one) | A working BindCraft 2 checkout with its `.venv` | A BindCraft 2 checkout (its files only), a BindCraft 2 Apptainer image, the AlphaFold 2 parameters, glibc 2.28 or later |
| **GPU and BindCraft** | Neither | Both, here | On the cluster's compute nodes |
| **Documented in** | `binderscope/README.md` | [The full install, step by step](#the-full-install-step-by-step) | [On an SGE cluster](#on-an-sge-cluster) |

`./install.sh --dry-run` shows what any of them would do and does nothing. None needs root. `./install.sh --help` lists the options.

**The review on its own** is for anyone who runs BindCraft some other way, or has been handed a run folder. It is a self-contained Python package with its own installer and its own small app: no GPU, no AlphaFold weights, no BindCraft checkout, no web service, no account, nothing uploaded. `./install.sh --review` runs `binderscope/install.sh`, which can also be run directly; on macOS, double-clicking `binderscope/Install Binderscope.command` does the same.

The installs are independent and can live on the same machine: the review-only one keeps its projects in `~/Binderscope` and touches nothing the full ones use. Going the other way, a full install can review a run it did not start: paste the run folder's path into **A run from somewhere else** on its Review tab.

---

## Requirements for the full install

For `--local`. The cluster install's are under [On an SGE cluster](#on-an-sge-cluster).

- An NVIDIA GPU, on the machine that will run the service. Campaigns run on it.
- About 2.5 GB of AlphaFold weights, which BindCraft 2 loads. Binderscope does not fetch them; they are part of getting BindCraft 2 working.
- Python 3.12 or later. The `binder_review` package sets `requires-python >=3.12`.
- A working BindCraft 2 checkout with its virtual environment at `.venv/` (see the next section). Binderscope does not install BindCraft 2 for you.
- For `./bc2-serve.sh install`, a Linux system with systemd and user services. Without systemd you can still start it by hand.

---

## The layout contract: this repository is an overlay

**This repository does not run on its own.** Its service runs from inside a BindCraft 2 checkout, into which three of its parts are copied. The directories `bindcraft/`, `settings/`, `scaffolds/` and `examples/` come from upstream BindCraft 2 and are not in this repository.

The reason is in the code, not in preference:

- `bc2serve` expects to live inside a BindCraft 2 checkout. Its `REPO` is the parent of the `bc2serve/` directory, unless you set `BINDCRAFT_HOME`.
- `campaign.py` reads its presets from `<repo>/settings/target/*.json`. It reads them from BindCraft 2 rather than keeping its own copies, so they stay in step with the engine.
- The service starts campaigns with `<repo>/.venv/bin/bindcraft` and runs reviews with `<repo>/.venv/bin/binder-review`. Those paths are fixed.
- The web pages are found relative to the code, and importing the app creates its state and results folders and starts its worker threads.

For these reasons `bc2serve` is deliberately **not** an installable package. A `pyproject.toml` would suggest an independence it does not have. It has a `requirements.txt` instead.

**Clone this repository somewhere else, not over the checkout, and run `./install.sh`.** It copies `bc2serve/`, `binderscope/` and `bc2-serve.sh` into the checkout and nothing more. Laying the whole repository over the checkout would overwrite BindCraft 2's own files: both have a `README.md`, a `LICENSE` and an `install.sh` at the top, and BindCraft 2's licence file is one of them.

So the layout you need, once everything is in place, is:

```
/path/to/BindCraft2/          the BindCraft 2 checkout, and the root of this overlay
  bindcraft/  settings/       from upstream BindCraft 2
  scaffolds/  examples/       from upstream BindCraft 2
  .venv/                      the BindCraft 2 virtual environment
  bc2-serve.sh                from this repository
  bc2serve/                   from this repository: the web service
  binderscope/                from this repository: the review package
```

---

## The full install, step by step

`./install.sh --local` does all of this. The steps are here so you can see what it changes, or do it by hand.

Do this on the machine with the GPUs. The commands assume you are in the BindCraft 2 checkout, that BindCraft 2 itself already works, and that `bc2serve/`, `binderscope/` and `bc2-serve.sh` from this repository have been copied into it (`cp -R bc2serve binderscope bc2-serve.sh /path/to/BindCraft2/`). Copy those three, not the whole repository: see the layout contract above.

**1. Install the dependencies into the BindCraft 2 environment.**

```
.venv/bin/pip install --prefer-binary -r bc2serve/requirements.txt
.venv/bin/pip install --prefer-binary -e ./binderscope
```

The second line is required, and so is its `-e`. `binderscope/` is the `binder_review` package, which the service imports and whose `binder-review` command it runs. It is not on PyPI, so it cannot be listed in `requirements.txt` as a version. It is installed editable because a review needs the files beside its source: the pages in `binderscope/viewer/`, the schemas in `binderscope/docs/schema/` and the validator in `binderscope/scripts/`. A plain `pip install ./binderscope` copies the package alone, and the first review then fails with *viewer sources not found*. `--prefer-binary` takes a prebuilt package wherever there is one, rather than a newer release that would have to be compiled.

**2. Install the service.**

```
./bc2-serve.sh install
```

This writes a systemd user unit, turns on lingering so the service can run with nobody logged in, and starts it. It will then start at boot and restart itself if it crashes. If lingering cannot be enabled, the script prints the one command an administrator needs to run, and until then the service stops when you log out.

It also writes `bc2serve.env`, beside the script, if there is not one already. Then it prints your port and the tunnel command.

**3. Day to day.**

```
./bc2-serve.sh status       is it up?
./bc2-serve.sh stop         stop it
./bc2-serve.sh start        start it again
./bc2-serve.sh restart      after editing bc2serve.env or the code
./bc2-serve.sh logs         follow the log
./bc2-serve.sh uninstall    stop starting it at boot
```

Run without `install`, `./bc2-serve.sh` on its own starts it by hand, with a pidfile instead of systemd. Stopping the service does not stop campaigns that are running: they are started in their own process groups on purpose, so a restart of the service leaves running designs alone.

**4. Connect from your own computer.**

The service listens on your machine's loopback interface only, so you reach it through an SSH tunnel. Run this on your own computer, not on the GPU machine:

```
ssh -N -L PORT:localhost:PORT you@your-workstation
```

Leave it running, and open `http://localhost:PORT` in a browser. Replace `PORT` with the port the install printed. To work it out yourself, run this on the GPU machine:

```
echo $((8080 + $(id -u) % 900))
```

Close the tunnel when you are done (see the warning above).

**Settings** live in `bc2serve.env`. After editing it, run `./bc2-serve.sh restart`. The file is written once and never overwritten, so upgrading keeps your port. It holds:

- `BC2SERVE_PORT`, the port.
- `BC2SERVE_RESERVED_GPUS`, a comma-separated list of cards the queue must never use, for cards that should stay free for other work. Empty means it may use all of them.
- `BC2SERVE_NTFY_SERVER`, where campaign notifications are sent. The default is the public `https://ntfy.sh`, which needs no account but is a third-party server. Point it at your own ntfy server if you would rather not use it.

You can add three more lines to the same file. `BC2SERVE_STATE` moves the queue database and uploads (default `bc2serve_state/` in the checkout). `BC2SERVE_RESULTS` moves where campaigns are written (default `results/`). `BC2SERVE_RESULT_ROOTS`, a comma-separated list, sets which folders the interface offers as places to write. If you do not set it, `/ssd/$USER` and `/data/$USER` are also offered when they exist and are writable, which reflects the machine this was built on.

### Keep the port the same

The port is derived from your user id, and it **must stay stable for an account.** The review pages keep your keep, maybe and reject decisions in the browser, against the address the page was opened from. If the port changes, the browser sees a different address, and it looks as though every decision has been lost. They are still there under the old address, but you will not find them. This is why the port is not "whichever one is free". If you change `BC2SERVE_PORT`, you are choosing to move your decisions out of sight.

---

## On an SGE cluster

`./install.sh --sge` sets up the same service on a host that can submit jobs to an SGE (Sun or Son of Grid Engine) scheduler. Slurm is not supported.

### What runs where

| | Where | Under what |
|---|---|---|
| The service and its pages | The submit host, usually a login node | The checkout's `.venv` |
| Each campaign | A compute node, as one SGE job | BindCraft 2 inside your Apptainer image |
| Each review | The submit host, or on a host older than glibc 2.28 a compute node | The checkout's `.venv` capped at `BC2SERVE_REVIEW_JOBS` workers, or a Python container (see below) |

The submit host needs the BindCraft 2 checkout for its files (the target presets in `settings/target/`), not for its GPU environment: BindCraft itself runs inside the image. Nothing on the submit host uses a GPU.

### What you need

- **SGE's client commands** (`qsub`, `qstat`, `qdel`) on the submit host, and **Apptainer** on the compute nodes.
- **A BindCraft 2 Apptainer image** (`.sif`). Many clusters run GPU drivers too old for CUDA 13, so build or pull one made for CUDA 12. Binderscope does not build it.
- **The AlphaFold 2 parameters**, the folder holding `params_model_*.npz`. Each job binds it into the image.
- **For everything in one environment, glibc 2.28 or later on the submit host.** The review package needs numpy 2.5 and scipy 1.18, whose prebuilt packages require it. On an older host the installer switches to launcher-only mode instead: see [On a host older than glibc 2.28](#on-a-host-older-than-glibc-228).
- **Python 3.12 or later** on the submit host. Without root: `pip install --user uv && uv python install 3.12`, then pass `--python "$(uv python find 3.12)"`.

### Install

```bash
./install.sh --sge --sif /path/to/bindcraft2-cuda12.sif --params /path/to/af2params
```

Anything you leave out, it asks for. It creates the checkout's `.venv` when there is none, installs the service's packages into it, and writes these to `bc2serve.env`:

| Setting | What it is |
|---|---|
| `BC2SERVE_BACKEND=sge` | Send campaigns to the scheduler. `local` uses this machine's cards; unset, the service uses SGE whenever `qsub` and `qstat` are both on the path. |
| `BC2SERVE_SGE_SIF` | The BindCraft 2 image every job runs. There is no default: without it, a campaign is refused with the reason. |
| `BC2SERVE_SGE_PARAMS` | The AlphaFold 2 parameters folder. No default either. |
| `BC2SERVE_SGE_QUEUE` | The queue to submit to (`--queue`). Empty means your site's default queue. |
| `BC2SERVE_SGE_SLOTS` | Slots per campaign, 8 unless set. On clusters where a slot buys a share of a node's memory, single-worker campaigns were measured at 16 to 33 GB. |
| `BC2SERVE_REVIEW_JOBS` | Worker processes per review: 2 when reviews run on the login node, 4 when they go to a compute node. Unset, a review takes every core but one, which suits a machine of your own and not a shared login node. |

### On a host older than glibc 2.28

On glibc 2.17 (CentOS 7, for example) the review package's numpy 2.5 and scipy 1.18 cannot be installed at all: they have no prebuilt packages for it, and pip can only try to compile them. The launcher is less demanding. So `./install.sh --sge` installs the launcher on its own, from `bc2serve/requirements-launcher.txt` (floors that resolved and ran on a glibc 2.17 login node), and puts the review package in a virtualenv inside a Python 3.12 container, which brings its own glibc. Reviews then run on a compute node:

```bash
./install.sh --sge --sif /path/to/bindcraft2-cuda12.sif --params /path/to/af2params \
  --python /path/to/python3.12 --review-queue CPU_QUEUE
```

It needs `qrsh` and `apptainer` on the submit host as well. It pulls `python:3.12-slim` into `.binderscope/python312.sif` in the checkout unless you give an image with `--review-sif`, and writes two more settings:

| Setting | What it is |
|---|---|
| `BC2SERVE_REVIEW_RUNTIME` | How to run the review package: `apptainer exec -e --bind <checkout> <image> <venv>/bin/python -m binder_review`. The chain table shown before a build comes from here, through `binder-review chains --json`, so the launcher never imports the package itself. |
| `BC2SERVE_REVIEW_SUBMIT` | What goes in front of a build: `qrsh -noshell -now no -q <queue> -pe smp 4`. `qrsh` because the build's output must stream back to drive the progress stages, `-noshell` so a login shell such as tcsh does not re-read the arguments, `-now no` so it waits for a slot. If your site needs more than a queue, give the whole command with `--review-submit`, for example one that adds a "no GPU" resource. |

**Expect the first install to take most of an hour on a home directory that is a network filesystem.** The two environments are some 20,000 small files (about 650 MB), and writing them is nearly all of the time. It is a one-time cost; updates are a copy of the source.

### What a job asks for

Each campaign is one job: `-pe smp <slots>`, then `-l ngpus=<n>` for any card, or `-l gpu_<model>=true` for a card type chosen on the Design page. Those resource names are the ones the cluster this was written on defines. If yours names GPUs differently, `job_script` in `bc2serve/sge.py` is the one place that builds the request.

The job binds the campaign's project folder and the folders of its targets into the image, each at its own path. Without that, on a cluster whose home directories are reached through a symlink, the container sees home only by its real path, and Apptainer cannot even enter the project folder to start.

The **Cluster** box on the Design page reads queue and card state from `qstat`, including per-card values (`gpu.cuda.<n>.mem_free`) that only some sites publish, and marks the queues that are yours from `whichq`, a command your site may not have. Where they are missing, the box shows less. Submitting does not depend on it.

### Running the service

A login node usually has no systemd user session, so the installer starts the service by hand. It then runs until you stop it, log out for good, or the node restarts; `./bc2-serve.sh` starts it again. **Check your site's rules for long-running processes on login nodes first.** You reach it the same way as on a workstation, through an SSH tunnel to the host it runs on.

### Status

**Run end to end on one production SGE cluster** (glibc 2.17, Apptainer, home on a network filesystem behind a symlink), in launcher-only mode: installed from a clean clone; the service started by hand on a login node; a campaign submitted from the API ran as an SGE job on a GPU node (28 minutes on an A10, 22 GB at peak, one accepted design); its review ran through `qrsh` in the container on a CPU node (40 seconds, 600 MB) with a full-length reference; and the bundle's six pages loaded with no errors. That run also found the bind problem described above, now fixed.

Not yet run: the everything-in-one-environment mode on a cluster with glibc 2.28 or later, and any SGE site other than that one, whose resource names (`ngpus`, `gpu_<model>`, the `smp` parallel environment) may differ from yours. Treat your first campaign as a test, and read its `campaign.log`.

---

## One instance per person

This is the architecture, not a missing feature. The service is a systemd *user* service. Each person who uses the machine runs their own, with their own port, their own queue database, their own results and their own settings, and the operating system keeps them apart. There is no multi-user mode, there are no accounts, and none is planned. If two people want to use Binderscope on one machine, each installs their own instance.

The unit is per account, so one account has one service. `./bc2-serve.sh install` in a second checkout refuses and names the checkout that owns it, and `stop`, `restart` and `uninstall` there leave that service alone. A second checkout can still be run by hand: set `BC2SERVE_PORT` in its `bc2serve.env` to a free port and run `./bc2-serve.sh`.

---

## What is in this repository

Two halves, which ship together and install together. `bc2serve` imports `binder_review`; `binder_review` knows nothing about `bc2serve`, so that a review works with no server at all.

- **`bc2serve/`** is the web application. FastAPI, with the interface as plain HTML, CSS and JavaScript and no build step. It has the Design, Jobs, Results, Review and Storage tabs, the job queue and GPU allocation, and it starts and serves reviews. It also contains `sge.py`, the backend for SGE clusters described in [On an SGE cluster](#on-an-sge-cluster); it is not needed to run on a single machine. Dependencies are in `bc2serve/requirements.txt`.
- **`binderscope/`** is the `binder_review` package. It reads BindCraft run folders and builds a static review site that also opens straight from disk in a browser. It can be used by itself, without the web service, and it has its own `README.md` and a `docs/` directory with a page per analysis. Start with `binderscope/README.md`.
- **`install.sh`** is the one installer: it asks which install you want and sets it up.
- **`bc2-serve.sh`** installs and runs the service, as above. It is short and well commented, and is the most reliable account of how it is started.
- **`LICENSE`** covers Binderscope, as set out above.

The project's other working notes (the architecture notes, the build plan and the like) are internal and are not part of this repository. This README and the files named above are the documentation that ships.

---

## About this document

`./install.sh --local` has been run from scratch on a GPU workstation: a fresh BindCraft 2 v1.0.3 checkout, then the installer from a clean clone, then a campaign submitted through the service, which ran and was reviewed. `--sge` has been run end to end on a production SGE cluster in launcher-only mode (see [Status](#status)). The review-only install has been run on Rocky Linux 9 with no root, and not yet on a clean Mac. If a step does not work as written, the step is wrong, not you.
