from fastapi import HTTPException

from services.ipr.model import calculate_ipr
from shared.validation import detail, issue
from shared.api import VALIDATION_ERRORS, create_app
from shared.contracts import Curve, IprRequest

app = create_app("ipr", "Well Model Builder · IPR", version="0.2.0")


@app.post("/ipr/calc", response_model=Curve, responses=VALIDATION_ERRORS, summary="Построить кривую притока")
def calculate(request: IprRequest) -> Curve:
    """Composite IPR по Brown. Давления в атм, дебиты жидкости в м³/сут."""
    try:
        return calculate_ipr(request)
    except OverflowError as exc:
        msg = "Расчётный дебит превышает численный диапазон. Проверьте пластовое давление и продуктивность в указанных единицах."
        raise HTTPException(422, detail([issue(["pi", "p_res"], msg)])) from exc
