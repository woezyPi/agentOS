"""Diagnostic de connectivité : appelle chaque modèle configuré (préférence, replis, rôles) avec un prompt minimal.

Les clés ne sont JAMAIS affichées. C'est aussi la réponse au besoin « vérifie que mes modèles répondent » : le
superviseur n'a pas à fouiller $HOME avec bash (masqué par l'isolation, volontairement) pour y chercher des clés.
"""
from __future__ import annotations

import asyncio
import time

from langchain_core.tools import tool

from claude_clone.config import Settings


def configured_models(s: Settings) -> dict[str, list[str]]:
    """{modèle: [rôles qui l'utilisent]} dans l'ordre de première apparition."""
    out: dict[str, list[str]] = {}

    def add(model: str, role: str) -> None:
        if model and role not in out.setdefault(model, []):
            out[model].append(role)

    for m in (s.supervisor_model, *s.supervisor_fallbacks):
        add(m, "superviseur" if m == s.supervisor_model else "repli superviseur")
    for m in (s.coder_model, *s.coder_fallbacks):
        add(m, "codeur" if m == s.coder_model else "repli codeur")
    for m in (s.research_model, *s.research_fallbacks):
        add(m, "chercheur" if m == s.research_model else "repli chercheur")
    for role, m in s.agent_models.items():
        add(m, role)
    for m in s.agent_fallbacks:
        add(m, "repli agents")
    add(s.worker_model, "workers temporaires")
    for m in s.panel_models:
        add(m, "panel de revue")
    return out


async def check_models(s: Settings, timeout: float = 30.0) -> str:
    from claude_clone.graph import make_llm  # import tardif : graph importe ce module

    gate = asyncio.Semaphore(4)

    async def ping(model: str) -> tuple[bool, float, str]:
        async with gate:
            t0 = time.monotonic()
            try:
                llm = make_llm(s.supervisor_base_url, s.supervisor_api_key, model, 0, "diagnostic", max_retries=0)
                r = await asyncio.wait_for(llm.ainvoke("Réponds uniquement : OK"), timeout)
                text = str(getattr(r, "content", "")).strip()
                return True, time.monotonic() - t0, text[:30]
            except Exception as exc:  # jamais de clé dans le message : on ne garde que le type et un extrait court
                return False, time.monotonic() - t0, f"{type(exc).__name__}: {str(exc)[:90]}"

    models = configured_models(s)
    results = await asyncio.gather(*(ping(m) for m in models))
    lines = [f"{'✔' if ok else '✖'} {m} — {dt:.1f} s — {', '.join(models[m])}" + ("" if ok else f" — {info}") for m, (ok, dt, info) in zip(models, results)]
    bad = sum(not ok for ok, _, _ in results)
    return f"{len(models) - bad}/{len(models)} modèles répondent (endpoint {s.supervisor_base_url}, clé non affichée)\n" + "\n".join(lines)


def make_check_models_tool(settings: Settings):
    @tool("check_models")
    async def check_models_tool() -> str:
        """Vérifie que chaque modèle configuré (superviseur, codeur, rôles, replis) répond : appel minimal, succès/échec et
        latence par modèle. Les clés ne sont jamais affichées. À utiliser plutôt que de chercher des clés avec bash."""
        return await check_models(settings)

    return check_models_tool
