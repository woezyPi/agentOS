"""Recette navigateur hors ligne : python tests/test_browser.py (extra dev + Chromium)."""
import os
import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from langchain_core.messages import AIMessage
from playwright.sync_api import expect, sync_playwright

from claude_clone import tools as T
from claude_clone import gateway as GW
from claude_clone.graph import build_graph
from claude_clone.server import create_app
from test_smoke import FakeLLM, call


def main():
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as workdir:
        os.chdir(workdir)
        T.set_workdir(workdir)
        tasks = [{"name": "alpha", "goal": "Inspecte les fichiers du projet."},
                 {"name": "beta", "goal": "Relis la structure du projet."}]
        supervisor = FakeLLM([
            call("spawn_workers", {"tasks": tasks}, "workers"),
            call("write_file", {"path": "release.txt", "content": "release intégrée"}, "write"),
            AIMessage(content="Intégration terminée."),
        ])
        graph = build_graph(supervisor_llm=supervisor, coder_llm=FakeLLM([]),
                            agent_llm=FakeLLM([AIMessage(content="Rapport alpha"), AIMessage(content="Rapport beta")]))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        app = create_app(token="browser-test", hosts={f"127.0.0.1:{port}"}, graph=graph)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Le serveur de recette ne démarre pas")
                time.sleep(0.05)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # La recette reste indépendante de Google Fonts et du réseau externe.
                context.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(status=200, body="", content_type="text/css"))
                # Model editor UI uses a fake catalog; no user configuration is written.
                model_view = {
                    "roles": [{"key": "supervisor", "title": "Superviseur", "model": "catalog/one", "default": "catalog/one", "source": "env", "env": "SUPERVISOR_MODEL"}],
                    "panel": {"models": ["catalog/one", "catalog/two"], "source": "défaut"},
                    "available": [{"id": "catalog/one"}, {"id": "catalog/two"}], "error": None,
                }
                def models_route(route):
                    if route.request.method == "PUT":
                        payload = route.request.post_data_json
                        model_view["roles"][0]["model"] = payload["assignments"]["supervisor"]
                        model_view["roles"][0]["source"] = "modifié"
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(model_view))
                def model_metadata_route(route):
                    response = route.fetch()
                    metadata = response.json()
                    metadata['role_models'] = {'supervisor':'superviseur', 'developer':'worker'}
                    metadata['model_routes'] = {'superviseur':{'provider':'deepseek','model':'deepseek-flash'},'worker':{'provider':'groq','model':'test-worker'}}
                    route.fulfill(response=response, json=metadata)
                page.route("**/api/meta", model_metadata_route)
                page.route("**/api/models", models_route)
                catalog = [
                    {"id": "custom/model:free", "name": "Custom Free", "provider": "custom", "tools": True, "free": True, "window": 128000},
                    {"id": "custom/paid", "provider": "custom", "tools": True, "free": False, "window": 64000},
                    {"id": "other/free", "provider": "other", "tools": False, "free": True, "window": 128000},
                    {"id": "other/large", "provider": "other", "tools": True, "free": True, "window": 256000},
                ]
                page.route("**/api/models/catalog?*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"available": catalog, "error": None})))
                gateway_view = {**GW.defaults(), "installed": True, "running": False, "url": "http://127.0.0.1:4000/v1", "keys": {"deepseek": False, "openrouter": True, "groq": False, "cerebras": False}, "missing_keys": ["deepseek"], "free_routes": GW.free_defaults()["routes"]}
                def gateway_route(route):
                    if route.request.method == "PUT":
                        payload = route.request.post_data_json
                        gateway_view["routes"] = payload["routes"]
                        gateway_view["free_only"] = payload.get("free_only",False)
                        for provider, value in payload["keys"].items(): gateway_view["keys"][provider] = bool(value)
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(gateway_view))
                def gateway_action(route):
                    gateway_view["enabled"] = gateway_view["running"] = route.request.url.endswith("/start")
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(gateway_view))
                page.route("**/api/gateway", gateway_route)
                page.route("**/api/gateway/start", gateway_action)
                page.route("**/api/gateway/stop", gateway_action)
                page.goto(f"http://127.0.0.1:{port}/#token=browser-test")
                expect(page.locator("#status")).to_contain_text("CONNECTÉ")
                assert page.evaluate("document.styleSheets.length") >= 2
                expect(page.locator('#activeModelName')).to_have_text('DeepSeek · deepseek-flash')
                page.locator('#agent').select_option('developer')
                expect(page.locator('#activeModelName')).to_have_text('Groq · test-worker')
                page.locator('#agent').select_option('auto')
                expect(page.locator('#activeModelName')).to_have_text('DeepSeek · deepseek-flash')
                page.locator("#openModels").click()
                model = page.get_by_role("combobox", name="Modèle — Superviseur", exact=True)
                expect(model).to_be_enabled()
                expect(page.locator("#availableModels option")).to_have_count(2)
                page.get_by_role("combobox", name="Source du catalogue").select_option("openrouter")
                expect(page.get_by_role("checkbox", name="Appels d’outils")).to_be_checked()
                expect(page.locator(".catalog-model")).to_have_count(3)
                page.get_by_role("checkbox", name="Gratuits").check()
                expect(page.locator(".catalog-model")).to_have_count(2)
                page.get_by_role("combobox", name="Famille du modèle").select_option("custom")
                page.get_by_role("combobox", name="Contexte minimum").select_option("128000")
                expect(page.locator(".catalog-model")).to_have_count(1)
                search = page.get_by_role("searchbox", name="Rechercher un modèle")
                search.fill("introuvable")
                expect(page.locator(".catalog-model")).to_have_count(0)
                search.fill("model")
                page.get_by_role("button", name="Choisir custom/model:free", exact=True).click()
                expect(model).to_have_value("custom/model:free")
                output = previous / "artifacts/ui"
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / "model-catalog.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator(".model-catalog").evaluate("e=>e.scrollWidth<=e.clientWidth+1")
                page.screenshot(path=str(output / "model-catalog-mobile.png"))
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="ENREGISTRER", exact=False).click()
                expect(model).to_have_value("custom/model:free")
                expect(page.locator(".studio-msg")).to_contain_text("Enregistré")
                page.locator("#studioClose").click()
                page.reload()
                page.locator("#openModels").click()
                expect(model).to_have_value("custom/model:free")
                page.locator("#studioClose").click()

                page.locator("#openConnections").click()
                # Password inputs have no implicit textbox role.
                key_input = page.locator('input[aria-label="Clé API DeepSeek"]')
                expect(key_input).to_have_attribute("type", "password")
                key_input.fill("test-secret-not-for-storage")
                page.get_by_role("combobox", name="Fournisseur de worker", exact=True).select_option("groq")
                page.get_by_role("textbox", name="Modèle de worker", exact=True).fill("test/model")
                page.get_by_role("button", name="Enregistrer et activer LiteLLM", exact=True).click()
                expect(page.locator(".gateway-state")).to_have_text("Active")
                expect(key_input).to_have_value("")
                expect(page.get_by_role("combobox", name="Fournisseur de worker", exact=True)).to_have_value("groq")
                assert page.evaluate("!JSON.stringify(localStorage).includes('test-secret-not-for-storage')")
                page.screenshot(path=str(output / "gateway-connections.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator("#studioForm").evaluate("e=>e.scrollWidth<=e.clientWidth+1")
                page.screenshot(path=str(output / "gateway-connections-mobile.png"))
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="Revenir à la connexion directe", exact=True).click()
                expect(page.locator(".gateway-state")).to_have_text("Connexion directe")
                budget=page.get_by_role('checkbox',name='Budget strict 0 $ — superviseur et secours compris')
                budget.check()
                expect(page.get_by_role('combobox',name='Fournisseur de superviseur',exact=True)).to_have_value('openrouter')
                expect(page.get_by_role('textbox',name='Modèle de agent_code',exact=True)).to_have_value('cohere/north-mini-code:free')
                page.get_by_role('button',name='Enregistrer la configuration',exact=True).click()
                expect(page.locator('.gateway-state')).to_have_text('Budget 0 $ · à activer')
                expect(page.get_by_role('button',name='Revenir à la connexion directe',exact=True)).to_be_disabled()
                page.locator("#studioClose").click()

                theme = page.get_by_role("button", name="MODE JOUR")
                expect(theme).to_have_attribute("aria-pressed", "false")
                theme.click()
                expect(theme).to_have_attribute("aria-pressed", "true")
                assert page.evaluate("document.documentElement.dataset.theme") == "light"
                assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(250, 249, 246)"
                assert page.locator(".brand .logo").evaluate("e => getComputedStyle(e).backgroundColor") == "rgba(0, 0, 0, 0)"
                page.reload()
                expect(theme).to_have_attribute("aria-pressed", "true")
                assert page.evaluate("localStorage.getItem('agentos-theme')") == "light"
                page.locator("#delegation").select_option("max")
                assert page.evaluate("localStorage.getItem('agentos-theme')") == "light"
                other = page.context.new_page()
                other.goto(f"http://127.0.0.1:{port}/")
                expect(other.locator("#themeToggle")).to_have_attribute("aria-pressed", "true")
                theme.click()
                expect(theme).to_have_attribute("aria-pressed", "false")
                expect(other.locator("#themeToggle")).to_have_attribute("aria-pressed", "false")
                other.close()
                assert page.evaluate("document.documentElement.dataset.theme") is None
                page.locator("#prompt").fill("Inspecte puis écris le fichier de release.")
                page.locator("#send").click()
                expect(page.get_by_text("CONFIRMATION REQUISE", exact=True)).to_be_visible()
                assert not Path("release.txt").exists()
                assert page.locator(".agent-inspect").count() >= 2
                page.locator(".agent-inspect").first.click()
                expect(page.locator("#agentInspector")).to_be_visible()
                for tab in ("logs", "files", "metrics", "activity"):
                    page.locator(f'[data-inspector-tab="{tab}"]').click()
                    expect(page.locator("#aiBody")).not_to_be_empty()
                page.locator("#aiClose").click()
                page.get_by_role("button", name="AUTORISER", exact=True).click()
                page.get_by_role("button", name="VALIDER ↗", exact=True).click()
                expect(page.get_by_text("Intégration terminée.", exact=True)).to_be_visible()
                assert Path("release.txt").read_text() == "release intégrée"
                page.locator(".agent-inspect").first.click()
                page.locator("#newConversation").click()
                expect(page.locator("#agentInspector")).to_be_hidden()
                toggle = page.locator("#menuToggle")
                page.locator("#sidebarClose").click()
                expect(page.locator("#sidebar")).to_be_hidden()
                expect(toggle).to_have_attribute("aria-expanded", "false")
                assert page.locator(".main").evaluate("e=>e.getBoundingClientRect().left") == 0
                page.reload()
                expect(page.locator("#sidebar")).to_be_hidden()
                toggle.click()
                expect(page.locator("#sidebar")).to_be_visible()
                expect(toggle).to_have_attribute("aria-expanded", "true")
                page.set_viewport_size({"width": 390, "height": 844})
                theme.click()
                expect(theme).to_have_attribute("aria-pressed", "true")
                theme_box, menu_box = theme.bounding_box(), toggle.bounding_box()
                assert abs((theme_box["y"] + theme_box["height"] / 2) -
                           (menu_box["y"] + menu_box["height"] / 2)) < 1
                expect(toggle).to_have_attribute("aria-expanded", "false")
                toggle.click()
                expect(page.locator("#sidebarClose")).to_be_visible()
                page.locator("#sidebarClose").click()
                expect(toggle).to_have_attribute("aria-expanded", "false")
                overflow = page.evaluate("""() => [...document.querySelectorAll('body *')]
                    .filter(e => e.getBoundingClientRect().right > innerWidth + 1)
                    .map(e => [e.className, Math.round(e.getBoundingClientRect().right)])""")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), overflow
                assert not errors, errors
                browser.close()
                print("✓ navigateur : nouveau static, workers, inspecteur, approbation/reprise, sessions, mobile")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            os.chdir(previous)
            T.set_workdir(str(previous))


if __name__ == "__main__":
    main()
