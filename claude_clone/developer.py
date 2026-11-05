"""Agent développeur : travaille dans une COPIE JETABLE du projet et ne renvoie qu'un jeu de modifications.

Rien n'atteint le vrai dossier tant que le superviseur n'appelle pas apply_dev_changes (confirmation humaine, avec
diff). La copie exclut .git, .env*, clés (*.pem, *.key) et les dossiers lourds (SKIP_DIRS) : l'agent ne voit pas vos secrets.
Son terminal est isolé et sans réseau (voir agent_tools.run_command).
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import os
import shutil
import tempfile

from langchain_core.tools import ToolException

from claude_clone import tools as T
from claude_clone.autopilot import _protected

MAX_FILES, MAX_TOTAL, MAX_FILE, MAX_CHANGED = 5000, 40_000_000, 1_000_000, 40
CHANGE_SETS_KEPT = 5


def _excluded(name: str) -> bool:
    return name.startswith(".env") or name.endswith((".pem", ".key", ".p12", ".pfx")) or name in ("id_rsa", "id_ed25519")


def _walk(root: str):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in T.SKIP_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not os.path.islink(full) and not _excluded(name):
                yield os.path.relpath(full, root), full


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_scratch(real: str) -> tuple[str, dict[str, str]]:
    """Copie le projet dans un dossier temporaire. Renvoie (dossier, {chemin relatif: sha256})."""
    scratch = tempfile.mkdtemp(prefix="cc-dev-")
    manifest: dict[str, str] = {}
    total = 0
    try:
        for rel, full in _walk(real):
            size = os.path.getsize(full)
            total += size
            if size > MAX_FILE:
                continue
            if len(manifest) >= MAX_FILES or total > MAX_TOTAL:
                raise ToolException("Projet trop volumineux pour la copie du développeur (limite 5000 fichiers / 40 Mo).")
            dest = os.path.join(scratch, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(full, dest)
            with open(dest, "rb") as fh:
                manifest[rel] = _sha(fh.read())
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return scratch, manifest


def collect_changes(scratch: str, manifest: dict[str, str]) -> dict:
    files, skipped, seen = [], [], set()
    for rel, full in _walk(scratch):
        seen.add(rel)
        with open(full, "rb") as fh:
            data = fh.read()
        if manifest.get(rel) == _sha(data):
            continue
        try:
            if len(data) > MAX_FILE or b"\0" in data[:4096]:
                raise UnicodeDecodeError("x", b"", 0, 1, "binaire ou trop gros")
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append(rel)
            continue
        if len(files) >= MAX_CHANGED:
            skipped.append(rel)
            continue
        files.append({"path": rel, "status": "modified" if rel in manifest else "new", "base_sha": manifest.get(rel), "content": text})
    return {"files": files, "deleted": sorted(r for r in manifest if r not in seen), "skipped": skipped}


def change_set_diff(cs: dict) -> str:
    """Diff unifié du jeu de modifications par rapport au vrai dossier de travail."""
    real = T.get_workdir()
    parts = []
    for f in cs["files"]:
        try:
            with open(os.path.join(real, f["path"]), encoding="utf-8", errors="replace") as fh:
                old = fh.read()
        except OSError:
            old = ""
        parts.append("".join(difflib.unified_diff(old.splitlines(True), f["content"].splitlines(True), f"a/{f['path']}", f"b/{f['path']}", n=3)))
    return "\n".join(p for p in parts if p) or "(aucun changement)"


def describe(cs: dict) -> str:
    items = [f"{f['path']} ({'nouveau' if f['status'] == 'new' else 'modifié'})" for f in cs["files"]]
    extra = (f" · suppressions NON appliquées : {', '.join(cs['deleted'])}" if cs["deleted"] else "") + \
            (f" · ignorés (binaire/volumineux) : {', '.join(cs['skipped'])}" if cs["skipped"] else "")
    return (", ".join(items) or "aucun fichier modifié") + extra


def apply_change_set(cs: dict) -> str:
    """Écrit les fichiers du jeu dans le VRAI dossier. Refuse un fichier modifié entre-temps (conflit) ou sensible."""
    lines, applied = [], 0
    for f in cs["files"]:
        rel = f["path"]
        try:
            target = T.safe_path(rel)
        except ToolException as exc:
            lines.append(f"✖ {rel} : {exc}")
            continue
        if _protected(rel):
            lines.append(f"✖ {rel} : fichier sensible, à modifier à la main")
            continue
        exists = os.path.isfile(target)
        current = _sha(open(target, "rb").read()) if exists else None
        if current != f["base_sha"]:
            lines.append(f"✖ {rel} : conflit (fichier {'modifié depuis la copie' if exists else 'supprimé'}" if f["base_sha"] else f"✖ {rel} : existe déjà")
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(f["content"])
        applied += 1
        lines.append(f"✔ {rel}")
    if cs["deleted"]:
        lines.append(f"ℹ suppressions non appliquées (à faire à la main) : {', '.join(cs['deleted'])}")
    return f"{applied}/{len(cs['files'])} fichier(s) appliqué(s)\n" + "\n".join(lines)


async def run_in_scratch(run_subagent, *args, **kwargs) -> tuple[str, dict]:
    """Exécute `run_subagent(*args)` avec tous les outils redirigés vers une copie jetable ; renvoie (rapport, jeu de modifs)."""
    scratch, manifest = await asyncio.to_thread(make_scratch, T.get_workdir())
    try:
        with T.use_workdir(scratch):
            report = await run_subagent(*args, **kwargs)
        return report, await asyncio.to_thread(collect_changes, scratch, manifest)
    finally:
        await asyncio.to_thread(shutil.rmtree, scratch, True)
