"""Общие HTTP-настройки четырёх независимых приложений FastAPI."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from shared.contracts import ApiError, Health


VALIDATION_ERRORS = {
    422: {"model": ApiError, "description": "Некорректные исходные данные."},
}


def create_app(
    service: str, title: str, *, version: str = "0.1.0", docs_prefix: str = "",
) -> FastAPI:
    prefix = docs_prefix.strip().rstrip("/")
    if prefix and not prefix.startswith("/"):
        raise ValueError("docs_prefix должен начинаться с '/'.")

    app = FastAPI(title=title, version=version, docs_url=None, redoc_url=None)
    swagger_dir = Path(__file__).with_name("swagger")
    assets = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14"
    if swagger_dir.is_dir():
        app.mount("/_swagger", StaticFiles(directory=swagger_dir), name="swagger-assets")
        assets = f"{prefix}/_swagger" if prefix else "/_swagger"

    @app.get("/docs", include_in_schema=False)
    def docs() -> HTMLResponse:
        openapi_url = f"{prefix}/openapi.json" if prefix else "/openapi.json"
        return get_swagger_ui_html(
            openapi_url=openapi_url,
            title=f"{title} · Swagger UI",
            swagger_js_url=f"{assets}/swagger-ui-bundle.js",
            swagger_css_url=f"{assets}/swagger-ui.css",
            swagger_favicon_url="data:,",
            swagger_ui_parameters={"validatorUrl": None},
        )

    health_path = "/health" if service == "backend" else f"/{service}/health"

    @app.get(health_path, response_model=Health, summary="Проверить HTTP-приложение")
    def health() -> Health:
        return Health(status="ok", service=service)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Исключаем сырые значения, в том числе NaN/Inf, из тела JSON-ошибки.
        messages = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            messages.append(f"{location} — {error['msg']}")
        return JSONResponse(status_code=422, content={"detail": " | ".join(messages)})

    return app
