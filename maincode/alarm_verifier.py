"""
EcoVision -- second-stage alarm verification with a vision-language model.

WHY
---
The X3D detectors look at one short clip and give one number. That number
cannot tell a robbery from an ordinary hand-off in a busy market, or a fight
from rough play, so every threshold trades missed events against false
alarms. A video-language model can look at the same two seconds and answer
the actual question ("is someone being attacked?") -- too slowly to run on
every frame, fast enough to run once per alarm.

So the detector runs at a LOOSER setting (more candidate alarms, more caught
events) and every candidate is checked here before it becomes an alert.
Measured in the training repo (experiments/crop_recheck: cascade_eval.py,
vlm_verify.py) on 200 held-out fights / 133 robbery events spliced into street
footage + 5 h of held-out street cameras, cut-off fitted on half the source
videos and scored on the other half:

    violence  deployed 0.45 x 2               88.0% caught  1.4 false alarms/hr
              0.25 x 1 + this verifier         90.5%         0.4
              + re-check every 7 s             94.0%         0.4   (VerifiedGate)
    robbery   deployed 0.85 x 3                 8.3%         1.4   (25% of fights
                                                                   labelled robbery)
              fight model's whole-frame        59.4%         0.6   (20.5%)
              candidates + two questions
              (robbery_from_fight, below)

HOW
---
Qwen3-VL-2B-Instruct gets 8 frames spread over the 2 s before the alarm,
longest side 448 px, plus one yes/no question. Two backends (config
"backend"):

  gpu   transformers, fp16, ~4.2 GB VRAM, the frames as one video. ~7 s per
        check, but the detectors share the GPU and drop to ~2 FPS meanwhile.
  cpu   llama.cpp's llama-server (started and stopped by this module), 4-bit
        GGUF, ~2.1 GB RAM, no VRAM, the frames as 8 images. ~15 s per check;
        the detectors keep ~85% of their frame rate. Same accuracy on the 5 h
        held-out test (AUC 0.992 vs 0.996 on fights). Its scores are on a
        different scale, so each task has its own "cut_cpu".
  auto  gpu when enough GPU memory is free at load time, else cpu.

The score is log P("Yes") - log P("No") for its first answer
token: one forward pass, no text generation. An alarm passes if the score is
at or above the task's cut-off. For a passed alarm a short reason is then
generated (a few dozen tokens) so the operator sees why.

The scoring here must match vlm_verify.py / vlm_verify_llama.py exactly --
same frame sampling, resize, prompt and metadata -- because the cut-offs
were fitted on their scores.

SAFETY
------
Everything here is optional. If transformers or llama-server is missing, the
weights are missing, loading fails, or the GPU runs out of memory, `active` is False and
main.py falls back to the detectors' original, stricter settings -- the
system as it was measured before this module existed. Verification runs on
one background thread; the detection loop never waits for it.
"""
import atexit
import base64
import json
import os
import queue
import socket
import subprocess
import threading
import time
import urllib.request
from collections import deque

import cv2
import numpy as np

