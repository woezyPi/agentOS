/"""CLI interactif : boucle prompt-toolkit + affichage rich des étapes du graphe.

Usage :
    export LLM_BASE_URL=https://llm.mon-entreprise.example/v1
    export LLM_API_KEY=sk-...
    python main.py            # travaille dans le dossier courant

Options : --resume reprend la dernière session du dossier · --auto active le mode auto.
Commandes : /help  /reset  /exit  (Ctrl-D quitte, Ctrl-C annule la saisie ou le tour en cours)
"""
from __future__ import annotations

import argparse
from collections import Counter
import asyncio
import difflib
import json
import os
import sys
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.tools import ToolException
from langgraph.errors import GraphRecursionError
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from claude_clone import tools as tools_mod
from claude_clone.context import context_info, format_context
from claude_clone.config import MAX_RETRIES, RECURSION_LIMIT, load_settings
from claude_clone.autopilot import classify
from claude_clone.graph import as_text, build_graph
from claude_clone.memory import load_project_memory
from claude_clone.telemetry import TELEMETRY
from claude_clone.mcp_client import load_mcp_tools
from claude_clone.prompts import DEFAULT_MODE, MODES
from claude_clone.skills import load_skills
from claude_clone import developer as DEV
from claude_clone.agents import get_roles
from claude_clone.tools import APPLY_TOOL_NAME, ASK_AGENT_TOOL_NAME, DELEGATE_TOOL_NAME, RESEARCH_TOOL_NAME, register_extra_tools, requires_approval, apply_edit, resolve_coder_args, safe_path

console = Console()
TURN = {"tools": Counter(), "agents": []}  # qui a travaillé pendant le tour en cours
ACTIVE_SETTINGS = None  # réglages du CLI en cours (pour la fenêtre de contexte du modèle)
TOOL_PREVIEW_LINES = 12
CODER_PREVIEW_LINES = 25


# --------------------------------------------------------------------------- #
# Rendu
# --------------------------------------------------------------------------- #
def _short_args(args: dict, limit: int = 100) -> str:
    parts = []
    for k, v in (args or {}).items():
        s = str(v).replace("\n", "⏎")
        parts.append(f"{k}={s[:limit]}{'…' if len(s) > limit else ''}")
    return ", ".join(parts)


