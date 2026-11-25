"""Локальная страница состояния с портом API из параметров запуска."""

import os
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/backend-port.js")
def backend_config() -> Response:
    port = int(os.environ.get("BACKEND_PORT", "8080"))
    return Response(f"export const backendPort = {port};\n", media_type="text/javascript",
                    headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "frontend", html=True))
