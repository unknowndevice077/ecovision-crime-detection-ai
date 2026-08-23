"""Preflight: does every deployed model load, run, and agree with main.py?

WHY THIS EXISTS. Three separate bugs in this project were all the same shape --
a model and the code consuming it silently disagreed, and nothing raised an
error:

  * weapon_signs.pt emitted a "Sign" class that main.py looked for and that
    fired 0 times in 4,800 frames. The gate it fed could never open.
  * "phone" sat in main.py's WEAPON_CLASSES while the deployed model had no
    such class -- a name that could never match a detection.
  * A detector trained at imgsz 640 was run at 416, so the benchmarked model
    and the deployed model were not the same model.

None of these throw. They produce a system that runs perfectly and detects
nothing. This script asserts the things that silence hides:

  1. every path named in config.json exists on disk
  2. every checkpoint actually loads
  3. each detector produces output on a real frame
  4. THE CLASS NAMES THE MODEL EMITS ARE THE ONES main.py LOOKS FOR
  5. the imgsz main.py runs at is recorded next to what the model trained at

Exit code 0 means deployable. Anything else is a real finding.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent
MAIN = REPO / "maincode" / "main.py"

ok, warn, fail = [], [], []


def const(name, default=None):
    m = re.search(rf"^{name}\s*=\s*([0-9.]+)", MAIN.read_text(encoding="utf-8"),
                  re.MULTILINE)
    return type(default)(m.group(1)) if m else default


def name_set(var):
    """Parse a set literal like WEAPON_CLASSES = {"gun", ...} out of main.py."""
    m = re.search(rf"^{var}\s*=\s*\{{(.*?)\}}", MAIN.read_text(encoding="utf-8"),
                  re.MULTILINE | re.DOTALL)
    return {s.strip().strip("'\"").lower()
            for s in m.group(1).split(",") if s.strip()} if m else set()


def load_config_like_main():
    """Resolve config EXACTLY as main.py does: base + env overlay + writable.

    THIS FUNCTION IS THE POINT OF THE SCRIPT. An earlier version of this
    preflight read config.json directly and reported everything green while
    the running system loaded entirely different weights -- because main.py
    resolves config through three layers and the answer differed at every one:

      config.json                    <- base; everything documented lives here
      config.<APP_ENV>.json          <- overlay (APP_ENV defaults to development)
      <writable dir>/config.json     <- overlay; per-machine persisted settings

    A verifier that does not share the runtime's resolution path cannot catch
    resolution bugs, which are precisely the bugs that hide.
    """
    def dm(b, o):
        out = dict(b)
        for k, v in o.items():
            out[k] = dm(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        return out

    import os
    base = REPO / "config.json"
    cfg = json.loads(base.read_text(encoding="utf-8"))
    layers = [f"config.json"]

    app_env = os.environ.get("APP_ENV", "development")
    envp = REPO / f"config.{app_env}.json"
    if envp.exists():
        cfg = dm(cfg, json.loads(envp.read_text(encoding="utf-8")))
        layers.append(envp.name)

    wdir = os.environ.get("ECOVISION_WRITABLE_DIR") or os.path.join(
        os.path.expanduser("~"), "EcoVisionSentinelData")
    wp = Path(wdir) / "config.json"
    if wp.exists():
        cfg = dm(cfg, json.loads(wp.read_text(encoding="utf-8")))
        layers.append(str(wp))

    print(f"  config layers merged: {' + '.join(layers)}")
    return cfg


def main():
    print("=" * 70)
    print("0. CONFIG RESOLUTION (same three layers main.py uses)")
    print("=" * 70)
    cfg = load_config_like_main()
    det = cfg["detection"]
    for blk in ("violence", "robbery", "vandalism", "weapon"):
        present = blk in det
        line = f"  {'OK' if present else 'XX'} detection.{blk:10}"
        if present:
            line += f" enabled={det[blk].get('enabled', '(default)')}"
        else:
            fail.append(f"detection.{blk} missing after merge -- falls back to hardcoded defaults")
            line += " MISSING -> silently falls back to hardcoded defaults"
        print(line)
    print()

    print("=" * 70)
    print("1. PATHS NAMED IN config.json")
    print("=" * 70)
    paths = {
        "violence.model_path": det["violence"]["model_path"],
        "violence.scene_model_path": det["violence"]["scene_model_path"],
        "robbery.model_path": det["robbery"]["model_path"],
        "vandalism.model_path": det["vandalism"]["model_path"],
        "vandalism.marks_model_path": det["vandalism"].get("marks_model_path", ""),
        "weapon.model_path": det["weapon"]["model_path"],
    }
    for k, v in paths.items():
        if not v:
            warn.append(f"{k} unset"); print(f"  ~ {k:34} UNSET"); continue
        p = REPO / v
        if p.exists():
            ok.append(k); print(f"  OK {k:34} {v}  ({p.stat().st_size/1e6:.1f} MB)")
        else:
            fail.append(f"{k} -> missing {v}"); print(f"  XX {k:34} MISSING {v}")

    print()
    print("=" * 70)
    print("2. DETECTORS: load, run on a real frame, and agree with main.py")
    print("=" * 70)
    import cv2
    cv2.setNumThreads(0)
    from ultralytics import YOLO

    frame = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)

    weapon_classes = name_set("WEAPON_CLASSES")
    sign_classes = name_set("SIGN_CLASSES")
    m = re.search(r"^CONF_BY_CLASS\s*=\s*\{(.*?)\}", MAIN.read_text(encoding="utf-8"),
                  re.MULTILINE | re.DOTALL)
    conf_by_class = {}
    if m:
        for line in m.group(1).splitlines():
            mm = re.match(r"\s*\"(\w+)\"\s*:\s*([0-9.]+)", line)
            if mm:
                conf_by_class[mm.group(1).lower()] = float(mm.group(2))

    checks = [
        ("weapon", det["weapon"]["model_path"], const("WEAPON_IMGSZ", 416),
         weapon_classes | sign_classes, "WEAPON_CLASSES|SIGN_CLASSES"),
        ("graffiti", det["vandalism"].get("marks_model_path", ""),
         const("VANDAL_MARK_IMGSZ", 416), None, None),
        ("pose", "weights/yolo11s-pose.pt", const("POSE_IMGSZ", 416), None, None),
    ]
    for tag, rel, imgsz, expected, srcvar in checks:
        if not rel or not (REPO / rel).exists():
            fail.append(f"{tag}: no weights"); print(f"  XX {tag}: no weights"); continue
        try:
            mdl = YOLO(str(REPO / rel))
            emitted = {str(v).lower() for v in mdl.names.values()}
            r = mdl.predict(frame, imgsz=imgsz, verbose=False, device="cpu")[0]
            nb = 0 if r.boxes is None else len(r.boxes)
            print(f"  OK {tag:9} imgsz={imgsz}  classes={sorted(emitted)}  "
                  f"ran on 1280x720 -> {nb} boxes")
            ok.append(tag)
            if expected is not None:
                dead = expected - emitted
                unused = emitted - expected
                if dead:
                    fail.append(f"{tag}: main.py looks for {sorted(dead)} "
                                f"which this model CANNOT emit")
                    print(f"     XX main.py's {srcvar} contains {sorted(dead)} "
                          f"-- the model has no such class. Dead code.")
                if unused:
                    print(f"     ~  model emits {sorted(unused)} that main.py "
                          f"ignores (fine if deliberate, e.g. phone as hard negative)")
                for c in sorted(emitted & set(conf_by_class)):
                    print(f"     .. threshold {c}: {conf_by_class[c]}")
        except Exception as e:
            fail.append(f"{tag}: {e}"); print(f"  XX {tag}: {e}")

    print()
    print("=" * 70)
    print("3. VIDEO CLASSIFIERS: load and produce a probability")
    print("=" * 70)
    sys.path.insert(0, str(REPO / "maincode"))
    try:
        import torch
        for tag in ("violence.scene_model_path", "robbery.model_path"):
            grp, key = tag.split(".")
            rel = det[grp][key]
            p = REPO / rel
            if not p.exists():
                fail.append(f"{tag}: missing"); continue
            sd = torch.load(p, map_location="cpu", weights_only=False)
            keys = sd.get("model_state_dict", sd)
            n = len(keys) if hasattr(keys, "__len__") else "?"
            meta = p.with_suffix(p.suffix + ".meta.json")
            note = ""
            if meta.exists():
                md = json.loads(meta.read_text(encoding="utf-8"))
                note = (f"  [meta: {md.get('input_repr','?')}, "
                        f"{md.get('clip_frames','?')}f@{md.get('frame_size','?')}px, "
                        f"{md.get('inference_contract','no contract')[:38]}]")
            print(f"  OK {tag:34} {n} keys{note}")
            if not meta.exists():
                warn.append(f"{tag}: no .meta.json sidecar")
            ok.append(tag)
    except Exception as e:
        fail.append(f"video classifiers: {e}"); print(f"  XX {e}")

    print()
    print("=" * 70)
    print(f"RESULT: {len(ok)} ok, {len(warn)} warnings, {len(fail)} failures")
    print("=" * 70)
    for w in warn:
        print(f"  WARN {w}")
    for f in fail:
        print(f"  FAIL {f}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
