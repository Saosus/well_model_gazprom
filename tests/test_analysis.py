import copy
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from services.backend.analysis import make_analysis_router, compare, Measurement
from tests.test_assistant import EXAMPLE, RESULT, Fake, END, setup


def case(name='A',params=None,q=100,p=None):
    return {'name':name,'parameters':copy.deepcopy(EXAMPLE if params is None else params), 'measured':{'q_liq':q,'p_wf':p}}


def client(calc):
    app=FastAPI();app.include_router(make_analysis_router(calc));return TestClient(app)


def test_batch_isolated_errors_and_measurements():
    async def calc(req):
        if req.well.p_wh==20: raise HTTPException(400,'NODAL — нет пересечения')
        return RESULT
    mock=AsyncMock(side_effect=calc)
    invalid=copy.deepcopy(EXAMPLE);invalid['pvt']['wct']=110
    failed=copy.deepcopy(EXAMPLE);failed['well']['p_wh']=20
    actual=RESULT.operating_point.q_liq
    out=client(mock).post('/api/analysis/batch',json={'wells':[case(q=actual),case('B',invalid),case('C',failed),case('D',q=0)],'deviation_threshold':1})
    assert out.status_code==200,out.text
    data=out.json();assert data['successful']==2 and data['flagged']==1
    assert data['rows'][0]['comparison']['flow']['delta']==0
    assert data['rows'][1]['calculation'] is None and data['rows'][1]['issues']
    assert data['rows'][2]['calculation'] is None and 'NODAL' in data['rows'][2]['error']
    assert data['rows'][3]['comparison']['flow']['percent'] is None
    assert mock.await_count==3


def test_batch_no_measurements_and_no_implicit_defaults():
    calc=AsyncMock(return_value=RESULT)
    absent=copy.deepcopy(EXAMPLE);absent.pop('sampling')
    row=case();row['measured']=None
    result=client(calc).post('/api/analysis/batch',json={'wells':[row,case('B',absent)]}).json()
    assert result['rows'][0]['calculation'] and result['rows'][0]['comparison'] is None
    assert result['rows'][1]['calculation'] is None
    assert calc.await_count==1


def test_measurement_pressure_and_threshold():
    p=RESULT.operating_point
    result=compare(p,Measurement(q_liq=p.q_liq,p_wf=p.p_wf/2),10)
    assert result['pressure']['percent']==100 and result['flagged']
    assert result['flow']['percent']==0


def test_calibration_preserves_base_and_chooses_minimum_absolute_delta():
    async def calc(req):
        out=RESULT.model_dump();out['operating_point']['q_liq']=req.reservoir.pi*100
        if req.reservoir.pi==2: raise HTTPException(400,'NODAL — нет пересечения')
        return out
    mock=AsyncMock(side_effect=calc)
    payload={'well':case(q=145),'pi_min':1,'pi_max':2,'count':3}
    result=client(mock).post('/api/analysis/calibrate',json=payload)
    assert result.status_code==200,result.text
    data=result.json();assert data['best_index']==1
    assert data['baseline']['parameters']==EXAMPLE and data['rows'][1]['pi']==1.5
    assert data['rows'][2]['calculation'] is None
    assert mock.await_count==3


@pytest.mark.parametrize('change',[{'pi_min':-1},{'pi_max':0},{'count':9},{'count':1}])
def test_bad_calibration(change):
    calc=AsyncMock(return_value=RESULT)
    response=client(calc).post('/api/analysis/calibrate',json={'well':case(),'pi_min':0,'pi_max':2,'count':5,**change})
    assert response.status_code==422;calc.assert_not_awaited()


def test_chat_report_compare_and_calibrate(monkeypatch):
    calc=AsyncMock(return_value=RESULT); c=setup(monkeypatch,Fake(END,END),calc)
    base=c.post('/api/assistant/chat',json={'message':json.dumps(EXAMPLE)}).json()
    def send(text,**kw):return c.post('/api/assistant/chat',json={'message':text,'session_id':base['session_id'],**kw}).json()
    report=send('Сформируй отчёт');assert report['report']['calculation']==base['calculation'];assert report['mode']=='conversation'
    out=send('Сравни с фактическим дебитом 100 м³/сут');assert out['report']['comparison']['flow']['actual']==100
    assert send('Сформируй отчёт',conversation_only=True)['report'] is None
    out=send('Подбери PI по фактическому дебиту 100 м³/сут от 0.5 до 2, 3 варианта');assert len(out['analysis']['rows'])==3
    assert out['parameters']==EXAMPLE


def test_real_batch_and_calibration_match_http():
    from scripts.verification.http_stack import running_stack
    payload=json.load(open('examples/batch.json',encoding='utf-8'))
    with running_stack() as (http,urls):
        url=urls['backend']
        result=http.post(url+'/api/analysis/batch',json=payload);assert result.status_code==200,result.text
        rows=result.json()['rows'];assert all(r['calculation'] for r in rows)
        calibration=http.post(url+'/api/analysis/calibrate',json={'well':payload['wells'][0],'pi_min':.8,'pi_max':1.2,'count':3});assert calibration.status_code==200,calibration.text
        cal=calibration.json();assert cal['best_index'] is not None
        for row in rows+[cal['baseline'],*cal['rows']]:
            assert row['calculation'] is not None
            direct=http.post(url+'/api/calculate',json=row['parameters']);assert direct.status_code==200,direct.text
            assert direct.json()==row['calculation']
