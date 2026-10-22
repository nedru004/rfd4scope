"""The bundle writer on the toy project: layout, data wrappers for file://, hashes and the validator, structures, options, offline
behaviour and the safeguards against writing over a folder that is not a bundle. (The page behaviour is test_browser_bundle.py.)"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from binder_review import bundle as B
from binder_review import export as E
from binder_review import viewer_build
from toydata.testing import load_validator

PAGES = ("index", "board", "inspector", "map", "query")
BLOBS = ("campaign", "inspect", "homolog", "profile", "manifest")
GLOBALS = {"campaign": "RAW", "inspect": "INS", "homolog": "HOM", "profile": "PROFILE", "manifest": "MANIFEST"}
NODE = shutil.which("node")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unwrap(js_text: str, blob: str):
    """The object a data/<blob>.js assigns (the wrapper of docs/CONTRACTS.md section 8)."""
    head = f"window.BINDERSCOPE = window.BINDERSCOPE || {{}};\nwindow.BINDERSCOPE.{blob} = window.{GLOBALS[blob]} = "
    assert js_text.startswith(head), js_text[:120]
    assert js_text.rstrip().endswith(";")
    return json.loads(js_text[len(head):].rstrip()[:-1])


def _without_structures(ins: dict) -> dict:
    """inspect.json as data/inspect.js holds it: the binder structures (pdb_b) are loaded from structures/<id>.js on demand."""
    out = dict(ins)
    out["designs"] = {t: {k: v for k, v in d.items() if k != "pdb_b"} for t, d in ins["designs"].items()}
    return out


# ---------------------------------------------------------------------------------------------------- units
def test_structure_script_is_valid_javascript_assigning_the_text():
    text = 'HELIX  1\nATOM      1  N   MET B   1      47.585  31.242  95.360  1.00 52.91           N\n"quoted" \\ back\n'
    js = B.structure_script("run01/x_mpnn1", text)
    assert js.startswith("window.BINDERSCOPE = window.BINDERSCOPE || {};") and js.count("\n") == 2     # the text's newlines are escaped inside the literal
    if NODE:
        r = subprocess.run([NODE, "-e", f"global.window = {{}}; {js}; console.log(JSON.stringify(window.BINDERSCOPE.structures))"],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout) == {"run01/x_mpnn1": text}


def test_design_id_of_inverts_the_file_name():
    assert B.design_id_of("binder_l77_s305024_mpnn4.pdb") == "binder_l77_s305024_mpnn4"
    assert B.design_id_of("run01__binder_x_mpnn1.js") == "run01/binder_x_mpnn1"
    assert E.structure_filename(B.design_id_of("a__b.pdb")) == "a__b.pdb"


def test_write_bundle_needs_an_export_folder(tmp_path):
    with pytest.raises(B.BundleError, match="not an export folder"):
        B.write_bundle(tmp_path / "nothing", tmp_path / "out")
    (tmp_path / "e").mkdir()
    with pytest.raises(B.BundleError, match="campaign.json"):
        B.load_export(tmp_path / "e")


# ---------------------------------------------------------------------------------------------------- the toy bundle
def test_bundle_layout(toy_bundle, toy_blobs):
    b = toy_bundle
    for p in PAGES:
        assert (b / f"{p}.html").is_file(), p
    for n in BLOBS:
        assert (b / "data" / f"{n}.json").is_file() and (b / "data" / f"{n}.js").is_file(), n
    assert (b / "assets" / "3Dmol-min.js").stat().st_size > 400_000 and "BSD" in (b / "assets" / "3Dmol-LICENSE.txt").read_text()
    for f in ("css/shared.css", "js/bs.js", "js/common.js", "js/family.js", "js/mini3d.js"):
        assert (b / "assets" / f).is_file(), f
    assert (b / "reviews").is_dir() and (b / "README.txt").read_text().startswith("Binderscope bundle")
    n = len(toy_blobs.inspect["designs"])
    assert len(list((b / "structures").glob("*.pdb"))) == n and len(list((b / "structures").glob("*.js"))) == n
    ids = {d["id"] for d in json.loads((b / "data" / "campaign.json").read_text())["designs"] if d["inspect"]}
    assert {p.stem for p in (b / "structures").glob("*.pdb")} == ids and not (b / "guide.html").exists()


def test_data_scripts_hold_the_same_objects_as_the_json_files(toy_bundle):
    for n in BLOBS:
        js = _unwrap((toy_bundle / "data" / f"{n}.js").read_text(encoding="utf-8"), n)
        canon = json.loads((toy_bundle / "data" / f"{n}.json").read_text(encoding="utf-8"))
        assert js == (_without_structures(canon) if n == "inspect" else canon), n
    camp = json.loads((toy_bundle / "data" / "campaign.json").read_text())
    assert camp["profile"] == json.loads((toy_bundle / "data" / "profile.json").read_text()) == json.loads((toy_bundle / "data" / "inspect.json").read_text())["profile"]


def test_the_inspector_script_leaves_the_structures_to_the_structure_scripts(toy_bundle):
    js = _unwrap((toy_bundle / "data" / "inspect.js").read_text(encoding="utf-8"), "inspect")
    canon = json.loads((toy_bundle / "data" / "inspect.json").read_text(encoding="utf-8"))
    assert all("pdb_b" not in d for d in js["designs"].values())
    assert all(isinstance(d["pdb_b"], str) and d["pdb_b"].startswith(("HELIX", "SHEET", "ATOM")) for d in canon["designs"].values())
    assert (toy_bundle / "data" / "inspect.js").stat().st_size < 0.4 * (toy_bundle / "data" / "inspect.json").stat().st_size
    for d in js["designs"].values():                                              # every design has the script the page will ask for
        assert (toy_bundle / "structures" / (d["id"].replace("/", "__") + ".js")).is_file(), d["id"]


def test_every_page_loads_the_scripts_it_needs_and_all_links_resolve(toy_bundle):
    for page, needs in viewer_build.NEEDS.items():
        html = (toy_bundle / f"{page}.html").read_text(encoding="utf-8")
        for blob in needs:
            assert f'src="data/{blob}.js"' in html, (page, blob)
    assert viewer_build.check_links(toy_bundle, list(PAGES)) == []


def test_manifest_hashes_every_file_but_itself_and_the_validator_agrees(toy_bundle, tmp_path):
    man = json.loads((toy_bundle / "data" / "manifest.json").read_text())
    files = {f["path"]: f for f in man["bundle"]["files"]}
    on_disk = {p.relative_to(toy_bundle).as_posix() for p in toy_bundle.rglob("*") if p.is_file()}
    assert on_disk - set(files) == {"data/manifest.json", "data/manifest.js"} and set(files) <= on_disk
    first = sorted(p.name for p in (toy_bundle / "structures").glob("*.pdb"))[0]
    for path in ("index.html", "data/campaign.json", f"structures/{first}", "assets/3Dmol-min.js"):
        assert files[path]["sha256"] == _sha(toy_bundle / path) and files[path]["bytes"] == (toy_bundle / path).stat().st_size
    assert man["bundle"]["pages"]["index"] == "index.html" and man["bundle"]["structures"]["count"] == len(list((toy_bundle / "structures").glob("*.pdb")))
    vs = load_validator()
    rep = vs.check_bundle(toy_bundle)
    assert rep.errors == [] and rep.warnings == []
    copy = tmp_path / "copy"
    shutil.copytree(toy_bundle, copy)                                             # a tampered file is caught (on a copy)
    p = copy / "assets" / "css" / "shared.css"
    p.write_text(p.read_text() + "/* tampered */")
    assert any("shared.css" in e for e in vs.check_bundle(copy).errors)


def test_structure_scripts_carry_the_pdb_text_and_the_pdb_files_are_the_exported_ones(toy_bundle, toy_blobs):
    for fn in sorted(toy_blobs.structures)[:4]:
        text = (toy_bundle / "structures" / fn).read_text()
        assert text == toy_blobs.structures[fn]
        stem = fn[:-4]
        assert (toy_bundle / "structures" / f"{stem}.js").read_text() == B.structure_script(B.design_id_of(fn), text)
        assert (toy_bundle / "structures" / fn).stat().st_size > 20_000


def test_bundle_works_offline_nothing_leaves_the_folder(toy_bundle):
    ext = re.compile(r"""(?:src|href)=["'](https?://[^"']+)["']""")
    for p in toy_bundle.glob("*.html"):
        text = p.read_text(encoding="utf-8")
        for u in ext.findall(text):                                               # outbound links are anchors, never resources
            assert re.search(r'<a [^>]*href="' + re.escape(u), text), (p.name, u)
        assert not re.search(r"""<(script|link|img|iframe)[^>]*(src|href)=["']https?://""", text), p.name
        assert "cdnjs" not in text and "unpkg" not in text and "jsdelivr" not in text and "fonts.googleapis.com" not in text
    for p in (toy_bundle / "assets" / "js").glob("*.js"):
        t = p.read_text(encoding="utf-8")
        assert "XMLHttpRequest" not in t and not re.search(r"\bfetch\s*\(", t), p.name
    css = (toy_bundle / "assets" / "css" / "shared.css").read_text()
    assert re.search(r"--font-ui:[^;]*system-ui[^;]*sans-serif", css) and re.search(r"--font-mono:[^;]*monospace", css)    # system font stacks


