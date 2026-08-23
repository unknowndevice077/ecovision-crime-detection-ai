# EcoVision Sentinel: Building and Honestly Measuring a Real-Time CCTV Crime-Detection System

**A consolidated technical report**
Compiled 2026-08-21 · single-GPU (GTX 1660 SUPER) deployment target

---

> **What this document is.** Everything in `docs/` assembled into one narrative,
> written as a research report rather than as notes. The separate documents
> remain authoritative for their own subjects and are cross-referenced
> throughout; nothing here replaces them.
>
> **A note on tone.** This report includes the things that did not work, in
> roughly the same detail as the things that did. That is deliberate. Several of
> this project's most useful results are negative ones, and two of its published
> numbers had to be withdrawn after measurement exposed them. A report that
> recorded only the successes would misrepresent both the work and the system's
> actual reliability.

---

## Abstract

EcoVision Sentinel is a real-time crime-detection system for municipal CCTV,
built to run on a single consumer GPU. It detects four things by two distinct
mechanisms: **violence, robbery and vandalism** as *acts*, using an X3D-XS video
classifier over 13-frame clips; and **weapons and graffiti marks** as *objects*,
using YOLO11s single-frame detectors. Pose estimation supplies keypoints to
rule-based logic that links objects to people.

The deployed violence detector identifies **95.0% of events** on continuous
footage at **4.50 false alarms per hour** across three real cameras. Robbery
reaches **84.2% accuracy at 65.3% recall**. A rebuilt, leakage-free weapon
detector reaches **89.0% recall at 3.1% false-positive rate** after threshold
selection performed on validation and reported on test.

**The vandalism class was disabled for most of this project's life and is now
deployed**, and the path there is the report's most instructive result. Four
routes failed and were measured rather than assumed: a rule whose gate fired 0
times in 3,600 frames, a detector-only signal at the majority baseline, a
change-detection prototype that alarmed on every camera, and a trained model
scoring *below* its own always-guess baseline. Two changes fixed it, neither of
them architectural: raising the scene count 11 to 26 made the class carry
positive information, and adding **real deployment-camera footage as negatives**
cut false alarms from **125.25 to 6.75 per hour** -- an 18.6x reduction that
brings it within reach of the violence detector's accepted 4.50/hr. A survey of
every public video anomaly dataset establishes that **no dataset of the graffiti
act exists**, so the remaining ceiling is field-level rather than a gap in
collection.

The report also documents three measurement errors found and corrected during
development, each of which had inflated a published figure.

---

## 1. Introduction

### 1.1 The problem

Municipal CCTV is overwhelmingly *retrospective*. Cameras record; humans review
footage after an incident is reported. The operator-to-camera ratio makes live
monitoring of every feed impossible, and attention degrades within minutes of
watching an uneventful screen.

The target deployment is a streetlight-mounted camera in Davao City. That
constrains everything: outdoor, fixed, wide field of view, variable light, and —
critically — **one consumer GPU for the whole pipeline**.

### 1.2 What "working" has to mean here

Both error types are costly, and in opposite ways:

- **A missed assault** is the failure the system exists to prevent.
- **Alert fatigue** destroys the system just as completely, only more slowly. An
  operator who has dismissed forty false alarms will dismiss the forty-first
  without looking.

So no single number characterises this system. Every claim in §7 is an operating
point on a measured curve, not a headline accuracy.

### 1.3 The measurement problem, stated first

The most important limitation is not a model's. **No labelled incident has ever
been recorded on the deployment cameras**, because none has occurred while
recording. Recall on the actual target cameras is therefore *unmeasurable*, not
merely unmeasured — violence cannot be labelled in footage containing none.

Everything reported here is measured on held-out benchmark data and on real
Davao footage used only for false-alarm rates. This follows from the problem
rather than from a gap in the work, and it is stated plainly rather than
papered over.

---

## 2. Related work

Full notes with per-citation verification status: [`related_work_notes.md`](related_work_notes.md).

### 2.1 Weakly-supervised video anomaly detection

