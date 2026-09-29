"""
Turns operator verdicts on AI alerts into a training dataset.

Every Confirm / Dismiss on an AI alert is stored in detection_feedback
(backend.py's _record_detection_feedback). This script copies the matching
clean frames, event clips and weapon crops out of the app's data folder
into a dated dataset folder, writes a manifest, and builds contact sheets
so the labels can be LOOKED AT before anything trains on them -- every
dataset this project has used so far had wrong labels somewhere, and the
only thing that caught them was a contact sheet.

Run from the repo root:

    python-env\\python.exe tools\\export_feedback_dataset.py
    python-env\\python.exe tools\\export_feedback_dataset.py --all --out D:\\EcoVisionImagesTraining\\feedback

By default only verdicts not exported before are included, and they're
marked exported afterwards (--dry-run leaves them unmarked). --all
re-exports everything.

Layout written:

    <out>/feedback_<timestamp>/
        manifest.csv
        frames/<label>/<ai_event>/<incident>.jpg      clean frame (or the annotated
                                                      evidence image, flagged in the
                                                      manifest, for alerts raised
                                                      before clean frames were saved)
        clips/<label>/<ai_event>/<incident>_<n>.mp4
        weapon_crops/<label>/<weapon>/<incident>_<n>.jpg
        contact_sheet_<label>.jpg

What each folder is for:
    frames/dismissed   -> hard negatives for the scene models, per camera
    clips/*            -> clip-level retraining of the X3D violence/robbery models
    weapon_crops/*     -> the second-stage weapon verifier (confirmed = real,
                          dismissed = the things the detector mistakes for weapons)
    final_type column  -> where an officer re-typed the alert (AI said ASSAULT,
                          report says ROBBERY): the robbery model's first real labels
"""
import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime

import cv2
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "app"))
from db import get_conn  # noqa: E402  (same SQLite/Postgres switch the backend uses)

WRITABLE_DIR = os.environ.get("ECOVISION_WRITABLE_DIR") or os.path.join(os.path.expanduser("~"), "EcoVisionSentinelData")
SCREENSHOTS_DIR = os.path.join(WRITABLE_DIR, "static", "screenshots")
DEFAULT_OUT = r"D:\EcoVisionImagesTraining\feedback"

# Padding around a weapon box when cropping, as a fraction of box size --
# the verifier needs to see the hand holding it, not just the object.
CROP_PAD = 0.35
SHEET_THUMB = (240, 135)
SHEET_COLS = 6


def _screenshot_file(url_path):
    return os.path.join(SCREENSHOTS_DIR, os.path.basename(url_path)) if url_path else None


def _crop(img, box, pad=CROP_PAD):
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    x1, y1 = max(0, int(x1 - bw * pad)), max(0, int(y1 - bh * pad))
    x2, y2 = min(w, int(x2 + bw * pad)), min(h, int(y2 + bh * pad))
    if x2 <= x1 or y2 <= y1:
        return None
    return img[y1:y2, x1:x2]


