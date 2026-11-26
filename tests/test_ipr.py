import math
import unittest

import httpx

from services.ipr.main import app
from services.ipr.model import calculate_ipr
from shared.contracts import IprRequest


SAMPLE = {"p_res": 200, "wct": 50, "pi": 1, "pb": 150}


def curve(**changes):
    return calculate_ipr(IprRequest(**(SAMPLE | changes)))


class IprModelTests(unittest.TestCase):
    def test_pure_oil_linear_and_vogel_meet_at_bubble_point(self):
        # Сетка содержит 1, 51, 101 (Pb), 151 и 201 атм.
        result = curve(p_res=201, pb=101, wct=0, pi=2, points=5)
        for pressure, rate in zip(result.p_wf, result.q_liq, strict=True):
            if pressure < 101:
                ratio = pressure / 101
                expected = 200 + (2 * 101 / 1.8) * (1 - 0.2 * ratio - 0.8 * ratio**2)
            else:
                expected = 2 * (201 - pressure)
            self.assertAlmostEqual(rate, expected, places=10)
        self.assertEqual(result.q_liq[2:], [200.0, 100.0, 0.0])

    def test_mixed_curve_recovers_pressure_at_every_rate(self):
        # Независимая обратная проверка по исходной форме Brown p(q).
        # Сетка с шагом 1 атм попадает ровно на обе границы (20 и 90 атм).
        p_res, pb, pi, water = 181, 90, 2, 0.5
        result = curve(p_res=p_res, pb=pb, pi=pi, wct=100 * water, points=181)
        bubble_rate = pi * (p_res - pb)
        oil_max = bubble_rate + pi * pb / 1.8

        def mixed_pressure(rate):
            part = (rate - bubble_rate) / (oil_max - bubble_rate)
            p_oil = pb / 8 * (math.sqrt(81 - 80 * part) - 1)
            p_water = p_res - rate / pi
            return water * p_water + (1 - water) * p_oil

        join = mixed_pressure(oil_max)
        span = 0.001 * oil_max
        slope = (mixed_pressure(oil_max - span) - join) / span
        for pressure, rate in zip(result.p_wf, result.q_liq, strict=True):
            if rate <= bubble_rate:
                reconstructed = p_res - rate / pi
            elif rate <= oil_max:
                reconstructed = mixed_pressure(rate)
            else:
                reconstructed = join - (rate - oil_max) * slope
            self.assertAlmostEqual(reconstructed, pressure, places=9)
        self.assertAlmostEqual(result.q_liq[19], oil_max)
        self.assertAlmostEqual(result.q_liq[49], 252.0)
        self.assertAlmostEqual(result.q_liq[89], bubble_rate)

    def test_grid_limits_order_and_finite_rates(self):
        for points in (2, 21, 201):
            for wct in (0, 1e-10, 17, 64.25, 99.99999999, 100):
                with self.subTest(points=points, wct=wct):
                    result = curve(wct=wct, points=points)
                    self.assertEqual(len(result.p_wf), points)
                    self.assertEqual(len(result.q_liq), points)
                    self.assertEqual((result.p_wf[0], result.p_wf[-1]), (1, 200))
                    self.assertEqual(result.q_liq[-1], 0)
                    for i, pressure in enumerate(result.p_wf):
                        self.assertAlmostEqual(pressure, 1 + 199 * i / (points - 1))
                    self.assertTrue(all(math.isfinite(q) and q >= 0 for q in result.q_liq))
                    self.assertTrue(all(a >= b for a, b in zip(result.q_liq, result.q_liq[1:])))

    def test_water_and_zero_bubble_pressure_are_linear(self):
        for settings in ({"wct": 100}, {"wct": 40, "pb": 0}, {"wct": 0, "pb": 0}):
            with self.subTest(settings=settings):
                result = curve(pi=1.5, **settings)
                self.assertEqual(result.q_liq, [1.5 * (200 - p) for p in result.p_wf])

    def test_zero_productivity(self):
        for wct in (0, 30, 100):
            with self.subTest(wct=wct):
                self.assertEqual(curve(pi=0, wct=wct).q_liq, [0.0] * 21)

    def test_saturated_reservoir_uses_reservoir_pressure(self):
        for pb in (200, 320):
            with self.subTest(pb=pb):
                result = curve(pb=pb, wct=0)
                for p, rate in zip(result.p_wf, result.q_liq, strict=True):
                    x = p / 200
                    self.assertAlmostEqual(rate, 200 / 1.8 * (1 - 0.2 * x - 0.8 * x**2))
                self.assertEqual(curve(pb=pb, wct=40), curve(pb=200, wct=40))

    def test_no_inflow_when_grid_is_at_or_above_reservoir_pressure(self):
        for p_res, pressures in ((0.5, [1, 0.75, 0.5]), (1, [1, 1, 1])):
            with self.subTest(p_res=p_res):
                result = curve(p_res=p_res, points=3)
                self.assertEqual(result.p_wf, pressures)
                self.assertEqual(result.q_liq, [0, 0, 0])

    def test_large_finite_pressure_and_small_productivity(self):
        result = curve(p_res=1e308, pb=1e308, pi=1e-308, points=201)
        self.assertTrue(all(math.isfinite(p) for p in result.p_wf))
        self.assertTrue(all(0 <= q <= 1 for q in result.q_liq))
        self.assertGreater(result.q_liq[0], 0.5)
        self.assertEqual(result.q_liq[-1], 0.0)


class IprHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_published_example_and_default_points(self):
        response = await self.client.post("/ipr/calc", json=SAMPLE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
        body = response.json()
        self.assertEqual(set(body), {"q_liq", "p_wf"})
        self.assertEqual(len(body["p_wf"]), 21)
        self.assertEqual(len(body["q_liq"]), 21)
        self.assertEqual(body["p_wf"][:2], [1, 10.95])
        self.assertEqual([round(q, 2) for q in body["q_liq"][:2]], [139.98, 137.93])
        self.assertEqual((body["p_wf"][-1], body["q_liq"][-1]), (200, 0))

    async def test_two_point_water_example(self):
        response = await self.client.post("/ipr/calc", json=SAMPLE | {"wct": 100, "points": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"q_liq": [199, 0], "p_wf": [1, 200]})

    async def test_field_ranges_and_types(self):
        invalid = {
            "p_res": [0, -0.1, "200"], "wct": [-0.01, 101, False],
            "pi": [-1, None], "pb": [-0.01, "150"],
            "points": [1, 202, 2.5, "21", True, None],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    response = await self.client.post("/ipr/calc", json=SAMPLE | {field: value})
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.headers["content-type"], "application/json")
                    self.assertIn(field, response.json()["detail"])

    async def test_required_fields_and_malformed_json(self):
        for field in SAMPLE:
            with self.subTest(field=field):
                body = SAMPLE.copy()
                del body[field]
                response = await self.client.post("/ipr/calc", json=body)
                self.assertEqual(response.status_code, 422)
                self.assertIn(field, response.json()["detail"])
        response = await self.client.post("/ipr/calc", content="{", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertIsInstance(response.json()["detail"], str)

    async def test_unrepresentable_rate_returns_json_error(self):
        response = await self.client.post("/ipr/calc", json=SAMPLE | {"pi": 1e308})
        self.assertEqual(response.status_code, 422)
        self.assertIn("дебит", response.json()["detail"])

    async def test_openapi_describes_active_endpoint(self):
        schema = (await self.client.get("/openapi.json")).json()
        response_types = schema["paths"]["/ipr/calc"]["post"]["responses"]
        self.assertEqual(set(response_types), {"200", "422"})
        s = schema["components"]["schemas"]["IprRequest"]
        self.assertCountEqual(s["required"], SAMPLE)
        self.assertEqual(s["properties"]["points"]["default"], 21)
        self.assertEqual(s["properties"]["points"]["minimum"], 2)
        self.assertEqual(s["properties"]["points"]["maximum"], 201)
