"""Skills : procédures réutilisables (instructions en Markdown) que le superviseur charge à la demande.

Un skill = un dossier avec un fichier SKILL.md :

    ---
    name: stack-map                 # [a-z0-9-], 2 à 41 caractères
    description: Une phrase : quoi + quand l'utiliser (c'est ce que le superviseur lit pour choisir).
    tools: [scan_stack]             # (informatif) outils sur lesquels s'appuie le skill
    ---
    # Instructions détaillées…

Chargement « progressif » : seul l'INDEX (nom + description) est dans le prompt ; le corps n'est lu que quand le superviseur
appelle use_skill(name). Sources : skills intégrés (claude_clone/skills/) puis ~/.claude_clone/skills/ (vos skills ; ils peuvent
remplacer un intégré du même nom). JAMAIS le dossier du projet : un skill est une instruction injectée dans le prompt, donc un
dépôt tiers ne doit pas pouvoir en déclarer (même raison que pour mcp.json).
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml
from langchain_core.tools import ToolException, tool

BUILTIN_DIR = Path(__file__).parent / "skills"
USER_DIR = Path.home() / ".claude_clone" / "skills"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MAX_BYTES = 40_000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    tools: tuple[str, ...]
    builtin: bool
    path: str


def parse_skill(path: Path, builtin: bool) -> Skill:
    return parse_skill_text(path.read_bytes(), builtin, str(path))


def parse_skill_text(raw: bytes, builtin: bool, path: str) -> Skill:
    """Lève ValueError avec un message clair si le skill est invalide."""
    if len(raw) > MAX_BYTES:
        raise ValueError(f"fichier trop gros ({len(raw)} octets, max {MAX_BYTES})")
    text = raw.decode("utf-8", errors="replace")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise ValueError("en-tête manquant : le fichier doit commencer par --- name / description ---")
    if m.group(1).count("*") > 50 or m.group(1).count("&") > 50:
        raise ValueError("en-tête YAML suspect")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"en-tête YAML invalide : {str(exc)[:80]}")
    if not isinstance(meta, dict):
        raise ValueError("en-tête YAML inattendu")
    name, desc = str(meta.get("name", "")), str(meta.get("description", "")).strip()
    if not NAME_RE.match(name):
        raise ValueError(f"nom invalide {name!r} : [a-z0-9-], 2 à 41 caractères")
    if not 10 <= len(desc) <= 400:
        raise ValueError("description : entre 10 et 400 caractères (elle sert au superviseur à choisir le skill)")
    body = m.group(2).strip()
    if not body:
        raise ValueError("corps vide")
    tools = tuple(str(t) for t in (meta.get("tools") or []) if isinstance(t, str))
    return Skill(name, desc, body, tools, builtin, path)


def _load_dir(directory: Path, builtin: bool) -> tuple[dict[str, Skill], list[str]]:
    skills: dict[str, Skill] = {}
    warnings: list[str] = []
    if not directory.is_dir():
        return skills, warnings
    for path in sorted(directory.glob("*/SKILL.md")):
        if path.is_symlink() or path.parent.is_symlink():
            warnings.append(f"skill ignoré (lien symbolique) : {path.parent.name}")
            continue
        try:
            skill = parse_skill(path, builtin)
        except (ValueError, OSError) as exc:
            warnings.append(f"skill « {path.parent.name} » ignoré : {exc}")
            continue
        if skill.name != path.parent.name:
            warnings.append(f"skill « {path.parent.name} » ignoré : le nom ({skill.name}) doit égaler le dossier")
            continue
        skills[skill.name] = skill
    return skills, warnings


def load_skills() -> tuple[dict[str, Skill], list[str]]:
    """({nom: Skill}, avertissements). Un skill invalide est ignoré (et signalé), il ne casse jamais le démarrage.
    Un skill utilisateur remplace l'intégré du même nom."""
    builtin, w1 = _load_dir(BUILTIN_DIR, True)
    user, w2 = _load_dir(USER_DIR, False)
    return {**builtin, **user}, w1 + w2


def user_skill_names() -> set[str]:
    return set(_load_dir(USER_DIR, False)[0])


def save_user_skill(name: str, description: str, tools: list[str], body: str) -> Skill:
    """Crée ou remplace ~/.claude_clone/skills/<name>/SKILL.md (écriture atomique, validée avant d'écrire)."""
    import yaml as _yaml
    if not NAME_RE.match(name or ""):
        raise ValueError(f"nom invalide {name!r} : [a-z0-9-], 2 à 41 caractères")
    meta = {"name": name, "description": description.strip(), "tools": [t for t in tools if isinstance(t, str)]}
    text = "---\n" + _yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000) + "---\n" + body.strip() + "\n"
    target = USER_DIR / name / "SKILL.md"
    if len(text.encode()) > MAX_BYTES:
        raise ValueError(f"skill trop gros (max {MAX_BYTES} octets)")
    skill = parse_skill_text(text.encode(), False, str(target))  # valide AVANT de toucher au disque
    created = not target.parent.exists()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)  # atomique : jamais de fichier à moitié écrit
    except BaseException:
        for cleanup in (lambda: os.unlink(tmp), lambda: target.parent.rmdir() if created else None):
            try:
                cleanup()
            except OSError:
                pass
        raise
    return skill


def delete_user_skill(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise ValueError("nom invalide")
    d = USER_DIR / name
    f = d / "SKILL.md"
    if not f.is_file() or f.is_symlink() or d.is_symlink():
        raise FileNotFoundError(name)
    f.unlink()
    try:
        d.rmdir()  # seulement s'il est vide : on ne supprime jamais de fichiers que l'utilisateur y aurait ajoutés
    except OSError:
        pass


def skills_index(skills: dict[str, Skill] | None = None) -> str:
    skills = load_skills()[0] if skills is None else skills
    return "\n".join(f"- {s.name} : {s.description}" for s in skills.values())


@tool("use_skill")
async def use_skill(name: str, precisions: str = "") -> str:
    """Charge les instructions d'un skill (procédure prédéfinie de l'utilisateur) ; tu dois ensuite les SUIVRE. L'index des skills
    disponibles est dans ton prompt. `precisions` : ce que l'utilisateur a ajouté (sous-dossier, périmètre…)."""
    skills, _ = load_skills()
    skill = skills.get(str(name).strip())
    if skill is None:
        raise ToolException(f"Skill inconnu : {name!r}. Disponibles : {', '.join(skills) or 'aucun'}.")
    return (f"# Skill « {skill.name} »" + (f" — précisions de l'utilisateur : {precisions}" if precisions else "")
            + "\n(Procédure de l'utilisateur : suis-la ; en cas de doute, demande.)\n\n" + skill.body)