def _contact_sheet(items, path, title):
    """items: [(image_path, caption)]. One grid per label, captions in red
    when the frame is the annotated fallback rather than a clean frame."""
    if not items:
        return
    tw, th = SHEET_THUMB
    rows = (len(items) + SHEET_COLS - 1) // SHEET_COLS
    sheet = np.full((rows * (th + 22) + 30, SHEET_COLS * tw, 3), 24, np.uint8)
    cv2.putText(sheet, title, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    for i, (img_path, caption, annotated) in enumerate(items):
        img = cv2.imread(img_path)
        if img is None:
            continue
        r, c = divmod(i, SHEET_COLS)
        y, x = 30 + r * (th + 22), c * tw
        sheet[y:y + th, x:x + tw] = cv2.resize(img, (tw, th))
        colour = (80, 80, 255) if annotated else (220, 220, 220)
        cv2.putText(sheet, caption[:34], (x + 3, y + th + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.38, colour, 1, cv2.LINE_AA)
    cv2.imwrite(path, sheet)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"parent folder for the dataset (default {DEFAULT_OUT})")
    ap.add_argument("--all", action="store_true", help="include verdicts already exported")
    ap.add_argument("--dry-run", action="store_true", help="don't mark rows as exported")
    ap.add_argument("--camera", help="only this camera id")
    args = ap.parse_args()

    conn = get_conn()
    cur = conn.cursor()
    where, params = [], []
    if not args.all:
        where.append("f.exported_at IS NULL")
    if args.camera:
        where.append("f.camera_id = ?")
        params.append(args.camera)
    cur.execute(
        "SELECT f.*, c.name AS camera_name FROM detection_feedback f LEFT JOIN cameras c ON c.id = f.camera_id "
        + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY f.decided_at",
        tuple(params),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        print("Nothing to export -- no new operator verdicts on AI alerts yet.")
        return

    out = os.path.join(args.out, f"feedback_{datetime.now():%Y%m%d_%H%M%S}")
    os.makedirs(out, exist_ok=True)
    sheets = {"confirmed": [], "dismissed": []}
    manifest = []
    missing_frames = 0

    for r in rows:
        iid, label, event = r["incident_id"], r["label"], (r["ai_event"] or "UNKNOWN").replace(" ", "_")
        ctx = {}
        try:
            ctx = json.loads(r["ai_context"]) if r.get("ai_context") else {}
        except ValueError:
            pass

        # Frame: clean if the AI core saved one, else the annotated evidence image.
        evidence = _screenshot_file(r["screenshot"])
        clean = evidence[:-4] + "_clean.jpg" if evidence and evidence.endswith(".jpg") else None
        src, annotated = (clean, False) if clean and os.path.exists(clean) else (evidence, True)
        frame_rel = ""
        img = None
        if src and os.path.exists(src):
            frame_rel = os.path.join("frames", label, event, f"{iid}.jpg")
            os.makedirs(os.path.join(out, os.path.dirname(frame_rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(out, frame_rel))
            img = cv2.imread(src)
            caption = f"{r['confidence'] or 0:.2f} {event} {r['camera_name'] or r['camera_id'] or '?'}"
            if r.get("final_type") and r["final_type"] != r["ai_event"]:
                caption += f" ->{r['final_type']}"
            sheets[label].append((os.path.join(out, frame_rel), caption, annotated))
        else:
            missing_frames += 1

        # Weapon crops need clean pixels; an annotated frame has boxes drawn in.
        crop_rels = []
        if img is not None and not annotated:
            for n, w in enumerate(ctx.get("weapons") or []):
                if not w.get("box"):
                    continue
                crop = _crop(img, w["box"])
                if crop is None:
                    continue
                rel = os.path.join("weapon_crops", label, (w.get("name") or "weapon").lower(), f"{iid}_{n}.jpg")
                os.makedirs(os.path.join(out, os.path.dirname(rel)), exist_ok=True)
                cv2.imwrite(os.path.join(out, rel), crop)
                crop_rels.append(rel)

        # Event clips registered against this incident.
        cur.execute("SELECT file_path FROM video_records WHERE associated_incident_id = ? ORDER BY recorded_at", (iid,))
        clip_rels = []
        for n, clip in enumerate(cur.fetchall()):
            path = clip["file_path"]
            if path and os.path.exists(path):
                rel = os.path.join("clips", label, event, f"{iid}_{n}{os.path.splitext(path)[1] or '.mp4'}")
                os.makedirs(os.path.join(out, os.path.dirname(rel)), exist_ok=True)
                shutil.copy2(path, os.path.join(out, rel))
                clip_rels.append(rel)

        manifest.append({
            "incident_id": iid, "camera_id": r["camera_id"], "camera_name": r["camera_name"],
            "barangay_id": r["barangay_id"], "ai_event": r["ai_event"], "final_type": r["final_type"],
            "label": label, "confidence": r["confidence"], "detector": ctx.get("detector"),
            "decided_by": r["decided_by_username"], "decided_at": r["decided_at"],
            "frame": frame_rel, "frame_is_annotated": annotated if frame_rel else "",
            "weapon_crops": ";".join(crop_rels), "clips": ";".join(clip_rels),
        })

    with open(os.path.join(out, "manifest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys()))
        w.writeheader()
        w.writerows(manifest)
    for label, items in sheets.items():
        _contact_sheet(items, os.path.join(out, f"contact_sheet_{label}.jpg"),
                       f"{label.upper()} - {len(items)} alerts (red caption = annotated frame, not clean)")

    if not args.dry_run:
        for r in rows:
            cur.execute("UPDATE detection_feedback SET exported_at = NOW() WHERE incident_id = ?", (r["incident_id"],))
        conn.commit()
    conn.close()

    confirmed = sum(1 for m in manifest if m["label"] == "confirmed")
    print(f"Exported {len(manifest)} verdicts ({confirmed} confirmed, {len(manifest) - confirmed} dismissed) to {out}")
    if missing_frames:
        print(f"  {missing_frames} had no frame on disk (screenshot deleted or never saved)")
    print("  Open the contact sheets and check the labels before training on this.")


if __name__ == "__main__":
    main()