def render_update(node: str, update: dict | None, streamed_text: bool = False) -> None:
    """Affiche ce qu'un noeud vient de produire (le texte déjà streamé n'est pas réaffiché)."""
    if not update:
        return
    for err in update.get("errors", []) or []:
        info = err.startswith(("Contexte compacté", "↪"))
        console.print(Text(f"{'🗜 ' if err.startswith('Contexte') else ('' if err.startswith('↪') else '⚠ ')}{err}", style="dim" if info else "yellow"))
    if update.get("plan") is not None:
        icon = {"completed": "[green]✔[/green]", "in_progress": "[yellow]▶[/yellow]", "pending": "[dim]○[/dim]"}
        rows = "\n".join(f"{icon[t['status']]} {escape(t['content'])}" for t in update["plan"]) or "[dim](plan effacé)[/dim]"
        console.print(Panel(rows, title="Plan", border_style="magenta", title_align="left"))
    for msg in update.get("messages", []) or []:
        if isinstance(msg, AIMessage):
            text = as_text(msg.content).strip()
            if text and not streamed_text:
                console.print(Panel(Markdown(text), title="Superviseur", border_style="cyan", title_align="left"))
            for tc in msg.tool_calls:
                if tc["name"] in (ASK_AGENT_TOOL_NAME, RESEARCH_TOOL_NAME, DELEGATE_TOOL_NAME):
                    TURN["agents"].append({ASK_AGENT_TOOL_NAME: str(tc["args"].get("role")), RESEARCH_TOOL_NAME: "researcher", DELEGATE_TOOL_NAME: "coder"}[tc["name"]] + (" (panel)" if tc["args"].get("panel") else ""))
                elif tc["name"] != "update_plan":
                    TURN["tools"][tc["name"]] += 1
                label = {DELEGATE_TOOL_NAME: "→ Agent Codeur", RESEARCH_TOOL_NAME: "🔎 Chercheur",
                         ASK_AGENT_TOOL_NAME: f"🧩 Agent {tc['args'].get('role', '?')}"}.get(tc["name"], "⚙")
                console.print(Text(f"{label} {tc['name']}({_short_args(tc['args'])})", style="bold yellow"))
        elif isinstance(msg, ToolMessage):
            body = as_text(msg.content).strip()
            role = msg.additional_kwargs.get("role")
            if (node == "coder" or role) and msg.status != "error":
                lines = body.splitlines()
                if len(lines) > CODER_PREVIEW_LINES:  # le détail complet est montré à la confirmation d'écriture
                    body = "\n".join(lines[:CODER_PREVIEW_LINES]) + f"\n\n… *(+{len(lines) - CODER_PREVIEW_LINES} lignes)*"
                roles = get_roles()
                title, color = (roles[role].title, roles[role].color) if role in roles else ("Agent Codeur", "green")
                if msg.additional_kwargs.get("panel"):
                    title += " · PANEL"
                elif msg.additional_kwargs.get("model"):
                    title += " · " + msg.additional_kwargs["model"].split("/")[-1]
                console.print(Panel(Markdown(body), title=title, border_style=color, title_align="left"))
            else:
                lines = body.splitlines()
                shown = "\n".join(lines[:TOOL_PREVIEW_LINES])
                if len(lines) > TOOL_PREVIEW_LINES:
                    shown += f"\n… (+{len(lines) - TOOL_PREVIEW_LINES} lignes)"
                style = "red" if msg.status == "error" else "dim"
                console.print(Text(shown, style=style))


def render_pending_call(tc: dict, blocks: list[str], values: dict | None = None) -> None:
    """Détail d'un appel sensible avant confirmation."""
    args = tc["args"]
    if tc["name"] == "execute_bash":
        body = Syntax(str(args.get("command", "")), "bash", theme="ansi_dark", word_wrap=True)
        console.print(Panel(body, title=f"bash (timeout {args.get('timeout', 'défaut')})", border_style="red", title_align="left"))
    elif tc["name"] == "write_file":
        console.print(Panel(_write_preview(args, blocks), title=f"write_file → {escape(str(args.get('path')))}", border_style="red", title_align="left"))
    elif tc["name"] == APPLY_TOOL_NAME:
        cs = next((c for c in (values or {}).get("dev_changes", []) if c["id"] == args.get("change_set")), None)
        body = Group(Text(DEV.describe(cs), style="dim"), _diff_from_text(DEV.change_set_diff(cs))) if cs else Text("jeu de modifications introuvable — l'outil refusera", style="red")
        console.print(Panel(body, title=f"apply_dev_changes → jeu #{args.get('change_set')}", border_style="red", title_align="left"))
    elif tc["name"] == "edit_file":
        console.print(Panel(_edit_preview(args), title=f"edit_file → {escape(str(args.get('path')))}", border_style="red", title_align="left"))
    else:
        console.print(Text(f"{tc['name']}({_short_args(args)})", style="red"))


def _diff_from_text(diff: str):
    return Syntax(diff, "diff", theme="ansi_dark")


def _diff(old: str, new: str):
    diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "avant", "après", n=2))
    return Syntax(diff or "(aucun changement)", "diff", theme="ansi_dark")


