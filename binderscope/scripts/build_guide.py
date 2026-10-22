#!/usr/bin/env python3
"""Assemble viewer/pages/guide.html from the template, content.html, captured screenshots and element rectangles.

Inputs (written by the capture step): viewer/guide/figs.json (figure definitions), viewer/guide/rects.json (element rectangles in image pixels),
viewer/guide/shots/*.png (screenshots). Outputs: viewer/pages/guide.html and viewer/assets/guide/*.jpg|png.
Usage: python scripts/build_guide.py [--cmd "binder-review ..."]
"""
import json, re, shutil, sys, html
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def opt(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
T = Path(opt('--src', ROOT / 'viewer' / 'guide'))                 # content.html, figs.json, rects.json, shots/
OUT_PAGE = Path(opt('--page-out', ROOT / 'viewer' / 'pages' / 'guide.html'))
OUT_ASSETS = Path(opt('--assets-out', ROOT / 'viewer' / 'assets' / 'guide'))
REL = opt('--assets-rel', 'assets/guide')                          # prefix of the figure paths inside the page
STANDALONE = '--standalone' in sys.argv                               # a page outside a bundle: no app navigation, no web font link
cmd = 'binder-review import ... && binder-review build-bundle ...'
if '--cmd' in sys.argv: cmd = sys.argv[sys.argv.index('--cmd') + 1]
figs = json.loads((T / 'figs.json').read_text()) if (T / 'figs.json').exists() else {}
rects = json.loads((T / 'rects.json').read_text()) if (T / 'rects.json').exists() else {}
OUT_ASSETS.mkdir(parents=True, exist_ok=True)

def fig_html(name):
    f = figs.get(name)
    if not f: return f'<!-- missing figure {name} -->'
    src = T / 'shots' / f['img']
    if not src.exists(): return f'<!-- missing image {f["img"]} -->'
    try:
        from PIL import Image
        im = Image.open(src).convert('RGB'); W, H = im.size
        scale = min(1.0, 2400 / W)
        if scale < 1: im = im.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
        dst = OUT_ASSETS / (Path(f['img']).stem + '.jpg'); im.save(dst, quality=88, optimize=True)
        rel = f'{REL}/{dst.name}'
    except Exception:
        shutil.copy(src, OUT_ASSETS / src.name); rel = f'{REL}/{src.name}'; W, H = f['w'], f['h']
    W, H = f.get('w', W), f.get('h', H)
    pins, boxes, items = [], [], []
    for c in f.get('callouts', []):
        r = rects.get(c['rect']) if isinstance(c.get('rect'), str) else c.get('rect')
        if not r: continue
        x, y, w, h = (r['x'], r['y'], r['w'], r['h']) if isinstance(r, dict) else r
        n = c['n']; px = (x + min(w, 120) * 0.0) / W * 100; py = (y) / H * 100
        px = max(2.2, min(97.8, (x) / W * 100)); py = max(3.0, min(97.0, (y) / H * 100))
        pins.append(f'<span class="pin" data-n="{n}" style="left:{px:.2f}%;top:{py:.2f}%">{n}</span>')
        boxes.append(f'<span class="box" data-n="{n}" style="left:{x / W * 100:.2f}%;top:{y / H * 100:.2f}%;width:{w / W * 100:.2f}%;height:{h / H * 100:.2f}%"></span>')
        items.append(f'<li data-n="{n}"><i>{n}</i><span>{c["text"]}</span></li>')
    cap = f.get('caption', '')
    return (f'<figure class="shot" id="fig-{name}"><div class="frame"><img src="{rel}" alt="{html.escape(re.sub("<[^>]+>", "", cap))}" loading="lazy">{"".join(boxes)}{"".join(pins)}</div>'
            f'<figcaption>{cap}<ol>{"".join(items)}</ol></figcaption></figure>')

if not (T / 'content.html').exists():
    sys.exit('viewer/guide/content.html not found: there is no guide to build in this tree')
content = (T / 'content.html').read_text()
content = re.sub(r'\{\{FIG:([\w-]+)\}\}', lambda m: fig_html(m.group(1)), content)
content = content.replace('{{CMD_BUILD}}', html.escape(cmd))
tpl_path = T / 'guide.template.html'
if not tpl_path.exists(): tpl_path = ROOT / 'viewer' / 'guide' / 'guide.template.html'
tpl = tpl_path.read_text()
if STANDALONE:
    tpl = re.sub(r'<link rel="stylesheet" href="https://fonts\.googleapis\.com[^>]*>\n?', '', tpl)
    tpl = re.sub(r'<nav[^>]*>.*?</nav>', '', tpl, count=1, flags=re.S)
    tpl = tpl.replace('href="index.html" id="brand"', 'href="#" id="brand"')
OUT_PAGE.parent.mkdir(parents=True, exist_ok=True)
OUT_PAGE.write_text(tpl.replace('<!--CONTENT-->', content))
print('wrote', OUT_PAGE, len(OUT_PAGE.read_text()) // 1024, 'KB;', len(list(OUT_ASSETS.glob('*'))), 'asset files')
