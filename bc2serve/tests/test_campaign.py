"""Tests for bc2serve.campaign: the web form to a BindCraft 2 campaign file.

Everything is built in temporary directories. The "repository" is an empty
temporary folder with whatever `settings/target/*.json` a test writes into it,
and the results root is another temporary folder, so nothing here can write
outside what the test made.

Path validation comes first because it is the part with consequences: a campaign
name arrives from a form (and later from a URL), a results root from a form, and
neither may be talked into writing outside the chosen root.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from bc2serve import campaign
from bc2serve.campaign import BadCampaign


class Sandbox(unittest.TestCase):
    """A fake repository, a results root one level inside a scratch directory, and
    a structure file. Writes outside `self.results` are detectable."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        # realpath: macOS temp dirs sit behind a /var -> /private/var symlink, and
        # build() resolves every path it is given.
        self.base = Path(os.path.realpath(holder.name))
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.outer = self.base / "outer"
        self.results = self.outer / "results"
        self.results.mkdir(parents=True)
        self.structure = self.base / "target.pdb"
        self.structure.write_text("ATOM\n")

    def form(self, **over) -> dict:
        form = {"name": "demo",
                "targets": [{"target_path": str(self.structure), "name": "t1"}],
                "length_min": 60, "length_max": 100}
        form.update(over)
        return form

    def build(self, **over):
        roots = over.pop("allowed_roots", None)
        return campaign.build(self.repo, self.results, self.form(**over), roots)

    def files_outside_results(self) -> set:
        """Every file the sandbox holds except the ones the test itself made."""
        made = {self.structure}
        return {p for p in self.base.rglob("*") if p.is_file()
                and p not in made and self.results not in p.parents}


# ------------------------------------------------------------- campaign names

class NameTests(Sandbox):
    def test_empty_names_are_refused(self):
        for name in ("", "   ", "\t\n", None):
            with self.assertRaises(BadCampaign, msg=repr(name)):
                self.build(name=name)

    def test_a_missing_name_key_is_refused(self):
        form = self.form()
        del form["name"]
        with self.assertRaises(BadCampaign):
            campaign.build(self.repo, self.results, form)

    def test_surrounding_whitespace_is_dropped_and_inner_runs_collapse(self):
        made, _, project = self.build(name="  my  campaign \t two ")
        self.assertEqual(made["campaign_name"], "my_campaign_two")
        self.assertEqual(project.name, "my_campaign_two")

    def test_separators_become_underscores_so_the_name_is_one_path_segment(self):
        # Shaped, not followed: whatever was typed ends up as a single segment
        # inside the chosen root, and the leading punctuation goes so that the
        # result is a name the review pages will also serve. Where nothing is
        # left of it, that is a refusal rather than a campaign called "_",
        # which would run and then 404 on its own review URL.
        for name in ("a/b", "a\\b", "/etc/passwd", "../x", "a/../../b", "x/", "a:b|c*d?"):
            made, file, project = self.build(name=name)
            self.assertNotIn("/", made["campaign_name"], name)
            self.assertNotIn("\\", made["campaign_name"], name)
            self.assertRegex(made["campaign_name"], r"\A[A-Za-z0-9]", name)
            self.assertEqual(project.parent, self.results, name)
            self.assertEqual(project.name, made["campaign_name"], name)
            self.assertEqual(file.parent, project, name)
        for name in ("//", "///"):
            with self.assertRaises(BadCampaign, msg=name):
                self.build(name=name)

    def test_an_absolute_path_as_a_name_is_not_followed(self):
        outside = self.base / "elsewhere"
        outside.mkdir()
        made, _, project = self.build(name=str(outside))
        self.assertEqual(project.parent, self.results)
        self.assertEqual(list(outside.iterdir()), [])

    def test_nul_and_control_characters_do_not_survive(self):
        made, _, project = self.build(name="a\x00b\x07c\x1bd")
        self.assertEqual(made["campaign_name"], "a_b_c_d")
        self.assertEqual(project.parent, self.results)

    def test_shell_and_glob_characters_do_not_survive(self):
        made, _, _ = self.build(name="x;rm -rf $HOME `id` *.csv")
        self.assertRegex(made["campaign_name"], r"^[\w.-]+$")

    def test_unicode_letters_are_kept(self):
        made, _, _ = self.build(name="nanobody-étude")
        self.assertEqual(made["campaign_name"], "nanobody-étude")

    def test_ordinary_names_are_untouched(self):
        made, _, _ = self.build(name="Spike_VHH-round2.v1")
        self.assertEqual(made["campaign_name"], "Spike_VHH-round2.v1")

    def test_a_name_of_only_punctuation_is_refused_rather_than_invented(self):
        # It used to become a campaign called "_". That is inside the root and
        # so not an escape, but review.CAMPAIGN_NAME refuses a leading
        # underscore, so the campaign could never be reviewed. Refusing while
        # the person is still looking at the form is the better end.
        with self.assertRaises(BadCampaign):
            self.build(name="!!!")

    def test_resubmitting_a_name_reuses_the_folder_and_replaces_the_file(self):
        _, first, project = self.build(name="again", number_of_final_designs=3)
        _, second, again = self.build(name="again", number_of_final_designs=9)
        self.assertEqual((first, project), (second, again))
        self.assertEqual(json.loads(second.read_text())["number_of_final_designs"], 9)