def _write_preview(args: dict, blocks: list[str]):
    try:
        args = resolve_coder_args(args, blocks)  # même résolution que le noeud "tools"
        new = args.get("content")
        if not isinstance(new, str):
            return Text("(contenu invalide — l'outil refusera)", style="red")
        real = safe_path(str(args.get("path", "")))
    except ToolException as exc:
        return Text(f"refusé : {exc}", style="red")
    if os.path.isfile(real):
        try:
            old = Path(real).read_text(encoding="utf-8", errors="replace")
        except OSError:
            old = ""
        return _diff(old, new)
    lexer = Syntax.guess_lexer(real, new)
    head = "\n".join(new.splitlines()[:60])
    more = Text(f"\n… (+{len(new.splitlines()) - 60} lignes)", style="dim") if len(new.splitlines()) > 60 else Text("")
    return Group(Text(f"nouveau fichier · {len(new.splitlines())} lignes", style="dim"), Syntax(head, lexer, theme="ansi_dark", word_wrap=True), more)


def _edit_preview(args: dict):
    try:
        real = safe_path(str(args.get("path", "")))
        old_text = Path(real).read_text(encoding="utf-8", errors="replace")
        new_text, count = apply_edit(old_text, args.get("old"), args.get("new"), bool(args.get("replace_all")))
    except (ToolException, OSError) as exc:
        return Text(f"l'outil refusera : {exc}", style="red")
    return Group(Text(f"{count} remplacement(s)", style="dim"), _diff(old_text, new_text))


# --------------------------------------------------------------------------- #
# Human-in-the-loop
# --------------------------------------------------------------------------- #
BASH_SAFE_KEY = ("execute_bash", "*liste-blanche*")


def _allow_key(tc: dict) -> tuple:
    """Clé d'« autorisation pour la session » : commande exacte (bash) ou chemin (fichiers)."""
    a = tc["args"]
    return (tc["name"], a.get("command") if tc["name"] == "execute_bash" else a.get("path", a.get("change_set")))


async def decide(session: PromptSession, calls: list[dict], values: dict, allow: set, auto: bool = False) -> tuple[list[str], list[str]]:
    """Interroge l'utilisateur appel par appel ; renvoie (ids REFUSÉS, ids bash auto-approuvés à lancer hors réseau).

    Réponses : Y/Entrée = oui · n = non · a = oui et ne plus demander (jusqu'à la fin de la session) :
    pour ce fichier, pour cette commande, ou — si la commande est sûre — pour toutes les commandes sûres. Les lectures passent sans question."""
    denied: list[str] = []
    offline: list[str] = []
    for tc in calls:
        if not requires_approval(tc["name"]):
            continue
        safe_bash_ok = tc["name"] == "execute_bash" and BASH_SAFE_KEY in allow  # « a » donné sur une commande sûre
        if auto or safe_bash_ok:
            ok, why = classify(tc)
            label = "⚡ auto" if auto else "✓ session"
            if ok:
                console.print(Text(f"{label} : {tc['name']}({_short_args(tc['args'], 70)}) — {why}", style="green"))
                if tc["name"] in ("write_file", "edit_file"):
                    render_pending_call(tc, values.get("coder_blocks", []), values)  # le diff reste visible même sans confirmation
                if tc["name"] == "execute_bash":
                    offline.append(tc["id"])
                continue
            if auto:
                console.print(Text(f"⚡ auto : confirmation requise — {why}", style="yellow"))
        if _allow_key(tc) in allow:
            console.print(Text(f"✓ autorisé pour la session : {tc['name']}({_short_args(tc['args'], 60)})", style="dim"))
            continue
        render_pending_call(tc, values.get("coder_blocks", []), values)
        while True:
            try:
                ans = (await session.prompt_async(HTML("<b><ansired>Exécuter ? [Y/n/a=toujours] </ansired></b>"))).strip().lower()
            except (KeyboardInterrupt, EOFError):
                ans = "n"  # Ctrl-C / Ctrl-D = refus
            if ans in ("", "y", "yes", "o", "oui"):
                break
            if ans in ("a", "always", "toujours"):
                if tc["name"] == "execute_bash" and classify(tc)[0]:
                    allow.add(BASH_SAFE_KEY)  # toute commande de la liste blanche, sans réseau, jusqu'à la fin de la session
                    console.print(Text("✓ désormais : les commandes de la liste blanche (lecture, python, pytest, git local…) passent sans question, hors réseau.", style="green"))
                else:
                    allow.add(_allow_key(tc))
                break
            if ans in ("n", "no", "non"):
                denied.append(tc["id"])
                break
            console.print("Réponds par Y, n ou a.", style="dim")
    return denied, offline


