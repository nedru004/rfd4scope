#!/usr/bin/env python3
"""Convert viewer/guide/content.html (plus figs.json captions) into Markdown: docs/GUIDE.draft.md, or the file given with --out (never docs/GUIDE.md by default: that file is maintained by hand).
Handles the small HTML subset used in the guide: headings, paragraphs, lists, tables, code blocks, callouts, cards and figure placeholders.
Needs the guide sources of the working copy; a tree that has none (the public export has none) stops with a message."""
import json, re, sys, html
from html.parser import HTMLParser
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
T = ROOT / 'viewer' / 'guide'
if not (T / 'content.html').exists():
    sys.exit('viewer/guide/content.html not found: there is no guide to convert in this tree')
figs = json.loads((T / 'figs.json').read_text()) if (T / 'figs.json').exists() else {}
src = (T / 'content.html').read_text()
cmd = sys.argv[sys.argv.index('--cmd') + 1] if '--cmd' in sys.argv else 'binder-review import ... && binder-review build-bundle ...'
src = src.replace('{{CMD_BUILD}}', cmd)
def fig(m):
    n = m.group(1); f = figs.get(n, {})
    cap = re.sub(r'<[^>]+>', '', f.get('caption', n)).strip()
    items = ' '.join(f"({c['n']}) {re.sub('<[^>]+>', '', c['text'])}." for c in f.get('callouts', []))
    stem = Path(f.get('img', n + '.png')).stem
    return f'\n![{cap}](../viewer/assets/guide/{stem}.jpg)\n\n*{cap} {items}*\n'
src = re.sub(r'\{\{FIG:([\w-]+)\}\}', fig, src)

class MD(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.out = []; self.stack = []; self.list = []; self.pre = False; self.row = []; self.rows = []; self.cell = None; self.skip = 0; self.cls = []
    def w(self, s): self.out.append(s)
    def handle_starttag(self, tag, attrs):
        a = dict(attrs); c = a.get('class', ''); self.cls.append(c)
        if tag in ('style', 'script'): self.skip += 1
        if tag in ('h1', 'h2', 'h3', 'h4'): self.w('\n\n' + '#' * int(tag[1]) + ' ')
        elif tag == 'p': self.w('\n\n')
        elif tag == 'ul': self.list.append(['ul', 0])
        elif tag == 'ol': self.list.append(['ol', 0])
        elif tag == 'li':
            d = len(self.list) - 1; l = self.list[-1] if self.list else ['ul', 0]; l[1] += 1
            self.w('\n' + '  ' * d + ('- ' if l[0] == 'ul' else f'{l[1]}. '))
        elif tag == 'pre': self.pre = True; self.w('\n\n```bash\n')
        elif tag in ('b', 'strong'): self.w('**')
        elif tag in ('i', 'em'): self.w('*')
        elif tag in ('code', 'kbd'): self.w('`')
        elif tag == 'br': self.w('  \n')
        elif tag == 'table': self.rows = []
        elif tag == 'tr': self.row = []
        elif tag in ('td', 'th'): self.cell = []
        elif tag == 'div' and 'callout' in c: self.w('\n\n> ')
        elif tag == 'div' and c == 'card': self.w('\n\n- ')
        elif tag == 'span' and 'tag' in c: self.w('**[')
        elif tag == 'a' and self.cls and False: pass
        elif tag == 'a': self.w('[')
        if tag == 'a': self.stack.append(a.get('href', ''))
    def handle_endtag(self, tag):
        c = self.cls.pop() if self.cls else ''
        if tag in ('style', 'script'): self.skip -= 1
        if tag in ('h1', 'h2', 'h3', 'h4', 'p'): self.w('\n')
        elif tag in ('ul', 'ol'): self.list.pop(); self.w('\n')
        elif tag == 'pre': self.pre = False; self.w('\n```\n')
        elif tag in ('b', 'strong'): self.w('**')
        elif tag in ('i', 'em'): self.w('*')
        elif tag in ('code', 'kbd'): self.w('`')
        elif tag in ('td', 'th'): self.row.append(' '.join(''.join(self.cell).split()).replace('|', '/')); self.cell = None
        elif tag == 'tr': self.rows.append(self.row)
        elif tag == 'table':
            if self.rows:
                w = max(len(r) for r in self.rows); rows = [r + [''] * (w - len(r)) for r in self.rows]
                self.w('\n\n| ' + ' | '.join(rows[0]) + ' |\n|' + '---|' * w + '\n' + '\n'.join('| ' + ' | '.join(r) + ' |' for r in rows[1:]) + '\n')
        elif tag == 'span' and 'tag' in c: self.w(']** ')
        elif tag == 'span' and c == 'n': self.w('. ')
        elif tag == 'a': self.w(f']({self.stack.pop()})')
    def handle_data(self, d):
        if self.skip: return
        if self.cell is not None: self.cell.append(d); return
        if self.pre: self.w(d); return
        t = re.sub(r'\s+', ' ', d)
        if t.strip() or t == ' ': self.w(t)
p = MD(); p.feed(src)
md = ''.join(p.out)
md = re.sub(r'[ \t]+\n', '\n', md); md = re.sub(r'\n{3,}', '\n\n', md).strip() + '\n'
md = re.sub(r'(\*\*[^*\n]+\*\*)([A-Z])', r'\1 \2', md)
md = md.replace('Binderscope · guide', '').strip()
out = Path(sys.argv[sys.argv.index('--out') + 1]) if '--out' in sys.argv else ROOT / 'docs' / 'GUIDE.draft.md'; out.write_text('# Binderscope guide\n\n' + md + '\n'); print('wrote', out, len(md) // 1024, 'KB')