def test_overview_page_script_renders_the_highlights_from_the_data(toy_bundle):
    html = (toy_bundle / "index.html").read_text(encoding="utf-8")
    assert 'id="stand"' in html and "RAW.highlights" in html and 'id="hlList"' in html and not (toy_bundle / "viewer").exists()


def test_pages_have_no_unresolved_template_placeholders(toy_bundle):
    """Names come from the profile (here the toy target), and no page keeps a build-time placeholder. (Template tokens such as {@target.short}
    stay in the page script as default texts; the browser test checks that none is left in the rendered text.)"""
    text = "".join((toy_bundle / f"{p}.html").read_text(encoding="utf-8") for p in PAGES)
    assert "%%" not in text and "__PLACEHOLDER" not in text
    profile = json.loads((toy_bundle / "data" / "profile.json").read_text())
    assert profile["target"]["short"] in text and profile["brand"]["name"] in text


# ---------------------------------------------------------------------------------------------------- options on a second bundle
def test_options_system_fonts_csp_and_no_guide(toy_export_dir, tmp_path):
    out = tmp_path / "b"
    B.write_bundle(toy_export_dir, out, fonts="system", csp=True, guide=False, structure_scripts=False)
    assert not (out / "guide.html").exists() and not list((out / "structures").glob("*.js"))
    for p in PAGES:
        html = (out / f"{p}.html").read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in html.split("<style>")[0]
        assert 'http-equiv="Content-Security-Policy"' in html and "connect-src 'none'" in html
    man = json.loads((out / "data" / "manifest.json").read_text())
    assert man["bundle"]["structures"]["scripts"] is False and "guide" not in man["bundle"]["pages"] and man["bundle"]["name"] == "b"


