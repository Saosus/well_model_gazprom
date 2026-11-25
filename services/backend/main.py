import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import TypeAdapter, ValidationError

from shared.api import VALIDATION_ERRORS, create_app
from shared.contracts import ApiError, CalculateRequest, CalculateResponse, Curve, NodalResult

SERVICE_URLS = {
    "ipr": os.getenv("IPR_URL", "http://127.0.0.1:8003"),
    "vlp": os.getenv("VLP_URL", "http://127.0.0.1:8002"),
    "nodal": os.getenv("NODAL_URL", "http://127.0.0.1:8004"),
}
default_origins = "http://localhost:3000,http://127.0.0.1:3000,http://[::1]:3000"
origins = os.getenv("FRONTEND_ORIGINS", default_origins).split(",")
app = create_app("backend", "Well Model Builder · Backend", version="0.6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in origins if origin.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


async def service_status(client: httpx.AsyncClient, name: str, base_url: str) -> str:
    try:
        response = await client.get(f"{base_url.rstrip('/')}/{name}/health")
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and data.get("status") == "ok" and data.get("service") == name:
                return "ok"
    except (httpx.RequestError, ValueError):
        pass
    return "unavailable"


@app.get("/api/services/health", response_model=dict[str, Literal["ok", "unavailable"]],
         summary="Проверить связь backend с расчётными сервисами")
async def services_health() -> dict[str, str]:
    async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
        statuses = await asyncio.gather(*(service_status(client, name, url) for name, url in SERVICE_URLS.items()))
    return dict(zip(SERVICE_URLS, statuses, strict=True))


CALCULATE_EXAMPLE = json.loads(
    (Path(__file__).resolve().parents[2] / "examples" / "calculate.json").read_text(encoding="utf-8")
)
CURVE = TypeAdapter(Curve)
NODAL_POINTS = TypeAdapter(NodalResult)
BACKEND_ERRORS = {
    **VALIDATION_ERRORS,
    400: {"model": ApiError, "description": "Сервис отклонил параметры расчёта."},
    502: {"model": ApiError, "description": "Некорректный ответ расчётного сервиса."},
    503: {"model": ApiError, "description": "Расчётный сервис недоступен."},
    504: {"model": ApiError, "description": "Истекло время ожидания расчёта."},
}


async def post_calculation[T](
    client: httpx.AsyncClient, name: str, payload: dict, schema: TypeAdapter[T],
) -> T:
    url = f"{SERVICE_URLS[name].rstrip('/')}/{name}/calc"
    stage = name.upper()
    try:
        response = await client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"{stage} — истекло время ожидания ответа.") from exc
    except httpx.RequestError as exc:
        raise HTTPException(503, f"{stage} — сервис недоступен.") from exc

    if response.status_code in (400, 422):
        try:
            reason = response.json()
        except ValueError:
            reason = None
        if isinstance(reason, dict) and isinstance(reason.get("detail"), str):
            raise HTTPException(response.status_code, f"{stage} — {reason['detail']}")
    if response.status_code != 200:
        raise HTTPException(502, f"{stage} — неожиданный ответ сервиса (HTTP {response.status_code}).")
    try:
        return schema.validate_json(response.content)
    except ValidationError as exc:
        raise HTTPException(502, f"{stage} — ответ не соответствует контракту расчёта.") from exc


@app.post("/api/calculate", response_model=CalculateResponse, responses=BACKEND_ERRORS,
          summary="Получить IPR, VLP и одну рабочую точку")
async def calculate(request: Annotated[CalculateRequest, Body(openapi_examples={
    "well": {"summary": "Скважина из examples/calculate.json", "value": CALCULATE_EXAMPLE},
})]) -> CalculateResponse:
    sampling = request.sampling
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=3.0), trust_env=False) as client:
        ipr = await post_calculation(client, "ipr", {
            **request.reservoir.model_dump(), "wct": request.pvt.wct, "points": sampling.ipr_points,
        }, CURVE)
        vlp = await post_calculation(client, "vlp", {
            **request.well.model_dump(), "pvt": request.pvt.model_dump(),
            "points": sampling.vlp_points, "q_max": sampling.q_max,
        }, CURVE)
        points = await post_calculation(client, "nodal", {
            "ipr": ipr.model_dump(), "vlp": vlp.model_dump(),
        }, NODAL_POINTS)
    return CalculateResponse(ipr=ipr, vlp=vlp, operating_point=points[0])
