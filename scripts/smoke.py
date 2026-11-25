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


def check_calculation(base: str, service_bases: dict[str, str]) -> None:
    sample_path = Path(__file__).resolve().parents[1] / "examples" / "calculate.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    result = json.loads(fetch(base + "/api/calculate", "application/json", sample))

    def direct(name: str, payload: dict):
        url = f"{service_bases[name]}/{name}/calc"
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


def check(core_only: bool, compose: bool, backend_port: int = 8080, gateway: str | None = None) -> None:
    services = ["ipr", "vlp", "nodal"] if core_only else list(PORTS)
    if compose and not core_only:
        gateway = "http://nginx"
    service_bases = {}
    for name in services:
        host = name if compose else "127.0.0.1"
        port = backend_port if name == "backend" else PORTS[name]
        service_bases[name] = gateway or f"http://{host}:{port}"
    for name, base in service_bases.items():
        if name == "frontend":
            config = fetch(f"{base}/backend-port.js", "text/javascript")
            expected_port = "null" if gateway else str(backend_port)
            if f"export const backendPort = {expected_port};" not in config.splitlines():
                raise ValueError(f"{base}/: frontend настроен на другой порт backend")
            if gateway:
                fetch(f"{base}/services.html", "text/html")
                for script in ("service-url", "dashboard", "app", "chart"):
                    fetch(f"{base}/{script}.js", "text/javascript")
                fetch(f"{base}/_swagger/swagger-ui.css", "text/css")
                fetch(f"{base}/_swagger/swagger-ui-bundle.js", "text/javascript")
            html = fetch(f"{base}/", "text/html")
            if "Well Model Builder" not in html:
                raise ValueError(f"{base}/: неожиданная главная страница")
            print(f"OK  {name:8} {base}/")
            continue
        path = "/health" if name == "backend" else f"/{name}/health"
        if json.loads(fetch(base + path, "application/json")) != {"status": "ok", "service": name}:
            raise ValueError(f"{base}{path}: неожиданный ответ health")
        docs_base = f"{base}/{name}" if gateway and name != "backend" else base
        schema = json.loads(fetch(f"{docs_base}/openapi.json", "application/json"))
        calc_path = "/api/calculate" if name == "backend" else f"/{name}/calc"
        if "post" not in schema["paths"].get(calc_path, {}):
            raise ValueError(f"{docs_base}/openapi.json: отсутствует POST {calc_path}")
        if "SwaggerUIBundle" not in fetch(f"{docs_base}/docs", "text/html"):
            raise ValueError(f"{docs_base}/docs: страница Swagger UI не найдена")
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
            check_calculation(base, service_bases)
        print(f"OK  {name:8} {base}{path}  {docs_base}/docs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", action="store_true", help="проверить только IPR, VLP и Nodal")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gateway", metavar="URL", help="проверить маршруты через один адрес Nginx")
    mode.add_argument("--compose", action="store_true", help="использовать DNS-имена внутри сети Compose")
    parser.add_argument("--backend-port", type=int, default=8080, help="порт backend для проверки")
    options = parser.parse_args()
    try:
        check(options.core, options.compose, options.backend_port, options.gateway.rstrip("/") if options.gateway else None)
    except (KeyError, ValueError, RuntimeError) as exc:
        sys.exit(f"FAIL  {exc}")
