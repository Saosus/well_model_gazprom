"""Контрольный пример §5.3. Из корня проекта: python -m scripts.pvt_example."""

from services.vlp.pvt import calculate_pvt
from shared.contracts import Fluid


def main() -> None:
    fluid = Fluid(wct=50, rp=100, gamma_oil=0.8, gamma_gas=0.7, gamma_wat=1.0, t_res=90)
    low = calculate_pvt(fluid, pressure_atm=50, temperature_c=fluid.t_res)
    bubble = low.oil.pb_atm
    print(f"Смесь при {fluid.t_res:g} °C и обводнённости {fluid.wct:g} %. Pb = {bubble:.6f} атм.")
    print("Режим    P, атм     Rs¹    Св. газ²      Bo    ρ смеси, кг/м³   μ смеси, сП")
    for label, pressure in (("P < Pb", 50.0), ("P = Pb", bubble), ("P > Pb", 150.0)):
        state = calculate_pvt(fluid, pressure_atm=pressure, temperature_c=fluid.t_res)
        print(f"{label:7} {pressure:8.3f} {state.oil.rs_m3_m3:8.3f} "
              f"{state.gas.free_std_m3_per_m3_liq:9.3f} {state.oil.bo:8.4f} "
              f"{state.mixture.density_kg_m3:13.3f} {state.mixture.viscosity_cp:13.4f}")
    print("¹ ст. м³ газа/м³ товарной нефти.  ² ст. м³ газа/м³ поверхностной жидкости.")
    water = calculate_pvt(fluid.model_copy(update={"wct": 100.0}), pressure_atm=150, temperature_c=90)
    print(f"100 % воды при 150 атм: {water.mixture.density_kg_m3:g} кг/м³, "
          f"{water.mixture.viscosity_cp:g} сП.")


if __name__ == "__main__":
    main()
