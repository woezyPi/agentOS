"""Graphe LangGraph : Superviseur + Agent Codeur + noeud d'outils.

    START → supervisor ──┬─ (réponse finale / abandon) ─→ END
               ▲         ├─ (appels d'outils locaux) ───→ tools ─┐   ← interrupt_before
               │         ├─ (delegate_to_coder) ────────→ coder ─┤
               │         └─ (JSON invalide, feedback) ─→ supervisor
               └───────────────────────────────────────────────┘

Gestion des sorties LLM malformées (le point faible des modèles open-weights) :
  * arguments d'outil en JSON invalide → langchain les range dans
    `AIMessage.invalid_tool_calls`. On remplace le message par une version sans
    appel d'outil + un message de correction, et on relance le superviseur
    (au plus MAX_RETRIES fois de suite) ;
  * arguments JSON valides mais ne respectant pas le schéma, outil inconnu,
    exception dans l'outil → ToolMessage(status="error") renvoyé au LLM ;
  * erreur réseau/API → déjà retentée par ChatOpenAI (max_retries) ; si elle
    persiste, le tour s'arrête proprement avec l'erreur dans `state["errors"]`.
"""
from __future__ import annotations

import asyncio
import operator
import re
import time
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    RemoveMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from claude_clone import tools as tools_mod
from claude_clone.failover import ModelPool
from claude_clone.memory import load_project_memory
from claude_clone.telemetry import TELEMETRY, UsageCallback
from claude_clone.config import MAX_STEPS_PER_TURN, CONTEXT_COMPACT_TOKENS, CONTEXT_KEEP_TOKENS, MAX_RETRIES, MAX_OUTPUT_CHARS, Settings
from claude_clone.tools import (
    DELEGATE_TOOL_NAME,
    LOCAL_TOOLS,
    LOCAL_TOOLS_BY_NAME,
    PlanArgs,
    delegate_to_coder,
    resolve_coder_args,
)


# --------------------------------------------------------------------------- #
# État
# --------------------------------------------------------------------------- #
class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # historique complet
    active_agent: str                                    # "supervisor" | "tools" | "coder"
    errors: Annotated[list[str], operator.add]           # journal cumulatif des erreurs
    steps: int                                           # tours d'outils du superviseur depuis la dernière demande
    retries: int                                         # sorties LLM invalides consécutives
    plan: list[dict]                                     # plan de travail [{content, status}]
    summary: str                                         # résumé des messages anciens (compaction)
    coder_blocks: list[str]                              # blocs de code du dernier résultat du codeur (write_file coder_block=N)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
SUPERVISOR_PROMPT = """Tu es le Superviseur d'un assistant de développement en ligne de commande.
Dossier de travail : {workdir} (tu ne peux lire/écrire que dedans).

Outils :
- read_file, grep (regex), glob : lecture et recherche, sans confirmation. Explore avant de modifier.
- edit_file(path, old, new) : modification locale d'un fichier existant (old exact et unique). À préférer à write_file.
- write_file(path, content) : création / réécriture complète. execute_bash : commandes shell.
  edit_file, write_file et execute_bash demandent la confirmation de l'utilisateur : sois précis, évite le destructif.
- update_plan(todos) : plan visible de l'utilisateur (étapes pending / in_progress / completed).
- delegate_to_coder(task, context) : confie la rédaction de code non triviale à l'Agent Codeur.
  Il n'a accès ni au disque ni à cette conversation : mets dans `context` tout le code et les contraintes utiles.
  Son résultat arrive avec la liste de ses blocs de code numérotés.

Règles :
1. Pour appliquer du code du codeur : write_file(path=..., coder_block=N) avec `content` VIDE. Ne recopie jamais
   son code dans tes arguments (coûteux, risque d'altération).
2. Ne fais pas confiance aveuglément au codeur. Avant d'appliquer, relis son code : respecte-t-il la demande,
   est-il correct, gère-t-il les cas limites ? Si non, renvoie-le au codeur avec des critiques précises
   (nouvelle délégation) au lieu de l'appliquer.
3. Après écriture, vérifie en exécutant avec de VRAIS cas de test (y compris des cas qui doivent échouer).
   Si les résultats sont absurdes ou peu discriminants, dis-le franchement et corrige ; ne présente pas
   un résultat médiocre comme un succès.
4. Un appel d'outil à la fois quand ils dépendent les uns des autres ; appelle delegate_to_coder SEUL.
5. Arguments d'outil en JSON strictement valide (guillemets doubles, \\n échappés).
6. Si la demande est ambiguë, pose une question courte plutôt que d'inventer un cahier des charges.
7. Dès qu'une tâche a 3 étapes ou plus, appelle update_plan au début, puis à chaque étape terminée
   (une seule étape in_progress à la fois). Ne l'utilise pas pour une tâche triviale.
8. Réponse finale concise, dans la langue de l'utilisateur, sans appel d'outil.
9. Sobriété : au plus UNE courte phrase avant un appel d'outil, pas de récapitulatif intermédiaire.
   Regroupe les vérifications dans UNE commande (ou un petit script) plutôt que plusieurs. Reste dans le périmètre
   demandé : ne modifie pas le comportement du code au-delà de la demande ; signale plutôt le point ambigu.
   Si un défaut du code du codeur persiste après 2 tentatives de correction, arrête-toi : explique l'état
   en quelques lignes, ou renvoie-le au codeur avec des critiques précises. Tu as un budget d'étapes par demande.
"""

