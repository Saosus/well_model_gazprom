"""Stage 6: bounded batch runs and measured-flow calibration; no LLM arithmetic."""
import asyncio
import copy
import math
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError, model_validator
from shared.contracts import ApiModel, CalculateRequest, CalculateResponse, NonNegative, Positive
from shared.validation import preview
from .assistant.progress import calculation_stages


class Measurement(ApiModel):
    q_liq: NonNegative
    p_wf: Positive | None = None


class WellCase(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    parameters: dict
    measured: Measurement | None = None


class BatchRequest(ApiModel):
    wells: Annotated[list[WellCase], Field(min_length=1, max_length=8)]
    deviation_threshold: NonNegative = 10

    @model_validator(mode='after')
    def unique_names(self):
        names = [w.name.strip().casefold() for w in self.wells]
        if not all(names) or len(set(names)) != len(names):
            raise ValueError('Названия скважин должны быть непустыми и уникальными.')
        return self


class CalibrationRequest(ApiModel):
    well: WellCase
    pi_min: NonNegative
    pi_max: NonNegative
    count: Annotated[int, Field(ge=2, le=8)] = 5

    @model_validator(mode='after')
    def check_range(self):
        if self.well.measured is None:
            raise ValueError('Укажите фактический дебит жидкости.')
        if self.pi_max <= self.pi_min:
            raise ValueError('Верхняя граница PI должна быть больше нижней.')
        return self


def compare(point, measured, threshold=10):
    if measured is None:
        return None
    def metric(model, actual):
        delta = model - actual
        percent = delta / actual * 100 if actual else None
        return {'model': model, 'actual': actual, 'delta': delta,
                'percent': percent if percent is not None and math.isfinite(percent) else None}
    flow = metric(point.q_liq, measured.q_liq)
    pressure = metric(point.p_wf, measured.p_wf) if measured.p_wf is not None else None
    flagged = any(abs(m['percent']) > threshold if m['percent'] is not None else m['delta'] != 0
                  for m in (flow, pressure) if m is not None)
    notes = []
    if flagged:
        notes.append('Проверьте сопоставимость условий замера и исходных данных; возможны ошибки PI, свойств флюида, геометрии или ограничения модели.')
    if measured.q_liq == 0:
        notes.append('Фактический дебит равен нулю: относительное отклонение не определено.')
    return {'flow': flow, 'pressure': pressure, 'flagged': flagged, 'threshold': threshold, 'notes': notes}


def json_safe(value):
    # Preserve invalid-row diagnostics without emitting NaN/Inf in JSON.
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


async def evaluate(case, calculate, threshold=10, timeout=40):
    raw = copy.deepcopy(case.parameters)
    row = {'name': case.name, 'parameters': json_safe(raw), 'measured': case.measured.model_dump() if case.measured else None,
           'calculation': None, 'comparison': None, 'issues': [], 'error': None, 'stages': []}
    try:
        issues = preview(raw)
        row['issues'] = issues
        if any(i['level'] == 'error' for i in issues):
            row['error'] = 'Некорректные исходные данные.'
            return row
        # Batch files must explicitly contain all fields, including optional defaults.
        from .assistant.parameters import validation, flatten, label
        checked, req = validation(flatten(raw))
        if not checked['valid']:
            row['error'] = 'Проверьте параметры: ' + '; '.join([label(p) for p in checked['missing']] +
                [f'{label(p)} (укажите значение явно)' for p in checked['defaults_requiring_consent']] +
                [f"{label(i['path'])}: {i['message']}" for i in checked['errors']])
            return row
        row['parameters'] = req.model_dump()
        token = calculation_stages.set(row['stages'])
        try:
            async with asyncio.timeout(timeout):
                result = CalculateResponse.model_validate(await calculate(req))
            row['calculation'] = result.model_dump()
            row['comparison'] = compare(result.operating_point, case.measured, threshold)
        finally:
            calculation_stages.reset(token)
    except TimeoutError:
        row['error'] = 'Истекло время расчёта этой скважины.'
    except HTTPException as exc:
        row['error'] = str(exc.detail)
    except (ValidationError, ValueError, ArithmeticError, TypeError):
        row['error'] = 'Некорректные параметры или ответ расчётного сервиса.'
    return row


async def run_batch(spec, calculate):
    # Sequential execution, a total time budget with explicit status for unprocessed rows.
    loop = asyncio.get_running_loop(); deadline = loop.time() + 170
    rows = []
    for case in spec.wells:
        remaining = deadline - loop.time()
        if remaining <= 0:
            rows.append({'name':case.name,'parameters':json_safe(case.parameters),'measured':case.measured.model_dump() if case.measured else None,
                         'calculation':None,'comparison':None,'issues':[],'stages':[],'error':'Общий лимит времени исчерпан; скважина не рассчитана.'})
        else:
            rows.append(await evaluate(case, calculate, spec.deviation_threshold, min(40, remaining)))
    return {'rows':rows,'total':len(rows),'successful':sum(r['calculation'] is not None for r in rows),
            'flagged':sum(bool(r['comparison'] and r['comparison']['flagged']) for r in rows),
            'deviation_threshold':spec.deviation_threshold}


async def calibrate(spec, calculate):
    baseline = await evaluate(spec.well, calculate, timeout=35)
    # Invalid input cannot be corrected silently by changing PI.
    if any(i['level']=='error' for i in baseline['issues']) or 'Проверьте параметры' in (baseline['error'] or ''):
        return {'baseline':baseline,'rows':[],'best_index':None}
    rows=[]; deadline=asyncio.get_running_loop().time()+130
    for i in range(spec.count):
        pi = spec.pi_max if i == spec.count-1 else spec.pi_min+(spec.pi_max-spec.pi_min)*(i/(spec.count-1))
        if pi == baseline['parameters']['reservoir']['pi']:
            row=copy.deepcopy(baseline)
        else:
            params=copy.deepcopy(spec.well.parameters); params['reservoir']['pi']=pi
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:
                row={'name':spec.well.name,'parameters':params,'measured':spec.well.measured.model_dump(),'calculation':None,'comparison':None,'issues':[],'stages':[],'error':'Общий лимит времени исчерпан.'}
            else:
                row=await evaluate(WellCase(name=spec.well.name,parameters=params,measured=spec.well.measured),calculate,timeout=min(35,remaining))
        row['pi']=pi; rows.append(row)
    good=[i for i,r in enumerate(rows) if r['comparison'] is not None]
    best=min(good,key=lambda i:abs(rows[i]['comparison']['flow']['delta'])) if good else None
    return {'baseline':baseline,'rows':rows,'best_index':best}


def make_analysis_router(calculate):
    router=APIRouter(prefix='/api/analysis',tags=['reports and batch'])
    @router.post('/batch',summary='Пакетный расчёт скважин и сравнение с замерами')
    async def batch(spec: BatchRequest):
        return await run_batch(spec,calculate)
    @router.post('/calibrate',summary='Подбор PI по фактическому дебиту в заданных границах')
    async def calibration(spec: CalibrationRequest):
        return await calibrate(spec,calculate)
    return router
