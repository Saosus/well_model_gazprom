"""Public backend API for the integrated assistant."""
import asyncio
import logging
import re

import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .core import Assistant, SessionStore
from .parameters import unflatten, validation, flatten, label
from .documents import extract
from shared.contracts import CalculateResponse
from pydantic import ValidationError
from services.backend.scenarios import parse_scenario, run_sweep, scenario_answer
from services.backend.analysis import compare, Measurement, WellCase, CalibrationRequest, calibrate
from .llm import ChatClient, LLMError, Settings
from .progress import calculation_stages


class UploadFileData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=255)]
    content: Annotated[str, Field(min_length=1, max_length=7_000_000)]


class FilePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: Annotated[list[UploadFileData], Field(min_length=1, max_length=3)]
    session_id: UUID | None = None


class FileConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    preview_id: UUID


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Annotated[str, Field(min_length=1, max_length=50000)]
    session_id: UUID | None = None
    conversation_only: bool = False


def make_router(calculate, *, store=None, client_factory=ChatClient):
    router = APIRouter(prefix="/api/assistant", tags=["assistant"])
    store = store if store is not None else SessionStore()

    @router.get("/status")
    async def status():
        try:
            settings = Settings.from_env()
        except LLMError:
            return {"configured": False, "status": "invalid_config"}
        return {"configured": settings.enabled, "status": "configured" if settings.enabled else "disabled"}

    @router.post("/files/preview", summary="Извлечь параметры из файлов без расчёта")
    async def preview_files(request: FilePreviewRequest):
        if sum(len(f.content) for f in request.files)>7_000_000:
            raise HTTPException(413, "Общий объём файлов ограничен 5 МБ.")
        sid, session = store.get(str(request.session_id) if request.session_id else None)
        if session.lock.locked(): raise HTTPException(409, "Диалог занят.")
        async with session.lock:
            session.file_preview = None
            try:
                preview = await asyncio.to_thread(extract, [f.model_dump() for f in request.files])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            preview_id = str(uuid4())
            session.file_preview = (preview_id, preview)
            return {"session_id":sid,"preview_id":preview_id,**preview}

    @router.post("/files/confirm", summary="Подтвердить новый набор параметров из файлов")
    async def confirm_files(request: FileConfirmRequest):
        sid, session = store.get(str(request.session_id))
        if session.lock.locked(): raise HTTPException(409, "Диалог занят.")
        async with session.lock:
            saved = session.file_preview
            if not saved or saved[0] != str(request.preview_id):
                raise HTTPException(409, "Предпросмотр устарел. Загрузите файлы повторно.")
            preview = saved[1]
            if not preview['can_import']: raise HTTPException(422, "Исправьте ошибки или противоречия в файлах.")
            session.file_preview = None
            data = flatten(preview['parameters'])
            session.data,session.pending,session.provenance = data,{},preview['provenance']
            session.last_result = None
            check, calc_request = validation(data)
            started=time.monotonic(); stages=[]; trace=[]; result=None
            token=calculation_stages.set(stages)
            try:
                if check['valid']:
                    try:
                        async with asyncio.timeout(180):
                            result=CalculateResponse.model_validate(await calculate(calc_request))
                        session.last_result=result
                        p=result.operating_point
                        status='calculated'
                        answer=f"Данные из файлов приняты. Дебит жидкости: {p.q_liq:.6g} м³/сут. Забойное давление: {p.p_wf:.6g} атм. Рабочая точка — равенство притока и подъёма жидкости."
                    except (HTTPException, ValidationError, TimeoutError) as exc:
                        status='calculation_error'; answer=Assistant.failure_message(exc)+" Рабочая точка не выдана."
                    trace.append({'tool':'calculate_well','ok':result is not None,'duration_ms':round((time.monotonic()-started)*1000),'trigger':'backend'})
                else:
                    status='needs_input'; answer='Данные из файлов приняты. Дополните обязательные параметры: '+('; '.join(label(p) for p in check['missing']) or 'нет пропусков')+'.'
                    if check['defaults_requiring_consent']: answer+=' Для необязательных полей укажите значения или согласуйте значения по умолчанию.'
                session.calculation_requested = status=='needs_input'
                session.history=(session.history+[{'role':'user','content':'Подтверждён новый набор параметров из файлов.'},{'role':'assistant','content':answer}])[-12:]
                response=Assistant.response(sid,data,status,answer,result,trace,{'prompt_tokens':0,'completion_tokens':0},started,{},session.provenance)
                response['calculation_stages']=stages
                return response
            finally: calculation_stages.reset(token)

    @router.post("/chat", summary="Сообщение ИИ-ассистенту")
    async def chat(request: ChatRequest):
        if not request.message.strip():
            raise HTTPException(422, "Сообщение пустое.")
        try:
            settings = Settings.from_env()
        except LLMError as exc:
            raise HTTPException(503, str(exc)) from None
        if not settings.enabled:
            raise HTTPException(503, "ИИ не настроен. Ручной расчёт доступен.")
        sid, session = store.get(str(request.session_id) if request.session_id else None)
        if session.lock.locked():
            raise HTTPException(409, "Предыдущий запрос диалога ещё выполняется.")
        async with session.lock:
            stages = []
            token = calculation_stages.set(stages)
            try:
                async with asyncio.timeout(180):
                    # Stage 4: explicit, deterministic scenario commands leave the base session intact.
                    started = time.monotonic()
                    # Explicit report and measured-flow commands use the exact last chat result.
                    command = request.message.strip()
                    report_cmd = re.fullmatch(r'(?:сформируй|сделай|выгрузи|скачай|создай)\s+(?:мне\s+)?отч[её]т(?:\s+по\s+результатам)?[.!]?', command, re.I)
                    measure_cmd = re.fullmatch(r'сравни(?:те)?\s+с\s+фактическим\s+дебитом\s+([-+]?\d+(?:[.,]\d+)?)\s*(?:м[³3]/сут(?:ки)?)[.!]?', command, re.I)
                    pi_cmd = re.fullmatch(r'подбери\s+PI\s+по\s+фактическому\s+дебиту\s+([-+]?\d+(?:[.,]\d+)?)\s*м[³3]/сут(?:ки)?\s+от\s+([-+]?\d+(?:[.,]\d+)?)\s+до\s+([-+]?\d+(?:[.,]\d+)?)\s*,\s*(\d+)\s+вариант(?:а|ов)?[.!]?', command, re.I)
                    if report_cmd or measure_cmd or pi_cmd:
                        report = None; analysis = None; status = 'ok'
                        if request.conversation_only or session.last_result is None:
                            answer = 'Сначала выполните расчёт актуальных параметров в чате кнопкой «Рассчитать по форме».'
                        else:
                            row = {'name':'Скважина из диалога','parameters':unflatten(session.data),'calculation':session.last_result.model_dump(),'issues':[], 'measured':None,'comparison':None,'error':None}
                            try:
                                if measure_cmd:
                                    measured = Measurement(q_liq=float(measure_cmd[1].replace(',','.')))
                                    row['measured']=measured.model_dump(); row['comparison']=compare(session.last_result.operating_point, measured)
                                    metric=row['comparison']['flow']
                                    answer=f"Расчётный дебит: {metric['model']:.6g} м³/сут; фактический: {metric['actual']:.6g} м³/сут. Разница (расчёт − факт): {metric['delta']:.6g} м³/сут. "
                                    answer+=f"Относительное отклонение: {metric['percent']:.6g} %. " if metric['percent'] is not None else 'Относительное отклонение не определено. '
                                    answer+=' '.join(row['comparison']['notes'])
                                    report=row
                                elif pi_cmd:
                                    q,lo,hi=(float(pi_cmd[i].replace(',','.')) for i in (1,2,3))
                                    spec=CalibrationRequest(well=WellCase(name=row['name'],parameters=row['parameters'],measured=Measurement(q_liq=q)),pi_min=lo,pi_max=hi,count=int(pi_cmd[4]))
                                    analysis=await calibrate(spec,calculate)
                                    best=analysis['best_index']
                                    answer='Подходящий PI не найден в заданных пределах.' if best is None else f"Лучший среди проверенных PI: {analysis['rows'][best]['pi']:.6g} м³/(сут·атм). Дебит: {analysis['rows'][best]['calculation']['operating_point']['q_liq']:.6g} м³/сут. Исходные параметры не изменены; глобальный оптимум не гарантирован."
                                else:
                                    report=row; answer='Отчёт по последнему успешному расчёту готов. Скачайте его кнопкой под сообщением.'
                            except (ValueError, ValidationError):
                                status='analysis_error'; answer='Проверьте фактический дебит и диапазон PI: числа должны быть конечными и допустимыми.'
                        session.history=(session.history+[{'role':'user','content':request.message},{'role':'assistant','content':answer}])[-12:]
                        response=Assistant.response(sid,session.data,status,answer,None,[{'tool':'analyze_measurement' if (measure_cmd or pi_cmd) else 'export_report','ok':bool(report or analysis),'duration_ms':round((time.monotonic()-started)*1000),'trigger':'backend'}],{'prompt_tokens':0,'completion_tokens':0},started,session.pending,session.provenance,'conversation')
                        response['report']=report; response['analysis']=analysis; response['calculation_stages']=stages
                        return response
                    try:
                        spec = parse_scenario(request.message, unflatten(session.data))
                        scenario_error = None
                    except (ValueError, ValidationError) as exc:
                        spec, scenario_error = None, str(exc)
                    if spec is not None or scenario_error:
                        scenario = None
                        if request.conversation_only:
                            answer = 'Форма изменена. Сначала передайте её в чат или продолжите по сохранённым данным чата.'
                        elif scenario_error:
                            answer = scenario_error
                        elif not validation(session.data, session.pending)[0]['valid']:
                            answer = 'Сначала рассчитайте базовый режим с полными параметрами. Затем повторите запрос сценария.'
                        else:
                            scenario = await run_sweep(spec, calculate)
                            answer = scenario_answer(scenario)
                        session.history = (session.history + [{'role':'user','content':request.message},{'role':'assistant','content':answer}])[-12:]
                        trace = [{'tool':'run_scenarios','ok':any(r['calculation'] for r in scenario['rows']) if scenario else False,'duration_ms':round((time.monotonic()-started)*1000),'trigger':'backend'}]
                        logging.getLogger(__name__).info('assistant tool=run_scenarios ok=%s duration_ms=%s',trace[0]['ok'],trace[0]['duration_ms'])
                        response = Assistant.response(sid, session.data, 'ok' if scenario else 'scenario_error', answer, None, trace, {'prompt_tokens':0,'completion_tokens':0}, started, session.pending, session.provenance, 'conversation')
                        response['mode'] = 'scenario'
                        response['scenario'] = scenario
                    else:
                        response = await Assistant(client_factory(settings), calculate, settings.max_steps).run(session, request.message, sid, request.conversation_only)
                    response['calculation_stages'] = stages
                    return response
            except TimeoutError:
                raise HTTPException(504, "Истекло общее время обработки запроса ассистентом.") from None
            finally:
                calculation_stages.reset(token)

    return router
