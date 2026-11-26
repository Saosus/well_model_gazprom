import json
import math
from pathlib import Path
import unittest

from services.vlp.main import app
from services.vlp.model import _friction_gradient, _turbulent_factor, calculate_vlp
from shared.contracts import VlpRequest
from tests.test_scaffold import request

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "vlp.json"


def sample():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def curve(body):
    return calculate_vlp(VlpRequest.model_validate(body))


class HydraulicTests(unittest.TestCase):
    def test_colebrook_residual_and_relative_roughness(self):
        for re in (4000, 100_000, 1e8):
            for roughness in (1e-8, 0.0001, 0.02):
                f = _turbulent_factor(re, roughness)
                x = 1.0 / math.sqrt(f)
                self.assertAlmostEqual(x, -2 * math.log10(roughness / 3.7 + 2.51 * x / re), places=9)

    def test_transition_joins_laminar_and_turbulent(self):
        # При этих единичных свойствах Re численно равен скорости.
        def factor(re):
            return 2 * _friction_gradient(1, 1, re, 1, 0.001) / re**2
        for re in (2000, 4000):
            self.assertAlmostEqual(factor(re - 1e-5), factor(re + 1e-5), places=8)
        self.assertAlmostEqual(factor(3000), (64/2000 + _turbulent_factor(4000, 0.001)) / 2)

    def test_water_hydrostatics_and_laminar_losses_in_both_pipes(self):
        body = sample()
        body["pvt"]["wct"] = 100
        # Башмак между станциями, пласт выше последней станции.
        body["tubing"]["h_mes"] = 600
        body.update(h_res=1250, q_max=1, points=2)
        zero, flowing = curve(body).p_wf
        hydrostatic = 10 + 1000 * 9.80665 * 1050 / 101325
        self.assertAlmostEqual(zero, hydrostatic, places=9)
        q = 1 / 86400
        loss = (128 * 0.001 * q / math.pi) * (600 / 0.062**4 + 650 / 0.1**4)
        self.assertAlmostEqual(flowing, hydrostatic + loss / 101325, places=9)

    def test_horizontal_water_well_with_no_tubing(self):
        body = sample()
        body["pvt"]["wct"] = 100
        body["inclinometry"]["TVD"] = [0, 0, 0]
        body["tubing"]["h_mes"] = 0
        body["points"] = 2
        zero, flowing = curve(body).p_wf
        self.assertEqual(zero, body["p_wh"])
        # Независимое решение Colebrook–White простыми подстановками.
        d = body["casing"]["d"]
        v = 4 * body["q_max"] / (86400 * math.pi * d**2)
        re = 1000 * v * d / 0.001
        x = 6.0
        for _ in range(25):
            x = -2 * math.log10(body["roughness"] / 3.7 + 2.51 * x / re)
        self.assertAlmostEqual(flowing, zero + 1500 * 1000 * v**2 / (2 * d * x**2 * 101325), places=9)

    def test_temperature_follows_tvd_and_is_anchored_at_reservoir(self):
        from unittest.mock import patch
        from services.vlp.pvt import calculate_pvt
        observed = []

        def observe(fluid, *, pressure_atm, temperature_c):
            observed.append(temperature_c)
            return calculate_pvt(fluid, pressure_atm=pressure_atm, temperature_c=temperature_c)
        body = sample()
        body["points"] = 2
        with patch("services.vlp.model.calculate_pvt", side_effect=observe):
            curve(body)
        self.assertAlmostEqual(min(observed), 57)
        self.assertAlmostEqual(max(observed), 90)
        self.assertIn(87, observed)

    def test_splitting_straight_segment_preserves_mixture_curve(self):
        body = sample()
        body.update(points=3)
        original = curve(body)
        body["inclinometry"] = {"MD": [500, 1000, 1100, 1500], "TVD": [500, 1000, 1020, 1100]}
        for a, b in zip(curve(body).p_wf, original.p_wf):
            self.assertAlmostEqual(a, b, places=5)


class VlpHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_specification_example_with_defaults(self):
        body = sample()
        for key in ("q_max", "roughness", "points"):
            del body[key]
        response = await request(app, "POST", "/vlp/calc", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["q_liq"], list(range(0, 401, 20)))
        self.assertEqual(len(data["p_wf"]), 21)
        self.assertTrue(all(math.isfinite(p) and p > 0 for p in data["p_wf"]))
        for i, reference in [(0, 47.48), (1, 47.56), (-1, 62.16)]:
            self.assertLess(abs(data["p_wf"][i] / reference - 1), 0.05)

    async def test_maximum_grid_and_small_rates(self):
        body = sample()
        body.update(points=201, q_max=0.01)
        body["pvt"]["wct"] = 100
        response = await request(app, "POST", "/vlp/calc", json=body)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["p_wf"]), 201)
        rates = data["q_liq"]
        self.assertEqual((len(rates), rates[0], rates[-1]), (201, 0, 0.01))
        self.assertTrue(all(a < b for a, b in zip(rates, rates[1:])))

    async def test_invalid_inputs_return_json(self):
        for path, value in [
            ("inclinometry.MD", [0, 0, 1500]), ("inclinometry.TVD", [0, 900]),
            ("inclinometry.TVD", [0, 1000, 999]), ("casing.d", 0.062), ("tubing.d", 0),
            ("tubing.h_mes", 1500), ("tubing.h_mes", -1), ("h_res", 1501),
            ("roughness", 0), ("q_max", -1), ("points", 1), ("points", 202),
            ("pvt.wct", 101), ("pvt.wct", -1), ("p_wh", 0), ("p_wh", "10"),
            ("pvt.t_res", -10), ("tubing.d", 1e-200),
        ]:
            with self.subTest(path=path, value=value):
                body = sample()
                keys = path.split(".")
                obj = body if len(keys) == 1 else body[keys[0]]
                obj[keys[-1]] = value
                response = await request(app, "POST", "/vlp/calc", json=body)
                self.assertEqual(response.status_code, 422)
                self.assertTrue(response.json()["detail"])

    async def test_openapi_example_matches_file(self):
        spec = (await request(app, "GET", "/openapi.json")).json()
        schema = spec["components"]["schemas"]["VlpRequest"]
        self.assertEqual(schema["examples"], [sample()])
        self.assertEqual(set(spec["paths"]["/vlp/calc"]["post"]["responses"]), {"200", "422"})
