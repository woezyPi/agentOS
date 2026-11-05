"""Configuration centrale : endpoints, clés API, noms de modèles, limites.

Tout est surchargeable par variables d'environnement, ce qui évite de mettre
une clé dans le code. Seule la clé API n'a pas de défaut : le CLI refuse de
démarrer tant qu'elle est introuvable.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# --- Valeurs par défaut (surchargeables, voir load_settings) ------------------
# Passerelle AI Dashboard Cegedim (clé nominative : https://ai-dashboard.cegedim.cloud/api-keys).
# Le domaine *.cegedim.cloud doit être dans no_proxy, sinon 407 du proxy McAfee.
DEFAULT_URL = "https://llm.cegedim.cloud/v1"
PLACEHOLDER_KEY = "REMPLACER_PAR_VOTRE_CLE_API"
# Identifiants vérifiés via GET /v1/models le 2026-11-05.
DEFAULT_SUPERVISOR_MODEL = "deepseek-ai/deepseek-v4-flash-0731"
DEFAULT_CODER_MODEL = "mistralai/mistral-small-4-119b-2603"
# Modèles de repli (même passerelle), tous vérifiés avec appel d'outils le 2026-11-05. "none" désactive.
DEFAULT_SUPERVISOR_FALLBACKS = "zai-org/GLM-5.3-Flash,Qwen/Qwen3.6-27B-FP8"
DEFAULT_CODER_FALLBACKS = "Qwen/Qwen3.6-27B-FP8,zai-org/GLM-5.3-Flash"
# Sous-agents de recherche : modèle rapide à grand contexte (GLM-5.3-Flash : 1M).
DEFAULT_RESEARCH_MODEL = "zai-org/GLM-5.3-Flash"
DEFAULT_RESEARCH_FALLBACKS = "Qwen/Qwen3.6-27B-FP8"
# Modèles par rôle (famille différente du codeur pour le reviewer : angles morts moins corrélés). Surcharge : <ROLE>_MODEL.
ROLE_DEFAULT_MODELS = {
    "architect": "zai-org/GLM-5.3-Flash",
    "developer": DEFAULT_CODER_MODEL,
    "reviewer": "Qwen/Qwen3.6-27B-FP8",
    "challenger": "Qwen/Qwen3.6-27B-FP8",
    "devops": "Qwen/Qwen3.6-27B-FP8",
    "data": "Qwen/Qwen3.6-27B-FP8",
    "writer": DEFAULT_CODER_MODEL,
}
# Panel de revue : 3 familles DIFFÉRENTES entre elles et du développeur (Mistral). "none" désactive.
# Workers temporaires (spawn_workers) : un modèle RAPIDE, réflexion coupée (voir thinking_for). Mesuré : DeepSeek flash sans réflexion ≈ 1 s.
DEFAULT_WORKER_MODEL = "deepseek-ai/deepseek-v4-flash-0731"
DEFAULT_PANEL_MODELS = "deepseek-ai/deepseek-v4-flash-0731,Qwen/Qwen3.6-27B-FP8,zai-org/GLM-5.3-Flash"
DEFAULT_AGENT_FALLBACKS = "deepseek-ai/deepseek-v4-flash-0731,zai-org/GLM-5.3-Flash,Qwen/Qwen3.6-27B-FP8"

# --- Réflexion (« thinking ») des modèles --------------------------------------------------------------------------
# Mesuré sur la passerelle (2026-11-05, même prompt de 25 mots) : Qwen3.6 41,8 s / 3 079 tokens de réflexion cachée contre 0,6 s sans ;
# DeepSeek flash 12,7 s contre 1,0 s. C'est la première cause de lenteur, bien avant le manque de parallélisme.
# CC_THINKING : "auto" (défaut : réflexion gardée là où elle sert), "off" (jamais : le plus rapide), "on" (comportement natif des modèles).
THINKING = os.getenv("CC_THINKING", "auto").strip().lower()
THINKING_ROLES = frozenset({"architect", "challenger", "reviewer"})  # leur métier est de réfléchir : on garde la réflexion


def thinking_for(role: str, first_step: bool = False, challenge: bool = False) -> bool | None:
    """None = comportement par défaut du modèle (il réfléchit) ; False = on coupe la réflexion (effective sur DeepSeek et Qwen ;
    ignorée pour GLM et Mistral, voir failover.NO_THINK_FAMILIES)."""
    if THINKING == "on":
        return None
    if THINKING == "off":
        return False
    if role in THINKING_ROLES:
        return None
    return False


# --- Limites de sécurité / robustesse ----------------------------------------
MAX_RETRIES = 3            # réponses LLM invalides consécutives avant abandon
MAX_STEPS_PER_TURN = int(os.getenv("CC_MAX_STEPS", "0"))  # 0 = AUCUNE limite (défaut). N > 0 : point d'étape après N tours d'outils
RECURSION_LIMIT = int(os.getenv("CC_RECURSION_LIMIT", "1000"))  # garde-fou anti boucle infinie uniquement (pas une limite de travail)       # garde-fou LangGraph contre les boucles infinies
BASH_DEFAULT_TIMEOUT = 60  # secondes
BASH_MAX_TIMEOUT = 600
MAX_OUTPUT_CHARS = 20_000  # troncature des sorties renvoyées au LLM
MAX_READ_BYTES = 200_000
CONTEXT_COMPACT_TOKENS = 80_000  # au-delà (estimation), l'historique ancien est résumé
CONTEXT_KEEP_TOKENS = 20_000     # taille approximative de la fin de conversation conservée telle quelle


# --- Lecture des secrets ------------------------------------------------------
# On ne lit QUE ces noms, jamais tout le fichier, et on ne les exporte pas dans
# os.environ (sinon les commandes bash lancées par le LLM les hériteraient).
_WANTED = {
    "LLM_BASE_URL", "LLM_API_KEY", "CEGEDIM_LLM_API_KEY",
    "SUPERVISOR_BASE_URL", "SUPERVISOR_API_KEY", "SUPERVISOR_MODEL",
    "CODER_BASE_URL", "CODER_API_KEY", "CODER_MODEL", "SUPERVISOR_FALLBACKS", "CODER_FALLBACKS",
    "RESEARCH_MODEL", "RESEARCH_FALLBACKS", "AGENT_FALLBACKS", "PANEL_MODELS", "WORKER_MODEL",
    "ARCHITECT_MODEL", "DEVELOPER_MODEL", "REVIEWER_MODEL", "CHALLENGER_MODEL", "DEVOPS_MODEL", "DATA_MODEL", "WRITER_MODEL",
}
# Ordre de priorité : variables d'environnement > ~/.claude_clone/.env > ~/.hermes/.env
# Volontairement PAS de .env dans le dossier de travail : un dépôt tiers pourrait y mettre un LLM_BASE_URL
# malveillant et récupérer votre clé API.
_DOTENV_FILES = (Path.home() / ".claude_clone" / ".env", Path.home() / ".hermes" / ".env")


def _parse_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if key not in _WANTED:
            continue
        val = val.strip()
        if val[:1] in ("'", '"') and val.endswith(val[0]) and len(val) >= 2:
            val = val[1:-1]
        else:
            val = val.split(" #", 1)[0].strip()
        if val:
            out[key] = val
    return out


def _lookup(name: str, files: list[dict[str, str]]) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    for values in files:
        if name in values:
            return values[name]
    return None


@dataclass(frozen=True)
class Settings:
    supervisor_base_url: str
    supervisor_api_key: str
    supervisor_model: str
    coder_base_url: str
    coder_api_key: str
    coder_model: str
    supervisor_fallbacks: tuple[str, ...] = ()
    coder_fallbacks: tuple[str, ...] = ()
    research_model: str = DEFAULT_RESEARCH_MODEL
    research_fallbacks: tuple[str, ...] = ()
    agent_models: dict = field(default_factory=lambda: dict(ROLE_DEFAULT_MODELS))
    agent_fallbacks: tuple[str, ...] = ()
    panel_models: tuple[str, ...] = ()
    worker_model: str = DEFAULT_WORKER_MODEL

    def placeholder_fields(self) -> list[str]:
        """Liste les champs encore à leur valeur placeholder."""
        bad = []
        for name in ("supervisor_api_key", "coder_api_key"):
            if getattr(self, name) == PLACEHOLDER_KEY:
                bad.append(name)
        return bad


def _models(value: str) -> tuple[str, ...]:
    return () if value.strip().lower() in ("none", "off", "-") else tuple(m.strip() for m in value.split(",") if m.strip())


# --- Modèles choisis dans l'interface : ~/.claude_clone/models.json (la variable d'environnement reste prioritaire) ----------------
MODELS_FILE = Path.home() / ".claude_clone" / "models.json"
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/\-]{0,99}$")
# clé de l'interface → variable d'environnement qui, si elle existe, l'emporte
ENV_OF = {"supervisor": "SUPERVISOR_MODEL", "coder": "CODER_MODEL", "researcher": "RESEARCH_MODEL", "worker": "WORKER_MODEL", **{r: f"{r.upper()}_MODEL" for r in ROLE_DEFAULT_MODELS}}
ASSIGNABLE = tuple(ENV_OF)


def read_model_overrides() -> dict:
    """{clé: modèle, "panel": [modèles]} ; tout ce qui est invalide est ignoré (le fichier ne peut jamais casser le démarrage)."""
    try:
        raw = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict = {}
    if isinstance(raw, dict):
        for k in ASSIGNABLE:
            if isinstance(raw.get(k), str) and MODEL_ID.match(raw[k]):
                out[k] = raw[k]
        if isinstance(raw.get("panel"), list):
            panel = [m for m in raw["panel"] if isinstance(m, str) and MODEL_ID.match(m)]
            if 2 <= len(panel) <= 4:
                out["panel"] = panel
    return out


def write_model_overrides(overrides: dict) -> None:
    MODELS_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = MODELS_FILE.with_name(MODELS_FILE.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(overrides, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, MODELS_FILE)  # atomique


def model_sources() -> dict[str, str]:
    """{clé: "env" | "modifié" | "défaut"} : d'où vient le modèle effectif de chaque rôle."""
    files = [_parse_dotenv(p) for p in _DOTENV_FILES]
    ov = read_model_overrides()
    src = {k: ("env" if _lookup(ENV_OF[k], files) else "modifié" if k in ov else "défaut") for k in ASSIGNABLE}
    src["panel"] = "env" if _lookup("PANEL_MODELS", files) else "modifié" if "panel" in ov else "défaut"
    return src


