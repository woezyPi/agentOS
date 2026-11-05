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
| `CC_THINKING` | `auto` (défaut) : réflexion coupée sauf architecte/challenger/reviewer · `off` : jamais (le plus rapide) · `on` : comportement natif des modèles. Effectif sur DeepSeek et Qwen ; GLM ne peut pas être coupé |
| `CC_MAX_STEPS` | `0` = **aucune limite** (défaut) ; `N` : point d'étape après N tours d'outils (bouton CONTINUER) |
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

## Interface web
```bash
cc-web            # serveur local sur 127.0.0.1:8765 ; ouvre l'URL affichée (elle contient le jeton)
```
Front : `claude_clone/static/index.html` (flux temps réel, plan, rapports d'agents, **confirmations avec diff**, choix de l'agent,
posture challenge/direct, mode auto). Sécurité : écoute locale seulement, jeton par démarrage dans le fragment de l'URL,
Host/Origin vérifiés, CSP stricte, le client n'envoie que des décisions (le serveur relit ce qui est réellement en attente).

## Agents
Le **superviseur** coordonne ; il appelle des sous-agents via `ask_agent(role, task, context)` (parallèles, qui ne voient
pas la conversation) ou `research` :

| Rôle | Permissions (imposées par le code) | Modèle par défaut |
|---|---|---|
| `researcher` | web en lecture (PyPI, npm, GitHub…), garde SSRF, lecture du projet | GLM-5.3-Flash |
| `architect` | lecture du projet | GLM-5.3-Flash |
| `developer` | **copie jetable** du projet (sans `.env`, clés, `.git`), terminal isolé sans réseau ; renvoie un jeu de modifications | Mistral Small 119B |
| `reviewer` | lecture + `git diff`, contexte indépendant, autre famille de modèle | Qwen3.6-27B |
| `devops` | diagnostic en lecture seule (liste blanche, sans redirection ni réseau) ; **propose** les actions | Qwen3.6-27B |
| `data` | SQLite en lecture seule (`mode=ro` + autorisateur), outils MCP déclarés `allow` | Qwen3.6-27B |
| `writer` | lecture ; rend des documents numérotés, écrits avec `write_file(coder_block=N)` | Mistral Small 119B |

Flux type : `architect` → `developer` → `reviewer` (sur le jeu `#N`) → `apply_dev_changes(N)` (diff + confirmation, refus en cas
de conflit ou de fichier sensible). Modèles : `<ROLE>_MODEL`, replis : `AGENT_FALLBACKS`.

## Skills et agents personnalisés
**Par l'interface** : bouton « Skills & agents » (barre latérale) → créer, modifier, supprimer. Pris en compte immédiatement.
**Par fichiers** (équivalent) :
- Skill : `~/.claude_clone/skills/<nom>/SKILL.md` (en-tête `name`, `description`, `tools` + instructions Markdown). Un skill du même
  nom qu'un skill intégré le remplace ; le supprimer restaure l'intégré.
- Agent : `~/.claude_clone/agents/<clé>.md` (en-tête `key`, `title`, `description`, `model`, `tools`, `max_steps`, `temperature` + prompt).
  **Outils limités à une liste en lecture seule** (`read_file`, `grep`, `glob`, `git_diff`, `fetch_url`, `latest_version`, `sql_describe`,
  `sql_query`, `run_check`) : un agent personnalisé ne peut ni écrire ni exécuter. Les clés des agents intégrés sont réservées.
Jamais chargés depuis le dossier du projet (un dépôt tiers ne doit pas pouvoir injecter d'instructions ni d'agents).

## Sécurité en bref
Fichiers confinés au dossier de travail ; bash isolé par namespaces (`$HOME` masqué) et sans réseau en mode auto ;
confirmation humaine avant toute écriture/commande. Ce n'est pas un sandbox complet : relisez les commandes.