QUESTIONS = {
    # ARMED THREAT (added 2026-10-14). Asked about a square crop around the
    # person the alert names (WEAPON_CROP), not the whole frame: a street-
    # camera weapon is a few pixels wide at 448. Must match
    # experiments/weapon_alerts/vlm_weapon.py's QUESTION word for word.
    "weapon": "This is a close-up from a security camera, centred on one person. "
              "Is this person holding a gun or a knife? Answer Yes or No.",
    "violence": "This is a security camera clip. Is anyone in it physically fighting, punching, kicking, "
                "or attacking another person? Answer Yes or No.",
    # The robbery test events include shoplifting, which the robbery model is
    # trained to flag, so stealing is part of the question.
    "robbery": "This is a security camera clip. Is someone being robbed, held up, or having their "
               "belongings grabbed or forcibly taken, or is someone stealing? Answer Yes or No.",
}
# ROBBERY FROM THE FIGHT DETECTOR. Street robbery is mostly a struggle -- a
# grab, a shove, a snatch from a motorbike -- which the robbery model rarely
# flags and the fight detector's WHOLE-FRAME score does (before person-crop
# confirmation, which vetoes the small or undetected people of most street
# footage). Those candidates are asked the robbery question. Measured
# 2026-10-07 on 133 robbery events + 5 h of held-out cameras, cross-validated:
# robbery-labelled alerts 51.1% -> 69.2% (street 8% -> 44%), robbery false
# alarms 2.0 -> 0.8/hr, at about the same number of checks (67 vs 62 per
# camera-hour) -- so while the verifier runs, the robbery model is not run at
# all (main.py); it stays the fallback.
QUESTIONS["robbery_from_fight"] = QUESTIONS["robbery"]
REASON_PROMPT = {
    "violence": "In one short sentence, describe what the people in this security camera clip are doing.",
    "robbery": "In one short sentence, describe what the people in this security camera clip are doing.",
}
REASON_PROMPT["robbery_from_fight"] = REASON_PROMPT["robbery"]
# ...but the yes/no robbery question also says "yes" to most FIGHTS (86% of
# 200 held-out fights got a robbery label through this path; for street
# robbery it scored fights HIGHER than robberies). So a task with "mc_cut"
# also needs this question to put robbery (B) above fight (A):
# log P(B) - log P(A) >= mc_cut, asked only after the yes/no check passed.
# It is the best robbery-vs-fight separator measured (AUC 0.877 vs 0.667
# yes/no). Must match vlm_verify.py's MC_QUESTION word for word.
MC_QUESTION = ("This is a security camera clip. Which best describes what happens in it?\n"
               "A) People fighting, or one person attacking another, without taking anything\n"
               "B) A robbery, snatch or theft: someone taking a bag, phone, money or goods from a person, "
               "a shop or a vehicle\n"
               "C) Nothing criminal\n"
               "Answer with the letter only.")


# The weapon check's crop: the person box padded by this share of its size on
# every side (a held weapon sticks out past the body), squared, from the
# FULL-RESOLUTION frame, longest side SIDE. vlm_weapon.crop_box/frames.
WEAPON_PAD = 0.5


def weapon_crop_box(person_box, w, h, pad=WEAPON_PAD):
    x1, y1, x2, y2 = person_box
    s = max(x2 - x1, y2 - y1) * (1 + 2 * pad)
    s = min(s, w, h)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    x1 = int(min(max(0, cx - s / 2), w - s))
    y1 = int(min(max(0, cy - s / 2), h - s))
    return x1, y1, x1 + int(s), y1 + int(s)


