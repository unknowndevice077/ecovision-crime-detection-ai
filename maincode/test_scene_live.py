"""
EcoVision -- SCENE-MODE live tester
===================================
Runs the whole-frame violence classifier (SceneViolenceDetector) exactly as
main.py does, on a webcam or a video file, and shows what it is thinking.

Why this exists rather than visualize_all_detections.py: that script imports
X3DViolenceDetector -- the PER-TRACK, person-crop detector. That is the old
architecture, the one that scored ~70% because 17.2% of clips never reached
the classifier at all when no track survived 20 frames. It cannot show you
scene mode, so it cannot show you the model measured at 95.0%.

    # webcam
    python test_scene_live.py --source 0 --show

    # a video file
    python test_scene_live.py --source "D:/clips/fight.mp4" --show

    # a whole folder, no window, just the verdicts
    python test_scene_live.py --source "D:/clips" --summary-only

    # write an annotated copy
    python test_scene_live.py --source clip.mp4 --save out.mp4

WHAT TO WATCH
  raw   -- this inference's probability, one per check_interval frames
  ema   -- smoothed; this is what the threshold actually tests
  FIRED -- ema crossed the threshold for consecutive_required checks

On live footage the number that matters is not accuracy, it is how often it
FIRES when nothing is happening. The test split says ~90 false alarms/hour on
busy street footage and none on calm scenes, so point it at your real camera
and watch the alarm counter, not the confidence.
"""
import argparse
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from x3d_violence_detector import (
    SceneViolenceDetector,
    TiledSceneViolenceDetector,
    SCENE_CONFIDENCE_THRESHOLD,
    SCENE_CONSECUTIVE_REQUIRED,
    SCENE_MODEL_PATH,
    X3D_CHECK_INTERVAL,
    EMA_ALPHA,
)

VIDEO_EXT = (".mp4", ".avi", ".mov", ".mkv", ".m4v")


def draw_hud(frame, raw, ema, fired, thr, fires, elapsed, checks, graph):
    h, w = frame.shape[:2]
    panel_h = 116
    cv2.rectangle(frame, (0, 0), (w, panel_h), (18, 18, 18), -1)

    colour = (60, 60, 235) if fired else (80, 200, 80)
    label = "VIOLENCE" if fired else "clear"
    cv2.putText(frame, label, (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.95, colour, 2, cv2.LINE_AA)

    # Confidence bar with the threshold marked, so a near-miss is visible.
    bx, by, bw, bh = 12, 48, min(360, w - 24), 20
    cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (70, 70, 70), 1)
    cv2.rectangle(frame, (bx, by), (bx + int(bw * max(0.0, min(1.0, ema))), by + bh),
                  colour, -1)
    tx = bx + int(bw * thr)
    cv2.line(frame, (tx, by - 3), (tx, by + bh + 3), (0, 215, 255), 2)

    cv2.putText(frame, f"raw {raw:.3f}   ema {ema:.3f}   thr {thr:.2f}",
                (12, by + bh + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (220, 220, 220), 1, cv2.LINE_AA)

    rate = fires / (elapsed / 3600) if elapsed > 0 else 0.0
    cv2.putText(frame, f"alarms {fires}   {rate:6.1f}/hr   checks {checks}",
                (12, by + bh + 42), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 200, 120), 1, cv2.LINE_AA)

    # Rolling EMA trace on the right, with the threshold line.
    gw, gh = min(260, max(80, w - bx - bw - 30)), panel_h - 24
    gx, gy = w - gw - 12, 12
    cv2.rectangle(frame, (gx, gy), (gx + gw, gy + gh), (45, 45, 45), -1)
    ty = gy + gh - int(gh * thr)
    cv2.line(frame, (gx, ty), (gx + gw, ty), (0, 140, 170), 1)
    if len(graph) > 1:
        step = gw / max(len(graph) - 1, 1)
        pts = [(int(gx + i * step), int(gy + gh - gh * max(0.0, min(1.0, v))))
               for i, v in enumerate(graph)]
        cv2.polylines(frame, [np.array(pts, np.int32)], False, (90, 210, 250), 1, cv2.LINE_AA)
    return frame


