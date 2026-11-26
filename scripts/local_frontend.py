"""Локальная раздача Frontend для запуска без Docker/Podman."""

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles


class NoCacheStaticFiles(StaticFiles):
    """Не позволяет браузеру сохранять старые HTML/JS/CSS при обновлении проекта.

    В native venv-режиме статику отдаёт Starlette, а не Nginx. Поэтому cache
    headers задаются здесь отдельно: иначе браузер может продолжать исполнять
    старый dashboard.js с прежней точностью отображения.
    """

    def file_response(
        self,
        full_path: Any,
        stat_result: os.stat_result,
        scope: Any,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if Path(full_path).suffix.lower() in {".html", ".js", ".css"}:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/backend-port.js")
def backend_config() -> Response:
    port = int(os.environ.get("BACKEND_PORT", "8080"))
    return Response(
        f"export const backendPort = {port};\n",
        media_type="text/javascript",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


app.mount(
    "/",
    NoCacheStaticFiles(directory=Path(__file__).resolve().parents[1] / "frontend", html=True),
)
