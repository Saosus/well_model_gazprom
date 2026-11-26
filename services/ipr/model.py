"""Кривая притока Composite IPR (Brown, 1984). Формулы и источники — в README."""

from math import isfinite, sqrt

from shared.contracts import Curve, IprRequest


# Коэффициент квадратичного члена Вогеля в координатах депрессии: 0.8 / 1.8.
VOGEL_CURVATURE = 4.0 / 9.0
BROWN_SECANT_FRACTION = 0.001


def _effective_drawdown(p_wf: float, p_res: float, bubble: float, water: float) -> float:
    """Депрессия в атм, которую нужно умножить на PI для получения дебита жидкости."""
    if p_wf >= p_res:
        return 0.0
    if water == 1.0 or p_wf >= bubble:
        return p_res - p_wf

    # При максимальном нефтяном дебите p_oil = 0, p_water = 4/9 * bubble.
    join_pressure = bubble * (VOGEL_CURVATURE * water)
    if p_wf >= join_pressure:
        # Brown усредняет давления воды и нефти при одном дебите.
        # d = t - (4/9) * water * t², где t = (bubble - p_oil) / bubble.
        drawdown = (bubble - p_wf) / bubble
        discriminant = 1.0 - 4.0 * VOGEL_CURVATURE * water * drawdown
        oil_drawdown = 2.0 * drawdown / (1.0 + sqrt(discriminant))
        vogel = oil_drawdown * (1.0 - VOGEL_CURVATURE * oil_drawdown)
        return (p_res - bubble) + bubble * vogel

    # Продлеваем композитную кривую по секущей между 99.9% и 100% Q_o,max.
    oil_limit = p_res - VOGEL_CURVATURE * bubble
    secant_span = BROWN_SECANT_FRACTION * oil_limit / bubble
    # При h = Δq/(PI·bubble) и x = p_oil/bubble форма Вогеля даёт x + 4x² = 9h.
    # Нефтяной наклон x/h записан без вычитания близких значений.
    oil_slope = 18.0 / (1.0 + sqrt(1.0 + 144.0 * secant_span))
    composite_slope = water + (1.0 - water) * oil_slope
    return oil_limit + (join_pressure - p_wf) / composite_slope


def calculate_ipr(request: IprRequest) -> Curve:
    """Построить равномерную сетку давлений и соответствующие дебиты, м³/сут."""
    step = (request.p_res - 1.0) / (request.points - 1)
    pressures = [1.0 + step * index for index in range(request.points - 1)]
    pressures.append(request.p_res)

    bubble = min(request.pb, request.p_res)
    water = request.wct / 100.0
    rates = [
        request.pi * _effective_drawdown(pressure, request.p_res, bubble, water)
        for pressure in pressures
    ]
    if not all(isfinite(rate) for rate in rates):
        raise OverflowError("Расчётный дебит выходит за диапазон конечных чисел. Проверьте pi и p_res.")
    return Curve(q_liq=rates, p_wf=pressures)