class NameEscapeTests(Sandbox):
    """What a name made only of dots does. These are the dangerous ones: "." and
    ".." survive the character filter, because the dot is allowed in a name, and
    then name a directory that is the root itself or the root's parent.
    """

    def assertStaysInside(self, name: str) -> None:
        try:
            made, file, project = self.build(name=name)
        except BadCampaign:
            return                      # refusing it is a fine answer
        self.assertEqual(project.parent, self.results,
                         f"{name!r} put the campaign folder at {project}")
        self.assertEqual(project.name, made["campaign_name"])
        self.assertEqual(file.parent, project)

    def test_double_dot_cannot_climb_out_of_the_results_root(self):
        self.assertStaysInside("..")
        self.assertEqual(self.files_outside_results(), set(),
                         "campaign.json was written outside the results root")

    def test_single_dot_cannot_write_into_the_root_itself(self):
        self.assertStaysInside(".")
        self.assertFalse((self.results / "campaign.json").exists(),
                         "campaign.json was written directly into the results root")

    def test_padded_dots(self):
        for name in (" .. ", "\t.\t", "..\n"):
            self.assertStaysInside(name)
        self.assertEqual(self.files_outside_results(), set())

    def test_dots_with_text_are_ordinary_names(self):
        for name in ("...", "..x", "x..", "a..b", ".hidden"):
            self.assertStaysInside(name)
        self.assertEqual(self.files_outside_results(), set())

    def test_the_reviews_folder_cannot_be_claimed_by_a_campaign(self):
        # "_reviews" is where reviews live, beside the campaigns (review.REVIEWS).
        # A campaign of that name would write campaign.json into the reviews
        # folder. The leading underscore is trimmed, so the campaign lands in
        # "reviews" instead and the folder it would have collided with is
        # untouched -- the same answer every other unusable character gets.
        made, _, project = self.build(name="_reviews")
        self.assertEqual(made["campaign_name"], "reviews")
        self.assertEqual(project, self.results / "reviews")
        self.assertFalse((self.results / "_reviews").exists())


class NameLengthTests(Sandbox):
    def test_a_very_long_name_is_refused_cleanly_or_built_inside_the_root(self):
        name = "n" * 300
        try:
            made, _, project = self.build(name=name)
        except BadCampaign:
            return
        except OSError as problem:
            self.fail(f"a long name leaked a raw OSError instead of a BadCampaign: {problem}")
        self.assertEqual(project.parent, self.results)

    def test_a_name_at_a_typical_limit_works(self):
        made, _, project = self.build(name="n" * 120)
        self.assertEqual(project.name, "n" * 120)


FULL_STACK = all(importlib.util.find_spec(m) is not None
                 for m in ("numpy", "biotite", "fastapi"))


