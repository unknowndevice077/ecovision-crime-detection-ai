"""Animated explainers: how a convolution moves, and what the detector emits.

Static diagrams cannot show the two things that matter most about these
networks -- that a kernel SLIDES, and that the temporal kernel reaches ACROSS
frames. Both are motion, so both get GIFs.

The third output is the one a panel actually asks for: a real clip with the
deployed model's own confidence drawn on it frame by frame, so "the detector
raised an alert here" stops being a claim and becomes something visible.

    .venv\\Scripts\\python.exe tools\\make_explainer_gifs.py

Outputs land in docs/media/. Nothing here invents data: the frames come from a
real dataset clip and the confidences come from the deployed checkpoint.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import cv2

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "maincode"))
OUT = REPO / "docs" / "media"
OUT.mkdir(parents=True, exist_ok=True)

DEFAULT_CLIP = (r"D:\EcoVisionImagesTraining\To_Be_Trained2\CCTV_Fights_Extracted"
                r"\Fight\fight_0171_seg1.mp4")

# Print-safe on white, and distinguishable in greyscale.
INK, MUTED = (24, 20, 20), (120, 128, 140)
BLUE, ORANGE, GREEN, RED = (214, 120, 42), (52, 104, 235), (122, 175, 27), (38, 43, 194)  # BGR


def read_frames(path, n=40, size=None):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while len(frames) < n:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(cv2.resize(f, size) if size else f)
    cap.release()
    if not frames:
        raise SystemExit(f"No frames read from {path}")
    return frames


def save_gif(frames_bgr, path, fps=8, colors=64):
    """Write a GIF without adding a dependency: Pillow is already present via
    matplotlib, and cv2 cannot write GIF.

    Quantised to a small palette on purpose. A full-colour GIF of 90 CCTV
    frames came to 9.5 MB, which is too heavy to embed in a document and over
    the limit for most previewers; 64 colours costs nothing visible on footage
    this noisy and cuts it by roughly 5x.
    """
    from PIL import Image
    imgs = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)).convert(
        "P", palette=Image.ADAPTIVE, colors=colors) for f in frames_bgr]
    imgs[0].save(path, save_all=True, append_images=imgs[1:],
                 duration=int(1000 / fps), loop=0, optimize=True)
    print(f"  {path.name}  ({path.stat().st_size / 1024:.0f} KB, {len(imgs)} frames)")


def label(img, text, org, color=INK, scale=0.5, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


# ---------------------------------------------------------------------------
# 1. A 2D kernel sliding over one real frame
# ---------------------------------------------------------------------------
def gif_conv2d(src_frame):
    GRID, CELL, PAD = 10, 34, 16
    gray = cv2.cvtColor(cv2.resize(src_frame, (GRID * CELL, GRID * CELL)), cv2.COLOR_BGR2GRAY)
    # Edge response is what an early conv layer actually responds to, so use a
    # real Sobel rather than random numbers.
    resp = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    resp = np.abs(resp)
    resp = (resp / (resp.max() + 1e-6) * 255).astype(np.uint8)

    outN = GRID - 2
    W = PAD * 3 + GRID * CELL + outN * CELL
    H = PAD * 2 + GRID * CELL + 46

    frames = []
    for step in range(outN * outN):
        r, c = divmod(step, outN)
        canvas = np.full((H, W, 3), 255, np.uint8)

        # left: the input, with the kernel window drawn on it
        left = cv2.cvtColor(cv2.resize(gray, (GRID * CELL, GRID * CELL),
                                       interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
        for i in range(GRID + 1):
            cv2.line(left, (i * CELL, 0), (i * CELL, GRID * CELL), (225, 225, 225), 1)
            cv2.line(left, (0, i * CELL), (GRID * CELL, i * CELL), (225, 225, 225), 1)
        cv2.rectangle(left, (c * CELL, r * CELL), ((c + 3) * CELL, (r + 3) * CELL), ORANGE, 3)
        canvas[PAD + 30:PAD + 30 + GRID * CELL, PAD:PAD + GRID * CELL] = left
        label(canvas, "input frame (one channel)", (PAD, PAD + 20), MUTED, 0.45)

        # right: the feature map, filled in only as far as the kernel has gone
        ox = PAD * 2 + GRID * CELL
        out = np.full((outN * CELL, outN * CELL, 3), 245, np.uint8)
        small = cv2.resize(resp, (outN, outN), interpolation=cv2.INTER_AREA)
        for k in range(step + 1):
            rr, cc = divmod(k, outN)
            v = int(small[rr, cc])
            out[rr * CELL:(rr + 1) * CELL, cc * CELL:(cc + 1) * CELL] = (v, v, v)
        for i in range(outN + 1):
            cv2.line(out, (i * CELL, 0), (i * CELL, outN * CELL), (215, 215, 215), 1)
            cv2.line(out, (0, i * CELL), (outN * CELL, i * CELL), (215, 215, 215), 1)
        cv2.rectangle(out, (c * CELL, r * CELL), ((c + 1) * CELL, (r + 1) * CELL), ORANGE, 3)
        canvas[PAD + 30:PAD + 30 + outN * CELL, ox:ox + outN * CELL] = out
        label(canvas, "feature map (what the filter found)", (ox, PAD + 20), MUTED, 0.45)

        label(canvas, "3x3 filter slides over every position -> one number each",
              (PAD, H - 12), INK, 0.46)
        frames.append(canvas)

    # Every 2nd window: the sliding motion is unambiguous at 32 steps and the
    # file is half the size.
    save_gif(frames[::2], OUT / "conv2d_slide.gif", fps=7, colors=32)


# ---------------------------------------------------------------------------
# 2. The temporal kernel reaching across frames
# ---------------------------------------------------------------------------
def gif_conv3d(frames_src):
    TH, TW, N = 92, 124, 9
    strip = [cv2.resize(f, (TW, TH)) for f in frames_src[:N]]
    PAD, GAP = 18, 10
    W = PAD * 2 + N * TW + (N - 1) * GAP
    H = PAD * 2 + TH + 78

    frames = []
    for center in range(1, N - 1):
        canvas = np.full((H, W, 3), 255, np.uint8)
        label(canvas, "a clip is 13 frames in sequence", (PAD, PAD + 14), MUTED, 0.46)
        for i, f in enumerate(strip):
            x = PAD + i * (TW + GAP)
            y = PAD + 26
            inside = abs(i - center) <= 1
            tile = f if inside else (f * 0.35 + 255 * 0.65).astype(np.uint8)
            canvas[y:y + TH, x:x + TW] = tile
            cv2.rectangle(canvas, (x, y), (x + TW, y + TH),
                          ORANGE if inside else (215, 215, 215), 3 if inside else 1)
        # bracket under the three frames the kernel currently spans
        x0 = PAD + (center - 1) * (TW + GAP)
        x1 = PAD + (center + 1) * (TW + GAP) + TW
        yb = PAD + 26 + TH + 12
        cv2.line(canvas, (x0, yb), (x1, yb), ORANGE, 2)
        cv2.line(canvas, (x0, yb), (x0, yb - 7), ORANGE, 2)
        cv2.line(canvas, (x1, yb), (x1, yb - 7), ORANGE, 2)
        label(canvas, "the temporal filter spans 3 frames at once - this is how motion is seen",
              (PAD, yb + 24), INK, 0.48)
        label(canvas, "a 2D network sees each frame alone and cannot tell a punch from a wave",
              (PAD, yb + 44), MUTED, 0.44)
        frames.append(canvas)

    save_gif(frames, OUT / "conv3d_time.gif", fps=3)


# ---------------------------------------------------------------------------
# 3. What the deployed model actually emits, frame by frame
# ---------------------------------------------------------------------------
def gif_detector_output(clip_path, model_key="violence"):
    from x3d_violence_detector import SceneViolenceDetector
    import json

    cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))["detection"]
    if model_key == "violence":
        blk = cfg["violence"]
        mp = blk["scene_model_path"]
        thr = blk["scene_confidence_threshold"]
        con = blk["scene_consecutive_required"]
        title = "PHYSICAL INJURY"
    else:
        blk = cfg[model_key]
        mp = blk["model_path"]
        thr = blk["confidence_threshold"]
        con = blk["consecutive_required"]
        title = blk.get("display_name", model_key).upper()

    det = SceneViolenceDetector(model_path=str(REPO / mp), threshold=thr, consecutive=con)
    det.reset_scene()

    src = read_frames(clip_path, n=90)
    W, H = 560, 315
    out_frames = []
    hist = []

    for i, f in enumerate(src):
        confirmed, raw = det.update(f, i)[:2]
        ema = det._scene_ema if det._scene_ema is not None else raw
        hist.append(ema)

        vis = cv2.resize(f, (W, H))
        # Text row and trace are STACKED, not side by side. Placing the graph
        # at a fixed x beside the text collided the moment the text was long
        # enough, and pushed the "thr" tick off the right edge.
        PH = 104
        panel = np.full((H + PH, W, 3), 255, np.uint8)
        panel[:H] = vis

        # alert border + banner, exactly the state the system would be in
        if confirmed:
            cv2.rectangle(panel, (2, 2), (W - 3, H - 3), RED, 4)
            cv2.rectangle(panel, (0, 0), (W, 26), RED, -1)
            label(panel, f"ALERT  -  {title}", (10, 18), (255, 255, 255), 0.55, 2)

        y0 = H + 6
        state = "CONFIRMED" if confirmed else ("above threshold" if ema >= thr else "quiet")
        label(panel, f"confidence {ema:.2f}", (10, y0 + 13), INK, 0.46)
        label(panel, f"threshold {thr:.2f}", (150, y0 + 13), MUTED, 0.44)
        label(panel, f"needs {con} in a row", (270, y0 + 13), MUTED, 0.44)
        label(panel, state, (410, y0 + 13), RED if confirmed else MUTED, 0.44,
              2 if confirmed else 1)

        # confidence trace, full width beneath the text, with room for the tick
        gx, gy, gw, gh = 10, y0 + 22, W - 46, 66
        cv2.rectangle(panel, (gx, gy), (gx + gw, gy + gh), (232, 232, 232), 1)
        ty = int(gy + gh - thr * gh)
        cv2.line(panel, (gx, ty), (gx + gw, ty), MUTED, 1)
        label(panel, "thr", (gx + gw + 4, ty + 4), MUTED, 0.36)
        pts = hist[-gw:]
        for k in range(1, len(pts)):
            x1 = gx + gw - len(pts) + k - 1
            x2 = gx + gw - len(pts) + k
            cv2.line(panel,
                     (x1, int(gy + gh - pts[k - 1] * gh)),
                     (x2, int(gy + gh - pts[k] * gh)),
                     RED if pts[k] >= thr else BLUE, 2)
        out_frames.append(panel)

    det.close()
    # Every 2nd frame: 90 frames of 10 fps footage reads the same at 45 and
    # halves the file.
    save_gif(out_frames[::2], OUT / f"detector_{model_key}.gif", fps=6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default=DEFAULT_CLIP)
    ap.add_argument("--skip-detector", action="store_true",
                    help="diagrams only; skips loading the models")
    args = ap.parse_args()

    clip = Path(args.clip)
    if not clip.exists():
        raise SystemExit(f"clip not found: {clip}")

    print(f"Source clip: {clip.name}")
    frames = read_frames(clip, n=40)
    print(f"Writing to {OUT}")

    gif_conv2d(frames[len(frames) // 2])
    gif_conv3d(frames)
    if not args.skip_detector:
        gif_detector_output(clip, "violence")
    print("\nDone.")


if __name__ == "__main__":
    raise SystemExit(main())
