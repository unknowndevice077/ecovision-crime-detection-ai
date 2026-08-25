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


def start_parent_watchdog(on_exit=None, poll_seconds: float = 5.0) -> None:
    """Self-terminates this process once the Electron process that spawned
    it (its PID passed via ECOVISION_PARENT_PID -- see spawnPython() in
    electron/main.js) has disappeared.

    BUG FOUND 2026-08-25: Electron's killAll() already tree-kills
    backend.py/main.py on a normal close (window-all-closed / before-quit /
    will-quit all call it) -- but that only runs if Electron's OWN shutdown
    code actually executes. On Windows, a child process is NOT automatically
    killed when its parent dies; there is no PDEATHSIG/process-group
    teardown the way there is on Linux. So a crashed or force-killed
    Electron process (Task Manager, a hung render process someone kills by
    PID, a previous run that never exited cleanly) leaves every python.exe
    it spawned running indefinitely -- each still holding its own share of
    a 6 GB GPU. The next launch then loads a full second set of models onto
    the same card on top of whatever the orphan never released. That is
    exactly the "optimize weights, cancel, close, reopen -- the whole
    laptop crashed" report this exists to prevent: VRAM exhaustion from
    stacked orphaned processes, not a one-off fluke.

    This polls because that is the only cross-platform way to observe "my
    parent -- an unrelated process from Windows' point of view once it's
    gone -- has disappeared"; Windows has no parent-death signal.
    `on_exit`, if given, runs first so a process holding its own tracked
    subprocess (backend.py's optimize_weights.py run) can try to take it
    down too before this process exits.
    """
    parent_pid_raw = os.environ.get("ECOVISION_PARENT_PID")
    if not parent_pid_raw:
        return  # not launched by Electron (standalone/dev run) -- nothing to watch
    try:
        parent_pid = int(parent_pid_raw)
    except ValueError:
        return

    import time
    import psutil

    def _watch():
        while True:
            time.sleep(poll_seconds)
            try:
                parent_alive = psutil.pid_exists(parent_pid)
            except Exception:
                continue  # transient error reading the process table -- retry next tick
            if parent_alive:
                continue
            # Parent is gone. BUG FOUND while testing this exact function:
            # the print() below used to carry an emoji, which throws
            # UnicodeEncodeError under some console code pages (cp1252,
            # notably) -- an uncaught exception in a thread just prints a
            # traceback and lets the thread die, silently disabling this
            # entire mechanism in precisely the situation it exists for.
            # Nothing from here down may be allowed to stop os._exit() from
            # running, so each step is caught independently rather than
            # trusting one try/except around all of it not to itself have a
            # gap.
            try:
                print(f"[watchdog] parent process {parent_pid} is gone "
                      f"-- shutting down to release the GPU", flush=True)
            except Exception:
                pass
            if on_exit:
                try:
                    on_exit()
                except Exception:
                    pass
            os._exit(1)

    threading.Thread(target=_watch, name="parent-watchdog", daemon=True).start()


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