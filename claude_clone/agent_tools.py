"""Outils réservés aux sous-agents (jamais exposés directement au superviseur).

Chaque outil impose sa limite PAR LE CODE (liste blanche, mode lecture seule SQLite, absence de réseau…),
pas par une consigne de prompt, qu'un contenu piégé pourrait contourner.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from pathlib import Path

from langchain_core.tools import ToolException, tool

from claude_clone import tools as T
from claude_clone.autopilot import classify_bash

# --- Terminal du développeur (dans SA copie jetable) -------------------------------------------------
@tool
async def run_command(command: str, timeout: int = 120) -> str:
    """Exécute une commande dans le terminal isolé (bac à sable, SANS réseau) du dossier de travail courant
    (pour le développeur : sa copie jetable). Sert à lancer tests, scripts, linters."""
    if not T.sandbox_enabled():
        raise ToolException("Terminal indisponible : l'isolation bash n'est pas active sur ce poste.")
    return await T.execute_bash.ainvoke({"command": command, "timeout": min(int(timeout), 120), "offline": True})


# --- Diagnostic DevOps : lecture seule stricte ---------------------------------------------------------
READONLY_CMDS = {"ls", "cat", "head", "tail", "wc", "grep", "rg", "sort", "uniq", "cut", "tr", "diff", "echo", "printf", "pwd",
                 "which", "file", "stat", "du", "df", "tree", "date", "basename", "dirname", "realpath", "find", "git", "pytest", "ruff"}
READONLY_GIT = {"status", "diff", "log", "show", "blame", "rev-parse", "ls-files"}


@tool
async def run_check(command: str) -> str:
    """Exécute une commande de DIAGNOSTIC en lecture seule (ls, cat, grep, find, git status/diff/log, pytest, ruff…),
    sans réseau ni redirection. Tout autre commande est refusée : liste-la dans « Actions proposées »."""
    ok, why = classify_bash(command, cmds=READONLY_CMDS, git=READONLY_GIT, redirects=False)
    if not ok:
        raise ToolException(f"Commande non autorisée pour ce rôle ({why}). Ne l'exécute pas : propose-la à l'utilisateur.")
    if not T.sandbox_enabled():
        raise ToolException("Diagnostic indisponible : l'isolation bash n'est pas active sur ce poste.")
    return await T.execute_bash.ainvoke({"command": command, "timeout": 60, "offline": True})


# --- git diff (reviewer) -------------------------------------------------------------------------------
async def _git(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", "-c", "core.fsmonitor=false", "-C", T.get_workdir(), *args,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=T.clean_env())
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 20)
    except asyncio.TimeoutError:
        proc.kill()
        raise ToolException("git : délai dépassé.")
    if proc.returncode != 0:
        raise ToolException(f"git a échoué : {err.decode(errors='replace')[:300].strip()}")
    return out.decode(errors="replace")


@tool
async def git_diff(staged: bool = False, path: str = "") -> str:
    """Affiche les modifications non commitées (ou indexées avec staged=true) du dépôt, plus la liste des fichiers non suivis."""
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"] + (["--staged"] if staged else [])
    if path:
        args += ["--", os.path.relpath(T.safe_path(path), T.get_workdir())]
    diff = await _git(*args)
    untracked = (await _git("ls-files", "--others", "--exclude-standard")).strip()
    text = diff[:60_000] + ("\n… [diff tronqué]" if len(diff) > 60_000 else "")
    return (text or "(aucune modification suivie)") + (f"\n\nFichiers non suivis :\n{untracked[:3000]}" if untracked else "")


# --- SQL en lecture seule (SQLite local) ---------------------------------------------------------------
_OK_ACTIONS = {getattr(sqlite3, n, v) for n, v in (("SQLITE_SELECT", 21), ("SQLITE_READ", 20), ("SQLITE_FUNCTION", 31), ("SQLITE_RECURSIVE", 33))}


def _connect(database: str) -> sqlite3.Connection:
    real = T.safe_path(database)
    if not os.path.isfile(real):
        raise ToolException(f"Base introuvable : {database}")
    return sqlite3.connect(Path(real).as_uri() + "?mode=ro", uri=True, timeout=5)  # mode=ro : ouverture en lecture seule


@tool
async def sql_describe(database: str) -> str:
    """Décrit une base SQLite du dossier de travail : tables, colonnes (type, clé) et nombre de lignes."""
    def _run() -> str:
        conn = _connect(database)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY 1 LIMIT 60")]
            out = []
            for t in tables:
                q = '"' + t.replace('"', '""') + '"'
                cols = ", ".join(f"{c[1]} {c[2]}{' PK' if c[5] else ''}" for c in conn.execute(f"PRAGMA table_info({q})"))
                out.append(f"{t} ({conn.execute(f'SELECT COUNT(*) FROM {q}').fetchone()[0]} lignes) : {cols}")
            return "\n".join(out) or "(base vide)"
        except sqlite3.Error as exc:
            raise ToolException(f"SQLite : {exc}")
        finally:
            conn.close()
    return await asyncio.to_thread(_run)


@tool
async def sql_query(database: str, query: str, max_rows: int = 100) -> str:
    """Exécute UNE requête SELECT (ou WITH…SELECT) en lecture seule sur une base SQLite du dossier de travail.
    Toute écriture, ATTACH ou PRAGMA est refusée par SQLite lui-même."""
    limit = max(1, min(int(max_rows), 200))

    def _run() -> str:
        conn = _connect(database)
        deadline = time.monotonic() + 8
        conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in _OK_ACTIONS else sqlite3.SQLITE_DENY)
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)  # coupe les requêtes trop longues
        try:
            cur = conn.execute(query)
            rows = cur.fetchmany(limit + 1)
            header = "\t".join(d[0] for d in (cur.description or []))
            body = "\n".join("\t".join(str(c)[:200] for c in r) for r in rows[:limit])
            return f"{header}\n{body}" + (f"\n… [limité à {limit} lignes]" if len(rows) > limit else "")
        except sqlite3.Error as exc:
            raise ToolException(f"SQLite : {exc}")
        finally:
            conn.close()
    return await asyncio.to_thread(_run)