@unittest.skipUnless(FULL_STACK, "review.py needs numpy and biotite")
class NamesTheReviewerAcceptsTests(Sandbox):
    """A campaign is only useful if it can be reviewed, and the review layer
    refuses any name that does not match its own pattern (review.CAMPAIGN_NAME):
    it must start with a letter or digit and be at most 128 characters. A name
    the form accepts but the reviewer refuses starts a campaign that then never
    appears in the review list and whose review URL is a 404.
    """

    def test_every_name_the_form_turns_into_a_campaign_is_a_reviewable_one(self):
        from bc2serve import review
        names = ["plain", "with space", "/leading/slash", ".hidden", "_underscored",
                 "-dash", "!!!", "n" * 129, "n" * 200, "a.b-c_d", "..x"]
        problems = []
        for name in names:
            try:
                made, _, _ = self.build(name=name)
            except BadCampaign:
                continue
            except OSError:
                continue            # covered by NameLengthTests
            if not review.CAMPAIGN_NAME.fullmatch(made["campaign_name"]):
                problems.append(f"{name!r} -> {made['campaign_name'][:40]!r}")
        self.assertEqual(problems, [], "accepted by the form, refused by the reviewer")


# ------------------------------------------------------------------ results root

class ResultsRootTests(Sandbox):
    def test_default_is_the_first_allowed_root_or_the_given_results_root(self):
        _, _, project = self.build()
        self.assertEqual(project, self.results / "demo")
        other = self.base / "other"
        other.mkdir()
        _, _, project = self.build(allowed_roots=[other])
        self.assertEqual(project, other / "demo")

    def test_a_typed_path_outside_the_offered_roots_is_allowed(self):
        # "The offered roots are a convenience, not a fence." The test records that
        # decision so that tightening it is a deliberate change.
        elsewhere = self.base / "my projects"
        elsewhere.mkdir()
        _, _, project = self.build(results_root=str(elsewhere))
        self.assertEqual(project, elsewhere / "demo")

    def test_a_relative_path_is_refused(self):
        for raw in ("results", "./results", "../results", "a/b"):
            with self.assertRaises(BadCampaign, msg=raw):
                self.build(results_root=raw)

    def test_dotdot_inside_an_absolute_root_is_resolved_before_it_is_judged(self):
        sneaky = f"{self.repo}/results/../settings"
        (self.repo / "settings").mkdir()
        with self.assertRaises(BadCampaign):
            self.build(results_root=sneaky)

    def test_folders_of_the_installation_are_protected(self):
        for part in ("bc2serve", ".venv", ".bindcraft-tools", "settings", "scaffolds",
                     "bindcraft", ".git", "docs", "examples", "containers"):
            (self.repo / part).mkdir(exist_ok=True)
            with self.assertRaises(BadCampaign, msg=part):
                self.build(results_root=str(self.repo / part))
            with self.assertRaises(BadCampaign, msg=part + "/deep"):
                self.build(results_root=str(self.repo / part / "deep" / "er"))
        self.assertEqual(self.files_outside_results(), set())
        self.assertEqual(list(self.repo.rglob("campaign.json")), [])

    def test_a_symlink_into_a_protected_folder_is_followed_before_judging(self):
        (self.repo / "settings").mkdir()
        link = self.results / "innocent"
        link.symlink_to(self.repo / "settings")
        with self.assertRaises(BadCampaign):
            self.build(results_root=str(link))
        self.assertEqual(list((self.repo / "settings").iterdir()), [])

    def test_a_symlink_as_the_campaign_name_cannot_redirect_the_write(self):
        # A pre-existing symlink named like the campaign would carry the write
        # elsewhere. The name filter cannot prevent that; this documents the
        # behaviour for the protected folders at least.
        (self.repo / "settings").mkdir()
        (self.results / "demo").symlink_to(self.repo / "settings")
        with self.assertRaises(BadCampaign):
            self.build(name="demo")
        self.assertEqual(list((self.repo / "settings").iterdir()), [])

    def test_the_default_results_folder_inside_the_repo_is_fine(self):
        # The default lives in the repository, so "inside the repo" alone is not a reason.
        default = self.repo / "results"
        default.mkdir()
        _, _, project = self.build(results_root=str(default))
        self.assertEqual(project, default / "demo")

    def test_a_root_that_does_not_exist_yet_is_made(self):
        fresh = self.results / "new" / "deeper"
        _, file, project = self.build(results_root=str(fresh))
        self.assertTrue(file.is_file())
        self.assertEqual(project, fresh / "demo")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write anywhere")
    def test_an_unwritable_root_is_refused_with_a_message(self):
        locked = self.base / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        with self.assertRaises(BadCampaign) as caught:
            self.build(results_root=str(locked / "sub"))
        self.assertIn("cannot be written", str(caught.exception))

    def test_a_root_that_is_a_file_is_refused_cleanly(self):
        blocker = self.base / "afile"
        blocker.write_text("x")
        try:
            self.build(results_root=str(blocker))
        except BadCampaign:
            return
        except OSError as problem:
            self.fail(f"leaked a raw OSError instead of a BadCampaign: {problem}")
        self.fail("a file was accepted as a results root")

    def test_the_campaign_file_lands_in_the_project_folder_and_matches_the_dict(self):
        made, file, project = self.build()
        self.assertEqual(file, project / "campaign.json")
        self.assertEqual(json.loads(file.read_text()), made)
        self.assertEqual(made["project_folder"], str(project))