def run_one(src, det, args):
    cap = cv2.VideoCapture(int(src) if str(src).isdigit() else str(src))
    if not cap.isOpened():
        print(f"  could not open {src}")
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if fps <= 1 or fps > 240:
        fps = 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

    det.reset_scene()
    writer = None
    graph = deque(maxlen=160)
    n = fires = checks = 0
    was_fired = False
    peak_raw = peak_ema = 0.0
    t0 = time.time()

    limit = int(args.seconds * fps) if getattr(args, "seconds", None) else None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        n += 1
        if limit and n > limit:
            break
        prev_checks = det.get_inference_count(det.SCENE_TID)

        # Crop BEFORE the detector sees it, so the 160x160 resize is applied to
        # the region people actually occupy rather than to the whole street.
        roi = getattr(args, "roi_box", None)
        if roi:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = (int(roi[0] * w), int(roi[1] * h),
                              int(roi[2] * w), int(roi[3] * h))
            analysed = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
            if analysed.size == 0:
                analysed = frame
        else:
            analysed = frame

        # Shrink AFTER any ROI crop: this stands in for pulling the camera
        # back, so it must act on whatever region is being analysed.
        if getattr(args, "shrink", None) and args.shrink < 1.0:
            ah, aw = analysed.shape[:2]
            sw, sh = max(8, int(aw * args.shrink)), max(8, int(ah * args.shrink))
            small = cv2.resize(analysed, (sw, sh), interpolation=cv2.INTER_AREA)
            analysed = cv2.copyMakeBorder(
                small, (ah - sh) // 2, ah - sh - (ah - sh) // 2,
                (aw - sw) // 2, aw - sw - (aw - sw) // 2,
                borderType=cv2.BORDER_REPLICATE)

        # TiledSceneViolenceDetector handles the grid internally, with its own
        # buffer and EMA per tile, so the call site is identical either way.
        fired, raw = det.update(analysed, n)

        info = det.get_scene_debug_info()
        ema = info["ema_confidence"]

        if roi and (args.show or args.save):
            h, w = frame.shape[:2]
            cv2.rectangle(frame, (int(roi[0]*w), int(roi[1]*h)),
                          (int(roi[2]*w), int(roi[3]*h)), (0, 200, 255), 2)

        if det.get_inference_count(det.SCENE_TID) > prev_checks:
            checks += 1
            graph.append(ema)
            peak_raw = max(peak_raw, raw)
            peak_ema = max(peak_ema, ema)

        # Count rising edges, not frames -- one incident is one alarm.
        if fired and not was_fired:
            fires += 1
        was_fired = fired

        elapsed = n / fps
        if args.show or args.save:
            vis = draw_hud(frame.copy(), raw, ema, fired,
                           det.threshold, fires, elapsed, checks, graph)
            if args.save:
                if writer is None:
                    writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"),
                                             fps, (vis.shape[1], vis.shape[0]))
                writer.write(vis)
            if args.show:
                cv2.imshow("EcoVision scene mode", vis)
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    cap.release()
                    if writer:
                        writer.release()
                    return "quit"

    cap.release()
    if writer:
        writer.release()

    secs = n / fps
    return {
        "frames": n, "seconds": secs, "checks": checks, "alarms": fires,
        "peak_raw": peak_raw, "peak_ema": peak_ema,
        "alarms_per_hour": fires / (secs / 3600) if secs > 0 else 0.0,
        "wall": time.time() - t0,
    }


