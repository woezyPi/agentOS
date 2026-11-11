"""Catalogues de conversation du fournisseur configuré et d’OpenRouter."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
import time

import httpx

from claude_clone.config import Settings

_CACHE: dict = {"t": 0.0, "key": None, "data": []}
_OPENROUTER_CACHE: dict = {"t": 0.0, "data": []}
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
TTL = 300.0
NOT_CHAT = re.compile(r"embed|rerank|bge-|e5-|whisper|tts|clip", re.I)  # embeddings, rerankers, audio : pas des modèles de conversation


async def list_available(settings: Settings, force: bool = False) -> tuple[list[dict], str | None]:
    """([{id, window}], erreur). Mis en cache 5 min ; une panne de la passerelle renvoie la dernière liste connue + l'erreur."""
    key = (settings.supervisor_base_url, settings.supervisor_api_key[:6])
    if not force and _CACHE["key"] == key and time.monotonic() - _CACHE["t"] < TTL:
        return _CACHE["data"], None
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=True) as c:
            r = await c.get(settings.supervisor_base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {settings.supervisor_api_key}"})
        r.raise_for_status()
        rows = normalize_catalog(r.json().get("data", []))
        if settings.managed_gateway:
            from claude_clone import gateway as GW
            routes = {route["alias"]: route for route in GW.read_config()["routes"]}
            for row in rows:
                if row["id"] in routes:
                    route = routes[row["id"]]
                    row["name"] = row["id"] + " — " + GW.PROVIDERS[route["provider"]] + " · " + route["model"]
                    row["provider"] = route["provider"]
    except Exception as exc:  # jamais la clé dans le message
        return (_CACHE["data"] if _CACHE["key"] == key else []), f"Passerelle injoignable ({type(exc).__name__})"
    rows.sort(key=lambda m: m["id"].lower())
    _CACHE.update(t=time.monotonic(), key=key, data=rows)
    return rows, None


def normalize_catalog(data: list) -> list[dict]:
    """Unify OpenAI-compatible and OpenRouter metadata; unknown capabilities stay unknown."""
    rows = []
    for model in data:
        if not isinstance(model, dict) or not isinstance(model.get("id"), str):
            continue
        ident = model["id"]
        architecture = model.get("architecture") or {}
        outputs = architecture.get("output_modalities")
        inputs = architecture.get("input_modalities")
        if NOT_CHAT.search(ident) or (outputs is not None and "text" not in outputs) or (inputs is not None and "text" not in inputs):
            continue
        pricing = model.get("pricing") or {}
        free = None
        if "prompt" in pricing and "completion" in pricing:
            try:
                free = all(Decimal(str(pricing[k])).is_zero() for k in ("prompt", "completion"))
            except (InvalidOperation, ValueError, TypeError):
                pass
        parameters = model.get("supported_parameters")
        rows.append({"id": ident, "name": model.get("name") or ident,
                     "provider": ident.split("/", 1)[0] if "/" in ident else "Autres",
                     "window": model.get("context_length") or model.get("max_model_len"),
                     "tools": "tools" in parameters if isinstance(parameters, list) else None,
                     "free": free})
    return sorted(rows, key=lambda m: m["id"].lower())


async def list_openrouter(force: bool = False) -> tuple[list[dict], str | None]:
    """Read the public catalogue without forwarding any configured provider credentials."""
    if not force and _OPENROUTER_CACHE["t"] and time.monotonic() - _OPENROUTER_CACHE["t"] < TTL:
        return _OPENROUTER_CACHE["data"], None
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=True) as client:
            response = await client.get(OPENROUTER_MODELS_URL)
        response.raise_for_status()
        rows = normalize_catalog(response.json().get("data", []))
    except Exception as exc:
        return _OPENROUTER_CACHE["data"], f"Catalogue OpenRouter indisponible ({type(exc).__name__})"
    _OPENROUTER_CACHE.update(t=time.monotonic(), data=rows)
    return rows, None