def test_structures_are_loaded_on_demand_unless_embedding_is_asked_for(toy_export_dir, tmp_path):
    lazy, emb, nos = tmp_path / "lazy", tmp_path / "emb", tmp_path / "nos"
    B.write_bundle(toy_export_dir, lazy, guide=False)
    B.write_bundle(toy_export_dir, emb, embed_structures=True, guide=False)
    B.write_bundle(toy_export_dir, nos, structure_scripts=False, guide=False)
    canon = json.loads((lazy / "data" / "inspect.json").read_text(encoding="utf-8"))
    has = lambda b: [("pdb_b" in d) for d in _unwrap((b / "data" / "inspect.js").read_text(encoding="utf-8"), "inspect")["designs"].values()]   # noqa: E731
    assert not any(has(lazy)) and all(has(emb)) and all(has(nos))                 # without the scripts the structures stay embedded
    assert json.loads((emb / "data" / "inspect.json").read_text(encoding="utf-8")) == canon
    assert len(list((lazy / "structures").glob("*.js"))) == len(canon["designs"])
    assert (lazy / "data" / "inspect.js").stat().st_size < (emb / "data" / "inspect.js").stat().st_size


def test_rewrites_cleanly_and_keeps_extra_files_only_when_asked(toy_export_dir, tmp_path):
    out = tmp_path / "b"
    B.write_bundle(toy_export_dir, out, guide=False)
    (out / "stale.txt").write_text("old")
    first = {p.relative_to(out).as_posix(): _sha(p) for p in out.rglob("*") if p.is_file() and p.name != "stale.txt"}
    B.write_bundle(toy_export_dir, out, guide=False)                           # clean: the stale file goes
    assert not (out / "stale.txt").exists()
    assert {p.relative_to(out).as_posix(): _sha(p) for p in out.rglob("*") if p.is_file()} == first          # two builds are byte-identical
    (out / "keep.txt").write_text("mine")
    B.write_bundle(toy_export_dir, out, clean=False, guide=False)
    assert (out / "keep.txt").exists()


def test_bundle_summary(toy_bundle):
    info = B.bundle_summary(toy_bundle)
    assert info["name"] == "toy" and info["counts"]["accepted"] == 22 and info["n_files"] > 60 and len(info["highlights"]) >= 4
    assert info["pages"]["index"] == "index.html" and info["validation"]["counts_match_csv"] is True


def test_write_bundle_never_wipes_a_folder_that_is_not_a_bundle(tmp_path):
    victim = tmp_path / "Documents"
    victim.mkdir()
    (victim / "thesis.txt").write_text("keep me")
    with pytest.raises(B.BundleError, match="not a Binderscope bundle"):
        B.check_clean_target(victim)
    export = tmp_path / "export"
    export.mkdir()
    for n in BLOBS:
        (export / f"{n}.json").write_text("{}")
    with pytest.raises(B.BundleError, match="refusing to clean"):                 # before any file is read or removed
        B.write_bundle(export, victim)
    assert (victim / "thesis.txt").read_text() == "keep me"
    B.check_clean_target(tmp_path / "does-not-exist")                             # missing and empty folders are fine
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / ".DS_Store").write_text("")
    B.check_clean_target(empty)
    prev = tmp_path / "prev"
    (prev / "data").mkdir(parents=True)
    (prev / "index.html").write_text("x")
    (prev / "data" / "manifest.json").write_text("{}")
    B.check_clean_target(prev)                                                    # an earlier bundle may be rebuilt over
    (tmp_path / "afile").write_text("x")
    with pytest.raises(B.BundleError, match="not a folder"):
        B.check_clean_target(tmp_path / "afile")
