"""Запустить Well Model Builder отдельными процессами без Docker/Podman."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
API_PORTS = {"ipr": 8003, "vlp": 8002, "nodal": 8004}
FRONTEND_PORT = 3000
DEFAULT_BACKEND_PORT = 8080
STARTUP_TIMEOUT = 30


def _url_host(host: str) -> str:
    """Return host formatted for an HTTP URL, including IPv6 brackets."""
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _probe_host(host: str) -> str:
    """Use a loopback address when servers listen on a wildcard interface."""
    if host in {"0.0.0.0", ""}:
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def _health_targets(core_only: bool, backend_port: int) -> dict[str, tuple[int, str, str | None]]:
    targets = {
        "ipr": (API_PORTS["ipr"], "/ipr/health", "ipr"),
        "vlp": (API_PORTS["vlp"], "/vlp/health", "vlp"),
        "nodal": (API_PORTS["nodal"], "/nodal/health", "nodal"),
    }
    if not core_only:
        targets["backend"] = (backend_port, "/health", "backend")
        targets["frontend"] = (FRONTEND_PORT, "/", None)
    return targets


def _wait_until_ready(
    processes: dict[str, subprocess.Popen], host: str, targets: dict[str, tuple[int, str, str | None]],
) -> None:
    probe_host = _url_host(_probe_host(host))
    opener = build_opener(ProxyHandler({}))
    for name, (port, path, service_name) in targets.items():
        url = f"http://{probe_host}:{port}{path}"
        last_error = "нет ответа"
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            process = processes[name]
            if process.poll() is not None:
                raise RuntimeError(f"Процесс {name} завершился при запуске (код {process.returncode}).")
            try:
                request = Request(url, headers={"User-Agent": "Well-Model-Builder-local-check/1.0"})
                with opener.open(request, timeout=1.5) as response:
                    if response.status != 200:
                        last_error = f"HTTP {response.status}"
                    elif service_name is None:
                        frontend_body = response.read(64)
                        if frontend_body:
                            break
                        last_error = "пустой ответ frontend"
                    else:
                        payload = json.loads(response.read().decode("utf-8"))
                        if payload == {"status": "ok", "service": service_name}:
                            break
                        last_error = f"неожиданный health: {payload!r}"
            except (OSError, ValueError, URLError) as exc:
                last_error = str(exc)
            time.sleep(0.2)
        else:
            raise RuntimeError(f"{name} не готов за {STARTUP_TIMEOUT} с: {last_error} ({url}).")


def run(core_only: bool, backend_port: int = DEFAULT_BACKEND_PORT, host: str = "127.0.0.1") -> None:
    ports = dict(API_PORTS)
    if not core_only:
        ports["backend"] = backend_port
        ports["frontend"] = FRONTEND_PORT

    commands = {
        name: [
            sys.executable, "-m", "uvicorn", f"services.{name}.main:app",
            "--host", host, "--port", str(port), "--no-use-colors",
        ]
        for name, port in ports.items() if name != "frontend"
    }
    if not core_only:
        commands["frontend"] = [
            sys.executable, "-m", "uvicorn", "scripts.local_frontend:app",
            "--host", host, "--port", str(FRONTEND_PORT), "--no-use-colors",
        ]

    env = os.environ.copy()
    env["BACKEND_PORT"] = str(backend_port)
    # Native services communicate over loopback by default. Explicit shell variables
    # may override these values when a custom local topology is required.
    env.setdefault("IPR_URL", "http://127.0.0.1:8003")
    env.setdefault("VLP_URL", "http://127.0.0.1:8002")
    env.setdefault("NODAL_URL", "http://127.0.0.1:8004")
    env.setdefault(
        "FRONTEND_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://[::1]:3000",
    )

    processes: dict[str, subprocess.Popen] = {}
    try:
        for name, command in commands.items():
            processes[name] = subprocess.Popen(command, cwd=ROOT, env=env)

        _wait_until_ready(processes, host, _health_targets(core_only, backend_port))

        display_host = _probe_host(host)
        display_host_url = _url_host(display_host)
        if core_only:
            print("Расчётные сервисы готовы:", flush=True)
            for name, port in API_PORTS.items():
                print(f"  {name.upper()}: http://{display_host_url}:{port}", flush=True)
        else:
            print("Все пять компонентов Well Model Builder готовы.", flush=True)
            print(f"Dashboard: http://{display_host_url}:{FRONTEND_PORT}/", flush=True)
            print(f"Backend:   http://{display_host_url}:{backend_port}/docs", flush=True)
            print(f"Services:  http://{display_host_url}:{FRONTEND_PORT}/services.html", flush=True)
            if host in {"0.0.0.0", "::"}:
                print("Сервер слушает все интерфейсы. Для доступа с другой машины используйте IP этого узла.", flush=True)
        print("Для остановки нажмите Ctrl+C.", flush=True)

        while True:
            for name, process in processes.items():
                if process.poll() is not None:
                    raise RuntimeError(f"Процесс {name} неожиданно завершился (код {process.returncode}).")
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\nОстановка Well Model Builder…", flush=True)
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", action="store_true", help="запустить только IPR, VLP и Nodal")
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT,
                        help="порт backend; можно изменить, если 8080 занят")
    parser.add_argument("--host", default="127.0.0.1",
                        help="адрес привязки всех процессов; по умолчанию только localhost, для LAN используйте 0.0.0.0")
    options = parser.parse_args()

    if not 1 <= options.backend_port <= 65535 or options.backend_port in (*API_PORTS.values(), FRONTEND_PORT):
        parser.error("Выберите свободный порт backend от 1 до 65535, отдельный от 3000, 8002, 8003 и 8004.")
    if not options.host.strip():
        parser.error("--host не может быть пустым.")

    try:
        run(options.core, options.backend_port, options.host)
    except RuntimeError as exc:
        print(f"ОШИБКА ЗАПУСКА: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
