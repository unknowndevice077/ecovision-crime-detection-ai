import shutil
from pathlib import Path

# Point these at wherever Roboflow exported your weapon datasets
WEAPON_DATASETS = [
    r"D:\datasets\weapon-cctv-v3",
    r"D:\datasets\mahad-gun-knife",
    r"D:\datasets\sanket-knife",
]

OUT_BASE = Path(r"D:\ecovision_training")

# Class remapping — different datasets use different class numbers
# Check each dataset's data.yaml and map to your final classes:
# 0 = Gun, 1 = Knife, 2 = Violence

CLASS_REMAP = {
    # weapon-cctv-v3 classes (check its data.yaml)
    "gun":      0,
    "Gun":      0,
    "pistol":   0,
    "Pistol":   0,
    "rifle":    0,
    "Rifle":    0,
    "knife":    1,
    "Knife":    1,
}

def remap_label_file(src_label, dst_label, remap):
    """Read label file, remap class IDs, write to destination"""
    lines_out = []
    with open(src_label) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts  = line.split()
            cls_id = int(parts[0])
            # If remap is by name you need the dataset's names list
            # Here we assume you've already checked and set correct IDs
            lines_out.append(line)
    with open(dst_label, "w") as f:
        f.write("\n".join(lines_out))

def merge_dataset(src_root):
    src_root = Path(src_root)
    merged   = 0

    for split in ["train", "val"]:
        img_src = src_root / "images" / split
        lbl_src = src_root / "labels" / split

        if not img_src.exists():
            # Some Roboflow exports use different structure
            img_src = src_root / split / "images"
            lbl_src = src_root / split / "labels"
        if not img_src.exists():
            print(f"  ⚠️  Skipping {src_root.name} — structure not recognized")
            return

        img_dst = OUT_BASE / "images" / split
        lbl_dst = OUT_BASE / "labels" / split

        for img in img_src.glob("*.jpg"):
            lbl = lbl_src / (img.stem + ".txt")
            if not lbl.exists():
                continue
            # Add dataset name prefix to avoid filename conflicts
            prefix  = src_root.name[:8]
            new_img = img_dst / f"{prefix}_{img.name}"
            new_lbl = lbl_dst / f"{prefix}_{lbl.name}"
            shutil.copy(str(img), str(new_img))
            shutil.copy(str(lbl), str(new_lbl))
            merged += 1

    print(f"  Merged {merged} images from {src_root.name}")

for ds in WEAPON_DATASETS:
    print(f"Merging {Path(ds).name}...")
    merge_dataset(ds)

print("Merge complete.")