def check_scale(source, roi, seconds=20, device=None):
    """Report person height as a fraction of the analysed frame, against the
    range measured on the training data. Framing, not resolution, is what
    decides whether this model can work on a given camera."""
    from ultralytics import YOLO

    TRAIN_LO, TRAIN_MED, TRAIN_HI = 0.237, 0.371, 0.600
    model = YOLO(str(Path(__file__).resolve().parents[1] / "weights" / "yolo11s-pose.pt"))

    cap = cv2.VideoCapture(int(source) if str(source).isdigit() else str(source))
    if not cap.isOpened():
        raise SystemExit(f"could not open {source}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if fps <= 1 or fps > 240:
        fps = 30.0

    heights, frames_seen, empty = [], 0, 0
    want = int(seconds * fps)
    print(f"sampling ~{seconds}s of {source} (every 10th frame)...")
    while frames_seen < want:
        ok, frame = cap.read()
        if not ok:
            break
        frames_seen += 1
        if frames_seen % 10:
            continue
        if roi:
            h, w = frame.shape[:2]
            frame = frame[int(roi[1]*h):int(roi[3]*h), int(roi[0]*w):int(roi[2]*w)]
            if frame.size == 0:
                continue
        H = frame.shape[0]
        res = model.predict(frame, imgsz=640, verbose=False,
                            device=device if device else None)
        got = 0
        if res and res[0].boxes is not None and len(res[0].boxes):
            for b in res[0].boxes.xyxy.cpu().numpy():
                heights.append((b[3] - b[1]) / H)
                got += 1
        if not got:
            empty += 1
    cap.release()

    print(f"\nframes sampled: {frames_seen//10}   with nobody detected: {empty}")
    if not heights:
        print("\nNo people detected at all. Either nobody is in view, or they are too\n"
              "small for the pose model -- which is itself the answer: if the detector\n"
              "cannot find them, the classifier cannot see them either.")
        return

    heights.sort()
    med = heights[len(heights)//2]
    p10, p90 = heights[int(len(heights)*.1)], heights[int(len(heights)*.9)]
    print(f"people detected: {len(heights)}")
    print(f"  height p10 / median / p90 : {p10*100:.1f}% / {med*100:.1f}% / {p90*100:.1f}%")
    print(f"  in the 160x160 tensor     : {p10*160:.0f} / {med*160:.0f} / {p90*160:.0f} px")
    print(f"\ntraining data              : {TRAIN_LO*100:.0f}% - {TRAIN_HI*100:.0f}% "
          f"(median {TRAIN_MED*100:.0f}%), i.e. {TRAIN_LO*160:.0f}-{TRAIN_HI*160:.0f} px")

    if med < TRAIN_LO:
        factor = TRAIN_MED / max(med, 1e-6)
        print(f"\nTOO SMALL. People are {med*100:.1f}% of frame height; the model learned "
              f"on {TRAIN_MED*100:.0f}%.\nZoom/crop by about {factor:.1f}x -- in OBS, crop to "
              f"roughly {100/factor:.0f}% of the current\nwidth and height around where people "
              f"walk. Expect false alarms until this is fixed:\nat this scale the model is "
              f"reacting to motion, not to people.")
    elif med > TRAIN_HI:
        print(f"\nTOO LARGE. People fill {med*100:.1f}% of the frame vs {TRAIN_MED*100:.0f}% "
              f"in training.\nWiden the crop; a fight may not fit in view.")
    else:
        print(f"\nGOOD. {med*100:.1f}% sits inside the trained range "
              f"({TRAIN_LO*100:.0f}-{TRAIN_HI*100:.0f}%).\nFraming is not your limiting "
              f"factor; if it still false-fires, the cause is domain shift and the fix is\n"
              f"hard negatives recorded from this camera.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="webcam index (0), a video file, or a folder of videos")
    ap.add_argument("--show", action="store_true", help="display a window (q/esc quits)")
    ap.add_argument("--save", default=None, help="write an annotated mp4 (single source only)")
    ap.add_argument("--summary-only", action="store_true", help="no window, just the numbers")
    ap.add_argument("--threshold", type=float, default=None,
                    help="override the scene threshold for this run only; config.json is "
                         "untouched. The test split put the best accuracy near 0.70-0.80, "
                         "but that was tuned on benchmark clips -- tune it on YOUR camera.")
    ap.add_argument("--device", default=None, help="cuda index or 'cpu'")
    ap.add_argument("--weights", default=None,
                    help="Score with a different .pt than config.json's scene_model_path. "
                         "Use it to A/B two checkpoints on the same feed -- on a live "
                         "source the scene changes minute to minute, so back-to-back runs "
                         "are only roughly comparable; longer runs make them less rough.")
    ap.add_argument("--roi", default=None, metavar="x1,y1,x2,y2",
                    help="Analyse only this sub-rectangle, as FRACTIONS of width/height "
                         "(e.g. 0.25,0.4,0.75,1.0). Use this on wide cameras. Measured on "
                         "the training data, a person is 24-60%% of frame height (38-96 px "
                         "after the 160x160 resize); a wide street view gives 6-12%% "
                         "(10-19 px), a scale the model never saw, so it responds to global "
                         "motion instead of to people. Cropping to where people actually "
                         "walk restores the trained scale without retraining.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="Stop after this many seconds of footage. Required in practice "
                         "for a live camera, which otherwise never ends. The alarm-rate "
                         "figure only means something over a decent stretch -- 10 min of "
                         "quiet street is a far more useful number than 30 s.")
    ap.add_argument("--check-scale", action="store_true",
                    help="Don't classify -- just measure how tall people are in this "
                         "source and say whether it matches what the model was trained "
                         "on (24-60%% of frame height, median 37%%). Run this after "
                         "adjusting the OBS crop to confirm the framing is right, "
                         "instead of guessing from the picture.")
    ap.add_argument("--shrink", type=float, default=None, metavar="F",
                    help="Simulate a WIDER camera: shrink the frame content to F of its "
                         "size and replicate the border, so people get smaller without "
                         "changing anything else. F=0.35 turns a 24%%-of-height person "
                         "into ~9%%, roughly an uncropped street view. Comparing F=1 "
                         "against F=0.35 on the SAME feed isolates person scale from "
                         "time-of-day and scene content, which back-to-back live runs "
                         "cannot do.")
    ap.add_argument("--tiles", type=int, default=1, choices=[1, 2, 3],
                    help="Split the frame into NxN tiles and score each separately, taking "
                         "the max. Another way to recover person scale on a wide view; "
                         "costs N*N inferences per check.")
    args = ap.parse_args()

    roi = None
    if args.roi:
        try:
            roi = tuple(float(v) for v in args.roi.split(","))
            if len(roi) != 4 or not all(0.0 <= v <= 1.0 for v in roi) \
               or roi[0] >= roi[2] or roi[1] >= roi[3]:
                raise ValueError
        except ValueError:
            raise SystemExit("--roi must be four fractions 0..1 as x1,y1,x2,y2 "
                             "with x1<x2 and y1<y2, e.g. 0.25,0.4,0.75,1.0")
    args.roi_box = roi

    if args.summary_only:
        args.show = False

    if args.check_scale:
        check_scale(args.source if str(args.source).isdigit() else Path(args.source),
                    roi, device=args.device)
        return

    if args.tiles > 1:
        det = TiledSceneViolenceDetector(grid=args.tiles, device=args.device,
                                         threshold=args.threshold,
                                         model_path=args.weights)
    else:
        det = SceneViolenceDetector(device=args.device, threshold=args.threshold,
                                    model_path=args.weights)
    print(f"\nweights   : {args.weights or SCENE_MODEL_PATH}")
    if args.tiles > 1:
        print(f"tiling    : {args.tiles}x{args.tiles} = {args.tiles**2} regions, "
              f"each with its own buffer/EMA; alarm if ANY tile fires. "
              f"Full coverage -- nothing cropped away.")
    if det.meta:
        print(f"trained as: {det.meta.get('run_name')}  "
              f"val_acc {det.meta.get('best_val_acc')}  "
              f"{det.meta.get('input_repr')}  loss={det.meta.get('loss', 'n/a')}")
    print(f"threshold : {det.threshold}   consecutive: {det.consecutive}   "
          f"check every {X3D_CHECK_INTERVAL} frames   ema_alpha {EMA_ALPHA}")
    if det.consecutive == 1:
        print("NOTE: consecutive_required=1 -- a single check above the threshold "
              "raises an alarm. Expect that to be twitchy on live footage.")
    print()

    src = Path(args.source) if not str(args.source).isdigit() else args.source
    if isinstance(src, Path) and src.is_dir():
        vids = sorted(p for p in src.rglob("*") if p.suffix.lower() in VIDEO_EXT)
        print(f"{len(vids)} videos under {src}\n")
        print(f"{'clip':<44} {'alarms':>7} {'peak ema':>9} {'checks':>7}")
        print("-" * 72)
        agg_alarms = agg_secs = 0
        for v in vids:
            r = run_one(v, det, args)
            if r == "quit":
                break
            if r:
                agg_alarms += r["alarms"]
                agg_secs += r["seconds"]
                print(f"{v.name[:43]:<44} {r['alarms']:>7} {r['peak_ema']:>9.3f} "
                      f"{r['checks']:>7}")
        if agg_secs:
            print("-" * 72)
            print(f"total: {agg_alarms} alarms over {agg_secs/60:.1f} min "
                  f"= {agg_alarms/(agg_secs/3600):.1f} alarms/hour")
    else:
        r = run_one(src, det, args)
        if r and r != "quit":
            print(f"frames        : {r['frames']}  ({r['seconds']:.1f}s of footage)")
            print(f"inferences    : {r['checks']}")
            print(f"alarms raised : {r['alarms']}")
            print(f"peak raw conf : {r['peak_raw']:.3f}")
            print(f"peak ema conf : {r['peak_ema']:.3f}")
            print(f"alarm rate    : {r['alarms_per_hour']:.1f}/hour")
            print(f"processed in  : {r['wall']:.1f}s "
                  f"({r['frames']/max(r['wall'],1e-6):.1f} fps)")
        if args.save:
            print(f"annotated video -> {args.save}")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
