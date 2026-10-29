# How every model was trained, and where its data came from

Compiled 2026-08-21, updated 2026-10-07 for the October models (violence ensemble,
`weapons_v3`, vandalism re-enabled, robbery at 0.85) and 2026-10-22 for the
Qwen3-VL-2B alarm double-check (§6b). One section per model: what it does, how it is implemented,
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

**Deployed (Oct 2026):** `weights/x3d_xs_violence_scene_ucabal.pt` **averaged with**
`weights/x3d_xs_violence_scene_ucabal_mined.pt`, person-crop confirmed, threshold
0.45, 2 checks in a row. (August: `x3d_xs_violence_scene_daynight.pt` — the
sections below on data and negatives describe how that checkpoint was built,
and `ucabal` is fine-tuned on top of the same recipe.)

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
| **UCA** (UCF-Crime Annotation, CVPR 2024) | event timestamps used to cut 821 fight + 835 calm clips from UCF-Crime (Oct) | see `CITATIONS.md` |

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

### October: three changes, each measured on the same footage

On 200 held-out fights from 200 different source videos spliced into street
footage, plus 2 h of four held-out real cameras:

| change | setting | fights caught | false alarms/hr |
|---|---|---|---|
| daynight, whole frame (Aug) | 0.50 × 3 | 75.0% | 5.0 |
| **person-crop confirmation** | 0.50 × 2 | 79.0% | 3.0 |
| **ucabal** — + 821 UCA-timestamped UCF fights and 835 same-venue calm clips | 0.50 × 2 | 86.5% | 2.0 |
| **ensemble** — averaged with a partner trained with 6 mined false alarms | 0.45 × 2 | **88.0%** | **2.0** |

- *Person-crop confirmation*: each check also scores crops (aspect-matched to
  whole frames) around the groups of people the pose model found; the value
  that is smoothed is min(whole-frame score, best crop over the last 3 checks).
- *Same-venue calm clips* matter as much as the fights: the unbalanced ucabal
  variant with only 279 calm clips caught 89.5% but at 13.0 false alarms/hr.
- *Hard-negative mining*: the partner model adds false alarms the detector
  raised on **training** cameras (never the held-out ones) as negatives. Alone
  it is worse (83.5%); averaged, the two make different mistakes.
- Per-clip on the 1,310-clip test split, ensemble at 0.45: accuracy 93.1%,
  recall 92.8%, precision 93.4%.

### Measured (August, daynight)

**95.0% of events detected** (38/40) on continuous spliced footage at threshold
0.50 / `consecutive_required=3`; **4.50 false alarms/hour** aggregate across
three real cameras. (A smaller, easier 40-fight night set — the 200-fight set
above replaced it.)

### What would improve it