def _refusal_messages(calls: list[dict], reason: str) -> list[ToolMessage]:
    return [
        ToolMessage(content=reason, tool_call_id=tc["id"], name=tc["name"], status="error")
        for tc in calls
    ]


async def repair_dangling_tool_calls(graph, config) -> None:
    """Après un Ctrl-C en plein tour, un AIMessage peut garder des tool_calls sans réponse
    (ce qui ferait rejeter la requête suivante par l'API). On les clôture."""
    snap = await graph.aget_state(config)
    msgs = snap.values.get("messages", [])
    idx = next((i for i in range(len(msgs) - 1, -1, -1) if isinstance(msgs[i], AIMessage)), None)
    if idx is None:
        return
    answered = {m.tool_call_id for m in msgs[idx + 1:] if isinstance(m, ToolMessage)}
    missing = [tc for tc in msgs[idx].tool_calls if tc["id"] not in answered]
    if missing:
        await graph.aupdate_state(
            config, {"messages": _refusal_messages(missing, "Annulé (tour interrompu).")}, as_node="tools"
        )


# --------------------------------------------------------------------------- #
# Exécution d'un tour
# --------------------------------------------------------------------------- #
async def stream_graph(graph, config, inputs) -> None:
    """Fait avancer le graphe jusqu'à sa fin ou jusqu'à l'interruption.

    Les tokens du superviseur sont affichés au fil de l'eau (texte brut) ; les autres étapes
    (appels d'outils, résultats, codeur) sont rendues quand leur noeud se termine.
    """
    def new_status(msg: str):
        st = console.status(msg, spinner="dots")
        st.start()
        return st

    status = new_status("[bold cyan]Superviseur : réflexion…")
    streaming = False  # du texte du superviseur est en cours d'affichage
    streamed_any = False
    coder_chars = 0
    try:
        async for mode, data in graph.astream(inputs, config, stream_mode=["updates", "messages"]):
            if mode == "messages":
                chunk, meta = data
                node = meta.get("langgraph_node")
                text = as_text(chunk.content) if isinstance(chunk, AIMessageChunk) else ""
                if node == "supervisor" and text:
                    if not streaming:
                        status.stop()
                        console.print(Text("Superviseur" + (f" · {ACTIVE_SETTINGS.supervisor_model.split(chr(47))[-1]}" if ACTIVE_SETTINGS else "") + " ▸", style="bold cyan"))
                        streaming = streamed_any = True
                    console.print(Text(text), end="", soft_wrap=True)
                elif node == "coder" and text:
                    coder_chars += len(text)
                    status.update(f"[bold green]Agent Codeur : génération… {coder_chars} car.")
                continue
            # mode == "updates"
            if streaming:
                console.print()  # fin de ligne du texte streamé
                streaming = False
                status = new_status("[bold cyan]Traitement…")
            for node, update in data.items():
                if node.startswith("__"):
                    continue
                status.stop()
                render_update(node, update, streamed_text=(node == "supervisor" and streamed_any))
                streamed_any = False if node == "supervisor" else streamed_any
                calls = [tc for m in (update or {}).get("messages", []) if isinstance(m, AIMessage) for tc in m.tool_calls]
                coder_chars = 0
                msg = ("[bold green]Agent Codeur : génération…" if any(tc["name"] == DELEGATE_TOOL_NAME for tc in calls)
                       else "[bold yellow]Sous-agents : travail en cours…" if any(tc["name"] in (RESEARCH_TOOL_NAME, ASK_AGENT_TOOL_NAME) for tc in calls)
                       else "[bold cyan]Superviseur : réflexion…")
                status = new_status(msg)
    finally:
        status.stop()
        if streaming:
            console.print()


