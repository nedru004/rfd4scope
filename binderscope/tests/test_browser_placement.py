"""The Inspector in headless Chrome: it shows the design's own target and tells the target from the binder by the chain id.

A BindCraft design carries its own copy of the target, which is not the crystal chain: the build places it on the reference chain. Two things went
wrong on real data, and both are checked here on the toy bundles:

* the Inspector drew the reference chain as the target and the binder next to it, so the contacts and hydrogen bonds of the page were measured against
  a chain the binder had never seen. Now, once the structure of a design has loaded (structures/<id>.js: chain A the design's own target, chain B the
  binder), the Inspector shows chain A of that file. The check compares the coordinates of the displayed target model with the file, and with the
  reference chain, which differs from it (a little in the toy bundle, by several A in the hinge bundle);
* a reference that calls the target chain B made the pages take the target for the binder (hover and click decide by the chain id). inspect.json now
  always carries the target as chain A, so this holds for the hinge bundle, whose reference calls the target chain B.

The page is driven through browser_smoke.mjs (--checks: JavaScript expressions that are evaluated in the page and return plain data); one Chrome at a
time. Needs Node 22 or later and Google Chrome (or Chromium); skipped otherwise.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from toydata import appkit

HERE = Path(__file__).resolve().parent
NODE, CHROME = appkit.NODE, appkit.CHROME

pytestmark = pytest.mark.skipif(not (NODE and CHROME), reason="needs Node 22+ and Google Chrome or Chromium (set CHROME_PATH)")

#: What the page is asked, for the design that is on screen and for another design whose structure has not been loaded yet.
OBSERVE = r"""
(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const idle = async () => { for (let i = 0; i < 150 && (window.__pending || !document.getElementById('vload').hidden); i++) await wait(100); await wait(300); };
  const cas = (text, ch) => text.split('\n').filter((l) => l.startsWith('ATOM') && l.slice(12, 16) === ' CA ' && l[21] === ch).map((l) => [+l.slice(22, 26), +l.slice(30, 38), +l.slice(38, 46), +l.slice(46, 54)]);
  const dist = (p, q) => Math.hypot(p[1] - q[1], p[2] - q[2], p[3] - q[3]);
  const same = (p, q) => p.length > 0 && p.length === q.length && p.every((a, i) => a[0] === q[i][0] && dist(a, q[i]) < 0.0015);
  const INS = BS.blob('inspect'), ref = cas(INS.target_pdb, 'A');
  const refChains = [...new Set(INS.target_pdb.split('\n').filter((l) => l.startsWith('ATOM')).map((l) => l[21]))];
  await idle();
  const V = __ins.V;
  const observe = async (X) => {
    await __ins.loadStructure(X);
    await idle();
    const file = window.BINDERSCOPE.structures[X.id] || window.BINDERSCOPE.structures[X.tag];
    const own = cas(file, 'A'), binder = cas(file, 'B');
    const read = () => {
      const by = {};
      for (const a of V.selectedAtoms({})) { const m = by[a.model] || (by[a.model] = { id: a.model, chains: new Set(), ca: [] }); m.chains.add(a.chain); if (a.atom === 'CA') m.ca.push([a.resi, a.x, a.y, a.z]); }
      return Object.values(by).map((m) => ({ id: m.id, chains: [...m.chains].sort(), n_ca: m.ca.length, own: same(m.ca, own), binder: same(m.ca, binder), reference: same(m.ca, ref), ca: m.ca }));
    };
    let models = read();
    for (let i = 0; i < 80 && !models.some((m) => m.own); i++) { await wait(100); models = read(); }
    const tm = models.find((m) => m.own), bm = models.find((m) => m.binder);
    const out = { id: X.id, has_pdb_b: !!X.pdb_b, has_pdb_t: !!X.pdb_t, n_file_a: own.length, n_file_b: binder.length,
      pdb_t_chains: X.pdb_t ? [...new Set(X.pdb_t.split('\n').filter((l) => l.startsWith('ATOM')).map((l) => l[21]))] : null,
      pdb_t_is_file_a: X.pdb_t ? same(cas(X.pdb_t, 'A'), own) : false,
      models: models.map(({ ca, ...m }) => m), target_model: tm ? tm.id : null, binder_model: bm ? bm.id : null,
      file_vs_reference_max: Math.max(...own.filter((c) => ref.some((r) => r[0] === c[0])).map((c) => dist(c, ref.find((r) => r[0] === c[0])))) };
    if (!tm || !bm) return out;
    const atoms = V.selectedAtoms({});
    const atomOf = (m) => { const c = m.ca[Math.floor(m.ca.length / 2)]; return atoms.find((a) => a.model === m.id && a.atom === 'CA' && a.resi === c[0]); };
    const t = atomOf(tm), b = atomOf(bm);
    const tip = (atom) => { atom.hover_callback(atom, V, { clientX: 300, clientY: 300 }); const el = document.querySelector('.tip'); return el ? el.innerText : ''; };
    const zoom = (atom) => {
      const calls = [], orig = V.zoomTo;
      V.zoomTo = function (sel) { calls.push(sel); return orig.apply(this, arguments); };
      try { atom.callback(atom, V, {}); } finally { V.zoomTo = orig; }
      const s = calls[0] && (calls[0].within ? calls[0].within.sel : calls[0]);
      return s && s.model ? s.model.id : null;
    };
    out.target_atom = { chain: t.chain, resi: t.resi, resn: t.resn, model: t.model }; out.binder_atom = { chain: b.chain, resi: b.resi, resn: b.resn, model: b.model };
    out.tip_target = tip(t); out.tip_binder = tip(b);
    out.zoom_target = zoom(t); out.zoom_binder = zoom(b);
    out.tip_control = tip(Object.assign({}, t, { chain: 'B' }));                 // the page tells the binder by its chain id: a target called B would be taken for it
    return out;
  };
  const first = await observe(__ins.cur());
  const order = __ins.listOrder(), tag2 = order[order.length - 1], X2 = INS.designs[tag2];
  const before = { id: X2.id, pdb_b: !!X2.pdb_b, pdb_t: !!X2.pdb_t, in_page: !!(window.BINDERSCOPE.structures && (window.BINDERSCOPE.structures[X2.id] || window.BINDERSCOPE.structures[X2.tag])) };
  __ins.select(tag2);
  const second = await observe(X2);
  return { ref_chains: refChains, n_ref: ref.length, n_designs: order.length, first, before, second };
})()
"""


def run_inspector(bundle: Path, tmp: Path) -> dict:
    checks = tmp / "checks.json"
    checks.write_text(json.dumps({"inspector": {"observe": OBSERVE}}))
    r = subprocess.run([NODE, str(HERE / "browser_smoke.mjs"), str(bundle), "--chrome", CHROME, "--pages", "inspector", "--checks", str(checks)], capture_output=True, text=True,
                       timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    page = json.loads(r.stdout)["pages"]["inspector"]
    assert page["settled"] is True and page["errors"] == [] and page["external"] == [], (page["errors"], page["external"])
    seen = page["checks"]["observe"]
    assert "error" not in seen, seen
    return seen


@pytest.fixture(scope="module")
def hinge_page(toy_hinge, tmp_path_factory):
    """The bundle built from the reference that calls the target chain B and has the binder's domain turned by 12 degrees."""
    return run_inspector(toy_hinge.bundle, tmp_path_factory.mktemp("hinge_page"))


