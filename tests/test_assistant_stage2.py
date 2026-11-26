"""Stage 2 regressions: deterministic input, dialog and real HTTP calculations."""
import copy
import json
from unittest.mock import AsyncMock
import pytest
from tests.test_assistant import EXAMPLE, RESULT, Fake, END, setup, call
from services.backend.assistant.parameters import parse_text, parse_json, validation, flatten

TEXT = ('Пластовое давление 200 атм; коэффициент продуктивности 1 м³/(сут·атм); '
        'давление насыщения 150 атм; MD [0, 1000, 1500] м; TVD [0, 1000, 1100] м; '
        'диаметр ЭК 100 мм; диаметр НКТ 62 мм; глубина спуска НКТ 1 км; '
        'устьевое давление 10 атм; геотермический градиент 3 °C/100 м; '
        'глубина пласта 1500 м; шероховатость 0,0001; обводненность 100 процентов; '
        'газовый фактор 100 м³/т; плотность нефти 0,8; плотность газа 0,7; '
        'плотность воды 1; пластовая температура 90 °C; число точек IPR 2; '
        'число точек VLP 2; максимальный дебит 200 м³/сут.')

@pytest.mark.parametrize('text,path,value',[
    ('Пластовое давление 20 МПа','reservoir.p_res',20e6/101325),
    ('Устьевое давление 5 бар','well.p_wh',5e5/101325),
    ('Диаметр НКТ 62 мм','well.tubing.d',.062),
    ('Обводненность 0,5 доли','pvt.wct',50),
    ('Пластовая температура 363,15 K','pvt.t_res',90),
    ('Пластовая температура 194 °F','pvt.t_res',90),
    ('Геотермический градиент 30 °C/км','well.geo_grad',3),
    ('Максимальный дебит 10 м³/ч','sampling.q_max',240),
    ('Коэффициент продуктивности 10 м³/(сут·МПа)','reservoir.pi',1.01325),
])
def test_unit_conversion(text,path,value):
    patch,errors,provenance=parse_text(text)
    assert not errors
    assert patch[path]==pytest.approx(value)
    assert provenance[path]['source']==text

@pytest.mark.parametrize('text,path',[
    ('Устьевое давление 5','well.p_wh'),
    ('Устьевое давление 5 бар изб.','well.p_wh'),
    ('Пластовое давление 200 атм или 300 атм','reservoir.p_res'),
    ('Пластовое давление 200 атм; пластовое давление 300 атм','reservoir.p_res'),
    ('Пластовое давление около 200 атм','reservoir.p_res'),
    ('Газовый фактор 100 м³/м³','pvt.rp'),
])
def test_ambiguity_not_guessed(text,path):
    patch,errors,_=parse_text(text)
    assert path in errors and path not in patch


def test_all_fields_in_prose():
    patch,errors,_=parse_text(TEXT)
    assert not errors and patch==flatten(EXAMPLE)
    assert validation(patch)[0]['valid']


def test_multiple_physical_errors_and_rows():
    data=flatten(EXAMPLE)
    data.update({'well.casing.d':.05,'well.tubing.d':.06,'well.h_res':1600,'pvt.wct':101,
                 'well.inclinometry.MD':[0,1000,900],'well.inclinometry.TVD':[0,1100,1200]})
    check,_=validation(data)
    assert not check['valid']
    assert {'well.casing.d','well.tubing.d','well.h_res','pvt.wct','well.inclinometry.MD','well.inclinometry.TVD'} <= {e['path'] for e in check['errors']}
    assert any(e.get('row')==2 for e in check['errors']) and any(e.get('row')==3 for e in check['errors'])


def test_warnings_are_nonblocking():
    data=flatten(EXAMPLE); data['reservoir.pb']=250
    check,_=validation(data)
    assert check['valid'] and check['warnings']


def test_temperature_domain():
    data=flatten(EXAMPLE); data['pvt.wct']=50; data['well.geo_grad']=40
    check,_=validation(data)
    assert not check['valid'] and any('−273,15' in e['message'] for e in check['errors'])


