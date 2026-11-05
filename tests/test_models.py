"""Test de connectivité : appelle chaque modèle configuré un par un (prompt minimal).

Usage :
    .venv/bin/python tests/test_models.py            # tous les modèles
    .venv/bin/python tests/test_models.py --only supervisor
    .venv/bin/python tests/test_models.py --timeout 30

Lit la configuration via claude_clone.config (env > ~/.claude_clone/.env > ~/.hermes/.env).
N'affiche JAMAIS les clés API. Sortie : un tableau succès/échec + latence par modèle.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_clone.config import load_settings  # noqa: E402
from claude_clone.graph import make_llm  # noqa: E402

PROMPT = "Réponds uniquement par le mot « OK »."


def collect_models(s):
    """Liste (label, base_url, api_key, model) de tous les modèles à tester, sans doublons."""
    seen = set()
    out = []
    for label, base_url, api_key, model in [
        ("superviseur", s.supervisor_base_url, s.supervisor_api_key, s.supervisor_model),
        ("codeur", s.coder_base_url, s.coder_api_key, s.coder_model),
        ("recherche", s.supervisor_base_url, s.supervisor_api_key, s.research_model),
        *[(f"agent:{role}", s.supervisor_base_url, s.supervisor_api_key, m)
          for role, m in s.agent_models.items()],
        *[(f"repli:superviseur:{m}", s.supervisor_base_url, s.supervisor_api_key, m) for m in s.supervisor_fallbacks],
        *[(f"repli:codeur:{m}", s.coder_base_url, s.coder_api_key, m) for m in s.coder_fallbacks],
        *[(f"repli:recherche:{m}", s.supervisor_base_url, s.supervisor_api_key, m) for m in s.research_fallbacks],
        *[(f"repli:agent:{m}", s.supervisor_base_url, s.supervisor_api_key, m) for m in s.agent_fallbacks],
    ]:
        key = (base_url, model)
        if key in seen:
            continue
        seen.add(key)
        out.append((label, base_url, api_key, model))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="ne tester que les modèles dont le label contient cette chaîne")
    ap.add_argument("--timeout", type=int, default=60, help="timeout par appel (s)")
    args = ap.parse_args()

    s = load_settings()
    if s.placeholder_fields():
        print(f"[ERREUR] Clé API manquante/placeholder : {', '.join(s.placeholder_fields())}")
        print("        Renseigne LLM_API_KEY (ou CEGEDIM_LLM_API_KEY) dans l'env, ~/.claude_clone/.env ou ~/.hermes/.env")
        return 2

    models = collect_models(s)
    if args.only:
        models = [m for m in models if args.only.lower() in m[0].lower()]
    if not models:
        print("Aucun modèle à tester (filtre trop restrictif ?).")
        return 1

    print(f"Test de {len(models)} modèle(s) sur {s.supervisor_base_url}\n")
    failures = 0
    for label, base_url, api_key, model in models:
        llm = make_llm(base_url, api_key, model, temperature=0.0, role="test", max_retries=1)
        t0 = time.monotonic()
        try:
            resp = llm.invoke([{"role": "user", "content": PROMPT}])
            dt = time.monotonic() - t0
            text = (resp.content if isinstance(resp.content, str) else str(resp.content)).strip().replace("\n", " ")
            print(f"  OK   {label:<22} {model:<42} {dt:5.1f}s  → {text[:60]!r}")
        except Exception as exc:
            dt = time.monotonic() - t0
            failures += 1
            print(f"  ÉCHEC {label:<22} {model:<42} {dt:5.1f}s  → {type(exc).__name__}: {str(exc)[:120]}")
    print(f"\n{len(models) - failures}/{len(models)} modèles répondent.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
