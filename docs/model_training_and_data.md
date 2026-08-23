# How every model was trained, and where its data came from

Compiled 2026-08-21. One section per model: what it does, how it is implemented,
which datasets it was built from with links, the exact training configuration,
what it measures, and what would improve it.

This is the RRL/methodology reference. Every number here is measured in this
repository; nothing is estimated. Where a figure is unverified it says so.

**Companion documents**
- [`data_splits_and_leakage.md`](data_splits_and_leakage.md) — how every split is grouped and why the numbers below are quotable at all
- [`related_work_notes.md`](related_work_notes.md) — the literature these choices were checked against
- [`detection_performance_report.md`](detection_performance_report.md) — full TP/FP/TN/FN tables

---

## 0. The two model families, and why both exist

| | video classifier | object detector |
|---|---|---|
| architecture | **X3D-XS** (3D CNN) | **YOLO11s** |
| input | 13 frames at 160×160 | one frame |
| answers | *is this act happening?* | *is this object present?* |
| used for | violence, robbery, vandalism | weapons, graffiti marks, pose |

The split is not stylistic. **A weapon is visible in a single frame; violence
only exists across frames.** A still image of a raised fist is ambiguous;
thirteen frames of one are not. Conversely a gun is fully described by one
frame, and asking a video model to find it wastes twelve frames of computation
on a question already answered by the first.

### Why separate models rather than one multi-class head

Four reasons, each measured or reasoned rather than assumed:

1. **A robbery involving assault is genuinely both.** A softmax forces
   probability mass to split between the two classes on exactly the clips that
   matter most, pushing both under threshold. Joining them would make violence
   detection *worse* on violent robberies.
2. **Class sizes differ by 20×.** Violence has ~2,805 training clips from
   hundreds of sources; property crime has ~120 from ~15. Joint training either
   ignores the small class or, oversampled, memorises fifteen driveways.
3. **One softmax is one decision surface.** Every measurement in this project
   says the operating point is the whole game — robbery's threshold could not be
   moved without moving violence's.
4. **A bad model can be switched off in config** rather than retrained out of
   shared weights. Vandalism is currently switched off; that is only cheap
   because it is its own model.

---

## 1. Violence — X3D-XS scene classifier

**Deployed:** `weights/x3d_xs_violence_scene_daynight.pt` (18 Aug)

### Architecture and implementation

