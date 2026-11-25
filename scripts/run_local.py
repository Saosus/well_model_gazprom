"""Запустить сервисы отдельными процессами на локальной машине или сервере."""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
API_PORTS = {"ipr": 8003, "vlp": 8002, "nodal": 8004, "backend": 8080}
DEFAULT_FRONTEND_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://[::1]:3000",
)


def normalize_origin(value: str) -> str:
    """Проверить и нормализовать origin, разрешённый для запросов браузера."""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "http"
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError(
            "origin должен иметь вид http://адрес:3000"
        )
    return f"{parsed.scheme}://{parsed.netloc}"


def run(
    core_only: bool,
    backend_port: int = 8080,
    host: str = "127.0.0.1",
    frontend_origins: list[str] | None = None,
) -> None:
    ports = {**API_PORTS, "backend": backend_port}
    commands = {
        name: [
            sys.executable, "-m", "uvicorn", f"services.{name}.main:app",
            "--host", host, "--port", str(port), "--no-use-colors",
        ]
        for name, port in ports.items()
        if not (core_only and name == "backend")
    }
    if not core_only:
        commands["frontend"] = [
            sys.executable, "-m", "uvicorn", "scripts.local_frontend:app",
            "--host", host, "--port", "3000", "--no-use-colors",
        ]

    inherited_origins = os.environ.get("FRONTEND_ORIGINS", ",".join(DEFAULT_FRONTEND_ORIGINS))
    origins = [origin.strip() for origin in inherited_origins.split(",") if origin.strip()]
    origins.extend(frontend_origins or [])
    environment = {
        **os.environ,
        "BACKEND_PORT": str(backend_port),
        "FRONTEND_ORIGINS": ",".join(dict.fromkeys(origins)),
    }

    processes: dict[str, subprocess.Popen] = {}
    try:
        if not core_only:
            print(
                f"Backend: http://127.0.0.1:{backend_port}/docs   "
                "Страница: http://127.0.0.1:3000/",
                flush=True,
            )
            if host not in {"127.0.0.1", "localhost", "::1"}:
                print(
                    "Сетевой режим: сервисы слушают на "
                    f"{host}. Открывайте страницу по адресу сервера на порту 3000.",
                    flush=True,
                )
        for name, command in commands.items():
            processes[name] = subprocess.Popen(command, cwd=ROOT, env=environment)
        print("Запуск сервисов. Для остановки нажмите Ctrl+C.", flush=True)
        while True:
            for name, process in processes.items():
                if process.poll() is not None:
                    raise SystemExit(f"Процесс {name} завершился с кодом {process.returncode}.")
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
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


def handle_sigterm(_signum, _frame) -> None:
    """Let the normal cleanup path stop all child processes on SIGTERM."""
    raise KeyboardInterrupt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", action="store_true", help="запустить только IPR, VLP и Nodal")
    parser.add_argument("--backend-port", type=int, default=8080, help="порт backend для локального запуска")
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="адрес прослушивания; используйте 0.0.0.0 для доступа по сети (по умолчанию 127.0.0.1)",
    )
    parser.add_argument(
        "--frontend-origin", action="append", type=normalize_origin, default=[], metavar="ORIGIN",
        help="дополнительный разрешённый origin frontend, например http://192.0.2.10:3000; можно повторять",
    )
    options = parser.parse_args()

    # Gracefully stop child services when a process manager sends SIGTERM (e.g. systemd).
    signal.signal(signal.SIGTERM, handle_sigterm)
    if not 1 <= options.backend_port <= 65535 or options.backend_port in (3000, 8002, 8003, 8004):
        parser.error("Выберите свободный порт backend от 1 до 65535, отдельный от остальных сервисов.")
    run(options.core, options.backend_port, options.host, options.frontend_origin)
