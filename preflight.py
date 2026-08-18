"""Will EcoVision actually run on this machine?

Run this FIRST on any new device. It loads the real models, runs real
inference, and reports measured memory and throughput -- rather than guessing
from spec sheets.

The immediate question it was written for: an RTX 3050 laptop with 8 GB of
system RAM. The development machine is a GTX 1660 SUPER (6 GB VRAM) with more
system memory, so neither the VRAM headroom nor the RAM headroom carries over
and both are worth measuring instead of assumed.

WHAT IT CHECKS, in order of how likely it is to be the thing that breaks:

  1. System RAM.   8 GB is the tight one. PyTorch + CUDA context + four models
                   + frame buffers all live in it, and Windows itself wants
                   2-3 GB. This is a more likely failure point than VRAM.
  2. VRAM.         Laptop RTX 3050s ship in 4 GB and 6 GB variants. Inference
                   needs far less than training, but four models are resident
                   at once.
  3. Throughput.   The pipeline must keep up with the camera. Below ~1.0x
                   real-time it falls behind and the buffer grows without
                   bound.

Nothing here writes to the database or the network. Safe to run anywhere.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "maincode"))

_CONFIG = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
_VIOLENCE_SCENE_WEIGHT = Path(_CONFIG["detection"]["violence"]["scene_model_path"]).name
_ROBBERY_WEIGHT = Path(_CONFIG["detection"]["robbery"]["model_path"]).name

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def ok(msg):
    print(f"  {GREEN}OK{RESET}    {msg}")


def warn(msg):
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def bad(msg):
    print(f"  {RED}FAIL{RESET}  {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=120,
                    help="frames of real inference to time")
    ap.add_argument("--skip-models", action="store_true",
                    help="environment checks only, no model loading")
    args = ap.parse_args()

    problems, warnings = [], []
    print("=" * 66)
    print("EcoVision preflight")
    print("=" * 66)

    # ---- 1. Python ------------------------------------------------------
    print("\n[1] Python")
    v = sys.version_info
    print(f"        {sys.version.split()[0]}  ({sys.executable})")
    if v < (3, 9):
        bad("Python 3.9+ required")
        problems.append("python version")
    else:
        ok("version supported")

    # ---- 2. System RAM --------------------------------------------------
    print("\n[2] System RAM  (the likely constraint on an 8 GB laptop)")
    try:
        import psutil
        vm = psutil.virtual_memory()
        total = vm.total / 2**30
        avail = vm.available / 2**30
        print(f"        {total:.1f} GB total, {avail:.1f} GB available now")
        if total < 7.0:
            bad(f"{total:.1f} GB total -- below the 8 GB this was sized for")
            problems.append("system RAM")
        elif avail < 3.0:
            warn(f"only {avail:.1f} GB free right now; close other apps before running")
            warnings.append("low free RAM")
        else:
            ok("enough headroom")
    except ImportError:
        warn("psutil not installed -- cannot measure RAM (pip install psutil)")
        warnings.append("psutil missing")

    # ---- 3. GPU ---------------------------------------------------------
    print("\n[3] GPU")
    try:
        import torch
    except ImportError:
        bad("PyTorch not installed -- run setup.bat")
        print("\nCannot continue without PyTorch.")
        return 1
    print(f"        torch {torch.__version__}")
    if not torch.cuda.is_available():
        warn("no CUDA device. The system RUNS on CPU but far below real time; "
             "usable for testing the UI, not for live detection.")
        warnings.append("CPU only")
        device = "cpu"
    else:
        name = torch.cuda.get_device_name(0)
        vram = torch.cuda.get_device_properties(0).total_memory / 2**30
        print(f"        {name}  ({vram:.1f} GB VRAM, CUDA {torch.version.cuda})")
        device = "cuda:0"
        if vram < 3.5:
            bad(f"{vram:.1f} GB VRAM is below what four resident models need")
            problems.append("VRAM")
        elif vram < 5.0:
            ok(f"{vram:.1f} GB -- workable; see the measured figure below")
        else:
            ok("ample")

    # ---- 4. Weights -----------------------------------------------------
    print("\n[4] Model weights")
    # Read from config.json rather than hardcoded -- these two are exactly the
    # checkpoint filenames that change whenever a retrained model is deployed
    # (see config.json's own _scene_model_path_rollback history). A hardcoded
    # name here silently drifts from what's actually deployed and reports a
    # false MISSING for the correct, present file on every single install.
    required = {
        "yolo11s-pose.pt": "person + pose detection",
        "weapon_signs.pt": "weapon / sign detection",
        _VIOLENCE_SCENE_WEIGHT: "violence (scene mode)",
        _ROBBERY_WEIGHT: "robbery",
    }
    wdir = REPO / "weights"
    for f, why in required.items():
        p = wdir / f
        alt = p.with_suffix(".engine")
        if p.exists():
            ok(f"{f:38} {p.stat().st_size/2**20:6.1f} MB   {why}")
        elif alt.exists():
            ok(f"{alt.name:38} {alt.stat().st_size/2**20:6.1f} MB   {why}")
        else:
            bad(f"{f:38} MISSING            {why}")
            problems.append(f"weights/{f}")

    # ---- 5. Database ----------------------------------------------------
    print("\n[5] Database")
    if os.environ.get("DATABASE_URL"):
        warn("DATABASE_URL is set -- the app will use Postgres, not the local "
             "SQLite file. Unset it for a standalone install.")
        warnings.append("DATABASE_URL set")
    else:
        ok("DATABASE_URL unset -> local SQLite (correct for a standalone install)")
    # Two layouts: the source tree keeps it in app/, while electron-builder
    # copies it to backend/ (see package.json extraResources). Checking only
    # app/ would fail on every packaged install, which is the one place this
    # check actually matters.
    schema = next((p for p in (REPO / "app" / "schema_sqlite.sql",
                               REPO / "backend" / "schema_sqlite.sql")
                   if p.exists()), None)
    if schema:
        ok(f"schema present ({schema.stat().st_size/1024:.0f} KB, {schema.parent.name}/)")
    else:
        bad("schema_sqlite.sql missing from app/ and backend/ -- "
            "the database cannot be created")
        problems.append("sqlite schema")

    # ---- 6. Real inference ---------------------------------------------
    if args.skip_models or problems:
        print("\n[6] Inference benchmark skipped"
              + (" (--skip-models)" if args.skip_models else " -- fix the failures above first"))
    else:
        print(f"\n[6] Inference benchmark ({args.frames} frames, real models)")
        import numpy as np
        try:
            if device.startswith("cuda"):
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats()
            base = (torch.cuda.memory_allocated() / 2**20) if device.startswith("cuda") else 0

            from ultralytics import YOLO
            from x3d_violence_detector import SceneViolenceDetector
            t0 = time.time()
            pose = YOLO(str(wdir / "yolo11s-pose.pt"))
            objd = YOLO(str(wdir / "weapon_signs.pt"))
            viol = SceneViolenceDetector(
                model_path=str(wdir / _VIOLENCE_SCENE_WEIGHT),
                device=device)
            robb = SceneViolenceDetector(
                model_path=str(wdir / _ROBBERY_WEIGHT),
                device=device, threshold=0.7, consecutive=3)
            print(f"        4 models loaded in {time.time()-t0:.1f}s")

            frame = (np.random.rand(720, 1280, 3) * 255).astype("uint8")
            for i in range(10):          # warm up; first passes allocate
                pose.predict(frame, verbose=False)
                viol.update(frame, i)

            t0 = time.time()
            for i in range(args.frames):
                pose.track(frame, persist=True, verbose=False)
                objd.predict(frame, verbose=False)
                viol.update(frame, 100 + i)
                robb.update(frame, 100 + i)
            dt = time.time() - t0
            fps = args.frames / dt
            print(f"        {fps:.1f} FPS  ({dt/args.frames*1000:.0f} ms/frame)")

            if device.startswith("cuda"):
                peak = torch.cuda.max_memory_allocated() / 2**20
                resv = torch.cuda.max_memory_reserved() / 2**20
                print(f"        VRAM: {peak:.0f} MB allocated peak, "
                      f"{resv:.0f} MB reserved")
                head = (torch.cuda.get_device_properties(0).total_memory / 2**20) - resv
                if head < 400:
                    warn(f"only {head:.0f} MB VRAM headroom -- may OOM under load")
                    warnings.append("tight VRAM")
                else:
                    ok(f"{head:.0f} MB VRAM headroom")

            try:
                import psutil
                rss = psutil.Process().memory_info().rss / 2**30
                print(f"        process RAM: {rss:.2f} GB")
                if rss > 4.0:
                    warn(f"{rss:.2f} GB resident is a lot for an 8 GB machine")
                    warnings.append("high RAM use")
            except ImportError:
                pass

            # A live camera delivers 30 fps but the detector rate-limits its
            # own heavy work, so the bar is not 30 -- it is "keeps up".
            if fps < 8:
                warn(f"{fps:.1f} FPS is likely below real-time for a 30 fps "
                     f"camera; consider raising check_interval in config.json")
                warnings.append("low throughput")
            else:
                ok("throughput sufficient")
        except Exception as e:
            bad(f"inference failed: {type(e).__name__}: {e}")
            problems.append("inference")

    # ---- verdict --------------------------------------------------------
    print("\n" + "=" * 66)
    if problems:
        print(f"{RED}NOT READY{RESET} -- {len(problems)} blocking problem(s):")
        for p in problems:
            print(f"    - {p}")
        return 1
    if warnings:
        print(f"{YELLOW}READY, with {len(warnings)} caveat(s){RESET}:")
        for w in warnings:
            print(f"    - {w}")
        return 0
    print(f"{GREEN}READY{RESET} -- all checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
