"""Запустить сервисы отдельными процессами на локальной машине с Python 3.12."""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_PORTS = {"ipr": 8003, "vlp": 8002, "nodal": 8004, "backend": 8080}


def run(core_only: bool, backend_port: int = 8080) -> None:
    commands = {
        name: [sys.executable, "-m", "uvicorn", f"services.{name}.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-use-colors"]
        for name, port in {**API_PORTS, "backend": backend_port}.items() if not (core_only and name == "backend")
    }
    if not core_only:
        commands["frontend"] = [
            sys.executable, "-m", "uvicorn", "scripts.local_frontend:app", "--host", "127.0.0.1",
            "--port", "3000", "--no-use-colors",
        ]
    processes = {}
    try:
        if not core_only:
            print(f"Backend: http://127.0.0.1:{backend_port}/docs   Страница: http://127.0.0.1:3000/", flush=True)
        for name, command in commands.items():
            processes[name] = subprocess.Popen(command, cwd=ROOT, env={**os.environ, "BACKEND_PORT": str(backend_port)})
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", action="store_true", help="запустить только IPR, VLP и Nodal")
    parser.add_argument("--backend-port", type=int, default=8080, help="порт backend для локального запуска")
    options = parser.parse_args()
    if not 1 <= options.backend_port <= 65535 or options.backend_port in (3000, 8002, 8003, 8004):
        parser.error("Выберите свободный порт backend от 1 до 65535, отдельный от остальных сервисов.")
    run(options.core, options.backend_port)
