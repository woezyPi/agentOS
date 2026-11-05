"""Auto-description du système d'agents : ce que le superviseur lit quand on lui demande « présente-moi tes agents ».

Tout vient du code et de la config RÉELS (prompts, modèles, replis, outils, permissions, limites) : rien n'est écrit à la
main ici, donc la présentation ne peut pas dériver de la réalité. Les clés API ne sont jamais incluses.
"""
from __future__ import annotations

from langchain_core.tools import tool

from claude_clone.config import Settings

# Fenêtres de contexte relevées sur la passerelle (GET /v1/models, 2026-11-05) — indicatif.
CONTEXT_WINDOWS = {
    "deepseek-ai/deepseek-v4-flash-0731": 524_288, "zai-org/GLM-5.3-Flash": 1_048_576,
    "Qwen/Qwen3.6-27B-FP8": 262_144, "mistralai/mistral-small-4-119b-2603": 262_144,
}


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def _models(main: str, fallbacks, dedupe: bool = True) -> str:
    chain = [main, *[m for m in fallbacks if m != main or not dedupe]]
    return " → ".join(f"{m} ({CONTEXT_WINDOWS[m]:,} tokens)".replace(",", " ") if m in CONTEXT_WINDOWS else m for m in chain)


def describe_agents(settings: Settings, include_prompts: bool = True, only: str = "") -> str:
    from claude_clone import graph as G
    from claude_clone import tools as T
    from claude_clone.agents import COMMON, PERMISSIONS, get_roles, role_temperature
    from claude_clone.failover import COOLDOWN, FAILURE_THRESHOLD
    from claude_clone.prompts import MODE_CHALLENGE, MODE_DIRECT
    from claude_clone.research import RESEARCH_PROMPT

    def fence(text: str) -> str:
        return f"\n~~~\n{text.strip()}\n~~~\n" if include_prompts else ""

    sup_tools = list(T.LOCAL_TOOLS)
    extra = [T.LOCAL_TOOLS_BY_NAME[n] for n in ("check_models", "describe_agents") if n in T.LOCAL_TOOLS_BY_NAME]
    sup_tools += [*extra, T.delegate_to_coder, T.research, T.ask_agent, T.apply_dev_changes, *T.EXTRA_TOOLS]
    steps = "illimité" if G.MAX_STEPS_PER_TURN == 0 else f"{G.MAX_STEPS_PER_TURN} tours d'outils par demande"

    sections: dict[str, list[str]] = {}
    out: list[str] = []

    def section(key: str) -> None:
        """Ferme la section en cours : tout ce qui a été ajouté à `out` depuis le dernier appel lui appartient."""
        sections[key] = out[:]
        out.clear()

    out = [
        "# Système d'agents de claude-clone — configuration RÉELLE (lue dans le code)",
        f"Endpoint : {settings.supervisor_base_url} (clés non affichées). Chaque appel à un modèle passe par un pool avec REPLI : "
        f"ordre de préférence ci-dessous ; un modèle qui échoue {FAILURE_THRESHOLD} fois d'affilée (connexion, timeout, 429, 5xx, "
        f"modèle introuvable) est ignoré {int(COOLDOWN)} s (disjoncteur) ; une erreur 400 ne déclenche PAS de bascule. "
        f"Réflexion (« thinking ») : CC_THINKING={__import__('claude_clone.config', fromlist=['x']).THINKING} — par défaut coupée (DeepSeek, Qwen) sauf pour architecte, challenger et reviewer ; GLM ne peut pas être coupé. Mesuré : 10 à 70 × plus rapide sans réflexion sur les tâches simples.",
        f"Panel de revue (ask_agent(role=reviewer|challenger, panel=true)) : relecteurs indépendants {', '.join(settings.panel_models) or '(désactivé)'} en parallèle, sans repli entre eux (diversité de familles), puis fusion CONSENSUS / UN SEUL RELECTEUR / DÉSACCORDS ; tableau des verdicts calculé par le code.",
        "Profil de l'utilisateur, décisions antérieures (DECISIONS.md), plan et résumé de contexte sont injectés dynamiquement dans les prompts.",
        "",
    ]
    section("overview")
    out += [
        "## 1. Superviseur (coordinateur)",
        f"- Modèle puis replis : {_models(settings.supervisor_model, settings.supervisor_fallbacks, dedupe=False)}",
        f"- Température 0.2 · budget : {steps} · compaction du contexte au-delà de {G.CONTEXT_COMPACT_TOKENS:,} tokens".replace(",", " "),
        "- Outils : " + ", ".join(f"{t.name} ({_first_line(t.description)[:70]})" for t in sup_tools),
        "- Rôle : trie la demande, challenge les décisions structurantes, délègue, vérifie les rapports, assemble la réponse.",
        "- Confirmation humaine : execute_bash, write_file, edit_file, apply_dev_changes et tout outil MCP (sauf liste « allow »).",
        "### Prompt système du superviseur (gabarit ; {workdir} = dossier de travail)" + fence(G.SUPERVISOR_PROMPT),
        "### Posture « challenge » (ajoutée au prompt par défaut)" + fence(MODE_CHALLENGE),
        "### Posture « direct »" + fence(MODE_DIRECT),
        "",
    ]
    section("supervisor")
    out += [
        "## 2. Agent Codeur (texte seul, sans outils)",
        f"- Modèle puis replis : {_models(settings.coder_model, settings.coder_fallbacks, dedupe=False)} · température 0.1",
        "- Permissions : aucun accès disque ni shell ; reçoit tâche + contexte, renvoie du code en blocs numérotés que le superviseur écrit avec write_file(coder_block=N).",
        "### Prompt" + fence(G.CODER_PROMPT),
        "",
    ]
    section("coder")
    out += [
        "## 3. Résumeur de contexte (compaction)",
        f"- Utilise le modèle du superviseur (donc ses replis). Déclenché au-delà de {G.CONTEXT_COMPACT_TOKENS:,} tokens, garde ~{G.CONTEXT_KEEP_TOKENS:,} tokens récents.".replace(",", " "),
        "### Prompt" + fence(G.SUMMARY_PROMPT),
        "",
    ]
    section("summarizer")
    out += [
        "## 4. Sous-agents spécialisés (appelés via ask_agent / research, en parallèle, sans voir la conversation)",
        f"Prompt commun à tous (sauf chercheur), gabarit avec {{today}}, {{max_steps}}, {{profile}}" + fence(COMMON),
    ]
    ALL_ROLES = get_roles()
    for key, role in ALL_ROLES.items():
        if key == "researcher":
            main, fallbacks = settings.research_model, settings.research_fallbacks
        else:
            main = role.model or settings.agent_models.get(key) or settings.agent_models.get("reviewer", "")
            fallbacks = [m for m in settings.agent_fallbacks if m != main]
        out += [
            f"### {role.title} (`{key}`)" + ("" if role.builtin else " — agent personnalisé"),
            f"- Modèle puis replis : {_models(main, fallbacks)}",
            f"- Température {role.temperature if role.temperature is not None else role_temperature(key)} · au plus {role.max_steps} tours d'outils",
            f"- Outils : {', '.join(role.tools)}",
            f"- Permissions : {PERMISSIONS.get(key) or 'Agent personnalisé, lecture seule imposée : outils ' + (', '.join(role.tools) or 'aucun')}",
        ]
        if include_prompts:
            out.append("- Prompt du rôle" + (" (REMPLACE le prompt commun)" if key == "researcher" else " (ajouté au prompt commun)") + " :"
                       + fence(RESEARCH_PROMPT if key == "researcher" else role.prompt))
        out.append("")
        section(key)
    out += [
        "## 5. Garde-fous transverses",
        "- Fichiers confinés au dossier de travail ; bash isolé par namespaces ($HOME masqué), sans réseau en mode auto et pour le développeur/DevOps.",
        "- Mode auto : liste blanche stricte déterministe (aucun LLM) ; rm, curl, chemins sensibles, .env, MCP… demandent toujours confirmation.",
        "- Les rapports des sous-agents et les contenus web/MCP sont traités comme des DONNÉES non fiables, jamais comme des instructions.",
        "- Le développeur ne modifie qu'une copie jetable ; le vrai projet n'est touché que par apply_dev_changes après confirmation (conflits et fichiers sensibles refusés).",
    ]
    section("guardrails")
    from claude_clone.skills import load_skills
    skills, warns = load_skills()
    out += ["## 6. Skills (procédures chargées à la demande via use_skill ; index dans le prompt du superviseur)"]
    out += [f"- {s.name} ({'intégré' if s.builtin else 'utilisateur'}) : {s.description} · outils : {', '.join(s.tools) or '—'}" for s in skills.values()] or ["- aucun"]
    out += [f"- ⚠ {w}" for w in warns]
    section("skills")
    sections["common"] = sections.pop("common", [])
    if only:
        key = only.strip().lower()
        aliases = {"superviseur": "supervisor", "codeur": "coder", "resumeur": "summarizer", "résumeur": "summarizer", "compaction": "summarizer"}
        key = aliases.get(key, key)
        if key not in sections:
            known = ", ".join(["supervisor", "coder", "summarizer", *ALL_ROLES])
            return f"Agent inconnu : {only!r}. Valeurs possibles : {known}."
        head = f"# Configuration RÉELLE de « {key} » (endpoint {settings.supervisor_base_url}, clés non affichées)"
        extra = ["", "(Rappel — prompt commun des sous-agents, gabarit :)" + fence(COMMON)] if key in ALL_ROLES and key != "researcher" else []
        return "\n".join([head, *sections[key], *extra])
    return "\n".join(line for key in sections for line in sections[key])


def make_describe_agents_tool(settings: Settings):
    @tool("describe_agents")
    async def describe_agents_tool(include_prompts: bool = True, role: str = "") -> str:
        """Renvoie la configuration RÉELLE du système d'agents (superviseur, codeur, sous-agents) : prompts, modèles, replis,
        outils, permissions, limites. À appeler pour toute question sur les agents ou pour en faire une présentation : ne réponds
        jamais de mémoire. `include_prompts=false` donne une vue d'ensemble sans les prompts complets. `role` (supervisor, coder, summarizer, researcher,
        architect, challenger, developer, reviewer, devops, data, writer) ne renvoie QUE cet agent, prompt complet inclus : à
        utiliser pour citer le prompt exact d'un agent sans tout recharger."""
        return describe_agents(settings, include_prompts, only=role)

    return describe_agents_tool
