"""Tests for bc2serve.sge, the SGE backend.

Everything here runs without a cluster. The parsers and the snapshot builder are
pure functions over text; the functions that shell out are driven through a fake
for the module's own `_run`, so no scheduler client is ever executed.

EVERY QSTAT FIXTURE IN THIS FILE IS SYNTHETIC. Host names, queue names and card
counts are invented to exercise the formats the parser reads (`qstat -f -F ...`
and `whichq`); none was captured from a real cluster. The host names end in
`.invalid`, which can never resolve, and the queue names are made up. The one
real-looking name is `all.q`, because the code under test treats it specially
and the test has to use it to reach that branch.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from bc2serve import sge

# --------------------------------------------------------------------- fixtures

MINE = {"LAB-GPU", "all.q"}          # queues "this user" may submit to, in these tests


def block(queue, host, *, resv=0, used=0, total=32, state="", ngpus=None,
          names=None, cards=None) -> str:
    """One queue instance in the layout `qstat -f -F ngpus,gpu.names,...` prints.

    SYNTHETIC: assembled from the format the parser reads, not captured anywhere.
    """
    head = f"{queue}@{host}".ljust(44) + f"BIP   {resv}/{used}/{total}".ljust(18) + "0.50     lx-amd64"
    if state:
        head += f"  {state}"
    lines = [head]
    if names is not None:
        lines.append("\thl:gpu.names=" + ";".join(names))
    if ngpus is not None:
        lines.append(f"\tqc:ngpus={ngpus}")
    for index, free in (cards or {}).items():
        lines.append(f"\thl:gpu.cuda.{index}.mem_free={free}")
    return "\n".join(lines)


RULE = "-" * 70
COLUMNS = "queuename                      qtype resv/used/tot. load_avg arch          states"


def qstat(*blocks: str) -> str:
    return COLUMNS + "\n" + RULE + "\n" + ("\n" + RULE + "\n").join(blocks) + "\n"


def instances_for(hosts: dict, queues: list, **kw) -> list:
    """`hosts` maps host -> (names, ngpus); each host is listed once per queue.

    SYNTHETIC. This is the shape that produced "8 free of 7 total": the same
    physical node appearing under several queues.
    """
    blocks = []
    for host, (names, ngpus) in hosts.items():
        for queue in queues:
            blocks.append(block(queue, host, names=names, ngpus=ngpus, **kw))
    return sge.parse_instances(qstat(*blocks))


def snap(instances, mine=MINE) -> dict:
    return sge.build_snapshot(instances, set(mine))


def gpu(snapshot: dict, model: str) -> dict:
    return next(g for g in snapshot["gpus"] if g["model"] == model)


class Sane:
    """The invariants no snapshot may ever break, whatever the fixture."""

    def assertSane(self, snapshot: dict) -> None:
        for g in snapshot["gpus"]:
            label = g["model"]
            self.assertGreaterEqual(g["free"], 0, label)
            self.assertLessEqual(g["free"], g["total"], f"{label}: more free than exist")
            self.assertLessEqual(g["evictable"], g["total"], f"{label}: evictable > total")
            self.assertLessEqual(g["free"] + g["evictable"], g["total"],
                                 f"{label}: free and evictable overlap")
            self.assertLessEqual(g["nodes_free"], g["nodes"], label)


# ------------------------------------------------------------------------ _to_gb

class ToGbTests(unittest.TestCase):
    def test_every_unit_suffix(self):
        self.assertAlmostEqual(sge._to_gb("79.148G"), 79.148)
        self.assertAlmostEqual(sge._to_gb("10752M"), 10.5)
        self.assertAlmostEqual(sge._to_gb("1048576K"), 1.0)
        self.assertAlmostEqual(sge._to_gb("2T"), 2048.0)

    def test_the_same_memory_in_different_units_agrees(self):
        self.assertAlmostEqual(sge._to_gb("2G"), sge._to_gb("2048M"))
        self.assertAlmostEqual(sge._to_gb("2G"), sge._to_gb("2097152K"))
        self.assertAlmostEqual(sge._to_gb("2G"), sge._to_gb("0.001953125T"))

    def test_a_bare_number_is_bytes(self):
        self.assertAlmostEqual(sge._to_gb("2147483648"), 2.0)

    def test_the_suffix_is_case_insensitive(self):
        self.assertAlmostEqual(sge._to_gb("512m"), 0.5)

    def test_junk_is_zero_rather_than_an_error(self):
        for junk in ("", "G", "abc", "12XG", "--", "1.2.3G", " "):
            self.assertEqual(sge._to_gb(junk), 0.0, repr(junk))


# ------------------------------------------------------------------ _gpu_model

class GpuModelTests(unittest.TestCase):
    def test_a100_is_not_caught_by_the_a10_rule(self):
        self.assertEqual(sge._gpu_model("NVIDIA A100-SXM4-80GB"), ("A100 80GB", "gpu_a100"))
        self.assertEqual(sge._gpu_model("A10"), ("A10", "gpu_a10"))

    def test_l40s_is_not_caught_by_the_l4_rule(self):
        self.assertEqual(sge._gpu_model("NVIDIA L40S")[1], "gpu_l40s")
        self.assertEqual(sge._gpu_model("NVIDIA L4")[1], "gpu_l4")

    def test_v100s_wins_over_v100_and_both_share_a_flag(self):
        self.assertEqual(sge._gpu_model("Tesla V100S-PCIE-32GB")[0], "V100S 32GB")
        self.assertEqual(sge._gpu_model("Tesla V100-PCIE-32GB")[0], "V100 32GB")
        self.assertEqual(sge._gpu_model("Tesla V100S")[1], sge._gpu_model("Tesla V100")[1])

    def test_match_ignores_case(self):
        self.assertEqual(sge._gpu_model("a100")[1], "gpu_a100")

    def test_an_unknown_model_comes_back_as_itself_with_no_flag(self):
        self.assertEqual(sge._gpu_model("Imaginary Accelerator 9000"),
                         ("Imaginary Accelerator 9000", None))

    def test_every_table_entry_finds_itself(self):
        # Guards the ordering comment on GPU_FLAGS: a looser rule listed earlier
        # would shadow a stricter one and this is where it would show.
        for needle, display, flag in sge.GPU_FLAGS:
            self.assertEqual(sge._gpu_model(needle), (display, flag), needle)


class FlagForTests(unittest.TestCase):
    def test_round_trip_from_a_display_name(self):
        for _, display, flag in sge.GPU_FLAGS:
            self.assertEqual(sge._flag_for(display), flag)

    def test_unknown_display_name_has_no_flag(self):
        self.assertIsNone(sge._flag_for("Imaginary Accelerator 9000"))
        self.assertIsNone(sge._flag_for(""))


# ---------------------------------------------------------------- parse_instances

class ParseInstancesTests(unittest.TestCase):
    def test_header_fields(self):
        # SYNTHETIC
        text = qstat(block("LAB-GPU", "gpu-a10-01.example.invalid", resv=1, used=3, total=16))
        (inst,) = sge.parse_instances(text)
        self.assertEqual((inst.queue, inst.host), ("LAB-GPU", "gpu-a10-01.example.invalid"))
        # The header reads resv/used/total, in that order.
        self.assertEqual((inst.reserved, inst.used, inst.total), (1, 3, 16))
        self.assertEqual(inst.free_slots, 12)
        self.assertEqual(inst.state, "")
        self.assertTrue(inst.usable)

    def test_resource_lines_belong_to_the_instance_above_them(self):
        # SYNTHETIC
        text = qstat(
            block("LAB-GPU", "gpu-a10-01.example.invalid", names=["A10", "A10"], ngpus=1,
                  cards={0: "22.5G", 1: "10752M"}),
            block("LAB-GPU", "cpu-01.example.invalid"),
        )
        a, b = sge.parse_instances(text)
        self.assertEqual(a.gpu_names, ["A10", "A10"])
        self.assertEqual(a.ngpus, 1)
        self.assertAlmostEqual(a.card_free_gb[0], 22.5)
        self.assertAlmostEqual(a.card_free_gb[1], 10.5)
        # A node with no resources reported has no lines between the rules at all.
        self.assertEqual((b.gpu_names, b.ngpus, b.card_free_gb), ([], None, {}))

    def test_any_state_flag_makes_it_unusable(self):
        # SYNTHETIC
        for flag in ("au", "d", "E", "S", "A", "u"):
            (inst,) = sge.parse_instances(qstat(block("LAB-GPU", "n.example.invalid", state=flag)))
            self.assertEqual(inst.state, flag)
            self.assertFalse(inst.usable, flag)

    def test_free_slots_never_goes_negative(self):
        # SYNTHETIC: an oversubscribed node
        (inst,) = sge.parse_instances(qstat(block("LAB-GPU", "n.example.invalid",
                                                  resv=2, used=40, total=32)))
        self.assertEqual(inst.free_slots, 0)

    def test_gpu_names_are_stripped_and_empties_dropped(self):
        text = qstat("LAB-GPU@n.example.invalid   BIP   0/0/8   0.1   lx-amd64\n"
                     "\thl:gpu.names= A10 ; ;A10;")
        (inst,) = sge.parse_instances(text)
        self.assertEqual(inst.gpu_names, ["A10", "A10"])

    def test_running_and_pending_job_lines_do_not_become_instances_or_resources(self):
        # SYNTHETIC: what qstat -f prints under an instance with work on it, and
        # then the pending-jobs section, which starts with a hash rule.
        text = (
            qstat(block("LAB-GPU", "n.example.invalid", used=2, names=["A10"], ngpus=0))
            + "    1000001 0.55500 bc2_1      someone      r     01/01/2026 00:00:00     2\n"
            + "\n###########################################################\n"
            + " - PENDING JOBS - PENDING JOBS - PENDING JOBS - PENDING JOBS\n"
            + "###########################################################\n"
            + "    1000002 0.00000 bc2_2      someone      qw    01/01/2026 00:00:00     8\n"
            + "\tqc:ngpus=99\n"          # would be a bug to attribute this to anything
        )
        (inst,) = sge.parse_instances(text)
        self.assertEqual(inst.ngpus, 0)

    def test_resource_lines_before_any_instance_are_ignored(self):
        text = "\tqc:ngpus=5\n" + qstat(block("LAB-GPU", "n.example.invalid"))
        (inst,) = sge.parse_instances(text)
        self.assertIsNone(inst.ngpus)

    def test_unparsable_values_leave_the_field_unset(self):
        text = qstat(block("LAB-GPU", "n.example.invalid", ngpus="many",
                           cards={0: "lots", 1: ""}))
        (inst,) = sge.parse_instances(text)
        self.assertIsNone(inst.ngpus)
        self.assertEqual(inst.card_free_gb, {})


class MalformedOutputTests(unittest.TestCase):
    """qstat can be cut off by a timeout, a broken pipe or a full buffer. None of
    these may raise, and whatever is read must still build a snapshot."""

    def good(self) -> str:
        return qstat(block("LAB-GPU", "gpu-a10-01.example.invalid", names=["A10"], ngpus=1,
                           cards={0: "22.5G"}))

    def check(self, text: str) -> list:
        instances = sge.parse_instances(text)
        sge.build_snapshot(instances, set(MINE))     # must not raise either
        return instances

    def test_empty_and_blank(self):
        for text in ("", "\n", "   \n\t\n", "\n\n\n"):
            self.assertEqual(self.check(text), [], repr(text))

    def test_garbage(self):
        for text in ("not qstat output at all", "\x00\x01\x02", "Error: commlib error: got select timeout",
                     "-" * 200, "@@@@", "a@b", "queue@host 1 2 3"):
            self.assertEqual(self.check(text), [], repr(text))

    def test_header_row_only(self):
        self.assertEqual(self.check(COLUMNS + "\n" + RULE + "\n"), [])

    def test_truncated_at_every_byte_never_raises(self):
        text = self.good()
        for cut in range(len(text) + 1):
            self.check(text[:cut])

    def test_truncation_inside_an_instance_line_drops_that_line_only(self):
        whole = qstat(block("LAB-GPU", "a.example.invalid"), block("LAB-GPU", "b.example.invalid"))
        cut = whole[:whole.index("b.example.invalid") + 20]     # mid-header of the second
        found = self.check(cut)
        self.assertEqual([i.host for i in found], ["a.example.invalid"])

    def test_truncated_resource_value(self):
        for tail in ("\tqc:ngpus=", "\tqc:ngpus", "\thl:gpu.names", "\thl:gpu.cuda.0.mem_free=",
                     "\thl:gpu.cuda.0.mem_free=12", "\thl:gpu.cuda.x.mem_free=1G"):
            (inst,) = self.check(qstat(block("LAB-GPU", "n.example.invalid")) + tail)
            self.assertIsNone(inst.ngpus, tail)

    def test_crlf_line_endings(self):
        found = self.check(self.good().replace("\n", "\r\n"))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].ngpus, 1)

    def test_snapshot_of_nothing_is_empty_not_an_error(self):
        out = sge.build_snapshot([], set())
        self.assertEqual((out["queues"], out["gpus"]), ([], []))

    def test_whichq_on_junk(self):
        self.assertEqual(sge.parse_whichq(""), set())
        self.assertEqual(sge.parse_whichq("\n\n  \n"), set())
        sge.parse_whichq("\x00\x01 \x02:")      # nothing meaningful, but it must not raise


# ------------------------------------------------------------------ parse_whichq

class WhichqTests(unittest.TestCase):
    def test_sentence_then_list(self):
        # SYNTHETIC: the sentence is paraphrased, the queue names are invented.
        text = ("You may submit to the following queues:\n"
                "-------------------------------------\n"
                "LAB-GPU\n"
                "all.q\n"
                "PARTNER-GPU\n")
        self.assertEqual(sge.parse_whichq(text), {"LAB-GPU", "all.q", "PARTNER-GPU"})

    def test_lines_with_spaces_or_colons_are_prose(self):
        self.assertEqual(sge.parse_whichq("Queues for user:\nsome words here\nQ1\n"), {"Q1"})

    def test_surrounding_whitespace_and_duplicates(self):
        self.assertEqual(sge.parse_whichq("  Q1  \nQ1\n\tQ2\n"), {"Q1", "Q2"})


# --------------------------------------------------------------- build_snapshot

class QueueCountingTests(unittest.TestCase):
    def test_each_node_is_classified_once(self):
        # SYNTHETIC
        q = "LAB-GPU"
        instances = sge.parse_instances(qstat(
            block(q, "idle.example.invalid", total=16),
            block(q, "partial.example.invalid", used=4, total=16),
            block(q, "full.example.invalid", used=16, total=16),
            block(q, "reserved.example.invalid", resv=16, total=16),
            block(q, "down.example.invalid", total=16, state="au"),
        ))
        out = snap(instances)
        (row,) = out["queues"]
        self.assertEqual((row["nodes"], row["idle"], row["partial"], row["full"], row["offline"]),
                         (5, 1, 1, 2, 1))
        # An offline node contributes no slots, however empty it looks.
        self.assertEqual(row["slots_free"], 16 + 12)
        self.assertTrue(row["mine"])

    def test_queues_are_ordered_mine_first_then_by_name(self):
        # SYNTHETIC
        instances = sge.parse_instances(qstat(
            block("ZZZ", "a.example.invalid"), block("AAA", "a.example.invalid"),
            block("LAB-GPU", "a.example.invalid"), block("all.q", "a.example.invalid")))
        names = [q["name"] for q in snap(instances)["queues"]]
        self.assertEqual(names, ["LAB-GPU", "all.q", "AAA", "ZZZ"])


class GpuCountingTests(unittest.TestCase, Sane):
    def test_regression_free_never_exceeds_total_when_a_host_is_in_several_queues(self):
        """The bug that said "A10: 8 free of 7 total".

        Aggregating per queue instance counted one physical card once for every
        queue its node belongs to, and took the maximum of per-node free counts.
        SYNTHETIC fixture: seven single-card nodes, each listed under three queues.
        """
        hosts = {f"gpu-a10-{n:02d}.example.invalid": (["A10"], 1) for n in range(1, 8)}
        out = snap(instances_for(hosts, ["LAB-GPU", "all.q", "PARTNER-GPU"]))
        self.assertSane(out)
        a10 = gpu(out, "A10")
        self.assertEqual(a10["total"], 7)
        self.assertEqual(a10["nodes"], 7)
        self.assertEqual(a10["free"], 7)          # all seven are free and reachable
        self.assertLessEqual(a10["free"], a10["total"])

    def test_regression_multi_card_hosts_in_several_queues(self):
        # SYNTHETIC: three nodes with two A10s each, listed under three queues.
        hosts = {
            "gpu-a10-01.example.invalid": (["A10", "A10"], 2),
            "gpu-a10-02.example.invalid": (["A10", "A10"], 1),
            "gpu-a10-03.example.invalid": (["A10", "A10"], 0),
        }
        out = snap(instances_for(hosts, ["LAB-GPU", "all.q", "PARTNER-GPU"]))
        self.assertSane(out)
        a10 = gpu(out, "A10")
        self.assertEqual(a10["total"], 6)          # 3 hosts x 2 cards, not 18
        self.assertEqual(a10["free"], 3)           # 2 + 1 + 0
        self.assertEqual(a10["nodes"], 3)
        self.assertEqual(a10["nodes_free"], 2)

    def test_cards_are_counted_once_per_host_however_many_queues_list_it(self):
        hosts = {
            "gpu-a100-01.example.invalid": (["A100", "A100"], 1),
            "gpu-a100-02.example.invalid": (["A100"], 1),
        }
        one = gpu(snap(instances_for(hosts, ["LAB-GPU"])), "A100 80GB")
        three = gpu(snap(instances_for(hosts, ["LAB-GPU", "all.q", "PARTNER-GPU"])), "A100 80GB")
        five = gpu(snap(instances_for(hosts, ["LAB-GPU", "all.q", "PARTNER-GPU", "Q4", "Q5"])),
                   "A100 80GB")
        for key in ("total", "nodes", "free", "nodes_free"):
            self.assertEqual(one[key], three[key], key)
            self.assertEqual(one[key], five[key], key)
        self.assertEqual(one["total"], 3)

    def test_per_instance_ngpus_is_not_summed_across_a_hosts_queues(self):
        # SYNTHETIC: the scheduler reports the node's free count on every
        # instance. Summing would give 3 x 2 = 6 free on a two-card node.
        hosts = {"gpu-a10-01.example.invalid": (["A10", "A10"], 2)}
        a10 = gpu(snap(instances_for(hosts, ["LAB-GPU", "all.q", "PARTNER-GPU"])), "A10")
        self.assertEqual((a10["total"], a10["free"]), (2, 2))

    def test_a_host_with_no_gpu_adds_to_the_queue_but_not_to_any_model(self):
        # SYNTHETIC
        instances = sge.parse_instances(qstat(
            block("LAB-GPU", "cpu-01.example.invalid"),
            block("LAB-GPU", "cpu-02.example.invalid", names=[]),
            block("LAB-GPU", "gpu-a10-01.example.invalid", names=["A10"], ngpus=1)))
        out = snap(instances)
        self.assertSane(out)
        self.assertEqual([g["model"] for g in out["gpus"]], ["A10"])
        self.assertEqual(gpu(out, "A10")["nodes"], 1)
        self.assertEqual(out["queues"][0]["nodes"], 3)

    def test_a_cluster_with_no_gpus_at_all(self):
        out = snap(sge.parse_instances(qstat(block("LAB-GPU", "cpu-01.example.invalid"))))
        self.assertEqual(out["gpus"], [])
        self.assertEqual(len(out["queues"]), 1)

    def test_mixed_models_on_one_host_count_as_capacity_but_never_as_free(self):
        # SYNTHETIC: one node carrying an A10 and an L4, one card free. Which
        # model that card is cannot be known, so neither may claim it.
        hosts = {"gpu-mix-01.example.invalid": (["A10", "L4"], 1)}
        out = snap(instances_for(hosts, ["LAB-GPU", "all.q"]))
        self.assertSane(out)
        for model in ("A10", "L4"):
            g = gpu(out, model)
            self.assertEqual((g["total"], g["nodes"]), (1, 1), model)
            self.assertEqual((g["free"], g["evictable"], g["nodes_free"]), (0, 0, 0), model)

    def test_mixed_host_does_not_disturb_a_pure_host_of_the_same_model(self):
        hosts = {
            "gpu-mix-01.example.invalid": (["A10", "L4"], 1),
            "gpu-a10-01.example.invalid": (["A10"], 1),
        }
        out = snap(instances_for(hosts, ["LAB-GPU"]))
        self.assertSane(out)
        a10 = gpu(out, "A10")
        self.assertEqual((a10["total"], a10["nodes"], a10["free"]), (2, 2, 1))

    def test_a_node_reachable_only_through_all_q_is_evictable_not_free(self):
        # SYNTHETIC
        hosts = {"gpu-a10-01.example.invalid": (["A10"], 1)}
        out = snap(instances_for(hosts, ["all.q"]))
        a10 = gpu(out, "A10")
        self.assertEqual((a10["free"], a10["evictable"]), (0, 1))
        self.assertEqual(a10["via"], ["all.q"])
        self.assertSane(out)

    def test_a_real_queue_beats_all_q_so_a_card_is_not_counted_twice(self):
        hosts = {"gpu-a10-01.example.invalid": (["A10"], 1)}
        out = snap(instances_for(hosts, ["LAB-GPU", "all.q"]))
        a10 = gpu(out, "A10")
        self.assertEqual((a10["free"], a10["evictable"]), (1, 0))
        self.assertEqual(a10["via"], ["LAB-GPU"])

    def test_a_node_in_no_queue_of_mine_has_capacity_but_nothing_free(self):
        hosts = {"gpu-a10-01.example.invalid": (["A10"], 1)}
        a10 = gpu(snap(instances_for(hosts, ["PARTNER-GPU"])), "A10")
        self.assertEqual((a10["total"], a10["free"], a10["evictable"], a10["via"]), (1, 0, 0, []))

    def test_an_offline_instance_offers_nothing_but_still_exists(self):
        hosts = {"gpu-a10-01.example.invalid": (["A10"], 1)}
        a10 = gpu(snap(instances_for(hosts, ["LAB-GPU"], state="d")), "A10")
        self.assertEqual((a10["total"], a10["free"]), (1, 0))

    def test_when_one_instance_of_a_host_is_down_the_others_still_count(self):
        # SYNTHETIC
        host = "gpu-a10-01.example.invalid"
        instances = sge.parse_instances(qstat(
            block("LAB-GPU", host, names=["A10"], ngpus=1, state="au"),
            block("all.q", host, names=["A10"], ngpus=1)))
        a10 = gpu(snap(instances), "A10")
        self.assertEqual(a10["total"], 1)
        self.assertEqual((a10["free"], a10["evictable"]), (0, 1))

    def test_missing_ngpus_means_none_free(self):
        hosts = {"gpu-a10-01.example.invalid": (["A10"], None)}
        a10 = gpu(snap(instances_for(hosts, ["LAB-GPU"])), "A10")
        self.assertEqual((a10["total"], a10["free"]), (1, 0))

    def test_an_unknown_model_is_listed_under_its_own_name_with_no_flag(self):
        hosts = {"gpu-x-01.example.invalid": (["Imaginary Accelerator 9000"], 1)}
        g = gpu(snap(instances_for(hosts, ["LAB-GPU"])), "Imaginary Accelerator 9000")
        self.assertIsNone(g["flag"])
        self.assertEqual(g["total"], 1)

    def test_models_sort_by_what_is_obtainable_then_by_name(self):
        hosts = {
            "a.example.invalid": (["A10"], 0),
            "b.example.invalid": (["A100"], 1),
            "c.example.invalid": (["L4"], 0),
        }
        names = [g["model"] for g in snap(instances_for(hosts, ["LAB-GPU"]))["gpus"]]
        self.assertEqual(names, ["A100 80GB", "A10", "L4"])

    def test_a_fleet_never_breaks_the_invariants(self):
        # SYNTHETIC: a larger mixed fleet, every host in a different set of queues.
        queues_for = [["LAB-GPU"], ["LAB-GPU", "all.q"], ["all.q"], ["PARTNER-GPU", "all.q"],
                      ["LAB-GPU", "PARTNER-GPU", "all.q"]]
        specs = [(["A10"], 1), (["A10", "A10"], 2), (["A100"], 0), (["A100", "A100"], 1),
                 (["L4"], 1), (["A10", "L4"], 1), (["RTX 6000 Ada"], 1)]
        blocks = []
        for n in range(40):
            names, free = specs[n % len(specs)]
            for queue in queues_for[n % len(queues_for)]:
                blocks.append(block(queue, f"fleet-{n:02d}.example.invalid", names=names,
                                    ngpus=free, state="au" if n % 9 == 0 else ""))
        self.assertSane(snap(sge.parse_instances(qstat(*blocks))))


class CardMemoryTests(unittest.TestCase):
    def test_cards_reported_in_different_units_compare_correctly(self):
        # SYNTHETIC: one card in M, one in G. 23000M is 22.46 G, so the G card is roomier.
        host = "gpu-a10-01.example.invalid"
        instances = sge.parse_instances(qstat(block(
            "LAB-GPU", host, names=["A10", "A10"], ngpus=2, cards={0: "23000M", 1: "22.9G"})))
        a10 = gpu(snap(instances), "A10")
        self.assertEqual(a10["free_mem_gb"], 22.9)
        self.assertEqual(a10["card_gb"], 22.9)

    def test_k_and_t_units(self):
        host = "gpu-a100-01.example.invalid"
        instances = sge.parse_instances(qstat(block(
            "LAB-GPU", host, names=["A100", "A100"], ngpus=2,
            cards={0: "1048576K", 1: "0.0771484375T"})))        # 1.0 G and 79.0 G
        g = gpu(snap(instances), "A100 80GB")
        self.assertEqual(g["free_mem_gb"], 79.0)

    def test_memory_is_not_summed_across_a_hosts_queue_instances(self):
        host = "gpu-a10-01.example.invalid"
        cards = {0: "22.5G"}
        instances = sge.parse_instances(qstat(*[
            block(q, host, names=["A10"], ngpus=1, cards=cards)
            for q in ("LAB-GPU", "all.q", "PARTNER-GPU")]))
        self.assertEqual(gpu(snap(instances), "A10")["free_mem_gb"], 22.5)

    def test_evictable_memory_is_kept_apart_from_free_memory(self):
        host = "gpu-a10-01.example.invalid"
        instances = sge.parse_instances(qstat(block(
            "all.q", host, names=["A10"], ngpus=1, cards={0: "22.5G"})))
        g = gpu(snap(instances), "A10")
        self.assertEqual((g["free_mem_gb"], g["evictable_mem_gb"]), (0.0, 22.5))

    def test_mixed_hosts_do_not_set_a_card_size(self):
        host = "gpu-mix-01.example.invalid"
        instances = sge.parse_instances(qstat(block(
            "LAB-GPU", host, names=["A10", "L4"], ngpus=1, cards={0: "22.5G", 1: "22.5G"})))
        self.assertEqual(gpu(snap(instances), "A10")["card_gb"], 0.0)

    def test_card_size_is_the_roomiest_card_seen_even_when_busy(self):
        # SYNTHETIC: one host is fully busy (ngpus=0) but still shows what the model holds.
        instances = sge.parse_instances(qstat(
            block("LAB-GPU", "a.example.invalid", names=["A10"], ngpus=0, cards={0: "3G"}),
            block("LAB-GPU", "b.example.invalid", names=["A10"], ngpus=0, cards={0: "22.5G"})))
        g = gpu(snap(instances), "A10")
        self.assertEqual(g["card_gb"], 22.5)
        self.assertEqual(g["free_mem_gb"], 0.0)


# ------------------------------------------------------------ the shelling-out bits

class FakeRun:
    """Stands in for `sge._run`: records every command and answers from a table."""

    def __init__(self, answers=None, default=(0, "")):
        self.calls: list[list[str]] = []
        self.answers = answers or {}
        self.default = default

    def __call__(self, args, stdin=None):
        self.calls.append(list(args))
        for prefix, answer in self.answers.items():
            if " ".join(args).startswith(prefix):
                return answer
        return self.default


class RunTests(unittest.TestCase):
    def test_success_returns_stdout_and_stderr(self):
        done = subprocess.CompletedProcess(["x"], 0, stdout="out\n", stderr="err\n")
        with mock.patch.object(sge.subprocess, "run", return_value=done):
            self.assertEqual(sge._run(["x"]), (0, "out\nerr\n"))

    def test_missing_client_is_a_failure_not_an_exception(self):
        with mock.patch.object(sge.subprocess, "run", side_effect=FileNotFoundError("qstat")):
            code, text = sge._run(["qstat"])
        self.assertEqual(code, 1)
        self.assertIn("qstat", text)

    def test_timeout_is_a_failure_not_an_exception(self):
        timeout = subprocess.TimeoutExpired(["qstat"], 1)
        with mock.patch.object(sge.subprocess, "run", side_effect=timeout):
            self.assertEqual(sge._run(["qstat"])[0], 1)

    def test_none_streams_are_tolerated(self):
        done = subprocess.CompletedProcess(["x"], 3, stdout=None, stderr=None)
        with mock.patch.object(sge.subprocess, "run", return_value=done):
            self.assertEqual(sge._run(["x"]), (3, ""))


class SnapshotTests(unittest.TestCase):
    GOOD = qstat(block("LAB-GPU", "gpu-a10-01.example.invalid", names=["A10"], ngpus=1,
                       cards={0: "22.5G"}))

    def run_snapshot(self, stdout, *, whichq=(0, "LAB-GPU\n"), available=True, run_error=None):
        done = subprocess.CompletedProcess(["qstat"], 0, stdout=stdout, stderr="")
        runner = mock.patch.object(sge.subprocess, "run", return_value=done, side_effect=run_error)
        with mock.patch.object(sge, "available", return_value=available), runner as ran, \
                mock.patch.object(sge, "_run", FakeRun({"whichq": whichq})):
            return sge.snapshot(), ran

    def test_no_scheduler_on_this_machine(self):
        out, ran = self.run_snapshot("", available=False)
        self.assertIn("error", out)
        self.assertEqual((out["queues"], out["gpus"]), ([], []))
        ran.assert_not_called()

    def test_reads_a_good_listing(self):
        out, ran = self.run_snapshot(self.GOOD)
        self.assertNotIn("error", out)
        self.assertNotIn("note", out)
        self.assertEqual(gpu(out, "A10")["free"], 1)
        # Long queue names would otherwise be cut off as queue@host.
        self.assertEqual(ran.call_args.kwargs["env"]["SGE_LONG_QNAMES"], "-1")
        self.assertEqual(ran.call_args.args[0][:2], ["qstat", "-f"])

    def test_empty_output_is_an_error_the_page_can_show(self):
        for text in ("", "garbage"):
            out, _ = self.run_snapshot(text)
            self.assertIn("nothing readable", out["error"])
            self.assertEqual((out["queues"], out["gpus"]), ([], []))

    def test_a_timeout_is_an_error_the_page_can_show(self):
        out, _ = self.run_snapshot("", run_error=subprocess.TimeoutExpired(["qstat"], 1))
        self.assertIn("Could not read the queues", out["error"])

    def test_without_whichq_nothing_is_called_reachable(self):
        out, _ = self.run_snapshot(self.GOOD, whichq=(1, "whichq: command not found"))
        self.assertIn("note", out)
        self.assertEqual(gpu(out, "A10")["free"], 0)
        self.assertFalse(any(q["mine"] for q in out["queues"]))


class SubmitTests(unittest.TestCase):
    def test_terse_id(self):
        with mock.patch.object(sge, "_run", FakeRun({"qsub": (0, "4242\n")})) as fake:
            self.assertEqual(sge.submit("/x/job.sh"), ("4242", ""))
        self.assertEqual(fake.calls, [["qsub", "-terse", "/x/job.sh"]])

    def test_array_job_id_is_reduced_to_the_job(self):
        with mock.patch.object(sge, "_run", FakeRun({"qsub": (0, "4242.1-3:1\n")})):
            self.assertEqual(sge.submit("j"), ("4242", ""))

    def test_failure_carries_the_scheduler_message(self):
        with mock.patch.object(sge, "_run", FakeRun({"qsub": (1, "Unable to run job: nope\n")})):
            self.assertEqual(sge.submit("j"), (None, "Unable to run job: nope"))

    def test_silent_failure_still_says_something(self):
        with mock.patch.object(sge, "_run", FakeRun({"qsub": (3, "")})):
            self.assertEqual(sge.submit("j"), (None, "qsub exited 3"))
        with mock.patch.object(sge, "_run", FakeRun({"qsub": (0, "")})):
            job, message = sge.submit("j")
            self.assertIsNone(job)
            self.assertTrue(message)


class StateTests(unittest.TestCase):
    def answers(self, listing, *, known=True):
        gone = (1, "Following jobs do not exist: 4242") if not known else (0, "job_number: 4242")
        return FakeRun({"qstat -j": gone, "qstat -u": (0, listing)})

    def state(self, listing, job="4242", **kw):
        with mock.patch.object(sge, "_run", self.answers(listing, **kw)), \
                mock.patch.dict(os.environ, {"USER": "someone"}):
            return sge.state(job)

    HEADER = ("job-ID  prior   name       user         state submit/start at     queue   slots\n"
              + "-" * 70 + "\n")

    def line(self, job, flags):     # SYNTHETIC qstat -u row
        return f"   {job} 0.55500 bc2_x      someone      {flags}     01/01/2026 00:00:00 LAB-GPU@n.example.invalid     8\n"

    def test_running(self):
        for flags in ("r", "t"):
            self.assertEqual(self.state(self.HEADER + self.line(4242, flags)), ("running", ""))

    def test_waiting_and_held(self):
        for flags in ("qw", "hqw"):
            self.assertEqual(self.state(self.HEADER + self.line(4242, flags)), ("queued", ""))

    def test_error_state_is_a_failure_not_waiting(self):
        which, message = self.state(self.HEADER + self.line(4242, "Eqw"))
        self.assertEqual(which, "gone")
        self.assertIn("error state", message)

    def test_unknown_to_the_scheduler_is_gone(self):
        self.assertEqual(self.state("", known=False), ("gone", ""))

    def test_scheduler_unreachable_reads_as_gone(self):
        with mock.patch.object(sge, "_run", FakeRun(default=(1, "commlib error"))):
            self.assertEqual(sge.state("4242"), ("gone", ""))

    def test_listed_by_j_but_absent_from_the_user_listing_is_still_queued(self):
        self.assertEqual(self.state(self.HEADER), ("queued", ""))

    def test_a_job_id_is_matched_whole_not_by_prefix(self):
        listing = self.HEADER + self.line(42421, "r") + self.line(424, "r")
        self.assertEqual(self.state(listing), ("queued", ""))

    def test_short_or_garbled_lines_do_not_raise(self):
        self.assertEqual(self.state("4242\n4242 r\n\n\x00\n"), ("queued", ""))


class FinishedTests(unittest.TestCase):
    def finished(self, out, code=0):
        with mock.patch.object(sge, "_run", FakeRun({"qacct": (code, out)})):
            return sge.finished("4242")

    def test_clean_exit(self):
        self.assertEqual(self.finished("failed       0\nexit_status  0\n"), (0, ""))

    def test_sigterm_is_called_an_eviction_or_a_cancel(self):
        status, why = self.finished("failed       0\nexit_status  143\n")
        self.assertEqual(status, 143)
        self.assertIn("stopped by the scheduler", why)

    def test_scheduler_reported_failure_keeps_its_own_words(self):
        status, why = self.finished("failed       100 : assumed failure\nexit_status  137\n")
        self.assertEqual(status, 137)
        self.assertEqual(why, "100 : assumed failure")

    def test_not_in_accounting_yet_means_ask_again(self):
        self.assertEqual(self.finished("error: job id 4242 not found", code=1), (None, ""))

    def test_malformed_accounting_does_not_raise(self):
        self.assertEqual(self.finished("exit_status\nfailed\n"), (None, ""))
        self.assertEqual(self.finished("exit_status  banana\n"), (None, ""))
        self.assertEqual(self.finished(""), (None, ""))


class CancelTests(unittest.TestCase):
    def test_issues_qdel_and_reports_success(self):
        fake = FakeRun()
        with mock.patch.object(sge, "_run", fake):
            self.assertTrue(sge.cancel("4242"))
        self.assertEqual(fake.calls, [["qdel", "4242"]])

    def test_an_integer_id_is_accepted(self):
        fake = FakeRun()
        with mock.patch.object(sge, "_run", fake):
            sge.cancel(4242)       # type: ignore[arg-type]
        self.assertEqual(fake.calls, [["qdel", "4242"]])

    def test_failure_is_reported_not_raised(self):
        with mock.patch.object(sge, "_run", FakeRun(default=(1, "denied"))):
            self.assertFalse(sge.cancel("4242"))


# ------------------------------------------- JobStore.cancel, where the bug lived
#
# The early return was in JobStore.cancel, not in sge.cancel, so the regression
# test has to go through the store. jobs.py imports review.py, which needs numpy
# and biotite, so this class is skipped where they are absent (the Mac).

def _store_importable() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("numpy", "biotite"))


@unittest.skipUnless(_store_importable(), "bc2serve.jobs needs numpy and biotite")
class JobStoreCancelTests(unittest.TestCase):
    def setUp(self):
        from bc2serve import jobs
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        # No scheduler or postman thread: nothing in this test may start a job or
        # touch a card. Threads are constructed by __init__ and never started.
        with mock.patch.object(threading.Thread, "start", lambda self: None):
            self.store = jobs.JobStore(self.tmp / "repo", self.tmp / "state")
        self.fake = FakeRun()
        patch = mock.patch.object(sge, "_run", self.fake)
        patch.start()
        self.addCleanup(patch.stop)

    def job(self, state, *, pid=None, sched="local", kind="design"):
        job_id = self.store.submit("c", "", self.tmp / "c.json", self.tmp / "c", 1, kind=kind)
        with sqlite3.connect(self.store.database) as db:
            db.execute("UPDATE jobs SET state=?, pid=?, sched=? WHERE id=?",
                       (state, pid, sched, job_id))
        return job_id

    def qdels(self):
        return [c for c in self.fake.calls if c and c[0] == "qdel"]

    def test_regression_a_queued_job_handed_to_the_cluster_is_qdeled(self):
        """A cancelled campaign once started on a GPU a minute later because the
        queued branch dropped the row without telling the scheduler."""
        job_id = self.job("queued", pid=777, sched="sge")
        self.assertTrue(self.store.cancel(job_id))
        self.assertEqual(self.qdels(), [["qdel", "777"]])
        self.assertEqual(self.store.get(job_id)["state"], "cancelled")

    def test_a_running_cluster_job_is_qdeled_too(self):
        job_id = self.job("running", pid=888, sched="sge")
        self.assertTrue(self.store.cancel(job_id, quiet=True))
        self.assertEqual(self.qdels(), [["qdel", "888"]])
        self.assertEqual(self.store.get(job_id)["state"], "cancelled")

    def test_a_queued_job_never_handed_over_has_nothing_to_qdel(self):
        job_id = self.job("queued")
        self.assertTrue(self.store.cancel(job_id))
        self.assertEqual(self.qdels(), [])
        self.assertEqual(self.store.get(job_id)["state"], "cancelled")

    def test_a_local_pid_is_never_given_to_qdel(self):
        # A local pid and an SGE job id are different namespaces; sched says which.
        job_id = self.job("queued", pid=999, sched="local")
        self.store.cancel(job_id)
        self.assertEqual(self.qdels(), [])

    def test_a_finished_job_is_refused_and_nothing_is_sent(self):
        for state in ("done", "failed", "cancelled"):
            job_id = self.job(state, pid=555, sched="sge")
            self.assertFalse(self.store.cancel(job_id), state)
        self.assertEqual(self.qdels(), [])

    def test_an_unknown_job_is_refused(self):
        self.assertFalse(self.store.cancel(123456))
        self.assertEqual(self.qdels(), [])


# --------------------------------------------------------------------- job_script

def make_script(**over) -> str:
    args = dict(name="bc2_7", campaign="/work/c/campaign.json", project="/work/c",
                log="/work/c/campaign.log", sif="/home/u/containers/x.sif",
                params="/home/u/af2params", cache="/work/c/xla_cache",
                queue="LAB-GPU", slots=8, gpu_flag="gpu_a100", gpus=1)
    args.update(over)
    return sge.job_script(**args)


class JobScriptTests(unittest.TestCase):
    def directives(self, script):
        return [line for line in script.splitlines() if line.startswith("#$")]

    def test_carries_the_queue_and_the_slot_count_it_was_given(self):
        script = make_script(queue="PARTNER-GPU", slots=12)
        self.assertIn("#$ -q PARTNER-GPU", self.directives(script))
        self.assertIn("#$ -pe smp 12", self.directives(script))

    def test_no_queue_means_no_directive_rather_than_an_empty_one(self):
        # The default used to be one site's queue name. With none configured the
        # scheduler should pick its own default, which it does only if `-q` is absent.
        d = self.directives(make_script(queue=""))
        self.assertFalse([x for x in d if x.startswith("#$ -q")], d)
        self.assertIn("#$ -pe smp 8", d)

    def test_names_the_job_and_its_log(self):
        script = make_script(name="bc2_99", log="/work/c/campaign.log")
        d = self.directives(script)
        self.assertIn("#$ -N bc2_99", d)
        self.assertIn("#$ -o /work/c/campaign.log", d)

    def test_a_named_model_asks_for_that_flag_and_not_a_card_count(self):
        d = self.directives(make_script(gpu_flag="gpu_a100", gpus=2))
        self.assertIn("#$ -l gpu_a100=true", d)
        # gpu_*=true already implies one card; both would ask for two.
        self.assertFalse([x for x in d if "ngpus" in x])

    def test_no_model_asks_for_the_card_count(self):
        d = self.directives(make_script(gpu_flag=None, gpus=2))
        self.assertIn("#$ -l ngpus=2", d)
        self.assertFalse([x for x in d if "=true" in x])

    def test_the_campaign_is_run_under_apptainer(self):
        script = make_script()
        self.assertTrue(script.startswith("#!/bin/bash\n"))
        self.assertIn("apptainer exec -e", script)
        self.assertIn("bindcraft design /work/c/campaign.json", script)
        self.assertIn("exit $status", script)

    def test_no_unfilled_placeholders_survive(self):
        script = make_script()
        for token in ("{name}", "{log}", "{resources}", "{sif}", "{params}", "{cache}",
                      "{project}", "{campaign}"):
            self.assertNotIn(token, script)

    def test_bash_accepts_the_script(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.sh"
            path.write_text(make_script())
            done = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


class JobScriptExecutionTests(unittest.TestCase):
    """Run the generated script against fake `apptainer`, `nvidia-smi` and no
    scheduler, and look at the arguments the container would have received.

    This is the only way to test quoting honestly: what matters is what bash
    does with the paths, not what the text looks like.
    """

    def setUp(self):
        if not shutil.which("bash"):
            self.skipTest("no bash")
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(os.path.realpath(holder.name))
        self.bin = self.tmp / "fakebin"
        self.bin.mkdir()
        self.argv_file = self.tmp / "argv.txt"
        self.write_tool("apptainer",
                        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> "{self.argv_file}"; done\n')
        self.write_tool("nvidia-smi", "#!/bin/sh\nexit 0\n")

    def write_tool(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def execute(self, **over) -> list[str]:
        script = self.tmp / "job.sh"
        script.write_text(make_script(**over))
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp)}
        subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                       cwd=self.tmp, timeout=30)
        return self.argv_file.read_text().splitlines() if self.argv_file.exists() else []

    def test_plain_paths_reach_the_container_intact(self):
        params, cache = self.tmp / "af2params", self.tmp / "cache"
        project, campaign, sif = self.tmp / "proj", self.tmp / "proj" / "campaign.json", self.tmp / "x.sif"
        argv = self.execute(params=str(params), cache=str(cache), project=str(project),
                            campaign=str(campaign), sif=str(sif))
        self.assertEqual(argv, ["exec", "-e",
                                "--bind", f"{params}:/af2params",
                                "--bind", f"{cache}:/xla_cache",
                                "--pwd", str(project),
                                str(sif), "bindcraft", "design", str(campaign)])
        self.assertTrue(cache.is_dir())          # the XLA cache is made before the run

    def test_each_folder_the_campaign_touches_is_bound_at_its_own_path(self):
        # A home reached through a symlink is mounted by its real path, so the
        # spelling the project folder was given by does not exist in the container
        # unless it is bound under that spelling.
        script = make_script(binds=("/Users/u/r/c 1", "/Users/u/BC/settings/target/structures", "/Users/u/r/c 1"))
        line = next(l for l in script.splitlines() if "--pwd" in l)
        self.assertIn("--bind '/Users/u/r/c 1:/Users/u/r/c 1'", line)
        self.assertIn("--bind /Users/u/BC/settings/target/structures:/Users/u/BC/settings/target/structures", line)
        self.assertEqual(line.count("/Users/u/r/c 1:"), 1)          # once each

    def test_no_binds_leaves_the_line_as_it_was(self):
        line = next(l for l in make_script().splitlines() if "--pwd" in l)
        self.assertEqual(line.count("--bind"), 2)                    # the parameters and the cache

    def test_paths_with_spaces_are_quoted(self):
        base = self.tmp / "my projects"
        params, cache = base / "af2 params", base / "xla cache"
        project, campaign, sif = base / "proj one", base / "proj one" / "campaign.json", base / "the image.sif"
        argv = self.execute(params=str(params), cache=str(cache), project=str(project),
                            campaign=str(campaign), sif=str(sif))
        self.assertEqual(argv, ["exec", "-e",
                                "--bind", f"{params}:/af2params",
                                "--bind", f"{cache}:/xla_cache",
                                "--pwd", str(project),
                                str(sif), "bindcraft", "design", str(campaign)])
        self.assertTrue(cache.is_dir())
        # One directory, not one per word.
        self.assertFalse((self.tmp / "my").exists())

    def test_shell_metacharacters_in_a_path_are_not_executed(self):
        marker = self.tmp / "PWNED"
        nasty = self.tmp / f"x$(touch {marker})y"
        self.execute(project=str(nasty), campaign=str(nasty / "campaign.json"),
                     cache=str(nasty / "xla_cache"))
        self.assertFalse(marker.exists(), "a path in the job script was run as a command")


if __name__ == "__main__":
    unittest.main()
