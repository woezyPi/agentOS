"""Capture the real UI with anonymous, offline demo data. No keys or LLM calls.
Run from the repository: python scripts/capture_readme.py (requires the dev extra + Chromium).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_clone.gateway import FREE_ROLE_ALIASES, free_defaults

OUT = ROOT / "docs" / "images"
ROUTES = free_defaults()["routes"]
ROLES = {"researcher": "Chercheur", "architect": "Architecte", "developer": "Développeur", "challenger": "Challenger", "reviewer": "Reviewer", "devops": "DevOps", "data": "Analyste data", "writer": "Rédacteur"}
CONTEXT = {"tokens": 7200, "limit": 128000, "pct_compact": 9, "compact_at": 80000, "summary_chars": 0, "turns": 1}


def demo_state():
    now = int(time.time() * 1000)
    def step(ident, name, path):
        return {"k": "tool", "call": ident, "name": name, "summary": "path=" + path, "status": "ok", "text": "Lecture terminée. Aucun fichier modifié."}
    def agent(role, alias, status, summary, steps, result=""):
        return {"t": "tool", "id": "demo-" + role, "name": role, "agent": role, "model": alias, "summary": summary,
                "status": status, "start": now-14000, "end": now-3000 if status == "ok" else None, "steps": steps,
                "result": result, "expanded": False}
    blocks = [
        agent("architect", "agent_validation", "ok", "Définir une recherche rapide, locale et intégrée à la navigation.",
              [step("architecture", "read_file", "claude_clone/server.py"), step("structure", "read_file", "claude_clone/static/js/app.js")],
              "## Architecture proposée\n\nAjouter une recherche dans les titres et le contenu des conversations.\n\n- Garder les données sur la machine.\n- Préserver le streaming et la conversation active.\n- Tester les états vide, chargé et sans résultat.\n\n**Périmètre :** interface web et historique local."),
        agent("developer", "agent_code", "run", "Préparer le champ de recherche et le filtrage des conversations.",
              [step("code", "read_file", "claude_clone/static/js/app.js"), step("styles", "read_file", "claude_clone/static/css/base.css")]),
        agent("researcher", "agent_recherche", "ok", "Vérifier les contraintes du projet et les conventions existantes.",
              [step("project", "read_file", "pyproject.toml")], "Les conventions du projet permettent de conserver une interface sans étape de build frontend."),
        agent("reviewer", "agent_validation", "run", "Contrôler l’accessibilité, les cas limites et les changements proposés.",
              [step("review", "read_file", "claude_clone/server.py"), step("tests", "read_file", "tests/test_browser.py")]),
    ]
    return {"active": "documentation-demo-0001", "conversations": [{"id": "documentation-demo-0001", "title": "Une recherche dans les conversations", "messages": [
        {"id": "demo-user", "role": "user", "text": "Ajoute une recherche dans les conversations. Prépare l’architecture, l’interface et la revue avant d’appliquer les changements."},
        {"id": "demo-answer", "role": "assistant", "agent": "Superviseur", "model": "superviseur", "blocks": blocks, "status": "Agents au travail…"},
    ]}]}


def available():
    return [{"id": r["alias"], "name": r["alias"] + " — OpenRouter · " + r["model"], "provider": "openrouter", "tools": True, "free": True, "window": None} for r in ROUTES]


def payload(path):
    if path == "/api/meta":
        return {"workdir": "/workspace/agentos", "sandbox": False, "setup_required": False, "free_only": True,
                "modes": ["challenge", "direct"], "roles": [{"key": key, "title": title, "builtin": True} for key, title in ROLES.items()],
                "readonly_tools": [], "models": {"superviseur": "superviseur", "codeur": "agent_code"},
                "role_models": FREE_ROLE_ALIASES, "model_routes": {r["alias"]: r for r in ROUTES}}
    if path == "/api/skills":
        return {"skills": [{"name": "stack-map", "description": "Cartographier la structure et les dépendances du projet.", "builtin": True, "tools": [], "editable": False}], "warnings": []}
    if path == "/api/gateway":
        return {**free_defaults(), "enabled": True, "installed": True, "running": True, "url": "http://127.0.0.1:4000/v1",
                "keys": {"deepseek": False, "openrouter": True, "groq": False, "cerebras": False}, "missing_keys": [], "free_routes": ROUTES}
    if path == "/api/models":
        return {"roles": [{"key": key, "title": ROLES.get(key, {"supervisor": "Superviseur", "coder": "Agent codeur", "worker": "Workers"}.get(key, key)),
                           "model": alias, "default": alias, "source": "route LiteLLM", "fallbacks": [], "env": ""} for key, alias in FREE_ROLE_ALIASES.items()],
                "panel": {"models": ["agent_validation", "agent_recherche"], "default": ["agent_validation", "agent_recherche"], "source": "route LiteLLM"},
                "available": available(), "error": None, "routing": "litellm", "openrouter": False, "free_only": True}
    if path == "/api/models/catalog":
        return {"available": available(), "error": None}
    if path.endswith("/context"):
        return CONTEXT
    if path.endswith("/pending"):
        return {"pending": None, "waiting": False}
    return {}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1100}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def route(request):
            url = urlparse(request.request.url)
            if url.hostname != "agentos.demo":
                return request.fulfill(status=200, body="", content_type="text/css")
            path = url.path
            if path.startswith("/api/"):
                return request.fulfill(status=200, body=json.dumps(payload(path)), content_type="application/json")
            if path == "/":
                asset = ROOT / "claude_clone/static/index.html"
            elif path.startswith("/static/"):
                asset = ROOT / "claude_clone" / path.lstrip("/")
            else:
                return request.fulfill(status=404, body="")
            mime = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}.get(asset.suffix, "application/octet-stream")
            return request.fulfill(status=200, body=asset.read_bytes(), content_type=mime)
        page.route("**/*", route)
        page.add_init_script('localStorage.setItem("cc-token","documentation-demo");localStorage.setItem("agentos-theme","dark");localStorage.setItem("agentos-brut-v1",' + json.dumps(json.dumps(demo_state())) + ');')
        page.goto("http://agentos.demo/")
        expect(page.locator("#status")).to_contain_text("CONNECTÉ")
        expect(page.locator(".agent-card")).to_have_count(4)
        expect(page.locator("#activeModelName")).to_contain_text("0 $")
        page.locator("#feed").evaluate("element=>element.scrollTop=0")
        page.screenshot(path=str(OUT / "overview-dark.png"))
        page.locator("#themeToggle").click()
        page.mouse.move(0, 0)
        expect(page.locator(".agent-card").first).to_have_css("background-color", "rgb(255, 255, 255)")
        page.screenshot(path=str(OUT / "overview-light.png"))
        page.locator(".agent-inspect").first.click()
        expect(page.locator("#agentInspector")).to_be_visible()
        page.screenshot(path=str(OUT / "inspector.png"))
        page.locator("#aiClose").click()
        page.locator("#openConnections").click()
        expect(page.locator(".gateway-state")).to_have_text("Budget 0 $ actif")
        assert page.locator('input[type="password"]').evaluate_all("items=>items.every(input=>input.value==='')")
        page.screenshot(path=str(OUT / "connections.png"))
        page.locator('[data-tab="models"]').click()
        expect(page.locator(".catalog-model")).to_have_count(7)
        page.screenshot(path=str(OUT / "models.png"))
        assert not errors, errors
        browser.close()
    print("5 captures générées dans docs/images — données anonymes, aucun fournisseur ni compte interrogé.")


if __name__ == "__main__":
    main()
