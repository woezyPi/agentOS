"""Serveur web local : sert l'interface (static/index.html) et expose le graphe d'agents.

SÉCURITÉ — cette interface pilote bash et des écritures de fichiers sur VOTRE machine :
  * écoute sur 127.0.0.1 uniquement (par défaut) ;
  * jeton aléatoire par démarrage (en-tête X-CC-Token), transmis dans le FRAGMENT de l'URL affichée au lancement
    (le fragment n'est jamais envoyé au serveur ni journalisé) ;
  * en-tête Host vérifié (anti DNS-rebinding) et Origin vérifié sur les requêtes qui modifient (anti CSRF) ;
    pas de CORS : l'interface est servie par le serveur lui-même (même origine) ;
  * le client n'envoie JAMAIS le contenu à approuver : il renvoie une décision par identifiant d'appel, le serveur
    relit ce qui est réellement en attente dans l'état du graphe ;
  * CSP stricte, rendu des messages en textContent côté interface.
Protocole : NDJSON (une ligne JSON par événement) sur POST /api/chat et /api/resume.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from claude_clone import tools as T
from claude_clone import agent_tools as AT
from claude_clone import config as CFG
from claude_clone import models as MD
from claude_clone import gateway as GW
from claude_clone import skills as SK
from claude_clone import web as WEB
from claude_clone.agents import PERMISSIONS, READONLY_TOOLS, delete_user_agent, get_roles, load_custom_roles, role_temperature, save_user_agent
from claude_clone.doctor import configured_models
from claude_clone.research import RESEARCH_PROMPT
from claude_clone.autopilot import _protected, classify
from claude_clone.config import RECURSION_LIMIT, load_settings
from claude_clone.context import context_info
from claude_clone.graph import as_text, build_graph
from claude_clone.main import BASH_SAFE_KEY, _allow_key, repair_dangling_tool_calls
from claude_clone.previews import preview
from claude_clone.skills import delete_user_skill, load_skills, save_user_skill, user_skill_names
from claude_clone.telemetry import TELEMETRY

STATIC_INDEX = Path(__file__).parent / "static" / "index.html"
THREAD_ID = r"^[A-Za-z0-9-]{8,64}$"
MAX_MESSAGE = 50_000
BUSY_STALE = 900  # s : un verrou plus vieux est considéré comme périmé (client parti sans fermer proprement)
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{1,40}$"
AGENT_PATTERN = r"^(auto|[a-z0-9][a-z0-9-]{1,40})$"
STATUS_TEXT = {"coder": "Agent Codeur : génération…", "agents": "Sous-agents : travail en cours…", "tools": "Outils / sous-agents : en cours…"}
CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class ChatIn(BaseModel):
    thread_id: str = Field(pattern=THREAD_ID)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    agent: str = Field(default="auto", pattern=AGENT_PATTERN)
    mode: Literal["challenge", "direct"] = "challenge"
    delegation: Literal["minimal", "balanced", "max"] = "balanced"
    auto: bool = False


class SkillIn(BaseModel):
    description: str = Field(min_length=1, max_length=400)
    tools: list[str] = Field(default_factory=list, max_length=10)
    body: str = Field(min_length=1, max_length=30_000)


class AgentIn(BaseModel):
    title: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=300)
    model: str = Field(default="", max_length=100)
    tools: list[str] = Field(default_factory=list, max_length=12)
    max_steps: int = Field(default=8, ge=1, le=16)
    temperature: float | None = Field(default=None, ge=0, le=1)
    prompt: str = Field(min_length=1, max_length=8000)


def check_agent(agent: str) -> None:
    if agent != "auto" and agent not in get_roles():
        raise HTTPException(422, f"Agent inconnu : {agent}")


class ModelsIn(BaseModel):
    assignments: dict[str, str] = Field(default_factory=dict, max_length=16)
    panel: list[str] | None = Field(default=None, max_length=4)


class ModelsResetIn(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=20)


class OpenIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    line: int = Field(default=0, ge=0, le=10_000_000)


EDITORS = ("code", "code-insiders", "cursor", "codium")  # CC_EDITOR ne peut valoir que l'un d'eux (jamais une commande arbitraire)


def _resolve_for_editor(path: str) -> str:
    """Même périmètre que le visionneur : dossier de travail, jamais de fichier sensible ; un nom seul doit être unique."""
    try:
        real = T.safe_path(path)
    except Exception:
        raise HTTPException(403, "Chemin hors du dossier de travail")
    if not os.path.isfile(real):
        if "/" in path.strip("/"):
            raise HTTPException(404, f"Fichier introuvable : {path}")
        cands = _by_basename(os.path.basename(path))
        if len(cands) != 1:
            raise HTTPException(404 if not cands else 409, f"Fichier introuvable : {path}" if not cands else f"« {path} » existe à plusieurs endroits : donne le chemin complet.")
        real = T.safe_path(cands[0])
    if _protected(os.path.relpath(real, T.get_workdir())):
        raise HTTPException(403, "Fichier sensible : non ouvert par l'interface")
    return real


class CompactIn(BaseModel):
    focus: str = Field(default="", max_length=500)


class ResumeIn(BaseModel):
    thread_id: str = Field(pattern=THREAD_ID)
    decisions: dict[str, Literal["approve", "deny", "always"]]
    agent: str = Field(default="auto", pattern=AGENT_PATTERN)
    mode: Literal["challenge", "direct"] = "challenge"
    delegation: Literal["minimal", "balanced", "max"] = "balanced"
    auto: bool = False


def _short(args: dict, limit: int = 100) -> str:
    return ", ".join(f"{k}={str(v).replace(chr(10), '⏎')[:limit]}" for k, v in (args or {}).items())


def triage(calls: list[dict], values: dict, allow: set, auto: bool):
    """Sépare les appels sensibles : (auto-approuvés [(tc, raison)], ids bash à lancer hors réseau, appels à soumettre à l'utilisateur)."""
    auto_ok, offline, needs = [], [], []
    for tc in calls:
        if not T.requires_approval(tc["name"]):
            continue
        safe_bash = tc["name"] == "execute_bash" and BASH_SAFE_KEY in allow
        if auto or safe_bash:
            ok, why = classify(tc)
            if ok:
                auto_ok.append((tc, why if auto else "commande sûre autorisée pour la session"))
                if tc["name"] == "execute_bash":
                    offline.append(tc["id"])
                continue
        if _allow_key(tc) in allow:
            auto_ok.append((tc, "autorisé pour la session"))
            continue
        needs.append(tc)
    return auto_ok, offline, needs


def _call_card(tc: dict, values: dict, auto: bool) -> dict:
    """Carte de confirmation d'un appel ; en mode auto, dit POURQUOI une confirmation est quand même requise."""
    card = {"id": tc["id"], "name": tc["name"], "summary": _short(tc["args"], 80), "preview": preview(tc, values)}
    if auto:
        card["reason"] = classify(tc)[1]
    return card


def expected_model(settings, key, panel: bool = False) -> str:
    """Nom COURT du modèle qui va servir un rôle (sans bascule éventuelle)."""
    if settings is None or not key:
        return ""
    if panel:
        return f"{len(settings.panel_models)} modèles"
    roles = get_roles()
    if key == "coder":
        m = settings.coder_model
    elif key == "researcher":
        m = settings.research_model
    elif key == "worker":
        m = settings.worker_model
    elif key in roles:
        m = roles[key].model or settings.agent_models.get(key) or settings.agent_models.get("reviewer", "")
    else:
        return ""
    if getattr(settings, "free_only", False) and m not in {r["alias"] for r in GW.read_config()["routes"]}:
        m = settings.worker_model
    return m.split("/")[-1]


def status_text(node: str, calls: list | None, settings) -> str:
    """« En cours : Reviewer · qwen3.6-27b | Chercheur · glm-5.3-flash » : QUI travaille, avec quel modèle (sinon texte générique)."""
    parts = []
    for tc in calls or []:
        name, a = tc["name"], tc.get("args") or {}
        if name == T.ASK_AGENT_TOOL_NAME:
            key, panel = a.get("role"), bool(a.get("panel"))
        elif name == T.RESEARCH_TOOL_NAME:
            key, panel = "researcher", False
        elif name == T.DELEGATE_TOOL_NAME:
            key, panel = "coder", False
        elif name == T.WORKERS_TOOL_NAME:
            parts.append(f"Workers ×{len(a.get('tasks') or [])} · {expected_model(settings, 'worker')}")
            continue
        else:
            parts.append(name)
            continue
        roles = get_roles()
        title = "Agent Codeur" if key == "coder" else (roles[key].title if key in roles else str(key))
        model = expected_model(settings, key, panel)
        parts.append(title + (" (panel)" if panel else "") + (f" · {model}" if model else ""))
    return ("En cours : " + " | ".join(parts)) if parts else STATUS_TEXT[node]


def node_events(node: str, update: dict | None, streamed_text: bool, model_of=None) -> list[dict]:
    """Traduit la sortie d'un noeud du graphe en événements pour l'interface."""
    out: list[dict] = []
    if not update:
        return out
    for err in update.get("errors") or []:
        out.append({"type": "notice", "level": "info" if err.startswith(("Contexte compacté", "↪")) else "warn", "text": err})
    if update.get("plan") is not None:
        out.append({"type": "plan", "todos": update["plan"]})
    for msg in update.get("messages") or []:
        if isinstance(msg, AIMessage):
            out.append({"type": "ai", "text": as_text(msg.content).strip(), "streamed": streamed_text})
            for tc in msg.tool_calls:
                agent = tc["args"].get("role") if tc["name"] == T.ASK_AGENT_TOOL_NAME else ("researcher" if tc["name"] == T.RESEARCH_TOOL_NAME else ("worker" if tc["name"] == T.WORKERS_TOOL_NAME else None))
                out.append({"type": "tool_call", "id": tc["id"], "name": tc["name"], "summary": (" ".join(str(tc["args"].get("task") or "").split())[:300] or _short(tc["args"])) if tc["name"] == T.ASK_AGENT_TOOL_NAME else _short(tc["args"]), "agent": agent, "panel": bool(tc["args"].get("panel")),
                        "model": model_of(agent, bool(tc["args"].get("panel"))) if (model_of and agent) else None})
        elif isinstance(msg, ToolMessage):
            role = msg.additional_kwargs.get("role") or ("coder" if node == "coder" else None)
            text = as_text(msg.content)
            limit = 12000 if role else 3000
            out.append({"type": "tool_result", "id": msg.tool_call_id, "name": msg.name, "status": msg.status, "agent": role, "panel": bool(msg.additional_kwargs.get("panel")), "model": (msg.additional_kwargs.get("model") or "").split("/")[-1] or None,
                        "text": text[:limit], "truncated": len(text) > limit})
    return out


def used_models(mark: int) -> list[str]:
    """Modèles qui ont réellement répondu pendant ce tour, ex. « superviseur: deepseek-v4-flash-0731 »."""
    seen: dict = {}
    for r in TELEMETRY.records[mark:]:
        if r["kind"] == "llm" and not r.get("error"):
            seen[(r["role"], (r.get("served_model") or r["model"]).split("/")[-1])] = None
    return [f"{role}: {model}" for role, model in seen][:8]


MAX_VIEW = 400_000  # octets affichés au maximum dans le visionneur de fichier


def _by_basename(name: str) -> list[str]:
    """Fichiers du projet portant ce nom, quand le chemin donné est sans dossier (ex. « api.py:42 » écrit par un modèle)."""
    found, seen, base = [], 0, T.get_workdir()
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in T.SKIP_DIRS]
        seen += len(filenames)
        if name in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, name), base))
        if seen > 8000 or len(found) >= 12:
            break
    return found


