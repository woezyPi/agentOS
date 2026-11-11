"""Offline visual checks with seeded UI data (no LLM calls or user sessions).
Run: .venv/bin/python tests/test_visual.py [after]; screenshots: artifacts/ui/.
"""

import sys
import json
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / "tests"))
from fastapi.testclient import TestClient
from claude_clone.server import create_app
from claude_clone.graph import build_graph
from test_smoke import FakeLLM
from playwright.sync_api import sync_playwright, expect


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "after"
    out = Path.cwd() / "artifacts/ui"
    out.mkdir(parents=True, exist_ok=True)
    client = TestClient(
        create_app(
            token="visual",
            hosts={"testserver"},
            graph=build_graph(supervisor_llm=FakeLLM([]), coder_llm=FakeLLM([])),
        )
    )

    def worker(ident, name, status, steps):
        return dict(
            t="tool",
            id=ident,
            name="worker",
            agent="worker",
            label=name,
            model="deepseek-v4-flash",
            summary="Vérifie les chemins des fichiers et les dépendances du projet avant la mise à jour.",
            status=status,
            start=1000,
            end=4400 if status != "run" else None,
            steps=steps,
            result="Vérification terminée. Les fichiers sont cohérents avec la structure du projet."
            if status != "run"
            else "",
            expanded=False,
        )

    state = {
        "active": "visual-thread-0001",
        "conversations": [
            {
                "id": "visual-thread-0001",
                "title": "Harmoniser l’interface Agentos",
                "messages": [
                    {
                        "id": "v-user",
                        "role": "user",
                        "text": "Harmonise les composants de l’interface et vérifie les changements avec deux workers.",
                    },
                    {
                        "id": "v-answer",
                        "role": "assistant",
                        "agent": "Superviseur",
                        "blocks": [
                            {
                                "t": "text",
                                "text": "Je vérifie les composants existants, puis je prépare les changements. Les outils et leurs autorisations sont conservés.",
                            },
                            worker(
                                "worker1",
                                "Structure & composants",
                                "ok",
                                [
                                    {
                                        "k": "tool",
                                        "call": "read",
                                        "name": "read_file",
                                        "summary": "path=claude_clone/static/index.html",
                                        "status": "ok",
                                        "text": "Structure HTML validée.",
                                    }
                                ],
                            ),
                            worker(
                                "worker2",
                                "Styles & accessibilité",
                                "error",
                                [
                                    {
                                        "k": "tool",
                                        "call": "styles",
                                        "name": "read_file",
                                        "summary": "path=claude_clone/static/css/base.css",
                                        "status": "error",
                                        "text": "Deux règles de style contradictoires repérées.",
                                    }
                                ],
                            ),
                            {
                                "t": "tool",
                                "id": "bash1",
                                "name": "execute_bash",
                                "summary": "command=python -m compileall claude_clone",
                                "status": "ok",
                                "start": 1000,
                                "end": 2300,
                                "open": True,
                                "result": "Listing claude_clone…\nCompilation terminée : aucun problème détecté.",
                            },
                            {
                                "t": "approval",
                                "calls": [
                                    {
                                        "id": "diff1",
                                        "name": "edit_file",
                                        "summary": "path=styles.css",
                                        "preview": {
                                            "kind": "diff",
                                            "title": "styles.css",
                                            "body": "--- a/styles.css\n+++ b/styles.css\n@@ -1,3 +1,3 @@\n .message {\n-  border-radius: 0;\n+  border-radius: 14px;\n }",
                                        },
                                    }
                                ],
                                "choices": {},
                                "resolved": True,
                                "decided": {"diff1": "approve"},
                            },
                            {
                                "t": "text",
                                "text": "Les composants sont prêts à être relus. **Prochaine étape :** vérifier le rendu et les interactions.",
                            },
                        ],
                        "usage": "4,2 s · 3 appels LLM · 6 038 tokens en entrée · 184 en sortie · 2 outils",
                    },
                ],
            }
        ],
    }
    # Content pressure: report tables, long paths and an unbroken code line.
    report = "| Catégorie | Constat | Action |\n| --- | --- | --- |\n"
    for category in ("TECH DEBT", "OPPORTUNITÉ", "À VÉRIFIER"):
        report += f"| {category} | `agentos_release/claude_clone/static/` duplique les fichiers et les dépendances du projet. | Vérifier les paramètres et conserver les fonctionnalités existantes. |\n"
    report += "\n```bash\n" + "command-with-a-very-long-argument-" * 30 + "\n```"
    state["conversations"][0]["messages"][1]["blocks"].append(
        {"t": "text", "text": report}
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1512, "height": 1080}, device_scale_factor=1
        )
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def route(r):
            req = r.request
            if not req.url.startswith("http://testserver"):
                return r.fulfill(status=200, body="", content_type="text/css")
            path = req.url.removeprefix("http://testserver")
            if phase == "before" and (path == "/" or path.startswith("/static/")):
                asset = (
                    "claude_clone/static/index.html"
                    if path == "/"
                    else "claude_clone/" + path.lstrip("/")
                )
                content = subprocess.check_output(["git", "show", "HEAD:" + asset])
                mime = (
                    "text/html"
                    if asset.endswith(".html")
                    else "text/css"
                    if asset.endswith(".css")
                    else "application/javascript"
                )
                return r.fulfill(status=200, body=content, content_type=mime)
            res = client.request(
                req.method, path, headers=dict(req.headers), content=req.post_data
            )
            r.fulfill(
                status=res.status_code, body=res.content, headers=dict(res.headers)
            )

        page.route("**/*", route)
        page.add_init_script(
            'localStorage.setItem("cc-token","visual");localStorage.setItem("agentos-brut-v1",'
            + json.dumps(json.dumps(state))
            + ");"
        )
        page.goto("http://testserver")
        expect(page.locator("#status")).to_contain_text("CONNECTÉ")
        if phase == "after":
            for selector, radius in [
                (".message", "14px"),
                (".agent-card", "14px"),
                (".tool", "12px"),
                (".composer", "16px"),
            ]:
                expect(page.locator(selector).first).to_have_css(
                    "border-radius", radius
                )
            assert (
                page.locator(".message").first.evaluate(
                    "(e)=>getComputedStyle(e).borderLeftWidth"
                )
                == "1px"
            )
            assert page.evaluate(
                "[...document.querySelectorAll('*')].every(e=>!getComputedStyle(e).backgroundImage.includes('gradient'))"
            )
        page.locator("#feed").evaluate("(e)=>e.scrollTop=0")
        page.screenshot(path=str(out / f"{phase}-conversation.png"))
        page.locator(".agent-inspect").first.click()
        expect(page.locator("#agentInspector")).to_be_visible()
        page.screenshot(path=str(out / f"{phase}-inspector.png"))
        for tab in ("logs", "files", "metrics", "activity"):
            page.locator(f'[data-inspector-tab="{tab}"]').click()
            expect(page.locator("#aiBody")).not_to_be_empty()
        page.locator('[data-inspector-tab="files"]').click()
        page.locator(".ai-file").first.click()
        expect(page.locator("#fileview")).to_be_visible()
        page.screenshot(path=str(out / f"{phase}-file.png"))
        page.locator("#fvClose").click()
        page.locator("#aiClose").click()
        page.get_by_role("button", name="CÔTE À CÔTE", exact=True).click()
        expect(page.locator(".sbs")).to_be_visible()
        page.locator("#feed").evaluate("(e)=>e.scrollTop=e.scrollHeight")
        page.screenshot(path=str(out / f"{phase}-diff.png"))
        page.get_by_role("button", name="UNIFIÉ", exact=True).click()
        expect(page.locator("pre.pv")).to_be_visible()
        page.locator("#openStudio").click()
        expect(page.locator("#studio")).to_be_visible()
        page.screenshot(path=str(out / f"{phase}-studio.png"))
        page.locator("#studioClose").click()
        for w in (
            1920,
            1512,
            1440,
            1381,
            1380,
            1280,
            1262,
            1200,
            1101,
            1100,
            1024,
            900,
            851,
            850,
            768,
            700,
            520,
            390,
            360,
            320,
        ):
            page.set_viewport_size({"width": w, "height": 1000 if w > 850 else 844})
            assert page.evaluate(
                "document.documentElement.scrollWidth<=innerWidth"
            ), f"Overflow {w}"
            violations = page.evaluate("""() => {
                const selectors = ['.topbar', '.controls', '#agent', '#skill', '#delegation', '#mode', '.auto', '.composer', '#send', '#compactBtn', '.message', '.agent-card', '.md table', '.code'];
                return selectors.flatMap(selector => [...document.querySelectorAll(selector)].flatMap(e => {
                    const r=e.getBoundingClientRect();
                    return r.width && (r.left < -1 || r.right > innerWidth+1) ? [{selector,left:r.left,right:r.right,width:innerWidth}] : [];
                }));
            }""")
            assert not violations, violations
            assert page.locator("#feed").evaluate(
                "(e)=>e.scrollWidth <= e.clientWidth + 1"
            ), f"Feed overflow {w}"
            assert page.locator("#send").evaluate(
                "(e)=>e.getBoundingClientRect().bottom <= innerHeight"
            ), f"Send hidden {w}"
            assert page.locator("#agent").evaluate(
                "(e)=>e.getBoundingClientRect().width >= 112"
            ), f"Control compressed {w}"
            if w == 1262:
                page.locator("#feed").evaluate("(e)=>e.scrollTop=e.scrollHeight")
                page.screenshot(path=str(out / "responsive-report-desktop.png"))
            if w == 390:
                page.wait_for_function(
                    "() => document.querySelector('#sidebar').getBoundingClientRect().right <= 1"
                )
                page.locator("#feed").evaluate("(e)=>e.scrollTop=e.scrollHeight")
                page.screenshot(path=str(out / f"{phase}-mobile.png"))
                table = page.locator(".md table").first
                assert table.evaluate("(e)=>e.scrollWidth > e.clientWidth")
                assert table.evaluate("(e)=>{e.scrollLeft=100;return e.scrollLeft>0}")
                page.locator("#menuToggle").click()
                expect(page.locator("#newConversation")).to_be_in_viewport()
                page.locator(".thread-title").first.click()
        for width, height in (
            (1262, 768),
            (1024, 600),
            (844, 390),
            (390, 600),
            (320, 568),
        ):
            page.set_viewport_size({"width": width, "height": height})
            assert page.locator("#send").evaluate(
                "(e)=>{const r=e.getBoundingClientRect();return r.right<=innerWidth && r.bottom<=innerHeight}"
            ), f"Send hidden {width}x{height}"
            assert page.locator("#feed").evaluate(
                "(e)=>e.clientHeight >= 60 && e.scrollWidth<=e.clientWidth+1"
            ), f"Chat squeezed {width}x{height}"
            if width == 844:
                page.wait_for_function(
                    "() => document.querySelector('#sidebar').getBoundingClientRect().right <= 1"
                )
                page.screenshot(path=str(out / "responsive-landscape.png"))
        page.emulate_media(reduced_motion="reduce")
        if phase == "after":
            assert (
                page.locator("#sidebar").evaluate(
                    "(e)=>getComputedStyle(e).transitionDuration"
                )
                == "0s"
            )
        assert not errors, errors
        print(
            "Visual scenarios OK:",
            phase,
            "conversation, workers, inspector, file, diff, studio, responsive; no JS errors",
        )
        browser.close()


if __name__ == "__main__":
    main()