async def run_turn(graph, session: PromptSession, config: dict, user_text: str, allow: set, auto: bool = False) -> None:
    await repair_dangling_tool_calls(graph, config)
    mark = TELEMETRY.mark()
    TURN["tools"].clear(); TURN["agents"].clear()
    inputs: dict | None = {"messages": [HumanMessage(user_text)], "retries": 0, "steps": 0}
    try:
        while True:
            await stream_graph(graph, config, inputs)
            snap = await graph.aget_state(config)
            if not snap.next:  # terminé
                if snap.values.get("retries", 0) >= MAX_RETRIES:
                    console.print(f"[red]Tour abandonné après {MAX_RETRIES} échecs consécutifs du modèle.[/red]")
                return
            # Interruption avant "tools" : le dernier message est l'AIMessage avec les appels.
            calls = snap.values["messages"][-1].tool_calls
            denied, offline = await decide(session, calls, snap.values, allow, auto)
            if denied:
                console.print(f"[red]{len(denied)} appel(s) refusé(s).[/red]")
            # Le noeud "tools" lit cette liste dans sa config et répond « REFUSÉ » à ces appels.
            config["configurable"]["denied_call_ids"] = denied
            config["configurable"]["offline_call_ids"] = offline
            inputs = None  # reprise depuis le checkpoint
    except (KeyboardInterrupt, asyncio.CancelledError):
        task = asyncio.current_task()
        if task is not None and hasattr(task, "uncancel"):
            task.uncancel()  # Python 3.11+ : SIGINT a annulé la tâche principale
        console.print("\n[yellow]Tour interrompu.[/yellow]")
    except GraphRecursionError:
        console.print(f"[red]Limite de {RECURSION_LIMIT} étapes atteinte : boucle probable, tour arrêté.[/red]")
    except Exception as exc:  # filet de sécurité : le CLI ne doit pas mourir sur une erreur
        console.print(f"[red]Erreur : {type(exc).__name__}: {escape(str(exc))}[/red]")
    finally:
        config["configurable"].pop("denied_call_ids", None)
        config["configurable"].pop("offline_call_ids", None)
        if line := TELEMETRY.summary(mark):
            console.print(Text(line, style="dim"))
        if TURN["tools"] or TURN["agents"]:  # qui a travaillé : le superviseur seul, ou avec quels sous-agents
            sup = (ACTIVE_SETTINGS.supervisor_model.split("/")[-1] if ACTIVE_SETTINGS else "?")
            direct = ", ".join(f"{k} ×{v}" if v > 1 else k for k, v in TURN["tools"].items())
            console.print(Text(f"🧭 superviseur ({sup})" + (f" — outils directs : {direct}" if direct else "") + " — sous-agents : " + (", ".join(TURN["agents"]) or "aucun (le superviseur a tout fait lui-même)"), style="dim"))
        try:  # jauge de contexte en fin de tour
            console.print(Text("🧮 " + format_context(context_info((await graph.aget_state(config)).values, ACTIVE_SETTINGS), bar=False), style="dim"))
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Boucle principale
# --------------------------------------------------------------------------- #
HELP = "/help  aide · /skills  liste · /skill NOM  lance un skill · /context  jauge · /compact [consigne]  compacte · /summary  résumé · /doctor  teste les modèles · /mode  posture challenge|direct · /auto  mode auto · /stats  consommation · /reset  nouvelle conversation · /exit  quitter · Ctrl-C  annule · Ctrl-D  quitte"
STATE_DIR = Path.home() / ".claude_clone"


def _threads() -> dict:
    try:
        return json.loads((STATE_DIR / "threads.json").read_text())
    except (OSError, ValueError):
        return {}


def _remember_thread(thread_id: str) -> None:
    data = _threads()
    data[tools_mod.WORKDIR] = thread_id  # une session « courante » par dossier de travail
    (STATE_DIR / "threads.json").write_text(json.dumps(data))


def _new_config(thread_id: str | None = None) -> dict:
    return {"configurable": {"thread_id": thread_id or uuid.uuid4().hex}, "recursion_limit": RECURSION_LIMIT}


