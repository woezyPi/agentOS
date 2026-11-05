# claude-clone

Assistant de code en ligne de commande : un **superviseur** (outils locaux, relecture, plan) qui délègue
la rédaction de code à un **agent codeur**. LangGraph + modèles compatibles OpenAI (passerelle Cegedim par défaut).

## Installation
```bash
cd ~/claude-clone
python3 -m venv .venv && .venv/bin/pip install -e .
```
Commande : `.venv/bin/cc` (ou un lien/alias `cc`). Tests hors-ligne : `.venv/bin/python tests/test_smoke.py`.

## Configuration (priorité : variables d'environnement > `~/.claude_clone/.env` > `~/.hermes/.env`)
| Variable | Défaut |
|---|---|
| `LLM_API_KEY` ou `CEGEDIM_LLM_API_KEY` | — (obligatoire) |
| `LLM_BASE_URL` | `https://llm.cegedim.cloud/v1` |
| `SUPERVISOR_MODEL` / `CODER_MODEL` | `deepseek-ai/deepseek-v4-flash-0731` / `mistralai/mistral-small-4-119b-2603` |
| `SUPERVISOR_FALLBACKS` / `CODER_FALLBACKS` | modèles de repli, séparés par des virgules ; `none` désactive |
| `BASH_SANDBOX` / `BASH_SANDBOX_NET` | `auto` / `1` (`off` ; `0` coupe le réseau de bash) |

Le domaine `cegedim.cloud` doit figurer dans `no_proxy`. Pas de `.env` dans le dossier de travail : voulu (sécurité).

## Utilisation
```bash
cc                 # nouvelle session dans le dossier courant
cc --resume        # reprend la dernière session de ce dossier
cc --auto          # mode auto : actions sûres approuvées sans question (voir claude_clone/autopilot.py)
```
Commandes : `/help` `/auto` `/stats` `/reset` `/exit`. Confirmation `Y/n/a` pour chaque action sensible.
Mémoire projet : `AGENTS.md` (ou `CLAUDE.md`) du dossier, et `~/.claude_clone/AGENTS.md` pour vos préférences globales.

## Sécurité en bref
Fichiers confinés au dossier de travail ; bash isolé par namespaces (`$HOME` masqué) et sans réseau en mode auto ;
confirmation humaine avant toute écriture/commande. Ce n'est pas un sandbox complet : relisez les commandes.
