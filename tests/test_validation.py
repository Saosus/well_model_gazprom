import unittest

from shared.validation import preview
from tests.test_scaffold import backend, vlp, dashboard_example, request


class InputChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.data = dashboard_example()

    def test_inclusive_and_phase_boundaries(self):
        self.data["reservoir"].update(pb=0, pi=0, p_res=0.5)
        self.data["sampling"].update(ipr_points=2, vlp_points=201)
        self.data["pvt"].update(wct=100, t_res=-200, rp=0, gamma_oil=0.1)
        self.data["well"].update(geo_grad=0)
        self.data["well"]["tubing"]["h_mes"] = 0
        self.data["well"]["inclinometry"]["TVD"] = [0] * len(self.data["well"]["inclinometry"]["MD"])
        self.assertEqual(preview(self.data), [])
        self.data["pvt"]["wct"] = 0
        self.assertTrue(any(i["level"] == "error" for i in preview(self.data)))

    def test_interpolated_temperature(self):
        self.data["pvt"].update(wct=10, t_res=12)
        self.data["well"].update(h_res=150, geo_grad=20)
        self.data["well"]["tubing"]["h_mes"] = 0
        self.data["well"]["inclinometry"] = {"MD": [100, 200], "TVD": [100, 200]}
        errors = [i for i in preview(self.data) if i["level"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("well.inclinometry.TVD.1", errors[0]["paths"])
        self.data["well"]["geo_grad"] = 0
        self.assertFalse(any(i["level"] == "error" for i in preview(self.data)))

    async def test_warnings_allow_calculation(self):
        self.data["pvt"].update(wct=50, t_res=150, gamma_oil=0.74)
        issues = preview(self.data)
        self.assertTrue(issues)
        self.assertTrue(all(i["level"] == "warning" for i in issues))
        body = {**self.data["well"], "pvt": self.data["pvt"], "points": 2}
        self.assertEqual((await request(vlp, "POST", "/vlp/calc", json=body)).status_code, 200)

    async def test_independent_and_cross_field_errors_survive_invalid_siblings(self):
        self.data["well"]["casing"]["d"] = self.data["well"]["tubing"]["d"]
        self.data["well"]["inclinometry"] = {"MD": [0, 10, 10], "TVD": [0, 12, 9]}
        self.data["pvt"]["wct"] = 101
        self.data["sampling"]["ipr_points"] = 1.5
        self.data["well"]["p_wh"] = None
        visible = (await request(backend, "POST", "/api/validate", json=self.data)).json()["issues"]
        paths = {p for i in visible for p in i["paths"]}
        self.assertTrue({"pvt.wct", "well.p_wh", "sampling.ipr_points", "well.casing.d", "well.tubing.d",
                         "well.h_res", "well.inclinometry.MD.2", "well.inclinometry.TVD.2"} <= paths)
        rejected = await request(backend, "POST", "/api/calculate", json=self.data)
        self.assertEqual(rejected.status_code, 422)
        for path in paths:
            self.assertIn(path, rejected.json()["detail"])

    def test_missing_cell_and_subnormal_grid(self):
        self.data["well"]["inclinometry"] = {"MD": [0, 1000], "TVD": [0]}
        self.data["sampling"].update(vlp_points=3, q_max=5e-324)
        paths = {p for i in preview(self.data) for p in i["paths"]}
        self.assertTrue({"well.inclinometry.TVD.1", "sampling.q_max", "sampling.vlp_points"} <= paths)
