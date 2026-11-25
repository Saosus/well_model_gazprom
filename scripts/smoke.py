"""Проверка запущенных HTTP-приложений. Зависит только от стандартной библиотеки."""

import argparse
import json
import math
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


PORTS = {"ipr": 8003, "vlp": 8002, "nodal": 8004, "backend": 8080, "frontend": 3000}
opener = build_opener(ProxyHandler({}))


def fetch(url: str, media_type: str, payload: dict | None = None) -> str:
    request = Request(url)
    if payload is not None:
        request.data = json.dumps(payload).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    deadline = time.monotonic() + 20
    while True:
        try:
            with opener.open(request, timeout=3) as response:
                actual_type = response.headers.get_content_type()
                valid_types = {media_type, "application/javascript"} if media_type == "text/javascript" else {media_type}
                if response.status != 200 or actual_type not in valid_types:
                    raise ValueError(f"{url}: HTTP {response.status}, Content-Type {actual_type}")
                return response.read().decode("utf-8")
        except HTTPError as error:
            raise RuntimeError(f"{url}: HTTP {error.code} {error.read().decode('utf-8', 'replace')}") from error
        except (OSError, URLError) as error:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{url}: {error}") from error
            time.sleep(0.3)


def check_calculation(base: str, compose: bool) -> None:
    sample_path = Path(__file__).resolve().parents[1] / "examples" / "calculate.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    result = json.loads(fetch(base + "/api/calculate", "application/json", sample))

    def direct(name: str, payload: dict):
        host = name if compose else "127.0.0.1"
        url = f"http://{host}:{PORTS[name]}/{name}/calc"
        return json.loads(fetch(url, "application/json", payload))

    sampling = sample["sampling"]
    ipr = direct("ipr", {**sample["reservoir"], "wct": sample["pvt"]["wct"], "points": sampling["ipr_points"]})
    vlp = direct("vlp", {**sample["well"], "pvt": sample["pvt"], "q_max": sampling["q_max"],
                         "points": sampling["vlp_points"]})
    curves = {"ipr": ipr, "vlp": vlp}
    points = direct("nodal", curves)
    if len(points) != 1 or result != {**curves, "operating_point": points[0]}:
        raise ValueError("Ответ backend отличается от последовательного вызова IPR, VLP и Nodal.")
    print(f"OK  Backend  обе кривые и точка совпали: q_liq={points[0]['q_liq']:.5f}, p_wf={points[0]['p_wf']:.5f}")


def check(core_only: bool, compose: bool, backend_port: int = 8080) -> None:
    services = ["ipr", "vlp", "nodal"] if core_only else (
        ["ipr", "vlp", "nodal", "backend"] if compose else list(PORTS)
    )
    for name in services:
        host = name if compose else "127.0.0.1"
        port = backend_port if name == "backend" else PORTS[name]
        base = f"http://{host}:{port}"
        if name == "frontend":
            config = fetch(f"{base}/backend-port.js", "text/javascript")
            if "export const backendPort" not in config or "export function backendUrl" not in config:
                raise ValueError(f"{base}/: frontend не содержит helper backendUrl")
            html = fetch(f"{base}/", "text/html")
            if "Well Model Builder" not in html:
                raise ValueError(f"{base}/: неожиданная главная страница")
            print(f"OK  {name:8} {base}/")
            continue
        path = "/health" if name == "backend" else f"/{name}/health"
        if json.loads(fetch(base + path, "application/json")) != {"status": "ok", "service": name}:
            raise ValueError(f"{base}{path}: неожиданный ответ health")
        schema = json.loads(fetch(f"{base}/openapi.json", "application/json"))
        calc_path = "/api/calculate" if name == "backend" else f"/{name}/calc"
        if "post" not in schema["paths"].get(calc_path, {}):
            raise ValueError(f"{base}/openapi.json: отсутствует POST {calc_path}")
        if "SwaggerUIBundle" not in fetch(f"{base}/docs", "text/html"):
            raise ValueError(f"{base}/docs: страница Swagger UI не найдена")
        if name == "ipr":
            sample = {"p_res": 200, "wct": 100, "pi": 1, "pb": 150, "points": 2}
            curve = json.loads(fetch(base + calc_path, "application/json", sample))
            if curve != {"q_liq": [199, 0], "p_wf": [1, 200]}:
                raise ValueError(f"{base}{calc_path}: неверная кривая для 100% воды")
            print(f"OK  IPR      POST {base}{calc_path}  q_liq=[199, 0]")
        if name == "vlp":
            sample = schema["components"]["schemas"]["VlpRequest"]["examples"][0]
            curve = json.loads(fetch(base + calc_path, "application/json", sample))
            if curve["q_liq"] != list(range(0, 401, 20)) or len(curve["p_wf"]) != 21:
                raise ValueError(f"{base}{calc_path}: неверная сетка из §5.3")
            if not all(math.isfinite(p) and p > 0 for p in curve["p_wf"]):
                raise ValueError(f"{base}{calc_path}: некорректное забойное давление")
            print(f"OK  VLP      POST {base}{calc_path}  P(0)={curve['p_wf'][0]:.4f}, P(400)={curve['p_wf'][-1]:.4f}")
        if name == "nodal":
            sample = schema["components"]["schemas"]["NodalRequest"]["examples"][0]
            point = json.loads(fetch(base + calc_path, "application/json", sample))
            if point != [{"q_liq": 1, "p_wf": 2}]:
                raise ValueError(f"{base}{calc_path}: неверная рабочая точка {point}")
            print(f"OK  Nodal    POST {base}{calc_path}  q_liq=1, p_wf=2")
        if name == "backend":
            statuses = json.loads(fetch(f"{base}/api/services/health", "application/json"))
            if statuses != {"ipr": "ok", "vlp": "ok", "nodal": "ok"}:
                raise ValueError(f"Связь backend с сервисами: {statuses}")
            check_calculation(base, compose)
        print(f"OK  {name:8} {base}{path}  /docs  /openapi.json")


