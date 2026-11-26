"""Временные HTTP-сервисы для проверок. Порты резервирует ОС до запуска детей."""

from contextlib import ExitStack, contextmanager
from multiprocessing import get_context
import os
import socket
import time

import httpx
import uvicorn


def serve(name, listener, urls):
    os.environ.update({f"{key.upper()}_URL": url for key, url in urls.items() if key != "backend"})
    config = uvicorn.Config(f"services.{name}.main:app", log_level="warning", access_log=False)
    uvicorn.Server(config).run(sockets=[listener])


def stop(process):
    if process.is_alive():
        process.terminate()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join()
    process.close()


@contextmanager
def running_stack():
    with ExitStack() as stack:
        listeners, urls = {}, {}
        for name in ("ipr", "vlp", "nodal", "backend"):
            sock = stack.enter_context(socket.socket())
            sock.bind(("127.0.0.1", 0))
            listeners[name] = sock
            urls[name] = f"http://127.0.0.1:{sock.getsockname()[1]}"
        workers = {}
        for name, sock in listeners.items():
            proc = get_context("spawn").Process(target=serve, args=(name, sock, urls))
            proc.start()
            stack.callback(stop, proc)
            workers[name] = proc
        client = stack.enter_context(httpx.Client(timeout=90, trust_env=False))
        deadline = time.monotonic() + 60
        for name, base in urls.items():
            path = "/health" if name == "backend" else f"/{name}/health"
            while time.monotonic() < deadline:
                if not workers[name].is_alive():
                    raise RuntimeError(f"{name}: процесс завершился при запуске")
                try:
                    r = client.get(base + path, timeout=1)
                    if r.status_code == 200 and r.json() == {"status": "ok", "service": name}:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError(f"{name}: не получен корректный health за 60 с")
        yield client, urls
