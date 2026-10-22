"""Turning what the web form collects into a BindCraft2 campaign file.

The presets are read from the repository rather than copied here, so a BindCraft
update that adds a target or a modality shows up in the UI without changing this.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Binder formats. A scaffolded format takes its length from the scaffold, so the
# UI hides the length control for those rather than sending a length that would
# be ignored or refused.
FORMATS = [
    ("binder", "De novo binder", "A new miniprotein folded from nothing", [60, 180]),
    ("large_binder", "Large binder", "Over 300 residues, with extended optimisation", [250, 600]),
    ("peptide", "Linear peptide", "Short, judged bound without needing a free fold", [12, 25]),
    ("cyclic_peptide", "Cyclic peptide", "Closed head to tail", [7, 16]),
    ("homo_oligomer", "Homo-oligomer", "Identical copies of one chain; length is per copy", [40, 120]),
    ("multidomain", "Multidomain", "Several domains on one chain", [120, 300]),
    ("VHH", "VHH nanobody", "Single-domain antibody; CDRs designed on a scaffold", None),
    ("ARP", "Ankyrin repeat", "Consensus ankyrin-repeat scaffold", None),
    ("scFv", "scFv", "Paired antibody variable domains, two chains", None),
    ("Fab", "Fab", "Heavy and light chains with constant domains", None),
]

CONFORMATIONAL = [
    ("induced_fit", "Induced fit", "The interface moves at least 5 A on binding"),
    ("fold_switch", "Fold switch", "The whole fold differs between free and bound"),
]

PROPERTIES = [
    ("forced_targeting", "Focused epitope", "Concentrate binding on the chosen hotspots"),
    ("humanize", "Humanize", "Human-like sequence features (in development)"),
    ("protease_stable", "Protease resistant", "Penalise cleavage motifs and exposed loops"),
    ("disulfide_staple", "Disulfide staple", "Allow cysteine and require a detected disulfide"),
    ("mixed_topology", "Mixed topology", "At most 50% helix, at least 20% sheet"),
    ("termini_together", "Termini together", "N and C termini within 10 A"),
    ("termini_accessible", "Termini accessible", "Both chain ends point away from the target"),
    ("initial_guess", "Initial guess", "Re-predict the designs that passed from the folded pose"),
    ("bigbang", "Big bang", "Seed the gradient stages from the origin too"),
]

RESIDUE_SPEC = re.compile(r"[A-Za-z]?\d+(-\d+)?(\s*,\s*[A-Za-z]?\d+(-\d+)?)*")


class BadCampaign(ValueError):
    """Something the form sent cannot be made into a campaign."""


def shipped_targets(repo: Path) -> list[dict]:
    """The targets BindCraft ships, with their structures resolved to real paths."""
    found = []
    for path in sorted((repo / "settings" / "target").glob("*.json")):
        try:
            preset = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        entries = []
        for target in preset.get("targets", []):
            target = dict(target)
            structure = target.get("target_path")
            if structure and not Path(structure).is_absolute():
                target["target_path"] = str((path.parent / structure).resolve())
            entries.append(target)
        found.append({
            "name": path.stem,
            "description": preset.get("description", ""),
            "targets": entries,
        })
    return found


def presets(repo: Path) -> dict:
    """Everything the form needs to build itself."""
    return {
        "targets": shipped_targets(repo),
        "formats": [{"name": n, "label": l, "blurb": b, "lengths": r}
                    for n, l, b, r in FORMATS],
        "conformational": [{"name": n, "label": l, "blurb": b} for n, l, b in CONFORMATIONAL],
        "properties": [{"name": n, "label": l, "blurb": b} for n, l, b in PROPERTIES],
    }


def _clean_residues(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not RESIDUE_SPEC.fullmatch(value):
        raise BadCampaign(
            f"{field} should look like 54,56,66-70 (or A54,B12-16 across chains), not {value!r}")
    return re.sub(r"\s+", "", value)


#: What a campaign may be called. The authority for this, imported by
#: review.py rather than copied: a name that the form accepts but the review
#: pages refuse is a campaign that runs and then cannot be reviewed, its URL a
#: 404 with nothing to say why. Leading letter or digit, then word characters,
#: dots or dashes, up to 128 in all.
#:
#: It also does the path safety. A name is joined to a results root, so a name
#: that is only dots climbs out of it: `..` resolved to the root's PARENT and
#: wrote campaign.json there, which on a machine whose results live on a shared
#: disk means scattering a campaign across someone else's space. Requiring a
#: leading letter or digit refuses `..`, `.` and `.hidden` together, and the
#: length cap keeps a name from reaching the filesystem's own limit, which used
#: to surface as an uncaught OSError and a 500.
CAMPAIGN_NAME = re.compile(r"[A-Za-z0-9][\w.-]{0,127}")

#: Names a campaign may not take, because a review route already answers to
#: them: `{campaign}` matches any one segment, so a campaign called "projects"
#: or "import" would never reach its own describe endpoint.
RESERVED_NAMES = frozenset({"project", "projects", "import", "imports"})


def _whole(form: dict, field: str, default: int, least: int, what: str) -> int:
    """A whole number from the form, or a refusal the page can show.

    Without this a letter in a number field raised ValueError out of the
    handler and the person was told only that the server had failed."""
    raw = form.get(field)
    if raw is None or raw == "":
        return max(least, default)
    try:
        return max(least, int(str(raw).strip()))
    except (TypeError, ValueError):
        raise BadCampaign(f"{what} must be a whole number, not {str(raw)[:40]!r}.")


def build(repo: Path, results_root: Path, form: dict,
          allowed_roots: list[Path] | None = None) -> tuple[dict, Path, Path]:
    """Returns (campaign, campaign_file, project_folder) for what the form describes."""
    typed = (form.get("name") or "").strip()
    # Separators and anything else unusable become underscores, so a name is
    # always one path segment: "/etc/passwd" is a campaign called
    # "_etc_passwd", not a path. Then the leading punctuation is trimmed and
    # the length capped, which is what makes the result a name CAMPAIGN_NAME
    # accepts -- and so a campaign the review pages will serve.
    name = re.sub(r"[^\w.-]+", "_", typed).lstrip("._-")[:128]
    if not name:
        # Reached by "", by punctuation alone, and by "." and ".." -- the names
        # that used to resolve to the results root or its parent.
        raise BadCampaign(
            f"{typed!r} leaves nothing to name a campaign with. Start it with a letter "
            "or a number." if typed else "Give the campaign a name.")
    # A review of a whole project is served under /api/review/project/... and
    # /api/review/projects, and a run brought in from elsewhere under
    # /api/review/import(s). A campaign of any of those names would shadow those
    # routes and lose its own describe endpoint, so the words are not available.
    if name.lower() in RESERVED_NAMES:
        raise BadCampaign(
            f"{name!r} is reserved: Binderscope serves its own pages under that name. "
            "Choose another.")
    if not CAMPAIGN_NAME.fullmatch(name):          # belt and braces; the shaping above should suffice
        raise BadCampaign(
            f"{name!r} cannot be a campaign name. It has to start with a letter or a "
            "number and be at most 128 characters.")

    campaign: dict = {"campaign_name": name}

    # --- targets -------------------------------------------------------------
    entries = form.get("targets") or []
    if not entries:
        raise BadCampaign("Choose at least one target.")
    targets = []
    for entry in entries:
        structure = (entry.get("target_path") or "").strip()
        if not structure:
            raise BadCampaign("A target is missing its structure.")
        if not Path(structure).is_file():
            raise BadCampaign(f"No structure at {structure}")
        target: dict = {
            "name": re.sub(r"[^\w.-]+", "_", (entry.get("name") or "target").strip()),
            "target_path": structure,
        }
        chains = (entry.get("chains") or "").strip()
        if chains:
            target["chains"] = re.sub(r"\s+", "", chains)
        hotspots = _clean_residues(entry.get("hotspots", ""), "Hotspots")
        if hotspots:
            target["hotspots"] = hotspots
        coldspots = _clean_residues(entry.get("coldspots", ""), "Coldspots")
        if coldspots:
            target["coldspots"] = coldspots
        if entry.get("objective") == "detarget":
            target["objective"] = "detarget"
        targets.append(target)
    if all(t.get("objective") == "detarget" for t in targets):
        raise BadCampaign("Every target is set to avoid. At least one has to be bound.")
    campaign["targets"] = targets

    # --- binder format -------------------------------------------------------
    chosen = form.get("format") or "binder"
    known = {n: r for n, _, _, r in FORMATS}
    if chosen not in known:
        raise BadCampaign(f"Unknown binder format {chosen!r}")
    modalities = [chosen]
    conformational = form.get("conformational")
    if conformational:
        if conformational not in {n for n, _, _ in CONFORMATIONAL}:
            raise BadCampaign(f"Unknown conformational objective {conformational!r}")
        modalities.append(conformational)
    campaign["modality"] = ",".join(modalities)

    if known[chosen] is not None:
        low = form.get("length_min")
        high = form.get("length_max")
        try:
            low, high = int(low), int(high)
        except (TypeError, ValueError):
            raise BadCampaign("Binder length needs two whole numbers.")
        if low < 4 or high < low:
            raise BadCampaign("Binder length must be sensible, and the range in order.")
        campaign["binder_lengths"] = [low, high]
        if chosen == "homo_oligomer":
            campaign["copies"] = _whole(form, "copies", 2, 2, "Copies")
        if chosen == "multidomain":
            campaign["n_domains"] = _whole(form, "domains", 2, 2, "Domains")

    # --- properties ----------------------------------------------------------
    allowed = {n for n, _, _ in PROPERTIES}
    for prop in form.get("properties") or []:
        if prop not in allowed:
            raise BadCampaign(f"Unknown design property {prop!r}")
        campaign[prop] = True

    # --- scale ---------------------------------------------------------------
    try:
        wanted = int(form.get("number_of_final_designs") or 10)
        attempts = int(form.get("max_trajectories") or 200)
    except (TypeError, ValueError):
        raise BadCampaign("Designs wanted and attempt budget must be whole numbers.")
    if wanted < 1 or attempts < 1:
        raise BadCampaign("Designs wanted and attempt budget must be at least 1.")
    campaign["number_of_final_designs"] = wanted
    campaign["max_trajectories"] = attempts

    # --- acceptance ----------------------------------------------------------
    filters = {}
    for key, higher in (("i_pTM", True), ("i_pAE", False)):
        raw = form.get(f"filter_{key}")
        if raw in (None, ""):
            continue
        try:
            filters[key] = {"threshold": float(raw), "higher": higher}
        except (TypeError, ValueError):
            raise BadCampaign(f"{key} threshold must be a number.")
    if filters:
        campaign["filters"] = filters

    if form.get("reproducible"):
        campaign["core"] = "benchmark"

    # --- where ---------------------------------------------------------------
    # The offered roots are a convenience, not a fence: a typed path is allowed
    # too, because no fixed list covers where someone keeps a project. What is
    # checked is that the path is absolute, is not inside the installation's own
    # files, and can actually be written.
    roots = [Path(r).resolve() for r in (allowed_roots or [results_root])]
    raw = (form.get("results_root") or "").strip() or str(roots[0])
    chosen = Path(raw).expanduser()
    if not chosen.is_absolute():
        raise BadCampaign(f"Give a full path starting with /, not {raw!r}.")
    chosen = chosen.resolve()

    # Writing a campaign over the code, the environment or the shipped presets
    # would break the installation for everyone.
    repo = Path(repo).resolve()
    protected = [repo / part for part in
                 ("bc2serve", ".venv", ".bindcraft-tools", "settings", "scaffolds",
                  "bindcraft", ".git", "docs", "examples", "containers")]
    if any(chosen == spot or spot in chosen.parents for spot in protected):
        raise BadCampaign(
            f"{chosen} is inside the BindCraft installation. Choose somewhere results "
            "can live, such as one of the offered folders.")

    project = (chosen / name).resolve()
    # The name is checked above, so this cannot be reached by naming alone. It
    # can be reached by a symlink already sitting in the results root under the
    # name being used, which resolve() follows: the check is on where the write
    # would actually land, not on what was typed.
    if chosen not in project.parents:
        raise BadCampaign(
            f"{name!r} would write to {project}, which is outside {chosen}. "
            "Choose another name.")

    # The nearest parent that exists is the one that has to permit writing; the
    # rest of the path is created below.
    existing = project
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not os.access(existing, os.W_OK):
        raise BadCampaign(f"{existing} cannot be written by this service.")

    campaign["project_folder"] = str(project)

    # A worker cannot share a trajectory, so the attempt budget has to clear the
    # worker count or the campaign stalls with nothing to hand out.
    gpus = _whole(form, "gpus", 1, 1, "GPUs")
    workers = gpus * 7
    if campaign["max_trajectories"] <= workers:
        campaign["max_trajectories"] = workers + 1

    try:
        project.mkdir(parents=True, exist_ok=True)
    except OSError as problem:
        raise BadCampaign(f"Could not make {project}: {problem}")
    campaign_file = project / "campaign.json"
    campaign_file.write_text(json.dumps(campaign, indent=2) + "\n")
    return campaign, campaign_file, project
