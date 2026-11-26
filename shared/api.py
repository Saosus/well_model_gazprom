"""Общие HTTP-настройки четырёх независимых приложений FastAPI."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from shared.validation import detail, domain_issues, schema_issues
from shared.contracts import ApiError, Health


VALIDATION_ERRORS = {
    422: {"model": ApiError, "description": "Некорректные исходные данные."},
}


def create_app(service: str, title: str, *, version: str = "0.1.0") -> FastAPI:
    app = FastAPI(title=title, version=version, docs_url=None, redoc_url=None)
    swagger_dir = Path(__file__).with_name("swagger")
    assets = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14"
    if swagger_dir.is_dir():
        assets = "/_swagger"
        app.mount(assets, StaticFiles(directory=swagger_dir), name="swagger-assets")

    @app.get("/docs", include_in_schema=False)
    def docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="./openapi.json",
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
        issues = schema_issues(exc.errors()) + domain_issues(exc.body, service)
        return JSONResponse(status_code=422, content={"detail": detail(issues)})

    return app
