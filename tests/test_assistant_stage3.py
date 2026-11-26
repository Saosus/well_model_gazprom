"""Dialog routing and grounding regressions for MVP stage 3."""
import copy
import json
from unittest.mock import AsyncMock
import pytest
from tests.test_assistant import EXAMPLE, RESULT, Fake, END, setup, call

@pytest.mark.parametrize('text', ['Привет', 'Что такое IPR?', 'Как рассчитать дебит?', 'Обводнённость 50 % — это много?', 'Что будет, если устьевое давление 10 атм?', 'Спасибо!', 'Какая погода?', 'Расскажи про VLP'])
def test_questions_are_not_calculation_requests(monkeypatch, text):
    fake = Fake({'role':'assistant','content':'Ответ по существу.'})
    calc = AsyncMock(return_value=RESULT)
    client = setup(monkeypatch, fake, calc)
    out = client.post('/api/assistant/chat', json={'message':text}).json()
    assert out['mode']=='conversation' and out['status']=='ok'
    assert out['answer']=='Ответ по существу.'
    assert out['parameters']=={} and out['calculation'] is None
    assert not out['validation']['missing'] and not out['validation']['defaults_requiring_consent']
    calc.assert_not_awaited()

def test_question_during_clarification_does_not_reset_or_calculate(monkeypatch):
    partial=copy.deepcopy(EXAMPLE); del partial['reservoir']['p_res']
    calc=AsyncMock(return_value=RESULT)
    client=setup(monkeypatch,Fake(END,END,END,END),calc)
    first=client.post('/api/assistant/chat',json={'message':json.dumps(partial)}).json()
    sid=first['session_id']
    def send(text): return client.post('/api/assistant/chat',json={'session_id':sid,'message':text}).json()
    assert first['status']=='needs_input'
    for text in ('Что такое пластовое давление?', 'Привет'):
        out=send(text)
        assert out['mode']=='conversation' and out['parameters']==partial
    calc.assert_not_awaited()
    out=send('200 атм')
    assert out['status']=='calculated' and out['parameters']==EXAMPLE
    calc.assert_awaited_once()

def test_followup_uses_grounded_result_and_never_recalculates(monkeypatch):
    calc=AsyncMock(return_value=RESULT)
    fake=Fake(END, call('calculate_well'), {'role':'assistant','content':'Дебит: [q_liq], давление: [p_wf].'}, {'role':'assistant','content':'Дебит 999999'})
    client=setup(monkeypatch,fake,calc)
    first=client.post('/api/assistant/chat',json={'message':json.dumps(EXAMPLE)}).json()
    sid=first['session_id']
    def send(text): return client.post('/api/assistant/chat',json={'session_id':sid,'message':text}).json()
    out=send('Какой получился дебит?')
    assert out['mode']=='conversation' and out['calculation'] is None
    assert '5 м³/сут' in out['answer'] and '6 атм' in out['answer']
    assert out['tool_trace'][0]['ok'] is False
    assert '999999' not in send('Объясни результат')['answer']
    calc.assert_awaited_once()

def test_manual_form_conflict_is_read_only(monkeypatch):
    calc=AsyncMock(return_value=RESULT)
    client=setup(monkeypatch,Fake(END,END),calc)
    first=client.post('/api/assistant/chat',json={'message':json.dumps(EXAMPLE)}).json()
    def send(text): return client.post('/api/assistant/chat',json={'session_id':first['session_id'],'message':text,'conversation_only':True}).json()
    assert send('Что такое VLP?')['mode']=='conversation'
    blocked=send('Устьевое давление 20 атм')
    assert blocked['status']=='form_conflict' and blocked['parameters']==EXAMPLE
    calc.assert_awaited_once()