@pytest.fixture(scope="module")
def toy_page(toy_bundle, tmp_path_factory):
    """The plain toy bundle, whose designs carry the reference's own target (to 0.03 A)."""
    return run_inspector(toy_bundle, tmp_path_factory.mktemp("toy_page"))


def shows_own_target(obs: dict) -> None:
    """The displayed target is chain A of the design's structure file, the binder is its chain B, and nothing else is either."""
    assert obs["has_pdb_b"] is True and obs["has_pdb_t"] is True and obs["pdb_t_chains"] == ["A"] and obs["pdb_t_is_file_a"] is True        # taken from the file
    assert obs["n_file_a"] >= 100 and obs["n_file_b"] >= 60
    targets = [m for m in obs["models"] if m["own"]]
    binders = [m for m in obs["models"] if m["binder"]]
    assert [m["chains"] for m in targets] == [["A"]] and [m["n_ca"] for m in targets] == [obs["n_file_a"]]                                  # one model, chain A
    assert [m["chains"] for m in binders] == [["B"]] and [m["n_ca"] for m in binders] == [obs["n_file_b"]]
    assert not [m for m in obs["models"] if m["reference"]], obs["models"]                                                                     # not the reference chain
    assert obs["target_model"] == targets[0]["id"] and obs["binder_model"] == binders[0]["id"] and obs["target_model"] != obs["binder_model"]
    others = [m for m in obs["models"] if not m["own"] and not m["binder"]]
    assert all(m["chains"] == ["Z"] for m in others), others                                                                                   # the ghost traces of the context


