#!/usr/bin/env python3
"""Ajoute un correctif lisibilité au index.html EXISTANT sans remplacer son JS/backend.
Usage: python3 apply_agentos_readability.py path/to/index.html
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('Usage: python3 apply_agentos_readability.py chemin/vers/index.html')
path = Path(sys.argv[1]).expanduser().resolve()
if not path.is_file():
    raise SystemExit(f'Fichier introuvable : {path}')
css = Path(__file__).with_name('agentos-readability.css').read_text(encoding='utf-8')
html = path.read_text(encoding='utf-8')
if '</head>' not in html.lower():
    raise SystemExit('Balise </head> introuvable ; fichier non modifié.')
start, end = '<!-- agentos readability start -->', '<!-- agentos readability end -->'
if start in html and end in html:
    a = html.index(start)
    b = html.index(end, a) + len(end)
    html = html[:a] + html[b:]
patch = f'{start}\n<style>\n{css}\n</style>\n{end}\n'
ix = html.lower().index('</head>')
html = html[:ix] + patch + html[ix:]
backup = path.with_suffix(path.suffix + '.before-readability.bak')
if not backup.exists():
    backup.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
path.write_text(html, encoding='utf-8')
print(f'CSS appliqué : {path}')
print(f'Sauvegarde initiale : {backup}')
