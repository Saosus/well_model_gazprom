import os

from fastapi import HTTPException

from services.nodal.model import calculate_nodal
from shared.api import VALIDATION_ERRORS, create_app
from shared.contracts import NodalRequest, NodalResult

app = create_app("nodal", "Well Model Builder · Nodal", docs_prefix=os.getenv("DOCS_PREFIX", ""), version="0.5.0")


@app.post("/nodal/calc", response_model=NodalResult, responses=VALIDATION_ERRORS, summary="Найти рабочую точку")
def calculate(request: NodalRequest) -> NodalResult:
    """Пересечение ломаных IPR и VLP. Требуется единственная общая точка."""
    try:
        return [calculate_nodal(request)]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
