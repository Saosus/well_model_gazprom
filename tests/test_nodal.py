import json
import unittest
from pathlib import Path

import httpx

from services.nodal.main import app


def curve(q, p):
    return {"q_liq": q, "p_wf": p}


class NodalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def post(self, ipr, vlp):
        return await self.client.post("/nodal/calc", json={"ipr": ipr, "vlp": vlp})

    async def test_example_and_reversed_directions(self):
        sample = Path(__file__).resolve().parents[1] / "examples/nodal.json"
        curves = json.loads(sample.read_text(encoding="utf-8"))
        for reverse_ipr, reverse_vlp in [(False, False), (True, False), (False, True), (True, True)]:
            with self.subTest(reverse_ipr=reverse_ipr, reverse_vlp=reverse_vlp):
                ipr, vlp = [
                    {k: v[::-1] if reverse else v for k, v in curves[key].items()}
                    for key, reverse in [("ipr", reverse_ipr), ("vlp", reverse_vlp)]
                ]
                result = await self.post(ipr, vlp)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json(), [{"q_liq": 1, "p_wf": 2}])

    async def test_geometry(self):
        cases = [
            ("граница", curve([2, 0], [1, 3]), curve([0, 2], [3, 4]), 0, 3),
            ("общий узел", curve([4, 2, 0], [1, 3, 5]), curve([0, 2, 4], [2, 3, 4]), 2, 3),
            ("касание", curve([0, 2, 4], [5, 3, 4]), curve([0, 4], [3, 3]), 2, 3),
            ("вертикаль", curve([0, 0], [1, 5]), curve([0, 3], [4, 6]), 0, 4),
            ("разные сетки", curve([8, 6, 2, 0], [2, 3, 7, 10]), curve([1, 5], [3, 5]), 13/3, 14/3),
            ("общий конец на прямой", curve([0, 2], [1, 3]), curve([2, 3], [3, 4]), 2, 3),
            ("повтор точки", curve([1, 1], [2, 2]), curve([0, 2], [1, 3]), 1, 2),
            ("большие координаты", curve([0, 1e300], [3, 1]), curve([0, 1e300], [1, 3]), 5e299, 2),
        ]
        for name, ipr, vlp, q, p in cases:
            with self.subTest(name=name):
                result = await self.post(ipr, vlp)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json(), [{"q_liq": q, "p_wf": p}])

    async def test_missing_or_ambiguous_intersection(self):
        ipr = curve([2, 0], [1, 3])
        for name, vlp in [
            ("выше IPR", curve([0, 2], [4, 5])),
            ("вне интервала", curve([3, 4], [2, 3])),
            ("параллельные", curve([0, 2], [3.000000000001, 1.000000000001])),
            ("два пересечения", curve([0, 1, 2], [2, 3, 0.5])),
            ("общий отрезок", curve([0, 1], [3, 2])),
        ]:
            with self.subTest(name=name):
                result = await self.post(ipr, vlp)
                self.assertEqual(result.status_code, 422)
                self.assertTrue(result.json()["detail"])

    async def test_invalid_coordinates(self):
        good = curve([0, 2], [1, 3])
        for bad in [curve([1], [2]), curve([1, 2], [1, 2, 3]), curve([-1, 2], [1, 2]),
                    curve([1, 2], [0, 2]), curve([1, "2"], [1, 2])]:
            with self.subTest(bad=bad):
                result = await self.post(bad, good)
                self.assertEqual(result.status_code, 422)
                self.assertTrue(result.json()["detail"])
        for token in ["NaN", "Infinity", "-Infinity", "1e1000"]:
            with self.subTest(token=token):
                raw = '{"ipr":{"q_liq":[0,2],"p_wf":[1,3]},"vlp":{"q_liq":[0,2],"p_wf":[1,' + token + ']}}'
                result = await self.client.post("/nodal/calc", content=raw, headers={"Content-Type": "application/json"})
                self.assertEqual(result.status_code, 422)
                self.assertTrue(result.json()["detail"])