[**X3D**](https://arxiv.org/abs/2004.04730) (Feichtenhofer, CVPR 2020) expands a
2D image network along temporal, spatial, width and depth axes progressively,
keeping the smallest model that still performs. **XS is the smallest variant** —
3,010,271 parameters — chosen because the deployment target is a single GTX 1660
SUPER that must also run pose and two detectors concurrently.

```
input        13 frames @ 160×160, whole frame (not person-cropped)
normalize    (x/255 − 0.45) / 0.225
head         softmax
loss         NLLLoss(log(p))
transfer     init from a violence checkpoint, unfreeze last 2 blocks,
             backbone_lr_mult = 0.1
```

**Inference contract, and why it is written into the checkpoint's sidecar:** the
head already applies softmax, so the model outputs *probabilities*. Applying
softmax again at inference — a natural mistake, since most PyTorch classifiers
emit logits — flattens the distribution and caps achievable accuracy. Every
`.meta.json` carries `inference_contract: use model output directly; do not apply
softmax` for that reason.

### Data

| dataset | role | link |
|---|---|---|
| **RWF-2000** | 2,000 surveillance clips, violent/non-violent | [github.com/mchengny/RWF2000-…](https://github.com/mchengny/RWF2000-Video-Database-for-Violence-Detection) |
| **SCVD** (Smart-City CCTV Violence Detection) | CCTV violence + weaponised violence | [kaggle.com/…/smartcity-cctv-violence-detection-dataset-scvd](https://www.kaggle.com/datasets/toluwaniaremu/smartcity-cctv-violence-detection-dataset-scvd) |
| **UCF-Crime** | untrimmed real CCTV, 13 anomaly classes | [crcv.ucf.edu/projects/real-world](https://www.crcv.ucf.edu/projects/real-world/) |
| **CCTV-Fights** | additional fight footage | Kaggle |
| **Davao street capture** | in-domain negatives, 26 cameras | captured for this project |

**Licensing note that belongs in the paper:** RWF-2000 may not be modified or
redistributed without SMIIP Lab approval and is non-commercial. SCVD accompanies
[SSIVD-Net (arXiv 2207.12850)](https://arxiv.org/abs/2207.12850).

### The negatives decision, which mattered more than the architecture

Three checkpoints were trained differing **only** in their negative set:

| checkpoint | negatives | real-camera alarms/hr |
|---|---|---|
| `ucf_neg_motion` | UCF normals, motion-filtered | 41.0 |
| `corpus_neg` | 4,825 negatives from 26 daytime cameras | 32.0 |
| **`daynight`** (deployed) | day + night capture | **4.50** |

Per-clip recall was *unchanged* at 95.0% across the daynight swap while alarms
fell 12.75 → 4.50/hr. **Not a uniform win:** the flyover camera improved 45 → 6
alarms/hr, but Lyn's Restaurant got *worse*, 6 → 12/hr. The aggregate hides that
spread, which is why the per-camera breakdown lives in the checkpoint sidecar.

### Measured

**95.0% of events detected** (38/40) on continuous spliced footage at threshold
0.50 / `consecutive_required=3`; **4.50 false alarms/hour** aggregate across
three real cameras.

### What would improve it

- **Person-crop two-stage** — [CUE-Net (CVPR 2024W)](https://arxiv.org/abs/2404.18952) reaches SOTA by cropping around detected people specifically to handle distant subjects. Measured here: at 15% person height the model detects 7/40; at 9%, **0/40**. A wide city camera puts a person at 6–12%.
- **Scene-bias debiasing** — [Choi et al., NeurIPS 2019](https://proceedings.neurips.cc/paper_files/paper/2019/file/ab817c9349cf9c4f6877e1894a1faa00-Paper.pdf), adversarial scene loss + human-mask confusion loss.
- **Mean Teacher on unlabeled Philippine capture** — removes the current risk that footage *assumed* violence-free is trained as negative when a real incident occurs in it.

---

## 2. Robbery — X3D-XS, same architecture, different manifest

**Deployed:** `weights/x3d_xs_robbery_scene.pt` · threshold 0.7

Identical architecture and training recipe, initialised from the violence
checkpoint (`corpus_neg`), `unfreeze_blocks=2`. **26 train / 9 val / 8 test
scenes.**

**Data:** UCF-Crime (Burglary, Robbery, Shoplifting, Stealing) plus
**CamNuvem** bank-robbery footage. Negatives are the non-crime spans of the
*same* videos.

**Measured at 0.7:** accuracy 84.2%, recall 65.3%, precision 86.5%, FPR 5.6%.

**Honest reading:** recall 65.3% means it misses roughly one robbery in three,
and the figure comes from 139 clips drawn from **eight** source videos — not 139
independent samples.

**Why it matters to the vandalism section:** robbery is the reference point for
how many scenes this architecture needs. It works at 26 training scenes. That
number is the yardstick used everywhere below.

---

## 3. Vandalism — X3D-XS, currently disabled, retraining

**Status:** `detection.vandalism.enabled = false`

### The rejected model, recorded rather than deleted

`x3d_xs_vandalism_scene.pt`, trained on **11 scenes**:

| threshold | accuracy | recall | precision | FPR |
|---|---|---|---|---|
| 0.5 | 70.3% | 86.2% | 78.1% | **87.5%** |
| 0.9 | 73.0% | 75.9% | 88.0% | 37.5% |

**Rejected because 70.3% is below the 78.4% obtained by labelling every clip
vandalism.** The model carried negative information. It fired on 7 of 8 normal
clips. The test split was three scenes.

> The 70.3% figure belongs to *this* model. It has been misattributed to the
> weapon detector in working notes; the weapon baseline is 79.7% recall on the
> final epoch-98 checkpoint (74.3% on epoch 68).

### The rule-based route, also measured

`score_vandalism()` requires four conditions: a wrist near a static target,
velocity in a "sweep" band, sustained across frames, and no other person nearby.

Instrumented per condition on 48 graffiti clips:

| target-box source | frames with target | recall |
|---|---|---|
| original `sign` class | **0 / 3,600** | **0.0%** |
| graffiti v1 | 732 | 6.2% |
| graffiti v2 | 849 | 8.3% |

The gate was **structurally impassable**, not badly tuned — `weapon_signs.pt`'s
`Sign` class detects road signs, not walls. Replacing it with a purpose-trained
graffiti detector fixed condition 1 and proved the diagnosis, but recall stayed
at 8.3%.

Per-condition counts show why: 3,231 person-frames fail condition 1 *after* a
mark is already detected somewhere in frame. **The rule keys on the residue of
the act; while someone is spraying, the mark does not exist yet.**

### The change-detection prototype

Inverted the question — detect a mark *appearing* in a grid cell that was
reliably empty during a baseline. Reached **3 of 4 events in-span**, but
false-alarmed on **all four** held-out cameras. A persistence check (a real tag
stays; an occluding van leaves) reduced alarms and recall together. Also
disabled.

### The retrain — completed 21 Aug

Scene count raised **11 → 26 sources** by annotating 8 further UCF scenes off
24-frame filmstrips and extracting 90 additional clips. Manifest: 364 clips,
train 244 / val 69 / test 51, **0 group-level leakage**. Early-stopped at epoch 9
of 30 (best val 62.3% at epoch 3).

**Held-out test split (51 clips, 5 scenes), threshold 0.5:**

| | old (11 scenes) | **new (26 sources)** |
|---|---|---|
| accuracy | 70.3% | **82.4%** |
| majority baseline | 78.4% | 62.7% |
| **vs baseline** | **−8.1** | **+19.7** |
| recall | 86.2% | **93.8%** |
| FPR | 87.5% | **36.8%** |

The rejected model's own metadata predicted this — *"Scene count, not
architecture"* — and the prediction held. **Still not deployed:** 36.8% FPR
against deployed robbery's 5.6%. Train accuracy reached 89.3% against ~60%
validation, so the model is memorising its 16 training scenes; more epochs will
not help, more scenes will. Test split is 5 scenes and 19 negatives, so the FPR
is a small-denominator figure. Sidecar:
`x3d_xs_violence_best_vandalism_v2.pt.meta.json`.

**One decision against the original plan:** two separate models (graffiti,
property damage) were planned. Graffiti has **four** scenes; the model that
failed had eleven. Splitting produces two failures instead of one chance, so the
scenes are pooled.

### What would improve it

**Scene count, not architecture.** Confirmed by survey — see
[`related_work_notes.md` §5](related_work_notes.md): **no public video dataset
of the graffiti act exists.** NWPU Campus (28 classes) and UBnormal (22) contain
none; XD-Violence has six unrelated classes. Only
[MSAD (NeurIPS 2024)](https://msad-dataset.github.io/) adds vandalism, and its
subtypes are glass and doors — property destruction, not marking.

So: [`vandalism_data_collection.md`](vandalism_data_collection.md), ~20 filmed
locations. That is the only lever that exists.

---

## 4. Weapons — YOLO11s detector

**Trained, not yet deployed.** Config still points at the old `weapon_signs.pt`.

### Data and the class design

| source | contributes | note |
|---|---|---|
| **Weapon-Detection.v8** | gun, knife | `person_with_mask` dropped |
| **Robbery Activity.v7i** | **phone** (1,892 boxes) | metal-detector and thermal-gun labels dropped, images kept |

Earlier lineage (`weapon_signs.pt`) also merged *CCTV Knife Detection v1i*, *gun
and knife detection v1i*, *gun detection v4i*, *Gun-cctv-detection v1i*, *knife
v1i*, *knife-dataset v2i*, and *Traffic and Road Signs v1i* — all Roboflow
Universe, searchable by exact name.

**`phone` is a class, not background — and this is the key design decision.** The
old model's vocabulary was {Gun, Knife, Sign}. Anything roughly gun-shaped *had*
to be emitted as one of those: it produced **47 Gun detections in 30 minutes of a
street with no guns**, and 1,786 detections/hour on barbershop footage. A cheap
fix — vetoing detections overlapping a COCO "cell phone" box — was measured and
**failed**: only 1 of 310 barbershop detections overlapped any benign COCO
object. COCO does not see the scissors and razors either.

So the fix went into the training data. Promoting `phone` to a real class gives
*positive* supervision — the model learns what a phone **is**, not merely that
one region is not a gun. `main.py` then drops phone detections, making a
correctly-detected phone a true negative that raises no alert.

**The `Sign` class was removed.** It fired **0 times in 4,800 measured frames**
(it detects road signs, not walls) while inflating the old model's headline
recall to 88.7%.

### Training configuration

```
model      yolo11s.pt          optimizer  AdamW
imgsz      640                 lr0        0.001
batch      8                   warmup     3 epochs
workers    2                   epochs     120, patience 25
```

`cv2.setNumThreads(0)` **before** the ultralytics import — OpenCV otherwise
spawns a competing thread pool inside every DataLoader worker, which is what
raised `_ArrayMemoryError: Unable to allocate 1.17 MiB` and killed this run
twice.

### Measured (final epoch-98 checkpoint, on 2,157 held-out images)

At the inherited thresholds (gun 0.52 / knife 0.45), final epoch-98 checkpoint:
**79.7% recall · 85.0% accuracy · 99.6% precision · 0.9% FPR** (TP 1255 / FP 5 /
TN 578 / FN 319). The epoch-68 checkpoint measured 74.3% / 81.0% on the same
split; both were reproduced independently by two scripts.

Thresholds swept on **val**, reported on **test**:

| FPR budget | gun / knife | test recall | test FPR |
|---|---|---|---|
| ≤1% | 0.45 / 0.43 | 82.8% | 1.5% |
| ≤2% | 0.36 / 0.23 | 87.2% | 2.7% |
| **≤3%** | **0.30 / 0.23** | **89.0%** | **3.1%** |
| ≤5% | 0.17 / 0.20 | 91.9% | 4.6% |
| ≤8% | 0.09 / 0.16 | 94.3% | 7.5% |

**+9.3 points of recall for 2.2 points of FPR.** The deployed 0.52/0.45 were
inherited from a *different, worse* model that needed high thresholds to
suppress its own false positives.

Image-level FPR overstates live behaviour: `main.py` requires
`ARMED_CONFIRM_FRAMES=4`, a 3-of-8 evidence window, and a static-object filter
that removed 97.4% / 81.0% / 78.1% of false weapons on three real feeds.

### Status and what would improve it

The run **crashed at epoch 70 of 120** (system RAM exhaustion) and was resumed
21 Aug, reaching **epoch 100** before a power interruption stopped it. By then it
had converged — mAP50 flat across epochs 96–100, recall pinned at 0.745–0.747 —
so it was not resumed a second time.

**Final checkpoint: epoch 98**, mAP50 0.8360 / mAP50-95 0.5279 / P 0.863 /
R 0.747. At the deployed thresholds on the test split this gives **79.7% recall
/ 85.0% accuracy / 99.6% precision / 0.9% FPR** (TP 1255 / FP 5 / TN 578 /
FN 319) — **+5.4 points of recall over epoch 68** for no change but finishing the
training.

Re-swept thresholds (val-selected, test-reported): **≤3% FPR → gun 0.30 /
knife 0.23 → 89.0% recall**; ≤5% → 0.17 / 0.20 → 91.9%. Note a non-uniform
effect: at permissive thresholds epoch 98 is marginally *worse* than epoch 68
(91.9% vs 92.9% at ≤5%). The extra epochs sharpened confident detections without
improving the low-confidence tail.

- Re-sweep thresholds against the **final** checkpoint — reusing the cached ones would repeat the exact "inherited from a model that no longer exists" mistake.
- Resolve `imgsz`: trained at 640, production runs 416. Measure the cost before choosing.
- CCTV-scale data. Median box is 36.1% of frame (p90 = 92%) — close-up product photography, not a street camera.

---

## 5. Graffiti marks — YOLO11s detector

**Trained 21 Aug, beats the deployed model, not yet deployed.**

### Data

| source | images | link |
|---|---|---|
| **17K-Graffiti** (Universe mirror) | 7,636 | [universe.roboflow.com/detr-2bavb/17k-graffiti](https://universe.roboflow.com/detr-2bavb/17k-graffiti) |
| civic-issues corpus | 1,527 | prior project data |

The full 17K-Graffiti corpus is [Zenodo record 5899631](https://zenodo.org/record/5899631) (73.1 GB), restricted to academic use behind an access request; the Universe mirror is a directly-downloadable subset.

**Combined rather than replaced, for a measured reason:**

```
17k-graffiti      median box 18.54% of frame,  7.9% of boxes under 2%
civic-issues      median box  4.88% of frame, 29.7% of boxes under 2%
```

The new corpus is 6× larger but its boxes are **4× bigger** — photographer-framed
shots *of* graffiti. The small civic-issues set carries the small-scale instances
that resemble a CCTV frame. Training on 17k alone would repeat the weapon
detector's mistake exactly.

### Training configuration

```
model yolo11s.pt   imgsz 416   batch 8   epochs 100, patience 20
AdamW, lr0 0.001, warmup 3, scale=0.5, mosaic=1.0, fliplr=0.5
```

**imgsz 416 deliberately**, matching `VANDAL_MARK_IMGSZ` in `main.py`. Training
at a resolution production does not use means the engine, the benchmark and the
deployed model all disagree.

### Measured — same untouched 165-image benchmark, both models scored in one run

| | mAP50 | mAP50-95 | precision | recall |
|---|---|---|---|---|
| v1 (deployed) | 0.718 | 0.472 | 0.794 | 0.602 |
| **v2 (epoch 72)** | **0.734** | **0.485** | **0.804** | **0.646** |

A **strict improvement** — no metric traded against another; largest gain is
recall (+7.2% relative). Training ran 85 epochs before early-stopping.

**Honest scale of the win:** 6.75× more data bought +1.6 points of mAP50. The
bottleneck is **scale-matched** data, not quantity.

---

## 6. Pose — YOLO11s-pose

Off-the-shelf `yolo11s-pose.pt`, not retrained. Supplies 17 COCO keypoints for
the rule-based logic (grip assignment, strike scoring, vandalism sweep) and
person boxes for tracking via **BoT-SORT**/**ByteTrack**.

Runs at `POSE_IMGSZ = 416`. **Known limitation:** a 1280×720 frame at 416 turns a
65px person into ~21px, below reliable detection. This is the first blocker on
any two-stage person-crop architecture and is a measurement, not an assumption.

---

## 7. Cross-cutting practices

Applied to every model above; each exists because its absence caused a
withdrawn number.

| practice | why |
|---|---|
| **Group-level splits** | base image for detectors, source video for clips. File-level splitting measured 99.4% / 14.6% / 12.0% train-val-test overlap. |
| **Same-camera negatives** | otherwise the model separates classes on the DVR timestamp, not the act |
| **Thresholds swept on val, reported on test** | selecting on the reported split manufactures improvement with no file moving |
| **`imgsz` matched to runtime** | `optimize_weights.py` reads the constants out of `main.py`, not the checkpoint |
| **`.meta.json` beside every checkpoint** | records manifest, scene counts, caveats and the inference contract |
| **Rejected models kept** | `x3d_xs_vandalism_scene.pt` ships with `status: REJECTED FOR DEPLOYMENT` and its reason |

---

## 8. Improvement priorities

Cheapest and highest-certainty first.

1. **Re-sweep weapon thresholds after the resume completes.** +15 points of recall already demonstrated; costs one inference pass.
2. **Deploy graffiti v2.** Strictly better on the same benchmark; the work is done.
3. **Resolve weapon `imgsz` 416 vs 640** by measurement, then rebuild the TensorRT engine.
4. **Film ~20 vandalism locations.** The only lever that exists — public data is exhausted at four graffiti scenes.
5. **Request MSAD access.** Maps to the property-damage class; will not arrive before defense, but makes future work concrete.
6. **Two-stage person-crop for violence**, gated on measuring whether pose resolves people at 9–12% frame height at an affordable `imgsz`.
7. **RTFM-style top-k MIL** if weakly-supervised training is revisited — [84.30% AUC on UCF-Crime](https://arxiv.org/abs/2101.10030) versus the MIL formulation this project measured and rejected.
