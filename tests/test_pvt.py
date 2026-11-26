import math
import unittest

from pydantic import ValidationError

from services.vlp.pvt import calculate_pvt
from shared.contracts import Fluid


SAMPLE = dict(wct=50, rp=100, gamma_oil=0.8, gamma_gas=0.7, gamma_wat=1.0, t_res=90)


def state(pressure, temperature=90, **changes):
    return calculate_pvt(Fluid(**(SAMPLE | changes)), pressure_atm=pressure, temperature_c=temperature)


class PvtTests(unittest.TestCase):
    def test_dead_oil_against_published_examples(self):
        # Ahmed, пример 2-34. Пары (°F, API, сП).
        for t_f, api, expected in [(250, 47.1, 0.568), (220, 40.7, 1.020)]:
            dead = state(1, (t_f - 32) / 1.8, rp=0, gamma_oil=141.5 / (131.5 + api))
            self.assertAlmostEqual(dead.oil.viscosity_cp, expected, delta=0.0006)

    def test_cold_oil_branch_and_boundary(self):
        # В логарифмических координатах продолжение проходит через значения при 70 и 80 °F.
        def mu(t_f):
            return state(1, (t_f - 32) / 1.8, rp=0).oil.viscosity_cp
        slope = math.log(mu(70) / mu(80)) / math.log(80 / 70)
        for t_f in (48, 60, 69.99999):
            self.assertAlmostEqual(mu(t_f), mu(70) * (70 / t_f)**slope, places=9)

    def test_numerical_oil_properties_below_at_and_above_pb(self):
        # Приложения 1–6. Контрольные значения для 363.15 K, Rsb = 80 м³/м³.
        bubble = state(50).oil.pb_atm
        self.assertAlmostEqual(bubble, 106.5354477145, places=8)
        cases = [
            (50, 32.1571669942, 23.9214165029, 1.1454056530, 0.5534826128),
            (bubble, 80, 0, 1.2756208697, 0.3959549446),
            (150, 80, 0, 1.2555061550, 0.4237636088),
        ]
        for pressure, rs, free, bo, mu in cases:
            with self.subTest(pressure=pressure):
                s = state(pressure)
                self.assertAlmostEqual(s.oil.rs_m3_m3, rs, places=8)
                self.assertAlmostEqual(s.gas.free_std_m3_per_m3_liq, free, places=8)
                self.assertAlmostEqual(s.oil.bo, bo, places=9)
                self.assertAlmostEqual(s.oil.viscosity_cp, mu, places=9)

    def test_bubble_point_and_nearest_floats(self):
        bubble = state(10).oil.pb_atm
        below, at, above = (state(p) for p in (math.nextafter(bubble, 0), bubble, math.nextafter(bubble, math.inf)))
        self.assertGreater(below.gas.free_std_m3_per_m3_liq, 0)
        for s in (at, above, state(250)):
            self.assertEqual(s.gas.free_std_m3_per_m3_liq, 0)
            self.assertEqual(s.gas.volume_m3_per_m3_liq, 0)
            self.assertEqual(s.oil.rs_m3_m3, 80)
        for s in (below, above):
            self.assertAlmostEqual(s.oil.bo, at.oil.bo, places=12)
            self.assertAlmostEqual(s.oil.viscosity_cp, at.oil.viscosity_cp, places=12)
            self.assertAlmostEqual(s.mixture.density_kg_m3, at.mixture.density_kg_m3, places=10)

    def test_rs_uses_local_temperature_and_caps_at_available_gas(self):
        # Прямая обратная формула Standing, независимо от способа разделения в модуле.
        for t in (20, 65, 105):
            for p in (1, 12, 60, 100, 240):
                with self.subTest(t=t, p=p):
                    s = state(p, t, rp=75, gamma_oil=0.86, gamma_gas=0.79)
                    y = 1.2254503 + 0.001638 * (273.15 + t) - 1.76875 / 0.86
                    rs = 0.79 * (1.9243101395421235e-6 * p * 101325 / 10**y)**(1 / 0.83)
                    expected = min(75 * 0.86, rs)
                    self.assertAlmostEqual(s.oil.rs_m3_m3, expected, places=8)
                    self.assertAlmostEqual(s.oil.rs_m3_m3 + 2 * s.gas.free_std_m3_per_m3_liq, 75 * 0.86)
        self.assertNotEqual(state(50, 60).oil.pb_atm, state(50, 90).oil.pb_atm)
        self.assertEqual(state(50, 60, t_res=150), state(50, 60, t_res=90))

    def test_gas_free_and_low_gas_oil(self):
        for rp in (0, 0.01):
            for p in (0.5, 1, 10, 200):
                with self.subTest(rp=rp, p=p):
                    s = state(p, wct=0, rp=rp)
                    self.assertEqual(s.oil.pb_atm == 0, rp == 0)
                    self.assertLess(s.oil.pb_atm, p)
                    self.assertEqual(s.oil.rs_m3_m3, 0.8 * rp)
                    self.assertEqual(s.gas.free_std_m3_per_m3_liq, 0)
                    self.assertEqual(s.water.volume_m3_per_m3_liq, 0)
                    self.assertAlmostEqual(s.mixture.density_kg_m3, s.oil.density_kg_m3)
                    self.assertAlmostEqual(s.mixture.viscosity_cp, s.oil.viscosity_cp)
        self.assertLess(state(200, rp=0).oil.bo, state(1, rp=0).oil.bo)

    def test_incompressible_water_only(self):
        for t, p, gravity in [(20, 1, 1), (90, 200, 1), (80, 50, 1.07)]:
            with self.subTest(t=t, p=p, gravity=gravity):
                s = state(p, t, wct=100, gamma_wat=gravity)
                self.assertEqual(s.mixture, s.water)
                self.assertEqual((s.water.density_kg_m3, s.water.viscosity_cp), (1000 * gravity, 1))
                self.assertEqual(s.water.volume_m3_per_m3_liq, 1)
                self.assertIsNone(s.oil)
                self.assertIsNone(s.gas)

    def test_ideal_gas_standard_conditions_and_scaling(self):
        std = state(1, 20).gas
        self.assertEqual(std.bg, 1.0)
        self.assertAlmostEqual(std.density_kg_m3, 0.84289018893, places=9)
        for t, p in [(20, 2), (90, 50), (75, 200)]:
            with self.subTest(t=t, p=p):
                gas = state(p, t).gas
                self.assertAlmostEqual(gas.bg, ((273.15 + t) / 293.15) / p, places=12)
                self.assertAlmostEqual(gas.bg * gas.density_kg_m3, std.density_kg_m3)

    def test_carr_viscosity_against_chart(self):
        # Ahmed, пример 2-13: график CKB при 140 °F, γg=0.72 и 1 атм / 2000 psia.
        at_one = state(1, 60, gamma_gas=0.72).gas
        compressed = state(2000 / 14.69594878, 60, gamma_gas=0.72).gas
        self.assertAlmostEqual(at_one.viscosity_cp, 0.0113, delta=0.0001)
        self.assertAlmostEqual(compressed.viscosity_cp, 0.01695, delta=0.0005)

    def test_carr_low_pressure_joins(self):
        p_pc = (756.8 - 131.07 * 0.7 - 3.6 * 0.7**2) / 14.69594877551422
        mu_atm = state(1).gas.viscosity_cp
        mu_pc = state(p_pc).gas.viscosity_cp
        self.assertAlmostEqual(state((1 + p_pc) / 2).gas.viscosity_cp, (mu_atm + mu_pc) / 2)
        for p in (1, p_pc):
            self.assertAlmostEqual(state(p - 1e-6).gas.viscosity_cp, state(p + 1e-6).gas.viscosity_cp)

    def test_no_slip_mixing_conserves_mass_and_uses_local_phase_volumes(self):
        gas_std_density = 0.84289018893
        for wct in (0, 10, 50, 90, 99.999):
            w = wct / 100
            for p in (10, 105, 200):
                with self.subTest(wct=wct, p=p):
                    s = state(p, wct=wct, gamma_wat=1.05)
                    oil_volume = (1 - w) * s.oil.bo
                    gas_volume = (1 - w) * (80 - s.oil.rs_m3_m3) * s.gas.bg
                    total = oil_volume + w + gas_volume
                    mass = (1 - w) * (800 + 80 * gas_std_density) + w * 1050
                    mu = (oil_volume * s.oil.viscosity_cp + w + gas_volume * s.gas.viscosity_cp) / total
                    self.assertAlmostEqual(s.gas.volume_m3_per_m3_liq, gas_volume)
                    self.assertAlmostEqual(s.mixture.volume_m3_per_m3_liq, total)
                    self.assertAlmostEqual(s.mixture.density_kg_m3 * total, mass)
                    self.assertAlmostEqual(s.mixture.viscosity_cp, mu)

    def test_input_validation_and_numerical_domain(self):
        for p in (0, -1, True, "50", math.nan, math.inf, 1e308):
            with self.subTest(p=p), self.assertRaises(ValueError):
                state(p)
        for t in (-273.15, -18, math.nan, math.inf, False, "90"):
            with self.subTest(t=t), self.assertRaises(ValueError):
                state(50, t)
        for change in (dict(rp=-1), dict(wct=101), dict(wct=-1), dict(gamma_oil=0),
                       dict(gamma_gas=-1), dict(gamma_wat=0), dict(t_res=-273.15)):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                Fluid(**(SAMPLE | change))


if __name__ == "__main__":
    unittest.main()
