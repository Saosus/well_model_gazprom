import copy
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from services.backend.scenarios import make_scenario_router
from tests.test_assistant import EXAMPLE, RESULT, Fake, END, setup


def client_for(calc):
    app=FastAPI(); app.include_router(make_scenario_router(calc)); return TestClient(app)


def spec(**kw):
    return {'baseline':copy.deepcopy(EXAMPLE),'parameter':'well.p_wh','values':[5,10,15], 'objective':'max_flow',**kw}


def test_sweep_preserves_base_and_chooses_best():
    async def calc(req):
        out=RESULT.model_dump(); out['operating_point']['q_liq']=20-req.well.p_wh
        return out
    mock=AsyncMock(side_effect=calc)
    payload=spec(); out=client_for(mock).post('/api/scenarios/run',json=payload).json()
    assert out['best_index']==0
    assert out['baseline']['parameters']==EXAMPLE
    assert [r['calculation']['operating_point']['q_liq'] for r in out['rows']]==[15,10,5]
    assert [r['delta']['q_liq'] for r in out['rows']]==[5,0,-5]
    assert mock.await_count==3 # baseline reused when candidate equals base
    assert all(r['parameters']['reservoir']==EXAMPLE['reservoir'] for r in out['rows'])


def test_invalid_and_failed_candidates_have_no_result():
    async def calc(req):
        if req.well.p_wh==20: raise HTTPException(400,'NODAL — нет пересечения')
        return RESULT
    mock=AsyncMock(side_effect=calc)
    out=client_for(mock).post('/api/scenarios/run',json=spec(values=[-1,20,5])).json()
    assert out['best_index']==2
    assert out['rows'][0]['calculation'] is None and out['rows'][0]['error']
    assert out['rows'][1]['calculation'] is None and 'NODAL' in out['rows'][1]['error']
    assert out['rows'][0]['delta'] is None
    assert mock.await_count==3

@pytest.mark.parametrize('change',[{'values':[1]*9},{'values':[1,1]},{'parameter':'bad'},{'baseline':{}},{'values':['5']}])
def test_reject_invalid_spec(change):
    calc=AsyncMock(return_value=RESULT)
    assert client_for(calc).post('/api/scenarios/run',json=spec(**change)).status_code==422
    calc.assert_not_awaited()


def test_chat_scenario_and_optimization_keep_session(monkeypatch):
    calc=AsyncMock(return_value=RESULT)
    client=setup(monkeypatch,Fake(END,END),calc)
    first=client.post('/api/assistant/chat',json={'message':json.dumps(EXAMPLE)}).json()
    def send(text,**kw): return client.post('/api/assistant/chat',json={'message':text,'session_id':first['session_id'],**kw}).json()
    result=send('Что если устьевое давление 15 атм?')
    assert result['mode']=='scenario' and result['scenario']['rows'][0]['value']==15
    assert result['parameters']==EXAMPLE
    result=send('Подбери устьевое давление от 5 до 15 атм, 5 вариантов, максимум дебита')
    assert result['scenario']['best_index']==0 and len(result['scenario']['rows'])==5
    assert result['parameters']==EXAMPLE
    assert send('Что если устьевое давление 15 атм?',conversation_only=True)['scenario'] is None
    assert send('Что такое IPR?')['mode']=='conversation'
    assert calc.await_count==8


def test_real_http_scenarios_match_independent_calculations():
    from scripts.verification.http_stack import running_stack
    with running_stack() as (http, urls):
        url=urls['backend']
        response=http.post(url+'/api/scenarios/run',json=spec(values=[5,15]))
        assert response.status_code==200, response.text
        result=response.json()
        for row in [result['baseline'], *result['rows']]:
            direct=http.post(url+'/api/calculate',json=row['parameters'])
            assert direct.status_code==200, direct.text
            assert direct.json()==row['calculation']
            assert [s['stage'] for s in row['stages']]==['IPR','VLP','NODAL']
        assert result['rows'][0]['calculation']['operating_point']['q_liq'] > result['rows'][1]['calculation']['operating_point']['q_liq']
        assert result['best_index']==0
