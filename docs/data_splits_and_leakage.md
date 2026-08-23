# Data splits and leakage audit

Audited 2026-08-21. Every split this project currently trains or reports on is
listed here with its grouping rule, its measured leakage, and the limitations
that survive the audit.

**Why this document exists.** Leakage is the failure that makes every other
number meaningless, and it is invisible in a training log — a leaked split
produces a *better* looking curve, not a worse one. This project has already
been bitten by it once, badly enough that a whole model's published metrics had
to be withdrawn. So the splits are audited, and the audit is written down.

---

## The audit is independent of the builders

[`verify_all_leakage.py`](../../EcoVisionImagesTraining/verify_all_leakage.py)
re-derives the grouping from the files on disk and from the manifest. It
deliberately does **not** import anything from the scripts that built the
splits.

The reason is not paranoia. Each build script prints its own "0 leaked" line,
and that line is computed from the same grouping function the script used to
make the split. If that function is wrong, the split is wrong *and* the report
says it is fine — the error is invisible precisely where it matters. An audit
that shares code with the thing it audits is not an audit.

### Three failure modes, checked separately

They fail differently, so they are counted differently:

| # | mode | why names alone are not enough |
|---|---|---|
| 1 | **Group overlap** | the same base image / source video in two splits — the one that inflates metrics |
| 2 | **Byte-identical content** | the same file under a different name in two splits; survives any name-based grouping. Checked by SHA-256 over every file. |
| 3 | **Name collision** | the same filename in two splits |

---

## Results

All three datasets: **zero on all three modes.**

| dataset | grouping | train | val | test | group overlap | identical content | name collision |
|---|---|---|---|---|---|---|---|
| weapons v2 | base image | 27,936 files / 10,067 groups | 2,157 | 2,157 | 0 / 0 / 0 | 0 | 0 |
| graffiti v2 | base image | 7,943 / 7,742 | 1,055 | 165 | 0 / 0 / 0 | 0 | 0 |
| vandalism v2 | source video | 244 / 16 | 69 / 5 | 51 / 5 | 0 / 0 / 0 | 0 | 0 |

Two figures that look wrong and are not:

- **weapons train holds 27,936 files across only 10,067 groups.** That ~2.8x
  ratio is Roboflow's augmentation, and it now lives entirely inside train,
  which is where it belongs. Val and test are strictly one file per base image.
- **graffiti test holds 165 files across 162 groups.** Three base images
  contribute two files each *within* test. Duplication inside one split is not
  leakage and costs nothing.

---

## Why the grouping rule differs by dataset

### Image datasets group by BASE IMAGE, not by file

Roboflow exports roughly 2.5–2.8 augmented copies of each source photograph. A
split made at *file* level therefore scatters an image's own augmented siblings
across train, val and test: the model is tested on rotations of its training
data.

This is not hypothetical here. Measured overlap in the original weapon corpus,
before the split was rebuilt:

```
train/val   99.4%
train/test  14.6%
val/test    12.0%
```

At 99.4%, the validation set was very nearly a copy of the training set, and
every early-stopping decision made against it was noise. The rebuild
([`build_weapon_split_v2.py`](../../EcoVisionImagesTraining/build_weapon_split_v2.py))
groups by base image and deals one copy per base into val and test, which is
what produces the 0/0/0 above.

**Any published metric from a file-level-split Roboflow export is inflated.**
That includes the previous weapon detector's numbers, which is why they were
withdrawn rather than adjusted.

### Video datasets group by SOURCE VIDEO, not by clip

Ten five-second clips cut from one UCF video are ten views of one act, on one
camera, in one location — not ten samples. Splitting by clip puts the same
driveway in train and test and reports an accuracy that measures memorisation of
a driveway.

This is the same mechanism the literature calls **scene bias** (Choi et al.,
NeurIPS 2019 — see [related_work_notes.md](related_work_notes.md) §7): action
models predict from the background rather than the action. Grouping by source
video is what forces the model to be judged on the act.

It is also brutal, and the cost is stated rather than hidden: it reduces the
vandalism class to **16 training scenes and 5 test scenes**.

### Negatives come from the same cameras as the positives

For the video datasets, negatives are cut from the **non-crime spans of the same
source videos**. If positives came from UCF and negatives from Davao street
capture, the model could separate the classes on the burnt-in DVR timestamp
instead of on the act — and would score extremely well while learning nothing.
Same-camera negatives make camera identity carry no information about the label.

---

## Limitations that survive a clean audit

Zero leakage is necessary, not sufficient. These remain true:

- **The vandalism test split is 5 scenes.** Any accuracy it produces is a
  measurement over five scenes. That belongs next to the number in the paper,
  not in a footnote. 26 sources is where the robbery class *started* working,
  not where it worked comfortably.
- **The graffiti test split is 165 images from 162 bases**, and is deliberately
  the *old* benchmark set, left untouched so v2's mAP50 is directly comparable
  to v1's. Swapping in an easier test set would manufacture an improvement.
- **Held-out is not the same as in-domain.** All of these splits are held out
  from training, and none of them are Philippine street cameras. Recall on the
  deployment cameras remains unmeasured, because that footage carries no labels.
  This is a real limitation and is stated plainly rather than papered over.
- **Threshold selection is a leakage channel too.** Choosing an operating point
  on the split you then report manufactures an improvement without any file
  moving between splits. Weapon thresholds are therefore swept on **val** and
  reported on **test**
  ([`sweep_weapon_thresholds.py`](../../EcoVisionImagesTraining/sweep_weapon_thresholds.py)).

---

## Reproducing the audit

```
python D:\EcoVisionImagesTraining\verify_all_leakage.py
```

Exit code 0 means clean. It re-hashes every file, so it takes a few minutes on
the weapon corpus; that cost is the point, since hashing is what catches
duplicate content under different names.
