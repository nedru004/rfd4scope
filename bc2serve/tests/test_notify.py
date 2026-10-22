"""Tests for bc2serve.notify, and for the once-only rule that lives beside it.

No test here may reach the network. Every test class starts from a `urlopen`
that fails the test if it is called, and the tests that exercise `send` replace
it with a recording fake. A test that reaches ntfy is a failed test.

Topic names are the only secret protecting a campaign's progress, so nothing in
this file prints a generated topic: assertions on topics are written as plain
boolean checks, because the usual `assertEqual` and `assertRegex` helpers put the
value in the failure message. `send` prints the topic when a delivery fails, so
those calls run with stdout captured.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from bc2serve import notify


class NoNetwork(unittest.TestCase):
    """Fails any test that tries to open a URL it did not stub itself."""

    def setUp(self) -> None:
        def refuse(*args, **kwargs):
            raise AssertionError("a test reached for the network")
        patch = mock.patch.object(notify.urllib.request, "urlopen", refuse)
        patch.start()
        self.addCleanup(patch.stop)
        env = mock.patch.dict("os.environ")
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("BC2SERVE_NTFY_SERVER", None)


class Response:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Recorder:
    """A stand-in for urlopen that records the request and answers as told."""

    def __init__(self, status=200, error=None):
        self.requests, self.timeouts = [], []
        self.status, self.error = status, error

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.error:
            raise self.error
        return Response(self.status)


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


# ----------------------------------------------------------------------- topics

class TopicTests(NoNetwork):
    PATTERN = re.compile(r"bindcraft-[0-9a-f]+")

    def test_shape_is_prefix_then_hex(self):
        topic = notify.new_topic()
        self.assertTrue(self.PATTERN.fullmatch(topic) is not None, "topic has an unexpected shape")

    def test_the_random_part_is_at_least_64_bits(self):
        # The topic is a bearer secret: whoever knows it can read and post. The
        # property is length, not any particular value.
        random_part = notify.new_topic().split("-", 1)[1]
        self.assertTrue(len(random_part) >= 16, "the random part of a topic is too short to be a secret")

    def test_topics_do_not_repeat(self):
        topics = {notify.new_topic() for _ in range(2000)}
        self.assertTrue(len(topics) == 2000, "new_topic repeated itself")

    def test_two_consecutive_topics_differ(self):
        self.assertTrue(notify.new_topic() != notify.new_topic())

    def test_topics_look_random_not_counted(self):
        # Not a statistical test, only a guard against a counter or a timestamp:
        # across many topics the leading hex digit should take many values.
        firsts = {notify.new_topic().split("-", 1)[1][0] for _ in range(400)}
        self.assertTrue(len(firsts) >= 12, "topics are not spread across the alphabet")

    def test_the_random_part_comes_from_the_secrets_module(self):
        # `random` is not a source of secrets. Replacing token_hex proves which
        # source is consulted without any chance of the real value being shown.
        with mock.patch.object(notify.secrets, "token_hex", return_value="ab" * 8) as asked:
            topic = notify.new_topic()
        asked.assert_called_once()
        self.assertTrue(topic == "bindcraft-" + "ab" * 8)

    def test_the_prefix_can_be_changed_and_is_not_part_of_the_secret(self):
        topic = notify.new_topic("lab")
        self.assertTrue(topic.startswith("lab-"))
        self.assertTrue(len(topic.split("-", 1)[1]) >= 16)

    def test_a_topic_is_safe_to_put_in_a_url(self):
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]+", notify.new_topic()) is not None)


# -------------------------------------------------------------------------- send

class SendTests(NoNetwork):
    def send(self, *args, recorder=None, **kwargs):
        recorder = recorder or Recorder()
        with mock.patch.object(notify.urllib.request, "urlopen", recorder), quiet():
            result = notify.send(*args, **kwargs)
        return result, recorder

    def test_posts_the_message_to_server_slash_topic(self):
        sent, rec = self.send("some-topic", "Title", "Body text")
        self.assertTrue(sent)
        (request,) = rec.requests
        self.assertEqual(request.full_url, "https://ntfy.sh/some-topic")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"Body text")
        self.assertEqual(request.get_header("Title"), "Title")
        self.assertEqual(request.get_header("Priority"), "default")
        self.assertEqual(rec.timeouts, [notify.TIMEOUT_SECONDS])

    def test_the_server_can_come_from_the_environment_or_the_call(self):
        os.environ["BC2SERVE_NTFY_SERVER"] = "https://ntfy.example.invalid/"
        _, rec = self.send("t", "T", "m")
        self.assertEqual(rec.requests[0].full_url, "https://ntfy.example.invalid/t")
        _, rec = self.send("t", "T", "m", server="http://other.example.invalid///")
        self.assertEqual(rec.requests[0].full_url, "http://other.example.invalid/t")

    def test_tags_and_priority_are_sent_only_when_given(self):
        _, rec = self.send("t", "T", "m")
        self.assertFalse(rec.requests[0].has_header("Tags"))
        _, rec = self.send("t", "T", "m", priority="high", tags="warning")
        self.assertEqual(rec.requests[0].get_header("Priority"), "high")
        self.assertEqual(rec.requests[0].get_header("Tags"), "warning")

    def test_a_non_ascii_title_is_made_safe_for_a_header(self):
        _, rec = self.send("t", "café ✓ done", "m")
        title = rec.requests[0].get_header("Title")
        self.assertTrue(title.isascii())
        self.assertTrue(title.startswith("caf"))

    def test_a_non_ascii_message_is_sent_as_utf8(self):
        _, rec = self.send("t", "T", "é✓")
        self.assertEqual(rec.requests[0].data, "é✓".encode("utf-8"))

    def test_an_empty_topic_sends_nothing(self):
        for topic in ("", "   ", None):
            sent, rec = self.send(topic, "T", "m")
            self.assertFalse(sent)
            self.assertEqual(rec.requests, [])

    def test_the_topic_is_trimmed(self):
        _, rec = self.send("  padded  ", "T", "m")
        self.assertEqual(rec.requests[0].full_url, "https://ntfy.sh/padded")

    def test_failures_return_false_and_never_raise(self):
        for error in (urllib.error.URLError("down"), OSError("reset"), ValueError("bad url"),
                      TimeoutError("slow")):
            sent, _ = self.send("t", "T", "m", recorder=Recorder(error=error))
            self.assertFalse(sent, repr(error))

    def test_a_server_error_status_is_not_success(self):
        for status in (400, 404, 429, 500, 503):
            sent, _ = self.send("t", "T", "m", recorder=Recorder(status=status))
            self.assertFalse(sent, status)
        for status in (200, 201, 204):
            sent, _ = self.send("t", "T", "m", recorder=Recorder(status=status))
            self.assertTrue(sent, status)

    def test_the_guard_itself_works(self):
        # If this ever stops raising, the "no network" promise of the file is void.
        with self.assertRaises(AssertionError):
            notify.urllib.request.urlopen("http://example.invalid/")


# ------------------------------------------------------------- the halfway rule

class FractionDoneTests(unittest.TestCase):
    def counts(self, ranked=0, attempts=0):
        return {"ranked": ranked, "passed": ranked, "rejected": 0, "attempts": attempts}

    def test_is_the_nearer_of_the_two_finishing_lines(self):
        # The rule is max(ranked/wanted, attempts/budget): BindCraft's target counts
        # final designs, which is the ranked shortlist, not everything that passed.
        self.assertEqual(notify.fraction_done(self.counts(5, 20), 10, 200), 0.5)     # designs lead
        self.assertEqual(notify.fraction_done(self.counts(1, 100), 10, 200), 0.5)    # attempts lead
        self.assertEqual(notify.fraction_done(self.counts(2, 20), 10, 200), 0.2)
        self.assertEqual(notify.fraction_done(self.counts(3, 190), 10, 200), 0.95)

    def test_either_line_alone_reaches_halfway(self):
        self.assertGreaterEqual(notify.fraction_done(self.counts(5, 0), 10, 200), 0.5)
        self.assertGreaterEqual(notify.fraction_done(self.counts(0, 100), 10, 200), 0.5)

    def test_it_is_not_the_average_and_not_the_minimum(self):
        # With 5/10 designs and 0/200 attempts, an average would say 0.25 and a
        # minimum 0. The campaign stops at the first line it reaches, so it is 0.5.
        self.assertEqual(notify.fraction_done(self.counts(5, 0), 10, 200), 0.5)

    def test_just_under_and_just_over(self):
        self.assertLess(notify.fraction_done(self.counts(4, 99), 10, 200), 0.5)
        self.assertGreater(notify.fraction_done(self.counts(6, 0), 10, 200), 0.5)

    def test_it_can_exceed_one(self):
        self.assertEqual(notify.fraction_done(self.counts(20, 0), 10, 200), 2.0)

    def test_an_unknown_target_does_not_divide_by_zero(self):
        self.assertEqual(notify.fraction_done(self.counts(5, 50), 0, 0), 0.0)
        self.assertEqual(notify.fraction_done(self.counts(5, 100), 0, 200), 0.5)
        self.assertEqual(notify.fraction_done(self.counts(5, 100), 10, 0), 0.5)

    def test_nothing_done_is_zero(self):
        self.assertEqual(notify.fraction_done(self.counts(), 10, 200), 0.0)


# ------------------------------------------------------------ reading the tables

class ProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)

    def table(self, relative: str, text: str) -> None:
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_a_folder_with_nothing_in_it_reads_as_zero(self):
        self.assertEqual(notify.progress(self.folder),
                         {"ranked": 0, "passed": 0, "rejected": 0, "attempts": 0})

    def test_a_folder_that_does_not_exist_reads_as_zero(self):
        self.assertEqual(notify.progress(self.folder / "gone")["attempts"], 0)
        self.assertIsNone(notify.best_design(self.folder / "gone"))

    def test_counts_come_from_the_three_tables(self):
        self.table("3_Ranked/!_Ranked.csv", "design,i_pTM\na,0.9\nb,0.8\n")
        self.table("2_Refolded/!_Refolded.csv",
                   "design,outcome\na,passed\nb,passed\nc,rejected\nd,failed_filters\ne,\n")
        self.table("1_Trajectories/!_Trajectories.csv", "design\n1\n2\n3\n4\n5\n6\n")
        # passed and rejected both come from the refolded table and add up to its
        # scored rows (the blank outcome is not yet scored); ranked is its own quantity.
        self.assertEqual(notify.progress(self.folder),
                         {"ranked": 2, "passed": 2, "rejected": 2, "attempts": 6})

    def test_the_shortlist_and_what_passed_are_counted_apart(self):
        # The real shape: one best sequence per backbone is ranked, its siblings passed too.
        self.table("3_Ranked/!_Ranked.csv", "design,i_pTM\na_seq0,0.9\n")
        self.table("2_Refolded/!_Refolded.csv",
                   "design,outcome\na_candidate1,passed\na_candidate2,passed\nb_candidate1,rejected\n")
        counts = notify.progress(self.folder)
        self.assertEqual((counts["ranked"], counts["passed"], counts["rejected"]), (1, 2, 1))

    def test_header_only_and_empty_files_are_zero_rows(self):
        self.table("3_Ranked/!_Ranked.csv", "design,i_pTM\n")
        self.table("1_Trajectories/!_Trajectories.csv", "")
        counts = notify.progress(self.folder)
        self.assertEqual((counts["ranked"], counts["passed"], counts["attempts"]), (0, 0, 0))

    def test_best_design_is_the_first_ranked_row(self):
        self.table("3_Ranked/!_Ranked.csv", "design,i_pTM,i_pDAE\ntop,0.91,3.2\nnext,0.80,4.0\n")
        best = notify.best_design(self.folder)
        self.assertEqual((best["design"], best["i_pTM"]), ("top", "0.91"))

    def test_best_design_of_an_empty_table_is_none(self):
        self.table("3_Ranked/!_Ranked.csv", "design,i_pTM\n")
        self.assertIsNone(notify.best_design(self.folder))


# ----------------------------------------------------------------- the messages

class SpellTests(unittest.TestCase):
    def test_durations(self):
        for seconds, words in ((None, ""), (0, ""), (-5, ""), (45, "45s"), (59.9, "59s"),
                               (60, "1m"), (125, "2m"), (3600, "1h 0m"), (3700, "1h 1m"),
                               (90000, "25h 0m")):
            self.assertEqual(notify._spell(seconds), words, seconds)


class ComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)
        self.counts = {"ranked": 2, "passed": 3, "rejected": 4, "attempts": 20}

    def job(self, **over):
        job = {"name": "demo", "project_folder": str(self.folder), "started_at": 1000.0,
               "_now": 1000.0 + 3700, "state": "done", "wanted": 10, "budget": 200,
               "gpus_assigned": "0"}
        job.update(over)
        return job

    def ranked(self):
        path = self.folder / "3_Ranked" / "!_Ranked.csv"
        path.parent.mkdir(parents=True)
        path.write_text("design,i_pTM,i_pDAE\ntop,0.91,3.2\n")

    def test_every_event_returns_four_strings(self):
        for event in notify.EVENTS:
            parts = notify.compose(event, self.job(), self.counts)
            self.assertEqual(len(parts), 4, event)
            self.assertTrue(all(isinstance(p, str) and p for p in parts), event)

    def test_started(self):
        title, message, priority, _ = notify.compose("running", self.job(gpus_assigned="2,3"), self.counts)
        self.assertEqual(title, "demo started")
        self.assertIn("2,3", message)
        self.assertEqual(priority, "low")

    def test_first_design_quotes_the_best_scores_when_they_exist(self):
        self.ranked()
        _, message, _, _ = notify.compose("first", self.job(), self.counts)
        self.assertIn("i_pTM 0.91", message)
        self.assertIn("1h 1m", message)
        _, bare, _, _ = notify.compose("first", self.job(project_folder=str(self.folder / "x")), self.counts)
        self.assertNotIn("i_pTM", bare)

    def test_halfway_says_what_the_targets_are(self):
        title, message, _, _ = notify.compose("halfway", self.job(), self.counts)
        self.assertIn("halfway", title)
        self.assertIn("10 designs", message)
        self.assertIn("200 attempts", message)

    def test_finished_with_designs(self):
        self.ranked()
        title, message, priority, tags = notify.compose("done", self.job(), self.counts)
        self.assertEqual(title, "demo finished: 3 passed")
        self.assertIn("Best i_pTM 0.91", message)
        self.assertEqual((priority, tags), ("default", "white_check_mark"))

    def test_finished_with_nothing_is_a_warning_not_a_celebration(self):
        counts = dict(self.counts, passed=0, ranked=0)
        title, message, _, tags = notify.compose("done", self.job(), counts)
        self.assertIn("nothing passed", title)
        self.assertEqual(tags, "warning")
        self.assertIn("Loosening", message)

    def test_the_tally_names_each_quantity_and_adds_up(self):
        # It used to read "3 accepted, 4 rejected" where 3 was the ranked shortlist
        # and 4 came from the refolded table, so the two did not belong together.
        _, message, _, _ = notify.compose("done", self.job(), self.counts)
        self.assertIn("3 passed (2 ranked), 4 rejected, 20 attempts", message)

    def test_cancelled(self):
        title, message, _, _ = notify.compose("done", self.job(state="cancelled"), self.counts)
        self.assertEqual(title, "demo was stopped")
        self.assertIn("kept", message)

    def test_failed_carries_the_reason_and_is_urgent(self):
        title, message, priority, _ = notify.compose(
            "done", self.job(state="failed", message="out of memory"), self.counts)
        self.assertEqual(title, "demo failed")
        self.assertIn("out of memory", message)
        self.assertEqual(priority, "high")
        _, message, _, _ = notify.compose("done", self.job(state="failed"), self.counts)
        self.assertIn("see the log", message)

    def test_no_start_time_leaves_the_duration_out_rather_than_inventing_one(self):
        _, message, _, _ = notify.compose("halfway", self.job(started_at=None, _now=0), self.counts)
        self.assertNotIn(" in.", message)
        self.assertNotIn("in,", message)


# ----------------------------------------------- an event is told at most once
#
# The rule lives in JobStore (jobs.py), which imports review.py and so needs numpy
# and biotite. Skipped where they are absent. The store is built with its two
# threads never started, so nothing here schedules, launches or sends anything:
# `_announce` is called directly and the postman's queue is read.

def _store_importable() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("numpy", "biotite"))


@unittest.skipUnless(_store_importable(), "bc2serve.jobs needs numpy and biotite")
class OnceOnlyTests(NoNetwork):
    def setUp(self) -> None:
        super().setUp()
        from bc2serve import jobs
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        with mock.patch.object(threading.Thread, "start", lambda self: None):
            self.store = jobs.JobStore(self.tmp / "repo", self.tmp / "state")

    def job(self, *, topic="fixture-topic", wanted=10, budget=200, state="running", kind="design"):
        folder = self.tmp / "campaign"
        job_id = self.store.submit("demo", "", folder / "campaign.json", folder, 1,
                                   notify_topic=topic, wanted=wanted, budget=budget, kind=kind)
        with sqlite3.connect(self.store.database) as db:
            db.execute("UPDATE jobs SET state=?, started_at=? WHERE id=?", (state, time.time(), job_id))
        return job_id

    def row(self, job_id):
        with self.store._connect() as db:
            return db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def announce(self, job_id, event):
        with self.store._lock, self.store._connect() as db:
            self.store._announce(db, db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), event)

    def queued(self):
        out = []
        while not self.store._notices.empty():
            out.append(self.store._notices.get_nowait())
        return out

    def write_table(self, relative, rows):
        path = self.tmp / "campaign" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("design,outcome\n" + "".join(f"{n},passed\n" for n in range(rows)))

    def test_an_event_is_queued_once_however_often_it_is_announced(self):
        job_id = self.job()
        for _ in range(5):
            self.announce(job_id, "halfway")
        sent = self.queued()
        self.assertEqual([event for _, event, _ in sent], ["halfway"])
        self.assertEqual(self.row(job_id)["notified"], "halfway")

    def test_different_events_are_each_told_once(self):
        job_id = self.job()
        for event in ("running", "first", "halfway", "first", "done", "running", "done"):
            self.announce(job_id, event)
        self.assertEqual([e for _, e, _ in self.queued()], ["running", "first", "halfway", "done"])
        self.assertEqual(set(self.row(job_id)["notified"].split(",")),
                         {"running", "first", "halfway", "done"})

    def test_the_event_is_marked_told_before_it_is_sent(self):
        # Deliberate: a failing ntfy must not be retried every tick forever.
        job_id = self.job()
        self.announce(job_id, "first")
        self.assertIn("first", self.row(job_id)["notified"])

    def test_no_topic_means_no_notice_and_no_record(self):
        for topic in ("", "   "):
            job_id = self.job(topic=topic)
            self.announce(job_id, "done")
            self.assertEqual(self.queued(), [])
            self.assertEqual(self.row(job_id)["notified"], "")

    def test_jobs_do_not_share_their_told_events(self):
        a, b = self.job(), self.job()
        self.announce(a, "done")
        self.announce(b, "done")
        self.assertEqual(len(self.queued()), 2)

    def test_a_restart_does_not_repeat_what_was_already_told(self):
        from bc2serve import jobs
        job_id = self.job()
        self.announce(job_id, "first")
        self.queued()
        with mock.patch.object(threading.Thread, "start", lambda self: None):
            reopened = jobs.JobStore(self.tmp / "repo", self.tmp / "state")
        with reopened._lock, reopened._connect() as db:
            reopened._announce(db, db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), "first")
        self.assertTrue(reopened._notices.empty())

    # -- the watcher: first design, and halfway by whichever line is nearer

    def watch(self):
        self.store._watch_progress()
        return [event for _, event, _ in self.queued()]

    def test_halfway_by_attempts_alone(self):
        job_id = self.job(wanted=10, budget=200)
        self.write_table("1_Trajectories/!_Trajectories.csv", 120)       # 0.6 of the budget
        self.assertEqual(self.watch(), ["halfway"])
        self.assertEqual(self.row(job_id)["notified"], "halfway")

    def test_halfway_by_designs_alone(self):
        self.job(wanted=10, budget=200)
        self.write_table("3_Ranked/!_Ranked.csv", 5)                      # exactly 0.5 of the wanted
        self.assertEqual(sorted(self.watch()), ["first", "halfway"])

    def test_the_boundary_is_inclusive_at_a_half(self):
        self.job(wanted=10, budget=200)
        self.write_table("1_Trajectories/!_Trajectories.csv", 100)        # exactly 0.5 of the budget
        self.assertEqual(self.watch(), ["halfway"])

    def test_below_half_says_nothing(self):
        self.job(wanted=10, budget=200)
        self.write_table("1_Trajectories/!_Trajectories.csv", 99)
        self.assertEqual(self.watch(), [])

    def test_past_the_finish_line_there_is_no_halfway_to_announce(self):
        self.job(wanted=10, budget=200)
        self.write_table("1_Trajectories/!_Trajectories.csv", 200)
        self.assertEqual(self.watch(), [])

    def test_the_first_design_is_told_once_and_independently_of_halfway(self):
        job_id = self.job(wanted=100, budget=1000)
        self.write_table("3_Ranked/!_Ranked.csv", 1)
        self.assertEqual(self.watch(), ["first"])
        self.assertEqual(self.watch(), [])                                # next tick: silence
        self.write_table("1_Trajectories/!_Trajectories.csv", 600)
        self.assertEqual(self.watch(), ["halfway"])
        self.assertEqual(self.watch(), [])
        self.assertEqual(set(self.row(job_id)["notified"].split(",")), {"first", "halfway"})

    def test_many_ticks_with_the_same_progress_send_one_notice(self):
        self.job(wanted=10, budget=200)
        self.write_table("1_Trajectories/!_Trajectories.csv", 150)
        told = []
        for _ in range(10):
            told += self.watch()
        self.assertEqual(told, ["halfway"])

    def test_only_running_design_campaigns_with_a_topic_are_watched(self):
        for kwargs in ({"state": "queued"}, {"state": "done"}, {"kind": "analysis"}, {"topic": ""}):
            tmp_job = self.job(**kwargs)
            self.write_table("1_Trajectories/!_Trajectories.csv", 150)
            self.assertEqual(self.watch(), [], kwargs)
            self.assertEqual(self.row(tmp_job)["notified"], "", kwargs)

    def test_notice_goes_out_through_notify_send_and_nowhere_else(self):
        # End to end through the postman's own code path, with the HTTP stubbed.
        job_id = self.job()
        self.announce(job_id, "running")
        (topic, event, job), = self.queued()
        rec = Recorder()
        title, message, priority, tags = notify.compose(event, job, notify.progress(job["project_folder"]))
        with mock.patch.object(notify.urllib.request, "urlopen", rec), quiet():
            notify.send(topic, title, message, priority=priority, tags=tags)
        self.assertEqual(len(rec.requests), 1)
        self.assertTrue(rec.requests[0].full_url.endswith("/fixture-topic"))


if __name__ == "__main__":
    unittest.main()
