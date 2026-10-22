"""Tests for bc2serve.storage. Trees are built in temporary directories; nothing here
touches a real results root."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from bc2serve import storage


def write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def disk(path) -> int:
    st = os.lstat(path)
    return st.st_blocks * 512


class TempTree(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        # realpath: macOS temp dirs sit behind a /var -> /private/var symlink.
        self.base = Path(os.path.realpath(holder.name))
        self.tree = self.base / "tree"
        self.tree.mkdir()


class MeasureTests(TempTree):
    def test_known_tree_matches_block_arithmetic(self):
        write(self.tree / "a.txt", 10_000)
        write(self.tree / "sub" / "b.bin", 123_456)
        write(self.tree / "sub" / "deeper" / "c.bin", 1)
        write(self.tree / "empty" / ".keep", 0)

        expected_dirs = sum(disk(p) for p in [self.tree, self.tree / "sub",
                                              self.tree / "sub" / "deeper", self.tree / "empty"])
        expected_files = sum(disk(p) for p in [self.tree / "a.txt", self.tree / "sub" / "b.bin",
                                               self.tree / "sub" / "deeper" / "c.bin",
                                               self.tree / "empty" / ".keep"])
        result = storage.measure(self.tree)

        self.assertEqual(result.bytes, expected_dirs + expected_files)
        self.assertEqual(result.files, 4)
        self.assertEqual(result.unreadable, 0)
        self.assertFalse(result.missing)

    def test_children_breakdown_one_level_deep(self):
        write(self.tree / "2_Refolded" / "Complexes" / "x.pdb", 50_000)
        write(self.tree / "compile_cache" / "k" / "v.so", 20_000)
        write(self.tree / "campaign.log", 3_000)

        result = storage.measure(self.tree)

        self.assertEqual(set(result.children), {"2_Refolded", "compile_cache", "campaign.log"})
        self.assertEqual(result.children["2_Refolded"].kind, "dir")
        self.assertEqual(result.children["campaign.log"].kind, "file")
        self.assertEqual(result.children["campaign.log"].bytes, disk(self.tree / "campaign.log"))
        refolded = (disk(self.tree / "2_Refolded") + disk(self.tree / "2_Refolded" / "Complexes")
                    + disk(self.tree / "2_Refolded" / "Complexes" / "x.pdb"))
        self.assertEqual(result.children["2_Refolded"].bytes, refolded)
        self.assertEqual(result.children["2_Refolded"].files, 1)
        self.assertEqual(sum(c.bytes for c in result.children.values()) + result.root_bytes,
                         result.bytes)

    def test_symlink_to_large_file_is_not_followed(self):
        big = write(self.base / "elsewhere" / "big.bin", 4_000_000)
        write(self.tree / "real.txt", 1_000)
        link = self.tree / "link.bin"
        os.symlink(big, link)

        result = storage.measure(self.tree)

        self.assertLess(result.bytes, 1_000_000)
        self.assertEqual(result.symlinks, 1)
        self.assertEqual(result.symlink_paths, (str(link),))
        self.assertEqual(result.files, 1)
        self.assertEqual(result.bytes, disk(self.tree) + disk(self.tree / "real.txt") + disk(link))

    def test_symlink_to_directory_is_not_followed(self):
        write(self.base / "elsewhere" / "big.bin", 2_000_000)
        os.symlink(self.base / "elsewhere", self.tree / "linked_dir")

        result = storage.measure(self.tree)

        self.assertLess(result.bytes, 500_000)
        self.assertEqual(result.symlinks, 1)
        self.assertEqual(result.children["linked_dir"].kind, "symlink")

    def test_symlink_loop_terminates_and_adds_nothing(self):
        write(self.tree / "a" / "file", 1_000)
        os.symlink(self.tree / "a", self.tree / "a" / "loop")
        os.symlink(self.tree / "a" / "loop2", self.tree / "a" / "loop2")  # points at itself

        started = time.monotonic()
        result = storage.measure(self.tree)

        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result.symlinks, 2)
        self.assertEqual(result.files, 1)
        expected = (disk(self.tree) + disk(self.tree / "a") + disk(self.tree / "a" / "file")
                    + disk(self.tree / "a" / "loop") + disk(self.tree / "a" / "loop2"))
        self.assertEqual(result.bytes, expected)

    def test_symlinked_root_reports_the_link_not_the_target(self):
        write(self.base / "real" / "big.bin", 1_000_000)
        link = self.base / "home_link"
        os.symlink(self.base / "real", link)

        result = storage.measure(link)

        self.assertEqual(result.root_symlink, str(self.base / "real"))
        self.assertEqual(result.symlinks, 1)
        self.assertLess(result.bytes, 100_000)

    def test_hard_link_counted_once(self):
        original = write(self.tree / "one" / "data.bin", 100_000)
        (self.tree / "two").mkdir()
        os.link(original, self.tree / "two" / "alias.bin")

        result = storage.measure(self.tree)

        expected = (disk(self.tree) + disk(self.tree / "one") + disk(self.tree / "two")
                    + disk(original))
        self.assertEqual(result.bytes, expected)
        self.assertEqual(result.files, 1)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads everything")
    def test_unreadable_directory_is_counted_not_fatal(self):
        write(self.tree / "ok" / "file", 5_000)
        write(self.tree / "locked" / "hidden", 500_000)
        locked = self.tree / "locked"
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)

        result = storage.measure(self.tree)

        self.assertEqual(result.unreadable, 1)
        self.assertEqual(result.unreadable_paths, (str(locked),))
        expected = (disk(self.tree) + disk(self.tree / "ok") + disk(self.tree / "ok" / "file")
                    + disk(locked))
        self.assertEqual(result.bytes, expected)
        self.assertEqual(result.files, 1)

    def test_missing_root(self):
        result = storage.measure(self.base / "nope")
        self.assertTrue(result.missing)
        self.assertEqual(result.bytes, 0)
        self.assertEqual(result.unreadable, 0)

    def test_to_dict_is_json_ready(self):
        import json
        write(self.tree / "f", 10)
        json.dumps(storage.measure(self.tree).to_dict())


class BlockingMeasurer:
    """Counts walks, announces when one has begun, and holds it until released."""

    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, path: str) -> storage.Measurement:
        with self._lock:
            self.calls += 1
        self.started.set()
        self.release.wait(10)
        return storage.Measurement(path=path, bytes=42, files=1)


class SingleFlightTests(TempTree):
    def test_first_call_does_not_block_and_ten_callers_start_one_walk(self):
        measurer = BlockingMeasurer()
        self.addCleanup(measurer.release.set)
        cache = storage.StorageCache(measurer)
        path = str(self.tree)

        began = time.monotonic()
        first = cache.get("campaign:a", path)
        self.assertLess(time.monotonic() - began, 1.0)  # (a) returned while the walk is stuck
        self.assertTrue(first["measuring"])
        self.assertIsNone(first["measurement"])
        self.assertIsNone(first["measured_at"])
        self.assertTrue(measurer.started.wait(5))

        barrier = threading.Barrier(10)
        views = []

        def caller():
            barrier.wait()
            views.append(cache.get("campaign:a", path))

        threads = [threading.Thread(target=caller) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

        self.assertEqual(len(views), 10)
        self.assertTrue(all(v["measuring"] for v in views))
        self.assertEqual(measurer.calls, 1)  # (b)

        measurer.release.set()
        self.assertTrue(cache.join(5))
        done = cache.get("campaign:a", path)  # (c)
        self.assertFalse(done["measuring"])
        self.assertEqual(done["measurement"]["bytes"], 42)
        self.assertIsNotNone(done["measured_at"])
        self.assertFalse(done["stale"])
        self.assertEqual(measurer.calls, 1)

    def test_keys_are_independent(self):
        slow = BlockingMeasurer()
        self.addCleanup(slow.release.set)

        def measurer(path):
            if path.endswith("slow"):
                return slow(path)
            return storage.Measurement(path=path, bytes=7)

        cache = storage.StorageCache(measurer)
        (self.base / "slow").mkdir()
        (self.base / "fast").mkdir()
        cache.get("slow", str(self.base / "slow"))
        cache.get("fast", str(self.base / "fast"))
        self.assertTrue(slow.started.wait(5))

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            view = cache.get("fast", str(self.base / "fast"))
            if view["measurement"]:
                break
            time.sleep(0.01)
        self.assertEqual(view["measurement"]["bytes"], 7)
        self.assertTrue(cache.get("slow", str(self.base / "slow"))["measuring"])

    def test_walks_in_flight_are_capped(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        lock = threading.Lock()
        running = {"now": 0, "peak": 0}

        def measurer(path):
            with lock:
                running["now"] += 1
                running["peak"] = max(running["peak"], running["now"])
            gate.wait(0.3)
            with lock:
                running["now"] -= 1
            return storage.Measurement(path=path)

        cache = storage.StorageCache(measurer, max_concurrent=2)
        for i in range(6):
            cache.get(f"k{i}", str(self.tree))
        self.assertTrue(cache.join(10))
        self.assertLessEqual(running["peak"], 2)


class StalenessTests(TempTree):
    def make(self):
        calls = []

        def measurer(path):
            calls.append(path)
            return storage.Measurement(path=path, bytes=len(calls))

        return storage.StorageCache(measurer), calls

    def test_unchanged_fingerprint_does_not_remeasure(self):
        cache, calls = self.make()
        path = str(self.tree)
        cache.get("k", path, "t")
        cache.join(5)
        for _ in range(3):
            view = cache.get("k", path, "t")
        cache.join(5)
        self.assertEqual(len(calls), 1)
        self.assertFalse(view["stale"])
        self.assertFalse(view["measuring"])

    def test_changed_token_remeasures_and_old_value_is_served_meanwhile(self):
        cache, calls = self.make()
        path = str(self.tree)
        cache.get("k", path, "running")
        cache.join(5)
        view = cache.get("k", path, "done")
        self.assertTrue(view["stale"])
        self.assertEqual(view["measurement"]["bytes"], 1)  # stale value still shown
        cache.join(5)
        self.assertEqual(len(calls), 2)
        self.assertEqual(cache.get("k", path, "done")["measurement"]["bytes"], 2)

    def test_changed_directory_mtime_remeasures(self):
        cache, calls = self.make()
        path = str(self.tree)
        cache.get("k", path)
        cache.join(5)
        stat = os.stat(path)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        cache.get("k", path)
        cache.join(5)
        self.assertEqual(len(calls), 2)

    def test_refresh_forces_a_walk(self):
        cache, calls = self.make()
        path = str(self.tree)
        cache.get("k", path)
        cache.join(5)
        view = cache.refresh("k", path)
        self.assertTrue(view["measuring"])
        cache.join(5)
        self.assertEqual(len(calls), 2)

    def test_refresh_during_a_walk_walks_again_but_never_in_parallel(self):
        measurer = BlockingMeasurer()
        self.addCleanup(measurer.release.set)
        cache = storage.StorageCache(measurer)
        path = str(self.tree)
        cache.get("k", path)
        self.assertTrue(measurer.started.wait(5))
        cache.refresh("k", path)
        self.assertEqual(measurer.calls, 1)
        measurer.release.set()
        self.assertTrue(cache.join(5))
        self.assertEqual(measurer.calls, 2)

    def test_forget_drops_the_entry(self):
        cache, calls = self.make()
        path = str(self.tree)
        cache.get("k", path)
        cache.join(5)
        cache.forget("k")
        self.assertIsNone(cache.get("k", path)["measurement"])
        cache.join(5)
        self.assertEqual(len(calls), 2)


class FailureTests(TempTree):
    def test_cached_failure_is_not_retried_immediately(self):
        now = [1000.0]
        calls = []

        def measurer(path):
            calls.append(1)
            raise RuntimeError("disk on fire")

        cache = storage.StorageCache(measurer, retry_failed_after=60, clock=lambda: now[0])
        path = str(self.tree)

        cache.get("k", path, "t1")
        cache.join(5)
        view = cache.get("k", path, "t1")
        self.assertEqual(view["error"], "RuntimeError: disk on fire")
        self.assertTrue(view["stale"])
        self.assertFalse(view["measuring"])

        # A changed token would normally mean stale; after a failure, time decides.
        now[0] += 30
        cache.get("k", path, "t2")
        cache.join(5)
        self.assertEqual(len(calls), 1)

        now[0] += 31
        cache.get("k", path, "t2")
        cache.join(5)
        self.assertEqual(len(calls), 2)

    def test_refresh_ignores_the_backoff(self):
        calls = []

        def measurer(path):
            calls.append(1)
            raise OSError("nope")

        cache = storage.StorageCache(measurer, retry_failed_after=3600)
        path = str(self.tree)
        cache.get("k", path)
        cache.join(5)
        cache.refresh("k", path)
        cache.join(5)
        self.assertEqual(len(calls), 2)

    def test_failure_keeps_the_last_good_result_and_recovers(self):
        outcomes = [storage.Measurement(path="p", bytes=5), RuntimeError("boom"),
                    storage.Measurement(path="p", bytes=9)]

        def measurer(path):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        cache = storage.StorageCache(measurer, retry_failed_after=3600)
        path = str(self.tree)
        cache.get("k", path)
        cache.join(5)
        cache.refresh("k", path)
        cache.join(5)
        view = cache.get("k", path)
        self.assertEqual(view["measurement"]["bytes"], 5)
        self.assertIn("boom", view["error"])
        cache.refresh("k", path)
        cache.join(5)
        view = cache.get("k", path)
        self.assertEqual(view["measurement"]["bytes"], 9)
        self.assertIsNone(view["error"])


class FreeSpaceTests(TempTree):
    def test_two_roots_on_one_filesystem_share_an_entry(self):
        a = self.base / "a"
        b = self.base / "b"
        a.mkdir()
        b.mkdir()
        result = storage.free_space([a, b, a])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["roots"], [str(a), str(b)])
        self.assertGreater(result[0]["total"], 0)
        self.assertEqual(result[0]["total"] - result[0]["free"], result[0]["used"])

    def test_different_filesystems_are_separate(self):
        a = self.base / "a"
        b = self.base / "b"
        a.mkdir()
        b.mkdir()
        devices = {str(a): 1, str(b): 2}
        with mock.patch.object(storage, "_device_of", side_effect=lambda p: devices[p]):
            result = storage.free_space([a, b])
        self.assertEqual([r["roots"] for r in result], [[str(a)], [str(b)]])

    def test_missing_root_is_reported_not_raised(self):
        result = storage.free_space([self.base / "gone"])
        self.assertEqual(len(result), 1)
        self.assertIn("error", result[0])
        self.assertTrue(result[0]["error"])


class ReclaimableTests(unittest.TestCase):
    CHILDREN = {
        "compile_cache": storage.ChildSize(86_000_000, 10, "dir"),
        "2_Refolded": storage.ChildSize(900_000_000, 400, "dir"),
        "campaign.log": storage.ChildSize(2_000_000, 1, "file"),
        "1_Trajectories": storage.ChildSize(5, 1, "dir"),
    }

    def by_kind(self, state):
        items = storage.classify_campaign(self.CHILDREN, state, base="/results/camp")
        return {item["kind"]: item for item in items}

    def test_compile_cache_reclaimable_only_when_terminal(self):
        for state in ("done", "failed", "cancelled"):
            item = self.by_kind(state)["compile_cache"]
            self.assertTrue(item["reclaimable"], state)
            self.assertEqual(item["campaign_state"], state)
            self.assertTrue(item["rebuilt_automatically"])
        for state in ("running", "queued", "", None, "mystery"):
            item = self.by_kind(state)["compile_cache"]
            self.assertFalse(item["reclaimable"], state)
            self.assertTrue(item["requires_terminal"])

    def test_refolded_is_never_reclaimable(self):
        for state in ("done", "running", "failed", None):
            item = self.by_kind(state)["refolded"]
            self.assertFalse(item["reclaimable"])
            self.assertIn("GPU hours", item["cost"])
            self.assertIn("passed", item["reason"])        # one word for that quantity, UI-CONVENTIONS
            self.assertEqual(item["bytes"], 900_000_000)

    def test_logs_and_unrecognised_children(self):
        kinds = self.by_kind("done")
        self.assertTrue(kinds["campaign_log"]["reclaimable"])
        self.assertNotIn("1_Trajectories", [i["name"] for i in kinds.values()])
        self.assertFalse(self.by_kind("running")["campaign_log"]["reclaimable"])

    def test_paths_are_plain_and_nothing_resembles_a_command(self):
        items = storage.classify_campaign(self.CHILDREN, "done", base="/results/camp")
        self.assertEqual([i["bytes"] for i in items], sorted((i["bytes"] for i in items),
                                                             reverse=True))
        for item in items:
            self.assertTrue(item["path"].startswith("/results/camp/"))
            self.assertNotIn("rm ", " ".join(str(v) for v in item.values()))

    def test_accepts_plain_ints_and_dicts(self):
        items = storage.classify_campaign({"compile_cache": 10, "2_Refolded": {"bytes": 20}},
                                          "done")
        self.assertEqual({i["name"]: i["bytes"] for i in items},
                         {"compile_cache": 10, "2_Refolded": 20})

    def test_classify_does_not_touch_the_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "compile_cache"
            target.mkdir()
            storage.classify_campaign({"compile_cache": 1}, "done", base=tmp)
            self.assertTrue(target.exists())

    def test_orphaned_reviews(self):
        self.assertEqual(storage.orphaned_reviews(["a", "b", "c"], ["a", "c", "d"]), ["b"])
        item = storage.classify_orphan_review("b", 123, "/results/_reviews/b")
        self.assertTrue(item["reclaimable"])
        self.assertEqual(item["bytes"], 123)

    def test_server_log_and_uploads(self):
        log = storage.classify_server_log(5, "/state/bc2serve.log")
        self.assertTrue(log["reclaimable"])
        uploads = storage.classify_uploads(5, "/state/uploads")
        self.assertFalse(uploads["reclaimable"])

    def test_is_terminal(self):
        self.assertTrue(storage.is_terminal("done"))
        self.assertTrue(storage.is_terminal(" Failed "))
        self.assertFalse(storage.is_terminal("running"))
        self.assertFalse(storage.is_terminal(None))


if __name__ == "__main__":
    unittest.main()
