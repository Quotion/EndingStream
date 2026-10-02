#!/usr/bin/env python3
from __future__ import annotations

import atexit
import os
import sys
import time
import urllib.request
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
LOG_PATH = DATA_DIR / "ending-backend.log"
PID_PATH = DATA_DIR / "ending-backend.pid"
STATUS_PATH = DATA_DIR / "ending-backend.status"


def parse_port() -> int:
    if len(sys.argv) > 1:
        try:
            return int(sys.argv[1])
        except ValueError:
            pass
    return int(os.getenv("ENDING_PORT", "8080"))


def is_backend_online(host: str, port: int) -> bool:
    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return response.getcode() == 200
    except Exception:
        return False


def write_status(message: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    STATUS_PATH.write_text(f"{timestamp} {message}\n", encoding="utf-8")


def setup_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_stream = LOG_PATH.open("a", encoding="utf-8", buffering=1)
    sys.stdout = log_stream
    sys.stderr = log_stream
    return log_stream


def cleanup_pid() -> None:
    try:
        current_pid = str(os.getpid())
        if PID_PATH.exists() and PID_PATH.read_text(encoding="utf-8").strip() == current_pid:
            PID_PATH.unlink()
    except OSError:
        pass


def main() -> None:
    host = os.getenv("ENDING_HOST", "127.0.0.1")
    port = parse_port()

    if is_backend_online(host, port):
        write_status(f"already running at http://{host}:{port}/")
        return

    setup_logging()
    os.chdir(ROOT_DIR)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(cleanup_pid)

    print("")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Ending backend")
    print(f"Project: {ROOT_DIR}")
    print(f"URL: http://{host}:{port}/ending.html")
    write_status(f"starting at http://{host}:{port}/")

    try:
        import uvicorn

        write_status(f"running at http://{host}:{port}/")
        uvicorn.run(
            "backend.app:create_app",
            factory=True,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
    except BaseException as exc:
        write_status(f"stopped: {exc!r}")
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Backend stopped: {exc!r}")
        raise


if __name__ == "__main__":
    main()