def _editor_uri(real: str, line: int) -> str:
    """Lien « ouvrir dans VS Code » ; sous WSL il passe par l'extension distante."""
    where = f"{real}:{line}" if line else real
    distro = os.environ.get("WSL_DISTRO_NAME")
    return f"vscode://vscode-remote/wsl+{distro}{where}" if distro else f"vscode://file{where}"


class Runtime:
    def __init__(self, graph=None, meta: dict | None = None):
        self.graph, self.meta, self.settings = graph, meta or {}, None
        self.gateway_changing = False
        self.rebuild = None  # callable(settings) → graphe ; défini au démarrage réel (pas dans les tests à graphe injecté)
        self.busy: dict[str, float] = {}
        self.allow: dict[str, set] = {}

    def reload(self) -> None:
        """Relit la config (modèles choisis dans l'interface) et reconstruit le graphe ; les tours en cours gardent l'ancien."""
        self.settings = CFG.load_settings()
        if "models" in self.meta:
            self.meta["models"] = {"superviseur": self.settings.supervisor_model, "codeur": self.settings.coder_model}
        if self.rebuild is not None:
            self.graph = self.rebuild(self.settings)

    def take(self, tid: str) -> bool:
        if time.monotonic() - self.busy.get(tid, -BUSY_STALE - 1) < BUSY_STALE:
            return False
        self.busy[tid] = time.monotonic()
        return True