def test_service_json_and_conflict():
    ipr={**EXAMPLE['reservoir'],'wct':100,'points':2}
    vlp={**EXAMPLE['well'],'pvt':EXAMPLE['pvt'],'points':2,'q_max':200}
    p,e,_=parse_json(json.dumps({'ipr':ipr,'vlp':vlp})); assert not e and p==flatten(EXAMPLE)
    p,e,_=parse_json(json.dumps(ipr)); assert not e and p['pvt.wct']==100
    p,e,_=parse_json(json.dumps(vlp)); assert not e and p['well.p_wh']==10
    ipr['wct']=50
    p,e,_=parse_json(json.dumps({'ipr':ipr,'vlp':vlp})); assert 'pvt.wct' in e and 'pvt.wct' not in p
    with pytest.raises(ValueError): parse_json('{"reservoir":{"p_res":100,"p_res":200}}')


def test_conflict_survives_turns_then_corrects(monkeypatch):
    calc=AsyncMock(return_value=RESULT)
    client=setup(monkeypatch,Fake(END,call('calculate_well'),END,END),calc)
    partial=copy.deepcopy(EXAMPLE); del partial['reservoir']['p_res']
    first=client.post('/api/assistant/chat',json={'message':json.dumps(partial)}).json()
    sid=first['session_id']
    second=client.post('/api/assistant/chat',json={'message':'Пластовое давление 200 атм или 300 атм','session_id':sid}).json()
    assert second['status']=='needs_input' and second['validation']['errors']
    calc.assert_not_awaited()
    third=client.post('/api/assistant/chat',json={'message':'200 атм','session_id':sid}).json()
    assert third['status']=='calculated' and third['parameters']==EXAMPLE
    assert third['provenance']['reservoir.p_res']['source']=='200 атм'
    calc.assert_awaited_once()


def test_llm_wrong_field_rejected(monkeypatch):
    calc=AsyncMock(return_value=RESULT)
    client=setup(monkeypatch,Fake(call('update_parameters',{'updates':[{'path':'reservoir.pb','value':200,'evidence':'Пластовое давление 200 атм'}]}),END),calc)
    response=client.post('/api/assistant/chat',json={'message':'Пластовое давление 200 атм'}).json()
    assert response['parameters']=={'reservoir':{'p_res':200}}
    assert response['tool_trace'][0]['ok'] is False


def test_no_calculation_when_user_prohibits(monkeypatch):
    calc=AsyncMock(return_value=RESULT)
    client=setup(monkeypatch,Fake(call('calculate_well'),END),calc)
    response=client.post('/api/assistant/chat',json={'message':TEXT+' Пока не рассчитывай.'}).json()
    assert response['calculation'] is None
    calc.assert_not_awaited()


def test_real_http_text_equals_manual(monkeypatch):
    from scripts.verification.http_stack import running_stack
    from services.backend import main
    with running_stack() as (http,urls):
        expected=http.post(urls['backend']+'/api/calculate',json=EXAMPLE)
        assert expected.status_code==200
        monkeypatch.setattr(main,'SERVICE_URLS',{k:urls[k] for k in ('ipr','vlp','nodal')})
        client=setup(monkeypatch,Fake(END),main.calculate)
        out=client.post('/api/assistant/chat',json={'message':TEXT}).json()
        assert out['status']=='calculated' and out['calculation']==expected.json()
        assert out['tool_trace'][-1]['trigger']=='backend'


def test_llm_wrong_value_rejected(monkeypatch):
    calc=AsyncMock(return_value=RESULT)
    client=setup(monkeypatch,Fake(call('update_parameters',{'updates':[{'path':'reservoir.p_res','value':999,'evidence':'Пластовое давление 200 атм'}]}),END),calc)
    response=client.post('/api/assistant/chat',json={'message':'Пластовое давление 200 атм'}).json()
    assert response['parameters']=={'reservoir':{'p_res':200}}
    assert response['tool_trace'][0]['ok'] is False


def test_invalid_json_survey_keeps_valid_cells_and_addresses_each_error():
    data = copy.deepcopy(EXAMPLE)
    data['well']['inclinometry']['TVD'] = [0, "ошибка", None]
    fields, pending, _ = parse_json(json.dumps(data))
    check, request = validation(fields, pending)
    assert request is None and not check['valid']
    assert fields['well.inclinometry.TVD'] == [0, None, None]
    assert {e['row'] for e in check['errors'] if e['path'] == 'well.inclinometry.TVD'} == {2, 3}
