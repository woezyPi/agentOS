"""Stream worker events through the real browser without calling any model."""
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from fastapi.responses import StreamingResponse
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_clone import tools as T
from claude_clone.graph import build_graph
from claude_clone.server import create_app
from test_smoke import FakeLLM


def main():
    root=Path.cwd();previous_workdir=T.WORKDIR;release=threading.Event()
    with tempfile.TemporaryDirectory() as folder:
        T.set_workdir(folder)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        app=create_app(token='live-test',hosts={f'127.0.0.1:{port}'},graph=build_graph(supervisor_llm=FakeLLM([]),coder_llm=FakeLLM([])))
        app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/api/chat']
        async def stream():
            model='agent_validation'
            events=[
                {'type':'agent_start','id':'review','agent':'reviewer','summary':'Vérifier les dépendances et les responsabilités de chaque composant.','model':model},
                {'type':'agent_step','id':'review','kind':'tool_call','call':'read-review','name':'read_file','summary':'path=claude_clone/config.py'},
                {'type':'agent_step','id':'review','kind':'tool_result','call':'read-review','status':'ok','text':'Lu.'},
                {'type':'agent_done','id':'review','status':'ok','text':'Relecture terminée.','model':'nemotron-3-ultra-550b-a55b:free'},
                {'type':'agent_start','id':'architect','agent':'architect','summary':'Évaluer la structure du projet, la séparation des modules, les dépendances et les points à simplifier.','model':model},
            ]
            for event in events:
                yield json.dumps(event)+'\n';await asyncio.sleep(.08)
            for i in range(30):
                yield json.dumps({'type':'agent_step','id':'architect','kind':'tool_call','call':str(i),'name':'read_file','summary':f'path=component_{i}.py'})+'\n'
                await asyncio.sleep(.08)
            while not release.is_set():
                await asyncio.sleep(.05)
            yield json.dumps({'type':'agent_done','id':'architect','status':'ok','text':'Architecture vérifiée.','model':'nemotron-3-ultra-550b-a55b:free'})+'\n'
            yield json.dumps({'type':'done','usage':'Test local terminé.','waiting':False})+'\n'
        @app.post('/api/chat')
        async def simulated_chat(): return StreamingResponse(stream(),media_type='application/x-ndjson')
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            for _ in range(100):
                if server.started: break
                time.sleep(.05)
            with sync_playwright() as p:
                browser=p.chromium.launch();page=browser.new_page(viewport={'width':1512,'height':1080})
                page.route('https://fonts.googleapis.com/**',lambda route:route.fulfill(status=200,body='',content_type='text/css'))
                def meta_route(route):
                    response=route.fetch();data=response.json()
                    data['role_models']={'supervisor':'agent_validation'}
                    data['model_routes']={'agent_validation':{'provider':'openrouter','model':'nvidia/nemotron-3-ultra-550b-a55b:free'}}
                    route.fulfill(response=response,json=data)
                page.route('**/api/meta',meta_route)
                page.add_init_script('localStorage.setItem("cc-token","live-test");')
                page.goto(f'http://127.0.0.1:{port}/')
                expect(page.locator('#status')).to_contain_text('CONNECTÉ')
                page.locator('#prompt').fill('Vérifie la structure du projet.')
                page.locator('#send').click()
                expect(page.locator('.agent-card')).to_have_count(2)
                page.wait_for_function("()=>{const bar=document.querySelector('.status-bar');return bar&&!bar.hidden&&bar.firstElementChild.style.width==='50%'}")
                page.evaluate("""() => {
                    window.progressBar=document.querySelector('.status-bar');
                    window.progressStats=document.querySelector('.status-stats');
                    window.progressProblems=[];
                    window.progressObserver=new MutationObserver(()=>{
                        if(!progressBar.isConnected)progressProblems.push('bar replaced');
                        if(progressBar.hidden)progressProblems.push('bar hidden');
                        if(progressBar.firstElementChild.style.width!=='50%')progressProblems.push('width reset');
                        if(!progressStats.textContent)progressProblems.push('stats cleared');
                    });
                    progressObserver.observe(document.querySelector('.message.agent'),{subtree:true,childList:true,attributes:true,characterData:true});
                }""")
                page.wait_for_timeout(2800)
                problems=page.evaluate('progressProblems')
                assert not problems,problems
                heights=page.locator('.agent-card').evaluate_all('(cards)=>cards.map(card=>card.getBoundingClientRect().height)')
                assert abs(heights[0]-heights[1])<=1,heights
                models=page.locator('.agent-card .pill.model').all_text_contents()
                assert models[0]==models[1] and models[0].startswith('OpenRouter · '),models
                peek_tops=page.locator('.ac-peek').evaluate_all('(items)=>items.map(e=>e.getBoundingClientRect().top)')
                assert abs(peek_tops[0]-peek_tops[1])<=1,peek_tops
                footer_bottoms=page.locator('.agent-footer').evaluate_all('(items)=>items.map(e=>e.getBoundingClientRect().bottom)')
                assert abs(footer_bottoms[0]-footer_bottoms[1])<=1,footer_bottoms
                identities=page.locator('.agent-card').evaluate_all('(cards)=>cards.map(card=>({role:card.dataset.agentIdentity,colour:getComputedStyle(card.querySelector(".agent-mark")).color,path:card.querySelector(".agent-mark path").getAttribute("d")}))')
                assert identities[0]['role']=='reviewer' and identities[1]['role']=='architect',identities
                assert identities[0]['colour']!=identities[1]['colour'] and identities[0]['path']!=identities[1]['path'],identities
                assert page.locator('.ac-peek').last.inner_text().startswith('Lit component_')
                page.get_by_role('button',name='En cours',exact=True).click()
                expect(page.locator('.agent-card')).to_have_count(1)
                expect(page.get_by_role('button',name='En cours',exact=True)).to_have_attribute('aria-pressed','true')
                assert page.locator('.agent-card').get_attribute('data-agent-identity')=='architect'
                page.locator('.agent-inspect').click()
                expect(page.locator('#agentInspector')).to_be_visible()
                mark=page.locator('#aiTitle .agent-mark')
                assert mark.get_attribute('data-agent-identity')=='architect'
                assert mark.evaluate('e=>getComputedStyle(e).color')==identities[1]['colour']
                assert mark.locator('path').get_attribute('d')==identities[1]['path']
                page.locator('#aiClose').click()
                page.get_by_role('button',name='Erreurs',exact=True).click()
                expect(page.locator('.agent-card')).to_have_count(0)
                expect(page.locator('.agents-empty')).to_be_visible()
                page.get_by_role('button',name='Tous',exact=True).click()
                expect(page.locator('.agent-card')).to_have_count(2)
                assert page.evaluate('progressProblems')==[],page.evaluate('progressProblems')
                page.locator('#feed').evaluate('e=>e.scrollTop=e.scrollHeight')
                page.screenshot(path=str(root/'artifacts/ui/live-workers-stable.png'))
                page.screenshot(path=str(root/'artifacts/ui/agent-identities-dark.png'))
                page.locator('#themeToggle').click()
                page.mouse.move(0,0)
                expect(page.locator('.agent-card').first).to_have_css('background-color','rgb(255, 255, 255)')
                expect(page.locator('#themeToggle')).to_have_css('background-color','rgb(255, 255, 255)')
                colours=page.locator('.agent-card .agent-mark').evaluate_all('(marks)=>marks.map(mark=>getComputedStyle(mark).color)')
                assert colours[0]!=colours[1] and colours[0]!=identities[0]['colour'],colours
                page.screenshot(path=str(root/'artifacts/ui/agent-identities-light.png'))
                page.evaluate('progressObserver.disconnect()')
                release.set()
                expect(page.locator('#send')).to_contain_text('ENVOYER')
                browser.close()
            print('✓ streaming : barre et compteurs stables sur 30 événements, cartes et actions alignées, modèle identique avant/après terminaison')
        finally:
            release.set();server.should_exit=True;thread.join(timeout=10);T.set_workdir(previous_workdir)


if __name__=='__main__': main()
