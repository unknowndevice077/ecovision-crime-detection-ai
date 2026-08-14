"""Measure the real floor: what hardware does this actually need?

Two numbers from preflight.py needed correcting before they could be turned
into a minimum spec:

  1. `torch.cuda.max_memory_reserved()` counts only PyTorch's own allocator.
     The CUDA context, the cuDNN/cuBLAS kernels and the driver's own working
     set sit OUTSIDE it and are typically several hundred MB. Quoting 204 MB
     as the VRAM requirement would understate it badly. This reads the
     per-process figure from nvidia-smi instead, which includes all of it.

  2. Throughput was measured on a GPU that was simultaneously running a
     training job, so it was a floor rather than a representative number.

It also times CPU-only inference, which is what decides whether a discrete GPU
is a hard requirement or merely strongly recommended.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent   # this file lives in tools/
sys.path.insert(0, str(REPO / "maincode"))


def smi_process_mb():
    """VRAM this PID holds, per the driver -- context included."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15).stdout
        me = os.getpid()
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) == me:
                return float(parts[1])
    except Exception:
        pass
    return None


def bench(device, frames, wdir):
    import numpy as np
    from ultralytics import YOLO
    from x3d_violence_detector import SceneViolenceDetector

    t0 = time.time()
    pose = YOLO(str(wdir / "yolo11s-pose.pt"))
    objd = YOLO(str(wdir / "weapon_signs.pt"))
    viol = SceneViolenceDetector(
        model_path=str(wdir / "x3d_xs_violence_scene_corpus_neg.pt"), device=device)
    robb = SceneViolenceDetector(
        model_path=str(wdir / "x3d_xs_robbery_scene.pt"),
        device=device, threshold=0.7, consecutive=3)
    load = time.time() - t0

    frame = (np.random.rand(720, 1280, 3) * 255).astype("uint8")
    for i in range(8):
        pose.predict(frame, verbose=False, device=device)
        viol.update(frame, i)

    vram = smi_process_mb() if device != "cpu" else None
    t0 = time.time()
    for i in range(frames):
        pose.track(frame, persist=True, verbose=False, device=device)
        objd.predict(frame, verbose=False, device=device)
        viol.update(frame, 100 + i)
        robb.update(frame, 100 + i)
    dt = time.time() - t0

    rss = None
    try:
        import psutil
        rss = psutil.Process().memory_info().rss / 2**30
    except ImportError:
        pass
    return {"load_s": load, "fps": frames / dt, "ms": dt / frames * 1000,
            "vram_mb": vram, "rss_gb": rss}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--cpu-frames", type=int, default=12,
                    help="CPU is slow; fewer frames still establishes the order")
    args = ap.parse_args()
    wdir = REPO / "weights"

    import torch
    print(f"torch {torch.__version__}   CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        print(f"GPU: {p.name}  {p.total_memory/2**30:.1f} GB\n")

    results = {}
    if torch.cuda.is_available():
        print(f"--- GPU, {args.frames} frames ---")
        r = results["gpu"] = bench("cuda:0", args.frames, wdir)
        print(f"  load {r['load_s']:.1f}s   {r['fps']:.1f} FPS "
              f"({r['ms']:.0f} ms/frame)")
        print(f"  VRAM held by this process (driver figure, context included): "
              f"{r['vram_mb'] if r['vram_mb'] else '?'} MB")
        print(f"  process RAM: {r['rss_gb']:.2f} GB\n" if r["rss_gb"] else "")

    print(f"--- CPU, {args.cpu_frames} frames ---")
    r = results["cpu"] = bench("cpu", args.cpu_frames, wdir)
    print(f"  load {r['load_s']:.1f}s   {r['fps']:.2f} FPS "
          f"({r['ms']:.0f} ms/frame)")
    if r["rss_gb"]:
        print(f"  process RAM: {r['rss_gb']:.2f} GB")

    if "gpu" in results:
        print(f"\nGPU is {results['gpu']['fps']/results['cpu']['fps']:.0f}x "
              f"faster than CPU on this machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
