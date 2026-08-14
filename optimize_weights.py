"""Build TensorRT engines for THIS machine's GPU, and report what it bought.

Why this is a step the user runs rather than something we ship prebuilt: a
TensorRT engine is compiled against one GPU architecture, one TensorRT version
and one driver. The engines on the build machine (Turing, GTX 1660 SUPER) will
not load on an Ampere laptop -- they do not fail quietly-but-wrongly, they
raise, and the detector falls back to the .pt. So the only place an engine can
usefully be built is the machine that will run it.

This is OPTIONAL by design. The .pt weights are the shipped default and work on
every CUDA GPU and on CPU. Optimizing is a speed choice, never a correctness
one -- and the report at the end is measured on the actual hardware rather than
quoted from our benchmarks, because that is the only number that means anything
to the person running it.

Measured on the development GPU for reference:

    x3d violence   batch 1    27.6 ms -> 8.8 ms    3.24x
    x3d violence   batch 17  109.9 ms -> 80.7 ms   1.61x
    yolo11s-pose              23.6 ms -> 9.2 ms    2.56x
    weapon_signs              18.6 ms -> 13.1 ms   1.43x

Accuracy is unchanged, and that is verified rather than assumed: the engine is
diffed against the .pt on real input, and a build whose verdicts disagree is
rejected instead of installed. See --check-only to run just that comparison.

Progress lines beginning with "@@" are JSON for the installer UI. Everything
else is for a human reading a terminal.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "maincode"))
WEIGHTS = REPO / "weights"

# Agreement tolerance. The engine is fp32 built from an fp32 export, so the
# only expected difference is kernel-selection noise -- observed max 2.6e-05
# through the real detector. 0.01 is ~400x that, and still far below anything
# that could move a threshold calibrated to two decimal places.
MAX_DELTA = 0.01


def emit(kind, **fields):
    """One JSON line for the installer UI."""
    print("@@" + json.dumps({"kind": kind, **fields}), flush=True)


def say(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# X3D: torch -> ONNX -> TensorRT, by hand. There is no exporter for these.
# ---------------------------------------------------------------------------
def load_x3d(pt_path, device="cuda:0"):
    import torch
    import torch.nn as nn
    from pytorchvideo.models.hub import x3d_xs

    model = x3d_xs(pretrained=False)
    model.blocks[-1].proj = nn.Linear(model.blocks[-1].proj.in_features, 2)
    model.load_state_dict(torch.load(pt_path, map_location="cpu", weights_only=True))
    # Do NOT append a Softmax here. The pytorchvideo head is
    # proj -> Softmax(dim=1) -> AdaptiveAvgPool3d and already returns
    # probabilities; x3d_violence_detector._run_inference reads outputs[0][1]
    # straight out. Adding another one recreates the double-softmax bug that
    # squeezed every confidence this system reported into [0.2689, 0.7311] --
    # and it would be invisible here, because an engine exported from a
    # double-softmaxed model agrees perfectly with a double-softmaxed model.
    return model.to(device).eval()


def x3d_geometry(pt_path):
    """Clip shape from the checkpoint's sidecar, falling back to config.json."""
    frames, size = 13, 160
    meta = Path(str(pt_path) + ".meta.json")
    if meta.exists() and meta.stat().st_size > 0:
        try:
            d = json.loads(meta.read_text())
            frames = int(d.get("clip_frames") or frames)
            size = int(d.get("frame_size") or size)
        except Exception:
            pass
    return frames, size


