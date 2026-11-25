"""Гомогенный подъём. Независимая переменная RK45 — MD, от устья к пласту."""

from dataclasses import dataclass
from math import exp, isfinite, log, log10, pi

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from services.vlp.pvt import calculate_pvt
from shared.contracts import Curve, VlpRequest

PA_PER_ATM = 101_325.0
GRAVITY = 9.80665
SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class Section:
    length: float
    vertical_slope: float
    diameter: float
    top_temperature: float


def _sections(well: VlpRequest) -> list[Section]:
    md, tvd = well.inclinometry.MD, well.inclinometry.TVD
    if md[0] > 0:
        # Первая станция ниже устья: восстановить участок от (0, 0).
        md, tvd = [0.0, *md], [0.0, *tvd]
        if tvd[1] < 0:
            raise ValueError("TVD первой подземной станции ниже отметки 0 недопустима")
    ends = sorted({0.0, well.tubing.h_mes, well.h_res, *(m for m in md if 0 < m < well.h_res)})
    depths = np.interp(ends, md, tvd).tolist()
    segments = []
    for start, end, top, bottom in zip(ends, ends[1:], depths, depths[1:]):
        length = end - start
        diameter = well.tubing.d if start < well.tubing.h_mes else well.casing.d
        temperature = well.pvt.t_res - (depths[-1] - top) * (well.geo_grad / 100.0)
        segments.append(Section(length, (bottom - top) / length, diameter, temperature))
    return segments


def _turbulent_factor(reynolds: float, roughness: float) -> float:
    # Colebrook–White для x=1/sqrt(f): 10^(-x/2) = ε_rel/3.7 + 2.51*x/Re.
    if roughness >= 3.7:
        raise ValueError("Нет положительного решения Colebrook–White при этой шероховатости")

    def residual(x: float) -> float:
        return exp(-0.5 * log(10.0) * x) - roughness / 3.7 - (2.51 / reynolds) * x

    x = brentq(residual, 0.0, 2.0 * log10(reynolds), xtol=1e-15, rtol=1e-12)
    return 1.0 / x**2


def _friction_gradient(density: float, viscosity: float, velocity: float, diameter: float, roughness: float) -> float:
    """Потери на трение, Па/м. Вязкость в Па·с, скорость в м/с."""
    reynolds = density * velocity * diameter / viscosity
    if not isfinite(reynolds):
        raise ValueError("Число Рейнольдса выходит за численный диапазон")
    if reynolds <= 2000:
        # f=64/Re после сокращения. При нулевом расходе трение равно нулю.
        return 32.0 * viscosity * velocity / diameter**2
    factor = _turbulent_factor(max(reynolds, 4000.0), roughness)
    if reynolds < 4000:
        weight = (reynolds - 2000.0) / 2000.0
        factor = (1.0 - weight) * (64.0 / 2000.0) + weight * factor
    return factor * density * velocity**2 / (2.0 * diameter)


def _bottom_pressure(well: VlpRequest, section: Section, q: float, top_pressure: float) -> float:
    area = pi * section.diameter**2 / 4.0
    surface_velocity = q / SECONDS_PER_DAY / area
    temp_slope = well.geo_grad / 100.0 * section.vertical_slope

    def gradient(distance, pressure):
        temperature = section.top_temperature + temp_slope * distance
        mixture = calculate_pvt(well.pvt, pressure_atm=float(pressure[0]), temperature_c=float(temperature)).mixture
        density = mixture.density_kg_m3
        velocity = surface_velocity * mixture.volume_m3_per_m3_liq
        friction = _friction_gradient(density, mixture.viscosity_cp * 0.001, velocity, section.diameter, well.roughness)
        total = (density * GRAVITY * section.vertical_slope + friction) / PA_PER_ATM
        if not isfinite(total):
            raise ValueError("Градиент давления выходит за численный диапазон")
        return [total]

    solution = solve_ivp(gradient, (0.0, section.length), [top_pressure], method="RK45",
                         t_eval=[section.length], rtol=1e-7, atol=1e-8)
    if not solution.success:
        raise ValueError("Не удалось проинтегрировать давление на участке ствола")
    result = float(solution.y[0, -1])
    if not isfinite(result) or result <= 0:
        raise ValueError("Давление на конце участка выходит за численный диапазон")
    return result


def calculate_vlp(well: VlpRequest) -> Curve:
    rates = np.linspace(0.0, well.q_max, well.points).tolist()
    if any(a >= b for a, b in zip(rates, rates[1:])):
        raise ValueError("При заданном q_max точки сетки дебита неразличимы")
    segments = _sections(well)
    pressures = []
    for rate in rates:
        pressure = well.p_wh
        for segment in segments:
            pressure = _bottom_pressure(well, segment, rate, pressure)
        pressures.append(pressure)
    return Curve(q_liq=rates, p_wf=pressures)