def resize_crop(c, side):
    k = side / max(c.shape[:2])
    return cv2.resize(c, (max(32, int(c.shape[1] * k) // 32 * 32), max(32, int(c.shape[0] * k) // 32 * 32)),
                      interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)


class FrameHistory:
    """Clean (un-annotated) frames with timestamps, downscaled once on the way
    in, covering the last few seconds -- what a verification job samples.

    The clip ring buffer in main.py holds ANNOTATED frames (boxes, HUD); the
    verifier must see what the camera saw, as in the experiments."""

    def __init__(self, seconds: float = 4.0, side: int = 448):
        self.seconds = seconds
        self.side = side
        self._buf = deque()
        self._lock = threading.Lock()

    def push(self, frame: np.ndarray, ts: float = None):
        """side=None keeps the frame itself (no copy: main.py's clean_frame
        is a fresh array every loop) -- the weapon check crops from it."""
        ts = time.perf_counter() if ts is None else ts
        if self.side is None:
            small = frame
        else:
            h, w = frame.shape[:2]
            k = self.side / max(h, w)
            small = cv2.resize(frame, (int(w * k) // 32 * 32, int(h * k) // 32 * 32), interpolation=cv2.INTER_AREA)
        with self._lock:
            self._buf.append((ts, small))
            while self._buf and ts - self._buf[0][0] > self.seconds:
                self._buf.popleft()

    def clear(self):
        """After a reconnect or camera switch: no clip may mix the two."""
        with self._lock:
            self._buf.clear()

    def clip(self, end_ts: float = None, n: int = 8, span: float = 2.0):
        """n frames evenly over [end_ts - span, end_ts], RGB, as one array --
        the same sampling vlm_verify.frames_before() does on a video file."""
        with self._lock:
            buf = list(self._buf)
        if not buf:
            return None
        end_ts = buf[-1][0] if end_ts is None else end_ts
        times = np.array([t for t, _ in buf])
        out = []
        for s in np.linspace(end_ts - span, end_ts, n):
            i = int(np.argmin(np.abs(times - s)))
            out.append(cv2.cvtColor(buf[i][1], cv2.COLOR_BGR2RGB))
        return np.stack(out)

    def crop_clip(self, person_box, end_ts: float = None, n: int = 8, span: float = 2.0, side: int = 448):
        """The weapon check's clip: the same times as clip(), each frame cut
        to weapon_crop_box around person_box (fixed at the alert, as the
        experiment did) and resized like vlm_weapon.frames()."""
        with self._lock:
            buf = list(self._buf)
        if not buf:
            return None
        end_ts = buf[-1][0] if end_ts is None else end_ts
        times = np.array([t for t, _ in buf])
        h, w = buf[-1][1].shape[:2]
        x1, y1, x2, y2 = weapon_crop_box(person_box, w, h)
        out = []
        for s in np.linspace(end_ts - span, end_ts, n):
            fr = buf[int(np.argmin(np.abs(times - s)))][1]
            out.append(cv2.cvtColor(resize_crop(fr[y1:y2, x1:x2], side), cv2.COLOR_BGR2RGB))
        return np.stack(out)


def _kill_with_parent(proc):
    """Windows: put the child in a job object that kills it when this process
    exits for any reason, so a crashed AI core never leaves a 2 GB
    llama-server behind. Elsewhere the atexit hook covers it."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.Basic.LimitFlags = 0x2000                       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
        h = k32.OpenProcess(0x0101, False, proc.pid)          # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        ok = k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(h))
        k32.CloseHandle(wintypes.HANDLE(h))
        if not ok:
            raise OSError(ctypes.get_last_error())
        return job                                            # held open for this process's lifetime
    except Exception as e:
        print(f"⚠️  [VERIFIER] could not tie llama-server to this process ({e}); atexit will stop it")
        return None


class LlamaBackend:
    """llama-server on the CPU, started by us on a free localhost port. One
    request per check: the 8 frames as JPEG images + the question, 1 token,
    top-20 logprobs -- exactly vlm_verify_llama.py in the training repo."""

    YES = {"Yes", " Yes", "yes", " yes", "YES"}
    NO = {"No", " No", "no", " no", "NO"}

    def __init__(self, cfg: dict, project_root: str):
        def path(key, default):
            v = cfg.get(key) or default
            return v if os.path.isabs(v) else os.path.join(project_root, v)
        self.server = path("server", "tools/llamacpp/llama-server.exe")
        self.model = path("model", "weights/qwen3_vl_2b_gguf/Qwen3VL-2B-Instruct-Q4_K_M.gguf")
        self.mmproj = path("mmproj", "weights/qwen3_vl_2b_gguf/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf")
        # Half the logical cores: measured with 6 of 12 the detectors kept
        # ~85% of their frame rate.
        self.threads = int(cfg.get("threads") or max(2, (os.cpu_count() or 4) // 2))
        self.log_path = os.path.join(project_root, "logs", "verifier_llama_server.log")
        self.proc = self._job = self.url = None

    def start(self, timeout: float = 180.0):
        for f in (self.server, self.model, self.mmproj):
            if not os.path.isfile(f):
                raise FileNotFoundError(f"CPU verifier file not found: {f}")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cmd = [self.server, "-m", self.model, "--mmproj", self.mmproj, "--host", "127.0.0.1",
               "--port", str(port), "-t", str(self.threads), "-c", "3072", "-np", "1", "-ngl", "0",
               "--no-mmproj-offload", "--no-webui", "--offline",
               # No prompt cache: every check's frames differ, so nothing in
               # it is ever reused, and its 8 GB default grew the server from
               # 2 GB to 10 GB of committed memory in an afternoon here.
               "--cache-ram", "0"]
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        # No console window; below-normal priority -- the detectors come
        # first, a check can wait.
        flags = 0x08000000 | 0x00004000 if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     creationflags=flags)
        self._job = _kill_with_parent(self.proc)
        atexit.register(self.stop)
        base = f"http://127.0.0.1:{port}"
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with code {self.proc.returncode} (see {self.log_path})")
            try:
                with urllib.request.urlopen(base + "/health", timeout=2) as r:
                    if r.status == 200:
                        self.url = base + "/v1/chat/completions"
                        return
            except Exception:
                pass
            time.sleep(1.0)
        self.stop()
        raise TimeoutError(f"llama-server not ready after {timeout:.0f} s (see {self.log_path})")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()

    def _post(self, question: str, clip: np.ndarray, max_tokens: int, logprobs: bool) -> dict:
        content = []
        for f in clip:
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(f, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()}})
        content.append({"type": "text", "text": question})
        # Scores are computed from scratch (cache_prompt off): the
        # multiple-choice question follows the yes/no one on the same frames,
        # and reusing that request's cached image tokens moved
        # log P(B) - log P(A) by up to 0.65 against the from-scratch scores
        # the cuts were fitted on (verify_mc_cpu_shipped.py; 0.000 with it
        # off). The description has no cut, so it may reuse them.
        body = {"messages": [{"role": "user", "content": content}], "max_tokens": max_tokens, "temperature": 0,
                "cache_prompt": not logprobs}
        if logprobs:
            body.update(logprobs=True, top_logprobs=20)
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())["choices"][0]

    def score(self, question: str, clip: np.ndarray) -> float:
        top = self._post(question, clip, 1, True)["logprobs"]["content"][0]["top_logprobs"]
        floor = min(t["logprob"] for t in top) - 1.0   # a side absent from the top 20 is at most this likely
        y = [t["logprob"] for t in top if t["token"] in self.YES] or [floor]
        n = [t["logprob"] for t in top if t["token"] in self.NO] or [floor]
        return float(np.logaddexp.reduce(y) - np.logaddexp.reduce(n))

    def score_mc(self, clip: np.ndarray) -> float:
        """log P(B) - log P(A) for MC_QUESTION's first answer token."""
        top = self._post(MC_QUESTION, clip, 1, True)["logprobs"]["content"][0]["top_logprobs"]
        floor = min(t["logprob"] for t in top) - 1.0
        a = [t["logprob"] for t in top if t["token"].strip() == "A"] or [floor]
        b = [t["logprob"] for t in top if t["token"].strip() == "B"] or [floor]
        return float(np.logaddexp.reduce(b) - np.logaddexp.reduce(a))

    def reason(self, question: str, clip: np.ndarray, max_new_tokens: int = 40) -> str:
        text = self._post(question, clip, max_new_tokens, False)["message"]["content"] or ""
        return " ".join(text.split())[:300]


class AlarmVerifier:
    def __init__(self, cfg: dict, project_root: str, device: str = "cuda"):
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled", False))
        self.device = device
        self.project_root = project_root
        self.backend = str(self.cfg.get("backend", "gpu")).lower()     # gpu | cpu | auto
        self.active_backend = None
        self._llama = None
        self.model_path = self.cfg.get("model_path", "weights/qwen3_vl_2b")
        if not os.path.isabs(self.model_path):
            self.model_path = os.path.join(project_root, self.model_path)
        self.frames = int(self.cfg.get("frames", 8))
        self.span = float(self.cfg.get("span_seconds", 2.0))
        self.side = int(self.cfg.get("side", 448))
        self.generate_reason = bool(self.cfg.get("reason", True))
        self.max_queue = int(self.cfg.get("max_pending", 4))
        # "enabled": false keeps a task's measured settings in the config
        # while it is not used
        self.tasks = {k: v for k, v in (self.cfg.get("tasks") or {}).items() if v.get("enabled", True)}
        self.active = False          # True once the model is loaded and working
        self.status = "disabled" if not self.enabled else "loading"
        self.history = FrameHistory(seconds=self.span + 2.0, side=self.side)
        # Full-resolution frames for the weapon check's person crop; only
        # kept when that task is on (a 1080p camera: ~30 frames, ~180 MB).
        self.full_history = FrameHistory(seconds=self.span + 0.5, side=None) if "weapon" in self.tasks else None
        self._jobs = queue.Queue()
        self._model = self._proc = None
        self._prompt = {}
        self._yes = self._no = None
        self.stats = {"checked": 0, "passed": 0, "rejected": 0, "dropped": 0, "errors": 0, "last_seconds": None}
        if self.enabled:
            threading.Thread(target=self._worker, name="alarm-verifier", daemon=True).start()
        else:
            print("🔎 Alarm verifier: disabled in config (detection.verifier.enabled) -- detectors use their standalone settings")

    # ---- public -------------------------------------------------------------
    def stage1(self, task: str):
        """(threshold, consecutive) the detector should use for candidates, or
        None when this task is not verified."""
        t = self.tasks.get(task)
        if not t:
            return None
        return float(t["stage1_threshold"]), int(t["stage1_consecutive"])

    def submit(self, task: str, on_done, end_ts: float = None, context=None, clip=None) -> bool:
        """Queue one candidate alarm. on_done(passed, info, context) runs on
        the verifier thread; passed is True/False, or None when the check
        itself failed (the caller should then fall back to the detector's
        strict setting for this candidate). Returns False (and does not call
        on_done) when the verifier cannot take it -- same fallback applies."""
        if not self.active or task not in self.tasks:
            return False
        if self._jobs.qsize() >= self.max_queue:
            self.stats["dropped"] += 1
            print(f"⚠️  [VERIFIER] queue full ({self.max_queue}) -- {task} candidate dropped")
            return False
        if clip is None:
            clip = self.history.clip(end_ts, n=self.frames, span=self.span)
        if clip is None:
            return False
        self._jobs.put((task, clip, on_done, context, time.perf_counter()))
        return True

    def describe(self) -> dict:
        model = (os.path.basename(self._llama.model) if self.active_backend == "cpu"
                 else os.path.basename(self.model_path))
        return {"status": self.status, "active": self.active, "backend": self.active_backend or self.backend,
                "model": model, "tasks": sorted(self.tasks), **self.stats}

    def cut(self, task: str, key: str = "cut") -> float:
        """The task's cut-off ("cut" or "mc_cut") for the backend in use --
        the two score on different scales. KeyError (-> strict fallback) if
        none was fitted."""
        t = self.tasks[task]
        return float(t[key + "_cpu"] if self.active_backend == "cpu" else t[key])

    def can_run(self, task: str) -> bool:
        """Loaded, and every cut-off this task needs was fitted for the
        backend in use. main.py only hands a detector's job to a task that
        can run (robbery_from_fight replaces the robbery model only then)."""
        if not self.active or task not in self.tasks:
            return False
        try:
            self.cut(task)
            if "mc_cut" in self.tasks[task]:
                self.cut(task, "mc_cut")
        except KeyError:
            return False
        return True

    # ---- worker -------------------------------------------------------------
    def _load(self):
        # Load AFTER the detectors have loaded and run a while, and only into
        # memory that is actually free then. Loading first could take the
        # memory the detector needs and crash IT -- the verifier is optional,
        # the detector is not. On a 4 GB laptop GPU the gpu backend stays off
        # (auto: the cpu one is used) and the detectors are untouched.
        time.sleep(float(self.cfg.get("load_delay_seconds", 30)))
        backend = self.backend
        if backend in ("gpu", "auto"):
            why = self._gpu_unavailable() or self._memory_short("gpu")
            if why and backend == "gpu":
                raise RuntimeError(why)
            if why:
                print(f"🔎 Alarm verifier: {why} -- using the CPU backend")
            backend = "cpu" if why else "gpu"
        if backend == "cpu":
            why = self._memory_short("cpu")
            if why:
                raise MemoryError(why)
        t0 = time.time()
        if backend == "cpu":
            self._llama = LlamaBackend(self.cfg.get("cpu", {}), self.project_root)
            self._llama.start()
            self.active_backend = "cpu"
            print(f"🔎 Alarm verifier ready (CPU, llama.cpp, {self._llama.threads} threads): "
                  f"{os.path.basename(self._llama.model)} in {time.time() - t0:.1f} s "
                  f"(tasks: {', '.join(sorted(self.tasks))})")
            return
        self._load_gpu()
        self.active_backend = "gpu"
        print(f"🔎 Alarm verifier ready (GPU): {os.path.basename(self.model_path)} in {time.time() - t0:.1f} s "
              f"(tasks: {', '.join(sorted(self.tasks))})")

    # Measured 2026-10-07 on top of a running AI core: the gpu backend
    # (device_map straight to the GPU) peaks at +1.3 GB of physical RAM but
    # +5.3 GB of COMMITTED memory (+4.0 GB kept; little of it resident); the
    # cpu backend's llama-server holds ~2.1 GB, resident and committed. The
    # commit limit is what a too-big load breaks -- it crashed the detection
    # loop once that day -- so both are checked, with ~0.5 GB to spare.
    MEMORY_NEEDS_GB = {"gpu": {"physical_gb": 1.8, "commit_gb": 5.8},
                       "cpu": {"physical_gb": 2.6, "commit_gb": 2.6}}

    @staticmethod
    def _memory_free():
        """(physical RAM available, commit headroom) in GB; either None if
        it cannot be read here."""
        phys = commit = None
        try:
            import psutil
            phys = psutil.virtual_memory().available / 2**30
        except ImportError:
            pass
        if os.name == "nt":
            try:
                import ctypes

                class MemStatus(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + \
                               [(n, ctypes.c_ulonglong) for n in ("total_phys", "avail_phys", "total_page",
                                                                  "avail_page", "total_virt", "avail_virt",
                                                                  "avail_ext")]
                st = MemStatus()
                st.dwLength = ctypes.sizeof(MemStatus)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                    commit = st.avail_page / 2**30      # commit limit minus commit charge
                    phys = phys if phys is not None else st.avail_phys / 2**30
            except Exception:
                pass
        return phys, commit

    def _memory_short(self, backend: str):
        """None if this backend fits in the memory free now, else why not.
        config ram_guard.<backend>.{physical_gb, commit_gb} overrides."""
        need = dict(self.MEMORY_NEEDS_GB[backend], **((self.cfg.get("ram_guard") or {}).get(backend) or {}))
        phys, commit = self._memory_free()
        if phys is not None and phys < need["physical_gb"]:
            return (f"only {phys:.1f} GB of RAM available; the {backend} backend needs "
                    f"{need['physical_gb']:.1f} GB")
        if commit is not None and commit < need["commit_gb"]:
            return (f"only {commit:.1f} GB of commit headroom (RAM + page file) left; the {backend} "
                    f"backend needs {need['commit_gb']:.1f} GB")
        return None

    def _gpu_unavailable(self):
        """None if the gpu backend can load now, else why not."""
        if not str(self.device).startswith("cuda"):
            return "no CUDA device"
        try:
            import torch
            import transformers  # noqa: F401
        except ImportError as e:
            return f"transformers not installed ({e})"
        if not os.path.isdir(self.model_path):
            return f"verifier weights not found: {self.model_path}"
        need_gb = float(self.cfg.get("min_free_vram_gb", 4.3))
        free, total = torch.cuda.mem_get_info()
        if free / 2**30 < need_gb:
            return (f"only {free / 2**30:.1f} GB of {total / 2**30:.1f} GB GPU memory free "
                    f"after the detectors loaded; the GPU backend needs {need_gb:.1f} GB")
        return None

    def _load_gpu(self):
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
        if str(self.device).startswith("cuda"):
            # device_map streams each weight straight to the GPU (needs
            # accelerate) instead of materialising the model in RAM first.
            try:
                model = Qwen3VLForConditionalGeneration.from_pretrained(
                    self.model_path, dtype=torch.float16, device_map={"": self.device})
            except ImportError:
                model = Qwen3VLForConditionalGeneration.from_pretrained(
                    self.model_path, dtype=torch.float16).to(self.device)
        else:
            model = Qwen3VLForConditionalGeneration.from_pretrained(self.model_path, dtype=torch.float16)
        self._model = model.eval()
        self._proc = AutoProcessor.from_pretrained(self.model_path)
        tok = self._proc.tokenizer
        self._yes = [tok.encode(w, add_special_tokens=False)[0] for w in ("Yes", " Yes", "yes")]
        self._no = [tok.encode(w, add_special_tokens=False)[0] for w in ("No", " No", "no")]
        for task, q in QUESTIONS.items():
            msgs = [{"role": "user", "content": [{"type": "video"}, {"type": "text", "text": q}]}]
            self._prompt[task] = self._proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        for task, q in REASON_PROMPT.items():
            msgs = [{"role": "user", "content": [{"type": "video"}, {"type": "text", "text": q}]}]
            self._prompt["reason:" + task] = self._proc.apply_chat_template(msgs, tokenize=False,
                                                                            add_generation_prompt=True)
        self._letter = {L: [tok.encode(w, add_special_tokens=False)[0] for w in (L, " " + L)] for L in "AB"}
        msgs = [{"role": "user", "content": [{"type": "video"}, {"type": "text", "text": MC_QUESTION}]}]
        self._prompt["mc"] = self._proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

    def _inputs(self, prompt, clip):
        n = len(clip)
        meta = [{"fps": n / self.span, "total_num_frames": n, "frames_indices": list(range(n))}]
        return self._proc(text=[prompt], videos=[clip], return_tensors="pt",
                          video_metadata=meta, do_sample_frames=False).to(self.device)

    def score(self, task: str, clip: np.ndarray) -> float:
        if self.active_backend == "cpu":
            return self._llama.score(QUESTIONS[task], clip)
        import torch
        with torch.no_grad():
            logits = self._model(**self._inputs(self._prompt[task], clip)).logits[0, -1].float()
            lp = torch.log_softmax(logits, -1)
            return float(torch.logsumexp(lp[self._yes], 0) - torch.logsumexp(lp[self._no], 0))

    def score_mc(self, clip: np.ndarray) -> float:
        """Robbery above fight: log P(B) - log P(A) for MC_QUESTION."""
        if self.active_backend == "cpu":
            return self._llama.score_mc(clip)
        import torch
        with torch.no_grad():
            logits = self._model(**self._inputs(self._prompt["mc"], clip)).logits[0, -1].float()
            lp = torch.log_softmax(logits, -1)
            return float(torch.logsumexp(lp[self._letter["B"]], 0) - torch.logsumexp(lp[self._letter["A"]], 0))

    def reason(self, task: str, clip: np.ndarray, max_new_tokens: int = 40) -> str:
        if self.active_backend == "cpu":
            return self._llama.reason(REASON_PROMPT[task], clip, max_new_tokens)
        import torch
        with torch.no_grad():
            inp = self._inputs(self._prompt["reason:" + task], clip)
            out = self._model.generate(**inp, max_new_tokens=max_new_tokens, do_sample=False)
        text = self._proc.batch_decode(out[:, inp["input_ids"].shape[1]:], skip_special_tokens=True)[0]
        return " ".join(text.split())[:300]

    def _disable(self, why: str):
        self.active = False
        self.status = f"off: {why}"
        print(f"⚠️  [VERIFIER] {why} -- detectors fall back to their standalone settings")
        if self._llama is not None:
            self._llama.stop()
        try:
            import torch
            self._model = None
            torch.cuda.empty_cache()
        except Exception:
            pass

    def _worker(self):
        try:
            self._load()
            self.active, self.status = True, "ready"
        except Exception as e:
            self._disable(f"could not load ({type(e).__name__}: {str(e)[:160]})")
            return
        try:
            from torch.cuda import OutOfMemoryError
        except ImportError:
            OutOfMemoryError = MemoryError
        while True:
            task, clip, on_done, context, queued_at = self._jobs.get()
            if not self.active:
                try:
                    on_done(None, {"error": self.status}, context)
                except Exception:
                    pass
                continue
            t0 = time.perf_counter()
            try:
                s = self.score(task, clip)
                cut = self.cut(task)
                passed = s >= cut
                info = {"model": self.describe()["model"], "backend": self.active_backend,
                        "score": round(s, 3), "cut": cut,
                        "passed": passed, "waited_seconds": round(t0 - queued_at, 2)}
                if passed and "mc_cut" in self.tasks[task]:
                    m, mcut = self.score_mc(clip), self.cut(task, "mc_cut")
                    passed = m >= mcut
                    info.update(mc_score=round(m, 3), mc_cut=mcut, passed=passed)
                    print(f"🔎 [VERIFIER] {task}: robbery-vs-fight {m:+.2f} vs {mcut:+.2f}")
                if passed and self.generate_reason:
                    try:
                        info["reason"] = self.reason(task, clip)
                    except Exception as e:  # a reason is nice to have, never required
                        print(f"⚠️  [VERIFIER] reason generation failed: {e}")
                info["seconds"] = round(time.perf_counter() - t0, 2)
                self.stats["checked"] += 1
                self.stats["passed" if passed else "rejected"] += 1
                self.stats["last_seconds"] = info["seconds"]
                print(f"🔎 [VERIFIER] {task}: score {s:+.2f} vs cut {cut:+.2f} -> "
                      f"{'PASS' if passed else 'reject'} ({info['seconds']:.1f} s)")
            except OutOfMemoryError:
                self.stats["errors"] += 1
                self._disable("GPU out of memory")
                passed, info = None, {"error": "GPU out of memory"}
            except Exception as e:
                self.stats["errors"] += 1
                print(f"⚠️  [VERIFIER] check failed: {type(e).__name__}: {e}")
                passed, info = None, {"error": f"{type(e).__name__}: {str(e)[:120]}"}
            try:
                on_done(passed, info, context)
            except Exception as e:
                print(f"⚠️  [VERIFIER] callback failed: {e}")


class VerifiedGate:
    """Turns one detector's candidate alarms plus the verifier's verdicts into
    the single on/off signal main.py's episode logic already consumes.

    Per candidate episode (the loose state turning on, until it turns off):
      - the rising edge is sent to the verifier;
      - with `recheck` set, the episode is checked again every `recheck`
        seconds while the candidate stays on, unverified, with no check
        pending. A fight that starts while a candidate is ALREADY on has no
        rising edge of its own: 8 of the 15 fights that got no check at
        0.25 x1 were that. Measured (5 h held-out cameras, cross-validated):
        recheck 7 s 90.5% -> 94.0% of fights, 15 s 92.5%, false alarms
        unchanged at 0.4/hr. A check takes ~7 s (GPU) / ~15 s (CPU), so
        with "no check pending" 7 s is the GPU pace and the CPU's own;
      - a PASS turns the signal on, and holds it at least `min_hold` seconds
        so a verdict that arrives after a short candidate still opens an
        incident;
      - a REJECT keeps it off (until the candidate ends, or the next re-check);
      - no verdict possible (verifier off, queue full, check failed) falls
        back to the detector's STRICT state for that episode -- exactly the
        behaviour before verification existed.
    With no candidate state on the detector, or the verifier inactive, the
    strict state passes straight through.
    """

    def __init__(self, task: str, verifier, min_hold: float = 3.0, recheck: float = None):
        self.task = task
        self.verifier = verifier
        self.min_hold = min_hold
        self.recheck = float(recheck) if recheck else None
        self._lock = threading.Lock()
        self._verdicts = []
        self.epoch = 0
        self._seq = 0
        self._clear()

    def _clear(self):
        self.pending = 0
        self.verified = False
        self.verified_at = 0.0
        self.fallback = False
        self.info = None
        self.trigger_frame = None
        self.trigger_conf = 0.0
        self.last_submit = None
        self._frames = {}           # check seq -> (clean frame, conf) at submit

    def _done(self, passed, info, context):
        with self._lock:
            self._verdicts.append((passed, info, context))

    def step(self, det, strict_hit: bool, now: float, frame=None, conf: float = 0.0) -> bool:
        v = self.verifier
        if det is None or getattr(det, "candidate_threshold", None) is None or not v.active:
            if self.verified or self.pending:
                self._clear()
                self.epoch += 1
            return bool(strict_hit)

        due = (self.recheck is not None and det.candidate_confirmed and not self.verified
               and not self.fallback and not self.pending and self.last_submit is not None
               and now - self.last_submit >= self.recheck)
        if det.candidate_started or due:
            self._seq += 1
            if v.submit(self.task, self._done, end_ts=now, context=(self.epoch, self._seq)):
                self.pending += 1
                self.last_submit = now
                self._frames[self._seq] = (None if frame is None else frame.copy(), float(conf))
            else:
                self.fallback = True

        with self._lock:
            verdicts, self._verdicts = self._verdicts, []
        for passed, info, (epoch, seq) in verdicts:
            if epoch != self.epoch:
                continue
            self.pending = max(0, self.pending - 1)
            snap, snap_conf = self._frames.pop(seq, (None, 0.0))
            if passed:
                if not self.verified:
                    self.verified, self.verified_at, self.info = True, now, info
                    self.trigger_frame, self.trigger_conf = snap, snap_conf
            elif passed is None:
                self.fallback = True

        if not det.candidate_confirmed and not self.pending:
            if not (self.verified and now - self.verified_at < self.min_hold):
                if self.verified or self.fallback or self.last_submit is not None:
                    self._clear()
                    self.epoch += 1
        return self.verified or (self.fallback and bool(strict_hit))

    def alert_extras(self) -> dict:
        """What a verified alert carries: the clean frame from the moment of
        the check that passed (the alert itself is raised seconds later), the
        detector's confidence then, and the verifier's verdict."""
        if not self.verified:
            return {}
        return {"snap_src": self.trigger_frame, "conf": self.trigger_conf, "verifier": self.info}
