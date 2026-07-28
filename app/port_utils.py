"""
Shared port-negotiation helper for EcoVision Sentinel.

Each Python process (backend.py on default 8000, main.py/detector on
default 8001) calls find_free_port(preferred) to get a bindable port,
starts uvicorn/flask on it, then calls write_runtime_port(name, port)
so Electron (main.js) can discover which port it actually landed on.
"""
import json
import os
import socket
import threading

_LOCK = threading.Lock()


def find_free_port(preferred: int, max_attempts: int = 20) -> int:
    """Try `preferred`, then preferred+1, +2, ... up to max_attempts times.
    Returns the first port that can be bound. Raises RuntimeError if none
    of the attempted ports are free."""
    for offset in range(max_attempts):
        candidate = preferred + offset
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", candidate))
            s.close()
            return candidate
        except OSError:
            s.close()
            continue
    raise RuntimeError(
        f"Could not find a free port after {max_attempts} attempts starting from {preferred}."
    )


def _runtime_ports_path() -> str:
    writable_dir = os.environ.get("ECOVISION_WRITABLE_DIR") or os.path.join(
        os.path.expanduser("~"), "EcoVisionSentinelData"
    )
    os.makedirs(writable_dir, exist_ok=True)
    return os.path.join(writable_dir, "runtime_ports.json")


def write_runtime_port(name: str, port: int) -> None:
    """Merge-writes {name: port} into runtime_ports.json so Electron (and
    other local processes) can discover the actual bound port."""
    path = _runtime_ports_path()
    with _LOCK:
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[name] = port
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)


def read_runtime_ports() -> dict:
    path = _runtime_ports_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}