# ------------------------------------------------------------ the form to a campaign

class TargetTests(Sandbox):
    def test_no_targets(self):
        for targets in (None, [], ()):
            with self.assertRaises(BadCampaign):
                self.build(targets=targets)

    def test_a_target_without_a_structure(self):
        for entry in ({}, {"target_path": ""}, {"target_path": "   "}):
            with self.assertRaises(BadCampaign):
                self.build(targets=[entry])

    def test_a_structure_that_is_not_there(self):
        with self.assertRaises(BadCampaign) as caught:
            self.build(targets=[{"target_path": str(self.base / "gone.pdb")}])
        self.assertIn("No structure at", str(caught.exception))

    def test_a_directory_is_not_a_structure(self):
        with self.assertRaises(BadCampaign):
            self.build(targets=[{"target_path": str(self.base)}])

    def test_every_target_avoided_is_refused_but_one_bound_is_enough(self):
        avoid = {"target_path": str(self.structure), "objective": "detarget"}
        bind = {"target_path": str(self.structure)}
        with self.assertRaises(BadCampaign):
            self.build(targets=[avoid, dict(avoid)])
        made, _, _ = self.build(targets=[bind, avoid])
        self.assertEqual([t.get("objective") for t in made["targets"]], [None, "detarget"])

    def test_target_name_defaults_and_is_cleaned(self):
        made, _, _ = self.build(targets=[
            {"target_path": str(self.structure)},
            {"target_path": str(self.structure), "name": "  my/target one "}])
        self.assertEqual([t["name"] for t in made["targets"]], ["target", "my_target_one"])

    def test_chains_lose_their_whitespace_and_empty_chains_are_omitted(self):
        made, _, _ = self.build(targets=[
            {"target_path": str(self.structure), "chains": " A, B "},
            {"target_path": str(self.structure), "chains": "  "}])
        self.assertEqual(made["targets"][0]["chains"], "A,B")
        self.assertNotIn("chains", made["targets"][1])

    def test_hotspots_and_coldspots(self):
        made, _, _ = self.build(targets=[{
            "target_path": str(self.structure),
            "hotspots": " 54, 56 ,66-70 ", "coldspots": "A12,B3-9"}])
        self.assertEqual(made["targets"][0]["hotspots"], "54,56,66-70")
        self.assertEqual(made["targets"][0]["coldspots"], "A12,B3-9")

    def test_blank_hotspots_are_left_out(self):
        made, _, _ = self.build(targets=[{"target_path": str(self.structure), "hotspots": "  "}])
        self.assertNotIn("hotspots", made["targets"][0])

    def test_malformed_residue_lists_are_refused_with_the_field_named(self):
        for bad, field in (("abc", "Hotspots"), ("5-", "Hotspots"), ("1,,2", "Hotspots"),
                           ("1;2", "Hotspots"), ("-3", "Hotspots"), ("1-2-3", "Hotspots")):
            with self.assertRaises(BadCampaign, msg=bad) as caught:
                self.build(targets=[{"target_path": str(self.structure), "hotspots": bad}])
            self.assertIn(field, str(caught.exception))
        with self.assertRaises(BadCampaign) as caught:
            self.build(targets=[{"target_path": str(self.structure), "coldspots": "x"}])
        self.assertIn("Coldspots", str(caught.exception))

    def test_clean_residues_directly(self):
        self.assertEqual(campaign._clean_residues(None, "f"), "")
        self.assertEqual(campaign._clean_residues("  ", "f"), "")
        self.assertEqual(campaign._clean_residues("A54 , B12-16", "f"), "A54,B12-16")


