import json
import re
import unittest
from pathlib import Path

import httpx

from services.backend.main import app as backend
from services.backend.main import service_status
from services.ipr.main import app as ipr
from services.nodal.main import app as nodal
from services.vlp.main import app as vlp
from shared.contracts import CalculateRequest, CalculateResponse, IprRequest, VlpRequest


ROOT = Path(__file__).resolve().parents[1]
APPS = {"ipr": ipr, "vlp": vlp, "nodal": nodal, "backend": backend}


def dashboard_example() -> dict:
    return json.loads((ROOT / "examples/calculate.json").read_text(encoding="utf-8"))


async def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


class ContractTests(unittest.TestCase):
    def test_readme_request_matches_example_file(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        blocks = [json.loads(s) for s in re.findall(r"^```json\n(.*?)^```", readme, flags=re.M | re.S)]
        example = next(block for block in blocks if "reservoir" in block)
        self.assertEqual(example, dashboard_example())
        self.assertEqual(CalculateRequest.model_validate(example).pvt.wct, 100)
        result = next(block for block in blocks if "operating_point" in block)
        validated = CalculateResponse.model_validate(result)
        self.assertEqual(len(validated.ipr.q_liq), example["sampling"]["ipr_points"])
        self.assertEqual(len(validated.vlp.q_liq), example["sampling"]["vlp_points"])

    def test_dashboard_has_shared_water_cut_and_default_sampling(self):
        example = dashboard_example()
        del example["sampling"], example["well"]["roughness"]
        data = CalculateRequest.model_validate(example)
        self.assertEqual(data.sampling.model_dump(), {"ipr_points": 21, "vlp_points": 21, "q_max": 400})
        self.assertEqual(data.well.roughness, 0.0001)
        self.assertNotIn("wct", data.reservoir.model_dump())

    def test_dashboard_values_fit_service_inputs(self):
        data = CalculateRequest.model_validate(dashboard_example())
        inflow = IprRequest(**data.reservoir.model_dump(), wct=data.pvt.wct, points=data.sampling.ipr_points)
        lift = VlpRequest(**data.well.model_dump(), pvt=data.pvt, q_max=data.sampling.q_max, points=data.sampling.vlp_points)
        self.assertEqual(inflow.wct, lift.pvt.wct)
        self.assertEqual(inflow.points, data.sampling.ipr_points)
        self.assertEqual(lift.points, data.sampling.vlp_points)
        self.assertEqual(lift.q_max, data.sampling.q_max)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_identity(self):
        for name, app in APPS.items():
            with self.subTest(name=name):
                path = "/health" if name == "backend" else f"/{name}/health"
                result = await request(app, "GET", path)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json(), {"status": "ok", "service": name})

    async def test_swagger_and_openapi(self):
        for name, app in APPS.items():
            with self.subTest(name=name):
                docs = await request(app, "GET", "/docs")
                self.assertEqual(docs.status_code, 200)
                self.assertIn("SwaggerUIBundle", docs.text)
                self.assertIn("/openapi.json", docs.text)
                spec = (await request(app, "GET", "/openapi.json")).json()
                path = "/api/calculate" if name == "backend" else f"/{name}/calc"
                operation = spec["paths"][path]["post"]
                self.assertIn("application/json", operation["requestBody"]["content"])
                self.assertIn("application/json", operation["responses"]["200"]["content"])
                for other in {"ipr", "vlp", "nodal"} - {name}:
                    self.assertNotIn(f"/{other}/calc", spec["paths"])

    async def test_nodal_success_schema_is_one_point_array(self):
        spec = (await request(nodal, "GET", "/openapi.json")).json()
        schema = spec["paths"]["/nodal/calc"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual((schema["type"], schema["minItems"], schema["maxItems"]), ("array", 1, 1))

    async def test_invalid_numbers_stay_json_errors(self):
        for token in ['"200"', "true", "NaN", "Infinity", "-Infinity", "1e1000"]:
            with self.subTest(token=token):
                body = '{"p_res":' + token + ',"pi":1,"pb":150,"wct":50}'
                result = await request(ipr, "POST", "/ipr/calc", content=body, headers={"Content-Type": "application/json"})
                self.assertEqual(result.status_code, 422)
                self.assertTrue(result.headers["content-type"].startswith("application/json"))
                self.assertIsInstance(result.json()["detail"], str)

    async def test_missing_and_invalid_dashboard_values(self):
        sample = dashboard_example()
        sample["pvt"]["wct"] = 101
        for body in [{}, sample]:
            with self.subTest(body=body):
                result = await request(backend, "POST", "/api/calculate", json=body)
                self.assertEqual(result.status_code, 422)
                self.assertIsInstance(result.json()["detail"], str)

    async def test_mismatched_curve_lengths(self):
        body = {"ipr": {"q_liq": [0, 2], "p_wf": [1, 2, 3]}, "vlp": {"q_liq": [0, 2], "p_wf": [1, 2]}}
        result = await request(nodal, "POST", "/nodal/calc", json=body)
        self.assertEqual(result.status_code, 422)
        self.assertIn("длины", result.json()["detail"])

    async def test_invalid_well_geometry(self):
        sample = dashboard_example()
        sample["well"]["tubing"]["h_mes"] = sample["well"]["h_res"]
        body = {**sample["well"], "pvt": sample["pvt"]}
        result = await request(vlp, "POST", "/vlp/calc", json=body)
        self.assertEqual(result.status_code, 422)
        self.assertIn("h_mes", result.json()["detail"])

    async def test_browser_origin_can_send_json(self):
        headers = {
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        }
        result = await request(backend, "OPTIONS", "/api/calculate", headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["access-control-allow-origin"], headers["Origin"])

    async def test_status_checks_identity_and_handles_failures(self):
        cases = [
            (httpx.Response(200, json={"status": "ok", "service": "ipr"}), "ok"),
            (httpx.Response(200, json={"status": "ok", "service": "vlp"}), "unavailable"),
            (httpx.Response(200, content=b"broken"), "unavailable"),
            (httpx.Response(503), "unavailable"),
        ]
        for upstream, expected in cases:
            with self.subTest(expected=expected, upstream=upstream):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: upstream)) as client:
                    self.assertEqual(await service_status(client, "ipr", "http://ipr:8003"), expected)

        def offline(req):
            raise httpx.ConnectError("offline", request=req)

        async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
            self.assertEqual(await service_status(client, "ipr", "http://ipr:8003"), "unavailable")
