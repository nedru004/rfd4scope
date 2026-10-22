"""What is on disk, how much of it, and what could be given back.

This exists so the Storage page can answer "where did the disk go?" without ever
making the rest of the UI slow. The two halves are deliberately separate: a walker
that is plain and synchronous (`measure`), and a cache that keeps a walk from ever
happening inside a request (`StorageCache`).

Why the cache is not the obvious one. `/api/cluster` computes inline, under no
lock, and forgets failures; that is harmless for `qstat` and wrong for a directory
walk. A walk of a results root takes seconds to minutes on a shared disk, so the
first request after expiry would block for all of it, two browser tabs would start
two walks, and each would hold a worker thread from the same pool the 5-second job
poll uses. A pile-up there stalls the whole UI, not just this page. So `get`
returns whatever is cached at once and never walks; a missing or stale key starts
at most one daemon thread, decided under a lock so concurrent callers cannot both
start one. There is no periodic walker on purpose: nobody opens Storage for weeks
and it would read a shared disk for nothing.

Decisions that are not obvious from the code:

* Sizes are `st_blocks * 512`, not `st_size`, because the number gets compared
  against `du`, which reports allocation. Sparse and tiny files differ a lot
  between the two. Directories are counted too, as `du` does. Where the platform
  has no `st_blocks`, `st_size` is the fallback.
* Symlinks are never followed and their targets are never counted. A symlinked
  review home genuinely occurs (review.py's `_discard` handles one), and following
  links would double-count a target or walk off into a loop. If the *root* is a
  symlink the result says so and carries only the link itself; whoever wants the
  target measures the target.
* Hard links are counted once, on `(st_dev, st_ino)`. Only entries with
  `st_nlink > 1` are remembered, so the set stays small on a tree of ordinary files.
* Nothing unreadable aborts a walk. Each failure is counted (and the first few paths
  kept), so a total is always a lower bound and the page can say by how much it
  might be wrong.
* Freshness is `(path, directory mtime_ns, caller token)`. A directory's mtime only
  moves when a *direct* child is added or removed, so it cannot see a campaign
  writing deep inside `1_Trajectories`. That is what the token is for: the caller
  passes something that changes while a campaign runs (or a constant once it has
  finished), and the cache does not have to know what a campaign is.
* A failed walk is cached with a retry delay that wins over a changed fingerprint.
  A running campaign changes its fingerprint constantly, so without that a failing
  tree would be re-walked on every poll. Only `refresh` overrides it. A failure
  keeps the last good result, because a stale size beats an empty row.
* The reclaimable classification only reports. It carries a path to show and never
  builds a command: the page has no delete button, and `2_Refolded` in particular
  is not reclaimable at all, however large it is.

The campaign states below mirror `jobs.py` (`queued` and `running` are live;
`done`, `failed` and `cancelled` are not). They are copied rather than imported so
this module stays free of the job store's side effects, and an unrecognised state
is treated as not terminal: the safe direction to be wrong.
"""

from __future__ import annotations

import os
import shutil
import stat as statmod
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

# Enough to see what is wrong without a tree of denied directories bloating a payload.
MAX_REPORTED_PATHS = 20

# How long a failed walk is left alone. Long enough that a poll cannot turn a
# permission problem into a retry storm, short enough to recover by itself.
RETRY_FAILED_AFTER = 300.0

# Walks running at once across all keys. Each key still gets its own thread, so one
# slow tree cannot hold up the rest, but a page that finds forty campaigns missing
# should not read a shared disk forty ways at the same moment.
MAX_CONCURRENT_WALKS = 2

TERMINAL_STATES = frozenset({"done", "failed", "cancelled"})


# --------------------------------------------------------------------------- walker

@dataclass(frozen=True)
class ChildSize:
    """One entry directly under the measured root, with everything beneath it."""
    bytes: int
    files: int
    kind: str  # "dir", "file" or "symlink"

    def to_dict(self) -> Dict[str, Any]:
        return {"bytes": self.bytes, "files": self.files, "kind": self.kind}


