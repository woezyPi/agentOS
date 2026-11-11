"""Recette navigateur hors ligne : python tests/test_browser.py (extra dev + Chromium)."""
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from langchain_core.messages import AIMessage
from playwright.sync_api import expect, sync_playwright

from claude_clone import tools as T
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
                page.goto(f"http://127.0.0.1:{port}/#token=browser-test")
                expect(page.locator("#status")).to_contain_text("CONNECTÉ")
                assert page.evaluate("document.styleSheets.length") >= 2
                theme = page.get_by_role("button", name="MODE JOUR")
                expect(theme).to_have_attribute("aria-pressed", "false")
                theme.click()
                expect(theme).to_have_attribute("aria-pressed", "true")
                assert page.evaluate("document.documentElement.dataset.theme") == "light"
                assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(250, 250, 248)"
                assert page.locator(".brand .logo").evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(16, 16, 20)"
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
