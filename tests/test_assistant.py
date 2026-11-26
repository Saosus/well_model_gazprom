import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.backend.assistant.core import Assistant, Session, TOOLS, flatten
from services.backend.assistant.llm import ChatClient, LLMError, Settings
from services.backend.assistant.routes import make_router
from shared.contracts import CalculateResponse

EXAMPLE = json.loads((Path(__file__).parents[1] / 'examples/calculate.json').read_text())
RESULT = CalculateResponse.model_validate({'ipr': {'q_liq': [10, 0], 'p_wf': [1, 11]},
    'vlp': {'q_liq': [0, 10], 'p_wf': [1, 11]}, 'operating_point': {'q_liq': 5, 'p_wf': 6}})


def call(name, args=None):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'test-' + name,
        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args or {})}}]}


class Fake:
    def __init__(self, *messages):
        self.messages = iter(messages)
        self.seen = []

    async def complete(self, messages, tools):
        self.seen.append(copy.deepcopy(messages))
        assert tools in (TOOLS, [])
        return next(self.messages), {'prompt_tokens': 10, 'completion_tokens': 2}


END = {'role': 'assistant', 'content': 'Готово.'}


def setup(monkeypatch, fake, calc, steps=6):
    monkeypatch.setenv('LLM_BASE_URL', 'https://llm.example/v1')
    monkeypatch.setenv('LLM_MODEL', 'test-model')
    monkeypatch.setenv('LLM_MAX_TOOL_STEPS', str(steps))
    app = FastAPI()
    app.include_router(make_router(calc, client_factory=lambda _: fake))
    return TestClient(app)


def test_full_json_calculates_and_returns_exact_point(monkeypatch):
    fake = Fake(call('validate_parameters'), call('calculate_well'), {'role': 'assistant', 'content': 'Дебит 999999'})
    calc = AsyncMock(return_value=RESULT)
    client = setup(monkeypatch, fake, calc)
    response = client.post('/api/assistant/chat', json={'message': json.dumps(EXAMPLE)})
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'calculated'
    assert data['calculation'] == RESULT.model_dump()
    assert '999999' not in data['answer']
    assert calc.await_args.args[0].model_dump() == EXAMPLE
    assert [t['tool'] for t in data['tool_trace']] == ['validate_parameters', 'calculate_well']
    assert data['usage']['prompt_tokens'] == 20
    assert len(fake.seen) == 2
    assert fake.seen[-1][-1]['role'] == 'tool'


def test_missing_field_then_clarification(monkeypatch):
    partial = copy.deepcopy(EXAMPLE)
    del partial['reservoir']['p_res']
    fake = Fake(call('calculate_well'), END, call('update_parameters', {'updates': [{'path': 'reservoir.p_res', 'value': 200, 'evidence': 'Пластовое давление 200 атм'}]}), call('calculate_well'), END)
    calc = AsyncMock(return_value=RESULT)
    client = setup(monkeypatch, fake, calc)
    first = client.post('/api/assistant/chat', json={'message': json.dumps(partial)}).json()
    assert first['status'] == 'needs_input'
    assert 'reservoir.p_res' in first['validation']['missing']
    calc.assert_not_awaited()
    second = client.post('/api/assistant/chat', json={'message': 'Пластовое давление 200 атм', 'session_id': first['session_id']}).json()
    assert second['status'] == 'calculated'
    assert second['parameters'] == EXAMPLE
    calc.assert_awaited_once()
    assert any(m['role'] == 'user' and m['content'] == json.dumps(partial) for m in fake.seen[2])


def test_defaults_require_explicit_consent(monkeypatch):
    partial = copy.deepcopy(EXAMPLE)
    del partial['sampling']; del partial['well']['roughness']
    calc = AsyncMock(return_value=RESULT)
    client = setup(monkeypatch, Fake(call('calculate_well'), END, call('calculate_well'), END), calc)
    first = client.post('/api/assistant/chat', json={'message': json.dumps(partial)}).json()
    assert len(first['validation']['defaults_requiring_consent']) == 4
    calc.assert_not_awaited()
    second = client.post('/api/assistant/chat', json={'message': 'Использовать значения по умолчанию', 'session_id': first['session_id']}).json()
    assert second['status'] == 'calculated'
    assert second['parameters']['sampling']['q_max'] == 400


