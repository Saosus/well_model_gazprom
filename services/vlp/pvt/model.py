"""Состояние Black-Oil на единицу поверхностного объёма жидкости."""

from dataclasses import dataclass
from math import inf

from shared.contracts import Fluid

from .correlations import STD_T_K, gas_viscosity_cp, oil_volume_viscosity, standing_gas_split


# Сухой воздух при 20 °C и 1 физической атмосфере, кг/м³ (p*M / R*T).
AIR_STD_KG_M3 = 101_325.0 * 0.02896546 / (8.314462618 * STD_T_K)


@dataclass(frozen=True, slots=True)
class Phase:
    """Объём в условиях точки на 1 м³ товарной нефти и воды суммарно."""

    density_kg_m3: float
    viscosity_cp: float
    volume_m3_per_m3_liq: float

    def __post_init__(self) -> None:
        if not (
            0.0 < self.density_kg_m3 < inf
            and 0.0 < self.viscosity_cp < inf
            and 0.0 <= self.volume_m3_per_m3_liq < inf
        ):
            raise ValueError("Корреляция PVT вышла за численный диапазон плотности, вязкости или объёма")


@dataclass(frozen=True, slots=True)
class OilPhase(Phase):
    pb_atm: float
    rs_m3_m3: float  # Ст. м³ растворённого газа на м³ товарной нефти.
    bo: float        # Раб. м³ нефтяной фазы на м³ товарной нефти.


@dataclass(frozen=True, slots=True)
class GasPhase(Phase):
    bg: float        # Раб. м³ газа на ст. м³ газа при 20 °C и 1 атм.
    free_std_m3_per_m3_liq: float


@dataclass(frozen=True, slots=True)
class PvtState:
    oil: OilPhase | None
    gas: GasPhase | None
    water: Phase
    mixture: Phase


def _hydrocarbon_phases(
    fluid: Fluid, pressure_atm: float, temperature_c: float, oil_fraction: float,
) -> tuple[OilPhase, GasPhase]:
    temperature_k = temperature_c + 273.15
    temperature_f = temperature_c * 1.8 + 32.0
    if temperature_f <= 0.0:
        raise ValueError("Для формулы вязкости нефти Beggs–Robinson нужна температура выше 0 °F")
    api = 141.5 / fluid.gamma_oil - 131.5
    # 1000*γo кг/м³ = γo т/м³. Rp дан на тонну товарной нефти.
    rp_m3_m3 = fluid.rp * fluid.gamma_oil
    pb, rs, released = standing_gas_split(pressure_atm, temperature_k, fluid.gamma_oil, fluid.gamma_gas, rp_m3_m3)
    bo, mu_oil = oil_volume_viscosity(
        pressure_atm, pb, temperature_k, temperature_f, api, fluid.gamma_oil, fluid.gamma_gas, rs
    )

    gas_std_density = AIR_STD_KG_M3 * fluid.gamma_gas
    oil = OilPhase(
        density_kg_m3=(1000.0 * fluid.gamma_oil + rs * gas_std_density) / bo,
        viscosity_cp=mu_oil, volume_m3_per_m3_liq=oil_fraction * bo,
        pb_atm=pb, rs_m3_m3=rs, bo=bo,
    )
    # Идеальный газ, z=1. Абсолютное давление в атм, Pст=1 атм.
    bg = (temperature_k / STD_T_K) / pressure_atm
    free_std = oil_fraction * released
    gas = GasPhase(
        density_kg_m3=gas_std_density / bg,
        viscosity_cp=gas_viscosity_cp(pressure_atm, temperature_f, fluid.gamma_gas),
        volume_m3_per_m3_liq=free_std * bg,
        bg=bg, free_std_m3_per_m3_liq=free_std,
    )
    return oil, gas


def calculate_pvt(fluid: Fluid, *, pressure_atm: float, temperature_c: float) -> PvtState:
    """Рассчитать PVT при локальных абсолютном давлении и температуре.

    fluid — блок pvt запроса VLP. Дебит в рабочих м³/с равен поверхностному
    q_liq (м³/сут) × mixture.volume_m3_per_m3_liq / 86400. При 100% воды
    oil и gas равны None. Ошибка входа или области расчёта вызывает ValueError.
    """
    if (
        isinstance(pressure_atm, bool) or not isinstance(pressure_atm, (int, float))
        or not 0.0 < pressure_atm < inf
    ):
        raise ValueError("pressure_atm должно быть положительным конечным абсолютным давлением в атм")
    if (
        isinstance(temperature_c, bool) or not isinstance(temperature_c, (int, float))
        or not -273.15 < temperature_c < inf
    ):
        raise ValueError("temperature_c должна быть конечной температурой выше абсолютного нуля")

    water_fraction = fluid.wct / 100.0
    water = Phase(
        density_kg_m3=1000.0 * fluid.gamma_wat, viscosity_cp=1.0, volume_m3_per_m3_liq=water_fraction
    )
    if fluid.wct == 100.0:
        return PvtState(oil=None, gas=None, water=water, mixture=water)

    oil_fraction = (100.0 - fluid.wct) / 100.0
    try:
        oil, gas = _hydrocarbon_phases(fluid, pressure_atm, temperature_c, oil_fraction)
        phases = (oil, water, gas)
        volume = sum(phase.volume_m3_per_m3_liq for phase in phases)
        # Без скольжения доля фазы в стволе равна её доле в рабочем объёме.
        mixture = Phase(
            density_kg_m3=sum(phase.volume_m3_per_m3_liq * phase.density_kg_m3 for phase in phases) / volume,
            viscosity_cp=sum(phase.volume_m3_per_m3_liq * phase.viscosity_cp for phase in phases) / volume,
            volume_m3_per_m3_liq=volume,
        )
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("Параметры PVT выходят за численный диапазон корреляций") from exc
    return PvtState(oil=oil, gas=gas, water=water, mixture=mixture)