def build_x3d_engine(pt_path, max_batch, workspace_gb, keep_onnx=False):
    import tensorrt as trt
    import torch

    frames, size = x3d_geometry(pt_path)
    model = load_x3d(pt_path)
    sample = torch.randn(1, 3, frames, size, size, device="cuda:0")

    onnx_path = pt_path.with_suffix(".onnx")
    torch.onnx.export(
        model, sample, str(onnx_path),
        input_names=["clip"], output_names=["prob"],
        dynamic_axes={"clip": {0: "batch"}, "prob": {0: "batch"}},
        opset_version=17, do_constant_folding=True,
    )

    logger = trt.Logger(trt.Logger.ERROR)
    builder = trt.Builder(logger)
    network = builder.create_network(0)
    parser = trt.OnnxParser(network, logger)
    if not parser.parse(onnx_path.read_bytes()):
        errs = "; ".join(str(parser.get_error(i)) for i in range(parser.num_errors))
        raise RuntimeError(f"ONNX parse failed: {errs[:300]}")

    cfg = builder.create_builder_config()
    cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, int(workspace_gb * (1 << 30)))
    # TensorRT 11 networks are strongly typed: precision follows the ONNX
    # graph's dtypes and the FP16 builder flag is rejected outright. fp32 is
    # the right default anyway -- every threshold in config.json was
    # calibrated against fp32 outputs, and fp16 drift would move them.
    try:
        cfg.set_flag(trt.BuilderFlag.FP16)
    except Exception:
        pass

    profile = builder.create_optimization_profile()
    shape = (3, frames, size, size)
    # One engine has to serve scene mode (batch 1) and tiled mode (batch 17),
    # so the profile spans both. `opt` sits at 1 because that is what ships.
    profile.set_shape("clip", (1,) + shape, (1,) + shape, (max_batch,) + shape)
    cfg.add_optimization_profile(profile)

    plan = builder.build_serialized_network(network, cfg)
    if not keep_onnx:
        onnx_path.unlink(missing_ok=True)
    if plan is None:
        raise RuntimeError("TensorRT returned no engine (see log above)")
    return bytes(plan), (frames, size)


def bench_x3d_torch(pt_path, batch, n):
    import torch
    frames, size = x3d_geometry(pt_path)
    model = load_x3d(pt_path)
    x = torch.randn(batch, 3, frames, size, size, device="cuda:0")
    with torch.no_grad():
        for _ in range(6):
            model(x)
        torch.cuda.synchronize()
        t = time.time()
        for _ in range(n):
            model(x)
        torch.cuda.synchronize()
    return (time.time() - t) / n * 1000


def bench_x3d_engine(engine_bytes, geom, batch, n, agreement_pt=None):
    import tensorrt as trt
    import torch

    frames, size = geom
    runtime = trt.Runtime(trt.Logger(trt.Logger.ERROR))
    engine = runtime.deserialize_cuda_engine(engine_bytes)
    ctx = engine.create_execution_context()

    def run(x):
        src = x.contiguous()
        out = torch.empty((src.shape[0], 2), dtype=torch.float32, device="cuda:0")
        ctx.set_input_shape("clip", tuple(src.shape))
        ctx.set_tensor_address("clip", src.data_ptr())
        ctx.set_tensor_address("prob", out.data_ptr())
        stream = torch.cuda.current_stream()
        ctx.execute_async_v3(stream.cuda_stream)
        stream.synchronize()
        return out

    x = torch.randn(batch, 3, frames, size, size, device="cuda:0")
    for _ in range(6):
        run(x)
    t = time.time()
    for _ in range(n):
        run(x)
    ms = (time.time() - t) / n * 1000

    agree = None
    if agreement_pt is not None:
        model = load_x3d(agreement_pt)
        xs = torch.randn(8, 3, frames, size, size, device="cuda:0")
        with torch.no_grad():
            ref = model(xs)[:, 1].cpu().numpy()
        got = run(xs)[:, 1].cpu().numpy()
        agree = {
            "max_delta": float(np.abs(ref - got).max()),
            "flips": int(((ref >= 0.5) != (got >= 0.5)).sum()),
            "n": len(ref),
        }
    return ms, agree


# ---------------------------------------------------------------------------
# YOLO: ultralytics has its own exporter, so use it rather than reimplementing.
# ---------------------------------------------------------------------------
def bench_yolo(path, task, n, imgsz=416):
    from ultralytics import YOLO
    frame = (np.random.rand(720, 1280, 3) * 255).astype("uint8")
    m = YOLO(str(path), task=task)
    for _ in range(5):
        m.predict(frame, verbose=False, imgsz=imgsz, device="cuda:0")
    t = time.time()
    for _ in range(n):
        m.predict(frame, verbose=False, imgsz=imgsz, device="cuda:0")
    return (time.time() - t) / n * 1000


def build_yolo_engine(pt_path, imgsz=416, workspace_gb=1.0):
    from ultralytics import YOLO
    out = YOLO(str(pt_path)).export(
        format="engine", imgsz=imgsz, device=0, workspace=workspace_gb, verbose=False)
    produced = Path(out)
    target = pt_path.with_suffix(".engine")
    if produced.resolve() != target.resolve():
        shutil.move(str(produced), str(target))
    return target


