"""Optional live browser regression: real IPR/VLP/Nodal/backend, deterministic mock LLM.
Run from project root: python -m scripts.check_assistant_ui
Requires playwright and a Chromium browser (BROWSER_EXECUTABLE optional).
"""
import copy
import functools
import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect
from scripts.verification.http_stack import running_stack

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / 'examples/calculate.json').read_text())
TEXT = (ROOT / 'examples/assistant_text.txt').read_text(encoding='utf-8')

@contextmanager
def server(handler):
    srv = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{srv.server_port}'
    finally:
        srv.shutdown(); srv.server_close(); thread.join()

class MockLLM(BaseHTTPRequestHandler):
    fail = False
    def log_message(self, *args): pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(503 if self.fail else 200)
        self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(json.dumps({'choices':[{'message':{'role':'assistant','content':'Готово.'}}], 'usage':{'prompt_tokens':10,'completion_tokens':2}}).encode())

class Gateway(SimpleHTTPRequestHandler):
    def __init__(self, *args, backend, **kwargs):
        self.backend = backend
        super().__init__(*args, directory=str(ROOT/'frontend'), **kwargs)
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path.startswith('/api/'): self.proxy('GET')
        else: super().do_GET()
    def do_POST(self): self.proxy('POST')
    def proxy(self, method):
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        r=httpx.request(method,self.backend+self.path,content=body,headers={'Content-Type':'application/json'},timeout=190,trust_env=False)
        self.send_response(r.status_code); self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(r.content)