[**Sultani, Chen & Shah (CVPR 2018)**](https://openaccess.thecvf.com/content_cvpr_2018/papers/Sultani_Real-World_Anomaly_Detection_CVPR_2018_paper.pdf)
introduced both the UCF-Crime dataset and the multiple-instance-learning (MIL)
formulation: videos are bags, segments are instances, trained with a ranking
hinge loss plus sparsity and temporal-smoothness terms.

[**RTFM (Tian et al., ICCV 2021)**](https://arxiv.org/abs/2101.10030) replaces
MIL's single-max selection with a **top-k feature-magnitude** criterion,
addressing MIL's central weakness: only the argmax segment receives gradient, so
one mislabelled peak dominates learning. Reports **84.30% AUC on UCF-Crime**.

This matters to §8.1: a feature-cached MIL head was built here, its first
version's leakage found and fixed, and it was then rejected on measured merit.
RTFM is the reason that rejection is recorded as *"MIL as originally formulated
underperformed"* and not *"MIL does not work"* — the known fix for its failure
mode was never attempted.

### 2.2 Scene bias

[**Choi et al., "Why Can't I Dance in the Mall?" (NeurIPS 2019)**](https://proceedings.neurips.cc/paper_files/paper/2019/file/ab817c9349cf9c4f6877e1894a1faa00-Paper.pdf)
names the failure this project kept encountering: action models predict from
*scene context* rather than from the action — basketball from the court, not the
movement. Their fix combines an adversarial scene-type loss with a human-mask
confusion loss.

This is the published mechanism behind two decisions in §4.4: same-camera
negatives, and source-video-level splitting.

### 2.3 Spatial cropping for distant subjects

[**CUE-Net (Senadeera et al., CVPR 2024W)**](https://arxiv.org/abs/2404.18952),
SOTA on RWF-2000 and RLVS, performs violence detection by **spatially cropping
around detected people**, explicitly to handle "distant or partially obscured
subjects." The state of the art solves the small-person problem by cropping to
people, not by classifying whole wide frames — directly relevant to §9.2.

### 2.4 Calibrating expectations

Weakly-supervised VAD on real untrimmed CCTV currently sits near **91.6% AUC on
UCF-Crime** ([GS-MoE, 2025](https://arxiv.org/abs/2508.06318)). Published SOTA on
*real* CCTV is ~90% AUC — not the 95–97% this project reports on trimmed
benchmark clips. **Those numbers are not comparable**, and the gap between them
is roughly the benchmark-vs-real gap measured independently here.

### 2.5 The vandalism data survey

Conducted 2026-08-21. Class lists were read directly, not inferred from titles.

| dataset | vandalism class? | finding |
|---|---|---|
| [UCF-Crime](https://www.crcv.ucf.edu/projects/real-world/) | **yes** — 50 Vandalism + 53 Arson | the source used here |
| [MSAD](https://msad-dataset.github.io/) (NeurIPS 2024) | **yes** — "vandalizing glass, doors, structures" | property destruction, not marking |
| [NWPU Campus](https://campusvaa.github.io/) (CVPR 2023) | **no** — none of 28 classes | nearest: "kicking trash can" |
| [UBnormal](https://arxiv.org/abs/2111.08644) (CVPR 2022) | **no** — none of 22 classes | synthetic regardless |
| [XD-Violence](https://arxiv.org/abs/2007.04687) (ECCV 2020) | **no** — 6 classes | unrelated taxonomy |

> **Finding: there is no public video dataset of the graffiti act.** UCF-Crime is
> the only source of vandalism video of any kind. The four graffiti scenes
> hand-annotated for this project are close to the world's available supply.

---

## 3. System architecture

### 3.1 Two model families, and why both are necessary

| | video classifier | object detector |
|---|---|---|
| architecture | **X3D-XS** (3D CNN, 3,010,271 params) | **YOLO11s** |
| input | 13 frames @ 160×160 | one frame |
| question | *is this act happening?* | *is this object present?* |
| classes | violence, robbery, vandalism | weapons, graffiti marks, pose |

**A weapon is visible in a single frame; violence exists only across frames.** A
still image of a raised fist is ambiguous; thirteen frames of one are not.
Conversely a gun is fully described by one frame, so asking a video model to find
it spends twelve frames of computation on an already-answered question.

### 3.2 Why separate models rather than one multi-class head

1. **A robbery involving assault is genuinely both.** A softmax splits
   probability mass across both classes on exactly the clips that matter most,
   pushing both under threshold — making violence detection *worse* on violent
   robberies.
2. **Class sizes differ by ~20×.** Violence has ~2,805 training clips from
   hundreds of sources; property crime has ~120 from ~15. Joint training either
   ignores the small class or, oversampled, memorises fifteen driveways.
3. **One softmax is one decision surface.** Robbery's threshold could not move
   without moving violence's — and every result here says the operating point is
   the whole game.
4. **A bad model can be switched off in config** rather than retrained out of
   shared weights. Vandalism is currently off; that is only cheap because it is
   its own model.

### 3.3 Runtime pipeline

```
frame ─┬─► YOLO11s-pose (imgsz 416) ──► keypoints + person boxes ──► BoT-SORT tracking
       │                                          │
       │                                          └──► rule logic: grip assignment,
       │                                               strike scoring, sweep detection
       ├─► YOLO11s weapons (imgsz 416) ──► per-class thresholds ──► static-object filter
       ├─► YOLO11s graffiti (imgsz 416) ──► static_targets
       └─► X3D-XS scene classifier (every 15 frames, 13-frame buffer)
                    │
                    └──► EMA smoothing ──► consecutive-frame confirmation ──► alert
```

**Temporal gating is where per-frame noise becomes an operator-grade alert.**
`ARMED_CONFIRM_FRAMES = 4`, a 3-of-8 evidence window, `ALERT_COOLDOWN_FRAMES =
200`, and a static-object filter that removed **97.4% / 81.0% / 78.1%** of false
weapon detections on three real feeds. Per-image false-positive rates therefore
substantially *overstate* live alarm behaviour — a point that recurs in §7.4.

---

## 4. Data

Full provenance with links and licences: [`model_training_and_data.md`](model_training_and_data.md)
and [`datasets_used.txt`](../../EcoVisionImagesTraining/datasets_used.txt).

### 4.1 Sources

| model | datasets |
|---|---|
| **Violence** | [RWF-2000](https://github.com/mchengny/RWF2000-Video-Database-for-Violence-Detection), [SCVD](https://www.kaggle.com/datasets/toluwaniaremu/smartcity-cctv-violence-detection-dataset-scvd), [UCF-Crime](https://www.crcv.ucf.edu/projects/real-world/), CCTV-Fights, Davao capture (26 cameras) |
| **Robbery** | UCF-Crime (Burglary/Robbery/Shoplifting/Stealing), CamNuvem |
| **Vandalism** | UCF-Crime (Vandalism, Arson) — 19 scenes hand-annotated |
| **Weapons** | Weapon-Detection.v8, Robbery Activity.v7i (Roboflow) |
| **Graffiti** | [17K-Graffiti mirror](https://universe.roboflow.com/detr-2bavb/17k-graffiti), civic-issues corpus |

**Licence note for the paper:** RWF-2000 may not be modified or redistributed
without SMIIP Lab approval and is non-commercial.

### 4.2 Annotation: reading spans by eye, and why

UCF ships frame-accurate spans for only **5 of its 50** Vandalism videos. Two
automatic alternatives were tested against ground truth and **both failed**:

| method | result |
|---|---|
| coverage heuristic | median anomaly coverage 0.32 — an upper bound |
| motion ranking | crime is the busier half in 11 of 21 sources — a coin flip |

So 19 scenes were annotated by eye off 24-frame timestamped filmstrips, with
spans set deliberately **wide**: a positive clip carrying a second of run-up is
far less damaging than one that misses the act. Every exclusion is recorded with
its reason — indoor, multi-camera, act not identifiable — so the scope is
auditable rather than silent.

### 4.3 The augmented-duplicate leakage, and a withdrawn result

Roboflow exports ship roughly **2.5–2.8 augmented copies per source image**. A
split made at *file* level scatters an image's own augmented siblings across
train, val and test. Measured in the original weapon corpus:

```
train/val   99.4%      train/test  14.6%      val/test  12.0%
```

At 99.4%, validation was very nearly a copy of training, and **every
early-stopping decision made against it was noise.** The rebuild groups by base
image; overlap is now 0/0/0 on all three pairs.

> **Any published metric from a file-level-split Roboflow export is inflated.**
> The previous weapon detector's numbers were withdrawn rather than adjusted.

### 4.4 Two rules that make the numbers mean anything

**Group by source video, never by clip.** Ten five-second clips from one UCF
video are ten views of one act, on one camera, in one location. Splitting by clip
puts the same driveway in train and test and measures memorisation of a
driveway. This is scene bias (§2.2), and enforcing it is brutal: it reduces the
vandalism class to 16 training and 5 test scenes.

**Negatives come from the same cameras as positives.** If positives came from UCF
and negatives from Davao capture, the model could separate classes on the
burnt-in DVR timestamp instead of the act — scoring extremely well while learning
nothing. Negatives are cut from the non-crime spans of the *same* videos.

### 4.5 Verified splits

Audited by [`verify_all_leakage.py`](../../EcoVisionImagesTraining/verify_all_leakage.py),
which **deliberately shares no code with the scripts that built the splits** —
each builder computes its own "0 leaked" line from the same grouping function it
used to make the split, so a bug there is invisible exactly where it matters.

Three failure modes are checked separately: group overlap, **byte-identical
content under different names** (SHA-256 over every file), and name collisions.

| dataset | grouping | train | val | test | overlap / dupes / collisions |
|---|---|---|---|---|---|
| weapons v2 | base image | 27,936 files / 10,067 groups | 2,157 | 2,157 | 0 / 0 / 0 |
| graffiti v2 | base image | 7,943 / 7,742 | 1,055 | 165 | 0 / 0 / 0 |
| vandalism v2 | source video | 244 / 16 | 69 / 5 | 51 / 5 | 0 / 0 / 0 |

---

## 5. Training

### 5.1 X3D-XS video classifiers

```
input        13 frames @ 160×160, whole frame
normalize    (x/255 − 0.45) / 0.225
head         softmax
loss         NLLLoss(log(p))
transfer     init from violence checkpoint; unfreeze last 2 blocks
             backbone_lr_mult = 0.1
```

**Inference contract, recorded in every checkpoint's `.meta.json`:** the head
already applies softmax, so the model emits *probabilities*. Applying softmax
again at inference — the natural mistake, since most PyTorch classifiers emit
logits — flattens the distribution and caps achievable accuracy. This was a real
bug, found and fixed, and the contract is now written beside the weights.

### 5.2 The negatives result, which mattered more than architecture

Three violence checkpoints differing **only** in their negative set:

| checkpoint | negatives | alarms/hr |
|---|---|---|
| `ucf_neg_motion` | UCF normals, motion-filtered | 41.0 |
| `corpus_neg` | 4,825 clips from 26 daytime cameras | 32.0 |
| **`daynight`** (deployed) | day **and** night capture | **4.50** |

Per-clip recall was **unchanged at 95.0%** across the daynight swap while alarms
fell 12.75 → 4.50/hr.

**Not a uniform win, and the aggregate hides it:** the flyover camera improved
45 → 6 alarms/hr, but the camera outside Lyn's Restaurant got *worse*, 6 → 12/hr.
Per-camera breakdowns live in the checkpoint sidecar for that reason.

### 5.3 YOLO detectors

| | weapons v2 | graffiti v2 |
|---|---|---|
| model | yolo11s.pt | yolo11s.pt |
| imgsz | **640** | **416** |
| batch / workers | 8 / 2 | 8 / 2 |
| epochs / patience | 120 / 25 | 100 / 20 |
| optimizer | AdamW, lr0 1e-3, warmup 3 | same |

**Graffiti trains at 416 deliberately**, matching `VANDAL_MARK_IMGSZ` in
`main.py`. Training at a resolution production does not use means the TensorRT
engine, the benchmark and the deployed model all disagree — the mismatch class
that made the old weapon detector's numbers meaningless. Weapons' 640-vs-416
mismatch is a known open item (§9.1).

**`cv2.setNumThreads(0)` before the ultralytics import.** OpenCV otherwise spawns
a competing thread pool inside every DataLoader worker; that raised
`_ArrayMemoryError: Unable to allocate 1.17 MiB` and killed the weapon run twice.

### 5.4 The `phone` class: a design decision, not a default

The old weapon model's vocabulary was `{Gun, Knife, Sign}`. Anything roughly
gun-shaped **had** to be emitted as one of those. Measured: **47 Gun detections
in 30 minutes of a street with no guns**, and 1,786 detections/hour on barbershop
footage.

A cheap fix was tried first — veto weapon detections overlapping a COCO "cell
phone" or "scissors" box. It was **measured and failed**: only **1 of 310**
barbershop detections overlapped any benign COCO object. COCO does not see the
scissors and razors either.

So the fix went into the training data. `phone` was promoted to a **real class**
rather than left as background, because a class gives *positive* supervision —
the model learns what a phone **is**, not merely that one region is not a gun.
`main.py` then drops phone detections, so a correctly-detected phone is a **true
negative that raises no alert**.

**The `Sign` class was removed.** It fired **0 times in 4,800 measured frames**
— it detects road signs, not walls — while inflating the old model's headline
recall to 88.7%.

---

## 6. Threshold selection as a leakage channel

Choosing an operating point on the split you then report manufactures an
improvement **with no file moving between splits**. Weapon thresholds are
therefore swept on **validation** and reported on **test**.

Implementation note: the sweep caches each image's maximum confidence per class
in one inference pass, then evaluates all 7,396 threshold pairs in pure Python.
4,314 images are scored **once**, not once per candidate.

---

## 7. Results

### 7.1 Violence — deployed

**95.0% of events** (38/40) on continuous spliced footage at threshold 0.50 /
`consecutive_required = 3`; **4.50 false alarms/hour** aggregate across three real
cameras; per-clip recall 91.4% on 932 held-out clips.

`consecutive_required` was tunable only on continuous footage: 201 of 379 violent
benchmark clips have just **two** inference points, making `need ≥ 3`
arithmetically impossible — a property of the clips, not the model. Measured on
spliced continuous video, cons = 1, 2 and 3 all detect 38/40 while alarms fall
49.0 → 32.0 → 17.0/hr. **cons = 3 is the last free step; 4 is the first that
costs** (36/40).

### 7.2 Robbery — deployed

At threshold 0.70: **accuracy 84.2%, recall 65.3%, precision 86.5%, FPR 5.6%.**

**Recall 65.3% means it misses roughly one robbery in three**, and the figure
comes from 139 clips drawn from **eight** source videos — not 139 independent
samples. Robbery is also the yardstick for scene count: this architecture works
at **26 training scenes**.

### 7.3 Weapons — rebuilt, not yet deployed

Final checkpoint is **epoch 98 of 120**; the run crashed at epoch 70 on system
RAM exhaustion, was resumed, and converged (mAP50 flat across epochs 96–100,
recall pinned at 0.745–0.747). The resume mattered:

| checkpoint | test recall @ deployed thresholds | accuracy |
|---|---|---|
| epoch 68 (crash point) | 74.3% | 81.0% |
| **epoch 98 (final)** | **79.7%** | **85.0%** |

At the inherited thresholds (gun 0.52 / knife 0.45) on 2,157 held-out images:
**79.7% recall · 85.0% accuracy · 99.6% precision · 0.9% FPR**
(TP 1255 / FP 5 / TN 578 / FN 319).

Thresholds swept on val, reported on test:

| FPR budget | gun / knife | test recall | test FPR |
|---|---|---|---|
| ≤1% | 0.45 / 0.43 | 82.8% | 1.5% |
| ≤2% | 0.36 / 0.23 | 87.2% | 2.7% |
| **≤3%** | **0.30 / 0.23** | **89.0%** | **3.1%** |
| ≤5% | 0.17 / 0.20 | 91.9% | 4.6% |
| ≤8% | 0.09 / 0.16 | 94.3% | 7.5% |

**+9.3 points of recall for 2.2 points of FPR.** The deployed 0.52/0.45 were
inherited from a *different, worse* model that needed high thresholds to suppress
its own false positives.

**One non-uniform effect worth recording:** at *permissive* thresholds the
epoch-98 model is marginally worse than epoch 68 (91.9% vs 92.9% at the ≤5%
budget). The additional 30 epochs sharpened confident detections — the baseline
gained 5.4 points — without improving the low-confidence tail. The gain is
concentrated at deployable thresholds, not spread evenly across the curve.

### 7.4 Vandalism — deployed, after the negatives fix

| configuration | false alarms/hr | recall |
|---|---|---|
| v2, UCF negatives only, threshold 0.5 | **125.25** | 93.8% |
| v3, + 850 Davao street negatives, 0.5 | 21.75 | 87.5% |
| **v3, threshold 0.7 (deployed)** | **6.75** | **78.1%** |
| violence detector, for comparison | 4.50 | — |

Per camera at the deployed operating point: agdao_flyover 24.0, agdao_market
3.0, iloilo_guiez **0.0**, lyns_restaurant **0.0**.

**Two of those cameras were excluded from training** — `Agdao_Public_Market_PTZ`
and `Bankerohan_Lyn_s_Food_Haus` are the same cameras as two held-out
recordings, so training on them would have made the false-alarm measurement
circular. Their 8.7x and 29.5x improvements are therefore generalisation to
unseen cameras, not memorisation.

**Two guards were required before this number could be trusted**, and both came
from questioning the setup rather than reading the output. With 148 positives
against 796 negatives, (a) unweighted loss lets the majority class dominate the
gradient, and (b) raw-accuracy checkpoint selection *rewards collapse* — on that
validation split, predicting "never vandalism" scores 82.2% and would have been
saved as the best epoch. Trained with `--class-weights auto` and
`--select-metric balanced`; per-class recall at the chosen epoch was neg 86.7% /
pos 84.6%, which is what rules out collapse. An audit of all 18 checkpoints on
disk confirmed **no previously deployed model was affected** — every violence
manifest uses a 50/50 validation split, which makes the defect structurally
impossible.

**Honest cost:** UCF test recall fell 93.8% to 78.1% at the deployed threshold.
That split contains no Davao footage, so it cannot measure what the negatives
fixed, but the regression is real. `experimental: true` stays set, because the
test split is five scenes.

### 7.5 Graffiti marks — retrained, beats deployed

Both models scored in **one run** on the same untouched 165-image benchmark, so
the comparison cannot drift across sessions:

| | mAP50 | mAP50-95 | precision | recall |
|---|---|---|---|---|
| v1 (deployed) | 0.718 | 0.472 | 0.794 | 0.602 |
| **v2** | **0.734** | **0.485** | **0.804** | **0.646** |

A **strict improvement** — no metric traded against another.

**Honest scale of the win:** 6.75× more training data (1,177 → 7,943) bought
+1.6 points of mAP50. The new corpus is photographer-framed — median box 18.5% of
frame versus 4.9% in the CCTV-like data — so most extra images teach graffiti at
the wrong distance. **The bottleneck is scale-matched data, not quantity.**

---

## 8. Negative results

These are reported at length because they carry as much information as §7, and
because two of them corrected figures this project had already published.

### 8.1 MIL underperformed — but the comparison is qualified

A feature-cached MIL head was built, its first version's leakage found (8/8 test
and 9/9 val sources were in training), rebuilt with runtime exclusion, and then
**rejected on measured merit**. Per §2.1, the known fix for MIL's failure mode
(RTFM's top-k) was not attempted, so this is *"MIL as formulated underperformed
here"*, not *"MIL does not work."*

### 8.2 The vandalism model carried negative information

Trained on **11 scenes**:

| threshold | accuracy | recall | precision | FPR |
|---|---|---|---|---|
| 0.5 | 70.3% | 86.2% | 78.1% | **87.5%** |
| 0.9 | 73.0% | 75.9% | 88.0% | 37.5% |

**70.3% is below the 78.4% obtained by labelling every clip vandalism.** It fired
on 7 of 8 normal clips. Rejected, and kept on disk with
`status: REJECTED FOR DEPLOYMENT` and its reason.

**The retrain, and a confirmed prediction.** That rejected checkpoint's metadata
recorded a specific hypothesis: *"Scene count, not architecture. Robbery works at
26 training scenes; this fails at 11."* Raising the count to 26 sources and
changing nothing else tested it directly:

| | old (11 scenes) | **new (26 sources)** |
|---|---|---|
| accuracy | 70.3% | **82.4%** |
| majority-class baseline | 78.4% | 62.7% |
| **relative to baseline** | **−8.1** | **+19.7** |
| recall | 86.2% | **93.8%** |
| precision | 78.1% | 81.1% |
| FPR | 87.5% | **36.8%** |

The class moved from carrying *negative* information to carrying clearly positive
information, on a prediction made in advance. That is the strongest evidence in
this report that the vandalism problem is a **data** problem.

It is **still not deployed**, and the number that settles it is not per-clip FPR
at all. Replayed through the shipped smoothing at the deployed threshold 0.5 /
`consecutive_required = 3`, over 20 minutes of each of four held-out real
cameras:

| camera | alarms in 20 min | per hour |
|---|---|---|
| agdao_flyover | 65 | **195.0** |
| lyns_restaurant | 59 | **177.0** |
| agdao_market | 26 | 78.0 |
| iloilo_guiez | 17 | 51.0 |
| **total** | **167 over 1.33 h** | **125.25/hr** |

The deployed violence detector runs at **4.50/hr**. This is **28× worse — a
false alert every 29 seconds.**

This is why the per-clip figure had to be converted. "36.8% FPR on 19 negatives"
is arguable; "one false alarm every 29 seconds on your own cameras" is not. It
also demonstrates the general point from §1.2: per-clip rates systematically
understate a continuously-running camera, and the operating point must be chosen
on the metric the operator actually experiences.

Caveats that limit how hard this can be pushed: the test split is **5 scenes and
19 negatives**, so 36.8% FPR is 7 false positives out of 19 — the same
small-denominator artifact that made the old weapon detector's 21.2% FPR
meaningless. And train accuracy reached 89.3% while validation sat near 60%: the
model is memorising its 16 training scenes, so more epochs will not help. More
scenes will.

### 8.3 The vandalism rule was structurally impassable

`score_vandalism()` requires four conditions. Instrumented per condition on 48
graffiti clips:

| target-box source | frames with target | recall |
|---|---|---|
| original `Sign` class | **0 / 3,600** | **0.0%** |
| graffiti v1 | 732 | 6.2% |
| graffiti v2 | 849 | 8.3% |

The gate depended on a class that **never fired**. No threshold could have fixed
it — this was not a tuning problem. Replacing it with a purpose-trained detector
fixed condition 1 and confirmed the diagnosis, but recall reached only 8.3%.

Per-condition counts show why: **3,231 person-frames fail condition 1 even after
a mark is detected somewhere in frame.** Condition 4 (crowding) passes 88% of the
time; condition 2 (velocity) passes 70%. The rule keys on the **residue** of the
act — and while someone is spraying, the mark does not exist yet.

### 8.4 Change detection worked better, and still failed

Inverting the question — detect a mark *appearing* in a cell that was reliably
empty during a baseline — reached **3 of 4 events in-span** where the wrist rule
confirmed **0 of 48 clips**. But it false-alarmed on **all four** held-out
cameras at every operating point reaching that recall.

A persistence check (a real tag stays; an occluding van leaves) reduced alarms
and recall roughly one-for-one. **Caveat:** the test videos run 23–267 s, so
requiring 20 s of persistence is arithmetically impossible on the shortest — the
idea is partly being scored on video length rather than merit, and four positives
cannot separate the two effects.

### 8.5a A pose-based strike rule: built, measured, rejected

`main.py` defines `MIN_PUNCH_VEL`, `MIN_PUNCH_SPIKE_RATIO`, `MIN_APPROACH_DOT`
and `MIN_BBOX_OVERLAP_RATIO` — and **nothing consumes them.** The rule-based
strike path does not exist; violence is the X3D scene classifier alone. That
looked like free headroom, so it was built properly and measured.

**One flaw was fixed before it could be repeated.** `MIN_PUNCH_VEL = 60` is an
*absolute pixel* threshold. A person filling 40% of the frame swings a wrist
across far more pixels per punch than the same person at 9%, so an absolute
threshold is scale-blind for exactly the reason the X3D model is (§9.2). Every
geometric feature in the implemented rule is normalised by person-box height —
velocity in body-heights per frame, reach in body-heights — following the
precedent already in `main.py`, where grip radius is
`max(GRIP_THRESHOLD, box_h * FRAC)`.

Measured on 160 clips from the held-out test split:

| | recall | FPR |
|---|---|---|
| X3D alone (threshold 0.5) | 82.5% | **0.0%** |
| rule alone | 35.0% | 25.0% |
| **union (X3D or rule)** | 87.5% | **25.0%** |

The rule catches **4** violent clips X3D misses and adds **20 false alarms**
doing it — only 17% of its extra firings are real. Trading 0% → 25%
false-positive rate for five points of recall is not a trade worth making on a
continuously-running camera: the vandalism model's 36.8% clip-level FPR produced
125 alarms/hour on the same cameras.

**Rejected.** A further limitation makes this structural rather than a tuning
problem: **29 of the 160 clips never had a person tracked at all**, so the rule
cannot fire on them regardless of its thresholds. It is limited by pose
resolution — the same bottleneck as everything else in §9.2 — so it cannot help
where the system is actually weak. The honest improvement path for violence
remains the person-crop / tiled architecture, not rules.

### 8.5b The graffiti detector was detecting the clock

Asked whether the graffiti detector alone could serve as the vandalism signal,
its firing rate was measured on the four held-out real cameras:

| camera | frames with a detection |
|---|---|
| lyns_restaurant | **97.0%** |
| agdao_flyover | 10.7% |
| agdao_market | 9.3% |
| iloilo_guiez | 4.6% |

97% is higher than three of the four *actual* graffiti videos, so a frame was
rendered with its detections drawn and inspected. **The box sat squarely on the
burned-in DVR timestamp** — "12-08-2026 08:04:44 PM". On `agdao_flyover` the box
was on a "No Parking" road sign. To a detector trained on photographs of tags,
white text on a dark surface *is* graffiti; the model is not malfunctioning, it
is generalising exactly as trained.

Detection centres by vertical position settle the fix:

| source | detections | in top/bottom 12% |
|---|---|---|
| the four graffiti videos | 982 | **0.0%** |
| lyns_restaurant | 10,710 | **99.5%** |
| agdao_flyover | 1,210 | 15.6% |
| agdao_market / iloilo | 1,512 | 0.0% |

An edge-band mask (`VANDAL_MARK_EDGE_BAND = 0.12`) removes **75.3% of all
detections and zero true ones**: lyns_restaurant falls 97.0% → 0.5%, while all
952 frames-with-a-mark across the four graffiti videos survive intact. This is
the ROI-masking technique the literature reports cutting 40–60% of false
positives in exposed scenes, here justified by measurement rather than assumed.

**It does not rescue change detection.** Re-running the §8.4 sweep on masked
detections still gives 3/4 events with all four cameras alarming — the residual
4.6–9.3% mid-frame flicker on the other cameras is enough to trigger "a mark
appeared where there was none." The mask fixes a real defect in the detector; it
does not change the conclusion that change detection needs a stabler signal than
this detector provides.

### 8.4a Head-to-head: rule vs detector vs both

The three candidate vandalism signals, scored on the SAME 48 graffiti-act clips
and 40 same-camera negatives, so the comparison is a comparison rather than
three separate anecdotes:

| signal | recall | FPR | accuracy |
|---|---|---|---|
| rule only (`score_vandalism`) | 8.3% | **0.0%** | 50.0% |
| **detector only** (mark visible, sustained) | **20.8%** | 7.5% | 53.4% |
| union (either fires) | 22.9% | 7.5% | 54.5% |
| intersection (both fire) | 6.2% | 0.0% | 48.9% |

**The detector alone beats the rule 2.5x on recall, and the rule is redundant to
it**: the union catches 11 clips, of which the detector already had 10. If a
vandalism signal is ever enabled it should be the detector, not the rule — the
rule is both weaker and adds nothing on top.

**None of them work.** The majority-class baseline on this set is **54.5%** (48
of 88 clips positive). Every configuration lands at or below it; the best point
on a full detector sweep (fire if a mark is visible in ≥1% of frames) reaches
55.7%, **1.2 points above guessing**. This is the same test the trained X3D
vandalism model failed in §8.2, applied to the rule-based routes.

**Why, in one measurement:**

| | clips with a mark visible at all | median coverage |
|---|---|---|
| during the **act** | **29%** | **0.0%** |
| during **normal** spans | 12% | 0.0% |

During the graffiti act itself the detector sees no mark whatsoever in **71% of
clips**. That is not a weak signal, it is an absent one, and it is the cleanest
statement of the diagnosis in §8.3: the mark does not exist while it is being
made. A detector for finished graffiti cannot see graffiti being applied, and
sweeping its threshold from 1% to 90% never buys more than 1.2 points over
guessing.

The negatives are what make this conclusive. `graffiti_normal` holds the
non-act spans of the SAME four videos — same walls, same cameras, often the
same graffiti already on them — so the detector fires on them for exactly the
reason it fires on the positives. It answers *"is there graffiti here"*, not
*"is someone putting it there"*, and only the second one is a crime.

### 8.5 Three measurement errors found and corrected

| error | consequence | correction |
|---|---|---|
| **Vandalism rule measured against property-destruction clips** | reported 11.4% recall for a *graffiti* rule on footage containing no graffiti | figure **retracted**; graffiti-only clip set built |
| **Stride inflated wrist velocity** | sampling every 2nd frame doubles apparent px/frame against a per-frame band; genuine sweeps rejected as "too fast" | re-measured at stride 1 |
| **Ground truth read via the model's class order** | would score `Short_rifle` as knife across datasets with differing orders | eval reads each dataset's own `data.yaml` |

A fourth: the figure **70.3%** belongs to the *vandalism* model and was once
misattributed to the weapon detector in working notes. The weapon baseline is
**79.7%** on the final checkpoint (74.3% on the epoch-68 one).

### 8.6 Four deployment bugs, all silent, all found by running the system

Measuring a model and *deploying* it are different problems. After the retrained
models were written into `config.json` and a preflight reported every path,
checkpoint and class name correct, the pipeline was run end-to-end on a known
violent clip. It loaded the **old** weights.

Four independent defects, none of which raised an error:

1. **`config.json` was never read.** `APP_ENV` defaults to `"development"`, and
   `config.development.json` exists, so the loader *replaced* the base config
   with it rather than overlaying. Both env files are stale skeletons missing
   `detection.weapon`, `detection.robbery` and `detection.vandalism` entirely.
   Every model path, threshold, metric block and rollback note in `config.json`
   was inert in any normal run.

2. **The writable config also replaced rather than merged.** `~/EcoVisionSentinelData/config.json`,
   seeded 19 August, predates the same three blocks. Any key added to the shipped
   config after that date never reached a machine that had ever run the app.

3. **The weapon model filename was hardcoded** in `load_model_with_fallback(...)`.
   `detection.weapon.model_path` was decorative — editing it changed nothing,
   exactly like the already-documented dead `database.path` key.

4. **Two path conventions disagreed silently.** `marks_model_path` was consumed
   as a bare filename joined onto `WEIGHTS_DIR`, so a `"weights/..."`-style value
   produced `<root>/weights/weights/<file>`, which does not exist — and the
   caller only checks existence before falling back to a default. The wrong model
   loaded, with a reassuring log line naming it.

**The consequence was not cosmetic: the robbery detector was not running at
all.** A deployed, measured, documented model was absent from the config actually
being read, so it silently fell through to disabled. It now fires
(`ROBBERY | conf=0.83` on the verification clip).

The fixes make both layers *overlays*: `config.json` is the base, the env file
and the writable file override only what they genuinely change. A key added to
the base now reaches every environment.

**And a correction to the verification itself.** The first preflight passed
while the runtime was broken, because it read `config.json` directly instead of
resolving it the way `main.py` does. A verifier that does not share the runtime's
resolution path cannot catch resolution bugs — which are exactly the bugs that
hide. `verify_deployment.py` now merges all three layers in the same order and
reports which files it merged.

---

## 9. Threats to validity

### 9.1 Known and open

- **Recall on deployment cameras is unmeasurable** (§1.3). The single most
  important limitation.
- **Small test splits.** Robbery: 8 scenes. Vandalism: 5 scenes. Graffiti
  change-detection: 4 videos. These are measurements *over those scenes*, and
  that belongs beside the number, not in a footnote.
- **Weapon `imgsz` mismatch.** Trained at 640, production runs 416 — unresolved.
- **Benchmark ≠ domain.** RWF/SCVD/UCF are not Philippine street cameras.

### 9.2 Scale: the measured blind spot

Replaying 40 clips the model detects at 1.000 confidence, while shrinking the
people in them:

| person height (% of frame) | 37% | 30% | 22% | 19% | 15% | 9% |
|---|---|---|---|---|---|---|
| detected | 40/40 | 34/40 | 25/40 | 22/40 | 7/40 | **0/40** |

Training clips place a person at 24–60% of frame height (median 37%). **A wide
street camera puts them at 6–12%.** Below ~15% the model is not less confident —
it is **blind**, and a blind camera and a safe street produce identical output.
For a public-safety system that is the most dangerous failure mode there is,
because nothing looks wrong.

### 9.3 Stale metadata — corrected 21 Aug

`config.json`'s dashboard metrics had drifted from the measurements: violence
reported **17.0 alarms/hr** (a pre-`daynight` figure against the deployed
checkpoint's 4.50), and weapons reported **88.7% recall / 88.4% accuracy** — the
withdrawn numbers inflated by the `Sign` class and by split leakage. Both now
carry the current measurements and an explicit note that the weapon figures
*replace withdrawn ones and are not comparable to them*.

Current dashboard headlines: violence 95.0% detection rate, robbery 65.3%
recall, weapons 89.0% recall, vandalism 125.25 alarms/hr (disabled).

---

## 10. Future work

Ordered cheapest-and-most-certain first.

1. ~~Re-sweep weapon thresholds against the final checkpoint.~~ **Done 21 Aug** — gun 0.30 / knife 0.23 gives 89.0% recall at 3.1% FPR, versus 79.7% at the deployed thresholds. **Apply them to `main.py`'s `CONF_BY_CLASS` and deploy the epoch-98 checkpoint.**
2. **Deploy graffiti v2** — strictly better on the same benchmark; work complete.
3. ~~Correct `config.json`'s stale metrics.~~ **Done 21 Aug** (§9.3).
4. ~~Resolve weapon `imgsz` by measurement.~~ **Done 21 Aug** — 640 beats 416 on
   recall *and* FPR (79.7% vs 76.1% baseline; 89.0%@3.1% vs 88.3%@5.3%), and the
   val→test threshold drift at 416 (4.9%→7.2%) shows its confidence distribution
   is unstable at that resolution. `WEAPON_IMGSZ` is now 640. **The TensorRT
   engine still needs rebuilding at 640** — `optimize_weights.py` reads the
   constant, so it follows automatically.
5. **Test-time augmentation: measured and rejected.** TTA lifts recall at very
   low FPR budgets (+2.0 points at ~1.6% FPR) but *loses* 1.8 points at the
   chosen ~4.5% operating point — its downscale passes shrink already-small
   objects below detectability — while costing **2.7× inference** (12.5 → 34.3
   ms/frame). Not worth it on a GPU already running four models. Recorded because
   the negative result saves the next person the experiment.
5. **Film ~20 vandalism locations** ([`vandalism_data_collection.md`](vandalism_data_collection.md)). Given §2.5, this is the *only* lever that exists.
6. **Request [MSAD](https://msad-dataset.github.io/) access** — maps to the property-damage class; will not arrive before defense but makes future work concrete.
7. **Two-stage person-crop for violence** (§2.3, §9.2), gated on first measuring whether pose resolves people at 9–12% frame height at an affordable `imgsz`.
8. **RTFM-style top-k MIL** if weakly-supervised training is revisited.
9. **Mean Teacher on unlabeled Philippine capture** — removes the risk that footage *assumed* violence-free is trained as negative when a real incident occurs in it.

---

## 11. Conclusion

The system detects **95.0% of violent events at 4.50 false alarms per hour** on
real footage, and the rebuilt weapon detector reaches **89.0% recall at 3.1%
FPR** — both on a single consumer GPU.

The more durable contribution may be methodological. Three published figures were
withdrawn after measurement exposed how they were produced: a 99.4%-leaked
validation split, a detector class that had never fired, and a rule scored
against footage containing none of what it detects. Each was found by
instrumenting a claim rather than accepting it, and each is recorded here with
its correction.

The vandalism class remains disabled. That is the correct outcome given the
evidence, and the evidence is now specific: not *"it didn't work"* but *"its gate
fired 0 times in 3,600 frames, the inverted formulation reached 3 of 4 events but
alarmed on every camera, and no public video dataset of the graffiti act exists
to improve it."*

A system that reports what it cannot do is more useful than one that reports only
what it can.

---

## 12. Reproduction

```
python verify_all_leakage.py                 # split audit, exit 0 = clean
python eval_weapons_v2.py --weights <ckpt>   # weapon confusion matrix
python sweep_weapon_thresholds.py --weights <ckpt>
python eval_graffiti_bench.py                # both graffiti models, one run
python diagnose_vandal_rule_v2.py --stride 1 # per-condition rule instrumentation
python graffiti_change_detect.py             # change-detection sweep
```

## 13. Document map

| document | subject |
|---|---|
| [`model_training_and_data.md`](model_training_and_data.md) | per-model methodology, datasets, configs, improvements |
| [`data_splits_and_leakage.md`](data_splits_and_leakage.md) | split grouping, leakage audit, surviving limitations |
| [`related_work_notes.md`](related_work_notes.md) | literature, with per-citation verification status |
| [`detection_performance_report.md`](detection_performance_report.md) | full TP/FP/TN/FN tables |
| [`progress_report_violence_detection.md`](progress_report_violence_detection.md) | violence development narrative, 1,715 lines |
| [`vandalism_data_collection.md`](vandalism_data_collection.md) | the filming protocol |
| [`datasets_used.txt`](../../EcoVisionImagesTraining/datasets_used.txt) | RRL dataset list with links and licences |
