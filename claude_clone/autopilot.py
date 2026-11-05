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
import re
import shlex

from claude_clone import tools as tools_mod

SAFE_CMDS = {
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "sort", "uniq", "cut", "tr", "diff", "echo", "printf", "pwd",
    "true", "false", "test", "[", "which", "file", "stat", "du", "df", "tree", "date", "basename", "dirname",
    "realpath", "mkdir", "touch", "cp", "mv", "cd", "find", "sed", "git", "python", "python3", "pytest", "ruff",
    "black", "mypy", "whoami", "uname", "hostname", "id", "env", "printenv", "nproc", "command", "type",
}
GIT_OK = {"status", "diff", "log", "show", "add", "commit", "blame", "rev-parse", "ls-files"}
FIND_BAD = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
PROTECTED = {".git", ".github", ".vscode", ".idea", ".ssh", ".envrc", ".bashrc", ".zshrc", ".profile", ".gitmodules", ".gitattributes"}
SEPARATORS = {";", "&&", "||", "|"}
REDIRECTS = {">", ">>", ">&", "<"}
FORBIDDEN_SUBSTRINGS = ("$(", "`", "<(", ">(", "${")
# Code Python inline (python -c "...") : garde-fou heuristique — la vraie barrière reste l'isolation + l'absence de réseau.
PY_RISKY = re.compile("|".join([
    r"\bsubprocess\b", r"\bos\.(system|popen|exec\w*|spawn\w*|remove|unlink|rmdir|removedirs|chmod|chown|kill)\b",
    r"\bshutil\b", r"\brmtree\b", r"\bctypes\b", r"__import__", r"\beval\s*\(", r"\bexec\s*\(", r"\bcompile\s*\(",
    r"\bimportlib\b", r"\bpty\b", r"\.\./", r"/(etc|root|proc|sys|var|usr)\b", r"/dev/(?!null)",
]))  # le réseau est coupé (offline) et $HOME masqué : inutile de les filtrer ici


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


ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template", ".env.dist"}  # modèles de config : pas de secret


def _protected(path: str) -> bool:
    return any(p in PROTECTED or (p.startswith(".env") and p not in ENV_TEMPLATES) for p in path.replace("\\", "/").split("/") if p)


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
        if tok == "~" or tok.startswith("~/"):
            return True  # HOME est un tmpfs vide dans l'isolation : rien à y lire ni à y casser
        if tok.startswith("~"):
            return False  # ~autre_utilisateur
        try:
            real = os.path.realpath(os.path.join(tools_mod.WORKDIR, tok))
            return tools_mod._inside(real, tools_mod.WORKDIR)
        except (ValueError, OSError):
            return False
    return True


INTERPRETER = re.compile(r"^(python[0-9.]*|pytest|ruff|black|mypy)$")
HEREDOC = re.compile(r"^(?P<pre>[^\n]*?)<<-?[ \t]*(?P<q>['\"])(?P<tag>\w+)(?P=q)[ \t]*\n(?P<body>.*)\n(?P=tag)[ \t]*\n?$", re.S)


def _effective_head(head: str) -> str:
    """`.venv/bin/python` ou `./venv/bin/pytest` DANS le dossier de travail = l'interpréteur correspondant."""
    if "/" in head and INTERPRETER.match(os.path.basename(head)):
        lexical = os.path.abspath(os.path.join(tools_mod.WORKDIR, head))
        if tools_mod._inside(lexical, tools_mod.WORKDIR):
            name = os.path.basename(head)
            return "python3" if name.startswith("python") else name
    return head


def classify_bash(command: str, *, cmds: set[str] | None = None, git: set[str] | None = None, redirects: bool = True) -> tuple[bool, str]:
    """`cmds`/`git` restreignent la liste blanche (rôle DevOps : diagnostic seul) ; `redirects=False` interdit > >> <."""
    allowed, git_ok = (cmds if cmds is not None else SAFE_CMDS), (git if git is not None else GIT_OK)
    if not isinstance(command, str) or not command.strip():
        return False, "commande vide"
    if (hd := HEREDOC.match(command)) and "\n" not in hd["pre"]:
        # Heredoc « <<'TAG' » (délimiteur entre guillemets : aucune expansion) pour python / cat, en dernière position.
        body = hd["body"]
        if any(line.strip() == hd["tag"] for line in body.split("\n")):
            return False, "heredoc : le délimiteur apparaît dans le corps (commandes cachées)"
        ok, why = classify_bash(hd["pre"].strip(), cmds=cmds, git=git, redirects=redirects)
        if not ok:
            return ok, why
        try:
            head = _effective_head(_tokens(hd["pre"])[0])
        except (ValueError, IndexError):
            return False, "heredoc illisible"
        if head not in ("python", "python3", "cat"):
            return False, f"heredoc non géré pour {head}"
        if head != "cat" and (risky := PY_RISKY.search(body)):
            return False, f"code python (heredoc) sensible ({risky.group(0)})"
        return True, "heredoc python/cat, exécuté sans réseau"
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
        elif tok in REDIRECTS and not redirects:
            return False, "redirection interdite (lecture seule)"
        elif tok in REDIRECTS or not set(tok) <= set("<>&|;()"):
            segment.append(tok)
        else:
            return False, f"opérateur non géré ({tok})"
    segments.append(segment)
    for seg in segments:
        words = [t for t in seg]
        if not words:
            return False, "segment vide"
        head = _effective_head(words[0])
        if head not in allowed:
            return False, f"commande hors liste blanche ({head})"
        rest = words[1:]
        if head == "git" and (not rest or rest[0] not in git_ok):
            return False, "sous-commande git non autorisée"
        if head == "find" and FIND_BAD & set(rest):
            return False, "find avec action dangereuse"
        if head == "sed" and any(t == "--in-place" or (t.startswith("-") and not t.startswith("--") and "i" in t) for t in rest):
            return False, "sed -i"
        if head in ("python", "python3") and rest[:2] and ("pip" in rest[:2] or "ensurepip" in rest[:2]):
            return False, "installation de paquets"
        if head in ("python", "python3") and "-c" in rest:
            code = rest[rest.index("-c") + 1] if rest.index("-c") + 1 < len(rest) else ""
            risky = PY_RISKY.search(code)
            if risky:
                return False, f"code python inline sensible ({risky.group(0)})"
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
