"""Managed local LiteLLM proxy. Provider credentials never enter the browser response or YAML."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import BaseModel, Field, model_validator

STATE_DIR = Path.home() / ".claude_clone" / "gateway"
PORT = 4000
LOCK = threading.RLock()
_process: subprocess.Popen | None = None
PROVIDERS = {"deepseek": "DeepSeek", "openrouter": "OpenRouter", "groq": "Groq", "cerebras": "Cerebras", "ollama": "Ollama local"}
KEY_NAMES = {p: p.upper() + "_API_KEY" for p in PROVIDERS if p != "ollama"}
ROLE_ALIASES = {"supervisor": "superviseur", "coder": "superviseur", "researcher": "worker", "worker": "worker", **{r: "worker" for r in ("architect", "developer", "reviewer", "challenger", "devops", "data", "writer")}}
FREE_MODELS = {
    "code": "cohere/north-mini-code:free",
    "research": "liquid/lfm-2.5-2.6b:free",
    "writing": "google/gemma-4-31b-it:free",
    "validation": "nvidia/nemotron-3-ultra-550b-a55b:free",
}
FREE_ROLE_ALIASES = {"supervisor": "superviseur", "coder": "agent_code", "researcher": "agent_recherche", "worker": "worker",
                     "architect": "agent_validation", "developer": "agent_code", "reviewer": "agent_validation", "challenger": "agent_validation",
                     "devops": "agent_code", "data": "agent_code", "writer": "agent_redaction"}


def free_defaults() -> dict:
    models = {"superviseur": FREE_MODELS["validation"], "worker": FREE_MODELS["code"], "worker_secours": FREE_MODELS["research"],
              "agent_code": FREE_MODELS["code"], "agent_recherche": FREE_MODELS["research"], "agent_redaction": FREE_MODELS["writing"], "agent_validation": FREE_MODELS["validation"]}
    return {"enabled": False, "free_only": True, "routes": [{"alias": alias, "provider": "openrouter", "model": model,
             "fallback": "worker_secours" if alias in ("worker", "agent_code") else None} for alias, model in models.items()]}


def role_aliases(config: dict | None = None) -> dict:
    return FREE_ROLE_ALIASES if (config or read_config()).get("free_only") else ROLE_ALIASES



class Route(BaseModel):
    alias: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    provider: Literal["deepseek", "openrouter", "groq", "cerebras", "ollama"]
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
    fallback: str | None = Field(default=None, max_length=64)


class GatewayConfig(BaseModel):
    free_only: bool | None = None
    routes: list[Route] = Field(min_length=3, max_length=32)
    keys: dict[str, str] = Field(default_factory=dict, max_length=4, repr=False)

    @model_validator(mode="after")
    def coherent_routes(self):
        names = [r.alias for r in self.routes]
        if len(set(names)) != len(names):
            raise ValueError("Chaque alias doit être unique.")
        if not {"superviseur", "worker", "worker_secours"}.issubset(names):
            raise ValueError("Les alias superviseur, worker et worker_secours sont requis.")
        if self.free_only:
            if not set(FREE_ROLE_ALIASES.values()).union({"worker_secours"}).issubset(names):
                raise ValueError("Le profil Budget 0 $ nécessite les alias de code, recherche, rédaction et validation.")
            if any(r.provider != "openrouter" or r.model not in set(FREE_MODELS.values()) or not r.model.endswith(":free") for r in self.routes):
                raise ValueError("Budget 0 $ : seuls les quatre modèles OpenRouter :free autorisés sont acceptés, secours compris.")
        links = {r.alias: r.fallback for r in self.routes}
        for route in self.routes:
            seen = {route.alias}
            target = route.fallback
            while target:
                if target not in links or target in seen:
                    raise ValueError("Les secours doivent désigner un alias existant, sans boucle.")
                seen.add(target)
                target = links[target]
        if any(p not in KEY_NAMES or len(key) > 1024 or any(c in key for c in "\r\n\0") for p, key in self.keys.items()):
            raise ValueError("Clé fournisseur invalide.")
        return self


def defaults() -> dict:
    return {"enabled": False, "free_only": False, "routes": [
        {"alias": "superviseur", "provider": "deepseek", "model": "deepseek-flash", "fallback": None},
        {"alias": "worker", "provider": "openrouter", "model": "nvidia/nemotron-3-ultra-550b-a55b:free", "fallback": "worker_secours"},
        {"alias": "worker_secours", "provider": "openrouter", "model": "google/gemma-4-31b-it:free", "fallback": None},
    ]}


def read_config() -> dict:
    raw = None
    try:
        raw = json.loads((STATE_DIR / "settings.json").read_text())
        validated = GatewayConfig.model_validate(raw)
        return {"enabled": raw.get("enabled") is True, "free_only": validated.free_only is True, "routes": [r.model_dump() for r in validated.routes]}
    except (OSError, ValueError, TypeError):
        return free_defaults() if isinstance(raw, dict) and raw.get("free_only") is True else defaults()


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def write_config(config: dict) -> None:
    write_private(STATE_DIR / "settings.json", json.dumps(config, ensure_ascii=False, indent=2))


def enabled() -> bool:
    return read_config()["enabled"]


def proxy_url() -> str:
    return f"http://127.0.0.1:{PORT}/v1"


def master_key() -> str:
    path = STATE_DIR / "master.key"
    try:
        key = path.read_text().strip()
        if len(key) >= 24:
            return key
    except OSError:
        pass
    key = "sk-agentos-" + secrets.token_urlsafe(32)
    write_private(path, key)
    return key


def credentials() -> dict[str, str]:
    from claude_clone import config as C
    try:
        saved = json.loads((STATE_DIR / "credentials.json").read_text())
    except (OSError, ValueError):
        saved = {}
    files = [C._parse_dotenv(p) for p in C._DOTENV_FILES]
    keys = {p: saved.get(p) or C._lookup(env, files) for p, env in KEY_NAMES.items()}
    # Reuse the already configured provider without printing or duplicating its secret.
    host = urlparse(C._lookup("SUPERVISOR_BASE_URL", files) or C._lookup("LLM_BASE_URL", files) or C.DEFAULT_URL).hostname
    provider = {"openrouter.ai": "openrouter", "api.deepseek.com": "deepseek", "api.groq.com": "groq", "api.cerebras.ai": "cerebras"}.get(host)
    if provider and not keys.get(provider):
        keys[provider] = C._lookup("SUPERVISOR_API_KEY", files) or C._lookup("LLM_API_KEY", files)
    return {p: key for p, key in keys.items() if isinstance(key, str) and key and key != C.PLACEHOLDER_KEY}


def required_keys(config: dict) -> list[str]:
    keys = credentials()
    return sorted({r["provider"] for r in config["routes"] if r["provider"] != "ollama" and not keys.get(r["provider"])})


def executable() -> str | None:
    candidate = STATE_DIR.parent / "litellm-venv" / ("Scripts/litellm.exe" if os.name == "nt" else "bin/litellm")
    return str(candidate) if candidate.is_file() else shutil.which("litellm")


def proxy_config(config: dict) -> dict:
    models = []
    for route in config["routes"]:
        params = {"model": route["provider"] + "/" + route["model"]}
        if route["provider"] == "ollama":
            params["api_base"] = "http://127.0.0.1:11434"
        else:
            params["api_key"] = "os.environ/AGENTOS_GATEWAY_" + route["provider"].upper() + "_KEY"
        models.append({"model_name": route["alias"], "litellm_params": params})
    return {"model_list": models,
            "router_settings": {"num_retries": 0, "timeout": 120, "fallbacks": [{r["alias"]: [r["fallback"]]} for r in config["routes"] if r.get("fallback")]},
            "litellm_settings": {"drop_params": True, "telemetry": False},
            "general_settings": {"master_key": "os.environ/AGENTOS_GATEWAY_MASTER_KEY"}}


def reachable() -> bool:
    if not (STATE_DIR / "master.key").is_file():
        return False
    try:
        response = httpx.get(proxy_url() + "/models", headers={"Authorization": "Bearer " + master_key()}, timeout=1, trust_env=False)
        return response.is_success
    except httpx.HTTPError:
        return False


def status() -> dict:
    config = read_config()
    keys = credentials()
    return {**config, "url": proxy_url(), "installed": executable() is not None, "running": reachable(),
            "keys": {p: bool(keys.get(p)) for p in KEY_NAMES}, "missing_keys": required_keys(config), "free_routes": free_defaults()["routes"]}


def stop() -> None:
    """Only stop a process whose command matches our private LiteLLM config."""
    global _process
    if _process is not None:
        if _process.poll() is None:
            _process.terminate()
            try:
                _process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                raise ValueError("LiteLLM termine ses requêtes ; réessaie dans quelques instants.") from None
        _process = None
        return
    try:
        pid = int((STATE_DIR / "proxy.pid").read_text())
        command = subprocess.check_output(["ps", "-p", str(pid), "-o", "args="], text=True, stderr=subprocess.DEVNULL)
        if str(STATE_DIR / "config.yaml") not in command or "litellm" not in command:
            return
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass


def child_environment() -> dict[str, str]:
    # CLI flags such as DEBUG must not be inherited from the host application.
    allowed = {"PATH", "HOME", "USER", "LOGNAME", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def validate_free_catalogue(config: dict) -> None:
    if not config.get("free_only"):
        return
    # A missing or stale price is a refusal, never permission to spend.
    try:
        response = httpx.get("https://openrouter.ai/api/v1/models", timeout=10)
        response.raise_for_status()
        catalogue = {row["id"]: row for row in response.json()["data"] if isinstance(row, dict) and isinstance(row.get("id"), str)}
        for route in config["routes"]:
            row = catalogue.get(route["model"], {})
            pricing = row.get("pricing") or {}
            if "prompt" not in pricing or "completion" not in pricing or not all(Decimal(str(value)).is_zero() for value in pricing.values()):
                raise ValueError("Budget 0 $ : gratuité non confirmée pour " + route["model"])
            if "tools" not in (row.get("supported_parameters") or []):
                raise ValueError("Appels d’outils indisponibles pour " + route["model"])
    except (httpx.HTTPError, KeyError, TypeError, InvalidOperation) as exc:
        raise ValueError("Budget 0 $ : impossible de vérifier le catalogue ; activation refusée.") from None


def start() -> None:
    global _process
    config = read_config()
    if not executable():
        raise ValueError("LiteLLM n’est pas installé. Lance cc-gateway --install puis réessaie.")
    validate_free_catalogue(config)
    missing = required_keys(config)
    if missing:
        raise ValueError("Renseigne la clé API de " + ", ".join(PROVIDERS[p] for p in missing) + " dans Connexions.")
    if reachable():
        return
    write_private(STATE_DIR / "config.yaml", yaml.safe_dump(proxy_config(config), allow_unicode=True, sort_keys=False))
    env = child_environment()
    used_providers = {route["provider"] for route in config["routes"]}
    for provider, key in credentials().items():
        if provider not in used_providers:
            continue
        env["AGENTOS_GATEWAY_" + provider.upper() + "_KEY"] = key
    env["AGENTOS_GATEWAY_MASTER_KEY"] = master_key()
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    env["LITELLM_TELEMETRY"] = "False"
    fd = os.open(STATE_DIR / "proxy.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as log:
        process = subprocess.Popen([executable(), "--config", str(STATE_DIR / "config.yaml"), "--host", "127.0.0.1", "--port", str(PORT)], env=env, stdout=log, stderr=log, start_new_session=True)
    _process = process
    write_private(STATE_DIR / "proxy.pid", str(process.pid))
    for _ in range(100):
        if reachable():
            return
        if process.poll() is not None:
            raise ValueError("LiteLLM n’a pas démarré. Consulte le journal local gateway/proxy.log.")
        time.sleep(.15)
    stop()
    raise ValueError("LiteLLM ne répond pas sur le port " + str(PORT) + ". Vérifie que le port est disponible.")


def configure(body: GatewayConfig) -> dict:
    with LOCK:
        old = read_config()
        # Older clients cannot disable a strict budget just by omitting the field.
        free_only = old.get("free_only", False) if body.free_only is None else body.free_only
        body = GatewayConfig(routes=body.routes, keys=body.keys, free_only=free_only)
        keys = credentials()
        keys.update({p: key.strip() for p, key in body.keys.items() if key.strip()})
        write_private(STATE_DIR / "credentials.json", json.dumps(keys))
        if old["enabled"]:
            write_config({**old, "enabled": False})
        stop()
        config = {"enabled": False, "free_only": body.free_only, "routes": [r.model_dump() for r in body.routes]}
        write_config(config)
        if old["enabled"]:
            start()
            write_config({**config, "enabled": True})
        return status()


def activate() -> dict:
    with LOCK:
        start()
        write_config({**read_config(), "enabled": True})
        return status()


def deactivate() -> dict:
    with LOCK:
        if read_config().get("free_only"):
            raise ValueError("Budget 0 $ actif : désactive explicitement ce mode avant de revenir à une connexion directe potentiellement payante.")
        write_config({**read_config(), "enabled": False})
        stop()
        return status()


def apply_settings(settings, overrides: dict):
    config = read_config()
    if not config["enabled"] and not config.get("free_only"):
        return settings
    if config.get("free_only"):
        allowed = {route["alias"] for route in config["routes"]}
        panel = overrides.get("panel")
        overrides = {k: v for k, v in overrides.items() if k != "panel" and v in allowed}
        if isinstance(panel, list) and 2 <= len(panel) <= 4 and len(set(panel)) == len(panel) and all(model in allowed for model in panel):
            overrides["panel"] = panel
    assignments = {**role_aliases(config), **{k: v for k, v in overrides.items() if k != "panel"}}
    return replace(settings, supervisor_base_url=proxy_url(), supervisor_api_key=master_key(), supervisor_model=assignments["supervisor"],
                   coder_base_url=proxy_url(), coder_api_key=master_key(), coder_model=assignments["coder"],
                   research_model=assignments["researcher"], worker_model=assignments["worker"],
                   agent_models={r: assignments[r] for r in settings.agent_models},
                   panel_models=tuple(overrides.get("panel", ["agent_validation", "agent_recherche"] if config.get("free_only") else ["worker", "worker_secours"])),
                   supervisor_fallbacks=(), coder_fallbacks=(), research_fallbacks=(), agent_fallbacks=(), managed_gateway=True, free_only=config.get("free_only", False))


def cli() -> None:
    parser = argparse.ArgumentParser(description="Installer et piloter la passerelle LiteLLM locale d’AgentOS.")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.install:
        uv = shutil.which("uv")
        if not uv:
            raise SystemExit("Installe uv, puis relance cc-gateway --install.")
        destination = STATE_DIR.parent / "litellm-venv"
        python = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            subprocess.run([uv, "venv", "--python", "3.12", str(destination)], check=True)
        subprocess.run([uv, "pip", "install", "--python", str(python), "litellm[proxy]==1.109.0"], check=True)
    elif args.stop:
        deactivate()
    elif not args.status:
        activate()
    print(json.dumps(status(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