async def advance(rt: Runtime, tid: str, inputs, *, auto: bool, mode: str, agent: str, decisions: dict | None = None, delegation: str = "balanced"):
    """Générateur d'événements : fait avancer le graphe, applique les confirmations, s'arrête quand l'utilisateur doit décider.

    Boucle : (1) si on reprend après une pause (ou si tout est auto-approuvé), poser les décisions dans la config ;
    (2) faire tourner le graphe jusqu'à sa fin ou jusqu'à l'interruption avant « tools » ; (3) trier les appels sensibles :
    rien à demander → on enchaîne ; sinon on émet « approval » et le tour se termine (l'interface rappellera /api/resume)."""
    graph = rt.graph
    cfg = {"configurable": {"thread_id": tid, "mode": mode, "delegation": delegation, "agent_hint": "" if agent == "auto" else agent}, "recursion_limit": RECURSION_LIMIT}
    allow = rt.allow.setdefault(tid, set())
    mark = TELEMETRY.mark()
    supervisor_model = None
    enc = lambda ev: json.dumps(ev, ensure_ascii=False) + "\n"
    try:
        yield enc({"type": "start", "thread_id": tid})
        if inputs is not None:
            await repair_dangling_tool_calls(graph, cfg)
        while True:
            # (1) décisions à appliquer avant de reprendre le graphe
            if decisions is not None:
                snap = await graph.aget_state(cfg)
                calls = snap.values["messages"][-1].tool_calls
                auto_ok, offline, needs = triage(calls, snap.values, allow, auto)
                for tc, why in auto_ok:
                    yield enc({"type": "auto", "id": tc["id"], "name": tc["name"], "summary": _short(tc["args"], 70), "why": why,
                               "preview": preview(tc, snap.values) if tc["name"] in ("write_file", "edit_file") else None})  # le diff, AVANT que le fichier change
                denied = []
                for tc in needs:
                    d = decisions.get(tc["id"], "deny")  # sans décision explicite : refus
                    if d == "deny":
                        denied.append(tc["id"])
                    elif d == "always":
                        if tc["name"] == "execute_bash" and classify(tc)[0]:
                            allow.add(BASH_SAFE_KEY)
                            msg = "✓ TOUJOURS : désormais, toutes les commandes de la liste blanche (lecture, python, pytest, git local…) passent sans question, hors réseau — jusqu'à la fin de la session."
                        elif tc["name"] == "execute_bash":
                            allow.add(_allow_key(tc))
                            msg = "✓ TOUJOURS : cette commande EXACTE ne sera plus demandée. Les autres commandes hors liste blanche demanderont toujours confirmation."
                        else:
                            allow.add(_allow_key(tc))
                            msg = f"✓ TOUJOURS : {tc['name']} sur ce même fichier ne sera plus demandé."
                        yield enc({"type": "notice", "level": "info", "text": msg})
                cfg["configurable"]["denied_call_ids"] = denied
                cfg["configurable"]["offline_call_ids"] = offline
                decisions = None
            # (2) exécution
            streamed, announced, last_calls = False, set(), []
            async for kind, data in graph.astream(inputs, cfg, stream_mode=["updates", "messages", "custom"]):
                if kind == "custom":
                    if isinstance(data, dict) and data.get("type") in ("agent_step", "agent_start", "agent_done"):
                        yield enc(data)
                    continue
                if kind == "messages":
                    chunk, meta = data
                    node = meta.get("langgraph_node")
                    text = as_text(chunk.content) if isinstance(chunk, AIMessageChunk) else ""
                    aid = next((t[6:] for t in (meta.get("tags") or []) if isinstance(t, str) and t.startswith("agent:")), None)
                    if aid:  # jetons d'un sous-agent / worker / relecteur : vers SA carte, en direct
                        if text:
                            yield enc({"type": "agent_token", "id": aid, "text": text})
                        continue
                    if node == "supervisor" and text:
                        streamed = True
                        yield enc({"type": "token", "text": text})
                    elif node in STATUS_TEXT and node not in announced:
                        announced.add(node)
                        yield enc({"type": "status", "text": status_text(node, last_calls, rt.settings)})
                    continue
                for node, update in data.items():
                    if node.startswith("__"):
                        continue
                    if node == "supervisor":
                        for message in (update or {}).get("messages", []):
                            if isinstance(message, AIMessage):
                                supervisor_model = message.response_metadata.get("model_name") or supervisor_model
                        last_calls = [tc for m in (update or {}).get("messages", []) if isinstance(m, AIMessage) for tc in m.tool_calls] or last_calls
                    for ev in node_events(node, update, streamed and node == "supervisor", lambda role, panel=False: expected_model(rt.settings, role, panel)):
                        yield enc(ev)
                    if node == "supervisor":
                        streamed = False
                    announced.discard(node)
            inputs = None
            cfg["configurable"].pop("denied_call_ids", None)
            cfg["configurable"].pop("offline_call_ids", None)
            # (3) tri
            snap = await graph.aget_state(cfg)
            if not snap.next:
                break
            calls = snap.values["messages"][-1].tool_calls
            _, _, needs = triage(calls, snap.values, allow, auto)
            if needs:
                yield enc({"type": "approval", "thread_id": tid, "calls": [_call_card(tc, snap.values, auto) for tc in needs]})
                yield enc({"type": "done", "usage": TELEMETRY.summary(mark), "waiting": True, "context": context_info(snap.values, rt.settings), "models": used_models(mark), "supervisor_model": supervisor_model})
                return
            decisions = {}  # tout est auto-approuvé (ou ne demande rien) : on enchaîne, les décisions seront posées en (1)
        yield enc({"type": "done", "usage": TELEMETRY.summary(mark), "waiting": False, "context": context_info((await graph.aget_state(cfg)).values, rt.settings), "models": used_models(mark), "supervisor_model": supervisor_model})
    except asyncio.CancelledError:
        raise
    except GraphRecursionError:
        yield enc({"type": "error", "text": f"Limite de {RECURSION_LIMIT} étapes atteinte : boucle probable, tour arrêté."})
    except Exception as exc:  # le serveur ne doit pas mourir sur une erreur de tour
        yield enc({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
    finally:
        rt.busy.pop(tid, None)


def create_app(*, token: str, hosts: set[str], graph=None, settings=None) -> FastAPI:
    rt = Runtime(graph)
    rt.settings = settings
    hosts = {h.lower() for h in hosts}
    origins = {f"http://{h}" for h in hosts}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if rt.graph is not None:  # graphe injecté (tests)
            yield
            return
        if GW.enabled():
            await asyncio.to_thread(GW.start)
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from claude_clone.mcp_client import load_mcp_tools
        state_dir = Path.home() / ".claude_clone"
        state_dir.mkdir(mode=0o700, exist_ok=True)
        db = state_dir / "sessions.db"
        async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
            os.chmod(db, 0o600)
            await saver.conn.execute("PRAGMA journal_mode=WAL")        # écritures de checkpoint sans fsync bloquant
            await saver.conn.execute("PRAGMA synchronous=NORMAL")
            tools, allowed, report = await load_mcp_tools()
            T.register_extra_tools(tools, allowed)
            rt.graph = build_graph(settings, checkpointer=saver)
            rt.rebuild = lambda st: build_graph(st, checkpointer=saver)
            rt.meta = {"mcp": report, "models": {"superviseur": settings.supervisor_model, "codeur": settings.coder_model}}
            yield

    async def guard_host(request: Request):
        if request.headers.get("host", "").lower() not in hosts:
            raise HTTPException(403, "Hôte non autorisé")
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD") and origin and origin.lower() not in origins:
            raise HTTPException(403, "Origine non autorisée")

    async def guard_token(request: Request):
        if not hmac.compare_digest(request.headers.get("x-cc-token", ""), token):
            raise HTTPException(401, "Jeton invalide ou absent")

    app = FastAPI(title="claude-clone", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    @app.exception_handler(RequestValidationError)
    async def redact_gateway_validation(request: Request, exc: RequestValidationError):
        if request.url.path == "/api/gateway":
            return JSONResponse(status_code=422, content={"detail": [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]})
        return await request_validation_exception_handler(request, exc)

    api = dict(dependencies=[Depends(guard_token)])
    nostore = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}

    @app.middleware("http")
    async def protect_requests(request: Request, call_next):
        # Les dépendances FastAPI ne protègent pas les applications montées (StaticFiles).
        try:
            await guard_host(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=nostore)
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers.update(nostore)
        return response

    app.mount("/static", StaticFiles(directory=STATIC_INDEX.parent, check_dir=True), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        try:
            html = STATIC_INDEX.read_text(encoding="utf-8")
        except OSError:
            raise HTTPException(500, "Interface introuvable")
        return HTMLResponse(html, headers={**nostore, "Content-Security-Policy": CSP, "X-Frame-Options": "DENY"})

    tool_objs = {t.name: t for t in (*[T.LOCAL_TOOLS_BY_NAME[n] for n in ("read_file", "grep", "glob")], AT.git_diff, WEB.fetch_url, WEB.latest_version,
                                     AT.sql_describe, AT.sql_query, AT.run_check)}

    def model_metadata():
        st = rt.settings
        if st is None:
            return {"role_models": {}, "model_routes": {}}
        models = {"supervisor": st.supervisor_model, "coder": st.coder_model, "researcher": st.research_model, "worker": st.worker_model, **st.agent_models}
        for key, role in get_roles().items():
            if role.model:
                known_aliases = {r["alias"] for r in GW.read_config()["routes"]} if st.free_only else None
                models[key] = st.worker_model if known_aliases is not None and role.model not in known_aliases else role.model
        routes = {r["alias"]: {"provider": r["provider"], "model": r["model"], "fallback": r.get("fallback")} for r in GW.read_config()["routes"]} if st.managed_gateway else {}
        return {"role_models": models, "model_routes": routes, "free_only": st.free_only}

    @app.get("/api/meta", **api)
    async def meta():
        return JSONResponse({**model_metadata(), "setup_required": bool(rt.settings and rt.settings.placeholder_fields()), "workdir": T.WORKDIR, "sandbox": T.sandbox_enabled(), "modes": ["challenge", "direct"],
                             "roles": [{"key": k, "title": r.title, "builtin": r.builtin} for k, r in get_roles().items()],
                             "readonly_tools": [{"name": n, "description": (tool_objs[n].description or "").strip().splitlines()[0][:110]} for n in READONLY_TOOLS if n in tool_objs],
                             "models": list(configured_models(rt.settings)) if rt.settings else [], **rt.meta}, headers=nostore)

    def stream(gen):
        return StreamingResponse(gen, media_type="application/x-ndjson", headers={**nostore, "X-Accel-Buffering": "no"})

    @app.post("/api/chat", **api)
    async def chat(body: ChatIn):
        if rt.gateway_changing:
            raise HTTPException(409, "La connexion aux fournisseurs est en cours de modification. Réessaie dans quelques instants.")
        if rt.settings and rt.settings.free_only and not GW.enabled():
            raise HTTPException(409, "Budget 0 $ : active la passerelle gratuite dans Connexions avant d’envoyer une demande.")
        if rt.settings and rt.settings.placeholder_fields():
            raise HTTPException(422, "Configure les clés et les routes dans Connexions avant d’envoyer une demande.")
        check_agent(body.agent)
        if not rt.take(body.thread_id):
            raise HTTPException(409, "Un tour est déjà en cours pour cette conversation")
        inputs = {"messages": [HumanMessage(body.message)], "retries": 0, "steps": 0}
        return stream(advance(rt, body.thread_id, inputs, auto=body.auto, mode=body.mode, agent=body.agent, delegation=body.delegation))

    @app.post("/api/resume", **api)
    async def resume(body: ResumeIn):
        check_agent(body.agent)
        cfg = {"configurable": {"thread_id": body.thread_id}}
        snap = await rt.graph.aget_state(cfg)
        if not snap.next:
            raise HTTPException(409, "Rien n'est en attente de confirmation")
        calls = snap.values["messages"][-1].tool_calls
        _, _, needs = triage(calls, snap.values, rt.allow.setdefault(body.thread_id, set()), body.auto)
        missing = {tc["id"] for tc in needs} - set(body.decisions)
        if missing:
            raise HTTPException(400, "Décision manquante pour : " + ", ".join(sorted(missing)))
        if not rt.take(body.thread_id):
            raise HTTPException(409, "Un tour est déjà en cours pour cette conversation")
        return stream(advance(rt, body.thread_id, None, auto=body.auto, mode=body.mode, agent=body.agent, decisions=body.decisions, delegation=body.delegation))

    @app.get("/api/threads/{thread_id}/pending", **api)
    async def pending(thread_id: str, auto: bool = False):
        if not re.match(THREAD_ID, thread_id):
            raise HTTPException(422, "Identifiant invalide")
        snap = await rt.graph.aget_state({"configurable": {"thread_id": thread_id}})
        if not snap.next:
            return JSONResponse({"pending": None, "waiting": False}, headers=nostore)
        calls = snap.values["messages"][-1].tool_calls
        _, _, needs = triage(calls, snap.values, rt.allow.setdefault(thread_id, set()), auto)
        # waiting=True & pending=None : le graphe attend mais plus rien ne demande l'utilisateur (ex. AUTO vient d'être activé)
        return JSONResponse({"pending": [_call_card(tc, snap.values, auto) for tc in needs] or None, "waiting": True}, headers=nostore)

    DEFAULTS = {"supervisor": CFG.DEFAULT_SUPERVISOR_MODEL, "coder": CFG.DEFAULT_CODER_MODEL, "researcher": CFG.DEFAULT_RESEARCH_MODEL, "worker": CFG.DEFAULT_WORKER_MODEL, **CFG.ROLE_DEFAULT_MODELS}

    async def models_view(force: bool = False) -> dict:
        st = rt.settings or CFG.load_settings()
        available, err = await MD.list_available(st, force)
        src, titles = CFG.model_sources(), {"supervisor": "Superviseur", "coder": "Agent Codeur", "researcher": "Chercheur", "worker": "Workers (sous-agents temporaires)", **{k: r.title for k, r in get_roles().items() if r.builtin}}
        eff = {"supervisor": st.supervisor_model, "coder": st.coder_model, "researcher": st.research_model, "worker": st.worker_model, **st.agent_models}
        fb = {"supervisor": st.supervisor_fallbacks, "coder": st.coder_fallbacks, "researcher": st.research_fallbacks}
        return {"roles": [{"key": k, "title": titles.get(k, k), "model": eff[k], "default": GW.role_aliases()[k] if st.managed_gateway else DEFAULTS[k], "source": src[k], "env": CFG.ENV_OF[k], "fallbacks": list(fb.get(k, st.agent_fallbacks))} for k in CFG.ASSIGNABLE],
                "panel": {"models": list(st.panel_models), "default": (["agent_validation", "agent_recherche"] if st.free_only else ["worker", "worker_secours"]) if st.managed_gateway else CFG.DEFAULT_PANEL_MODELS.split(","), "source": src["panel"]},
                "available": available, "error": err, "free_only": st.free_only, "routing": "litellm" if st.managed_gateway else "direct", "openrouter": urlparse(st.supervisor_base_url).hostname == "openrouter.ai"}

    @app.get("/api/gateway", **api)
    async def gateway_get():
        return JSONResponse(await asyncio.to_thread(GW.status), headers=nostore)

    async def change_gateway(operation, *args):
        if rt.busy or rt.gateway_changing:
            raise HTTPException(409, "Attends la fin des demandes en cours avant de modifier les connexions.")
        rt.gateway_changing = True
        try:
            try:
                result = await asyncio.to_thread(operation, *args)
            except ValueError as exc:
                rt.reload()
                raise HTTPException(422, str(exc)) from None
            rt.reload()
            return JSONResponse(result, headers=nostore)
        finally:
            rt.gateway_changing = False

    @app.put("/api/gateway", **api)
    async def gateway_put(body: GW.GatewayConfig):
        return await change_gateway(GW.configure, body)

    @app.post("/api/gateway/start", **api)
    async def gateway_start():
        return await change_gateway(GW.activate)

    @app.post("/api/gateway/stop", **api)
    async def gateway_stop():
        return await change_gateway(GW.deactivate)

    @app.get("/api/models", **api)
    async def models_get(refresh: bool = False):
        return JSONResponse(await models_view(refresh), headers=nostore)

    @app.get("/api/models/catalog", **api)
    async def models_catalog(source: Literal["configured", "openrouter"] = "configured", refresh: bool = False):
        if source == "openrouter":
            available, error = await MD.list_openrouter(refresh)
        else:
            available, error = await MD.list_available(rt.settings or CFG.load_settings(), refresh)
        return JSONResponse({"available": available, "error": error}, headers=nostore)

    @app.put("/api/models", **api)
    async def models_put(body: ModelsIn):
        ov = CFG.read_model_overrides()
        allowed = {r["alias"] for r in GW.read_config()["routes"]} if GW.enabled() or GW.read_config().get("free_only") else None
        for key, model in body.assignments.items():
            if key not in CFG.ASSIGNABLE:
                raise HTTPException(422, f"Rôle inconnu : {key}")
            if allowed is not None and model not in allowed:
                raise HTTPException(422, "Choisis un alias LiteLLM ou ajoute une route dans Connexions.")
            if not CFG.MODEL_ID.fullmatch(model):
                raise HTTPException(422, f"Identifiant de modèle invalide : {model}")
            ov[key] = model
        if body.panel is not None:
            if not 2 <= len(body.panel) <= 4 or len(set(body.panel)) != len(body.panel):
                raise HTTPException(422, "Le panel doit contenir 2 à 4 modèles distincts.")
            if any(not CFG.MODEL_ID.fullmatch(m) or (allowed is not None and m not in allowed) for m in body.panel):
                raise HTTPException(422, "Un identifiant de modèle du panel est invalide.")
            ov["panel"] = body.panel
        CFG.write_model_overrides(ov)
        rt.reload()
        return JSONResponse(await models_view(), headers=nostore)

    @app.post("/api/models/reset", **api)
    async def models_reset(body: ModelsResetIn | None = None):
        keys = (body.keys if body else []) or None
        ov = {} if keys is None else {k: v for k, v in CFG.read_model_overrides().items() if k not in keys}
        CFG.write_model_overrides(ov)
        rt.reload()
        return JSONResponse(await models_view(), headers=nostore)

    @app.get("/api/file", **api)
    async def file_view(path: str = Query(min_length=1, max_length=500), line: int = Query(0, ge=0, le=10_000_000)):
        """Contenu d'un fichier du projet pour le visionneur (Ctrl+clic). Même périmètre que l'outil read_file : dossier de travail,
        liens symboliques sortants refusés, fichiers sensibles (.env, clés, .git…) jamais affichés."""
        try:
            real = T.safe_path(path)
        except Exception:
            raise HTTPException(403, "Chemin hors du dossier de travail")
        if not os.path.isfile(real):
            if "/" in path.strip("/") or not os.path.basename(path):
                raise HTTPException(404, f"Fichier introuvable : {path}")
            cands = _by_basename(os.path.basename(path))
            if not cands:
                raise HTTPException(404, f"Fichier introuvable : {path}")
            if len(cands) > 1:
                return JSONResponse({"detail": f"« {path} » existe à plusieurs endroits : choisis.", "candidates": cands}, status_code=409, headers=nostore)
            real = T.safe_path(cands[0])
        rel = os.path.relpath(real, T.get_workdir())
        if _protected(rel):
            raise HTTPException(403, "Fichier sensible : non affiché par l'interface")
        with open(real, "rb") as fh:
            raw = fh.read(MAX_VIEW + 1)
        if b"\0" in raw[:4096]:
            raise HTTPException(415, "Fichier binaire : non affichable")
        text = raw[:MAX_VIEW].decode("utf-8", errors="replace")
        return JSONResponse({"path": rel, "abs": real, "text": text, "total_lines": text.count("\n") + 1, "truncated": len(raw) > MAX_VIEW,
                             "line": line, "editor": _editor_uri(real, line)}, headers=nostore)

    @app.post("/api/open-editor", **api)
    async def open_editor(body: OpenIn):
        """Ouvre le fichier dans l'éditeur (VS Code par défaut) À PARTIR DU SERVEUR : un lien vscode:// est souvent bloqué par le navigateur."""
        real = _resolve_for_editor(body.path)
        editor, uri = os.environ.get("CC_EDITOR", "code"), _editor_uri(real, body.line)
        if editor not in EDITORS:
            raise HTTPException(500, f"CC_EDITOR invalide (valeurs : {', '.join(EDITORS)})")
        exe = shutil.which(editor)
        if not exe:
            return JSONResponse({"ok": False, "detail": f"La commande « {editor} » est introuvable sur le serveur.", "fallback": uri}, status_code=501, headers=nostore)
        try:  # argv fixe, pas de shell ; le chemin est absolu (commence par « / ») donc ne peut pas passer pour une option
            subprocess.Popen([exe, "--goto", f"{real}:{body.line}" if body.line else real], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, env=T.clean_env())
        except OSError as exc:
            return JSONResponse({"ok": False, "detail": f"Lancement impossible ({type(exc).__name__})", "fallback": uri}, status_code=500, headers=nostore)
        return JSONResponse({"ok": True, "editor": editor, "file": os.path.relpath(real, T.get_workdir())}, headers=nostore)

    @app.get("/api/skills", **api)
    async def skills():
        found, warnings = load_skills()
        mine, builtin = user_skill_names(), set(SK._load_dir(SK.BUILTIN_DIR, True)[0])
        return JSONResponse({"skills": [{"name": s.name, "description": s.description, "builtin": s.builtin, "tools": list(s.tools),
                                         "editable": s.name in mine, "overrides_builtin": s.name in mine and s.name in builtin} for s in found.values()],
                             "warnings": warnings}, headers=nostore)

    @app.get("/api/skills/{name}", **api)
    async def skill_detail(name: str = PathParam(pattern=NAME_PATTERN)):
        sk = load_skills()[0].get(name)
        if sk is None:
            raise HTTPException(404, "Skill introuvable")
        return JSONResponse({"name": sk.name, "description": sk.description, "tools": list(sk.tools), "body": sk.body, "builtin": sk.builtin,
                             "editable": name in user_skill_names()}, headers=nostore)

    @app.put("/api/skills/{name}", **api)
    async def skill_save(body: SkillIn, name: str = PathParam(pattern=NAME_PATTERN)):
        try:
            sk = save_user_skill(name, body.description, body.tools, body.body)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return JSONResponse({"ok": True, "name": sk.name}, headers=nostore)

    @app.delete("/api/skills/{name}", **api)
    async def skill_delete(name: str = PathParam(pattern=NAME_PATTERN)):
        try:
            delete_user_skill(name)
        except FileNotFoundError:
            raise HTTPException(404, "Aucun skill utilisateur de ce nom (les skills intégrés ne se suppriment pas)")
        return JSONResponse({"ok": True, "restored_builtin": name in SK._load_dir(SK.BUILTIN_DIR, True)[0]}, headers=nostore)

    def agent_card(key, role) -> dict:
        agent_models = (rt.settings.agent_models if rt.settings else {}) or {}
        effective = role.model or (rt.settings.research_model if rt.settings and key == "researcher" else agent_models.get(key) or agent_models.get("reviewer", ""))
        if rt.settings and rt.settings.free_only and effective not in {r["alias"] for r in GW.read_config()["routes"]}:
            effective = rt.settings.worker_model
        return {"key": key, "title": role.title, "summary": role.summary, "description": role.description, "model": role.model, "effective_model": effective,
                "tools": list(role.tools), "max_steps": role.max_steps, "temperature": role.temperature, "effective_temperature": role.temperature if role.temperature is not None else role_temperature(key),
                "builtin": role.builtin, "editable": not role.builtin, "prompt": RESEARCH_PROMPT if key == "researcher" else role.prompt,
                "permissions": PERMISSIONS.get(key) or "Agent personnalisé, lecture seule imposée par le code : seuls les outils cochés sont disponibles."}

    @app.get("/api/agents", **api)
    async def agents():
        return JSONResponse({"agents": [agent_card(k, r) for k, r in get_roles().items()], "warnings": load_custom_roles()[1]}, headers=nostore)

    @app.put("/api/agents/{key}", **api)
    async def agent_save(body: AgentIn, key: str = PathParam(pattern=NAME_PATTERN)):
        try:
            role = save_user_agent(key, body.title, body.description, body.model, body.tools, body.max_steps, body.temperature, body.prompt)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return JSONResponse({"ok": True, "agent": agent_card(key, role)}, headers=nostore)

    @app.delete("/api/agents/{key}", **api)
    async def agent_delete(key: str = PathParam(pattern=NAME_PATTERN)):
        try:
            delete_user_agent(key)
        except FileNotFoundError:
            raise HTTPException(404, "Aucun agent personnalisé de ce nom (les agents intégrés ne se suppriment pas)")
        return JSONResponse({"ok": True}, headers=nostore)

    @app.get("/api/threads/{thread_id}/context", **api)
    async def context(thread_id: str):
        if not re.match(THREAD_ID, thread_id):
            raise HTTPException(422, "Identifiant invalide")
        snap = await rt.graph.aget_state({"configurable": {"thread_id": thread_id}})
        return JSONResponse(context_info(snap.values, rt.settings), headers=nostore)

    @app.post("/api/threads/{thread_id}/compact", **api)
    async def compact(thread_id: str, body: CompactIn | None = None):
        if not re.match(THREAD_ID, thread_id):
            raise HTTPException(422, "Identifiant invalide")
        if not rt.take(thread_id):
            raise HTTPException(409, "Un tour est en cours pour cette conversation")
        try:
            cfg = {"configurable": {"thread_id": thread_id}}
            res = await rt.graph.compact_now(cfg, (body.focus if body else "") or "")
            res["context"] = context_info((await rt.graph.aget_state(cfg)).values, rt.settings)
        finally:
            rt.busy.pop(thread_id, None)
        return JSONResponse(res, headers=nostore)

    @app.delete("/api/threads/{thread_id}", **api)
    async def delete_thread(thread_id: str):
        if not re.match(THREAD_ID, thread_id):
            raise HTTPException(422, "Identifiant invalide")
        try:
            await rt.graph.checkpointer.adelete_thread(thread_id)
        except Exception:
            pass  # suppression au mieux : l'historique local de l'interface est déjà effacé
        rt.allow.pop(thread_id, None)
        return JSONResponse({"ok": True}, headers=nostore)

    return app


TOKEN_FILE = Path.home() / ".claude_clone" / "web_token"


def load_or_create_token(path: Path = TOKEN_FILE, rotate: bool = False) -> str:
    """Jeton stable d'un démarrage à l'autre (sinon un onglet resté ouvert est invalidé à chaque relance).
    Stocké en 600 dans ~/.claude_clone ; `cc-web --new-token` le renouvelle."""
    if not rotate:
        try:
            tok = path.read_text(encoding="utf-8").strip()
            if len(tok) >= 20:
                return tok
        except OSError:
            pass
    tok = secrets.token_urlsafe(24)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(tok + "\n")
    os.chmod(path, 0o600)
    return tok


def cli() -> None:
    ap = argparse.ArgumentParser(description="Interface web de claude-clone (serveur local).")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="déconseillé hors 127.0.0.1 : l'interface pilote bash et vos fichiers")
    ap.add_argument("--open", action="store_true", help="ouvre le navigateur")
    ap.add_argument("--new-token", action="store_true", help="renouvelle le jeton (invalide les onglets ouverts)")
    args = ap.parse_args()

    import uvicorn
    from rich.console import Console
    console = Console()
    settings = load_settings()
    if bad := settings.placeholder_fields():
        console.print("[yellow]Aucun fournisseur configuré : ouvre Connexions dans l’interface pour renseigner les clés.[/yellow]")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        console.print("[bold yellow]Attention : serveur exposé hors de cette machine. Quiconque a le jeton peut exécuter des commandes et écrire des fichiers.[/bold yellow]")
    token = load_or_create_token(rotate=args.new_token)
    hosts = {f"{args.host}:{args.port}", f"localhost:{args.port}", f"127.0.0.1:{args.port}"}
    url = f"http://{args.host}:{args.port}/#token={token}"
    console.print(f"[bold]claude-clone web[/bold] · dossier de travail : {T.WORKDIR}")
    console.print("Ouvre cette URL (copie-la ENTIÈRE, sur une seule ligne) :")
    console.print(url, soft_wrap=True, highlight=False, markup=False, style="cyan")  # soft_wrap : jamais coupée à la largeur du terminal
    console.print(f"[dim]Jeton aussi dans {TOKEN_FILE} (collable dans la page si besoin) · --new-token pour le renouveler · Ctrl-C pour arrêter.[/dim]")
    if args.open:
        webbrowser.open(url)
    uvicorn.run(create_app(token=token, hosts=hosts, settings=settings), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    cli()