class FormatTests(Sandbox):
    def test_default_is_a_plain_binder_with_the_given_lengths(self):
        made, _, _ = self.build()
        self.assertEqual(made["modality"], "binder")
        self.assertEqual(made["binder_lengths"], [60, 100])

    def test_unknown_format(self):
        with self.assertRaises(BadCampaign):
            self.build(format="superbinder")

    def test_every_listed_format_builds(self):
        for name, _, _, lengths in campaign.FORMATS:
            made, _, _ = self.build(format=name, length_min=40, length_max=90)
            self.assertEqual(made["modality"], name)
            self.assertEqual("binder_lengths" in made, lengths is not None, name)

    def test_scaffolded_formats_ignore_a_length_even_if_one_is_sent(self):
        made, _, _ = self.build(format="VHH", length_min="banana", length_max=None)
        self.assertNotIn("binder_lengths", made)

    def test_lengths_must_be_whole_numbers_in_order_and_sensible(self):
        for low, high in (("a", 5), (None, 5), (60, None), (3, 50), (100, 60), (-5, 10), (60.5, "x")):
            with self.assertRaises(BadCampaign, msg=(low, high)):
                self.build(length_min=low, length_max=high)

    def test_lengths_arrive_as_strings_from_a_form(self):
        made, _, _ = self.build(length_min="60", length_max="100")
        self.assertEqual(made["binder_lengths"], [60, 100])

    def test_equal_lengths_are_allowed(self):
        made, _, _ = self.build(length_min=80, length_max=80)
        self.assertEqual(made["binder_lengths"], [80, 80])

    def test_copies_and_domains_have_a_floor_of_two(self):
        made, _, _ = self.build(format="homo_oligomer", copies=1)
        self.assertEqual(made["copies"], 2)
        made, _, _ = self.build(format="homo_oligomer", copies=4)
        self.assertEqual(made["copies"], 4)
        made, _, _ = self.build(format="homo_oligomer")
        self.assertEqual(made["copies"], 2)
        made, _, _ = self.build(format="multidomain", domains="3")
        self.assertEqual(made["n_domains"], 3)
        made, _, _ = self.build(format="multidomain", domains=0)
        self.assertEqual(made["n_domains"], 2)

    def test_copies_and_domains_belong_only_to_their_own_format(self):
        made, _, _ = self.build(format="binder", copies=5, domains=5)
        self.assertNotIn("copies", made)
        self.assertNotIn("n_domains", made)

    def test_conformational_objective_is_appended_to_the_modality(self):
        made, _, _ = self.build(conformational="induced_fit")
        self.assertEqual(made["modality"], "binder,induced_fit")
        made, _, _ = self.build(conformational="")
        self.assertEqual(made["modality"], "binder")

    def test_unknown_conformational_objective(self):
        with self.assertRaises(BadCampaign):
            self.build(conformational="shape_shift")


