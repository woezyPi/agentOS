"""Mémoire projet : instructions persistantes injectées dans le prompt du superviseur.

Sources (dans cet ordre) :
  1. ~/.claude_clone/AGENTS.md            — préférences globales de l'utilisateur
  2. <dossier de travail>/AGENTS.md ou CLAUDE.md — conventions du projet (le premier trouvé)
Chaque fichier est tronqué à MAX_CHARS. Le contenu est traité comme des instructions de l'utilisateur :
ne mettez donc dans ces fichiers que ce que vous écririez vous-même.
"""
from __future__ import annotations

import os
from pathlib import Path

from claude_clone import tools as tools_mod

MAX_CHARS = 8000
GLOBAL_FILE = Path.home() / ".claude_clone" / "AGENTS.md"


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n… [tronqué]"
    return text or None


def load_project_memory() -> tuple[str, list[str]]:
    """Renvoie (texte à injecter, noms des fichiers chargés)."""
    parts: list[str] = []
    names: list[str] = []
    if (text := _read(GLOBAL_FILE)):
        parts.append(f"### Préférences globales\n{text}")
        names.append("~/.claude_clone/AGENTS.md")
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = Path(tools_mod.WORKDIR) / name
        # un lien symbolique pointant hors du dossier de travail est ignoré
        if path.is_file() and tools_mod._inside(os.path.realpath(path), tools_mod.WORKDIR) and (text := _read(path)):
            parts.append(f"### Projet ({name})\n{text}")
            names.append(name)
            break
    return "\n\n".join(parts), names