@dataclass(frozen=True)
class Measurement:
    path: str
    bytes: int = 0
    files: int = 0
    unreadable: int = 0
    unreadable_paths: Tuple[str, ...] = ()
    symlinks: int = 0
    symlink_paths: Tuple[str, ...] = ()
    # One level deep. Their sum is `bytes` minus `root_bytes`, the root directory's own
    # allocation, which belongs to no child.
    children: Mapping[str, ChildSize] = field(default_factory=dict)
    root_bytes: int = 0
    missing: bool = False
    root_symlink: Optional[str] = None  # the link's target, when the root itself is a link
    seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "bytes": self.bytes,
            "files": self.files,
            "unreadable": self.unreadable,
            "unreadable_paths": list(self.unreadable_paths),
            "symlinks": self.symlinks,
            "symlink_paths": list(self.symlink_paths),
            "children": {name: size.to_dict() for name, size in self.children.items()},
            "root_bytes": self.root_bytes,
            "missing": self.missing,
            "root_symlink": self.root_symlink,
            "seconds": round(self.seconds, 3),
        }


def _disk_bytes(st: os.stat_result) -> int:
    blocks = getattr(st, "st_blocks", None)
    if blocks is None:
        return st.st_size
    return blocks * 512


def measure(path: "os.PathLike[str] | str") -> Measurement:
    """Walk `path` once and total what it occupies on disk. Never raises."""
    started = time.monotonic()
    root = os.fspath(path)

    try:
        root_st = os.lstat(root)
    except FileNotFoundError:
        return Measurement(path=root, missing=True, seconds=time.monotonic() - started)
    except OSError:
        return Measurement(path=root, unreadable=1, unreadable_paths=(root,),
                           seconds=time.monotonic() - started)

    root_bytes = _disk_bytes(root_st)

    if statmod.S_ISLNK(root_st.st_mode):
        try:
            target: Optional[str] = os.readlink(root)
        except OSError:
            target = ""
        return Measurement(path=root, bytes=root_bytes, symlinks=1, symlink_paths=(root,),
                           root_bytes=root_bytes, root_symlink=target,
                           seconds=time.monotonic() - started)

    if not statmod.S_ISDIR(root_st.st_mode):
        return Measurement(path=root, bytes=root_bytes, files=1, root_bytes=root_bytes,
                           seconds=time.monotonic() - started)

    total = root_bytes
    files = 0
    unreadable = 0
    unreadable_paths: List[str] = []
    symlinks = 0
    symlink_paths: List[str] = []
    seen_inodes = set()
    # name -> [bytes, files, kind]; filled as the walk goes so nothing is walked twice.
    children: Dict[str, List[Any]] = {}

    def note_unreadable(where: str) -> None:
        nonlocal unreadable
        unreadable += 1
        if len(unreadable_paths) < MAX_REPORTED_PATHS:
            unreadable_paths.append(where)

    # Iterative, so a deep tree cannot hit the recursion limit and the walk holds no
    # more than one open directory handle.
    stack: List[Tuple[str, Optional[str]]] = [(root, None)]
    while stack:
        directory, top = stack.pop()
        try:
            iterator = os.scandir(directory)
        except OSError:
            note_unreadable(directory)
            continue
        with iterator:
            while True:
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                except OSError:
                    note_unreadable(directory)
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    note_unreadable(entry.path)
                    continue

                key = top if top is not None else entry.name
                mode = st.st_mode

                if statmod.S_ISDIR(mode):
                    size = _disk_bytes(st)
                    child = children.setdefault(key, [0, 0, "dir"])
                    stack.append((entry.path, key))
                elif statmod.S_ISLNK(mode):
                    size = _disk_bytes(st)
                    symlinks += 1
                    if len(symlink_paths) < MAX_REPORTED_PATHS:
                        symlink_paths.append(entry.path)
                    child = children.setdefault(key, [0, 0, "symlink"])
                else:
                    if st.st_nlink > 1:
                        inode = (st.st_dev, st.st_ino)
                        if inode in seen_inodes:
                            continue
                        seen_inodes.add(inode)
                    size = _disk_bytes(st)
                    files += 1
                    child = children.setdefault(key, [0, 0, "file"])
                    child[1] += 1

                total += size
                child[0] += size

    return Measurement(
        path=root,
        bytes=total,
        files=files,
        unreadable=unreadable,
        unreadable_paths=tuple(unreadable_paths),
        symlinks=symlinks,
        symlink_paths=tuple(symlink_paths),
        children={name: ChildSize(c[0], c[1], c[2]) for name, c in children.items()},
        root_bytes=root_bytes,
        seconds=time.monotonic() - started,
    )