class PropertyAndScaleTests(Sandbox):
    def test_properties_become_true_flags(self):
        made, _, _ = self.build(properties=["humanize", "bigbang"])
        self.assertIs(made["humanize"], True)
        self.assertIs(made["bigbang"], True)
        self.assertNotIn("protease_stable", made)

    def test_unknown_property(self):
        with self.assertRaises(BadCampaign):
            self.build(properties=["humanize", "make_it_better"])

    def test_defaults_for_scale(self):
        made, _, _ = self.build()
        self.assertEqual((made["number_of_final_designs"], made["max_trajectories"]), (10, 200))

    def test_scale_must_be_whole_numbers_of_at_least_one(self):
        for key in ("number_of_final_designs", "max_trajectories"):
            for bad in ("x", "1.5", -1, {"a": 1}):
                with self.assertRaises(BadCampaign, msg=(key, bad)):
                    self.build(**{key: bad})
        with self.assertRaises(BadCampaign):
            self.build(number_of_final_designs=-4)

    def test_the_budget_is_raised_to_clear_the_worker_count(self):
        # Seven workers per card, and a worker cannot share a trajectory.
        self.assertEqual(self.build(max_trajectories=5)[0]["max_trajectories"], 8)
        self.assertEqual(self.build(max_trajectories=7)[0]["max_trajectories"], 8)
        self.assertEqual(self.build(max_trajectories=8)[0]["max_trajectories"], 8)
        self.assertEqual(self.build(max_trajectories=5, gpus=2)[0]["max_trajectories"], 15)
        self.assertEqual(self.build(max_trajectories=500, gpus=2)[0]["max_trajectories"], 500)

    def test_a_nonsense_gpu_count_floors_at_one(self):
        self.assertEqual(self.build(max_trajectories=1, gpus=0)[0]["max_trajectories"], 8)
        self.assertEqual(self.build(max_trajectories=1, gpus=-3)[0]["max_trajectories"], 8)

    def test_filters(self):
        made, _, _ = self.build(filter_i_pTM="0.75", filter_i_pAE=0.3)
        self.assertEqual(made["filters"], {"i_pTM": {"threshold": 0.75, "higher": True},
                                           "i_pAE": {"threshold": 0.3, "higher": False}})

    def test_blank_filters_are_omitted_entirely(self):
        made, _, _ = self.build(filter_i_pTM="", filter_i_pAE=None)
        self.assertNotIn("filters", made)

    def test_a_non_numeric_filter_is_refused(self):
        with self.assertRaises(BadCampaign):
            self.build(filter_i_pTM="high")

    def test_reproducible_selects_the_benchmark_core(self):
        self.assertEqual(self.build(reproducible=True)[0]["core"], "benchmark")
        self.assertNotIn("core", self.build(reproducible=False)[0])


class NonNumericFormFieldTests(Sandbox):
    """BadCampaign is the module's contract for "the form sent something that
    cannot be made into a campaign", and the route turns it into a 400. A field
    that raises a bare ValueError instead becomes a 500 with no explanation.
    """

    def check(self, **over):
        try:
            self.build(**over)
        except BadCampaign:
            return
        except ValueError as problem:
            self.fail(f"{over} leaked a raw ValueError: {problem}")

    def test_gpus(self):
        self.check(gpus="two")

    def test_copies(self):
        self.check(format="homo_oligomer", copies="many")

    def test_domains(self):
        self.check(format="multidomain", domains="several")


# ------------------------------------------------------------------------ presets

class PresetTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.repo = Path(os.path.realpath(holder.name))
        self.dir = self.repo / "settings" / "target"

    def write(self, name: str, payload) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / name
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def test_presets_are_read_from_the_repository_not_copied(self):
        self.write("one.json", {"description": "first", "targets": [{"name": "a", "target_path": "/abs/a.pdb"}]})
        before = [t["name"] for t in campaign.presets(self.repo)["targets"]]
        self.write("two.json", {"description": "second", "targets": []})
        after = [t["name"] for t in campaign.presets(self.repo)["targets"]]
        self.assertEqual(before, ["one"])
        self.assertEqual(after, ["one", "two"])

    def test_a_changed_preset_is_seen_on_the_next_read(self):
        self.write("one.json", {"description": "old", "targets": []})
        campaign.presets(self.repo)
        self.write("one.json", {"description": "new", "targets": []})
        self.assertEqual(campaign.presets(self.repo)["targets"][0]["description"], "new")

    def test_a_repository_with_no_presets_gives_an_empty_list(self):
        self.assertEqual(campaign.presets(self.repo)["targets"], [])         # no settings/ at all
        self.dir.mkdir(parents=True)
        self.assertEqual(campaign.presets(self.repo)["targets"], [])         # an empty folder
        self.assertEqual(campaign.shipped_targets(self.repo / "no" / "such" / "place"), [])

    def test_the_form_still_has_its_other_choices_when_presets_are_missing(self):
        data = campaign.presets(self.repo)
        self.assertEqual([f["name"] for f in data["formats"]], [f[0] for f in campaign.FORMATS])
        self.assertTrue(data["properties"])
        self.assertTrue(data["conformational"])

    def test_unreadable_json_is_skipped_not_fatal(self):
        self.write("broken.json", "{ not json")
        self.write("empty.json", "")
        self.write("good.json", {"description": "ok", "targets": []})
        self.assertEqual([t["name"] for t in campaign.presets(self.repo)["targets"]], ["good"])

    def test_non_json_files_are_ignored(self):
        self.write("notes.txt", "hello")
        self.write("good.json", {"targets": []})
        self.assertEqual([t["name"] for t in campaign.shipped_targets(self.repo)], ["good"])

    def test_relative_structure_paths_resolve_against_the_preset_file(self):
        (self.dir / "structures").mkdir(parents=True)
        self.write("rel.json", {"targets": [{"name": "t", "target_path": "structures/t.pdb"},
                                            {"name": "up", "target_path": "../shared/u.pdb"}]})
        (target, up) = campaign.shipped_targets(self.repo)[0]["targets"]
        self.assertEqual(target["target_path"], str(self.dir / "structures" / "t.pdb"))
        self.assertEqual(up["target_path"], str(self.repo / "settings" / "shared" / "u.pdb"))

    def test_absolute_paths_and_other_keys_are_left_alone(self):
        self.write("abs.json", {"targets": [{"name": "t", "target_path": "/abs/t.pdb",
                                             "chains": "A", "hotspots": "1,2"}]})
        (target,) = campaign.shipped_targets(self.repo)[0]["targets"]
        self.assertEqual(target, {"name": "t", "target_path": "/abs/t.pdb",
                                  "chains": "A", "hotspots": "1,2"})

    def test_a_target_with_no_path_is_kept_as_it_is(self):
        self.write("nopath.json", {"targets": [{"name": "t"}]})
        self.assertEqual(campaign.shipped_targets(self.repo)[0]["targets"], [{"name": "t"}])

    def test_missing_description_and_targets_get_defaults(self):
        self.write("bare.json", {})
        (entry,) = campaign.shipped_targets(self.repo)
        self.assertEqual(entry, {"name": "bare", "description": "", "targets": []})

    def test_presets_are_listed_in_name_order(self):
        for name in ("b", "c", "a"):
            self.write(f"{name}.json", {"targets": []})
        self.assertEqual([t["name"] for t in campaign.shipped_targets(self.repo)], ["a", "b", "c"])

    def test_the_source_data_is_not_mutated(self):
        self.write("rel.json", {"targets": [{"name": "t", "target_path": "x.pdb"}]})
        campaign.shipped_targets(self.repo)
        again = json.loads((self.dir / "rel.json").read_text())
        self.assertEqual(again["targets"][0]["target_path"], "x.pdb")

    def test_a_preset_whose_structure_is_gone_is_listed_but_cannot_be_built(self):
        # shipped_targets reports what the preset says without checking the disk;
        # it is build() that insists the file exists.
        self.write("gone.json", {"targets": [{"name": "t", "target_path": "missing.pdb"}]})
        (entry,) = campaign.shipped_targets(self.repo)
        path = entry["targets"][0]["target_path"]
        self.assertFalse(Path(path).exists())
        results = self.repo / "results"
        results.mkdir()
        with self.assertRaises(BadCampaign) as caught:
            campaign.build(self.repo, results, {
                "name": "x", "targets": [{"target_path": path}],
                "length_min": 60, "length_max": 100})
        self.assertIn("No structure at", str(caught.exception))
        self.assertEqual(list(results.iterdir()), [])        # and nothing was written

    def test_a_preset_structure_that_exists_builds(self):
        self.dir.mkdir(parents=True)
        (self.dir / "t.pdb").write_text("ATOM\n")
        self.write("ok.json", {"targets": [{"name": "t", "target_path": "t.pdb"}]})
        path = campaign.shipped_targets(self.repo)[0]["targets"][0]["target_path"]
        results = self.repo / "results"
        results.mkdir()
        made, _, _ = campaign.build(self.repo, results, {
            "name": "x", "targets": [{"target_path": path, "name": "t"}],
            "length_min": 60, "length_max": 100})
        self.assertEqual(made["targets"][0]["target_path"], path)

    def test_format_table_is_passed_through(self):
        data = campaign.presets(self.repo)
        by_name = {f["name"]: f for f in data["formats"]}
        self.assertEqual(by_name["binder"]["lengths"], [60, 180])
        self.assertIsNone(by_name["VHH"]["lengths"])
        for f in data["formats"]:
            self.assertEqual(set(f), {"name", "label", "blurb", "lengths"})


if __name__ == "__main__":
    unittest.main()
