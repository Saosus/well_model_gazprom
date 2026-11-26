from fastapi import HTTPException

from services.vlp.model import calculate_vlp
from shared.validation import detail, enforce, issue
from shared.api import VALIDATION_ERRORS, create_app
from shared.contracts import Curve, VlpRequest

app = create_app("vlp", "Well Model Builder · VLP", version="0.4.1")


@app.post("/vlp/calc", response_model=Curve, responses=VALIDATION_ERRORS, summary="Построить кривую подъёма")
def calculate(request: VlpRequest) -> Curve:
    """Забойное давление (атм) при заданных поверхностных дебитах жидкости (м³/сут)."""
    enforce(request.model_dump(), "vlp")
    try:
        return calculate_vlp(request)
    except ValueError as exc:
        message = str(exc)
        if "Colebrook" in message:
            paths = ["roughness"]
            message = "В расчёте достигнут режим Colebrook–White. Проверьте относительную шероховатость: требуется значение больше 0 и меньше 3,7."
        elif "Sutton" in message:
            paths = ["pvt.gamma_gas"]
            message = "Проверьте плотность газа по воздуху: при расчётном давлении параметры Sutton должны быть положительными."
        elif "q_max" in message:
            paths = ["q_max", "points"]
            message = "Соседние дебиты совпали при округлении. Увеличьте максимальный дебит или уменьшите число точек VLP."
        elif any(key in message for key in ("PVT", "Standing", "вязкости", "temperature_c")):
            paths = ["pvt.t_res", "geo_grad", "pvt.gamma_oil", "pvt.gamma_gas", "pvt.rp"]
            message = "Свойства смеси вышли за численный диапазон корреляции. Сверьте температуру, градиент, относительные плотности и единицы газового фактора."
        else:
            paths = ["q_max", "tubing.d", "casing.d", "p_wh"]
            message = "Не удалось получить конечное давление вдоль ствола. Проверьте диаметры в метрах, дебит в м³/сут и абсолютное устьевое давление."
        raise HTTPException(422, detail([issue(paths, message)])) from exc
    except ArithmeticError as exc:
        message = "При вычислении потерь давления произошло переполнение или деление на ноль. Проверьте диаметры в метрах и верхнюю границу дебита."
        raise HTTPException(422, detail([issue(["tubing.d", "casing.d", "q_max"], message)])) from exc