def check_proxy(base: str) -> None:
    """Проверить единый Nginx entrypoint без прямой публикации внутренних портов."""
    base = base.rstrip("/")
    routes = [
        ("/", "text/html"),
        ("/services.html", "text/html"),
        ("/nginx-health", "text/plain"),
        ("/health", "application/json"),
        ("/backend/health", "application/json"),
        ("/backend/docs", "text/html"),
        ("/backend/openapi.json", "application/json"),
        ("/ipr/health", "application/json"),
        ("/ipr/docs", "text/html"),
        ("/ipr/openapi.json", "application/json"),
        ("/vlp/health", "application/json"),
        ("/vlp/docs", "text/html"),
        ("/vlp/openapi.json", "application/json"),
        ("/nodal/health", "application/json"),
        ("/nodal/docs", "text/html"),
        ("/nodal/openapi.json", "application/json"),
    ]
    # API images bundle Swagger UI assets in Docker. Native source runs may
    # intentionally use the CDN fallback because shared/swagger is absent.
    bundled_swagger = (
        Path(__file__).resolve().parents[1]
        / "shared" / "swagger" / "swagger-ui-bundle.js"
    ).is_file()
    if bundled_swagger:
        for prefix in ("/backend", "/ipr", "/vlp", "/nodal"):
            routes.extend([
                (f"{prefix}/_swagger/swagger-ui-bundle.js", "text/javascript"),
                (f"{prefix}/_swagger/swagger-ui.css", "text/css"),
            ])

    for path, media in routes:
        body = fetch(base + path, media)
        if path.endswith("/docs"):
            if "SwaggerUIBundle" not in body:
                raise ValueError(f"{base}{path}: Swagger UI не найден")
            prefix = path.removesuffix("/docs")
            has_prefixed_assets = f"{prefix}/_swagger/" in body
            uses_cdn_fallback = "cdn.jsdelivr.net/npm/swagger-ui-dist" in body
            if f"{prefix}/openapi.json" not in body or not (has_prefixed_assets or uses_cdn_fallback):
                raise ValueError(f"{base}{path}: Swagger UI не использует префикс {prefix} или не содержит assets")
            if bundled_swagger and not has_prefixed_assets:
                raise ValueError(f"{base}{path}: локальные Swagger assets не используют префикс {prefix}")
        if path.endswith("/openapi.json"):
            schema = json.loads(body)
            expected = "/api/calculate" if path.startswith("/backend/") else f"/{path.split('/')[1]}/calc"
            if "post" not in schema.get("paths", {}).get(expected, {}):
                raise ValueError(f"{base}{path}: отсутствует POST {expected}")
        if path.endswith("/health"):
            name = "backend" if path == "/health" or path.startswith("/backend/") else path.split("/")[1]
            if json.loads(body) != {"status": "ok", "service": name}:
                raise ValueError(f"{base}{path}: некорректный health")
        print(f"OK  Nginx    {base}{path}")

    sample_path = Path(__file__).resolve().parents[1] / "examples" / "calculate.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    result = json.loads(fetch(base + "/api/calculate", "application/json", sample))
    if not isinstance(result.get("operating_point"), dict):
        raise ValueError(f"{base}/api/calculate: нет рабочей точки")
    print("OK  Nginx    POST /api/calculate")

    # Exercise each public calculation route as well as the orchestrated backend route.
    sampling = sample["sampling"]
    ipr_input = {**sample["reservoir"], "wct": sample["pvt"]["wct"], "points": sampling["ipr_points"]}
    vlp_input = {**sample["well"], "pvt": sample["pvt"], "q_max": sampling["q_max"],
                 "points": sampling["vlp_points"]}
    ipr = json.loads(fetch(base + "/ipr/calc", "application/json", ipr_input))
    vlp = json.loads(fetch(base + "/vlp/calc", "application/json", vlp_input))
    nodal = json.loads(fetch(base + "/nodal/calc", "application/json", {"ipr": ipr, "vlp": vlp}))
    if len(nodal) != 1 or nodal[0] != result["operating_point"]:
        raise ValueError(f"{base}: прямой вызов Nodal не совпал с backend")
    print("OK  Nginx    POST /ipr/calc, /vlp/calc, /nodal/calc")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", action="store_true", help="проверить только IPR, VLP и Nodal")
    parser.add_argument("--compose", action="store_true", help="использовать DNS-имена внутри сети Compose")
    parser.add_argument("--backend-port", type=int, default=8080, help="порт backend для проверки")
    parser.add_argument("--proxy", help="проверить маршруты Nginx по указанному base URL, например http://nginx")
    options = parser.parse_args()
    try:
        check(options.core, options.compose, options.backend_port)
        if options.proxy:
            check_proxy(options.proxy)
    except (KeyError, ValueError, RuntimeError) as exc:
        sys.exit(f"FAIL  {exc}")
