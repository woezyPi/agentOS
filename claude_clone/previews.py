"""Aperçus TEXTUELS des actions soumises à confirmation (version web de l'affichage Rich du CLI).

Le serveur calcule l'aperçu depuis ce qui est réellement en attente dans l'état du graphe : le client n'envoie jamais
le contenu à approuver, seulement une décision par identifiant d'appel.
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

from langchain_core.tools import ToolException

from claude_clone import developer as DEV
from claude_clone import tools as T

LIMIT = 12_000


def _cut(text: str) -> str:
    return text if len(text) <= LIMIT else text[:LIMIT] + f"\n… [tronqué : {len(text)} caractères]"


def _udiff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{name}", f"b/{name}", n=2)) or "(aucun changement)"


def preview(tc: dict, values: dict) -> dict:
    """{kind: bash|diff|code|json, title, body} pour un appel d'outil en attente."""
    name, args = tc["name"], tc["args"]
    try:
        if name == "execute_bash":
            return {"kind": "bash", "title": f"bash (timeout {args.get('timeout', 'défaut')} s)", "body": str(args.get("command", ""))}
        if name in ("write_file", "edit_file"):
            real = T.safe_path(str(args.get("path", "")))
            rel = os.path.relpath(real, T.get_workdir())
            if name == "write_file":
                new = T.resolve_coder_args(args, values.get("coder_blocks", [])).get("content")
                if not isinstance(new, str):
                    return {"kind": "json", "title": f"write_file → {rel}", "body": "contenu invalide : l'outil refusera"}
                if os.path.isfile(real):
                    return {"kind": "diff", "title": f"write_file → {rel}", "body": _cut(_udiff(Path(real).read_text(encoding="utf-8", errors="replace"), new, rel))}
                return {"kind": "code", "title": f"write_file → {rel} (nouveau fichier, {len(new.splitlines())} lignes)", "body": _cut(new)}
            old = Path(real).read_text(encoding="utf-8", errors="replace")
            new, count = T.apply_edit(old, args.get("old"), args.get("new"), bool(args.get("replace_all")))
            return {"kind": "diff", "title": f"edit_file → {rel} ({count} remplacement(s))", "body": _cut(_udiff(old, new, rel))}
        if name == T.APPLY_TOOL_NAME:
            cs = next((c for c in values.get("dev_changes", []) if c["id"] == args.get("change_set")), None)
            if cs is None:
                return {"kind": "json", "title": "apply_dev_changes", "body": "jeu de modifications introuvable : l'outil refusera"}
            return {"kind": "diff", "title": f"apply_dev_changes → jeu #{cs['id']} : {DEV.describe(cs)}", "body": _cut(DEV.change_set_diff(cs))}
    except (ToolException, OSError) as exc:
        return {"kind": "json", "title": name, "body": f"l'outil refusera : {exc}"}
    return {"kind": "json", "title": name, "body": _cut(json.dumps(args, ensure_ascii=False, indent=2, default=str))}
