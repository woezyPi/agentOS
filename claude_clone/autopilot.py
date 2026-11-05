"""Mode auto : approuve sans question les actions jugées sûres, laisse le Y/n pour le reste.

Principe (déterministe, aucun appel LLM) — une action est auto-approuvée seulement si TOUT est vrai :
  * fichiers : chemin dans le dossier de travail (safe_path) ET pas un fichier sensible (.git, .env*, .github,
    .vscode, rc du shell…) : un hook git ou un .envrc modifié s'exécuterait plus tard HORS de l'isolation ;
  * bash : l'isolation (unshare) est active ; la commande ne contient ni substitution ($(), `…`), ni
    retour à la ligne, ni variable en préfixe ; chaque segment (; && || |) commence par une commande de la liste
    blanche SAFE_CMDS ; aucun argument ne désigne un chemin hors du dossier de travail (ni ~, ni ..) ni un
    chemin sensible ; les cas dangereux de find/sed/git/python sont exclus.
Une commande auto-approuvée tourne SANS réseau (offline). Tout ce qui n'est pas reconnu → confirmation normale.
Ce n'est pas une preuve d'innocuité : un script python du projet peut écrire ce qu'il veut dans le dossier de travail.
"""
from __future__ import annotations

import os
import shlex

from claude_clone import tools as tools_mod

SAFE_CMDS = {
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "sort", "uniq", "cut", "tr", "diff", "echo", "printf", "pwd",
    "true", "false", "test", "[", "which", "file", "stat", "du", "df", "tree", "date", "basename", "dirname",
    "realpath", "mkdir", "touch", "cp", "mv", "cd", "find", "sed", "git", "python", "python3", "pytest", "ruff",
    "black", "mypy",
}
GIT_OK = {"status", "diff", "log", "show", "add", "commit", "blame", "rev-parse", "ls-files"}
FIND_BAD = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
PROTECTED = {".git", ".github", ".vscode", ".idea", ".ssh", ".envrc", ".bashrc", ".zshrc", ".profile", ".gitmodules", ".gitattributes"}
SEPARATORS = {";", "&&", "||", "|"}
REDIRECTS = {">", ">>", ">&", "<"}
FORBIDDEN_SUBSTRINGS = ("$(", "`", "<(", ">(", "${")
# Code Python inline (python -c "...") : garde-fou heuristique — la vraie barrière reste l'isolation + l'absence de réseau.
PY_INLINE_RISKY = ("subprocess", "os.system", "os.popen", "os.exec", "os.spawn", "shutil", "rmtree", "os.remove", "os.unlink",
                   "os.rmdir", "socket", "urllib", "http", "requests", "ctypes", "__import__", "eval(", "exec(", "compile(",
                   "importlib", "pty", "/etc", "/home", "/root", "/proc", "/sys", "/var", "/usr", "/dev/", "~", "..")


def _unquoted_newline(cmd: str) -> bool:
    """Vrai s'il y a un retour à la ligne HORS guillemets (il séparerait deux commandes) ; dans des
    guillemets (code python -c, message de commit multi-lignes) c'est inoffensif."""
    quote, i = None, 0
    while i < len(cmd):
        c = cmd[i]
        if quote is None:
            if c in "\n\r":
                return True
            if c == "\\":
                i += 1
            elif c in "'\"":
                quote = c
        elif quote == "'":
            if c == "'":
                quote = None
        elif c == "\\":
            i += 1
        elif c == '"':
            quote = None
        i += 1
    return False


def _protected(path: str) -> bool:
    return any(p in PROTECTED or p.startswith(".env") for p in path.replace("\\", "/").split("/") if p)


def _tokens(cmd: str) -> list[str]:
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    return list(lex)


def _path_ok(tok: str) -> bool:
    """Vrai si le jeton, interprété comme un chemin, reste dans le dossier de travail et n'est pas sensible."""
    if tok == "/dev/null":
        return True
    if _protected(tok):
        return False
    if tok.startswith(("/", "~")) or ".." in tok.split("/"):
        if tok.startswith("~"):
            return False
        try:
            real = os.path.realpath(os.path.join(tools_mod.WORKDIR, tok))
            return tools_mod._inside(real, tools_mod.WORKDIR)
        except (ValueError, OSError):
            return False
    return True


def classify_bash(command: str) -> tuple[bool, str]:
    if not isinstance(command, str) or not command.strip():
        return False, "commande vide"
    if any(s in command for s in FORBIDDEN_SUBSTRINGS):
        return False, "substitution ou variable shell"
    if _unquoted_newline(command):
        return False, "retour à la ligne hors guillemets (commandes multiples)"
    try:
        toks = _tokens(command)
    except ValueError:
        return False, "guillemets non équilibrés"
    segment: list[str] = []
    segments: list[list[str]] = []
    for tok in toks:
        if tok in SEPARATORS:
            segments.append(segment)
            segment = []
        elif tok in REDIRECTS or not set(tok) <= set("<>&|;()"):
            segment.append(tok)
        else:
            return False, f"opérateur non géré ({tok})"
    segments.append(segment)
    for seg in segments:
        words = [t for t in seg]
        if not words:
            return False, "segment vide"
        head = words[0]
        if head not in SAFE_CMDS:
            return False, f"commande hors liste blanche ({head})"
        rest = words[1:]
        if head == "git" and (not rest or rest[0] not in GIT_OK):
            return False, "sous-commande git non autorisée"
        if head == "find" and FIND_BAD & set(rest):
            return False, "find avec action dangereuse"
        if head == "sed" and any(t == "--in-place" or (t.startswith("-") and not t.startswith("--") and "i" in t) for t in rest):
            return False, "sed -i"
        if head in ("python", "python3") and rest[:2] and ("pip" in rest[:2] or "ensurepip" in rest[:2]):
            return False, "installation de paquets"
        if head in ("python", "python3") and "-c" in rest:
            code = rest[rest.index("-c") + 1] if rest.index("-c") + 1 < len(rest) else ""
            risky = next((r for r in PY_INLINE_RISKY if r in code), None)
            if risky:
                return False, f"code python inline sensible ({risky})"
            rest = [t for t in rest if t != code]  # le code n'est pas un chemin : déjà inspecté ci-dessus
        for tok in rest:
            if not tok.startswith("-") and not _path_ok(tok):
                return False, f"chemin hors périmètre ou sensible ({tok})"
    return True, "commande de la liste blanche, exécutée sans réseau"


def classify(tc: dict) -> tuple[bool, str]:
    """(auto_ok, raison). Hors des outils éligibles → (False, …) : confirmation normale."""
    name, args = tc["name"], tc["args"]
    if name in ("write_file", "edit_file"):
        try:
            real = tools_mod.safe_path(str(args.get("path", "")))
        except Exception:
            return False, "chemin refusé"
        rel = os.path.relpath(real, tools_mod.WORKDIR)
        if _protected(rel):
            return False, f"fichier sensible ({rel})"
        return True, "écriture dans le dossier de travail"
    if name == "execute_bash":
        if not tools_mod.sandbox_enabled():
            return False, "isolation bash indisponible"
        return classify_bash(args.get("command"))
    return False, "outil non éligible"
