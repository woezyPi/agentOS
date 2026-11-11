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
Front modulaire issu de `agentos_release` : `claude_clone/static/index.html`, `static/css/{base,agentos}.css` et `static/js/app.js`,
servis sur `/static` par le même serveur et inclus dans le paquet Python (aucun build frontend requis).
Flux temps réel, inspecteur des workers (activité, logs, fichiers, performances), plan, rapports d'agents, **confirmations avec diff**, choix de l'agent,
posture challenge/direct, mode auto. Le bouton « MODE JOUR » permet de basculer entre les thèmes clair et sombre ; le choix est conservé dans le navigateur.
**Modèles** dans la barre latérale : parcourir le catalogue du fournisseur configuré ou le catalogue public OpenRouter, filtrer par nom, famille, gratuité, appels d’outils et contexte minimum, puis affecter un modèle au rôle choisi. On peut aussi saisir un identifiant complet (`fournisseur/modele`) pour chaque agent et le panel de revue. Les choix sont enregistrés dans `~/.claude_clone/models.json`, prioritaires sur les variables de modèles, et appliqués aux prochains tours sans redémarrage. « Réinitialiser tout » reprend les modèles de l’environnement ou les valeurs par défaut. Un identifiant absent du catalogue est accepté ; sa disponibilité est vérifiée par le fournisseur lors de l’appel. Le catalogue public OpenRouter ne change pas le fournisseur utilisé pour les appels. L’URL du fournisseur et la clé API restent configurées dans l’environnement ou `~/.claude_clone/.env`.

Sécurité : écoute locale seulement, jeton par démarrage dans le fragment de l'URL,
Host/Origin vérifiés, CSP stricte, le client n'envoie que des décisions (le serveur relit ce qui est réellement en attente).

Recette navigateur hors ligne : `.venv/bin/pip install -e '.[dev]'`, puis
`.venv/bin/python -m playwright install chromium` et `.venv/bin/python tests/test_browser.py`.

## Clés API et Git

Les clés saisies dans **Connexions** sont stockées dans `~/.claude_clone/gateway/credentials.json`, hors du dépôt, avec des permissions 600. Les clés de connexion directe restent dans les variables d’environnement ou `~/.claude_clone/.env`. Ces fichiers personnels ne sont pas inclus dans un push du projet.

Le `.gitignore` exclut aussi les fichiers locaux `.env.*`, `credentials*.json`, `api key*`, `api_key*`, ainsi que les fichiers `.key`, `.pem` et `.p12`. Le fichier `.env.example` reste versionné avec des valeurs vides ou fictives. Les exclusions empêchent l’ajout de fichiers non suivis ; elles ne retirent pas un secret qui aurait déjà été commité.

Le modèle configuré est visible dans l’en-tête et suit l’agent sélectionné. Avec LiteLLM, AgentOS affiche le fournisseur et le modèle derrière l’alias. Après un appel, le compte rendu utilise le nom renvoyé par le fournisseur, lorsqu’il est disponible, pour refléter les éventuels secours.

## Passerelle LiteLLM locale

La connexion directe reste disponible. Pour reprendre le fonctionnement `AgentOS → alias → LiteLLM → fournisseur`, installe la passerelle dans son environnement Python séparé :

```bash
cc-gateway --install
cc-web
```

L’installation utilise `uv` et Python 3.12, sans modifier les dépendances d’AgentOS. Sans clé existante, `cc-web` ouvre la configuration des connexions ; les demandes sont bloquées tant qu’aucun fournisseur n’est configuré. Dans **Connexions**, renseigne les clés et les modèles de chaque route, puis clique sur **Enregistrer et activer LiteLLM**. Les clés déjà configurées pour un fournisseur sont réutilisées ; les champs vides conservent la clé existante. Aucune clé n’est renvoyée au navigateur. Les nouvelles clés sont conservées localement dans `~/.claude_clone/gateway/credentials.json` (permissions 600).

| Alias appelé par AgentOS | Fournisseur initial | Modèle initial |
| --- | --- | --- |
| `superviseur` | DeepSeek | `deepseek-flash` |
| `worker` | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` |
| `worker_secours` | OpenRouter | `google/gemma-4-31b-it:free` |

Les modèles sont modifiables ; leur disponibilité dépend du fournisseur. Le secours de `worker` est initialement `worker_secours`. Deux variantes OpenRouter `:free` partagent leur quota quotidien : cette bascule traite une indisponibilité de modèle, pas un quota global épuisé. Pour un secours indépendant, choisis un autre fournisseur dans la route de secours. DeepSeek et tout autre modèle payant nécessitent une clé avec du crédit ; leur activation peut entraîner des frais.

Le proxy natif LiteLLM écoute sur `127.0.0.1:4000` avec une clé locale générée. Sa configuration YAML est produite dans `~/.claude_clone/gateway/config.yaml` ; elle référence les clés par variables d’environnement. Il est relancé avec AgentOS lorsque le mode LiteLLM est actif. L’interface **Modèles** propose alors les alias, pas les identifiants externes. Les appels, la recherche, les workers et le panel passent tous par la passerelle ; les retries et secours côté AgentOS sont désactivés pour éviter de les cumuler avec ceux du proxy.

**Revenir à la connexion directe** arrête le proxy et restaure les choix précédents. Les affectations en mode LiteLLM sont sauvegardées séparément dans `~/.claude_clone/gateway-models.json`. La configuration des routes n’est pas modifiable pendant une demande en cours.

**Budget strict 0 $** dans Connexions applique une liste fermée de quatre variantes OpenRouter `:free`, y compris au superviseur, au codeur et aux secours. Les rôles de code, recherche, rédaction et validation ont chacun leur alias. L’activation vérifie le catalogue public : modèles présents, tarifs déclarés nuls et prise en charge des outils. Si la vérification échoue, le mode ne démarre pas. Toute route payante, fournisseur différent ou identifiant hors liste est refusé côté serveur ; les anciens agents personnalisés ne peuvent pas imposer leur ancien modèle payant. Les clés des fournisseurs inutilisés ne sont pas transmises au proxy. La connexion directe est bloquée tant que ce mode est actif ; seul un changement explicite de la configuration peut le désactiver.

Ce contrôle porte sur les appels orchestrés par AgentOS et sa passerelle. Il ne configure pas les restrictions de la clé dans le compte OpenRouter et ne couvre pas les appels effectués ailleurs avec cette clé. Pour une protection au niveau du compte, ajoute aussi une liste de modèles autorisés et un plafond dans les guardrails OpenRouter. Les quotas gratuits continuent de s’appliquer.

Commandes de gestion : `cc-gateway --status`, `cc-gateway --stop`. En cas d’échec au démarrage, le journal privé est `~/.claude_clone/gateway/proxy.log`. Sur macOS/Linux, le processus géré est identifié par son chemin de configuration avant arrêt.

Validation sans appels payants : `.venv/bin/python tests/test_gateway.py`. Ce test démarre le vrai proxy LiteLLM devant un fournisseur local simulé, vérifie l’authentification, les alias, la bascule sur erreur et un appel d’outil AgentOS.

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