- **Person-crop two-stage** — [CUE-Net (CVPR 2024W)](https://arxiv.org/abs/2404.18952) reaches SOTA by cropping around detected people specifically to handle distant subjects. **Done in October as a confirmation step** (above); using crops to *add* detections was also measured and gave the same recall at 3–6× the false alarms. Distant subjects remain the limit: at 15% person height the model detects 7/40; at 9%, **0/40**.
- **More examples of the misses** — of the 24 fights the ensemble misses, 13 are scored low by the model: ground fights, distant crowd scuffles, close-up or occluded views.
- **A vision-language verifier** — done (§6b): fights 94.0% at 0.4 false alarms/hr with it, on the GPU backend.
- **Scene-bias debiasing** — [Choi et al., NeurIPS 2019](https://proceedings.neurips.cc/paper_files/paper/2019/file/ab817c9349cf9c4f6877e1894a1faa00-Paper.pdf), adversarial scene loss + human-mask confusion loss.
- **Mean Teacher on unlabeled Philippine capture** — removes the current risk that footage *assumed* violence-free is trained as negative when a real incident occurs in it.

---

## 2. Robbery — X3D-XS, same architecture, different manifest

**Deployed:** `weights/x3d_xs_robbery_scene.pt` · threshold **0.85** (0.70 until 3 Oct) · 3 in a row · labelled experimental

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

**October:** at 0.70 it raised 12.0 false alarms/hr on the held-out cameras
(28/hr on the market camera), so the threshold went to 0.85: 0.5/hr, clip recall
54.2% → 39.8%. Retrains measured and rejected: 737 UCF robbery clips with fights
as negatives (13.3% recall, 17.5/hr), and **robbery v5** — started from the
violence model, with 1,824 Davao street negatives and 71 of its own mined false
alarms — 40.5/hr on the held-out cameras at 0.85 × 2. A single look at a whole
frame cannot separate a robbery from an ordinary hand-off in a busy market; a
vision-language verifier was the measured way forward, and is now in the app
(§6b): robbery taken from the fight detector's candidates and checked by two
questions reaches 59.4% of events at 0.6/hr, and this model is only the
fallback.

**Why it matters to the vandalism section:** robbery is the reference point for
how many scenes this architecture needs. It works at 26 training scenes. That
number is the yardstick used everywhere below.

---

## 3. Vandalism — X3D-XS, enabled (experimental) since October

**Status (Oct 2026):** enabled, labelled experimental. Deployed
`x3d_xs_vandalism_scene_v3.pt` (v3 added 700 Davao street clips as negatives,
split by camera: false alarms 125.25 → 21.75/hr), then threshold 0.70 (6.0/hr),
then **person-crop confirmation at 0.60 × 3: 87.1% of 31 held-out acts at
1.0 false alarm/hr.** The subsections below are the August history of how it
got there.

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

**Deployed (Oct 2026): `weapons_v3.pt` at imgsz 960**, gates gun 0.30 / knife
0.23. v2 (this section's August training run) was deployed on 21 Aug at 640; v3
is v2 fine-tuned at 960 with **7,500 synthetic street-camera frames** — real
gun/knife cut-outs from v2's *training* images pasted at the wrists of real
people in our own Davao footage at street-camera size (a handgun ≈ 12% of a
person's height), colour-matched and re-encoded as JPEG, plus 2,500 untouched
frames from the same cameras so "street CCTV look" cannot itself become the cue.
No evaluation camera was used as a background. Result, both at 960: false
weapons on 4 h of weapon-free street footage **909 → 112/hr**; street-size gun
recall 39.4% (14% for v2 as it ran at 640); large guns 89.6%; street-size knives
75.7% → 83.8%. Guns under 5% of the frame remain ~6%.

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

**Deployed 21 Aug as `vandalism_marks_v2.pt`** (it beat v1 on all four metrics below).

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

## 6b. Alarm double-check — Qwen3-VL-2B (prompted, not trained)

**What it does.** A second stage behind the detectors: each candidate alarm's
last 2 seconds (8 frames, longest side 448 px) go to Qwen3-VL-2B-Instruct with
a fixed question, and the alarm passes only if the score — log P("Yes") −
log P("No") for the first answer token, one forward pass, no text generation —
is at or above a cut-off. Because it removes false alarms, the detectors in
front of it run looser and miss less.

| task | candidate | question(s) | asked on |
|---|---|---|---|
| fight | violence ensemble, 0.25 × 1, re-checked every 7 s while it stays on | is anyone physically fighting, punching, kicking or attacking another person? | the whole frame |
| robbery | the fight ensemble's whole-frame score (before person-crop confirmation) | yes/no: is someone being robbed, held up, having belongings grabbed, or stealing; then multiple choice: A fight / B robbery, snatch or theft / C nothing — B must beat A | the whole frame |
| weapon | every ARMED THREAT alert from the weapon pipeline | is this person holding a gun or a knife? | a square crop around that person (pad 0.5) from full-resolution frames |

**Not trained — calibrated.** No weights were changed. The only fitted values
are the cut-offs, each chosen as a *keep rate on true alarms* (fight 98% — 97% on
the CPU backend, robbery 98% and 80%, weapon 98%) on half the source videos and scored on the
other half, so the reported numbers are cross-validated. The weapon cut was
fitted on 1,175 hand-labelled guns and knives held by a person in the weapon
model's own test set.

**Two backends, one model.** GPU: transformers fp16 (`weights/qwen3_vl_2b/`),
~4.2 GB VRAM, ~7 s per check. CPU: llama.cpp `llama-server` with a 4-bit GGUF
(`weights/qwen3_vl_2b_gguf/`), ~2.1 GB RAM, ~15 s per check. They score on
different scales, so each task has its own CPU cut-off (`cut_cpu`), fitted at
the same keep rates; the weapon task has no CPU cut-off yet, so on the CPU
backend weapon alerts go out unverified. `backend: "auto"` uses the GPU when
4.3 GB of VRAM is free after the detectors load, else the CPU when ~2.6 GB of
RAM is free, else neither (strict detector settings); machines with no CUDA
GPU never load it.

**Measured (5 h held-out cameras, 200 held-out fights, 133 robbery events).**

| | without | with (GPU) | with (CPU) |
|---|---|---|---|
| fights caught / false alarms per hour | 88.0% / 1.4 | **94.0% / 0.4** | 91.0% / 0.2 |
| robbery events labelled robbery / per hour | 8.3% / 1.4 | **59.4% / 0.6** | 60.9% / 0.8 |
| held-out fights also labelled robbery | 25% | 20.5% | 22.7% |
| ARMED THREAT false alerts per hour | 2.8 | **1.4** (97.9% of real weapons kept) | 2.8 (unverified) |

**Rejected on measurement:** Qwen3-VL-4B (no better, ~32 s per alarm), NVIDIA
Cosmos-Embed1 as the verifier (slightly worse), two verifiers combined (no
gain), and a single yes/no robbery question (passed 86% of fights as robbery).

**Licence and source:** Apache-2.0, Qwen Team; llama.cpp MIT — see
`CITATIONS.md`. Code: `maincode/alarm_verifier.py`; experiments in the training
repo, `experiments/crop_recheck/` and `experiments/weapon_alerts/`.

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
| **Look before training** | every new dataset (mined clips, synthetic weapons, UCA cuts) goes on a contact sheet and is checked by eye — labels and folder names have been wrong before |
| **Measure the way it runs** | false alarms/hr on held-out cameras and events caught in spliced continuous footage, through `detection_rules.py` — the code the app runs |
| **Mine hard negatives only from training cameras** | mining on held-out cameras would quietly turn the test into training data |

---

## 8. Improvement priorities

Updated Oct 2026. Done since August: weapon thresholds re-swept and deployed
(0.30 / 0.23); graffiti v2 deployed; weapon `imgsz` resolved (960, v3);
person-crop confirmation for violence and vandalism.

1. ~~Vision-language verifier for alarms~~ — **in the app since 7 Oct** (§6b).
   What remains: a CPU cut-off for the weapon question, and a live end-to-end
   test of the weapon check in the running app.
2. **More held-out camera hours.** Two hours made every false-alarm rate coarse
   (one alarm = 0.5/hr); 6 Oct recordings brought it to 5 hours. More, including
   night footage, would tighten it further.
3. **Violence misses**: ground fights, distant crowds, close-up/occluded views;
   the "nobody found" fallback (a whole-frame score of 0.8+ counts on its own
   when the pose model has found no one for 6 checks) was confirmed on 5 h and
   deployed 7 Oct: 88.0% → 90.0% of fights at the same 1.4/hr.
4. **Film ~20 vandalism locations.** Still the only lever for the act itself —
   public data is exhausted at four graffiti scenes.
5. **Request MSAD access.** Maps to the property-damage class.
6. **Tiny guns** (under ~5% of the frame) need a higher-resolution or P2-head
   detector, which does not fit live on a GTX 1660 alongside the other models.
7. **RTFM-style top-k MIL** if weakly-supervised training is revisited — [84.30% AUC on UCF-Crime](https://arxiv.org/abs/2101.10030) versus the MIL formulation this project measured and rejected.
