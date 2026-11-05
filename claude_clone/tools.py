"""Outils locaux exposés au Superviseur.

Sécurité :
  * read_file / write_file passent par `safe_path` : le chemin est normalisé avec
    os.path.abspath (anti "../") PUIS résolu avec os.path.realpath (anti
    symlink sortant), et doit rester sous WORKDIR (comparaison par commonpath,
    pas par startswith : "/work2" n'est pas dans "/work").
  * execute_bash : $HOME masqué (namespaces, voir sandbox_enabled) + timeout + confirmation
    humaine (interrupt_before=["tools"]). Ce n'est pas une isolation complète (réseau et
    reste du système accessibles) : lisez les commandes avant de valider.

Les outils lèvent ToolException en cas d'échec attendu ; le noeud "tools" du
graphe la convertit en ToolMessage(status="error") pour que le LLM puisse
corriger.
"""
from __future__ import annotations

import asyncio
import fnmatch
import itertools
import os
import re
import shutil
import signal
import subprocess
import time
from typing import Literal

from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field

from claude_clone.config import (
    BASH_DEFAULT_TIMEOUT,
    BASH_MAX_TIMEOUT,
    MAX_OUTPUT_CHARS,
    MAX_READ_BYTES,
)

# Dossier de travail = répertoire courant au lancement. Figé une fois pour toutes.
WORKDIR: str = os.path.realpath(os.path.abspath(os.getcwd()))

DELEGATE_TOOL_NAME = "delegate_to_coder"
# Outils qui modifient la machine → confirmation Y/n obligatoire dans le CLI.
TOOLS_REQUIRING_APPROVAL = frozenset({"execute_bash", "write_file", "edit_file"})
# Dossiers jamais parcourus par grep/glob (bruit, volumineux).
SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build"})


_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)


def clean_env() -> dict[str, str]:
    """Environnement des commandes bash, sans variable ressemblant à un secret
    (le LLM ne doit pas pouvoir faire `env` et lire nos clés API)."""
    return {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}


# --------------------------------------------------------------------------- #
# Isolation de bash (espaces de noms Linux via `unshare`, sans root ni bwrap)
#   * $HOME est remplacé par un tmpfs vide → ~/.hermes/.env, ~/.ssh… sont invisibles ;
#   * le dossier de travail reste monté et modifiable ;
#   * pid isolé (--kill-child) ; réseau coupé si BASH_SANDBOX_NET=0.
# BASH_SANDBOX=off désactive. Si `unshare` ne fonctionne pas, repli non isolé (signalé par le CLI).
# --------------------------------------------------------------------------- #
_SANDBOX_SCRIPT = (
    'W="$1"; H="$2"; C="$3"; T=$(mktemp -d) || exit 126; '
    'mount --bind "$W" "$T" && mount -t tmpfs tmpfs "$H" && mkdir -p "$W" && mount --bind "$T" "$W" || exit 126; '
    'cd "$W" && exec bash -c "$C"'
)
_sandbox_ok: bool | None = None


def sandbox_enabled() -> bool:
    global _sandbox_ok
    if _sandbox_ok is None:
        if os.getenv("BASH_SANDBOX", "auto").lower() in ("off", "0", "false") or not shutil.which("unshare"):
            _sandbox_ok = False
        else:
            try:
                r = subprocess.run(["unshare", "-Urm", "--pid", "--fork", "true"], capture_output=True, timeout=5)
                _sandbox_ok = r.returncode == 0
            except (OSError, subprocess.SubprocessError):
                _sandbox_ok = False
    return _sandbox_ok


def _bash_argv(command: str, offline: bool = False) -> list[str]:
    argv = ["unshare", "-Urm", "--pid", "--fork", "--mount-proc", "--kill-child"]
    if offline or os.getenv("BASH_SANDBOX_NET", "1") == "0":
        argv.append("--net")
    return [*argv, "--", "bash", "-c", _SANDBOX_SCRIPT, "_", WORKDIR, os.path.expanduser("~"), command]


