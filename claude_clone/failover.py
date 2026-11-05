"""Failover entre modèles du cluster, avec circuit breaker.

ModelPool essaie les modèles dans l'ordre de préférence. Un modèle qui échoue FAILURE_THRESHOLD fois
d'affilée est « ouvert » (ignoré) pendant COOLDOWN secondes, pour ne pas payer un timeout à chaque appel.
On ne bascule que sur les pannes d'infrastructure (connexion, timeout, 429, 5xx, modèle absent) : une
erreur 400 (requête invalide) échouerait de la même façon partout et est relancée telle quelle.
"""
from __future__ import annotations

import re
import time
from typing import Any

import httpx
import openai

# Familles où `enable_thinking=false` est honoré (mesuré). Pour GLM, aucun réglage ne coupe la réflexion et certains font FUITER le
# raisonnement dans le texte de la réponse : on ne lui envoie donc jamais ce paramètre.
NO_THINK_FAMILIES = re.compile(r"deepseek|qwen", re.I)
NO_THINK_BODY = {"chat_template_kwargs": {"enable_thinking": False}}
FAILURE_THRESHOLD = 2
COOLDOWN = 60.0


def switchable(exc: BaseException) -> bool:
    """Vrai si l'erreur justifie d'essayer un autre modèle."""
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError,
                        openai.InternalServerError, httpx.TransportError)):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code in (404, 408, 502, 503, 504)


class ModelPool:
    def __init__(self, candidates: list[tuple[str, Any]], threshold: int = FAILURE_THRESHOLD, cooldown: float = COOLDOWN):
        assert candidates, "au moins un modèle"
        self.candidates, self.threshold, self.cooldown = candidates, threshold, cooldown
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._shared = {"notice": None, "last": None}  # partagé avec les copies créées par bind_tools

    def bind_tools(self, tools: list) -> "ModelPool":
        pool = ModelPool([(n, llm.bind_tools(tools)) for n, llm in self.candidates], self.threshold, self.cooldown)
        pool._fails, pool._open_until, pool._shared = self._fails, self._open_until, self._shared  # un seul disjoncteur/notice par modèle
        return pool

    @property
    def last_used(self) -> str | None:
        """Nom du modèle qui a réellement répondu au dernier appel (après d'éventuelles bascules)."""
        return self._shared["last"]

    def pop_notice(self) -> str | None:
        notice, self._shared["notice"] = self._shared["notice"], None
        return notice

    def _available(self) -> list[tuple[str, Any]]:
        now = time.monotonic()
        live = [c for c in self.candidates if self._open_until.get(c[0], 0) <= now]
        return live or list(self.candidates)  # tout est ouvert : on retente quand même tout

    async def ainvoke(self, messages, *, thinking: bool | None = None, **kwargs):
        """`thinking=False` coupe la réflexion pour les modèles qui le supportent (par candidat : un repli GLM n'est pas touché)."""
        last: BaseException | None = None
        for rank, (name, llm) in enumerate(self._available()):
            try:
                kw = {**kwargs, "extra_body": NO_THINK_BODY} if thinking is False and NO_THINK_FAMILIES.search(name) else kwargs
                result = await llm.ainvoke(messages, **kw)
            except Exception as exc:
                if not switchable(exc):
                    raise
                last = exc
                self._fails[name] = self._fails.get(name, 0) + 1
                if self._fails[name] >= self.threshold:
                    self._open_until[name] = time.monotonic() + self.cooldown
                continue
            self._fails[name] = 0
            self._shared["last"] = name
            self._open_until.pop(name, None)
            if rank > 0 or name != self.candidates[0][0]:
                self._shared["notice"] = f"↪ basculé sur {name}" + (f" ({type(last).__name__} sur le modèle préféré)" if last else " (modèle préféré indisponible)")
            return result
        assert last is not None
        raise last
