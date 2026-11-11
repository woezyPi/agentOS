"""Télémétrie locale : tokens et durées par appel LLM / outil, journal JSONL.

Rien ne quitte la machine. Le journal (~/.claude_clone/logs/AAAA-MM-JJ.jsonl, droits 600) ne contient
que des métadonnées (modèle, tokens, durée, nom d'outil, statut) — jamais de contenu ni d'arguments.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.callbacks import AsyncCallbackHandler
from claude_clone.failover import reported_model

LOG_DIR = Path.home() / ".claude_clone" / "logs"


class Telemetry:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def _log(self, entry: dict) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
        self.records.append(entry)
        try:  # la télémétrie ne doit jamais casser un tour
            LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = LOG_DIR / f"{datetime.now():%Y-%m-%d}.jsonl"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def record_llm(self, role: str, model: str, tokens_in: int | None, tokens_out: int | None, ms: float, error: str | None = None, served_model: str | None = None) -> None:
        self._log({"kind": "llm", "role": role, "model": model, "in": tokens_in, "out": tokens_out, "ms": round(ms), "error": error, "served_model": served_model})

    def record_tool(self, name: str, status: str, ms: float) -> None:
        self._log({"kind": "tool", "name": name, "status": status, "ms": round(ms)})

    def mark(self) -> int:
        return len(self.records)

    def summary(self, since: int = 0) -> str:
        recs = self.records[since:]
        llm = [r for r in recs if r["kind"] == "llm"]
        tools = [r for r in recs if r["kind"] == "tool"]
        if not llm and not tools:
            return ""
        known = [r for r in llm if r["in"] is not None]
        tok = f"{sum(r['in'] for r in known):,} in / {sum(r['out'] or 0 for r in known):,} out tokens".replace(",", " ") if known else "tokens n/d"
        by_role: dict[str, int] = {}
        for r in llm:
            by_role[r["role"]] = by_role.get(r["role"], 0) + 1
        roles = ", ".join(f"{k} {v}" for k, v in by_role.items())
        secs = sum(r["ms"] for r in llm + tools) / 1000
        errs = sum(1 for r in llm if r.get("error")) + sum(1 for r in tools if r["status"] == "error")
        return (f"⏱ {secs:.1f} s · {len(llm)} appel(s) LLM ({roles}) · {tok} · {len(tools)} outil(s)"
                + (f" · {errs} erreur(s)" if errs else ""))


TELEMETRY = Telemetry()


class UsageCallback(AsyncCallbackHandler):
    """Mesure chaque appel d'un modèle (y compris ceux faits depuis l'intérieur d'un noeud)."""
    raise_error = False

    def __init__(self, role: str, model: str) -> None:
        self.role, self.model, self._t0 = role, model, {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        self._t0[run_id] = time.monotonic()

    async def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        ms = (time.monotonic() - self._t0.pop(run_id, time.monotonic())) * 1000
        usage = None
        try:
            usage = response.generations[0][0].message.usage_metadata
        except (AttributeError, IndexError):
            pass
        served_model = None
        try:
            served_model = reported_model(response.generations[0][0].message.response_metadata)
        except (AttributeError, IndexError):
            pass
        TELEMETRY.record_llm(self.role, self.model, (usage or {}).get("input_tokens"), (usage or {}).get("output_tokens"), ms, served_model=served_model)

    async def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        ms = (time.monotonic() - self._t0.pop(run_id, time.monotonic())) * 1000
        TELEMETRY.record_llm(self.role, self.model, None, None, ms, error=type(error).__name__)
