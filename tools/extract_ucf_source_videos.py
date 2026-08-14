"""Extract the untrimmed UCF-Crime source videos this project actually needs.

The 96 GB archive is nested: the outer zip holds nine inner zips, and only some
of them matter here. The two Training-Normal parts alone are 67 GB and duplicate
negatives we already have 900 clips of, so they are skipped by default.

Disk discipline matters -- there are 73 GB free. Each inner zip is written out,
selectively unpacked, then DELETED before the next one starts, so peak usage is
one inner archive (~6 GB) plus the videos kept from it, not the whole 96 GB.

Why this unlocks anything: the current manifests were built from clips that
someone had to temporally annotate by hand, and that annotation is the
bottleneck -- robbery survives at 43 source videos, vandalism at 18. MIL trains
from a video-level label instead, so every untrimmed video below becomes usable
without anyone marking start and end frames.

    .venv\\Scripts\\python.exe tools\\extract_ucf_source_videos.py
    .venv\\Scripts\\python.exe tools\\extract_ucf_source_videos.py --all-categories
"""
import argparse
import shutil
import zipfile
from collections import Counter
from pathlib import Path

SRC = Path(r"D:\Users\User\Downloads\Anomaly-Detection-Dataset.zip")
DEST = Path(r"D:\EcoVisionImagesTraining\ucf_source")
STAGE = Path(r"D:\EcoVisionImagesTraining\_ucf_staging")

# The classes this project has a use for. Property crime feeds robbery
# (currently 43 sources); Vandalism feeds the disabled class (currently 18);
# the violence group is a possible source of harder positives than the
# benchmark clips, which are mostly indoor and not street CCTV.
WANTED = {
    "robbery":    ["Robbery", "Stealing", "Burglary", "Shoplifting"],
    "vandalism":  ["Vandalism", "Arson"],
    "violence":   ["Assault", "Fighting", "Abuse"],
}
INNER_DEFAULT = [f"Anomaly-Videos-Part-{i}.zip" for i in (1, 2, 3, 4)]


def category_of(name, groups):
    stem = Path(name).name
    for group, cats in groups.items():
        for c in cats:
            if stem.lower().startswith(c.lower()):
                return group, c
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-categories", action="store_true",
                    help="also take RoadAccidents, Explosion, Shooting, Arrest")
    ap.add_argument("--normals", action="store_true",
                    help="also extract Testing_Normal_Videos.zip (4.3 GB)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    groups = dict(WANTED)
    if args.all_categories:
        groups["other"] = ["RoadAccidents", "Explosion", "Shooting", "Arrest"]

    inner = list(INNER_DEFAULT)
    if args.normals:
        inner.append("Testing_Normal_Videos.zip")

    DEST.mkdir(parents=True, exist_ok=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(DEST.drive + "\\").free / 2**30
    print(f"{free:.0f} GB free on {DEST.drive}\n")

    kept = Counter()
    with zipfile.ZipFile(SRC) as outer:
        outer_names = {Path(n).name: n for n in outer.namelist()}

        for part in inner:
            if part not in outer_names:
                print(f"  (missing from archive: {part})")
                continue
            staged = STAGE / part
            print(f"[{part}] staging...", flush=True)
            if args.dry_run:
                continue
            with outer.open(outer_names[part]) as fsrc, open(staged, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst, length=1 << 24)

            with zipfile.ZipFile(staged) as inz:
                members = [m for m in inz.infolist()
                           if not m.is_dir()
                           and Path(m.filename).suffix.lower() in (".mp4", ".avi")]
                take = []
                for m in members:
                    grp, cat = category_of(m.filename, groups)
                    if grp:
                        take.append((m, grp, cat))
                print(f"  {len(members)} videos, taking {len(take)}")
                for m, grp, cat in take:
                    out = DEST / grp / cat / Path(m.filename).name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    if out.exists() and out.stat().st_size == m.file_size:
                        continue
                    with inz.open(m) as fsrc, open(out, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst, length=1 << 22)
                    kept[cat] += 1

            # Delete before the next part, so peak disk stays at one inner zip.
            staged.unlink(missing_ok=True)
            free = shutil.disk_usage(DEST.drive + "\\").free / 2**30
            print(f"  done. {free:.0f} GB free\n", flush=True)

    print("Extracted:")
    for cat, n in sorted(kept.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:16} {n:>4} source videos")
    total = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
    print(f"\n{sum(kept.values())} videos, {total / 2**30:.1f} GB -> {DEST}")


if __name__ == "__main__":
    raise SystemExit(main())