def _print_banner() -> None:
    """Affiche l'ASCII art du dossier racine (ascii-art.txt) en dégradé vertical."""
    art = Path(__file__).resolve().parent.parent / "ascii-art.txt"
    try:
        lines = art.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return  # pas de bannière : on démarre quand même
    non_empty = [i for i, l in enumerate(lines) if l.strip()]
    if not non_empty:
        return
    # Dégradé vertical : couleurs de départ et de fin (RGB). Modifie ici pour changer le rendu.
    start, end = (0x3D, 0xF0, 0xFF), (0xD1, 0x6B, 0xFF)  # cyan -> magenta
    first, last = non_empty[0], non_empty[-1]
    span = max(1, last - first)
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        t = (i - first) / span
        r = round(start[0] + (end[0] - start[0]) * t)
        g = round(start[1] + (end[1] - start[1]) * t)
        b = round(start[2] + (end[2] - start[2]) * t)
        console.print(Text(line, style=f"#{r:02x}{g:02x}{b:02x}"))


async def main() -> int:
    ap = argparse.ArgumentParser(description="Assistant de code multi-agents (superviseur + codeur).")
    ap.add_argument("--auto", action="store_true", help="mode auto : approuve les actions sûres sans question (voir autopilot.py)")
    ap.add_argument("--resume", action="store_true", help="reprend la dernière session de ce dossier")
    args = ap.parse_args()

    global ACTIVE_SETTINGS
    settings = ACTIVE_SETTINGS = load_settings()
    if bad := settings.placeholder_fields():
        console.print(f"[red]Configuration incomplète :[/red] {', '.join(bad)} (voir config.py : LLM_API_KEY ou CEGEDIM_LLM_API_KEY).")
        return 2

    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    db = STATE_DIR / "sessions.db"
    async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
        os.chmod(db, 0o600)  # les sessions contiennent du code et des sorties de commandes
        await saver.conn.execute("PRAGMA journal_mode=WAL")  # checkpoints sans fsync bloquant
        await saver.conn.execute("PRAGMA synchronous=NORMAL")
        mcp_tools, mcp_allowed, mcp_report = await load_mcp_tools()
        register_extra_tools(mcp_tools, mcp_allowed)
        graph = build_graph(settings, checkpointer=saver)
        session: PromptSession = PromptSession(history=FileHistory(os.path.expanduser("~/.claude_clone_history")))
        auto = args.auto
        mode = DEFAULT_MODE
        allow: set = set()  # autorisations « toujours » (valables pour la session en cours)

        config = _new_config(_threads().get(tools_mod.WORKDIR) if args.resume else None)
        _remember_thread(config["configurable"]["thread_id"])
        resumed = ""
        if args.resume:
            n = len((await graph.aget_state(config)).values.get("messages", []))
            resumed = f"session reprise ({n} messages)" if n else "aucune session à reprendre : nouvelle session"

        for line in mcp_report:
            console.print(Text(line, style="dim" if "indisponible" not in line and "illisible" not in line else "yellow"))
        sk, sk_warns = load_skills()
        for w in sk_warns:
            console.print(Text(f"⚠ {w}", style="yellow"))
        mem_files = load_project_memory()[1]
        memory = f" · mémoire : {', '.join(mem_files)}" if mem_files else ""
        auto_note = "\n[bold green]MODE AUTO[/bold green] : actions sûres approuvées sans question (/auto pour désactiver)" if auto else ""
        sandbox = "bash isolé ($HOME masqué)" if tools_mod.sandbox_enabled() else "[yellow]bash NON isolé[/yellow]"
        _print_banner()
        console.print(Panel.fit(
            f"[bold]claude-clone[/bold]  ·  superviseur [cyan]{settings.supervisor_model}[/cyan]  ·  codeur [green]{settings.coder_model}[/green]\n"
            f"dossier de travail : {tools_mod.WORKDIR}\n{sandbox}{memory}{auto_note}\nposture : {mode} · skills : {len(sk)}" + (f" · {resumed}" if resumed else "") + f"\n[dim]{HELP}[/dim]",
            border_style="blue",
        ))

        while True:
            try:
                line = (await session.prompt_async(HTML("<b><ansigreen>⚡❯ </ansigreen></b>") if auto else HTML("<b><ansicyan>❯ </ansicyan></b>"))).strip()
            except KeyboardInterrupt:
                continue  # annule la saisie en cours
            except EOFError:
                break
            if not line:
                continue
            if line in ("/exit", "/quit"):
                break
            if line == "/help":
                console.print(HELP, style="dim")
                continue
            if line == "/auto":
                auto = not auto
                console.print(Text("Mode auto " + ("ACTIVÉ : écritures dans le dossier (hors fichiers sensibles) et commandes de la liste blanche approuvées sans question ; le reste demande toujours confirmation." if auto else "désactivé : confirmation pour chaque action sensible."), style="green" if auto else "dim"))
                continue
            if line.startswith("/mode"):
                choice = line.split(maxsplit=1)[1].strip() if " " in line else ""
                if choice in MODES:
                    mode = choice
                    console.print(Text(f"Posture : {mode} " + ("(challenge les décisions structurantes avant de coder)" if mode == "challenge" else "(exécute sans phase de challenge)"), style="green"))
                else:
                    console.print(Text(f"Posture actuelle : {mode}. Usage : /mode challenge | /mode direct", style="dim"))
                continue
            if line == "/skills":
                skills, warns = load_skills()
                for sk in skills.values():
                    console.print(Text(f"{sk.name}", style="bold cyan"), Text(f"({'intégré' if sk.builtin else 'utilisateur'}) {sk.description}", style="dim"))
                for w in warns:
                    console.print(Text(f"⚠ {w}", style="yellow"))
                console.print(Text("Usage : /skill NOM [précisions] — ou demande simplement en langage naturel.", style="dim"))
                continue
            if line.startswith("/skill "):
                parts = line.split(maxsplit=2)
                skills, _ = load_skills()
                if parts[1] not in skills:
                    console.print(Text(f"Skill inconnu : {parts[1]}. Disponibles : {', '.join(skills) or 'aucun'}", style="yellow"))
                    continue
                line = f"Applique le skill « {parts[1]} »" + (f" — {parts[2]}" if len(parts) > 2 else ".")  # puis tour normal
            if line == "/context":
                console.print(Text("Contexte " + format_context(context_info((await graph.aget_state(config)).values, settings)), style="cyan"))
                continue
            if line == "/summary":
                summary = (await graph.aget_state(config)).values.get("summary")
                console.print(Panel(Markdown(summary), title="Résumé (remplace l'ancien historique)", border_style="cyan") if summary else Text("Aucun résumé : l'historique n'a pas encore été compacté.", style="dim"))
                continue
            if line.startswith("/compact"):
                focus = line[len("/compact"):].strip()
                with console.status("Compaction…", spinner="dots"):
                    res = await graph.compact_now(config, focus)
                console.print(Text(("🗜 " if res["ok"] else "⚠ ") + res["message"], style="green" if res["ok"] else "yellow"))
                continue
            if line == "/doctor":
                from claude_clone.doctor import check_models
                with console.status("Test des modèles…", spinner="dots"):
                    report = await check_models(settings)
                console.print(Text(report))
                continue
            if line == "/stats":
                console.print(Text(TELEMETRY.summary() or "Rien à afficher pour l'instant.", style="dim"))
                continue
            if line == "/reset":
                config = _new_config()
                _remember_thread(config["configurable"]["thread_id"])
                allow.clear()
                console.print("Conversation réinitialisée.", style="dim")
                continue
            config["configurable"]["mode"] = mode
            await run_turn(graph, session, config, line, allow, auto)
    console.print("À bientôt.", style="dim")
    return 0


def cli() -> None:
    """Point d'entrée de la commande `cc`."""
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    cli()
