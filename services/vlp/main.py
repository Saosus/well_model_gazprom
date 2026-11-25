from fastapi import HTTPException

from services.vlp.model import calculate_vlp
from shared.api import VALIDATION_ERRORS, create_app
from shared.contracts import Curve, VlpRequest

app = create_app("vlp", "Well Model Builder · VLP", version="0.4.1")


@app.post("/vlp/calc", response_model=Curve, responses=VALIDATION_ERRORS, summary="Построить кривую подъёма")
def calculate(request: VlpRequest) -> Curve:
    """Забойное давление (атм) при заданных поверхностных дебитах жидкости (м³/сут)."""
    try:
        return calculate_vlp(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ArithmeticError as exc:
        raise HTTPException(status_code=422, detail="Параметры вне численного диапазона VLP") from exc
