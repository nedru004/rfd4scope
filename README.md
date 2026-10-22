# Binderscope

Binderscope is a web application for running protein binder design campaigns on a GPU machine and deciding which designs to order. It is the launcher, the GPU queue and the review: it starts campaigns, shares the cards between them, watches them, and turns a finished campaign into an offline review site.

**BindCraft 2 is the engine it drives.** Binderscope does no design itself. BindCraft 2 chooses the binding sites, generates the binders and applies the filters that decide which designs passed. The two names are not interchangeable in this document or in the program.

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

## Licence: you must not host this for other labs

Binderscope's own code is MIT (see `LICENSE`).

BindCraft 2 is not. It is published under a source-available licence with a hosting restriction. You may redistribute it for others to run themselves. You may **not** offer it as a hosted web or API service without written permission from the copyright holders, who include the University of Zurich.

Binderscope is a web application whose material functionality is BindCraft 2. So if you install Binderscope and open it to other labs, you are the party breaking BindCraft 2's licence, and nothing in the software will tell you so. The MIT grant on Binderscope gives you no rights over BindCraft 2 to do this.

Running it for yourself, or for people in your own organisation, is what the `LICENSE` file describes as fine. The restricted case is exposing it to people outside. Read BindCraft 2's own licence before you rely on this summary; the wording there is what binds you.

One factual distinction, not legal advice: the review-only install described below contains no BindCraft 2 code and never runs it. It reads folders BindCraft has already written. What that means for you is a question for the licences themselves.

---

## Two ways to install this

**The whole thing** — the launcher, the GPU queue and the review — is what the
rest of this document describes. It needs a GPU machine and a working
BindCraft 2, and the requirements below are its requirements.

**The review on its own.** If you run BindCraft yourself, or somebody hands you
a run folder, you need none of that. `binderscope/` is a self-contained Python
package with its own installer and its own small app. It reads BindCraft 1 and
BindCraft 2 run folders wherever they are, builds the review and opens it:

```bash
cd binderscope && ./install.sh          # macOS; the command-line parts work on Linux too
```

No GPU, no AlphaFold weights, no BindCraft checkout, no web service, no
account, nothing uploaded. Python 3.12 or later is the only thing it asks for,
and it makes its own environment. `binderscope/README.md` is its documentation.

The two installs are independent and can live on the same machine: the
review-only one keeps its projects in `~/Binderscope` and touches nothing the
full one uses. Going the other way, the full install can review a run it did
not start — paste the run folder's path into **A run from somewhere else** on
its Review tab.

---

## Requirements

- An NVIDIA GPU, on the machine that will run the service. Campaigns run on it.
- About 2.5 GB of AlphaFold weights, which BindCraft 2 loads. Binderscope does not fetch them; they are part of getting BindCraft 2 working.
- Python 3.12 or later. The `binder_review` package sets `requires-python >=3.12`.
- A working BindCraft 2 checkout with its virtual environment at `.venv/` (see the next section). Binderscope does not install BindCraft 2 for you.
- For `./bc2-serve.sh install`, a Linux system with systemd and user services. Without systemd you can still start it by hand.

---

## The layout contract: this repository is an overlay

**This repository does not run on its own.** It is laid over a BindCraft 2 checkout. The directories `bindcraft/`, `settings/`, `scaffolds/` and `examples/` come from upstream BindCraft 2 and are not in this repository.

The reason is in the code, not in preference:

- `bc2serve` expects to live inside a BindCraft 2 checkout. Its `REPO` is the parent of the `bc2serve/` directory, unless you set `BINDCRAFT_HOME`.
- `campaign.py` reads its presets from `<repo>/settings/target/*.json`. It reads them from BindCraft 2 rather than keeping its own copies, so they stay in step with the engine.
- The service starts campaigns with `<repo>/.venv/bin/bindcraft` and runs reviews with `<repo>/.venv/bin/binder-review`. Those paths are fixed.
- The web pages are found relative to the code, and importing the app creates its state and results folders and starts its worker threads.

For these reasons `bc2serve` is deliberately **not** an installable package. A `pyproject.toml` would suggest an independence it does not have. It has a `requirements.txt` instead, and you copy or clone this repository on top of your BindCraft 2 checkout.

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

## Install, start, connect

Do this on the machine with the GPUs. The commands assume you are in the BindCraft 2 checkout, with this repository's files laid over it, and that BindCraft 2 itself already works.

**1. Install the dependencies into the BindCraft 2 environment.**

```
.venv/bin/pip install -r bc2serve/requirements.txt
.venv/bin/pip install ./binderscope
```

The second line is required. `binderscope/` is the `binder_review` package, which the service imports and whose `binder-review` command it runs. It is not on PyPI, so it cannot be listed in `requirements.txt` as a version. On an older system add `--only-binary=:all:` to both commands: without it pip may try to build numpy or scipy from source and fail.

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

## One instance per person

This is the architecture, not a missing feature. The service is a systemd *user* service. Each person who uses the machine runs their own, with their own port, their own queue database, their own results and their own settings, and the operating system keeps them apart. There is no multi-user mode, there are no accounts, and none is planned. If two people want to use Binderscope on one machine, each installs their own instance.

---

## What is in this repository

Two halves, which ship together and install together. `bc2serve` imports `binder_review`; `binder_review` knows nothing about `bc2serve`, so that a review works with no server at all.

- **`bc2serve/`** is the web application. FastAPI, with the interface as plain HTML, CSS and JavaScript and no build step. It has the Design, Jobs, Results, Review and Storage tabs, the job queue and GPU allocation, and it starts and serves reviews. It also contains `sge.py`, a backend for the Grid Engine job scheduler used on some clusters. That backend is not documented here and is not needed to run on a single machine. Dependencies are in `bc2serve/requirements.txt`.
- **`binderscope/`** is the `binder_review` package. It reads BindCraft run folders and builds a static review site that also opens straight from disk in a browser. It can be used by itself, without the web service, and it has its own `README.md` and a `docs/` directory with a page per analysis. Start with `binderscope/README.md`.
- **`bc2-serve.sh`** installs and runs the service, as above. It is short and well commented, and is the most reliable account of how it is started.
- **`LICENSE`** covers Binderscope, as set out above.

The project's other working notes (the architecture notes, the build plan and the like) are internal and are not part of this repository. This README and the files named above are the documentation that ships.

---

## About this document

This README was written from a working install on one machine, not from a clean install on another. The install steps are believed correct and have not been run on a fresh machine. If a step does not work as written, the step is wrong, not you.
