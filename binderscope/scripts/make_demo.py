"""Turn a built review bundle into a public demo: the same pages, scrubbed, marked as a demo.

    python scripts/make_demo.py BUNDLE_DIR OUT_DIR [--repo-url URL] [--words FILE] [--title TEXT]

A bundle is already a static site, so the demo is a copy of one -- nothing is rebuilt
and every page works exactly as it does for a reviewer. What changes:

- Absolute paths go. A bundle records where its inputs were read from, and a home
  directory names an account. Every /home/<name>/ or /Users/<name>/ becomes ~/.
- Every page says it is a demo with a chip in the header, beside the data badge,
  linking to the repository when one is given. The Overview also gets a short
  corner note saying what the demo is. Nothing takes layout space: the Board and
  the Inspector are sized to the window, so a banner would push them into
  scrolling, and a corner note there would sit over the family rail and the tracks.
- Link previews: a description and title for the pages people share.
- It refuses to finish if anything that looks private is left: a home path, an
  e-mail address, a host name, or a word from --words (one per line; the
  repository's .git/private-words is the natural file).

Stdlib only, so it runs anywhere the bundle can be copied to.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

PAGES = ("index.html", "board.html", "inspector.html", "map.html", "query.html", "guide.html")
TEXT = (".html", ".js", ".json", ".txt", ".css", ".svg", ".pdb", ".cif")
HOME = re.compile(r"/(?:home|Users)/[^/\s\"'<>\\]+/")
LEAKS = {
    "home path": re.compile(r"/(?:home|Users)/(?!<)[A-Za-z0-9._-]{2,}"),
    "e-mail": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.(?:edu|com|org|net|io|ac\.[a-z]{2})\b"),
    "host name": re.compile(r"\b[\w-]+\.(?:dynamic|internal|lan|local)\.[\w.-]+\b|\bdhcp[\w-]+\b"),
}
# a few public addresses the vendored and licence text legitimately carry
ALLOWED = re.compile(r"3dmol\.csb\.pitt\.edu|consurfdb\.tau\.ac\.il|github\.com|uniprot\.org|rcsb\.org|ebi\.ac\.uk")

NOTE_CSS = """<style id="bs-demo-css">
.bs-demo{position:fixed;left:12px;bottom:12px;z-index:9999;display:flex;align-items:center;gap:8px;max-width:min(560px,calc(100vw - 24px));
  padding:7px 10px 7px 12px;border-radius:10px;background:var(--surface,#fff);color:var(--ink-2,#444b5e);border:1px solid var(--line-strong,#c7ccda);
  box-shadow:0 6px 24px rgba(0,0,0,.18);font:12px/1.35 var(--font-ui,system-ui,sans-serif)}
.bs-demo b{color:var(--ink,#141824);font-weight:600}
.bs-demo a{color:var(--accent,#4a3aa7);font-weight:600;text-decoration:none;white-space:nowrap}
.bs-demo a:hover{text-decoration:underline}
.bs-demo button{flex:none;border:0;background:none;color:var(--ink-3,#586280);font-size:15px;line-height:1;cursor:pointer;padding:0 2px}
.bs-demo[hidden]{display:none}
.bs-demo-chip{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600;white-space:nowrap;
  text-decoration:none;color:var(--accent-ink,#fff);background:var(--accent,#4a3aa7);border:1px solid var(--accent,#4a3aa7);margin-left:6px}
.bs-demo-chip:hover{filter:brightness(1.12)}
@media print{.bs-demo,.bs-demo-chip{display:none}}
</style>"""


def note_html(repo_url: str | None) -> str:
    link = (f' <a href="{html.escape(repo_url)}" target="_blank" rel="noopener">Get Binderscope &#8599;</a>'
            if repo_url else ' <a href="guide.html">How to read it</a>')
    return ('<div class="bs-demo" id="bsDemo" role="note"><span><b>Demo.</b> One small BindCraft 2 run, reviewed by '
            'Binderscope. Click anything: decisions stay in your browser. These designs were never made or tested.'
            + link + '</span><button type="button" aria-label="Hide this note" title="Hide">&times;</button></div>'
            '<script>(function(){var n=document.getElementById("bsDemo");try{if(sessionStorage.getItem("bs-demo-hidden"))n.hidden=true}catch(e){}'
            'n.querySelector("button").onclick=function(){n.hidden=true;try{sessionStorage.setItem("bs-demo-hidden","1")}catch(e){}}})();</script>')


def scrub_text(text: str) -> str:
    return HOME.sub("~/", text)


def chip_html(repo_url: str | None) -> str:
    if repo_url:
        return (f'<a class="bs-demo-chip" href="{html.escape(repo_url)}" target="_blank" rel="noopener" '
                'title="This is a demo with sample data. Binderscope is free to install and reviews your own runs.">'
                'Demo &middot; Get Binderscope &#8599;</a>')
    return '<span class="bs-demo-chip" title="This is a demo with sample data.">Demo</span>'


def mark_page(text: str, title: str, description: str, repo_url: str | None, corner: bool = False) -> str:
    meta = (f'<meta name="description" content="{html.escape(description)}">\n'
            f'<meta property="og:title" content="{html.escape(title)}">\n'
            f'<meta property="og:description" content="{html.escape(description)}">\n'
            '<meta property="og:type" content="website">\n'
            '<meta name="robots" content="index,follow">\n')
    if 'id="bs-demo-css"' in text:
        return text
    text = re.sub(r"(<title>)(.*?)(</title>)", lambda m: m.group(1) + "Demo · " + m.group(2) + m.group(3), text, count=1, flags=re.S)
    text = text.replace("</head>", meta + NOTE_CSS + "\n</head>", 1)
    # the chip goes right after the data badge, which every page's header carries
    text, n = re.subn(r'(<span class="tag-run" id="tagMock"[^>]*>.*?</span>)', lambda m: m.group(1) + chip_html(repo_url), text, count=1, flags=re.S)
    if corner or not n:   # a page with no badge (the guide) gets the corner note instead
        text = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + note_html(repo_url), text, count=1)
    return text


def leaks(root: Path, words: list[str]) -> list[str]:
    found = []
    wrx = re.compile("|".join(re.escape(w) for w in words), re.I) if words else None
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in TEXT:
            continue
        t = f.read_text(encoding="utf-8", errors="ignore")
        for kind, rx in LEAKS.items():
            for m in rx.finditer(t):
                if not ALLOWED.search(t[max(0, m.start() - 40):m.end() + 40]):
                    found.append(f"{f.relative_to(root)}: {kind}")
                    break
        if wrx and wrx.search(t):
            found.append(f"{f.relative_to(root)}: a word from the private list")
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--repo-url", default=None, help="where the corner note's 'Get Binderscope' link points")
    ap.add_argument("--words", type=Path, default=None, help="file of private words that must not appear")
    ap.add_argument("--title", default="Binderscope demo: reviewing BindCraft binder designs")
    ap.add_argument("--description", default="A live Binderscope review of one BindCraft 2 run: backbone families, "
                    "3D design inspection, binding-mode maps and a rule-based structure query. Runs in the browser, "
                    "nothing to install.")
    a = ap.parse_args(argv)
    if not (a.bundle / "index.html").is_file() or not (a.bundle / "data").is_dir():
        ap.error(f"{a.bundle} is not a built bundle (no index.html and data/)")
    if a.out.exists() and any(a.out.iterdir()):
        ap.error(f"{a.out} exists and is not empty; choose an empty folder")
    shutil.copytree(a.bundle, a.out, dirs_exist_ok=True)
    # Decisions saved from a review belong to that review, not to the demo.
    for f in (a.out / "reviews").glob("*.jsonl"):
        f.unlink()
    for f in a.out.rglob("*"):
        if f.is_file() and f.suffix.lower() in TEXT:
            t = f.read_text(encoding="utf-8", errors="ignore")
            s = scrub_text(t)
            if f.parent == a.out and f.name in PAGES:
                s = mark_page(s, a.title, a.description, a.repo_url, corner=f.name == "index.html")
            if s != t:
                f.write_text(s, encoding="utf-8")
    man = a.out / "data" / "manifest.json"
    if man.is_file():
        m = json.loads(man.read_text(encoding="utf-8"))
        m.setdefault("bundle", {})["name"] = "demo"
        m.setdefault("notes", []).append("Public demo copy: input paths shortened to ~/, pages marked as a demo.")
        man.write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (a.out / ".nojekyll").write_text("")          # GitHub Pages serves the folder as it is
    words = [w.strip() for w in a.words.read_text().splitlines() if w.strip() and not w.startswith("#")] if a.words else []
    bad = leaks(a.out, words)
    if bad:
        print("Refusing: the demo still carries what looks private:\n  " + "\n  ".join(bad[:40]), file=sys.stderr)
        return 1
    n = sum(1 for f in a.out.rglob("*") if f.is_file())
    print(f"demo written to {a.out}: {n} files, scanned clean" + (f" (and against {len(words)} private words)" if words else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
