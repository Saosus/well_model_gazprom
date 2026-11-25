"""Обработка ошибочных HTTP-ответов внутренних сервисов."""

import unittest

import httpx
from fastapi import HTTPException

from services.backend.main import CURVE, NODAL_POINTS, post_calculation


class BackendErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_service_replies(self):
        for name, schema, response, status in [
            ("ipr", CURVE, httpx.Response(200, content=b"invalid"), 502),
            ("vlp", CURVE, httpx.Response(200, json={"q_liq": [0, 1], "p_wf": [1]}), 502),
            ("nodal", NODAL_POINTS, httpx.Response(200, json=[]), 502),
            ("ipr", CURVE, httpx.Response(500, content=b"internal"), 502),
            ("nodal", NODAL_POINTS, httpx.Response(422, json={"detail": "Нет пересечения"}), 422),
        ]:
            with self.subTest(name=name, response=response):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
                    with self.assertRaises(HTTPException) as caught:
                        await post_calculation(client, name, {}, schema)
                self.assertEqual(caught.exception.status_code, status)
                self.assertIn(name.upper(), caught.exception.detail)
                if status == 422:
                    self.assertIn("Нет пересечения", caught.exception.detail)

    async def test_transport_errors(self):
        for failure, status in [(httpx.ReadTimeout, 504), (httpx.ConnectError, 503)]:
            with self.subTest(failure=failure):
                def fail(request):
                    raise failure("unavailable", request=request)

                async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
                    with self.assertRaises(HTTPException) as caught:
                        await post_calculation(client, "vlp", {}, CURVE)
                self.assertEqual(caught.exception.status_code, status)
                self.assertIn("VLP", caught.exception.detail)