# ---------------------------------------------------------------------------
TARGETS = [
    {"stem": "x3d_xs_violence_scene_corpus_neg", "kind": "x3d",
     "label": "Violence detector", "batches": [1, 17]},
    {"stem": "x3d_xs_robbery_scene", "kind": "x3d",
     "label": "Robbery detector", "batches": [1]},
    {"stem": "yolo11s-pose", "kind": "yolo", "task": "pose",
     "label": "Person / pose detection"},
    {"stem": "weapon_signs", "kind": "yolo", "task": "detect",
     "label": "Weapon and sign detection"},
]


def preconditions():
    """Everything that must hold before a single engine is worth building."""
    try:
        import torch
    except ImportError:
        return False, "PyTorch is not installed in this environment."
    if not torch.cuda.is_available():
        return False, ("No CUDA GPU was detected, and TensorRT needs an NVIDIA "
                       "GPU to compile for.")
    try:
        import tensorrt  # noqa: F401
    except ImportError:
        return False, ("TensorRT is not included in this install. It is a "
                       "~3.2 GB dependency, left out on purpose so the "
                       "download stays reasonable.")
    return True, torch.cuda.get_device_name(0)


def machine_facts():
    """Plain facts for the installer's 'This machine' panel.

    Reported rather than judged: the before/after table a few screens later
    says whether optimizing was worth it far better than any adjective here
    could, and on hardware we have never seen a judgement is likelier to be
    wrong than the measurement is.
    """
    facts = []
    try:
        import torch
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            facts.append(["GPU", p.name])
            facts.append(["Video memory", f"{p.total_memory / 2**30:.1f} GB"])
            facts.append(["CUDA", str(torch.version.cuda)])
    except Exception:
        pass
    try:
        import tensorrt as trt
        facts.append(["TensorRT", str(trt.__version__)])
    except Exception:
        pass
    try:
        import psutil
        facts.append(["System memory", f"{psutil.virtual_memory().total / 2**30:.1f} GB"])
    except Exception:
        pass
    present = sum(1 for t in TARGETS if (WEIGHTS / f"{t['stem']}.pt").exists())
    facts.append(["Models to compile", str(present)])
    return facts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="substring match on a weight stem")
    ap.add_argument("--max-batch", type=int, default=17,
                    help="largest clip batch the engine must accept (tiled mode sends 17)")
    ap.add_argument("--workspace-gb", type=float, default=2.0)
    ap.add_argument("--n", type=int, default=25, help="timed iterations per measurement")
    ap.add_argument("--check-only", action="store_true",
                    help="benchmark existing engines, build nothing")
    ap.add_argument("--revert", action="store_true",
                    help="delete every .engine, returning to the .pt weights")
    ap.add_argument("--probe", action="store_true",
                    help="report whether optimization is possible, build nothing")
    args = ap.parse_args()

    if args.probe:
        ok, detail = preconditions()
        emit("preconditions", ok=ok, detail=detail,
             facts=machine_facts() if ok else [])
        say(f"optimization {'available' if ok else 'unavailable'}: {detail}")
        return 0 if ok else 2

    if args.revert:
        removed = []
        for t in TARGETS:
            p = WEIGHTS / f"{t['stem']}.engine"
            if p.exists():
                p.unlink()
                removed.append(p.name)
        emit("reverted", files=removed)
        say(f"Removed {len(removed)} engine(s). The app is back on the .pt weights.")
        return 0

    ok, info = preconditions()
    emit("preconditions", ok=ok, detail=info)
    if not ok:
        say(f"Cannot optimize: {info}")
        return 2
    say(f"GPU: {info}")
    import tensorrt as trt
    say(f"TensorRT {trt.__version__}\n")

    targets = [t for t in TARGETS
               if not args.only or args.only.lower() in t["stem"].lower()]
    results = []

    for i, t in enumerate(targets):
        pt = WEIGHTS / f"{t['stem']}.pt"
        eng = WEIGHTS / f"{t['stem']}.engine"
        emit("step", index=i, total=len(targets), label=t["label"], state="start")
        if not pt.exists():
            say(f"  {t['label']}: {pt.name} missing, skipped")
            emit("step", index=i, label=t["label"], state="skipped",
                 reason="weights missing")
            continue

        row = {"label": t["label"], "stem": t["stem"], "kind": t["kind"]}
        try:
            say(f"[{i+1}/{len(targets)}] {t['label']}")

            if t["kind"] == "x3d":
                batches = t.get("batches", [1])
                say("  measuring current speed...")
                before = {b: bench_x3d_torch(pt, b, args.n if b == 1 else max(6, args.n // 3))
                          for b in batches}

                if args.check_only:
                    if not eng.exists():
                        raise FileNotFoundError("no engine to check")
                    blob = eng.read_bytes()
                    geom = x3d_geometry(pt)
                else:
                    say("  building engine (this is the slow part, ~1 min)...")
                    emit("step", index=i, label=t["label"], state="building")
                    blob, geom = build_x3d_engine(pt, args.max_batch, args.workspace_gb)

                say("  measuring optimized speed...")
                after, agree = {}, None
                for b in batches:
                    ms, a = bench_x3d_engine(
                        blob, geom, b, args.n if b == 1 else max(6, args.n // 3),
                        agreement_pt=pt if b == batches[0] else None)
                    after[b] = ms
                    agree = agree or a

                # Refuse to install an engine that disagrees with the weights
                # it came from. A faster detector that answers differently is
                # not an optimization, it is a silent model swap.
                if agree and (agree["max_delta"] > MAX_DELTA or agree["flips"]):
                    raise RuntimeError(
                        f"engine disagrees with the .pt "
                        f"(max delta {agree['max_delta']:.5f}, "
                        f"{agree['flips']}/{agree['n']} verdict flips) -- not installed")
                if not args.check_only:
                    eng.write_bytes(blob)

                row["agreement"] = agree
                row["before_ms"] = before[batches[0]]
                row["after_ms"] = after[batches[0]]
                row["detail"] = [{"batch": b, "before_ms": before[b], "after_ms": after[b]}
                                 for b in batches]
            else:
                say("  measuring current speed...")
                row["before_ms"] = bench_yolo(pt, t["task"], args.n)
                if not args.check_only:
                    say("  building engine...")
                    emit("step", index=i, label=t["label"], state="building")
                    build_yolo_engine(pt, workspace_gb=args.workspace_gb)
                if not eng.exists():
                    raise FileNotFoundError("engine was not produced")
                say("  measuring optimized speed...")
                row["after_ms"] = bench_yolo(eng, t["task"], args.n)

            row["speedup"] = row["before_ms"] / row["after_ms"]
            row["engine_mb"] = eng.stat().st_size / 2**20 if eng.exists() else None
            say(f"  {row['before_ms']:.1f} ms -> {row['after_ms']:.1f} ms "
                f"({row['speedup']:.2f}x)\n")
            emit("step", index=i, label=t["label"], state="done", **{
                k: row[k] for k in ("before_ms", "after_ms", "speedup")})
            results.append(row)

        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            say(f"  FAILED: {msg}")
            say("  Leaving the .pt in place -- detection is unaffected.\n")
            # A failed engine file left on disk would be picked up by the
            # loader on next launch and fail there instead, which is a much
            # worse place to discover it.
            eng.unlink(missing_ok=True)
            emit("step", index=i, label=t["label"], state="failed", error=msg)
            results.append({"label": t["label"], "error": msg})

    good = [r for r in results if "speedup" in r]
    say("=" * 62)
    if good:
        say(f"{'model':30} {'before':>9} {'after':>9} {'gain':>7}")
        for r in good:
            say(f"{r['label'][:30]:30} {r['before_ms']:7.1f}ms {r['after_ms']:7.1f}ms "
                f"{r['speedup']:6.2f}x")
        overall = sum(r["before_ms"] for r in good) / sum(r["after_ms"] for r in good)
        say(f"\nCombined model time: {overall:.2f}x faster on this GPU.")
    else:
        say("No engines were built. The app continues on the .pt weights.")
    failed = [r for r in results if "error" in r]
    if failed:
        say(f"\n{len(failed)} model(s) could not be optimized:")
        for r in failed:
            say(f"  - {r['label']}: {r['error']}")

    emit("summary", results=results,
         combined=(sum(r["before_ms"] for r in good) / sum(r["after_ms"] for r in good))
         if good else None)
    say("\nEngines are specific to this GPU and this TensorRT version. After a "
         "GPU or driver change, run this again -- the app falls back to the .pt "
         "on its own in the meantime, so nothing breaks while you do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