SUMMARY_PROMPT = """Résume ces échanges pour qu'un assistant de développement puisse continuer le travail sans
l'historique : objectifs de l'utilisateur, décisions et contraintes, fichiers créés/modifiés (chemins exacts),
commandes importantes et leurs résultats, erreurs rencontrées, état actuel et prochaines étapes.
Dense, factuel, 600 mots maximum, en français. N'invente rien."""

CODER_PROMPT = """Tu es l'Agent Codeur, expert en génération et modification de code.
Tu reçois une tâche et un contexte. Réponds avec le résultat :
- une ligne de texte indiquant le fichier cible (ex : `Fichier : evaluation.py`), puis UN bloc ``` par fichier
  contenant uniquement le code complet et fonctionnel (pas de nom de fichier ni de commentaire parasite dans le bloc) ;
- ensuite au plus quelques lignes d'explication, y compris les limites connues de ta solution.
Ne pose pas de questions, ne propose pas d'alternatives non demandées. Si une information indispensable
manque, dis précisément laquelle au lieu de la deviner. Privilégie la correction et la simplicité à la quantité."""


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #
def as_text(content: Any) -> str:
    """Extrait le texte d'un `content` LangChain (str ou liste de blocs)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content or "")


_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)


def extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """[(langage, code)] pour chaque bloc ``` du texte ; le code se termine par un saut de ligne."""
    return [(lang.strip() or "texte", code if code.endswith("\n") else code + "\n") for lang, code in _FENCE.findall(text)]


def compaction_cut(history: list[AnyMessage]) -> int:
    """Indice à partir duquel on garde l'historique tel quel ; 0 = pas de compaction.

    On coupe toujours juste avant un HumanMessage : une paire AIMessage(tool_calls)/ToolMessage
    n'est ainsi jamais séparée (ce qui ferait rejeter la requête par l'API)."""
    if count_tokens_approximately(history) <= CONTEXT_COMPACT_TOKENS:
        return 0
    candidates = [i for i, m in enumerate(history) if i > 0 and isinstance(m, HumanMessage)]
    for i in candidates:  # plus petit indice dont la fin tient dans le budget
        if count_tokens_approximately(history[i:]) <= CONTEXT_KEEP_TOKENS:
            return i
    return candidates[-1] if candidates else 0


def transcript(messages: list[AnyMessage], per_message: int = 1500) -> str:
    lines = []
    for m in messages:
        body = as_text(m.content)[:per_message]
        for tc in getattr(m, "tool_calls", None) or []:
            body += f" [outil {tc['name']}({str(tc['args'])[:200]})]"
        lines.append(f"[{type(m).__name__.removesuffix('Message')}] {body}")
    return "\n".join(lines)


def make_llm(base_url: str, api_key: str, model: str, temperature: float, role: str = "llm", max_retries: int = 3) -> ChatOpenAI:
    """Client pour une API compatible OpenAI hébergée sur le cluster."""
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout=180,
        stream_usage=True,  # tokens consommés même en streaming (télémétrie)
        callbacks=[UsageCallback(role, model)],
        max_retries=max_retries,  # retries réseau/429/5xx avec backoff gérés par le SDK openai
    )


def _tool_error(tc: dict, message: str) -> ToolMessage:
    return ToolMessage(
        content=f"ERREUR : {message}", tool_call_id=tc["id"], name=tc["name"], status="error"
    )


# --------------------------------------------------------------------------- #
# Construction du graphe
# --------------------------------------------------------------------------- #
def build_graph(
    settings: Settings | None = None,
    *,
    supervisor_llm: BaseChatModel | None = None,
    coder_llm: BaseChatModel | None = None,
    checkpointer: Any = None,
):
    """Compile le graphe. Les LLM peuvent être injectés (tests) ; sinon ChatOpenAI est créé."""
    if supervisor_llm is None or coder_llm is None:
        assert settings is not None, "settings requis si les LLM ne sont pas injectés"

    def pool(injected, base_url, api_key, models, temperature, role) -> ModelPool:
        """Un modèle injecté (tests) ou la liste préférence + repli, chacun avec peu de retries SDK."""
        if injected is not None:
            return ModelPool([("injecté", injected)])
        retries = 1 if len(models) > 1 else 3  # avec un repli, on bascule vite plutôt que d'insister
        return ModelPool([(m, make_llm(base_url, api_key, m, temperature, role, max_retries=retries)) for m in models])

    supervisor_pool = pool(supervisor_llm, getattr(settings, "supervisor_base_url", ""), getattr(settings, "supervisor_api_key", ""),
                           [getattr(settings, "supervisor_model", ""), *getattr(settings, "supervisor_fallbacks", ())], 0.2, "superviseur")
    coder_pool = pool(coder_llm, getattr(settings, "coder_base_url", ""), getattr(settings, "coder_api_key", ""),
                      [getattr(settings, "coder_model", ""), *getattr(settings, "coder_fallbacks", ())], 0.1, "codeur")
    supervisor_llm, coder_llm = supervisor_pool, coder_pool  # même interface (.ainvoke) que le modèle seul
    supervisor = supervisor_pool.bind_tools([*LOCAL_TOOLS, delegate_to_coder])

    # ---- Noeud : Superviseur ------------------------------------------------
    async def supervisor_node(state: AgentState) -> dict:
        retries = state.get("retries", 0)
        history, summary, extra = state["messages"], state.get("summary", ""), {}

        # Compaction : on résume le début de la conversation et on le retire de l'état.
        cut = compaction_cut(history)
        if cut:
            try:
                prompt = SUMMARY_PROMPT + (f"\n\n## Résumé existant\n{summary}" if summary else "") \
                    + "\n\n## Échanges à résumer\n" + transcript(history[:cut])
                new_summary = as_text((await supervisor_llm.ainvoke([HumanMessage(prompt)])).content).strip()
                if new_summary:
                    summary = new_summary
                    extra = {"summary": summary, "errors": [f"Contexte compacté : {cut} messages résumés."]}
                    removals = [RemoveMessage(id=m.id) for m in history[:cut]]
                    history = history[cut:]
            except Exception as exc:  # on continue sans compacter plutôt que de bloquer le tour
                extra = {"errors": [f"Compaction impossible ({type(exc).__name__}: {exc})"]}

        def out(update: dict) -> dict:
            """Ajoute à la mise à jour l'effet de la compaction (suppressions + résumé) et les bascules de modèle."""
            if notice := supervisor_pool.pop_notice():
                update["errors"] = [*update.get("errors", []), notice]
            if "summary" in extra:
                update["messages"] = [*removals, *update.get("messages", [])]
                update["summary"] = extra["summary"]
            update["errors"] = [*extra.get("errors", []), *update.get("errors", [])]
            return update

        sys_text = SUPERVISOR_PROMPT.format(workdir=tools_mod.WORKDIR)
        memory_text, _ = load_project_memory()
        if memory_text:
            sys_text += f"\n\n## Instructions de l'utilisateur pour ce projet\n{memory_text}"
        if state.get("plan"):
            mark = {"completed": "x", "in_progress": "~", "pending": " "}
            sys_text += "\n\n## Plan en cours\n" + "\n".join(f"- [{mark[t['status']]}] {t['content']}" for t in state["plan"])
        if summary:
            sys_text += f"\n\n## Résumé de la conversation précédente (messages plus anciens retirés)\n{summary}"
        steps = state.get("steps", 0)
        over_budget = steps >= MAX_STEPS_PER_TURN
        if over_budget:  # plus d'outils : le modèle doit faire le point avec l'utilisateur
            sys_text += ("\n\nBUDGET D'ÉTAPES ATTEINT : n'appelle plus d'outil. En quelques lignes : ce qui est fait, "
                         "ce qui bloque, et l'action suivante que tu proposes. L'utilisateur dira s'il faut continuer.")
        try:
            ai: AIMessage = await (supervisor_llm if over_budget else supervisor).ainvoke([SystemMessage(sys_text), *history])
            if over_budget and ai.tool_calls:
                ai = AIMessage(content=as_text(ai.content) or "[budget d'étapes atteint]")
        except Exception as exc:  # réseau, auth, 4xx/5xx après retries du SDK
            # retries=MAX_RETRIES ⇒ le routeur arrête le tour (inutile de re-boucler).
            return out({
                "errors": [f"Superviseur injoignable : {type(exc).__name__}: {exc}"],
                "retries": MAX_RETRIES,
                "active_agent": "supervisor",
            })

        # 1) JSON d'arguments invalide → on retire TOUS les appels d'outil du message
        #    (l'API exige une réponse par tool_call_id, et l'id d'un appel invalide
        #    peut être absent) et on demande une réémission.
        if ai.invalid_tool_calls:
            details = "; ".join(
                f"{c.get('name') or '?'} → {c.get('error') or 'JSON invalide'} "
                f"(reçu : {str(c.get('args'))[:160]!r})"
                for c in ai.invalid_tool_calls
            )
            cleaned = AIMessage(content=as_text(ai.content) or "[appel d'outil rejeté : JSON invalide]")
            feedback = HumanMessage(
                content=(
                    "[système] Tes arguments d'appel d'outil ne sont pas du JSON valide "
                    f"({details}). Réémets l'appel avec un JSON strictement valide."
                )
            )
            return out({
                "messages": [cleaned, feedback],
                "errors": [f"JSON d'outil invalide ({retries + 1}/{MAX_RETRIES}) : {details}"],
                "retries": retries + 1,
                "active_agent": "supervisor",
            })

        # 2) Réponse vide : ni texte ni outil.
        if not ai.tool_calls and not as_text(ai.content).strip():
            return out({
                "messages": [
                    AIMessage(content="[réponse vide]"),
                    HumanMessage(content="[système] Réponse vide. Réponds ou appelle un outil."),
                ],
                "errors": [f"Réponse vide du superviseur ({retries + 1}/{MAX_RETRIES})"],
                "retries": retries + 1,
                "active_agent": "supervisor",
            })

        return out({"messages": [ai], "retries": 0, "steps": steps + (1 if ai.tool_calls else 0), "active_agent": "supervisor"})

    # ---- Noeud : outils locaux (précédé d'un interrupt_before) -------------
    async def tools_node(state: AgentState, config: RunnableConfig) -> dict:
        # Appels refusés par l'utilisateur : transmis par le CLI via config["configurable"].
        denied = set(config.get("configurable", {}).get("denied_call_ids") or [])
        offline = set(config.get("configurable", {}).get("offline_call_ids") or [])  # commandes auto-approuvées
        last = state["messages"][-1]
        results: list[ToolMessage] = []
        errors: list[str] = []
        new_plan: list[dict] | None = None
        for tc in last.tool_calls:  # séquentiel : l'ordre compte (write puis bash…)
            name = tc["name"]
            if tc["id"] in denied:
                results.append(_tool_error(tc, "REFUSÉ par l'utilisateur. Ne réessaie pas à l'identique ; propose une alternative ou demande des précisions."))
                continue
            if name == DELEGATE_TOOL_NAME:
                results.append(_tool_error(tc, "delegate_to_coder doit être appelé seul. Réémets-le séparément."))
                continue
            if name == "update_plan":
                try:
                    todos = PlanArgs(**tc["args"]).todos
                    if sum(t.status == "in_progress" for t in todos) > 1:
                        raise ValueError("une seule étape peut être in_progress")
                    new_plan = [t.model_dump() for t in todos]
                    done = sum(t.status == "completed" for t in todos)
                    results.append(ToolMessage(content=f"Plan enregistré : {done}/{len(todos)} étapes terminées.", tool_call_id=tc["id"], name=name))
                except Exception as exc:
                    results.append(_tool_error(tc, f"plan invalide : {exc}"))
                continue
            tool = LOCAL_TOOLS_BY_NAME.get(name)
            if tool is None:
                results.append(_tool_error(tc, f"outil inconnu '{name}'. Outils : {', '.join(LOCAL_TOOLS_BY_NAME)}."))
                continue
            t0 = time.monotonic()
            try:
                args = resolve_coder_args(tc["args"], state.get("coder_blocks", [])) if name == "write_file" else tc["args"]
                if tc["id"] in offline and name == "execute_bash":
                    args = {**args, "offline": True}
                output = await tool.ainvoke(args)  # valide les args via le schéma pydantic
                results.append(ToolMessage(content=as_text(output), tool_call_id=tc["id"], name=name))
                TELEMETRY.record_tool(name, "ok", (time.monotonic() - t0) * 1000)
            except Exception as exc:
                TELEMETRY.record_tool(name, "error", (time.monotonic() - t0) * 1000)  # ToolException, ValidationError, OSError…
                msg = f"{type(exc).__name__}: {exc}"
                results.append(_tool_error(tc, msg[:MAX_OUTPUT_CHARS]))
                errors.append(f"{name} : {msg[:200]}")
        update = {"messages": results, "errors": errors, "active_agent": "tools"}
        if new_plan is not None:
            update["plan"] = new_plan
        return update

    # ---- Noeud : Agent Codeur ----------------------------------------------
    notices: list[str] = []

    async def run_coder(tc: dict) -> ToolMessage:
        task, context = tc["args"].get("task"), tc["args"].get("context", "")
        if not isinstance(task, str) or not task.strip():
            return _tool_error(tc, "`task` manquant ou invalide.")
        prompt = f"## Tâche\n{task}\n\n## Contexte\n{as_text(context)}"
        try:
            resp = await coder_llm.ainvoke([SystemMessage(CODER_PROMPT), HumanMessage(prompt)])
        except Exception as exc:
            return _tool_error(tc, f"Agent Codeur indisponible : {type(exc).__name__}: {exc}")
        if notice := coder_pool.pop_notice():
            notices.append(notice)
        text = as_text(resp.content).strip()
        if not text:
            return _tool_error(tc, "L'Agent Codeur a renvoyé une réponse vide. Reformule la tâche.")
        return ToolMessage(content=text, tool_call_id=tc["id"], name=DELEGATE_TOOL_NAME)

    async def coder_node(state: AgentState) -> dict:
        last = state["messages"][-1]
        # Plusieurs délégations indépendantes → en parallèle.
        results = list(await asyncio.gather(*(run_coder(tc) for tc in last.tool_calls)))
        blocks: list[str] = []  # numérotation continue sur l'ensemble des résultats de ce tour
        for idx, msg in enumerate(results):
            if msg.status == "error":
                continue
            found = extract_code_blocks(as_text(msg.content))
            if not found:
                continue
            listing = ", ".join(
                f"#{len(blocks) + k + 1} ({lang}, {code.count(chr(10))} lignes)" for k, (lang, code) in enumerate(found)
            )
            blocks.extend(code for _, code in found)
            results[idx] = msg.model_copy(update={"content": (
                f"{msg.content}\n\n---\n[Blocs disponibles pour write_file(path, coder_block=N) : {listing}]"
            )})
        errors = [m.content for m in results if m.status == "error"] + notices[:]
        notices.clear()
        return {"messages": results, "errors": errors, "coder_blocks": blocks, "active_agent": "coder"}

    # ---- Routage -----------------------------------------------------------
    def route_after_supervisor(state: AgentState) -> str:
        if state.get("retries", 0) >= MAX_RETRIES:
            return END  # abandon : trop de sorties invalides / API HS
        last = state["messages"][-1]
        if isinstance(last, HumanMessage):  # message de correction → on réessaie
            return "supervisor"
        calls = getattr(last, "tool_calls", None)
        if not calls:
            return END  # réponse finale
        if all(tc["name"] == DELEGATE_TOOL_NAME for tc in calls):
            return "coder"
        return "tools"

    builder = StateGraph(AgentState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("tools", tools_node)
    builder.add_node("coder", coder_node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor", route_after_supervisor, ["supervisor", "tools", "coder", END]
    )
    builder.add_edge("tools", "supervisor")
    builder.add_edge("coder", "supervisor")

    # interrupt_before exige un checkpointer (ici en mémoire : l'état vit le temps du process).
    return builder.compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_before=["tools"],
    )