# --------------------------------------------------------------------------- cache

@dataclass
class _Entry:
    fingerprint: Tuple[Any, ...]
    result: Optional[Measurement]
    measured_at: Optional[float]
    error: Optional[str] = None
    failed_at: Optional[float] = None


def _fingerprint(path: str, token: Any) -> Tuple[Any, ...]:
    # lstat, to agree with the walker about what a symlinked root is. One stat is the
    # only filesystem touch `get` ever makes itself.
    try:
        mtime: Optional[int] = os.lstat(path).st_mtime_ns
    except OSError:
        mtime = None
    return (path, mtime, token)


class StorageCache:
    """Stale-while-revalidate sizes, one background walk per key at most.

    `get` and `refresh` return a plain dict at once and never wait for a walk:

        key, path        as passed
        measurement      `Measurement.to_dict()` of the last good walk, or None
        measured_at      wall-clock time that walk finished, or None
        measuring        a walk for this key is running or waiting its turn
        stale            what is shown predates the current fingerprint (or failed)
        error            why the latest walk failed, or None
    """

    def __init__(self, measurer: Callable[[str], Measurement] = measure, *,
                 retry_failed_after: float = RETRY_FAILED_AFTER,
                 max_concurrent: int = MAX_CONCURRENT_WALKS,
                 clock: Callable[[], float] = time.time) -> None:
        self._measurer = measurer
        self._retry_after = retry_failed_after
        self._clock = clock
        self._gate = threading.BoundedSemaphore(max(1, max_concurrent))
        self._lock = threading.Lock()
        self._entries: Dict[str, _Entry] = {}
        self._inflight: Dict[str, threading.Thread] = {}
        # A refresh that arrives mid-walk cannot trust that walk, which may have read the
        # tree before the change the caller is refreshing for. It re-walks once more.
        self._rerun: Dict[str, Tuple[str, Any]] = {}

    # -- public

    def get(self, key: str, path: str, token: Any = "") -> Dict[str, Any]:
        path = os.fspath(path)
        fingerprint = _fingerprint(path, token)
        with self._lock:
            entry = self._entries.get(key)
            if key not in self._inflight and self._needs_walk(entry, fingerprint):
                self._start(key, path, fingerprint)
            return self._view(key, path, entry, fingerprint)

    def get_many(self, items: Iterable[Tuple[str, str, Any]]) -> Dict[str, Dict[str, Any]]:
        """`get` for several `(key, path, token)` at once, keyed by key."""
        return {key: self.get(key, path, token) for key, path, token in items}

    def refresh(self, key: str, path: str, token: Any = "") -> Dict[str, Any]:
        """Invalidate and re-measure now, ignoring any failure back-off."""
        path = os.fspath(path)
        fingerprint = _fingerprint(path, token)
        with self._lock:
            entry = self._entries.get(key)
            if key in self._inflight:
                self._rerun[key] = (path, token)
            else:
                self._start(key, path, fingerprint)
            return self._view(key, path, entry, fingerprint)

    def forget(self, key: str) -> None:
        """Drop a key whose campaign or review has gone, so the cache does not grow."""
        with self._lock:
            self._entries.pop(key, None)

    def measuring(self) -> bool:
        with self._lock:
            return bool(self._inflight)

    def join(self, timeout: Optional[float] = None) -> bool:
        """Wait for running walks. For tests and shutdown; the server never calls it."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                threads = list(self._inflight.values())
            if not threads:
                return True
            for thread in threads:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                thread.join(remaining)

    # -- internals (call with the lock held unless noted)

    def _needs_walk(self, entry: Optional[_Entry], fingerprint: Tuple[Any, ...]) -> bool:
        if entry is None:
            return True
        if entry.error is not None:
            # Time, not fingerprint, decides after a failure; see the module docstring.
            return self._clock() - (entry.failed_at or 0.0) >= self._retry_after
        return entry.fingerprint != fingerprint

    def _start(self, key: str, path: str, fingerprint: Tuple[Any, ...]) -> None:
        thread = threading.Thread(target=self._run, args=(key, path, fingerprint),
                                  name="storage-walk", daemon=True)
        self._inflight[key] = thread
        thread.start()

    def _view(self, key: str, path: str, entry: Optional[_Entry],
              fingerprint: Tuple[Any, ...]) -> Dict[str, Any]:
        return {
            "key": key,
            "path": path,
            "measurement": entry.result.to_dict() if entry and entry.result else None,
            "measured_at": entry.measured_at if entry else None,
            "measuring": key in self._inflight,
            "stale": entry is None or entry.error is not None or entry.fingerprint != fingerprint,
            "error": entry.error if entry else None,
        }

    def _run(self, key: str, path: str, fingerprint: Tuple[Any, ...]) -> None:
        while True:
            error: Optional[str] = None
            result: Optional[Measurement] = None
            with self._gate:
                try:
                    result = self._measurer(path)
                except Exception as problem:  # a walk must never take the server down
                    error = f"{type(problem).__name__}: {problem}"
            now = self._clock()
            with self._lock:
                if error is None:
                    self._entries[key] = _Entry(fingerprint, result, now)
                else:
                    previous = self._entries.get(key)
                    self._entries[key] = _Entry(
                        fingerprint,
                        previous.result if previous else None,
                        previous.measured_at if previous else None,
                        error=error, failed_at=now)
                again = self._rerun.pop(key, None)
                if again is None:
                    # Same lock hold as the store above: there is no instant at which a
                    # caller sees a stale entry and no walk, so none starts a second.
                    del self._inflight[key]
                    return
            path, token = again
            fingerprint = _fingerprint(path, token)


# --------------------------------------------------------------------------- free space

def _device_of(path: str) -> int:
    return os.stat(path).st_dev


def free_space(roots: Iterable["os.PathLike[str] | str"]) -> List[Dict[str, Any]]:
    """Total, used and free for each *filesystem* the roots live on.

    `/ssd` and `/data` are different filesystems and are reported separately; two
    roots on one filesystem (or the same root listed twice) share a single entry,
    which names every root it serves, so one disk is never shown as two.
    """
    by_device: Dict[int, Dict[str, Any]] = {}
    ordered: List[Dict[str, Any]] = []
    seen_roots = set()
    for root in roots:
        root = os.fspath(root)
        if root in seen_roots:
            continue
        seen_roots.add(root)
        try:
            device = _device_of(root)
            usage = shutil.disk_usage(root)
        except OSError as problem:
            ordered.append({"roots": [root], "error": f"{type(problem).__name__}: {problem}"})
            continue
        existing = by_device.get(device)
        if existing is not None:
            existing["roots"].append(root)
            continue
        entry = {
            "roots": [root],
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent_used": round(100.0 * usage.used / usage.total, 1) if usage.total else None,
            "error": None,
        }
        by_device[device] = entry
        ordered.append(entry)
    return ordered


# --------------------------------------------------------------------------- reclaimable

def is_terminal(state: Optional[str]) -> bool:
    return (state or "").strip().lower() in TERMINAL_STATES


def _bytes_of(size: Any) -> int:
    if isinstance(size, Mapping):
        return int(size.get("bytes", 0))
    return int(getattr(size, "bytes", size))


def _join(base: str, name: str) -> str:
    return os.path.join(base, name) if base else name


def _item(kind: str, name: str, path: str, size: int, *, reclaimable: bool, cost: str,
          reason: str, state: Optional[str] = None, requires_terminal: bool = False,
          rebuilt_automatically: bool = False) -> Dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "path": path,
        "bytes": size,
        "reclaimable": reclaimable,
        "requires_terminal": requires_terminal,
        "campaign_state": state,
        "rebuilt_automatically": rebuilt_automatically,
        "cost": cost,
        "reason": reason,
    }


def classify_campaign(children: Mapping[str, Any], state: Optional[str],
                      base: str = "") -> List[Dict[str, Any]]:
    """What in one campaign folder is, and is not, worth reclaiming. Reports only.

    `children` is the one-level breakdown from `measure` (name -> size, where a size
    is a `ChildSize`, a dict with `bytes`, or a plain int). `base` is the campaign
    folder, used only to give each item a path the page can show. Children this does
    not recognise are left out. Largest first.
    """
    terminal = is_terminal(state)
    shown_state = (state or "").strip() or "unknown"
    items: List[Dict[str, Any]] = []

    for name, size in children.items():
        nbytes = _bytes_of(size)
        path = _join(base, name)

        if name == "compile_cache":
            items.append(_item(
                "compile_cache", name, path, nbytes, state=shown_state,
                reclaimable=terminal, requires_terminal=True, rebuilt_automatically=True,
                cost="Nothing is lost; the next run compiles it again.",
                reason=("The campaign is finished, so nothing is using it." if terminal else
                        f"The campaign is {shown_state}, so it may be in use.")))
        elif name == "2_Refolded":
            items.append(_item(
                "refolded", name, path, nbytes, state=shown_state, reclaimable=False,
                cost="Re-running the campaign: GPU hours.",
                reason=("Complexes/ holds every candidate structure, the ones that passed "
                        "included. It is the review's input, and rebuilding a review "
                        "reads it.")))
        elif name == "campaign.log":
            items.append(_item(
                "campaign_log", name, path, nbytes, state=shown_state,
                reclaimable=terminal, requires_terminal=True,
                cost="The history of how the campaign ran, which is what explains a failure.",
                reason=("Never rotated, so it only grows. Cheap, but it is the only record."
                        if terminal else
                        f"The campaign is {shown_state}, so it is still being written.")))

    items.sort(key=lambda item: item["bytes"], reverse=True)
    return items


def classify_server_log(size: Any, path: str) -> Dict[str, Any]:
    """The server's own log: real, cheap, and never rotated."""
    return _item(
        "server_log", os.path.basename(path), path, _bytes_of(size), reclaimable=True,
        cost="The server's history only; nothing else depends on it.",
        reason=("Never rotated, so it only grows. The running server holds it open, so "
                "emptying it frees space and removing it does not until a restart."))


