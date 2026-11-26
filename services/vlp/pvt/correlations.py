"""Локальные PVT-корреляции. Формы уравнений, единицы и источники приведены в docs/pvt.md."""

from math import exp, expm1, isfinite, log, log10, log1p, sqrt


STD_T_K = 273.15 + 20.0
PSIA_PER_ATM = 101_325.0 / 6_894.757293168
PA_PER_ATM = 101_325.0
SCF_STB_PER_M3_M3 = 1.0 / 0.17810760667903522


def standing_gas_split(
    pressure_atm: float, temperature_k: float, gamma_oil: float, gamma_gas: float, rp_m3_m3: float,
) -> tuple[float, float, float]:
    """Прил. 1 и 4 базы знаний. Давление в корреляции в Па, Rs и Rp в м³/м³."""
    y = 1.2254503 + 0.001638 * temperature_k - 1.76875 / gamma_oil
    pressure_scale = 10.0**y / (1.9243101395421235e-6 * PA_PER_ATM)
    bubble_atm = pressure_scale * (rp_m3_m3 / gamma_gas)**0.83
    if not isfinite(bubble_atm):
        raise ValueError("Давление насыщения выходит за численный диапазон Standing")
    if pressure_atm >= bubble_atm:
        return bubble_atm, rp_m3_m3, 0.0
    # Вычисляем разность Rp − Rs точно даже непосредственно под Pb.
    log_fraction = -log1p((bubble_atm - pressure_atm) / pressure_atm) / 0.83
    return bubble_atm, rp_m3_m3 * exp(log_fraction), -rp_m3_m3 * expm1(log_fraction)


def _dead_oil_viscosity(temperature_f: float, api: float) -> float:
    scale = log(10.0) * 10.0**(3.0324 - 0.02023 * api)
    if temperature_f >= 70.0:
        return expm1(scale / temperature_f**1.163)
    mu70 = expm1(scale / 70.0**1.163)
    mu80 = expm1(scale / 80.0**1.163)
    slope = log(mu70 / mu80) / log(80.0 / 70.0)
    return mu70 * (70.0 / temperature_f)**slope


def oil_volume_viscosity(
    pressure_atm: float, bubble_atm: float, temperature_k: float, temperature_f: float, api: float,
    gamma_oil: float, gamma_gas: float, rs_m3_m3: float,
) -> tuple[float, float]:
    """Bo по приложениям 1 и 4, вязкость в сП по приложениям 3 и 6 и §7.2 ТЗ."""
    rs = rs_m3_m3 * SCF_STB_PER_M3_M3
    volume_term = rs * sqrt(gamma_gas / gamma_oil) + 2.25 * temperature_k - 574.5875
    bo = 0.972 + 147e-6 * volume_term**1.175
    viscosity = _dead_oil_viscosity(temperature_f, api)
    if rs > 0.0:
        a = 10.715 * (rs + 100.0)**-0.515
        b = 5.44 * (rs + 150.0)**-0.338
        viscosity = a * viscosity**b

    reference_atm = max(bubble_atm, 1.0)
    if pressure_atm > reference_atm:
        k = (-1433.0 + 5.0 * rs + 17.2 * temperature_f - 1180.0 * gamma_gas + 12.61 * api) / 1e5
        pressure_ratio = pressure_atm / reference_atm
        bo *= exp(-max(0.0, k) * (pressure_atm - reference_atm) / reference_atm)
        pressure_psia = pressure_atm * PSIA_PER_ATM
        m = 2.6 * pressure_psia**1.187 * 10.0**(-5.0 - 0.000039 * pressure_psia)
        viscosity *= pressure_ratio**m
    return bo, viscosity


# Dempsey (1965), аппроксимация графика Carr–Kobayashi–Burrows.
# Строка i содержит коэффициенты при Pr^0..Pr^3 для степени Tr^i.
_DEMPSEY = (
    (-2.46211820, 2.97054714, -0.286264054, 0.00805420522),
    (2.80860949, -3.49803305, 0.360373020, -0.0104432413),
    (-0.793385684, 1.39643306, -0.149144925, 0.00441015512),
    (0.0839387176, -0.186408848, 0.0203367881, -0.000609579263),
)


def gas_viscosity_cp(pressure_atm: float, temperature_f: float, gamma_gas: float) -> float:
    """Carr–Kobayashi–Burrows для углеводородного газа, вязкость в сП."""
    atmospheric = (
        (1.709e-5 - 2.062e-6 * gamma_gas) * temperature_f
        + 0.008188 - 0.00615 * log10(gamma_gas)
    )
    if pressure_atm <= 1.0:
        return atmospheric

    # Псевдокритические параметры Sutton, psia и °R.
    p_pc = 756.8 - 131.07 * gamma_gas - 3.6 * gamma_gas**2
    t_pc = 169.2 + 349.5 * gamma_gas - 74.0 * gamma_gas**2
    if p_pc <= 0.0 or t_pc <= 0.0:
        raise ValueError("gamma_gas вне области определения псевдокритических параметров Sutton")
    t_r = (temperature_f + 459.67) / t_pc
    p_r = pressure_atm * PSIA_PER_ATM / p_pc
    p = max(1.0, p_r)
    polynomial = 0.0
    for a, b, c, d in reversed(_DEMPSEY):
        row = ((d * p + c) * p + b) * p + a
        polynomial = polynomial * t_r + row
    ratio = exp(polynomial) / t_r
    if p_r < 1.0:
        # Между опорой при 1 атм и началом диапазона Dempsey (Pr=1).
        fraction = (pressure_atm - 1.0) / (p_pc / PSIA_PER_ATM - 1.0)
        ratio = 1.0 + fraction * (ratio - 1.0)
    return atmospheric * ratio