def set_workdir(path: str) -> None:
    """Change le dossier de travail (utile pour les tests)."""
    global WORKDIR
    WORKDIR = os.path.realpath(os.path.abspath(path))


# --------------------------------------------------------------------------- #
# Garde anti directory-traversal
# --------------------------------------------------------------------------- #
def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # lecteurs différents (Windows), chemins mixtes…
        return False


def safe_path(path: str) -> str:
    """Retourne le chemin réel de `path` s'il est dans WORKDIR, sinon lève ToolException."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ToolException("Chemin invalide.")
    # os.path.join ignore WORKDIR si `path` est absolu → abspath → test explicite.
    lexical = os.path.abspath(os.path.join(WORKDIR, path))
    if not _inside(lexical, WORKDIR):
        raise ToolException(f"Accès refusé : '{path}' sort du dossier de travail ({WORKDIR}).")
    real = os.path.realpath(lexical)  # suit les symlinks (existants)
    if not _inside(real, WORKDIR):
        raise ToolException(f"Accès refusé : '{path}' pointe (symlink) hors du dossier de travail.")
    return real


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head, tail = text[: limit // 2], text[-limit // 2 :]
    return f"{head}\n… [{len(text) - limit} caractères tronqués] …\n{tail}"


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #
def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


@tool
async def execute_bash(command: str, timeout: int = BASH_DEFAULT_TIMEOUT, offline: bool = False) -> str:
    """Exécute une commande bash dans le dossier de travail et renvoie code de sortie, stdout et stderr.

    La commande est non interactive (stdin fermé) et tuée après `timeout` secondes.
    `offline=true` coupe le réseau pour cette commande.
    """
    if not isinstance(command, str) or not command.strip():
        raise ToolException("Commande vide.")
    timeout = max(1, min(int(timeout), BASH_MAX_TIMEOUT))
    if offline and not sandbox_enabled():
        raise ToolException("offline impossible : isolation bash indisponible.")
    common = dict(
        cwd=WORKDIR,
        env=clean_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # nouveau groupe → on peut tuer toute la descendance
    )
    if sandbox_enabled():
        proc = await asyncio.create_subprocess_exec(*_bash_argv(command, offline), **common)
    else:
        proc = await asyncio.create_subprocess_shell(command, executable=shutil.which("bash"), **common)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        _kill_process_group(proc)
        await proc.wait()
        raise ToolException(f"Timeout : commande interrompue après {timeout}s.")
    except asyncio.CancelledError:  # Ctrl-C côté CLI : ne pas laisser de zombie
        _kill_process_group(proc)
        raise
    return (
        f"exit_code: {proc.returncode}\n"
        f"--- stdout ---\n{_truncate(out.decode(errors='replace'))}\n"
        f"--- stderr ---\n{_truncate(err.decode(errors='replace'))}"
    )


@tool
async def read_file(path: str) -> str:
    """Lit un fichier texte (UTF-8) situé dans le dossier de travail et renvoie son contenu."""
    real = safe_path(path)

    def _read() -> str:
        if not os.path.isfile(real):
            raise ToolException(f"Fichier introuvable : {path}")
        with open(real, "rb") as fh:
            data = fh.read(MAX_READ_BYTES + 1)
        text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        if len(data) > MAX_READ_BYTES:
            text += f"\n… [fichier tronqué à {MAX_READ_BYTES} octets]"
        return text

    return await asyncio.to_thread(_read)


@tool
async def write_file(path: str, content: str = "", coder_block: int = 0) -> str:
    """Crée ou écrase un fichier texte (UTF-8) dans le dossier de travail. Crée les dossiers parents.

    Pour écrire du code produit par l'Agent Codeur SANS le recopier, laisse `content` vide et
    donne `coder_block` = N (≥ 1) : le N-ième bloc de code de son dernier résultat est écrit tel quel.
    """
    if coder_block:  # normalement résolu par le noeud "tools" avant l'appel
        raise ToolException("coder_block non résolu : aucun résultat de l'Agent Codeur disponible.")
    real = safe_path(path)
    if not isinstance(content, str):
        raise ToolException("`content` doit être une chaîne.")

    def _write() -> str:
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, "w", encoding="utf-8") as fh:
            fh.write(content)
        return f"OK : {len(content)} caractères écrits dans {os.path.relpath(real, WORKDIR)}"

    return await asyncio.to_thread(_write)


def resolve_coder_args(args: dict, blocks: list[str]) -> dict:
    """Remplace `coder_block=N` par le contenu du N-ième bloc du codeur (ou lève ToolException)."""
    n = args.get("coder_block") or 0
    if not n:
        return args
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(blocks):
        raise ToolException(f"coder_block={n!r} invalide : {len(blocks)} bloc(s) disponible(s) (numérotés à partir de 1).")
    if args.get("content"):
        raise ToolException("Fournis soit `content`, soit `coder_block`, pas les deux.")
    return {**args, "content": blocks[n - 1], "coder_block": 0}


def apply_edit(text: str, old: str, new: str, replace_all: bool = False) -> tuple[str, int]:
    """Remplacement exact. Lève ToolException si `old` est vide, introuvable ou ambigu."""
    if not isinstance(old, str) or not isinstance(new, str) or old == "":
        raise ToolException("`old` et `new` doivent être des chaînes, `old` non vide.")
    n = text.count(old)
    if n == 0:
        raise ToolException("`old` introuvable : copie exacte requise (indentation et sauts de ligne compris). Relis le fichier.")
    if n > 1 and not replace_all:
        raise ToolException(f"`old` apparaît {n} fois : ajoute du contexte pour le rendre unique, ou replace_all=true.")
    return (text.replace(old, new) if replace_all else text.replace(old, new, 1)), (n if replace_all else 1)


@tool
async def edit_file(path: str, old: str, new: str, replace_all: bool = False) -> str:
    """Modifie un fichier existant en remplaçant le texte exact `old` par `new` (préférable à write_file
    pour un changement local). `old` doit être unique dans le fichier, sauf si replace_all=true."""
    real = safe_path(path)

    def _edit() -> str:
        if not os.path.isfile(real):
            raise ToolException(f"Fichier introuvable : {path}")
        with open(real, encoding="utf-8", newline="") as fh:  # newline="" : préserve \r\n
            text = fh.read()
        updated, count = apply_edit(text, old, new, replace_all)
        with open(real, "w", encoding="utf-8", newline="") as fh:
            fh.write(updated)
        return f"OK : {count} remplacement(s) dans {os.path.relpath(real, WORKDIR)}"

    return await asyncio.to_thread(_edit)


def _readable_files(root: str, file_glob: str = ""):
    """Itère sur les fichiers texte sous `root` (sans SKIP_DIRS, sans symlink sortant, < 1 Mo)."""
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if file_glob and not fnmatch.fnmatch(name, file_glob):
                continue
            full = os.path.join(dirpath, name)
            try:
                if not _inside(os.path.realpath(full), WORKDIR) or os.path.getsize(full) > 1_000_000:
                    continue
            except OSError:
                continue
            yield full


@tool
async def grep(pattern: str, path: str = ".", file_glob: str = "", ignore_case: bool = False, max_results: int = 100) -> str:
    """Cherche une expression régulière (syntaxe Python) dans les fichiers texte du dossier de travail.
    Renvoie `fichier:ligne:texte`. `file_glob` filtre par nom (ex: '*.py')."""
    try:
        rx = re.compile(pattern, re.I if ignore_case else 0)
    except re.error as exc:
        raise ToolException(f"Expression régulière invalide : {exc}")
    root = safe_path(path)
    limit = max(1, min(int(max_results), 500))

    def _search() -> str:
        hits: list[str] = []
        deadline = time.monotonic() + 15
        for full in _readable_files(root, file_glob):
            if time.monotonic() > deadline:
                hits.append("… (temps limite de 15 s atteint)")
                break
            try:
                with open(full, "rb") as fh:
                    if b"\0" in fh.read(4096):
                        continue  # binaire
                with open(full, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if rx.search(line[:2000]):
                            hits.append(f"{os.path.relpath(full, WORKDIR)}:{lineno}:{line.rstrip()[:300]}")
                            if len(hits) >= limit:
                                hits.append(f"… (limite de {limit} résultats atteinte)")
                                return "\n".join(hits)
            except OSError:
                continue
        return "\n".join(hits) or "Aucun résultat."

    return await asyncio.to_thread(_search)


@tool("glob")
async def glob_files(pattern: str, max_results: int = 200) -> str:
    """Liste les fichiers dont le chemin (relatif au dossier de travail) correspond au motif glob,
    p. ex. '**/*.py' ou 'src/*.js'."""
    if not isinstance(pattern, str) or not pattern.strip() or os.path.isabs(pattern) or ".." in pattern.split("/"):
        raise ToolException("Motif invalide : relatif au dossier de travail, sans '..'.")
    limit = max(1, min(int(max_results), 1000))

    def _glob() -> str:
        from pathlib import Path
        found: list[str] = []
        for p in itertools.islice(Path(WORKDIR).glob(pattern), 50_000):  # plafond de parcours
            rel = p.relative_to(WORKDIR)
            if any(part in SKIP_DIRS for part in rel.parts) or not p.is_file():
                continue
            if not _inside(os.path.realpath(p), WORKDIR):
                continue
            found.append(str(rel))
            if len(found) >= limit:
                found.append(f"… (limite de {limit} résultats atteinte)")
                break
        return "\n".join(sorted(found[:limit]) + found[limit:]) or "Aucun fichier."

    return await asyncio.to_thread(_glob)


# --------------------------------------------------------------------------- #
# Outil de délégation (exécuté par le noeud "coder", pas par le noeud "tools")
# --------------------------------------------------------------------------- #
class DelegateArgs(BaseModel):
    task: str = Field(description="Description précise et autonome du code à produire ou à modifier.")
    context: str = Field(
        default="",
        description=(
            "Tout ce dont le codeur a besoin : contenu des fichiers concernés, contraintes, "
            "conventions. Le codeur n'a accès ni au disque ni à l'historique de la conversation."
        ),
    )


@tool(DELEGATE_TOOL_NAME, args_schema=DelegateArgs)
def delegate_to_coder(task: str, context: str = "") -> str:
    """Délègue la rédaction ou la modification de code à l'Agent Codeur (modèle spécialisé).

    Il renvoie le code en texte ; applique-le avec write_file(path, coder_block=N), sans le recopier.
    À appeler seul (jamais dans le même tour que d'autres outils).
    """
    raise RuntimeError("Exécuté par le noeud 'coder' du graphe, jamais appelé directement.")


class TodoItem(BaseModel):
    content: str = Field(description="Étape, formulée à l'impératif et vérifiable.")
    status: Literal["pending", "in_progress", "completed"] = "pending"


class PlanArgs(BaseModel):
    todos: list[TodoItem] = Field(
        description="Liste COMPLÈTE et ordonnée des étapes (remplace le plan précédent). "
        "Au plus une étape in_progress. Liste vide = effacer le plan."
    )


@tool("update_plan", args_schema=PlanArgs)
def update_plan(todos: list[TodoItem]) -> str:
    """Crée ou met à jour le plan de travail visible par l'utilisateur. À utiliser dès qu'une tâche
    comporte 3 étapes ou plus, puis à chaque changement d'état d'une étape."""
    raise RuntimeError("Exécuté par le noeud 'tools' du graphe (met à jour l'état).")


LOCAL_TOOLS = [execute_bash, read_file, write_file, edit_file, grep, glob_files, update_plan]
LOCAL_TOOLS_BY_NAME = {t.name: t for t in LOCAL_TOOLS}