def load_settings() -> Settings:
    """Construit les réglages (env > ~/.claude_clone/.env > ~/.hermes/.env).

    LLM_BASE_URL / LLM_API_KEY (ou CEGEDIM_LLM_API_KEY) servent de valeurs communes ;
    chaque modèle peut avoir son propre endpoint/clé via SUPERVISOR_* / CODER_*.
    """
    files = [_parse_dotenv(p) for p in _DOTENV_FILES]
    ov = read_model_overrides()
    get = lambda name, default=None: _lookup(name, files) or default
    base_url = get("LLM_BASE_URL", DEFAULT_URL)
    api_key = get("LLM_API_KEY") or get("CEGEDIM_LLM_API_KEY") or PLACEHOLDER_KEY
    return Settings(
        supervisor_base_url=get("SUPERVISOR_BASE_URL", base_url),
        supervisor_api_key=get("SUPERVISOR_API_KEY", api_key),
        supervisor_model=get("SUPERVISOR_MODEL", ov.get("supervisor", DEFAULT_SUPERVISOR_MODEL)),
        coder_base_url=get("CODER_BASE_URL", base_url),
        coder_api_key=get("CODER_API_KEY", api_key),
        coder_model=get("CODER_MODEL", ov.get("coder", DEFAULT_CODER_MODEL)),
        supervisor_fallbacks=_models(get("SUPERVISOR_FALLBACKS", DEFAULT_SUPERVISOR_FALLBACKS)),
        coder_fallbacks=_models(get("CODER_FALLBACKS", DEFAULT_CODER_FALLBACKS)),
        research_model=get("RESEARCH_MODEL", ov.get("researcher", DEFAULT_RESEARCH_MODEL)),
        research_fallbacks=_models(get("RESEARCH_FALLBACKS", DEFAULT_RESEARCH_FALLBACKS)),
        agent_models={role: get(f"{role.upper()}_MODEL", ov.get(role, model)) for role, model in ROLE_DEFAULT_MODELS.items()},
        agent_fallbacks=_models(get("AGENT_FALLBACKS", DEFAULT_AGENT_FALLBACKS)),
        worker_model=get("WORKER_MODEL", ov.get("worker", DEFAULT_WORKER_MODEL)),
        panel_models=_models(get("PANEL_MODELS", ",".join(ov["panel"]) if "panel" in ov else DEFAULT_PANEL_MODELS))[:4],
    )
