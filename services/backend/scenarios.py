"""Bounded scenario sweep; all operating points come from the existing orchestrator."""
import asyncio
import copy
import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from shared.contracts import CalculateResponse
from .assistant.parameters import FIELDS, flatten, unflatten, validation, label
from .assistant.progress import calculation_stages

PARAMETERS = ('well.p_wh','reservoir.p_res','reservoir.pi','reservoir.pb','pvt.wct','pvt.rp',
              'well.tubing.d','well.casing.d','well.tubing.h_mes','pvt.t_res','well.roughness')

class Sweep(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    baseline: dict
    parameter: str
    values: list[float] = Field(min_length=1, max_length=8)
    objective: Literal['compare','max_flow','min_pressure'] = 'compare'

    @model_validator(mode='after')
    def check(self):
        if self.parameter not in PARAMETERS:
            raise ValueError('Этот параметр не поддерживается для сценариев.')
        if len(set(self.values)) != len(self.values):
            raise ValueError('Значения сценариев должны быть различными.')
        return self

async def run_sweep(spec: Sweep, calculate):
    start = time.monotonic()
    base = flatten(spec.baseline)
    if set(base) - set(FIELDS):
        raise HTTPException(422, 'В исходных данных есть неизвестные поля.')
    report, request = validation(base)
    if not report['valid']:
        raise HTTPException(422, {'message':'Сначала заполните и проверьте базовый режим.','validation':report})
    async def evaluate(value, data):
        stages = []
        token = calculation_stages.set(stages)
        try:
            check, req = validation(data)
            row = {'value':value,'parameters':unflatten(data),'warnings':check['warnings'],'stages':stages,'calculation':None,'error':None}
            if not check['valid']:
                row['error'] = '; '.join(f"{label(e['path'])}: {e['message']}" for e in check['errors']) or 'Неполные данные.'
                return row
            try:
                row['calculation'] = CalculateResponse.model_validate(await calculate(req)).model_dump()
            except HTTPException as exc:
                row['error'] = str(exc.detail)
            except (ValidationError, ValueError, ArithmeticError):
                row['error'] = 'Расчётный сервис вернул некорректный результат.'
            return row
        finally:
            calculation_stages.reset(token)
    async with asyncio.timeout(175):
        baseline = await evaluate(base[spec.parameter], base)
        rows = []
        for value in spec.values:
            if value == baseline['value']:
                row = copy.deepcopy(baseline)
            else:
                data = {**base,spec.parameter:value}
                row = await evaluate(value,data)
            rows.append(row)
    good = [i for i,r in enumerate(rows) if r['calculation'] is not None]
    point = baseline['calculation']['operating_point'] if baseline['calculation'] else None
    for row in rows:
        p = row['calculation']['operating_point'] if row['calculation'] else None
        row['delta'] = {k:p[k]-point[k] for k in ('q_liq','p_wf')} if p and point else None
    best = None
    if good and spec.objective != 'compare':
        key = 'q_liq' if spec.objective == 'max_flow' else 'p_wf'
        best = (max if spec.objective == 'max_flow' else min)(good,key=lambda i:rows[i]['calculation']['operating_point'][key])
    return {'parameter':spec.parameter,'label':label(spec.parameter),'unit':FIELDS[spec.parameter],
            'objective':spec.objective,'baseline':baseline,'rows':rows,'best_index':best,
            'duration_ms':round((time.monotonic()-start)*1000)}

def make_scenario_router(calculate):
    router = APIRouter(prefix='/api/scenarios', tags=['scenarios'])
    @router.post('/run')
    async def run(spec: Sweep):
        try:
            return await run_sweep(spec, calculate)
        except TimeoutError:
            raise HTTPException(504, 'Серия расчётов не завершилась вовремя. Уменьшите число вариантов.') from None
    return router


def parse_scenario(text, baseline):
    """Explicit user commands only; never infer range limits or units with the LLM."""
    import re
    from .assistant.parameters import LABELS, PATHS, NUM, parse_text, normalize
    text = text.strip()
    compare = re.match(r'^(?:что\s+если|сравни(?:те)?|сценарий)\s*[:,]?\s+', text, re.I)
    optimize = re.match(r'^(?:подбери(?:те)?|оптимизируй(?:те)?)\s+', text, re.I)
    if not compare and not optimize:
        return None
    if compare:
        tail = text[compare.end():]
        patch, errors, _ = parse_text(tail)
        # Ordinary conceptual comparisons/questions remain with the conversational LLM.
        if not patch and not errors and not re.search(NUM, tail):
            return None
        if errors or len(patch)!=1:
            raise ValueError('Укажите один параметр и точное значение с единицей. Например: «Что если устьевое давление 15 атм?»')
        path, value = next(iter(patch.items()))
        if path not in PARAMETERS:
            raise ValueError('Этот параметр не поддерживается для сценариев. Выберите его в списке раздела «Сценарии».')
        return Sweep(baseline=baseline,parameter=path,values=[value])
    tail = text[optimize.end():]
    match = LABELS.match(tail)
    if not match:
        raise ValueError('Укажите параметр для подбора.')
    path = PATHS[int(match.lastgroup[1:])]
    # Explicit common unit after both endpoints; count and objective must be provided.
    m = re.fullmatch(rf'\s+от\s+({NUM})\s+до\s+({NUM})\s+(.+?),\s*(\d+)\s+вариант\w*,\s*(максимум дебита|минимум забойного давления)[.!?]?\s*',tail[match.end():],re.I)
    if not m:
        raise ValueError('Формат: «Подбери устьевое давление от 5 до 15 атм, 5 вариантов, максимум дебита».')
    lo,hi = [normalize(path,float(v.replace(',','.')),m[3]) for v in (m[1],m[2])]
    n=int(m[4]); objective='max_flow' if m[5].casefold()=='максимум дебита' else 'min_pressure'
    if path not in PARAMETERS:
        raise ValueError('Этот параметр не поддерживается для подбора.')
    if not 2<=n<=8 or lo>=hi:
        raise ValueError('Задайте возрастающий диапазон и от 2 до 8 вариантов.')
    return Sweep(baseline=baseline,parameter=path,values=[hi if i==n-1 else lo+(hi-lo)*i/(n-1) for i in range(n)],objective=objective)


def scenario_answer(data):
    def point(row):
        if not row['calculation']: return 'рабочая точка не получена: '+row['error']
        p=row['calculation']['operating_point']
        return f"дебит {p['q_liq']:.6g} м³/сут, забойное давление {p['p_wf']:.6g} атм"
    text = f"Сравнение режимов по параметру «{data['label']}». Базовый режим: {point(data['baseline'])}."
    for row in data['rows']:
        text += f"\n{row['value']:.6g} {data['unit']}: {point(row)}."
        if row['delta']:
            text+=f" Изменение дебита: {row['delta']['q_liq']:+.6g} м³/сут; давления: {row['delta']['p_wf']:+.6g} атм."
    if data['best_index'] is not None:
        row=data['rows'][data['best_index']]
        text+=f"\nЛучший среди проверенных вариантов: {row['value']:.6g} {data['unit']}. Это дискретный подбор, не гарантированный глобальный оптимум."
    return text+'\nБазовые параметры не изменены. Графики и таблица доступны в разделе «Сценарии».'