def run():
    with server(MockLLM) as llm:
        os.environ.update(LLM_BASE_URL=llm+'/v1', LLM_MODEL='test-model', LLM_API_KEY='')
        with running_stack() as (http,urls), server(functools.partial(Gateway,backend=urls['backend'])) as site, sync_playwright() as pw:
            expected=http.post(urls['backend']+'/api/calculate',json=EXAMPLE).json()
            browser=pw.chromium.launch(executable_path=os.getenv('BROWSER_EXECUTABLE') or None, args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1600,'height':1000})
            errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(site); page.set_default_timeout(30000)
            expect(page.locator('#chat-send')).to_be_enabled(); expect(page.locator('#chart')).to_be_visible()
            page.evaluate('data => { for(const input of document.querySelectorAll("#parameters input[name]")) { const v=input.name.split(".").reduce((o,k)=>o[k],data); input.value=Array.isArray(v)?v.join(" "):v; input.dispatchEvent(new Event("input",{bubbles:true})); } }', EXAMPLE)
            page.locator('#calculate').click(); expect(page.locator('#chart')).to_be_visible()
            manual=page.locator('#flow').inner_text(),page.locator('#pressure').inner_text()
            def send(text):
                page.locator('#chat-input').fill(text)
                with page.expect_response(lambda r:r.url.endswith('/api/assistant/chat'),timeout=30000) as info:
                    page.locator('#chat-send').click()
                result=info.value.json(); expect(page.locator('#chat-stop')).to_be_hidden(); return result
            def same_point(result):
                assert result['calculation']==expected
                expect(page.locator('#chart')).to_be_visible()
                assert (page.locator('#flow').inner_text(),page.locator('#pressure').inner_text())==manual
            with page.expect_response(lambda r:r.url.endswith('/api/assistant/chat'),timeout=30000) as info:
                page.locator('#chat-use-form').click()
            result=info.value.json(); expect(page.locator('#chat-stop')).to_be_hidden(); same_point(result)
            assert send('Привет')['mode']=='conversation'
            expect(page.locator('#chart')).to_be_visible()
            assert send('Что такое VLP?')['mode']=='conversation'
            expect(page.locator('#chart')).to_be_visible()
            # The exact same session and visual history survive a page refresh.
            previous=page.locator('#chat-messages').inner_text()
            page.reload(); expect(page.locator('#chat-send')).to_be_enabled()
            assert page.locator('#chat-messages').inner_text()==previous
            expect(page.locator('#chart')).to_be_visible()
            scenario=send('Что если устьевое давление 15 атм?')
            assert scenario['mode']=='scenario' and scenario['scenario']['rows'][0]['value']==15
            assert scenario['session_id']==result['session_id']
            expect(page.locator('#scenario-results')).to_be_visible()
            expect(page.locator('#scenario-chart')).to_be_visible()
            expect(page.locator('[name="well.p_wh"]')).to_have_value('10')
            page.locator('#scenario-min').fill('5'); page.locator('#scenario-max').fill('15'); page.locator('#scenario-count').fill('3')
            page.locator('#scenario-objective').select_option('max_flow')
            with page.expect_response(lambda r:r.url.endswith('/api/scenarios/run')) as sr:
                page.locator('#scenario-run').click()
            assert sr.value.ok
            expect(page.locator('#scenario-rows tr')).to_have_count(4)
            expect(page.locator('#scenario-rows tr.best')).to_have_count(1)

            page.locator('#chat-new').click(); same_point(send(json.dumps(EXAMPLE)))
            page.locator('#chat-new').click(); same_point(send(TEXT))
            page.locator('#chat-new').click(); partial=copy.deepcopy(EXAMPLE); del partial['reservoir']['p_res']
            result=send(json.dumps(partial)); assert result['status']=='needs_input'
            expect(page.locator('#chart')).to_be_hidden(); expect(page.locator('[name="reservoir.p_res"]')).to_have_value('')
            expect(page.locator('[name="reservoir.p_res"]')).to_have_attribute('aria-invalid','true')
            assert send('Что такое пластовое давление?')['mode']=='conversation'
            expect(page.locator('[name="reservoir.p_res"]')).to_have_value('')
            same_point(send('Пластовое давление 200 атм'))
            result=send('Обводнённость 120 процентов; диаметр ЭК 50 мм; диаметр НКТ 62 мм.')
            assert result['status']=='needs_input'; expect(page.locator('#chart')).to_be_hidden()
            for path in ['pvt.wct','well.casing.d','well.tubing.d']:
                expect(page.locator(f'[name="{path}"]')).to_have_attribute('aria-invalid','true')
            result=send('Обводнённость 100 процентов; диаметр ЭК 100 мм.'); same_point(result)
            page.locator('[name="well.p_wh"]').fill('500'); expect(page.locator('#chat-send')).to_be_enabled()
            assert send('Что такое IPR?')['mode']=='conversation'
            expect(page.locator('[name="well.p_wh"]')).to_have_value('500')
            with page.expect_response(lambda r:r.url.endswith('/api/assistant/chat'),timeout=30000) as info: page.locator('#chat-use-form').click()
            result=info.value.json(); expect(page.locator('#chat-stop')).to_be_hidden(); assert result['status']=='calculation_error'
            expect(page.locator('#chart')).to_be_hidden(); expect(page.locator('#flow')).to_have_text('—')
            MockLLM.fail=True
            result=send('Устьевое давление 10 атм'); assert result['status']=='llm_error'
            expect(page.locator('#calculate')).to_be_enabled(); expect(page.locator('#chart')).to_be_hidden()
            page.locator('#calculate').click(); expect(page.locator('#chart')).to_be_visible()
            assert (page.locator('#flow').inner_text(),page.locator('#pressure').inner_text())==manual
            MockLLM.fail=False
            assert send('Привет')['mode']=='conversation'
            expect(page.locator('#chart')).to_be_visible()
            assert send('Что такое VLP?')['mode']=='conversation'
            expect(page.locator('#chart')).to_be_visible()
            # The exact same session and visual history survive a page refresh.
            previous=page.locator('#chat-messages').inner_text()
            page.reload(); expect(page.locator('#chat-send')).to_be_enabled()
            assert page.locator('#chat-messages').inner_text()==previous
            expect(page.locator('#chart')).to_be_visible()
            scenario=send('Что если устьевое давление 15 атм?')
            assert scenario['mode']=='scenario' and scenario['scenario']['rows'][0]['value']==15
            assert scenario['session_id']==result['session_id']
            expect(page.locator('#scenario-results')).to_be_visible()
            expect(page.locator('#scenario-chart')).to_be_visible()
            expect(page.locator('[name="well.p_wh"]')).to_have_value('10')
            page.locator('#scenario-min').fill('5'); page.locator('#scenario-max').fill('15'); page.locator('#scenario-count').fill('3')
            page.locator('#scenario-objective').select_option('max_flow')
            with page.expect_response(lambda r:r.url.endswith('/api/scenarios/run')) as sr:
                page.locator('#scenario-run').click()
            assert sr.value.ok
            expect(page.locator('#scenario-rows tr')).to_have_count(4)
            expect(page.locator('#scenario-rows tr.best')).to_have_count(1)

            page.locator('#chat-new').click(); same_point(send(json.dumps(EXAMPLE)))
            survey = copy.deepcopy(EXAMPLE)
            survey['well']['inclinometry'] = {'MD': [0, 500, 1000, 1500], 'TVD': [0, 501, 990, 1100]}
            result = send(json.dumps(survey))
            assert result['status'] == 'needs_input'
            assert page.locator('#stations tr').count() == 4
            cell = page.locator('[name="well.inclinometry.TVD.1"]')
            expect(cell).to_have_attribute('aria-invalid', 'true')
            expect(page.locator('#calculate')).to_be_disabled()
            cell.fill('500')
            expect(cell).to_have_attribute('aria-invalid', 'false')
            expect(page.locator('#calculate')).to_be_enabled()
            expect(page.locator('#chat-send')).to_be_enabled()
            page.locator('#chat-new').click()
            same_point(send(json.dumps(EXAMPLE)))
            if os.getenv('UI_SCREENSHOT'): page.screenshot(path=os.environ['UI_SCREENSHOT'],full_page=True)
            page.set_viewport_size({'width':390,'height':844}); assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert not errors,errors
            browser.close()
            print('Browser: manual, form import, JSON, text, clarification, field errors, divergence, no intersection, LLM outage, responsive layout — OK')

if __name__=='__main__': run()
