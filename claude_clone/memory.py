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
PROFILE_FILE = Path.home() / ".claude_clone" / "profile.md"  # qui est l'utilisateur, comment il code, comment collaborer
DECISION_FILES = ("DECISIONS.md", "docs/decisions.md")      # journal des décisions du projet (ADR légères)


def _read(path: Path, tail: bool = False, limit: int = MAX_CHARS) -> str | None:
    """Lit un fichier texte ; au-delà de `limit`, garde le début (ou la FIN si tail : décisions les plus récentes)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if len(text) > limit:
        text = ("… [début tronqué]\n" + text[-limit:]) if tail else (text[:limit] + "\n… [tronqué]")
    return text or None


def load_profile(limit: int = 3500) -> str:
    """Profil de l'utilisateur (injecté aussi dans les sous-agents, qui ne voient pas la conversation)."""
    return _read(PROFILE_FILE, limit=limit) or ""


def load_project_memory() -> tuple[str, list[str]]:
    """Renvoie (texte à injecter, noms des fichiers chargés)."""
    parts: list[str] = []
    names: list[str] = []
    if (text := load_profile()):
        parts.append(f"### Profil de l'utilisateur (qui il est, comment il code)\n{text}")
        names.append("profil")
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
    for name in DECISION_FILES:
        path = Path(tools_mod.WORKDIR) / name
        if path.is_file() and tools_mod._inside(os.path.realpath(path), tools_mod.WORKDIR) and (text := _read(path, tail=True, limit=4000)):
            parts.append(f"### Décisions antérieures du projet ({name}) — à respecter, ou à remettre en cause EXPLICITEMENT\n{text}")
            names.append(name)
            break
    return "\n\n".join(parts), names
