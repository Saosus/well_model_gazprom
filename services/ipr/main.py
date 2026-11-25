import os

from fastapi import HTTPException

from services.ipr.model import calculate_ipr
from shared.api import VALIDATION_ERRORS, create_app
from shared.contracts import Curve, IprRequest

app = create_app("ipr", "Well Model Builder · IPR", docs_prefix=os.getenv("DOCS_PREFIX", ""), version="0.2.0")


@app.post("/ipr/calc", response_model=Curve, responses=VALIDATION_ERRORS, summary="Построить кривую притока")
def calculate(request: IprRequest) -> Curve:
    """Composite IPR по Brown. Давления в атм, дебиты жидкости в м³/сут."""
    try:
        return calculate_ipr(request)
    except OverflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
