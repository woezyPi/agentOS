"""Configuration centrale : endpoints, clés API, noms de modèles, limites.

Tout est surchargeable par variables d'environnement, ce qui évite de mettre
une clé dans le code. Seule la clé API n'a pas de défaut : le CLI refuse de
démarrer tant qu'elle est introuvable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
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

# --- Limites de sécurité / robustesse ----------------------------------------
MAX_RETRIES = 3            # réponses LLM invalides consécutives avant abandon
MAX_STEPS_PER_TURN = 12   # tours d'outils du superviseur par demande, puis il doit faire le point
RECURSION_LIMIT = 60       # garde-fou LangGraph contre les boucles infinies
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

    def placeholder_fields(self) -> list[str]:
        """Liste les champs encore à leur valeur placeholder."""
        bad = []
        for name in ("supervisor_api_key", "coder_api_key"):
            if getattr(self, name) == PLACEHOLDER_KEY:
                bad.append(name)
        return bad


def _models(value: str) -> tuple[str, ...]:
    return () if value.strip().lower() in ("none", "off", "-") else tuple(m.strip() for m in value.split(",") if m.strip())


def load_settings() -> Settings:
    """Construit les réglages (env > ~/.claude_clone/.env > ~/.hermes/.env).

    LLM_BASE_URL / LLM_API_KEY (ou CEGEDIM_LLM_API_KEY) servent de valeurs communes ;
    chaque modèle peut avoir son propre endpoint/clé via SUPERVISOR_* / CODER_*.
    """
    files = [_parse_dotenv(p) for p in _DOTENV_FILES]
    get = lambda name, default=None: _lookup(name, files) or default
    base_url = get("LLM_BASE_URL", DEFAULT_URL)
    api_key = get("LLM_API_KEY") or get("CEGEDIM_LLM_API_KEY") or PLACEHOLDER_KEY
    return Settings(
        supervisor_base_url=get("SUPERVISOR_BASE_URL", base_url),
        supervisor_api_key=get("SUPERVISOR_API_KEY", api_key),
        supervisor_model=get("SUPERVISOR_MODEL", DEFAULT_SUPERVISOR_MODEL),
        coder_base_url=get("CODER_BASE_URL", base_url),
        coder_api_key=get("CODER_API_KEY", api_key),
        coder_model=get("CODER_MODEL", DEFAULT_CODER_MODEL),
        supervisor_fallbacks=_models(get("SUPERVISOR_FALLBACKS", DEFAULT_SUPERVISOR_FALLBACKS)),
        coder_fallbacks=_models(get("CODER_FALLBACKS", DEFAULT_CODER_FALLBACKS)),
    )