def test_rejects_unquoted_value_and_unknown_tool(monkeypatch):
    fake = Fake(call('update_parameters', {'updates': [{'path': 'reservoir.p_res', 'value': 999, 'evidence': 'Давление 200'}]}), call('delete_everything'), END)
    calc = AsyncMock(return_value=RESULT)
    client = setup(monkeypatch, fake, calc)
    data = client.post('/api/assistant/chat', json={'message': 'Давление 200'}).json()
    assert data['parameters'] == {}
    assert [t['ok'] for t in data['tool_trace']] == [False, False]
    calc.assert_not_awaited()


def test_calculation_error_no_numeric_result(monkeypatch):
    calc = AsyncMock(side_effect=HTTPException(422, 'Nodal — нет пересечения'))
    client = setup(monkeypatch, Fake(call('calculate_well'), END), calc)
    data = client.post('/api/assistant/chat', json={'message': json.dumps(EXAMPLE)}).json()
    assert data['status'] == 'calculation_error' and data['calculation'] is None


def test_step_limit(monkeypatch):
    calc = AsyncMock(return_value=RESULT)
    client = setup(monkeypatch, Fake(call('get_current_parameters'), call('get_current_parameters')), calc, steps=2)
    data = client.post('/api/assistant/chat', json={'message': 'Привет'}).json()
    assert data['status'] == 'step_limit' and len(data['tool_trace']) == 2


def test_disabled_does_not_break_backend(monkeypatch):
    from services.backend.main import app
    monkeypatch.delenv('LLM_MODEL', raising=False)
    monkeypatch.delenv('LLM_BASE_URL', raising=False)
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/assistant/status').json()['configured'] is False
        assert client.post('/api/assistant/chat', json={'message': 'Привет'}).status_code == 503
        assert '/api/calculate' in client.get('/openapi.json').json()['paths']


def test_llm_transport_and_secret(monkeypatch):
    import asyncio
    settings = Settings('https://example.test/v1', 'model', 'secret')
    assert 'secret' not in repr(settings)
    def handler(req):
        assert req.headers['Authorization'] == 'Bearer secret'
        assert str(req.url) == 'https://example.test/v1/chat/completions'
        assert json.loads(req.content)['tools'] == TOOLS
        return httpx.Response(200, json={'choices': [{'message': END}], 'usage': {'prompt_tokens': 1}})
    msg, usage = asyncio.run(ChatClient(settings, httpx.MockTransport(handler)).complete([], TOOLS))
    assert msg == END and usage['prompt_tokens'] == 1
    bad = ChatClient(settings, httpx.MockTransport(lambda _: httpx.Response(401, text='secret')))
    with pytest.raises(LLMError) as error:
        asyncio.run(bad.complete([], TOOLS))
    assert 'secret' not in str(error.value)


def test_invalid_json_and_session_isolation(monkeypatch):
    calc = AsyncMock(return_value=RESULT)
    client = setup(monkeypatch, Fake(END, END), calc)
    first = client.post('/api/assistant/chat', json={'message': '{"reservoir":{"p_res":200}}'}).json()
    other = client.post('/api/assistant/chat', json={'message': 'Привет'}).json()
    assert other['session_id'] != first['session_id'] and other['parameters'] == {}
    invalid = client.post('/api/assistant/chat', json={'message': '{"reservoir":{"bogus":200}}', 'session_id': first['session_id']}).json()
    assert invalid['status'] == 'invalid_input' and invalid['parameters'] == first['parameters']
    calc.assert_not_awaited()


def test_assistant_uses_actual_http_calculation_stack(monkeypatch):
    from scripts.verification.http_stack import running_stack
    from services.backend import main
    with running_stack() as (http, urls):
        expected = http.post(urls['backend'] + '/api/calculate', json=EXAMPLE)
        assert expected.status_code == 200, expected.text
        monkeypatch.setattr(main, 'SERVICE_URLS', {k: urls[k] for k in ('ipr', 'vlp', 'nodal')})
        client = setup(monkeypatch, Fake(call('calculate_well'), END), main.calculate)
        result = client.post('/api/assistant/chat', json={'message': json.dumps(EXAMPLE)}).json()
        assert result['status'] == 'calculated'
        assert result['calculation'] == expected.json()
        assert [s['stage'] for s in result['calculation_stages']] == ['IPR', 'VLP', 'NODAL']
        assert all(s['ok'] and s['duration_ms'] >= 0 for s in result['calculation_stages'])
