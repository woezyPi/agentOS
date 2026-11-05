"""Modèles réellement disponibles sur la passerelle (GET <base>/models), pour que l'interface ne propose que des choix valides."""
from __future__ import annotations

import re
import time

import httpx

from claude_clone.config import Settings

_CACHE: dict = {"t": 0.0, "key": None, "data": []}
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
        rows = [{"id": m["id"], "window": m.get("max_model_len")} for m in r.json().get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str) and not NOT_CHAT.search(m["id"])]
    except Exception as exc:  # jamais la clé dans le message
        return (_CACHE["data"] if _CACHE["key"] == key else []), f"Passerelle injoignable ({type(exc).__name__})"
    rows.sort(key=lambda m: m["id"].lower())
    _CACHE.update(t=time.monotonic(), key=key, data=rows)
    return rows, None