def target_is_not_taken_for_the_binder(obs: dict) -> None:
    assert obs["target_model"] is not None and obs["binder_model"] is not None, obs["models"]
    assert obs["target_atom"]["chain"] == "A" and obs["binder_atom"]["chain"] == "B"
    assert obs["tip_target"].startswith(f"{obs['target_atom']['resn']} {obs['target_atom']['resi']} · target · pLDDT") and "binder" not in obs["tip_target"]
    assert obs["tip_binder"].startswith(f"{obs['binder_atom']['resn']} {obs['binder_atom']['resi']} · binder · pLDDT") and "buried" in obs["tip_binder"]
    assert obs["zoom_target"] == obs["target_model"] and obs["zoom_binder"] == obs["binder_model"]                                           # a click flies to its own side
    assert "· binder ·" in obs["tip_control"] and "target" not in obs["tip_control"]                                                 # what a target called B would be


# ---------------------------------------------------------------------------------------------------------------- the bundle of the hinge
def test_the_inspector_of_the_hinge_bundle_shows_the_design_s_own_target_not_the_reference_chain(hinge_page):
    assert hinge_page["ref_chains"] == ["A"] and hinge_page["n_ref"] >= 100                    # inspect.json: the target is chain A, whatever the reference calls it
    for obs in (hinge_page["first"], hinge_page["second"]):
        shows_own_target(obs)
        assert obs["file_vs_reference_max"] > 2.0, obs["file_vs_reference_max"]                # the reference chain has the domain turned: the two cannot be mixed up


def test_the_structure_of_a_design_is_taken_when_it_loads_and_the_target_follows_it(hinge_page, toy_hinge):
    assert hinge_page["n_designs"] == len(toy_hinge.blobs.inspect["order"]) and hinge_page["first"]["id"] == toy_hinge.design.id
    before, second = hinge_page["before"], hinge_page["second"]
    assert before["id"] == second["id"] != hinge_page["first"]["id"]
    assert before["pdb_b"] is False and before["pdb_t"] is False and before["in_page"] is False                                                 # not loaded yet
    assert second["has_pdb_b"] is True and second["has_pdb_t"] is True                                                                          # set when the file has loaded
    t1 = next(m for m in hinge_page["first"]["models"] if m["id"] == hinge_page["first"]["target_model"])
    t2 = next(m for m in hinge_page["second"]["models"] if m["id"] == hinge_page["second"]["target_model"])
    assert t1["own"] and t2["own"] and t1["chains"] == t2["chains"] == ["A"]


def test_hover_and_click_on_the_target_of_the_hinge_bundle_are_not_taken_for_the_binder(hinge_page):
    for obs in (hinge_page["first"], hinge_page["second"]):
        target_is_not_taken_for_the_binder(obs)


# ---------------------------------------------------------------------------------------------------------------- the plain toy bundle
def test_the_inspector_of_the_toy_bundle_shows_the_design_s_own_target(toy_page):
    assert toy_page["ref_chains"] == ["A"]
    for obs in (toy_page["first"], toy_page["second"]):
        shows_own_target(obs)
        assert 0.002 < obs["file_vs_reference_max"] < 1.0, obs["file_vs_reference_max"]        # the toy designs carry the reference's target to within a few hundredths of an A
        target_is_not_taken_for_the_binder(obs)
    before = toy_page["before"]
    assert (before["pdb_b"], before["pdb_t"], before["in_page"]) == (False, False, False) and toy_page["second"]["has_pdb_t"] is True