def classify_uploads(size: Any, path: str) -> Dict[str, Any]:
    """Uploaded construct files. Listed so the cost is visible, but not reclaimable."""
    return _item(
        "uploads", os.path.basename(path), path, _bytes_of(size), reclaimable=False,
        cost="Campaigns that name these files fail, and so do review rebuilds.",
        reason=("Campaigns reference construct files here, and a review refuses to "
                "build when one is missing."))


def orphaned_reviews(review_names: Iterable[str], campaign_names: Iterable[str],
                     members_left: Optional[Mapping[str, int]] = None) -> List[str]:
    """Review folders (`_reviews/<name>`) whose campaign folder no longer exists.

    A review whose name begins with "@" is a project's, pooled across several campaigns,
    and it has no campaign folder of its own by design: the name is not a folder anywhere.
    Judged like the rest it would always be an orphan, and the Storage page would offer to
    reclaim the one review a person built across a whole project. So it is skipped here and
    judged by the question that is true of it instead: is anything left to build it from.
    `members_left` says, per project review, how many of the campaigns it was built from
    still exist; a project review is an orphan when that is zero. One that is not in the
    mapping (its record could not be read, so its members are unknown) is not reported,
    because "unknown" is not "gone" and this is advice about what could be deleted.
    """
    campaigns = set(campaign_names)
    left = members_left or {}
    return sorted(name for name in review_names
                  if (left.get(name) == 0 if name.startswith("@") else name not in campaigns))


def classify_orphan_review(name: str, size: Any, path: str) -> Dict[str, Any]:
    """A review whose campaign is gone. Nothing will read it again, but it is also the
    only copy of that analysis, and it cannot be rebuilt without the campaign."""
    if name.startswith("@"):
        return _item(
            "orphan_review", name, path, _bytes_of(size), reclaimable=True,
            cost="The finished review itself: every campaign it was built from is gone, "
                 "so it cannot be rebuilt.",
            reason="None of the campaign folders it was pooled from exists any more.")
    return _item(
        "orphan_review", name, path, _bytes_of(size), reclaimable=True,
        cost="The finished review itself: its campaign is gone, so it cannot be rebuilt.",
        reason="Its campaign folder no longer exists.")
