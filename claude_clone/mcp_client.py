"""Client MCP : charge les outils de serveurs MCP déclarés dans ~/.claude_clone/mcp.json (fichier GLOBAL uniquement).

Volontairement pas de mcp.json dans le dossier de travail : un serveur « stdio » est une commande lancée sur
votre machine, un dépôt tiers ne doit pas pouvoir en déclarer. Format :

  {"servers": {
     "monserveur": {"command": "npx", "args": ["-y", "@x/serveur"], "env": {"TOKEN": "${MON_TOKEN}"},
                    "allow": ["lecture_seule_1"]},                      # outils sans confirmation (défaut : aucun)
     "distant":    {"url": "https://exemple/mcp", "headers": {"Authorization": "Bearer ${TOK}"}}
  }}

${VAR} est remplacé par la variable d'environnement (les secrets restent hors du fichier). Les outils sont
exposés sous le nom mcp__<serveur>__<outil> et demandent TOUS confirmation, sauf ceux listés dans « allow ».
Leurs résultats sont des données externes : non fiables.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude_clone" / "mcp.json"


def _expand(value):
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m[1], ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def to_connection(spec: dict) -> dict:
    spec = _expand(spec)
    if spec.get("command"):
        conn = {"transport": "stdio", "command": spec["command"], "args": list(spec.get("args", []))}
        if spec.get("env"):
            conn["env"] = {k: str(v) for k, v in spec["env"].items()}
        return conn
    if spec.get("url"):
        conn = {"transport": spec.get("transport", "streamable_http"), "url": spec["url"]}
        if spec.get("headers"):
            conn["headers"] = spec["headers"]
        return conn
    raise ValueError("il faut « command » (stdio) ou « url » (http)")


async def load_mcp_tools(config_path: Path | None = None, timeout: float = 25.0):
    """Renvoie (outils, noms autorisés sans confirmation, lignes de rapport). Ne lève jamais : un serveur HS est signalé."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    path = config_path or CONFIG_PATH
    if not path.is_file():
        return [], set(), []
    try:
        servers = json.loads(path.read_text(encoding="utf-8")).get("servers", {})
    except (OSError, ValueError) as exc:
        return [], set(), [f"mcp.json illisible : {exc}"]
    tools, allowed, report = [], set(), []
    for server, spec in servers.items():
        try:
            client = MultiServerMCPClient({server: to_connection(spec)})
            server_tools = await asyncio.wait_for(client.get_tools(), timeout)
        except Exception as exc:
            report.append(f"MCP « {server} » indisponible : {type(exc).__name__}: {str(exc)[:120]}")
            continue
        names = []
        for t in server_tools:
            original = t.name
            t.name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp__{server}__{original}")[:64]  # limite des API : 64 car.
            if original in spec.get("allow", []):
                allowed.add(t.name)
            names.append(t.name)
        tools.extend(server_tools)
        report.append(f"MCP « {server} » : {len(server_tools)} outil(s)")
    return tools, allowed, report
