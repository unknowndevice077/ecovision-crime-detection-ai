# EcoVision Security Sentinel — Violence Detection Module
## Research Progress Report

**Prepared:** August 8, 2026 · **Updated:** August 12, 2026, 15:40
**Module:** Real-time violence detection (YOLO person/pose + weapon detection, X3D-XS video classifier)
**Scope of this report:** Architecture and methods used, datasets used, diagnosis and correction of the violence-detection subsystem, discovery and partial resolution of a deployment-blocking scale limitation, a lighting/time-of-day domain gap and a train/test split defect, camera integration for arbitrary RTSP/ONVIF hardware, and honest per-source real-world validation results with open problems clearly stated.

---

## Abstract

EcoVision Security Sentinel is a real-time video-analytics system for public CCTV, combining a **YOLO-family detector** (person localization, pose keypoints, weapon recognition — a per-frame spatial task) with an **X3D-family video classifier** (violence recognition over a temporal window — a spatiotemporal task). This report documents a full diagnostic and correction pass on the violence-detection subsystem across four phases. First, five distinct defects were found and fixed, raising honest (held-out, never-seen-in-training) test accuracy from 78.4% to 95.0%. Second, a deployment check against a real, wide-angle city camera surfaced a problem invisible to any benchmark: the model was **blind, not merely less confident,** below roughly 15% of frame height, because training footage never showed it a person that small — fixed via tiled scene inference and scale-augmented retraining, recovering city-scale recall from 0/30 to 30/30 on synthetically shrunk clips. Third, validating against **real, continuously-running footage** surfaced a false-alarm-rate problem (4–14 alerts/hour) that neither of the first two fixes addressed, traced to the model never having been trained on ordinary, real street footage — only curated, balanced benchmark clips. Fourth, closing that gap directly: real CCTV footage (CCTV-Fights) and the project's own captured Philippine street footage were used to fine-tune the model. The first attempt, deployed in the existing tiled architecture, failed badly (an 11x increase in false alarms) — reported here in full, not hidden, because diagnosing *why* led to the actual fix: the same fine-tuned weights in scene mode (not tiled) measured **0.00 false alarms/hour on 5 of 6 real validation clips**, while retaining 92.5% recall at the camera's real-world scale. This configuration is now deployed. It is presented as the strongest evidenced result to date, not a claim that the problem is fully solved — one of the six validation clips got worse, not better, and is reported honestly as a known residual case. **Fifth**, extending validation along axes that had been held constant by accident rather than design overturned part of that result: all six clips above were night footage, and the same deployed configuration produces **75.20 false alarms/hour on daytime capture** (0.00 to 334.00/hour depending on camera) — the scale failure of the second phase repeating along a lighting axis. A parallel audit of the train/test split found **270 of 280 source videos had segments on both sides**, fixed with a group-aware split; re-evaluating on the clean split and **breaking results down by data source rather than aggregating** gives the most honest figure this project has produced: 91.9% overall but **67.0% accuracy and a 44.3% false-positive rate on the only real-CCTV source**, with the aggregate carried by benchmark data. A prediction stated earlier in this work — that the contamination was substantially inflating real-CCTV recall — is recorded here as **wrong**, since recall was essentially unchanged at 83.3%; the corrected conclusion is that false positives, not missed detections, are the binding constraint. Finally, the system was made source-agnostic (RTSP/ONVIF and any IP camera), verified against a real RTSP server, which surfaced two failure modes invisible to webcam testing.

---

## 0. Deployment Target (scope, clarified 2026-08-12)

The system is intended for a **solar/energy-efficient smart streetlight pole**,
detecting violence, vandalism and robbery on the street below. Indoor coverage
is an optional additional feature, not core scope.

This is recorded as its own section because it determines which of the failures
in this report are central and which are peripheral, and the ordering is not the
intuitive one:

- **A streetlight pole is an elevated mount.** People below it appear *small* in
  frame. §7 measures this model detecting **0 of 40** clips at ~9% person height
  that it scores 1.000 on at close range. Scale is therefore not an edge case for
  this deployment — it is the deployment geometry, and the single most important
  axis in this document.
- **A streetlight's defining condition is night.** Night false-alarm rates
  (§14.5, 0.00–8.00/hour) matter more than the daylight rates (§14.6, up to
  334/hour) — though the camera runs continuously, so daylight cannot be ignored.
- **Vandalism and robbery are in scope**, and both are currently implemented as
  hand-written geometric rules reporting *hardcoded* confidence values to the
  dashboard (§18). For a system whose stated purpose includes them, that gap is
  more serious than this violence-focused report has so far treated it.
- Indoor scenes (restaurant interiors, shop counters) are explicitly **not** the
  target. Negative training data drawn from them would tune the model for the
  wrong camera, so captured indoor footage is held separately.

---

## 1. Problem Statement

At the start of this work period, the violence-detection component of EcoVision Security Sentinel had been retrained four separate times — varying unfreeze depth, data augmentation, class oversampling, and input representation — without breaking a **held-out accuracy plateau of approximately 70%**. No prior retrain had identified *why* accuracy was capped, only that it was.

The objective of this work was threefold:

1. Diagnose the cause of the plateau through systematic measurement rather than further blind hyperparameter search.
2. Correct whatever defects were found, and re-measure honestly.
3. Verify the corrected model actually works on the system's real intended deployment target — full-view city CCTV — rather than stopping at benchmark accuracy.

Objective 3 surfaced two further, unrelated problems (§5, §12) not visible in any benchmark number, each requiring its own diagnosis and fix.

---

## 2. Methods: System Architecture

The system uses **two different convolutional architectures for two different tasks.** This section explains what each is, why each was chosen, and why they cannot substitute for one another — since the two answer structurally different questions about the same video.

### 2.1 YOLO (You Only Look Once) — spatial detection, per frame

YOLO is a **2D convolutional network**, single-shot object detector: given one image, it predicts bounding boxes and class labels for everything of interest in it, in one forward pass. It answers a purely **spatial** question — *where are the people, and where are the weapons, in this single frame* — and does not, by itself, know anything about what happened in the previous frame.

Two YOLO models are used here, each doing a distinct spatial job:

- **`yolo11s-pose`** — person detection plus 17 skeletal keypoints per person. Used for (a) locating and tracking people frame-to-frame, and (b) producing person crops for per-track violence classification (§2.5).
- **A separate weapon-detection YOLO model** — trained to recognize weapon classes directly.

YOLO is the right tool here because the task is genuinely per-frame and needs to run at full camera frame rate (30fps) cheaply: 2D convolutions are far less computationally expensive than 3D ones, which is why YOLO can run on every frame while the video classifier (§2.2) only needs to run periodically on a buffered window.

### 2.2 X3D — spatiotemporal classification, over a clip

X3D is a **3D convolutional network** family (Feichtenhofer et al., *X3D: Expanding Architectures for Efficient Video Recognition*, CVPR 2020) purpose-built for **video** recognition: its convolutions operate over a spatiotemporal volume (height × width × **time**), so it can recognize an *action* — something that only exists as a pattern across multiple frames — not just an object present in one frame. It answers a **temporal** question: *given this short window of frames, is what's happening in it violent?*

X3D's specific contribution over earlier 3D-CNN video models (e.g. I3D, SlowFast) is computational efficiency: rather than hand-designing a 3D architecture from scratch, it starts from a small 2D image-classification backbone and **expands it along independent axes** — temporal duration (γt), frame rate (γτ), spatial resolution (γs), network width (γw), and depth (γb) — searching for the combination that is accurate per unit of compute, rather than accurate at any cost. This project uses **X3D-XS**, the smallest variant in the family, specifically because the deployment target is a single consumer GPU (GTX 1660 SUPER), not a data-center accelerator.

### 2.3 Why two different CNN architectures, not one

A single 2D network cannot recognize an action, because an action is defined by *change over time* — a still frame of a person mid-swing looks identical whether the swing is a punch or a wave. A single 3D network, run on every frame at full resolution to do YOLO's job, would be many times more expensive than a 2D detector for a task that doesn't need the temporal axis at all — localizing a person in a frame doesn't require looking backward in time. Using each architecture only where its extra machinery (2D speed vs. 3D temporal reasoning) is actually needed is what makes real-time operation on modest hardware possible at all: **YOLO runs every frame (2D, cheap); X3D runs periodically on a buffered clip (3D, expensive but infrequent).**

### 2.4 Three operating modes, one shared classifier

The X3D classifier is deployed in three switchable modes (one config edit, `detection.violence.mode` in `config.json`):

| Mode | What X3D sees | Best for |
|---|---|---|
| **track** | A YOLO-tracked person's own crop (+ nearby bystanders merged in) | Close/medium-framed cameras where a tracked person occupies a meaningful fraction of the frame |
| **scene** | The whole frame, once per check interval | Simple, cheapest whole-frame baseline; blind to small distant people (§5) |
| **tiled** | The frame split into an overlapping grid of regions, each independently classified, **plus** one full-frame pass | Wide, full-view city cameras where a person is too small (6–12% of frame height) for scene mode to see at all — see §5, §10 |

Every mode shares the exact same confirmation logic (`_smooth_and_confirm`, §2.5) so an alarm always means the same thing regardless of which spatial mode produced it.

### 2.5 Temporal smoothing and alert confirmation

A raw per-check confidence is not used directly to fire an alert. Each detector (per tile, in tiled mode; once, in scene/track mode) runs an independent state machine:

1. **EMA smoothing** (`ema_alpha = 0.35`) damps single-frame noise.
2. **Consecutive-hit confirmation** requires the smoothed confidence to clear the threshold for `consecutive_required` checks in a row before an alarm fires — not a single lucky frame.
3. **Sticky release with hysteresis**: once confirmed, the alarm stays live until confidence drops a full `release_hysteresis_margin` *below* the threshold, not merely below it, so a boundary-hovering confidence doesn't flicker the alert on and off.

In tiled mode, each tile runs this state machine **independently**, and the combined system alarm is "any tile confirmed" — deliberately, so that one tile catching a real incident and holding it via sticky release is not diluted by other quiet tiles (§10).

---

## 3. Datasets

| Dataset | Role | Notes |
|---|---|---|
| **RWF-2000** | Training / validation / test (benchmark) | 2,000 real-world CCTV-style clips, balanced 50/50 violent/non-violent |
| **SCVD** (Smart-City CCTV Violence Detection) | Training / validation / test (benchmark) | Adds normal-class diversity; its "Normal" class scored a perfect 0% FPR in the final held-out test (§4.1), notably cleaner than RWF-2000's equivalent class |
| **Self-recorded Davao/PH live CCTV captures** | Real-world qualitative + quantitative validation (unlabelled) | See §12 — 35 min primary intersection feed + 5 diverse clips (barbershop, fiesta, street view, tire shop, traffic), captured directly from public YouTube live streams via `yt-dlp`/`ffmpeg`, **all stored on D:** |
| **CCTV-Fights** (NTU ROSE Lab, via Kaggle mirror) | Real-domain training/validation data, acquisition in progress | 13.31GB, 1,001 files, ground-truth annotated, genuine CCTV-sourced (as opposed to RWF-2000's mixed sourcing). Kaggle mirror download in progress as of this report (§13); **official registration with NTU ROSE Lab for the authoritative source is still pending** and is not a same-day task |

**Why a fourth, real-domain dataset matters:** RWF-2000 and SCVD, while described as "real-world," are still curated benchmark clips — median person height 37.1% of frame, balanced class ratios, short duration. None of that matches a continuously-running, full-view city camera, where people are 6–12% of frame height and violence is rare rather than 50% of all footage. This mismatch (**domain shift**) is the direct cause of both major findings in §5 and §12, and is a documented, actively-researched problem in the video-anomaly-detection literature generally, not unique to this system.

All three-way manifest splits (train/val/test) are assigned by **SHA-256 content hash** (§6.2) so that any of these datasets can be added to the pool without risking leakage between splits.

---

## 4. Methodology

The guiding principle throughout was **measure, do not assume**. Every claim below is backed by a script and a logged result; several hypotheses formed during the work were subsequently disproven by follow-up measurement and are reported alongside the ones that held, because the negative results are methodologically part of the record.

---

## 5. Defects Found and Corrected (Benchmark Accuracy Phase)

### 5.1 Detection coupled to tracking (the original architectural cause)

The deployed pipeline only classified a person if a single YOLO tracking ID survived 20 consecutive frames. On the held-out set, **132 of 769 clips (17.2%) never reached the classifier at all** — 36 of them violent, silently scored as "normal" because the model never ran, and 96 normal clips scored as true negatives "for free." This inflated the apparent accuracy while hiding the real recall problem.

**Fix:** a whole-frame ("scene mode") classification path was added (`SceneViolenceDetector` in `x3d_violence_detector.py`), which classifies the entire frame on a fixed interval independent of person tracking. This removed the tracking gate as a source of missed detections.

### 5.2 Dataset leakage and duplication

An audit of the training data (6,182 source files) found **485 byte-identical duplicate files** and, because the train/validation split was assigned by a random shuffle, **93 held-out clips (12.1%) had an identical byte-for-byte twin in the training set** — the model had memorised, not generalised, on those clips.

**Fix:** `build_dataset_manifest.py` was written to assign every clip's split **from a SHA-256 content hash** rather than a shuffle. Two files with identical content always land on the same side of the split, making this class of leakage structurally impossible rather than something to re-audit after every dataset change. A **three-way split** (train / val / test) was added specifically so that model-selection (validation) and final reporting (test) could no longer be the same data — see §5.6.

### 5.3 The double-softmax defect (root cause of the accuracy plateau)

This was the most significant and least visible defect. The underlying model architecture (`pytorchvideo`'s X3D) already terminates its classification head in a `Softmax` layer — its raw output **is already a probability distribution**. Both the training loss (`nn.CrossEntropyLoss`, which internally applies `softmax`) and the live inference code (which applied `torch.softmax` again to the model's output) were **softmaxing an already-softmaxed value.**

Measured consequences:

- **The training loss had a hard mathematical floor.** A perfectly classified example could score no better than `-log(softmax([0,1])[1]) ≈ 0.3133`. Across 390 logged training batches, the minimum recorded loss was exactly 0.3133, and none went below it — confirming the floor was actually binding, not merely theoretical.
- **This collapsed the loss's dynamic range to just 1.0** (a perfect prediction and a confidently wrong one differed by only 1.0 in loss), which starved the model of gradient signal on its hardest, most informative examples — a strong candidate for the true cause of the four-retrain plateau.
- **Every confidence value the system ever reported, logged, or thresholded was compressed into the range [0.269, 0.731].** A normal scene could never read below 27%, and a violent one could never read above 73%. Every threshold tuned against this compressed scale (e.g., the deployed 0.40) was tuned against a distorted number.

**Fix, and a self-correction on the way to it:** the first proposed fix — replacing the head's `Softmax` with `Identity` — was tested before being deployed and found to be **incorrect**: the model architecture averages per-timestep probabilities across 10 temporal positions before pooling, so removing the softmax changes what is being averaged (probabilities vs. logits), not merely its scale. This was caught by a controlled test on 120 real clips, which found one verdict that changed at a large decision margin (a logit gap of −1.30) — proof the two formulations are not equivalent. The correct fix — training with `NLLLoss` directly on the model's native probability output, with gradient clipping added as a safety measure once the loss floor was removed — was verified to be **exactly decision-preserving** (0/120 disagreements) at the deployed threshold, while restoring the full [0, 1] confidence range and removing the loss floor entirely.

### 5.4 Selection bias in reported accuracy

The training script selects its "best" checkpoint by accuracy on the validation set — meaning a validation-set accuracy figure is, by construction, a number the training process already optimized toward. Every accuracy figure reported by this project before this work period was a validation-set figure presented without that caveat. The evaluation script's own output banner asserted such numbers were "the number to cite as true generalization performance," which was corrected to distinguish validation (selection-optimistic) from test (honest) results, and a related bug where test-split results were being mislabeled as validation-split in the permanent log (`eval_history.csv`) was also fixed.

### 5.5 Supporting fixes

- A checkpoint-resume bug where the "best accuracy so far" value was saved to disk **before** being updated for the current epoch, meaning a training run interrupted and resumed (which occurred once, due to a memory-exhaustion crash during this work period, §11) would silently forget its actual best score and could overwrite a better model with a worse one. The associated "early stopping patience" counter had the same bug.
- The model's expected input resolution (`frame_size`) was hardcoded identically in two separate files with no mechanism to detect disagreement between them — the exact class of bug that caused §5.1–5.3. A `.meta.json` sidecar is now written next to every trained checkpoint recording its exact input contract (resolution, frame count, output convention), and the live detector reads it and **overrides a mismatched config with a loud warning** rather than silently using the wrong value.

### 5.6 Honest, leak-free, selection-free evaluation

With the three-way manifest in place, the final model was trained on a 3,298-clip training set, model-selected on a *separate* 722-clip validation set, and — for the first time in this project's history — scored on a **758-clip test set that neither gradient descent nor checkpoint selection ever saw.**

---

## 6. Results (Benchmark Accuracy)

| Stage | Split type | Accuracy | Recall | Precision | FPR |
|---|---|---|---|---|---|
| Start of this work period | selection-optimistic | 77.2%* | — | — | — |
| After leak removal, before softmax fix | honest (leak-excluded) | 78.4% | 77.0% | 91.2% | 18.2% |
| Retrained model, validation split | **selection-optimistic** | 93.9% | 96.3% | 91.9% | 8.4% |
| **Final model, held-out test split** | **honest — never seen in training or selection** | **95.0%** | **97.4%** | **92.9%** | **7.4%** |

*\*Confusion counts for the 78.4% figure: TP=237, FN=137, TN=304, FP=91 (n=769).*
*Final honest figure confusion counts: TP=369, FN=10, TN=351, FP=28 (n=758).*

The honest, held-out result represents a **+16.6 percentage point accuracy improvement and a +20.4 point recall improvement** over the session's starting point, achieved on a model trained with **18% less data** than an intermediate checkpoint, which underscores that the gain came from removing defects rather than from more data.

### 6.1 Error analysis on the held-out result

Breaking the 32 false positives down by data source found **all 32 originated from one sub-class** (RWF-2000's "NonFight," largely crowded/wide street footage); the parallel SCVD "Normal" class scored a perfect 0% FPR on 171 clips. Further analysis found false-positive rate is strongly correlated with **clip duration** — 0% at 1–5 inference checks per clip, rising to 19.9% at 10+ checks — meaning **per-clip false-positive rate systematically understates the false-alarm rate on a continuously running camera**, which never stops accumulating checks. This directly foreshadows §12.

---

## 7. A Deployment-Blocking Discovery: Scale Mismatch on Wide Camera Views

Following model correction, the corrected system was tested against a real, unlabelled public CCTV stream (a street-crossing camera in Davao City) as a qualitative deployment check. Initial observation: confidence readings fluctuated with traffic on a wide-angle view of the intersection, with no violence occurring.

**Working hypothesis (formed, then tested, then disproven):** it was hypothesized this fluctuation was the model misreading vehicle/crowd motion as violence (a false-positive risk). This was tested directly by taking the same live footage and *synthetically shrinking* the people in it while holding everything else constant. **Confidence did not rise — it barely moved (0.132 → 0.128), and no alarm fired at either scale.** The false-positive hypothesis was rejected by this test.

**The actual mechanism, confirmed by controlled measurement:** every training clip was measured for person size as a percentage of frame height using the existing pose-detection model. Training footage puts a person at a median of **37.1%** of frame height (range 24–60%). The Davao street camera, uncropped, puts a person at roughly **9–12%.** To determine the effect of this gap directly, 40 violent clips the model detects with 100% confidence in their original form were replayed at progressively smaller synthetic scale:

| Person height (% of frame) | 37% | 30% | 22% | 19% | 15% | 9% |
|---|---|---|---|---|---|---|
| **Clips still detected (of 40)** | 40/40 | 34/40 | 25/40 | 22/40 | 7/40 | **0/40** |

There is a sharp cliff between 19% and 15% person-height. **Below approximately 15%, the model does not become less confident — it detects nothing.** This is the correct explanation for the earlier field observation: a wide-angle street camera does not cause false alarms; it causes the system to be **effectively blind**, and a blind camera on a quiet street is statistically indistinguishable from a working camera on a quiet street. For a public-safety system, this is the most dangerous possible failure mode, because it produces no symptom.

### 7.1 Why the obvious fix (cropping the camera view) was rejected

The natural first response — configure each camera to crop/zoom into the region where people are expected — was proposed and then explicitly rejected after review, on the following reasoning: a camera is installed to monitor a specific area; cropping it to compensate for a model limitation reintroduces exactly the blind spot the camera was purchased to eliminate. This would violate the system's core purpose for the sake of a training-data artifact.

### 7.2 Root cause identified in the training pipeline

Investigating why the model had never learned to recognize small people, the clip-augmentation code was found to contain a **scale augmentation that could only zoom in** (random crop 80–100%, resized back up — which only ever makes a person *larger* than in the source footage). **No augmentation in the pipeline had ever shown the model a person smaller than what the raw source footage happened to contain.** This is now understood as the root cause of the scale-blindness finding, not merely a symptom of it.

### 7.3 Coverage-preserving architectural alternative

A tiled inference approach was designed and implemented as an alternative to cropping: the frame is divided into a grid of overlapping regions (each independently classified with its own temporal buffer), so a distant person becomes proportionally larger within their tile **without discarding any part of the camera's field of view.** A controlled test replaying the same shrunk clips through this design recovered **25 of 30** detections that a whole-frame pass missed entirely at the same scale (0 of 30), while covering 100% of the frame. A subtlety was found and addressed during this test: a non-overlapping grid can split a single incident across a tile boundary and cause it to be missed by every tile (measured: a non-overlapping 2×2 grid scored *worse*, at 1/30, than the whole-frame baseline) — solved by overlapping tiles plus retaining one full-frame pass for scene-scale context.

---

## 8. Related Work

- **CUE-Net** (CVPR 2024 Workshop, RWF-2000 SOTA) handles distant/partially-obscured subjects via **spatial cropping around detected people** — the state of the art solves the same scale problem this project found, by cropping to people rather than classifying whole wide frames. This project's `_crop_person()` already implements a CUE-Net-style crop (60% padding, bystander merge), but §10 (T1) found the *prerequisite* — reliably detecting a person at city-camera scale in the first place — fails before the classifier ever runs, which CUE-Net's own benchmark conditions do not require it to face.
- **Skeleton/ST-GCN**-family action recognition is scale-invariant by construction (it operates on pose keypoints, not pixels), and this system already computes those keypoints for its per-track mode — a plausible future direction, gated on the same pose-detection reliability problem at distance (§10, T1).
- **Domain shift between benchmark and deployment CCTV footage** is a documented, actively-researched problem in the video-anomaly-detection literature generally, not unique to this system — this project's own §12 false-alarm-rate gap is a direct, measured instance of it.
- **Weakly-supervised video anomaly detection** (UCF-Crime, MIL-ranking approaches) frames the problem differently: rather than a binary per-clip classifier trained on balanced benchmark clips, these methods train directly on continuous, realistically-imbalanced footage (violence as a rare event, not 50% of the data) — closer to this system's actual deployment condition than RWF-2000/SCVD are, and a candidate direction for future work once enough real-domain footage (§3) exists to train on.
- **CCTV-Fights** (NTU ROSE Lab) is, to date, the closest publicly available dataset to this project's actual deployment condition: genuine CCTV-sourced footage with ground-truth annotation, rather than a curated, balanced benchmark. Acquiring it is in progress (§13).

---

## 9. Phase 0 Results: the Architecture Question, Settled by Measurement

The plan following §7 was to decide — before writing any new training code — whether distant, city-scale people should be handled by cropping to a detected person (the CUE-Net-style approach already partially built into this codebase) or by the coverage-preserving tiled approach of §7.3. Three measurements were run to answer this rather than assume it.

**T1 — can the existing pose detector even find a city-scale person?** The pose model was swept across four input resolutions against people synthetically shrunk to the Davao camera's measured scale (9% of frame height). Result: **the gate failed.** At an affordable resolution (1280px, 30ms/frame), the pose model located the person in only 32.5% of frames. Pushed to the most expensive resolution tested (1920px, 291ms/frame — far past any real-time budget), detection still reached only 46.7%. Person-cropped classification cannot work if the person is never reliably detected in the first place, independent of how good the classifier afterward is.

**T2 — head-to-head confirmation on the same shrunk footage.** Whole-frame, tiled (§7.3), and per-track (pose + crop) modes were run on the same 30 held-out violent clips shrunk to 9% person-height:

| mode | clips detected (of 30) |
|---|---|
| whole-frame | 0/30 |
| per-track (pose + crop) | 2/30 — pose located a person in only 38.0% of frames |
| **tiled (4×4, 25% overlap, + full-frame pass)** | **22/30** |

This corroborates T1 directly: per-track's own architecture is scale-invariant once a crop is obtained, so its near-total failure here traces back to the pose stage failing to find anyone to crop, not to the classifier.

**T3 — cost per camera.** Real-time capacity requires clearing a fixed budget of 33.33ms per frame (the true frame-arrival period at 30fps — the live pipeline calls its detector on *every* frame, not on a longer "check window," which an initial pass at this measurement mistakenly divided by instead, inflating every capacity figure by roughly 15×; this was caught by re-reading the calling code and corrected before being reported here). Measured against the correct budget:

| mode | ms/frame (busy) | cameras sustainable per GPU |
|---|---|---|
| whole-frame | 3.31 | ~10 |
| tiled 4×4 (as first implemented) | 49.89 | 0.67 — cannot sustain even one live camera |
| per-track (pose + crop) | 103.96 | 0.32 — cannot sustain even one live camera |

Tiled mode won T1/T2 on capability but, as first implemented, failed T3 on cost — 17 tile inferences were being run as 17 separate sequential GPU calls per check. This was diagnosed (isolated to the per-call dispatch overhead, not the per-frame buffer maintenance) and fixed by batching all 17 tiles into a single GPU call: an isolated compute-only test measured a consistent ~1.9–2.7× speedup from batching depending on run. Projected against that isolated number alone, this looked sufficient to clear the real-time budget — **that projection was reported prematurely and has since been corrected.**

**Correction, reported for the same reason every other self-correction in this document is:** the isolated speedup test measured GPU compute only, using a pre-made tensor already resident on the GPU. It did not include the cost of building a real 17-tile batch from live video (cropping, resizing, normalizing, and transferring host→device), and — more materially — every full-pipeline measurement taken up to that point was run while a second process (the system's own live dev server, or the measurement chain's own later steps) was concurrently holding the GPU, which was not caught until each earlier "clean" figure was checked against the live process list rather than assumed. With every other GPU consumer confirmed stopped, the honest full-pipeline number is:

| mode | ms/frame (clean, GPU confirmed idle) | cameras sustainable per GPU |
|---|---|---|
| whole-frame | 1.92 | ~17 |
| tiled 4×4, interval=15 (batched, as first implemented) | 56.76 | 0.59 — cannot sustain one live camera |
| per-track (pose + crop) | ~38.9 | ~0.86 |

Batching the 17 tile calls is a real, measured improvement in raw GPU compute, but on its own it did not close the gap — tiled mode's full-pipeline cost remained under the real-time budget by roughly half.

**Repair, tested directly rather than assumed:** two further levers were tried on the same clean, GPU-idle protocol. Reducing the grid from 4×4 (17 tiles) to 3×3 (10 tiles) — trading some overlap margin for ~40% fewer inferences per check — cost exactly one clip out of the 30-clip city-scale recall benchmark (30/30 → 29/30) while dropping cost to 34.34 ms/frame (0.97×, still short). Combining that with a longer check cadence (15 → 20 frames, i.e. checking every ~0.67s instead of every 0.5s at 30fps) reached **28.48 ms/frame — 1.17× real-time capacity — with recall unchanged at 29/30.** This clears the budget with margin and is now the shipped default (`tile_grid: 3`, `tile_check_interval: 20` in `config.json`).

**Decision, now actionable:** tiled whole-frame scene mode (3×3 grid, 25% overlap, plus one full-frame pass, batched inference, 20-frame check interval) is the architecture for full-view city cameras — the only one of the three that reliably detects at city-camera scale (T1/T2), and now the only one that also clears the real-time throughput budget. It is wired into `config.json`/`main.py` as `"mode": "tiled"`, switchable back to `"scene"` or `"track"` by a single config edit, per the requirement that every mode stay reversible. Person-cropped/two-stage detection remains set aside for city cameras on both original grounds (pose cannot reliably find distant people; its own cost also falls short of one real-time camera).

A stale-dependency issue found alongside this work is also fixed: the pose and weapon detectors' `.engine` (TensorRT) files were failing to load ("platform specific tag mismatch" — a TensorRT engine is tied to the exact runtime/driver version it was compiled on, and these predated the current install) and silently falling back to slower PyTorch weights on every run. Both were rebuilt on this machine via the project's existing `optimize_weights.py` and verified to load and run inference successfully.

---

## 10. Phase 1 & 2 Results: Retrain Complete, Honest Numbers In

The whole-frame model was retrained on the same leak-free three-way manifest (§5.6), with the scale-augmentation range widened specifically to close the blind zone identified in §7: the augmentation now shrinks training clips down to as little as 6.7% person-height — below the measured 9% blind floor, with margin — so the model was exposed to city-scale people during training for the first time, not only at inference. Training completed (early stopping, 15 epochs, best validation accuracy 92.9% — a validation-set figure only, selection-optimistic per §5.4).

**City-scale recall — the entire point of this retrain — improved dramatically,** replayed on the same 30 clips shrunk to 9% person-height used throughout Phase 0:

| mode | old weights (baseline) | new (scale-augmented) weights |
|---|---|---|
| whole-frame | 0/30 | **28/30** |
| tiled | 22/30 | **30/30** |

**Honest test-split accuracy, however, regressed against the §6 baseline:**

| Metric | old (deployed) model | new (scale-augmented) model |
|---|---|---|
| Accuracy | 95.0% | **92.5%** |
| Recall | 97.4% | 96.3% |
| Precision | 92.9% | 89.5% |
| False Positive Rate | 7.4% | **11.3%** |

This does not recover with threshold tuning. A full sweep against the same test-split confidences found no threshold that matches the old model on both axes: at the default 0.50 the new model's recall is slightly better (97.6%) but FPR is nearly double (12.4%); pushed as high as 0.95, FPR drops below the old model's (4.5%) but recall falls to 91.8% and accuracy still caps at 93.7%, short of the 95.0% baseline at every threshold tested. **This is a genuine capability trade-off from widening the scale-augmentation range, not a tuning artifact** — the model that can see a person at 9% of frame height pays for that with reduced discrimination at ordinary, close-up scale.

One operational incident occurred during training and is recorded for completeness: partway through, the host machine ran out of committable system memory (unrelated concurrent processes, not a leak in the training code) and the training process crashed. Recovery used the run's own checkpoint/resume mechanism, which restores epoch number, best-validation-accuracy, and early-stopping-patience state rather than resetting them — a bug in exactly this mechanism was one of the fixes reported in §5.5, and it held up correctly here on its first real use. No progress was lost.

A second, unrelated operational lesson came from the cost re-measurement in §9: the automated evaluation chain checked that the training process had exited and that free memory was adequate before calling its own numbers "clean," but had no way to know the system's own live dev server was independently running and holding the GPU the whole time. "GPU idle" turned out to need an explicit process check, not an inference from "nothing I started is running." This is now handled by checking the live process list directly rather than assuming it.

---

## 11. Operational Note: Two Unrelated System Incidents

For completeness, this section records two incidents outside the modeling work itself:

1. **Training crash (§10)** — resolved via the existing checkpoint/resume mechanism; no data or progress lost.
2. **A second machine incident during this same work period:** while downloading the CCTV-Fights dataset (§13) to expand the real-domain training pool, the C: system drive was driven to **0 bytes free**, because the download library's default cache directory (`C:\Users\User\.cache\kagglehub`) is not on this machine's designated project/data drive (D:). The download crashed mid-extraction, leaving a 17.2GB corrupt partial archive. This was diagnosed, the corrupt cache was removed (with explicit confirmation before any deletion, since it touched a location outside the project directory), and the download was relaunched with its cache directory explicitly redirected to D: via the library's `KAGGLEHUB_CACHE` environment variable — the correct, permanent fix rather than a one-time cleanup.

---

## 12. Phase 3 Results: Real-World Validation — a Third, Still-Open Problem

Sections 9–10 fixed detectability (a blind camera now sees people) and re-confirmed benchmark accuracy stayed close to the original baseline. Neither measures the thing that actually determines whether a human operator can use this system: **how often does it cry wolf on ordinary footage with no violence in it?** §6.1 already flagged that per-clip benchmark FPR structurally understates this, because a continuously-running camera never stops accumulating inference checks the way a 5–10 second benchmark clip does. Phase 3 measures it directly, on real, unlabelled footage.

### 12.1 Primary measurement: 35 minutes of continuous Davao intersection footage

Three configurations were run head-to-head on the identical footage, to separate two variables the earlier phases had left confounded (model weights vs. tiled architecture):

| Configuration | Alarms / hour |
|---|---|
| 1. Old weights, scene mode (previously deployed) | 36.00 |
| 2. New (scale-aug) weights, scene mode | **296.57** |
| 3. New (scale-aug) weights, tiled mode | **32.57** |

Configuration 2 is uniquely bad: the scale-augmented model, run in whole-frame scene mode on a genuinely wide view, produces an order-of-magnitude worse false-alarm rate than either alternative — a scale-sensitivity artifact interacting badly with a wide field of view it was never architecturally suited to see as one frame. Configuration 3 (new weights + tiled architecture together) is a **modest net improvement** over the original deployed baseline (32.57 vs 36.00/hour), while also being the only configuration with proven city-scale recall (§10). This head-to-head is why **new weights + tiled mode**, not new weights + scene mode, is the system's current leading candidate (§14).

### 12.2 Offline operating-point sweep

Because the exact confirmation logic (§2.5) is a pure function, raw per-check confidence traces were captured once from the same footage and then replayed offline across a grid of threshold × consecutive-required combinations, without re-running any GPU inference — letting many operating points be tested cheaply against real footage rather than only the RWF-2000/SCVD benchmark. One counterintuitive finding: **for tiled mode specifically, raising the threshold or the consecutive-hit requirement made the false-alarm rate worse, not better.** The mechanism, confirmed rather than assumed: overlapping tiles cause real motion (e.g. a person crossing the frame) to sweep sequentially across several tiles. A **low** bar lets the first tile that sees it confirm and hold the alarm via sticky release — one long alarm. A **high** bar causes several tiles to each partially climb toward confirmation and fall back as the motion moves on — several short, flickering alarms, a worse outcome by the "alarms per hour" metric even though each individual tile is being more conservative. The system's current default operating point (threshold 0.50, consecutive=1) was confirmed by this sweep to already be at or near the best available point for tiled mode on this footage — tuning alone does not resolve the problem in §12.3.

### 12.3 Diverse-footage validation

To check whether the primary-footage result (§12.1) generalizes, the leading candidate (new weights, tiled mode, default operating point) was additionally run against five independent real street-camera clips, captured from different locations and scene types:

| Clip | Scene type | Duration | Alarms/hour |
|---|---|---|---|
| barbershop | quiet storefront | 15.0 min | 4.00 |
| fiesta | crowded festival | 8.7 min | 13.81 |
| streetview1 | street view | 15.0 min | 8.00 |
| tireshop | quiet storefront | 15.0 min | 4.00 |
| traffic | street traffic | 15.0 min | 4.00 |
| **Weighted average (68.7 min total)** | | | **6.11** |

All five clips complete. The pattern is coherent with what the system should do: a genuinely busy, crowded scene (fiesta) runs hotter than quiet storefronts and ordinary traffic (which cluster tightly at 4.00/hour), a sensible response to real activity level rather than random noise. But even the quietest, most favorable scenes still produce one false alert every 15 minutes on footage containing no violence at all — consistent with, not an exception to, the primary-footage finding (§12.1).

### 12.4 Honest assessment: this is not yet a usable false-alarm rate

Commercial video-analytics deployments generally target under roughly one false alarm per camera **per day** to remain usable by a human operator; below that threshold, operators learn to ignore the system, defeating its purpose. The measured rates here (4–14 alarms per **hour**) are roughly two orders of magnitude above that bar. This is presented plainly rather than minimized: **the recall fix (§10) and the false-alarm-rate fix are two different, only partially related problems, and only the first is solved.**

The most likely root cause is the same one identified in §3/§7: every number in this project, including the retrain in §10, is still trained and validated on **RWF-2000 and SCVD** — curated, balanced benchmark clips — never on ordinary, real, continuously-running street footage where "nothing happening" is the overwhelming majority class. The model has still never been shown what boring, ordinary motion (traffic, pedestrians, wind, lighting change) looks like at the scale and framing of a real deployment camera. This is precisely the gap the CCTV-Fights acquisition (§13) and the ongoing live-footage capture (§3) are intended to close, though closing it requires an actual retrain on that data, which had not yet occurred as of this report (§14).

---

## 13. Real-Domain Data Acquisition (In Progress)

Two efforts are underway to close the domain gap identified in §12.4, rather than continuing to tune around it:

1. **CCTV-Fights (Kaggle mirror)** — a 13GB, ground-truth-annotated dataset of genuine CCTV-sourced fight and non-fight footage (NTU ROSE Lab). Download completed and was verified on this machine (redirected to D:, §11): `ground-truth.json` parses cleanly (ActivityNet-style format — `duration`, `subset`, `frame_rate`, and per-clip `annotations` giving the exact `[start_sec, end_sec]` temporal segment of each fight, not just a clip-level label), with **1,000 annotated entries** exactly matching the file counts on disk (`CCTV_DATA`: 140 training / 70 testing / 70 validation = 280 genuine-CCTV clips; `NON_CCTV_DATA`: 360 / 180 / 180 = 720 non-CCTV clips). This is now a training-usable, non-benchmark, real-CCTV data source available to this project — not yet used in a retrain (§16).
2. **Official NTU ROSE Lab registration** — the Kaggle mirror is unofficial; the authoritative source requires account registration and a Release Agreement at the ROSE Lab's own site. This is **not started**, and realistically is not a same-day task — noted here as necessary future work, not a gap in this report.
3. **Continued opportunistic live-footage capture** — the same six Davao/Philippine public CCTV YouTube streams used for §12 continue to be sampled periodically. This can only ever produce real **negative** examples (no violence is expected to occur on camera during ordinary capture windows) — a structural limitation worth stating plainly: real-domain *positive* (violent) examples can only come from an annotated dataset like CCTV-Fights, not from passive live capture.

---

## 14. Phase 4: Closing the Domain Gap — a Real Failure, Then a Real Fix

§12.4 identified the root cause of the false-alarm problem plainly: every model in this project had only ever been trained on curated benchmark clips (RWF-2000/SCVD), never on real, ordinary, continuously-running street footage. §13 described acquiring the data to fix that. This section reports what happened when that data was actually used — including a genuine, measured failure along the way, reported in full rather than only reporting the outcome that worked.

### 14.1 Building a real-domain training set

CCTV-Fights has no separate "safe" video — every one of its 1,000 clips contains at least one annotated real fight (`ground-truth.json`, ActivityNet-style temporal segments). Both training classes were therefore derived from the same 280 genuine-CCTV-source videos: a 5-second window centered on each annotated Fight segment became a positive clip, and the gaps before/between/after those segments — real, ordinary CCTV activity surrounding a real incident, not a curated negative — became negative clips. The same 5-second windowing was applied to the project's own captured Davao/PH live footage (sampled every 30 seconds to avoid over-representing near-duplicate frames of the same static scene), all labeled negative since no violence occurred in it. This produced **1,281 new clips** (606 positive / 467 negative from CCTV-Fights, 208 negative from live capture), folded into an extended manifest alongside the existing RWF-2000/SCVD data via the same content-hash split logic as §3.6/§5.2 — verified leak-free (0 contents in both splits) exactly as before.

### 14.2 The fine-tune, and an honest negative result

The scale-augmented checkpoint (§10) was fine-tuned on this extended manifest — not retrained from scratch — for up to 10 epochs with early stopping (`--unfreeze-blocks 2`, matching the original run). Training completed at epoch 9 (patience exhausted), best validation accuracy 88.6%.

The first real-footage check of this new checkpoint, in the deployed tiled configuration, was a genuine failure: **360.00 alarms/hour on the exact footage that measured 32.57/hour before fine-tuning — an 11x increase, not an improvement.** This is reported in full because it is methodologically important, not despite being a bad result: a checkpoint that looked like the fix, deployed in the architecture already in production, made the system dramatically worse.

### 14.3 Diagnosis: model vs. architecture

Rather than discard the fine-tune outright, two further checks separated whether the checkpoint itself was broken or whether it was a bad pairing with tiled mode specifically:

- **Honest held-out clip-level accuracy** (the extended manifest's untouched test split, 942 clips, evaluated the same one-shot-per-clip way `val_acc` is computed during training): **90.0% accuracy, 87.5% recall, 92.2% precision, 7.4% FPR.** This flatly contradicts a broken model — 7.4% FPR is comparable to the *original* pre-scale-augmentation checkpoint's benchmark FPR (§6), not a degraded one.
- **Scene mode vs. tiled mode, same checkpoint, same footage**: tiled mode reproduced the 360.00/hour failure exactly (210 alarms, both runs) — internally consistent, not a fluke. **Scene mode, same weights, same footage: 0 alarms. 0.00/hour.**

The mechanism this points to: tiled mode runs 10 near-independent classifiers (one per tile) "OR"-combined every check, deliberately tuned toward a low bar per §12.2's finding that raising it makes tiled mode's false-alarm rate *worse*. A checkpoint with even a moderate, ordinary per-clip false-positive rate gets that rate multiplied by 10 independent chances to trigger, every 20 frames, for 35 minutes — compounding a manageable per-clip rate into a catastrophic real-world one. Scene mode, with exactly one classifier, does not have this multiplicative structure.

### 14.4 Confirming scene mode didn't trade away recall

Scene mode was the architecture abandoned in §9 specifically because it could not see people at city-camera scale (0/30 recall, pre-scale-augmentation). Since this checkpoint was *fine-tuned from* the scale-augmented base rather than trained from scratch, whether it retained that capability was the deciding question, not assumed:

| Person height (scale) | 37.1% (1.0x) | 22.3% (0.6x) | 14.8% (0.4x) | 11.1% (0.3x) | 9.3% (0.25x) | 7.4% (0.2x) |
|---|---|---|---|---|---|---|
| Detected (of 40 confidently-violent test clips) | 38/40 | 38/40 | 35/40 | 36/40 | **37/40** | 34/40 |

At 9.3% person height — matching the Davao camera's measured real-world scale (§7) — recall is **37/40 (92.5%)**, comparable to the scale-augmented base checkpoint's own scene-mode figure (28/30, §10). Scale robustness was not lost.

### 14.5 Full real-footage validation, both directions checked

| Footage | Old weights, tiled (prior deployment) | New weights, tiled | **New weights, scene (now deployed)** |
|---|---|---|---|
| davao_capture (35 min, primary) | 32.57/hr | 360.00/hr | **0.00/hr** |
| barbershop | 4.00/hr | — | 0.00/hr |
| fiesta (crowded festival) | 13.81/hr | — | 0.00/hr |
| streetview1 | 8.00/hr | — | 0.00/hr |
| tireshop | 4.00/hr | — | **8.00/hr** |
| traffic | 4.00/hr | — | 0.00/hr |

Five of six clips dropped to zero, including fiesta — the busiest, most crowded scene in the whole validation set, previously the worst performer at 13.81/hour. **One clip, tireshop, got worse (4.00 → 8.00/hour), not better**, and this is reported without smoothing it over.

Frames were pulled at both residual alarm timestamps to identify the actual trigger rather than assume one:

- **t=11.2 min (confidence 0.868)** — a motorcycle crosses the frame at speed, producing rapid lateral motion and motion blur. Nothing resembling an altercation is present.
- **t=14.4 min (confidence 0.610)** — three people stand clustered close together in the street with visible limb movement. This is genuinely ambiguous footage; a human reviewer glancing at a single frame could reasonably look twice.

These are two *different* failure modes, and only the second is the "hard ambiguous case" category. The first — fast vehicle motion misread as violence — is a more ordinary and more fixable error, and suggests the fine-tune's negative pool (drawn from relatively quiet street scenes and CCTV-Fights gap segments) may under-represent fast vehicle traffic specifically.

*Correction to an earlier draft of this report:* this residual was previously attributed to a reclining person appearing "person-on-the-ground"-like. That explanation came from a frame in the **pre-fine-tune** model's run, not from either of the two alarms actually produced by the deployed checkpoint, and is not supported by the evidence above. It is corrected here rather than left standing.

### 14.6 A third axis of domain gap: time of day

Every validation clip in §14.5 was recorded at night, between roughly 21:00 and 02:00 — as was every live-capture negative used in the fine-tune. The 0.00/hour results were therefore measured on the *only* lighting condition the model had ever been shown as "normal." Nothing in the methodology guaranteed those results would hold in daylight, so 150 minutes of midday capture were taken from five of the same streams and re-run at the deployed operating point.

| Daytime footage (30 min each) | False alarms/hour |
|---|---|
| barbershop | **334.00** |
| traffic | 34.00 |
| streetview1 | 8.00 |
| davao_primary | 0.00 |
| tireshop | 0.00 |
| **aggregate (150 min)** | **75.20** |

The same barbershop camera measured **0.00/hour at night and 334.00/hour in daylight**. That is not a marginal degradation; it is the scale problem of §7 repeating along a different axis. The model was taught "night-time Philippine street = normal" and daylight is simply out of distribution — brighter, higher-contrast, busier, with hard shadows and far more pedestrian and vehicle motion per minute.

Two points are worth drawing out for methodology rather than just results. First, the spread across cameras (0.00 to 334.00) is far wider than the aggregate suggests, so **the aggregate is close to meaningless on its own** — a single mean over cameras would have hidden both the catastrophic case and the two clean ones. Second, this was only found because the validation set was deliberately extended along an axis that had been held constant by accident, not by design. Three domain-gap axes have now each independently broken a model that looked finished: **scale** (§7), **scene realism** (§12.4), and **lighting/time-of-day** (here). The reasonable inference is not that this list is now complete.

The fix follows the same pattern as §14.1: 720 daytime negative clips were extracted at a 20-second stride from eight 30-minute midday captures (the original five plus three newly added streams), for inclusion in the combined retrain.

### 14.7 A methodological defect in the split itself

While preparing that retrain, the split rule was re-audited. §5.2's content-hash split guarantees that two *byte-identical* files land on the same side — but CCTV-Fights clips are produced by cutting long source videos into many short segments, and two segments of the same fight are not byte-identical. A diagnostic (`diag_cctvfights_split_leakage.py`) confirmed the consequence: **270 of 280 source videos had segments in both train and test.** The model could reach test-set accuracy partly by recognising scenes it had already been trained on.

The fix is a **group-aware split** (`build_dataset_manifest_grouped.py`): every clip is assigned a group key derived from its origin — source video for CCTV-Fights segments, physical camera for live captures — and the split is drawn over groups, never over clips. Manifest generation now runs **two** leakage checks, byte-level and group-level, and both report zero on the manifests used below.

### 14.8 The honest per-source numbers, and a prediction that was wrong

Re-training on the group-clean split (best 87.4% validation accuracy) and evaluating the untouched test split **broken down by data source**, rather than as a single aggregate:

| Test source | n | Accuracy | Recall | **FPR** |
|---|---|---|---|---|
| **CCTV-Fights (real CCTV)** | 103 | **67.0%** | 83.3% | **44.3%** |
| RWF-2000 (benchmark) | 362 | 93.1% | 92.9% | 6.7% |
| SCVD (benchmark) | 415 | 97.1% | 95.5% | 0.6% |
| **OVERALL** | **880** | 91.9% | 93.4% | 9.5% |

The 91.9% aggregate is **benchmark-carried**: 777 of 880 test clips are RWF/SCVD, so the one source that resembles the deployment target contributes under 12% of the number that would normally be reported as "the result." Reporting only the aggregate would not be false, but it would be misleading, which is the reason for the breakdown.

On real CCTV the failure is lopsided and specific: recall is 83.3%, but **FPR is 44.3%** — the model finds violence when it is there, and also finds it when it is not. This is the same defect the live false-alarm rates in §14.5 and §14.6 measure from the other direction, and it identifies precisely what the remaining work has to reduce.

*A prediction of mine that the data did not support.* Before this evaluation I stated that the split contamination in §14.7 was likely inflating real-CCTV recall substantially — from a true figure of roughly 50–55% up to the observed 84.4%. On the group-clean split, real-CCTV recall came in at **83.3%**: essentially unchanged. The contamination was real, and fixing it was correct for the integrity of every number in this report, but it was **not** materially inflating the result, and the stated magnitude was wrong. Recorded here rather than quietly dropped, because the corrected conclusion changes what to work on next: recall on real CCTV was never the problem, and effort belongs on the false-positive rate.

---

## 15. Camera Integration: Working With Cameras That Already Exist

A detection model that only runs on the specific hardware it was developed against is not deployable to a barangay that already owns cameras. This section documents making the system source-agnostic, and — equally important — the two failure modes that only appeared once it was tested against a real IP camera rather than assumed to work.

### 15.1 The gap, stated precisely

The `cameras` table already stored an `rtsp_url` column, and the dashboard could already save one. It was reasonable to assume from this that RTSP was supported. It was not. The detection core (`maincode/main.py`) resolved its video source as `config.json → camera.index`, an **integer**, opened it with `cv2.VideoCapture(idx, CAP_DSHOW)` — a Windows webcam-only backend that cannot parse a URL at all — and exposed a single `POST /set_camera_index` endpoint whose request schema (`index: int`) rejected a URL outright. The stored `rtsp_url` was never read by anything that opened a capture.

So the system could *record* an RTSP address and never *watch* it. This is worth stating plainly because it is a class of gap that is easy to miss: a feature can be present in the database schema, the API and the UI, and still be absent from the code path that matters.

### 15.2 What was implemented

A camera source is now either a local device index or a stream URL, resolved with the precedence `CAMERA_SOURCE` (environment) → `config.json: camera.source` → `camera.index` (legacy). Both kinds flow through one open/reconnect path, so the pose stage, X3D classifier, clip capture and MJPEG relay never learn which they are looking at.

| Concern | Decision | Why |
|---|---|---|
| Backend | FFmpeg for all string sources; DirectShow only for integer indices | DirectShow cannot open a URL; FFmpeg is what speaks RTSP |
| Transport | `rtsp_transport;tcp` forced | UDP drops frames under congestion, and a torn frame is worse than a late one when the next stage classifies *motion* |
| Resolution | Not forced on network sources | The camera encodes what it encodes; renegotiating can break the stream |
| Timeouts | `CAP_PROP_OPEN_TIMEOUT_MSEC` / `READ_TIMEOUT_MSEC` = 5s | See §15.3 |
| Bad URL | Roll back to the previous source, report the error | An operator mistyping a URL should not lose the feed they had |
| Dead network camera | Retry the URL; **never** fall back to probing local webcams | Silently switching a street camera to whatever USB device is in the server would show a plausible feed of the wrong place — worse than an obviously dead one |
| Credentials | Stripped by regex from every printed, logged and API-returned form of the URL | `rtsp://admin:pass@host/stream1` otherwise travels to the browser and into logs |

Protocol coverage follows from using FFmpeg: RTSP (`rtsp://`, `rtsps://`), HTTP MJPEG, RTMP, and plain file paths for offline replay. RTSP is the interface effectively every CCTV vendor exposes — Hikvision, Dahua, Tapo, Uniview, and any ONVIF-conformant device — so no vendor-specific integration work is required per camera.

### 15.3 Two failures that only a real camera revealed

Both were found by testing against an actual RTSP server (`mediamtx` serving a 720p H.264 stream over a real handshake), not by reasoning about the code. Both would have reached deployment otherwise.

**(a) The timeout that wasn't applied.** The conventional way to set an RTSP timeout is the `OPENCV_FFMPEG_CAPTURE_OPTIONS` environment string (`stimeout;5000000`). Measured against an unreachable address, opening still blocked for the full **30 seconds**: OpenCV installs its own interrupt callback whose hard-coded default overrides FFmpeg's own socket timeout, and it is configurable only through capture *parameters*, not the option string. Worse, the initial fix made it *worse* — 41.7s — because the `CAP_ANY` fallback backend silently ignores those parameters, so a failed 5s FFmpeg attempt was followed by an unbounded 30s retry. Removing the fallback (FFmpeg is the only backend that speaks RTSP, so it protected nothing) brought a dead camera to a bounded **5.0s**. On the reconnect path the original behaviour meant halting detection for half a minute per retry.

**(b) The stall that a webcam cannot reproduce.** With the capture working, the detector connected, ran for a few seconds, then entered a permanent read-fail/reconnect loop and never processed a frame. The server's own log named the cause: `write queue is full`. A USB webcam is a *pull* source — `read()` hands back the current frame. An IP camera is a *push* source: it transmits at its own frame rate regardless of whether anyone is consuming. The per-frame pipeline (pose + X3D + weapon pass) is slower than 30fps at 720p on a GTX 1660 SUPER, so unread frames accumulated in the socket until reads stalled past the timeout. `CAP_PROP_BUFFERSIZE = 1` does not help; the FFmpeg backend ignores it.

The fix is a drain thread (`_NetworkStreamReader`) that reads continuously and retains only the newest frame, exposing the same `read`/`isOpened`/`release` surface so the main loop is unchanged. Discarding the backlog is the correct trade rather than a compromise: **an alert about something that happened forty seconds ago is not an alert.** The reader also never returns the same frame twice — feeding duplicates to a temporal model would insert motionless repeats into the X3D clip buffer and corrupt the exact signal it classifies on. Applied only to live network sources; a file replay is a pull source and draining it would race to the end discarding most of the footage.

### 15.4 Verification

A 14-check suite (`test_rtsp_source.py`) covers source normalisation, credential redaction, the file path, a real RTSP handshake, and bounded failure on an unreachable address — **14/14 passing**, with the RTSP case reading 30 frames at 1280×720 from a live server. Beyond the unit level, the complete detector was booted against that stream and ran the full pipeline: models loaded, source correctly reported as `rtsp://127.0.0.1:8554/cam1`, frames processed, an alert posted to the backend (HTTP 200) and an event clip written. Server-side `write queue is full` events dropped from continuous to **zero** after the drain thread.

### 15.5 What is still not solved

The system now integrates *any* camera; it does not yet integrate *many*. `main.py` processes **one source at a time**, and the dashboard's "All Feeds" grid currently points every tile at the same `/video_feed`. Multi-camera concurrency is the Phase 5 scaling question (§9's ~17 cameras/GPU is a throughput estimate, not a shipped capability), and it is a separate piece of work from source flexibility. The distinction matters for an honest claim: *this system can be pointed at an existing barangay CCTV camera* is now true and tested; *this system can monitor a barangay's whole camera network* is not yet.

---

## 16. Current Deployment Decision

The system is now configured to run **the negatives-fine-tuned checkpoint in scene mode** (`config.json`: `"mode": "scene"`, `scene_model_path": "weights/x3d_xs_violence_scene_3way_nll_scaleaug_negatives.pt"`), deployed and smoke-tested live as of this report. This supersedes the tiled-mode configuration reported in earlier drafts of this document, which is now known — not merely suspected — to fail catastrophically with these particular weights (§14.2). The decision is evidence-based on both axes that matter (§ Context: both missed violence and false alarms carry real cost):

- **False-alarm rate**: 0.00/hour on 5 of 6 real validation clips (down from a 4.00–32.57/hour range), one residual case at 8.00/hour with a known, plausible cause.
- **Recall**: 92.5% at the camera's actual real-world scale, essentially unchanged from the scale-augmented base checkpoint.

**This deployment decision is now known to be conditional on time of day, and should be read together with §14.6.** Every figure above was measured on night footage. The same configuration on 150 minutes of *daytime* capture produces **75.20 false alarms/hour in aggregate**, ranging from 0.00/hour on two cameras to 334.00/hour on one. The configuration therefore remains the best evidenced one available and stays deployed, but it is **not** currently fit for unattended daytime operation, and the corrective retrain (§14.6, §14.8) is the open work.

This is presented as the strongest evidenced configuration to date, **not as a claim that the false-alarm problem is fully solved**: it is validated against six specific pieces of real footage, not a statistically large or fully diverse sample, and the tireshop result shows it is not failure-proof. The previous configuration (old weights, tiled) remains available by editing two lines in `config.json` and is fully reversible, per the project's standing requirement that every mode stay switchable.

---

## 17. Remaining Work

1. **Broaden real-footage validation** of the new scene-mode configuration beyond the six clips in §14.5 — more hours, more locations, more times of day — before treating 0.00/hour as representative rather than an encouraging early result.
2. **Address the two residual failure modes identified in §14.5** — (a) fast vehicle motion misread as violence, which suggests adding fast-traffic footage to the negative pool, a straightforward and testable fix; and (b) close-proximity group standing, the genuinely ambiguous case, which may need pose context rather than more data.
3. Complete official NTU ROSE Lab registration for the authoritative CCTV-Fights source (§13), for redistribution/citation legitimacy beyond the Kaggle mirror.
4. Investigate whether **person-crop mode** offers any further benefit now that scene mode's false-alarm problem is substantially addressed — lower priority than before §14, since the original motivating problem is largely resolved.
5. **Phase 5** — document the final cameras-per-GPU scaling path. Scene mode's cost profile (§9: ~1.92ms/frame, ~17 cameras/GPU) is far better than tiled mode's, which changes the scaling story favorably now that scene mode is the deployed candidate.
6. Consider a milder scale-augmentation ablation, as before (§10), though now a lower priority given §14.4 found no evidence of a recall cost in the current checkpoint.
7. **Drive down the 44.3% real-CCTV false-positive rate (§14.8)** — the single highest-priority item, and now known to be the binding constraint rather than recall. **§19 supplies a large part of the answer without retraining**: on held-out night cameras the deployed operating point produces 54.0 false alarms/hour, and moving `scene_confidence_threshold` 0.5 → 0.7 with `scene_consecutive_required` 1 → 3 reduces that to 6.0/hour for 6.9 points of benchmark recall. That is a two-line `config.json` edit and is pending a decision on the recall trade, not further work. The combined retrain (group-clean split + 720 daytime negatives + the non-CCTV violence pool) is the current attempt; it must be evaluated per-source, not in aggregate, and re-validated on daytime footage rather than only on the night clips that produced §14.5's zeros.
8. **Validate along the untested domain axes before claiming deployability** — at minimum wet-weather footage and a second camera height, given that all three axes tested so far have each broken the model (§18).
9. **Multi-camera concurrency (§15.5)** — source flexibility is done and tested; running more than one camera per process is not, and it is what separates "works on a camera" from "monitors a barangay."

The tiled architecture (§9) remains fully implemented and switchable, and its measurement infrastructure (batched tile inference, the grid/interval tuning) is not wasted work — it was the correct answer to the original scale-blindness problem (§7) and remains available if a future checkpoint doesn't share this one's scene-mode compatibility.

---

## 18. Limitations to State Plainly

- All benchmark accuracy figures in §6/§10 are measured on **RWF-2000 and SCVD**; §14's fine-tune data (CCTV-Fights + live capture) is the first real-domain training signal in this project, and it measurably changed real-footage behavior (§14.5) — direct evidence that the domain-gap diagnosis in §12.4 was correct, not merely plausible.
- **The tiled architecture and this fine-tuned checkpoint are a bad pairing** (§14.2/14.3) — a specific, measured, non-obvious interaction, not a general statement that tiled mode is worse than scene mode. A future checkpoint retrained with tiled mode's compounding structure in mind (e.g., fine-tuned with per-tile false-positive rate as an explicit objective) might not share this failure mode.
- **Real-footage validation (§14.5) covers six clips, roughly 1.7 hours total** — a real improvement in rigor over a single file, but still small next to a deployment that would run continuously, indefinitely, across many cameras. The 0.00/hour results are encouraging, not proof of a solved problem.
- Real-camera **recall on genuine violence** remains formally unmeasured on any of the captured Philippine footage, because none of it contains real violence — §14.4's recall figure comes from replaying benchmark clips at real-world scale, the same proxy methodology used throughout this project, not from a real incident.
- Two other detection capabilities in this system — robbery and vandalism — are implemented as hand-written geometric rules rather than trained models, and report fixed, hardcoded confidence values to the dashboard rather than measured ones. They remain out of scope for this work period (violence detection only, by explicit decision) and are noted here as a known gap for future work.
- **Every headline accuracy figure in this report is an aggregate over data sources that behave very differently.** §14.8 measures 91.9% overall and 67.0% on the only real-CCTV source, from the same evaluation run. Wherever a single number appears without a source breakdown, it should be assumed to be benchmark-weighted.
- **Three separate domain-gap axes have each broken a model that looked finished** — scale (§7), scene realism (§12.4) and lighting/time-of-day (§14.6). Each was invisible until validation was deliberately extended along it. There is no basis for assuming the third is the last; weather, camera height, lens distortion and crowd density are all untested and all plausible.
- The system's live negative footage carries an **unverified label**: no violence is assumed to have occurred during ordinary capture windows because none was observed, but the clips were not exhaustively reviewed frame by frame. A missed real incident inside a "negative" clip would be training the model to ignore exactly what it exists to detect.

---

## 19. The Operating Point: the Largest Available Improvement, and It Is Free

§17.7 names the real-CCTV false-positive rate as the binding constraint. This section measures it on cameras the model has never seen, and finds that most of it is removable by configuration alone — no retraining, no new data.

### 19.1 Baseline on held-out night cameras

Three YouTube-sourced Philippine street cameras were captured overnight, screened (§19.2), and replayed through the deployed scene-mode checkpoint, 10 minutes each, at both the deployed operating point and one step of extra persistence:

| camera | 0.5 / 1 (**deployed**) | 0.5 / 2 |
|---|---|---|
| MncLrf2LsT8 | 12.0 | 6.0 |
| ooU2gpVTJ8Y | 54.0 | 30.0 |
| u8CbGedbI08 | **96.0** | 78.0 |
| **aggregate** | **54.0** (27 alarms / 0.5h) | **38.0** (19 alarms / 0.5h) |

> **Correction (2026-08-13): this table originally mixed two operating points.** It gave the aggregate as **54.0/hour** above per-camera rows of 6.0 / 30.0 / 78.0, which average to 38.0 and cannot produce it. The source file (`baseline_holdout_fa.csv`) records 19 alarms over 0.5 hours = 38.0/hour, and `eval_false_alarms_corpus.py` defaults to `--consecutive 2` — so those rows were 0.5/**2** data presented under a 0.5/1 heading. **The 54.0 aggregate was correct; the row labels were not.**
>
> Re-running the measurement explicitly at `--consecutive 1` on the same three cameras confirms it exactly: **27 alarms / 0.5 h = 54.0/hour**. Both columns above are now measured rather than inferred, and the 0.5/1 per-camera figures (12 / 54 / 96) appear here for the first time — the earlier table understated per-camera severity at the deployed setting by roughly half.

Two things follow. First, 54/hour is roughly one alarm every 67 seconds — not deployable unattended; even the more forgiving 0.5/2 setting is one every 95 seconds. Second, and less obvious, the **8× spread between cameras at the deployed setting** (12 to 96, widening to 13× at 0.5/2) means the aggregate is dominated by the worst one. A single global threshold is being asked to serve cameras whose false-alarm behaviour differs by nearly an order of magnitude, which is the strongest evidence yet for per-camera configuration (§17) rather than one tuned number.

One further caveat on the corpus itself: `corpus/holdout_night` contains **four** screened captures, but this baseline covers only three. `2iENQ0dDmqI` is a legitimate holdout camera that was never included in it, so any future aggregate over all four is not directly comparable to the number above. Comparisons between checkpoints must be made on the *same* videos, and the per-video rows exist precisely so that can be checked rather than assumed.

### 19.2 The operating-point curve

`_smooth_and_confirm()` is a pure function of `(prev_ema, prev_hits, was_confirmed, raw_conf)`, and the detector already logs `raw_conf` for every inference. The entire confirmation stage can therefore be replayed offline at any parameter setting — exactly, not approximately — with no GPU. Recall is measured separately by replaying 250 labelled violent clips through the same function.

| threshold / persistence | false alarms/hr | recall | alert latency |
|---|---|---|---|
| **0.5 / 1 — deployed** | **54.0** | 91.8% | 0.5s |
| 0.5 / 2 | 38.0 | 91.8% | 1.0s |
| 0.7 / 2 | 8.0 | 85.5% | 1.0s |
| **0.7 / 3 — recommended** | **6.0** | **84.9%** | 1.5s |
| 0.8 / 4 | 2.0 | 80.5% | 2.0s |

Moving from 0.5/1 to 0.7/3 is a **9× reduction in false alarms for 6.9 points of recall** and one extra second of latency. The offline sweep independently reproduced the directly-measured 38.0/hr figure at 0.5/2, so the replay and the GPU measurement agree.

Recall figures are from the **fixed 159-clip subset** — the only column-comparable one. The "all clips" table is contaminated because short clips physically cannot satisfy a high persistence requirement, so their apparent recall loss is an artefact of clip length rather than a real miss.

**Three limits on how far this can be pushed:**

- **The low rows are statistically empty.** "2.0/hr" means *one alarm in 30 minutes*; the 0.9 threshold row's zeros mean zero. 2/hr cannot be distinguished from 0/hr on this much footage. Only the 54 → 8 range rests on meaningful counts (27 alarms vs 4).
- **Recall is benchmark recall.** The 250 violent clips are RWF/SCVD, not street CCTV. Recall on Philippine street cameras remains unmeasured, exactly as §18 already states.
- **The false-alarm side assumes no real violence occurred** in the captured footage. Reasonable for 30 minutes of ordinary street activity, but unverified.

Two measurement defects were found and corrected in the sweep tool itself, both of which had flattered the results: it divided alarm counts by **wall-clock** time (inflating every rate 3.9× when replaying recorded video faster than real time), and quoted persistence in wall seconds, so `need=3` displayed as 0.4s when the true wait is 1.5s. Every figure above is post-correction.

### 19.2b Independent confirmation of the recall cost, on 6× the clips

The recall figures above come from a fixed **159-clip** subset, which §19.2 already flags as the only column-comparable one. That is a small number to hang a deployment decision on, so the threshold half of the change was re-measured from the other direction: the deployed scene checkpoint scored on its own honest test split (`dataset_manifest_3way_negatives.json`, **942 clips**, 471 violent / 471 normal, never touched by training or checkpoint selection), with `eval_test_split.py --dump-probs` producing a full sweep from one inference pass.

| threshold | accuracy | recall | precision | FPR |
|---|---|---|---|---|
| 0.1 | 89.7% | 95.3% | 85.7% | 15.9% |
| 0.2 | 90.4% | 93.8% | 87.9% | 13.0% |
| 0.3 | 90.3% | 91.5% | 89.4% | 10.8% |
| 0.4 | 89.9% | 89.4% | 90.3% | 9.6% |
| **0.5 — deployed** | 90.0% | **87.5%** | 92.2% | **7.4%** |
| 0.6 | 89.4% | 84.7% | 93.4% | 5.9% |
| **0.7 — recommended** | 88.0% | **80.3%** | 95.0% | **4.2%** |
| 0.8 | 86.3% | 76.4% | 95.2% | 3.8% |
| 0.9 | 83.2% | 68.6% | 97.0% | 2.1% |

**§19.2's estimate holds.** It put the cost of moving to 0.7 at 6.9 points of recall from 159 clips; this puts the threshold component at **7.2 points** (87.5 → 80.3) from 942. Two independent measurements on different data, agreeing within 0.3 points.

**0.7 sits almost exactly at the knee of the curve**, which is an argument for it that does not depend on the live alarm-rate measurement at all:

| step | recall cost | FPR gained |
|---|---|---|
| 0.5 → 0.6 | −2.8 | −1.5 |
| 0.6 → 0.7 | −4.4 | −1.7 |
| **0.7 → 0.8** | **−3.9** | **−0.4** |
| 0.8 → 0.9 | −7.8 | −1.7 |

Past 0.7 the trade collapses — 3.9 points of recall for 0.4 points of FPR. Pushing the threshold higher is poor value, and §19.2's separate observation that the low rows are statistically empty says the same thing from the alarm-count side.

This also surfaces an option §19.2 did not consider. **Threshold 0.6** cuts clip-level FPR from 7.4% to 5.9% for only **2.8** points of recall, with accuracy essentially unchanged (90.0 → 89.4). It is a materially gentler step than 0.7 while still moving in the right direction — worth having on the table if 0.7/3 is judged too aggressive for a first deployment.

**Scope, stated precisely:** this measures the *threshold* only. Clip-level FPR is not alarms per hour — the persistence change (`consecutive` 1 → 3) acts on continuous footage, where consecutive inferences are correlated, and is captured by §19.2's replay rather than here. The two measurements are complementary, cover different halves of the same change, and agree on the half they share.

### 19.2c The split those numbers came from was leaking, and fixing it exposed something worse

The table above was measured on `dataset_manifest_3way_negatives.json`'s test split — the manifest the deployed checkpoint was trained on. That manifest has **group-level leakage: 176 of its 277 CCTV-Fights source videos have clips in more than one split**, covering 654 clips. `fight_0123_seg0` in train and `fight_0123_gap0` in test: different bytes, same camera, same scene, often the same people.

`dataset_manifest_3way_grouped.json` contains the identical 6,128 clips and the identical 277 source videos and straddles **none**, so `_negatives` was simply built before the group-aware fix landed. Both record `split_rule: bucket=int(sha256[:8],16)%100`, which describes the *bucketing* but not whether the key was the clip hash or the source video — **so a leaky manifest is indistinguishable from a clean one by its own metadata.** That is why this survived unnoticed through every number derived from it. `check_manifest_group_leakage.py` now tests any manifest directly and runs in the standing check suite.

Re-measuring on the 804 test clips whose entire source video stays in test (138 demoted; test purity verified as zero straddling groups touching test) gave a **higher** score, not a lower one:

| | 942 clips (leaking) | 804 clips (leak-free) |
|---|---|---|
| accuracy | 90.0% | **94.0%** |
| recall | 87.5% | **90.0%** |
| FPR | 7.4% | **2.0%** |

Leakage inflating a score is the expected direction, so this needed explaining rather than accepting. The 138 demoted clips are **100% CCTV-Fights**, while the surviving 804 are only 48% — removing the contamination also removed the hardest domain, and the two numbers therefore differ in *composition* as well as in leakage. Neither is "the corrected version" of the other.

Because the demoted clips are exactly the difference between two measured confusion matrices, their own scores follow by subtraction (and check out exactly: 71 violent, 67 normal):

| the 138 real-CCTV clips | |
|---|---|
| accuracy | **66.7%** |
| recall | **73.2%** |
| precision | 65.8% |
| FPR | **40.3%** |

**73.2% recall / 40.3% FPR are the exact figures quoted inside `group_key()`'s own docstring** as the real-CCTV numbers that "could not be trusted in either direction". This reproduces them independently and identifies precisely which clips produced them.

The conclusion is worse than the leakage alone implied. These clips were **helped** by leakage — the model trained on their siblings — and it still gets only two thirds of them right, with a 40% false-positive rate. The honest reading is that real-CCTV performance is *at best* that, and the aggregate 94.0% is flattering mainly because RWF and SCVD, which are curated and comparatively easy, dominate the surviving test set. This is the same conclusion §17.7 and §14.8 reach from other directions, now with a specific number attached to a specific set of clips.

**What survives for the operating-point decision.** The recall cost of raising the threshold from 0.5 to 0.7 is now measured three ways on three different clip sets: **−6.9** points (§19.2, 159 clips), **−7.2** points (§19.2b, 942 leaking clips), and **−5.2** points (this leak-free 804). The absolute levels move around with the population, as they should; the *delta* is stable at roughly 5–7 points. The recommendation rests on that delta, and the delta holds.

### 19.3 Capture screening caught a camera that would have corrupted the holdout

Of 12 overnight captures, `screen_capture_quality.py` rejected one: **12.45 cuts/minute**, meaning a multiplexed feed switching between several cameras rather than one fixed view. It was in the *holdout* set, which is the quiet way this kind of defect does damage — it would not have poisoned training, it would have poisoned the number reported as honest, since every scene cut is a whole-frame content change a motion-sensitive model can read as an event. Quarantined, not deleted.

---

## 20. Weapon False Positives Are Mostly One Stuck Detection

The weapon detector's false-alarm counts were dominated not by many distinct errors but by **static scene objects re-counted every frame**. Measured box-centre stability across whole clips:

| camera | class | n | centre stdev (fraction of frame) |
|---|---|---|---|
| tireshop | Gun | 68 | (0.0209, 0.0111) |
| newcam2 | Knife | 55 | (0.0386, **0.0000**) |
| streetview1 | Knife | 16 | (**0.0004**, **0.0003**) |

Inspecting the frames directly — rather than inferring from counts — the tire shop's detector locks onto a **utility pole**, a box 48% of frame width by 100% of frame height, and reports it as a Gun at 0.93 confidence frame after frame. Its "6,438 detections/hour" was one stuck detection re-counted, not thousands of distinct errors.

Two earlier explanations for these detections ("impact wrenches", "vendor knives") were inferences from detection counts and were **both wrong**; looking at the footage disproved them. This is recorded because it is the third instance in this project of a count-based inference failing where a visual check succeeded.

A position-stability filter (`_is_static_scene_object()` in `main.py`, switchable via `config.json`) rejects detections whose box centre stays fixed across a rolling window. Measured removal: **97.4% / 81.0% / 78.1%** on streetview1 / tireshop / barbershop.

**The cost, stated rather than hidden:** a genuinely motionless weapon is suppressed — a knife left on a table, or someone standing very still holding a gun for longer than the window. Two things bound it: the window is a few seconds, and an object that starts moving is released after **3 observations (~30px of travel)**, verified against the shipped function. Note that newcam2, a market, only dropped 23.6% — its knife detections *do* move, consistent with vendors genuinely handling knives. The filter correctly leaves those alone, and a real knife in a market remains a problem this rule cannot and should not solve.

---

## 21. UCF-Crime: Why Most of It Was Rejected

UCF-Crime (95.9 GiB, 1,900 videos) was acquired to supply real continuous CCTV. What it actually yields for this project is much smaller than the headline size suggests, and the reasons are worth recording.

### 21.1 Frame-accurate labels cover only the test split

Frame-level anomaly annotations exist for **290 videos**. The other ~1,600 carry **video-level labels only** — a ten-minute "Fighting" video is labelled violent though the fight is twenty seconds of it. Cutting clips from those would produce ~97% mislabelled positives, teaching the model that the CCTV *look* means violence — the precise failure already recorded at §14.7. Multiple-instance learning is the published method for using them; that is a separate build, not a data-extraction step.

Usable frame-accurate yield: **10 videos / ~7.3 minutes** of violence; 68 videos / ~31.5 minutes across all crime types.

### 21.2 The violent clips are 96% indoor — rejected

1,818 clips were extracted. A contact sheet (one row per source video) was then built and **viewed**, per the standing requirement to inspect data before training it:

| source | clips | setting |
|---|---|---|
| Assault006 | **115 (54% of the class)** | indoor shop, one camera |
| Fighting047 | 27 | indoor auto garage |
| Fighting003 | 21 | indoor metro station |
| Assault010 | 16 | indoor dormitory, very dark |
| Fighting042 | 15 | indoor lobby |
| **Assault011** | **9** | **outdoor street** |
| Fighting018 | 5 | indoor corridor |
| Fighting033 | 4 | underpass |
| Abuse028 / Abuse030 | 2 | **animal cruelty (RSPCA watermark), no people fighting** |

Only **9 of 214 clips (4%)** are outdoor street violence — the only kind in scope for a streetlight (§0). Meanwhile the UCF *normal* clips sample at roughly **57% outdoor**. Training on both would offer the model a shortcut it would certainly take — *indoor ⇒ violent* — making outdoor violence **harder** to separate. The entire violent half was therefore excluded (`EXCLUDE_DIRS` in the manifest builder, with the measurements recorded inline).

This is a negative result about a dataset, not about the extraction: the pipeline works and the clips are on disk. Recovering data does not make it the right data.

### 21.3 What was kept, and two defects fixed to keep it honestly

**Kept: 900 normal clips** — real continuous CCTV, majority outdoor, day and night, from cameras nothing in this pipeline has seen. As negatives they cannot teach a false positive, and they target §19.1's 54/hour directly. Robbery (541) and vandalism (163) clips are retained on disk but not manifested: this classifier is binary violent/normal, robbery frequently involves no violence, and vandalism involves none by definition.

Two defects had to be fixed before these could be used without corrupting the measurement:

1. **Group-aware splitting.** Split assignment was by content hash, which is correct for byte-identical duplicates but wrong for clips *cut from* a shared source: the 115 clips from Assault006 are different bytes and would have scattered across train/val/test — same scene, same camera, same people on both sides. `group_key()` now buckets UCF clips by source video (12 unit tests, including confirmation that RWF/SCVD/CCTV-Fights/live-capture splits are unchanged so prior numbers stay comparable).

2. **The balance discard.** The builder equalised classes per split by dropping the surplus. Adding 900 negatives therefore produced **930 balance-drops** — the new negatives *displaced* existing ones and the manifest came out *smaller* than before (3,057/3,057 vs 3,283/3,283). More negatives were bought and none were kept. `--train-neg-ratio` now allows the training split to tilt; **val and test remain 50/50**, so accuracy and FPR keep their meaning and stay comparable with every earlier figure.

Resulting manifest (`dataset_manifest_3way_ucf.json`): train 3,057 violent / 3,925 normal, val 643/643, test 655/655. Byte-level leakage 0, group-level leakage 0.

### 21.4 The robbery and vandalism clips have the same defect, for different reasons

Both classes were screened visually before any future use, since the violence class showed that folder labels are not a description of content.

**Robbery (541 clips, 44 source videos).** Dominated by **Shoplifting**, which is indoor retail by nature — shop counters, aisles, electronics displays. The genuinely in-scope material is a smaller outdoor subset: a night car break-in (Stealing058, 51 clips), a gate intrusion (Burglary079, 27), a driveway approach (Robbery050, 15). Beyond the indoor problem, **shoplifting is a different detection problem entirely**: there is no violent motion signature, only a person quietly placing an item into a bag. A motion-based clip classifier such as X3D is the wrong instrument for it regardless of camera placement.

**Vandalism (163 clips, 14 source videos).** Roughly **69% is arson**, not property damage — bright flames and sensor blowout, a visual signature with nothing in common with the graffiti/damage the rule-based detector targets. Fire detection on a streetlight may be a worthwhile capability in its own right, but it is a separate model, not this class. Genuine outdoor property damage amounts to roughly **17 clips** (Vandalism015, and Vandalism028 in which someone climbs on a car). One source (Arson011) is unusable at any scope: a face filling the frame while the camera itself is tampered with.

**The consistent pattern:** UCF-Crime is an *anomaly* dataset, and every crime class in it is dominated by a setting or a signature that does not match an outdoor streetlight — indoor retail for robbery, fire for vandalism, indoor premises for violence. The 900 normal clips remain the one unambiguously valuable component, which is what §21.3's manifest uses.

---

## 22. Motionless Clips Labelled Violent

The UCF review established that folder labels do not describe content. Applying the same scepticism to the datasets already *in* training turned up a defect that had been there the whole time.

### 22.1 The measurement

X3D is a motion classifier. A clip containing no motion cannot demonstrate what violence looks like; the only thing it can teach is that stillness is compatible with the violent label — which is precisely the direction that produces false alarms on an empty street, the system's main outstanding problem at 54 alarms/hour.

`audit_frozen_clips.py` scored mean inter-frame absolute difference (160×96 grayscale, to suppress sensor noise and compression shimmer) over a 1,500-clip sample per label:

| | median motion | frozen (< 0.5) |
|---|---|---|
| normal | 0.801 | 528 / 1500 (35.2%) |
| **violent** | 2.969 | **138 / 1500 (9.2%)** |

Violent clips move 3.7× more, as they should. The 35.2% figure for normals is expected and harmless — a static camera watching an empty street genuinely is still, and those clips are exactly what teaches the model not to alarm. The 9.2% is the problem.

### 22.2 Ruling out the obvious confound first

Mean absolute pixel difference is not scale-free: a dark, low-contrast night clip compresses every difference, so real motion in the dark can score lower than trivial motion in daylight. Reporting these as mislabelled without checking would have been a measurement error dressed up as a data finding. `audit_frozen_confound.py` measured brightness and contrast alongside a contrast-normalised motion score:

| | brightness | contrast | motion / contrast |
|---|---|---|---|
| violent, frozen | 96.2 | 56.3 | **0.006** |
| violent, moving | 99.6 | 60.2 | **0.055** |

Frozen and moving violent clips are equally bright and equally contrasty, and the 9× gap survives normalisation. The metric is reading stillness, not darkness.

### 22.3 What the footage actually shows

Per the standing rule that label claims are verified by viewing, `review_frozen_violent.py` rendered the 20 lowest-motion violent clips. They do not merely lack motion — **most contain no people at all**: empty car parks, an empty covered bicycle shelter, an empty garden path, parked cars. Several also contain a hard scene cut mid-clip (a stairwell becoming a car park).

Most originate from `SCVD_converted_sec_split` — one-second splits of longer SCVD videos, where every second inherits the **video-level** label whether or not anything happens in it. This is the same video-level-label problem that got UCF-Crime rejected in §21, except it was already inside the training set.

Frozen rate by origin, among sampled violent clips:

| origin | frozen |
|---|---|
| SCVD_converted | 10/48 (20.8%) |
| SCVD_converted_sec_split | 55/282 (19.5%) |
| archive/Complete Dataset | 45/450 (10.0%) |
| CCTV_Fights_Extracted | 21/210 (10.0%) |
| CCTVFights_NonCCTV_Extracted | **7/510 (1.4%)** |

The non-CCTV CCTV-Fights extraction — the most recent addition, cut from explicit temporal annotations rather than inherited video labels — is by far the cleanest. That is consistent with the diagnosis: the defect tracks how the labels were assigned, not which dataset they came from.

### 22.4 Status

`build_dataset_manifest_grouped.py` gained `--min-violent-motion` (default **0**, off). Only violent clips are ever filtered. The default path was verified to rebuild `dataset_manifest_3way_ucf.json` byte-identically, so every number measured so far remains valid.

Whether removing these clips actually improves the false-alarm rate is **a hypothesis, not a result.** It is plausible — but so was "UCF violence will help", and that turned out to be wrong on inspection. It gets a controlled run against the unfiltered manifest at the same geometry, and the outcome is reported either way.

### 22.5 The filter creates a measurement trap, and the comparison has to dodge it

Built at `--min-violent-motion 0.5`, `dataset_manifest_3way_ucf_motion.json` drops **378 violent clips (8.7%)** — close to the 9.2% the 1,500-clip sample predicted. The loss is concentrated exactly where the per-origin frozen rates said it would be: Weaponized 956 → 778 (**18.6%**), Fight 3399 → 3199 (5.9%).

The split assignment is stable, as the content-hash rule intends — verified explicitly: **zero clips changed split**, and each filtered split is a strict subset of the unfiltered one (train 6982 → 6730, val 1286 → 1164, test 1310 → 1180).

That stability is what creates the trap. The filter removes violent clips from **val and test as well as train**, so the filtered test split is not the same benchmark — it is the old benchmark **with the hardest cases deleted**. A motionless clip labelled violent is one the model essentially cannot get right, so dropping 130 of them from the test split raises measured accuracy on its own, before any training effect exists. Reporting "filtered manifest scores higher on its own test split" would be measuring the filter, not the model.

The comparison therefore uses two things that cannot be gamed this way:

1. **False alarms/hour on `corpus/holdout_night`** — footage entirely outside every manifest, so no split arithmetic touches it. This is also the metric the project actually cares about.
2. **Accuracy on the UNFILTERED 1,310-clip test split**, for both models — one fixed benchmark, including the hard clips, scored identically for each.

The same argument applies to **val**, with a smaller consequence. Val is the model-*selection* split, so the filtered run picks its best epoch against a filtered val set (1,164 vs 1,286). That is the right thing to do in practice — you select on the data you believe — but it means the two runs' reported `val_acc` figures are **not comparable to each other**, nor to any other run's.

That is not a hypothetical caution: §24.1 records this exact mistake being made in this report, comparing a 48×224 run's val accuracy against a number from a different manifest. Every `val_acc` in this project is measured against its own manifest's split. Only the two external measurements above compare models honestly. The reference point that *is* trustworthy for the 13×160 model is **88.1% on the shared 1,116-clip test split** (§24.2), because both models were scored on the identical clips.

---

## 23. Zero-Frame Clips From an ffmpeg Failure Mode

`build_dataset_manifest_grouped.py` reported `1 identical files carry BOTH labels`. Tracing it found three 261-byte MP4s decoding to **zero frames**, all cut from source video `fight_0520` — two labelled Fight, one labelled NonFight.

**Cause.** CCTV-Fights' `ground-truth.json` claims a longer duration for that video than the file contains, so every requested cut began past the end of the stream. **ffmpeg exits 0 in that case**, writing a structurally valid MP4 with no frames, so the extractor's `check=True` saw success.

**Why it mattered.** `train_x3d_full.py`'s `load_clip_frames()` returns an all-black clip for a video reporting zero frames, silently — deliberate robustness that here would have fed a blank clip into training under a real label. The label conflict is the only reason it surfaced; had the `gap1` file not existed, the two identical Fight clips would have deduped into one zero-frame **violent** example with nothing to flag it.

**Fixed.** `ffmpeg_extract()` in `extract_cctv_fights_noncctv.py` and `extract_cctv_fights_clips.py` now decodes each cut and requires 8 frames, deleting and raising otherwise. `extract_ucf_crime.py`'s `size > 1024` check was upgraded to a frame count — a byte threshold cannot catch a larger empty file. A size scan across all 10,026 dataset videos confirmed **exactly these three** were affected; they are in `quarantine/empty_clips/` with the write-up, not deleted. `verify_manifest_integrity.py` now checks every manifest entry exists and decodes before any training run.

---

## 24. Negative Result: a Bigger Clip Geometry Did Not Help

X3D-XS is normally trained at longer clips and larger frames than this project uses (13 frames at 160px). The obvious hypothesis was that the small geometry was the accuracy ceiling, so a run at **48 frames × 224px** — 3.7× the frames and 2× the resolution — was given a full 10 epochs.

| epoch | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|
| val acc | 80.3 | 78.6 | 81.0 | 81.9 | **83.4** | 82.0 | 77.5 |

Best: **83.4%** at epoch 8.

### 24.1 Correction: the first version of this comparison was invalid

This section originally read "the 13×160 run reached 86.6%, so the bigger geometry lost by 3.2 points." **That comparison was not valid, and the 3.2-point figure should be disregarded.**

Every training run in this project reports `val_acc` against *its own* manifest's validation split, and those splits are not the same set:

| checkpoint | val_acc | manifest | val size |
|---|---|---|---|
| `3way_nll` | 0.9446 | 3way | 722 |
| `..._scaleaug_negatives` | 0.8864 | 3way_negatives | 898 |
| `3way_full` | 0.8656 | 3way_full | 1280 |
| `geom48x224` | 0.8336 | **3way_clean** | **1100** |
| `ucf_neg` | 0.8701 | 3way_ucf | 1286 |

The 86.6% came from `3way_full`; the 48×224 run trained and validated on `3way_clean`, whose val split is a strict subset (1,100 of those 1,280 clips). Two different benchmarks. More broadly, **none of the numbers in that column are comparable to each other**, so the apparent decline across runs from 94.5% to 86.6% to 83.4% is substantially a change of benchmark rather than of model quality. This is the same error §22.5 warns about, made in this report before it was written down.

### 24.2 The comparison done properly

`3way_clean` and `3way_full` turn out to share an **identical 1,116-clip test split** (the content-hash split rule is deterministic, so a clip lands in the same bucket in both), and that split is disjoint from both manifests' train and val sets — verified explicitly. So both checkpoints can be scored on one fixed benchmark:

- `x3d_xs_violence_best_geom48x224.pt` — 48 frames × 224px
- `x3d_xs_violence_best_3way_full.pt` — 13 frames × 160px

evaluated with `eval_test_split.py`, which now takes each checkpoint's geometry from its `.meta.json` sidecar (§25.3 — without that fix the 48×224 model would have been fed 13×160 clips and scored nonsense).

Both scored on the identical 1,116 clips:

| | 48 × 224 | 13 × 160 |
|---|---|---|
| accuracy | 81.4% | **88.1%** |
| recall | 69.5% (TP 388, FN 170) | **92.1%** (TP 514, FN 44) |
| precision | **91.1%** | 85.2% |
| FPR | **6.8%** (FP 38) | 15.9% (FP 89) |

**The original conclusion survives, and by a wider margin than the invalid version claimed: 6.7 points of accuracy, not 3.2.**

### 24.3 But "worse" is the wrong word for the shape of it

The two models do not differ the way a good model differs from a bad one. The 48×224 model is dramatically more **conservative**: it gives up 22.6 points of recall and buys back less than half the false-positive rate (6.8% vs 15.9%) and 5.9 points of precision.

That is a description of *where a model sits on its curve*, not automatically of how good the curve is — and both numbers above are taken at `argmax`, i.e. a single threshold of 0.5. A single-point comparison genuinely cannot separate "this model is worse" from "this model is more conservative", which matters here more than usual, because §19 exists entirely to move the operating point around and a 6.8% FPR is very attractive for a system whose main problem is false alarms.

What can be said from one point: **Youden's J** (recall + specificity − 1) is 0.627 for 48×224 against **0.762** for 13×160. A gap that size indicates a genuinely better curve rather than a luckier threshold, so the conclusion stands.

It is not fully settled, and the remaining step is cheap: `eval_test_split.py` now supports `--dump-probs` and prints a nine-point threshold sweep from the probabilities it already computed, so a full curve for both models costs one GPU-minute each once the training queue clears. Until then the claim is "13×160 is better on the evidence available", not "proven dominant at every threshold".

### 24.4 The cost argument is independent and decisive on its own

48 frames at 224px is roughly **13× the decode-and-inference work** of 13×160 per clip — visible directly in this evaluation, where the two models ran on the same CPU over the same 1,116 clips and the larger one took nearly an order of magnitude longer. Training cost about 2× per epoch. For a system whose scaling story is cameras-per-GPU (§ Phase 5), a geometry that costs an order of magnitude more per inference would have to be *much* better to justify itself, and it is not better at all.

**13×160 remains the deployment geometry**, now on measured grounds rather than inherited ones.

---

## 25. Two Ways the Measurements Could Have Been Measuring the Wrong Code

Both of these were found by trying to *prove* an assumption rather than restate it. Neither produced a wrong number that reached this report, but both could have, without any visible symptom.

### 25.1 A stale module was shadowing the shipped detector

`D:\EcoVisionImagesTraining\` contained a **7,801-byte** copy of `x3d_violence_detector.py` dated 7 July, next to the measurement scripts. The shipped module is **43,712 bytes**. Running any script from that directory puts it on `sys.path` first, so the local copy wins.

Most scripts there defend with `sys.path.insert(0, ".../maincode")`. Two did not: **`test_x3d_true_heldout.py`** — the honest held-out evaluator named in this project's verification steps — and `generate_eval_report.py`.

What makes this the dangerous kind of bug: the fossil still defines `X3DViolenceDetector`, the one symbol those two scripts import. The import succeeded. Nothing crashed, nothing warned, and the scripts would have evaluated July-era code while reporting the result as current. A *missing* symbol fails loudly; a stale but present one fails silently.

The scripts that already pinned `maincode` — `eval_false_alarms_corpus.py`, the `phase0_*`/`phase3_*` measurement scripts, `measure_scale_recall*.py`, `compare_modes.py` — resolved correctly all along, and additionally use `SceneViolenceDetector`/`TiledSceneViolenceDetector`, which the fossil does not contain and could not have supplied. **Those results stand.**

Fixed: both scripts now pin `maincode` first with the reason recorded inline; the fossil is in `quarantine/stale_module/` with a write-up; `check_stale_detector_copy.py` verifies both conditions on demand.

### 25.2 The operating-point sweep used a copy of the FSM, kept in sync by a comment

`sweep_operating_point.py` reimplements `_smooth_and_confirm()` so the operating point can be replayed from recorded confidences with no GPU — the design that made the whole threshold × persistence curve affordable. Its comment read: *"Mirrors maincode/x3d_violence_detector.py::_smooth_and_confirm. If that changes, this must change."*

A comment is not a mechanism, and the exposure is total: **every false-alarms-per-hour figure in §19, including the 54/hr baseline and the 6/hr recommendation, came out of the copy rather than the original.** Any drift would mean those numbers describe a system that was never deployed.

`test_sweep_matches_shipped_fsm.py` now drives both implementations with identical confidence traces — ramps, alternating spikes, values sitting inside the release-hysteresis band, values straddling each threshold by ±0.001, and randomised sequences — across all 7 thresholds × 5 persistence values, and requires identical `(confirmed, ema, hits)` at every step. It also checks the default-argument path the three live call sites depend on, and the cold-start rule that seeds the EMA from the first reading instead of blending against zero.

Result: **51,100 transitions compared, zero mismatches.** The §19 operating-point numbers are confirmed to describe the code that actually ships.

### 25.3 The test-split evaluator ignored the checkpoint's input geometry

`maincode/x3d_violence_detector.py` has treated a checkpoint's `.meta.json` sidecar as authoritative over `config.json` for some time, because a wrong `frame_size` costs accuracy with nothing to point at. `eval_test_split.py` — which produces the honest test-split accuracy figures — had no such check. It builds `ViolenceClipDataset`, which calls `load_clip_frames()` with `num_frames=None, size=None`, resolving `train_x3d_full`'s module globals at call time: **13 frames at 160px, always**.

This was a live near-miss. The 48×224 run in §24 had just finished; evaluating that checkpoint with this script would have fed **13×160 clips to weights trained at 48×224** and printed an accuracy that looks entirely real. The failure is silent in both directions — nothing in the tensor shapes objects, because the model accepts either.

Fixed: the script now reads the sidecar, adopts the checkpoint's geometry (announcing the override), and **refuses to run without one** unless `--allow-missing-meta` is passed. Verified both ways — the refusal fires on `x3d_xs_violence_best.pt`, which has no sidecar, and the override correctly reads 48f × 224px from the geometry run's sidecar against the 13f × 160px default.

`torch.load` in the same script was also pinned to `weights_only=True`, matching §23's change elsewhere.

---

## 26. Real-CCTV Negatives Halve the False-Alarm Rate

§21 rejected UCF-Crime's violence classes after visual review and kept only its **900 normal clips** — real continuous CCTV, roughly 57% outdoor street, day and night, on cameras nothing in this pipeline had seen. §22 flagged that this was a hypothesis, not a result, and owed a controlled run.

`ucf_neg` is that run: the same proven 13×160 geometry, the same initialisation weights, the same unfreeze depth, differing from its predecessor **only** in the manifest. It early-stopped at epoch 8 with its best at epoch 2 (val 87.0%) — converging in two epochs and then memorising, which is what fine-tuning an already-converged checkpoint on modest extra data looks like.

Per §24.1, that 87.0% says nothing on its own. The measurement that matters is false alarms on held-out night footage, scored against the deployed checkpoint on **identical video** at the **deployed** 0.5/1 operating point, one decode feeding both models:

| | deployed baseline | `ucf_neg` | change |
|---|---|---|---|
| **three baseline cameras** (§19.1) | 54.0/hr (27 alarms) | **26.0/hr (13)** | **−51.9%** |
| all four holdout cameras | 76.5/hr (51 alarms) | **27.0/hr (18)** | **−64.7%** |

Per camera, on the four-camera set:

| camera | baseline | `ucf_neg` |
|---|---|---|
| 2iENQ0dDmqI | 144.0 | **30.0** |
| ooU2gpVTJ8Y | 54.0 | **6.0** |
| u8CbGedbI08 | 96.0 | **54.0** |
| MncLrf2LsT8 | 12.0 | 18.0 |

**900 clips of real street CCTV cut the false-alarm rate roughly in half, with no architecture change and no new labelling.** For a system whose binding constraint is false positives (§17.7, §14.8), this is the largest single improvement measured in this work period other than the operating point itself — and unlike the operating point it costs no recall at a fixed threshold, because it changes what the model believes rather than how confident it must be.

**Honest limits.**

- MncLrf2LsT8 got *worse* (12.0 → 18.0/hr), which is **2 alarms versus 3**. At these counts a one-alarm difference is noise, and `analyze_false_alarm_csv.py` prints raw counts beside every rate specifically so that cannot be read as a trend.
- Even the three-camera aggregate is 27 vs 13 alarms over half an hour. The direction is clear and the effect is large, but the precision is not — this establishes "roughly halved", not "51.9%".
- The corpus holds **four** screened captures while §19.1's baseline used three (`2iENQ0dDmqI` was never in it), so the four-camera aggregate is *not* comparable to the historical 54.0. Both rows are reported, computed as total alarms ÷ total hours over videos both models actually ran on.
- **Recall on these cameras remains unmeasured**, exactly as §18 states: the footage contains no labelled violence. That leaves one serious loophole — a model that simply alarms *less* would produce the same table as a model that discriminates *better*. §26.1 closes it.

### 26.1 It is not desensitisation: recall went up, not down

Halving false alarms is only good news if detection survived. The two checkpoints were therefore scored on **1,194 clips held out from both of them** — clips in the UCF manifest's test split that are either absent from the negatives manifest entirely or test there with no sibling leakage, verified at zero straddling groups. Neither model trained on any of them.

| at threshold 0.5 | deployed baseline | `ucf_neg` |
|---|---|---|
| accuracy | 86.6% | **88.9%** |
| recall | 84.5% | **89.1%** |
| precision | 88.9% | 89.2% |
| FPR | 11.2% | 11.4% |

**Recall rose 4.6 points at an unchanged false-positive rate.** The improvement is not a quieter model; it is a better one. It holds across the curve rather than at one lucky threshold — at 0.7 `ucf_neg` reaches 80.9% recall at 6.7% FPR, while the baseline must go to 0.8 to reach a comparable 6.9% FPR and gets only 73.4% recall there. Youden's J at each model's best threshold: **79.2 for `ucf_neg` against 75.8** for the baseline.

### 26.2 The benchmark could not see the improvement that matters

Read the two measurements together:

| | benchmark FPR (1,194 clips) | real footage (3 cameras) |
|---|---|---|
| deployed baseline | 11.2% | 54.0 alarms/hr |
| `ucf_neg` | 11.4% | **26.0 alarms/hr** |

**Benchmark false-positive rate is essentially identical, while real-camera false alarms halved.** Had this work relied on the test split alone — the standard thing to do — the single largest false-alarm improvement of the period would have registered as *no change at all*, or marginally worse.

The reason is domain, not statistics. The benchmark's negatives are RWF `NonFight` and SCVD `Normal`: curated, framed, mostly indoor or close-range. Real street CCTV at night is none of those things. A model can be excellent at rejecting the first kind and hopeless at the second, and clip-level FPR on a curated set cannot tell the difference. This is the same gap §19.2c measured from the other side, where the real-CCTV subset scored 40.3% FPR against 2.0% on the curated remainder.

Practical consequence, worth stating as a rule rather than an observation: **benchmark FPR is not a proxy for false alarms per hour on a real camera, and must not be used as one.** Every false-alarm claim in this report rests on recorded footage from the target domain for exactly this reason.


## 27. Negative Result: Pose, Wrist-Velocity and Motion-Localisation Gates All Lose to the Threshold

A gate is a cheap check that runs alongside the classifier and can veto an
alarm. Gates are attractive because they need no retraining and revert with one
line, and dangerous for the same reason: *any* veto reduces false alarms, so a
gate that vetoes indiscriminately looks identical to a gate that discriminates
unless recall is measured beside it.

Four were proposed after a live session on `agdao_market` produced 32 alarms in
38.5 minutes on footage a human confirmed contained nothing:

| gate | rule | intended target |
|---|---|---|
| `pose2` | at least 2 people within N body-lengths | vehicles, animals, empty scenes |
| `wrist` | peak per-person wrist speed above a threshold | ordinary close-range activity |
| `localized` | fraction of frame-difference energy inside person boxes | passing vehicles, camera pans |
| `unstuck` | veto a confidence pinned while the scene barely changes | the 242 s alarm on a static market |

All four were measured on 60 minutes of held-out Davao footage
(`agdao_market`, `agdao_flyover`, `lyns_restaurant`) plus 89 violent test
clips, recording every gate's features at every inference point in a single
decode so all gates saw identical frames.

### 27.1 The result

Scored at the deployed operating point (0.5 / 1), with the veto wired as
`block` — the classifier's own state left untouched and only the alarm
withheld, which is both the honest description of a veto and the wiring that
stays cleanly revertible:

| gate | alarms/hr | recall | threshold alone, same recall | verdict |
|---|---|---|---|---|
| none | 63.0 | 85.4% | — | — |
| `pose1` | 57.0 | 68.5% | 20.0 | worse |
| `pose2` | 54.0 | 55.1% | 8.0 | worse |
| `close1.5` | 45.0 | 46.1% | 1.0 | worse |
| `wrist12` | 8.0 | 37.1% | 0.0 | worse |
| `local0.5` | 15.0 | 34.8% | 0.0 | worse |
| all three | 0.0 | 12.4% | 0.0 | same |

**Not one gate sits below the threshold-only curve.** `wrist12` cuts false
alarms by 87%, which reads as a triumph until the recall column shows 85.4% →
37.1%; raising the threshold to 0.90 reaches 1 alarm/hour at 50.6% recall,
strictly better on both axes with no pose model, no tracker and no additional
per-frame cost.

The comparison was made deliberately unfavourable to the gates: each is
compared against the *lowest-alarm* threshold that still meets its recall, so a
gate only wins if it beats the best available alternative.

### 27.2 Why each failed

**Motion localisation is confounded with person size.** Median `localized` by
person count on the flyover:

| people in frame | 0 | 1 | 2–3 | 4–7 | 8+ |
|---|---|---|---|---|---|
| median `localized` | 0.000 | 0.029 | 0.104 | 0.222 | 0.644 |

It rises monotonically with the frame area people occupy, so it substantially
measures *how big the people are*, not *whether people caused the motion*. On a
wide camera where a person is 8% of frame height it reads low whether they are
fighting or standing still — precisely the cameras it was meant to help.

**Proximity fails in crowds.** Assault requires closeness, but so does a
market. Firing points had closest-pair distances of 0.28–0.85 body-lengths on
all three cameras: in any crowd some pair is always touching, so a proximity
gate never fires where it is needed.

**Wrist velocity barely separates.** Median tracked wrist speed at firing
points versus all points: 2.94 → 3.21 body-lengths/s on the market, 1.90 → 3.35
on the flyover, 3.97 → 4.75 at Lyn's. The 87% alarm reduction `wrist12`
produces comes from vetoing nearly everything, not from discriminating.

### 27.3 A measurement bug that would have sold the wrist gate

The first wrist implementation compared each wrist to the *nearest* wrist in
the previous frame, with no track association. In a crowd the nearest wrist
belongs to a different person, so the metric was silently measuring identity
switches. It produced values up to **177 body-lengths per frame** — a wrist
cannot travel 177 torso lengths between two frames, and the implausibility of
the magnitude is the only thing that revealed the bug.

Rebuilt on ByteTrack, so every comparison is a person against their own
previous position, with a keypoint-confidence floor and a plausibility ceiling
above which comparisons are counted and discarded rather than silently kept:

| | untracked (void) | tracked |
|---|---|---|
| max | 177.7 | 29.7 |
| p90 | — | 9.3 |
| median | — | 4.2 |

Pose is sampled every 3 frames rather than at the classifier's cadence: a 0.5 s
gap is far longer than a punch, so sampling at the alarm rate would measure
where an arm ended up rather than how fast it moved.

**All wrist figures recorded before this fix are void**, including those in the
earlier pose-separation experiment.

### 27.4 What this cost and what it bought

Nothing was wired into the live path at any point — every gate lived in a
standalone measurement script — so the negative result required no revert.

It also produced the largest available improvement of the session, from the
threshold sweep the gates were being compared against:

| threshold | alarms/hr | recall |
|---|---|---|
| **0.50 (deployed)** | **63.0** | **85.4%** |
| 0.52 | 56.0 | 85.4% |
| 0.54 | 50.0 | 85.4% |
| **0.55** | **42.0** | **85.4%** |
| 0.56 | 40.0 | 84.3% |

**0.50 → 0.55 removes a third of all false alarms at zero recall cost** — the
identical 76 of 89 clips are detected at both settings, so this is not a
rounding artefact but dead space below the point where any violent clip's
confidence lands.

---

## 28. False Alarms Are Camera-Specific, Not Time-of-Day-Specific

The working hypothesis after §26 was that the model's negatives lacked daytime
Philippine street footage. That hypothesis was wrong in two ways, and the
correction changes what the fix has to be.

**First, the daytime data was already there.** The training negatives contain
833 daytime Davao clips from 7 cameras, two of which (`newcam1`, W Aquino
market area; `newcam2`, Bankerohan market) are market scenes. The gap was
volume, not absence: 180 market clips out of 5,097 negatives is 3.5%.

**Second, and decisively, the model is already clean on those cameras.**
Running the deployed detector across 253 minutes of newly captured daytime
TRAIN footage and cutting a clip wherever smoothed confidence exceeded 0.45:

| cameras | hard windows in 20 min each |
|---|---|
| 12 of 14 TRAIN cameras | **0** |
| `Soliman_Street_cam_5` | 2 |
| `Bankerohan` | 1 |

Against, on the same day and the same hours:

| camera | alarms/hr at 0.5 |
|---|---|
| `agdao_flyover` | 46 |
| `agdao_market` | 9 |
| `lyns_restaurant` | 8 |

The false alarms are concentrated on specific cameras, not on a time of day or
a scene type. Hard-negative mining from TRAIN cameras therefore yields almost
nothing — those cameras do not trigger the model in the first place.

### 28.1 Per-camera threshold calibration

If cameras differ in what "quiet" looks like, one global threshold is
necessarily too low for the noisy ones and too high for the rest. Calibrating
per camera is standard practice and, for a streetlight-mounted unit, a natural
install step: record a few quiet minutes, set one scalar, done — no retraining
and no second model.

Measured on a temporal split, threshold set from each camera's first half and
every alarm counted on the second half the calibration never saw:

| | flyover | market | Lyn's | total | recall |
|---|---|---|---|---|---|
| global 0.55 | 120/hr | 0 | 18/hr | 46/hr | 85.4% |
| global 0.70 | — | — | — | 26/hr | 69.7% |
| **per-camera p99.5** | 42/hr @ 0.78 | **0** @ 0.63 | 18/hr @ 0.55 | **20/hr** | 62–85% by camera |

The gain improves with network size: quiet cameras sit at the 0.55 floor and
keep full recall, and only the problem camera pays. A global threshold forces
every camera to accept the worst camera's compromise — and with 12 of 14
cameras producing no alarms at all, that is most of the network paying for one.

A temporal split is a weaker guarantee than a held-out camera and is stated
rather than hidden. It is, however, the correct guarantee for this method,
because in deployment the calibration genuinely does come from that same
camera's earlier footage.

### 28.2 The flyover is not fixable by thresholding

Even calibrated to 0.86 — near the point of switching the camera off — the
flyover still produces 18 alarms/hour. No threshold rescues it.

The obvious explanation is §7's scale finding — an elevated view puts people at
6–12% of frame height, below the band where the model was measured to be blind.
**That explanation was recorded here first and is wrong**; §29 measures the
current model at 9% person height and finds 71.9% recall, not zero. The scale
limit §7 describes belongs to a checkpoint that predates scale augmentation.

The flyover's alarm rate is therefore an unexplained observation rather than a
diagnosed one. Vehicle traffic across an elevated view is the leading
hypothesis — the alarms coincide with heavy traffic, and the reviewed alarm
frames are dominated by vehicles — but the TRAIN-role `traffic` camera (Leon
Garcia Street) produces zero alarms in 20 minutes, so "traffic" alone does not
account for it. The mechanism is open.

**A methodological constraint applies either way, and should be recorded rather
than worked around:** `agdao_flyover` is a HOLDOUT camera, and the inventory
contains no TRAIN-role camera with comparable elevated geometry. There is
currently no way to fix the flyover *and* honestly measure the fix. The options
are to promote another elevated camera to TRAIN, or to document the flyover as
a known-bad case.

### 28.3 A mining bug worth recording

The first hard-negative run reported 60 hard windows from three minutes of one
camera — a rate 80× every other camera. The file was still being written by
ffmpeg. A partially-written file decodes as truncated, corrupt h264, and
**corrupt frames score high**: the run's own log ended with
`error while decoding MB 30 43`.

Mining it would have taught the model that video breakup is normal street
activity. This is the same failure family as §23's zero-frame clips — a file
existing is not evidence it contains anything — and it is very likely the
mechanism behind the field observation that low-quality video is marked violent
almost immediately.

The miner now skips files touched within 90 seconds and takes duration from
`ffprobe` rather than the container header, which reports full duration for a
file whose tail does not yet exist.


## 29. The Scale Problem Is Already Solved, and Tiling Is Now the Wrong Answer

§7 recorded a deployment-blocking discovery: replaying 40 clips the model
detected at 1.000 confidence while shrinking the people in them gave 40/40
detections at 37% person-height and **0/40 at 9%**. Not less confident —
blind. That measurement drove the entire architecture plan, and
`TiledSceneViolenceDetector` exists because of it.

That measurement was made on a checkpoint predating scale augmentation. The
currently deployed `ucf_neg_motion` was fine-tuned from
`x3d_xs_violence_best_3way_nll_scaleaug_negatives.pt`, and the trainer applies
`ZOOM_OUT_PROB = 0.45` with `ZOOM_OUT_MIN = 0.18` — 45% of augmented clips are
shrunk to between 18% and 90% of linear size, taking a median 37.1%
person-height down to as little as 6.7%.

Re-measured on 89 violent test clips through the same shrink transform the
repository's own `--shrink` harness uses:

| person height | ~37% (full) | ~13% | ~9% |
|---|---|---|---|
| §7, pre-augmentation checkpoint | 40/40 | — | **0/40** |
| deployed `ucf_neg_motion` | 85.4% | 80.9% | **71.9%** |

**Graceful degradation, not a cliff.** The augmentation worked, and the
blindness that motivated the tiled architecture no longer exists.

### 29.1 Tiled mode measured properly, and rejected

`TiledSceneViolenceDetector` had never been measured for false alarms — only
for recall on shrunk clips. It fires when ANY tile confirms, which at grid=3
plus the full frame is 10 independent state machines and 10 chances to be
wrong per check. Measured on the same 60 minutes of held-out Davao footage as
every other false-alarm figure in this report:

At full scale, at matched recall:

| | alarms/hr | recall |
|---|---|---|
| whole-frame @ 0.55 | **42** | 85.4% |
| tiled ≥2 tiles @ 0.75 | 211 | 85.4% |

At 9% person-height, expressed as the best recall available inside an alarm
budget — which is the form an operator actually faces:

| alarms/hr budget | whole-frame | tiled |
|---|---|---|
| 10/hr | 19.1% @ 0.85 | unreachable |
| 20/hr | 32.6% @ 0.75 | 1.1% |
| 50/hr | **70.8% @ 0.55** | 9.0% |

Tiling reaches a higher recall ceiling than whole-frame ever does (97.8% at
full scale) but only at 294 alarms/hour, which is an alert nobody reads.
Requiring 2 or 3 tiles to agree reduces the alarms but never brings the
trade below the whole-frame curve.

**Tiled mode loses at every scale and every alarm budget, at roughly 8x the
compute.** It should not be deployed, and the architecture question §9 settled
in its favour should be considered reopened and settled the other way — by
augmentation rather than by architecture.

This is a better outcome than the plan anticipated. Tiling was measured at
28.48 ms/frame, 1.17x real-time, unable to sustain a second concurrent camera
on a GTX 1660 SUPER. Whole-frame runs at 3.3 ms/frame. Solving the scale
problem in the training data instead of the architecture keeps the per-camera
cost roughly 8x lower, which is the difference between about 1 camera per GPU
and about 15 — the same argument §5 made for person-crop mode, arrived at from
the opposite direction.

### 29.2 Why the earlier tiling result did not transfer

The prior evidence for tiling was "whole-frame 0/30, 3x3 tiles 25/30" on
shrunk clips. Both halves of that comparison were recall-only. Nothing was
wrong with the measurement; it simply answered half the question, and the
missing half reversed the conclusion.

The general form is worth stating, because it is the same error the gate
experiment in §27 was designed to avoid: **a change that increases detections
must be measured on false alarms, and a change that reduces false alarms must
be measured on recall.** Tiling multiplies the number of independent chances to
fire, so it necessarily improves recall and necessarily worsens false alarms;
measuring only the first guarantees a favourable answer.

### 29.3 What this leaves unexplained

Correcting the scale premise removes the explanation §28.2 originally offered
for the flyover's 46 alarms/hour. The model is not blind on that camera. The
alarm rate is real and reproduced across two separate 20-minute recordings,
but its mechanism is now open rather than diagnosed, and the report should say
so rather than retain a tidy explanation that measurement has contradicted.

Reviewing the alarm footage frame by frame corrected two further assumptions
and produced one hypothesis that did not survive testing:

- **The held-out flyover recording is NIGHT footage**, not daytime. Wet road,
  headlights, streetlight glare. It had been reasoned about as a daytime
  camera throughout §28.
- **People on it are close to the camera, not small** — 40–60% of frame
  height at the bus stop, the opposite of the inventory's "elevated, small
  people" description. The camera is a PTZ and its framing is not a fixed
  property, so any conclusion keyed to its geometry is keyed to something that
  moves.
- **One alarm was a camera pan**, with the whole scene sweeping and
  motion-blurred; YOLO reported `airplanex8` and zero people on it. Camera
  motion filling the frame is a plausible violence signature and would be
  cheap to detect, so it was tested across all 161 flyover firing points —
  and **rejected**: only 6% fall in the top decile of global scene motion
  against 10% expected by chance. Pans occur but do not drive the alarm rate.

A third hypothesis was then tested and also rejected. `audit_quality_bias.py`
found, across 581 normal benchmark clips, that lower SHARPNESS correlates with
scoring violent (Spearman rho −0.192) while lower RESOLUTION does not — false
positives are in fact **2× higher on the higher-resolution half** (14.1% vs
7.2%). That splits the field observation "low quality video is marked violent"
into a true half and a false one, and blur is the true half. A panning PTZ
produces heavy blur at full resolution, so it fit.

It does not survive measurement either. Sharpness (variance of the Laplacian)
at every inference point, against each camera's own baseline:

| camera | alarms in the blurriest decile | chance | enrichment |
|---|---|---|---|
| `agdao_flyover` | 4% | 10% | **0.4×** |
| `agdao_market` | 5% | 10% | **0.5×** |

**All three artefact hypotheses are depleted among alarms, not enriched**
(scale, 0.6× for global motion, 0.4× for blur). Read together they say
something useful rather than nothing: the model fires on *clean, sharp,
moderate-motion* frames — the ones where it can see the scene properly. The
false alarms are not an artefact of degraded video at all.

What is left is the plainest explanation, and the one that survived three
attempts to replace it with something cleverer: **the model looks at ordinary
night bus-stop and roadside traffic activity and finds it genuinely
violence-like.** That is a negative-data coverage problem, not a signal
processing one, and it is consistent with the flyover's confidence being
elevated *throughout* rather than spiking on events — its median inter-frame
motion is 4.58 against the market's 0.46. A camera that is simply busier all
the time is also the case per-camera calibration (§28.1) exists for, which is
why calibration reduces it from 120 to 42 alarms/hour where no global threshold
could.

The corresponding action is more night negatives, not another filter: the
training negatives run 630 daytime to 203 night, while the failing camera is a
night camera.

---

## 30. Two of the Three Holdout Cameras Share a Venue With Training Cameras

Verifying the night capture surfaced something that had gone unnoticed through
every measurement in this report. `corpus_night/TRAIN` contains
`Agdao_Public_Market_PTZ` — while `agdao_market_heldout.ts` is one of the three
cameras that every false-alarm figure here is measured on. 60 clips from the
TRAIN market camera are already in the manifest.

Stream roles in `stream_inventory.txt` are assigned per **YouTube video ID**, and
two IDs can be two channels restreaming one camera, two cameras in one place, or
genuinely unrelated views. None of that is visible in a file listing.
`check_holdout_overlap.py` pulls four frames from each candidate and puts them
side by side, deliberately leaving the verdict to a human: a PTZ camera at two
zoom levels is one camera and would score as different, while two cameras across
one street are two cameras and could score as similar.

### 30.1 No camera is in both roles

| venue | HOLDOUT | TRAIN | same camera? |
|---|---|---|---|
| Agdao Market | `u8CbGedbI08` tight night view of a food stall | `nTHJUQqW3wc` wide daytime street, blue hoarding | **no** |
| outside Lyn's | `MncLrf2LsT8` wide road junction at night | `vnniDOWtM3Q` street-level shopfront; `7-oD0lZA7JQ` covered eatery | **no** |
| Agdao flyover | `15weHVdoNFs` | *(none)* | — |

The market pair is decided by more than framing: the two carry different on-screen
display formats — `MM-DD-YYYY` left-aligned with a `Cam 1 ZOOM` label, versus
`DD-MM-YYYY` right-aligned with a `JazBaz Philippines` watermark. Different DVR
overlay means a different recording system, which means a different camera.

**So the manifest is not leaking.** No holdout footage is in training.

### 30.2 But the venues overlap, and that is an alternative explanation

Two of three holdout cameras have a training camera at the same venue — same
street furniture, same lighting, same passers-by, same hours. The inventory
itself already records "four Lyn's restaurant angles". Ranking the three cameras
by how much venue presence they have in training reproduces the ranking of how
much they improved:

| holdout camera | training camera at same venue | alarms before | after | change |
|---|---|---|---|---|
| agdao_flyover | none | 93 | 84 | −10% |
| outside Lyn's | 2 angles | 15 | 9 | −40% |
| agdao_market | 1 angle, 60 clips | 15 | **3** | **−80%** |

The camera with no venue overlap improved least; the camera with the most
overlap improved most. That ordering is exactly what venue-level leakage would
produce. It is *also* exactly what "we added negatives from busy Philippine
market scenes, and market cameras got better" would produce — which is the
intended mechanism. **The two explanations are not separable with the data on
hand**, and no amount of re-analysis of these three cameras will separate them.

### 30.3 What follows

Nothing was deleted. Removing the market and Lyn's training clips would spend
real negative diversity to fix a measurement problem, and would not even fix it
— it would only move the venue overlap into the past.

The correct response is to the *reporting*, not the data:

- **`agdao_flyover` is the headline number.** It is the only holdout camera with
  no training camera at its location, which makes its 84 alarms the most
  trustworthy figure in this report and, not coincidentally, the least
  flattering one. §29's conclusion — that the flyover's alarms are a
  night-coverage problem — is unaffected, since that camera has no overlap.

  Stated precisely, because the weaker claim is the true one: seven training
  cameras *are* in Agdao district (House View, Jet Wash, Outside Cynthia, Van
  Storage, Vulcanizing, Outside Lodi's, Public Market). None shares the
  flyover's view, subject, or elevation — they are shopfronts at street level,
  it is an elevated road and bus stop. So the flyover has **district-level**
  co-location but no venue-level overlap, which is a materially different and
  much weaker form of shared context than pointing a second camera at the same
  storefront.
- The market and Lyn's figures are reported as **venue-adjacent** and are an
  upper bound on generalisation, not a measure of it.
- The next holdout camera added should be chosen for having *no* training
  presence, which is the property that was never a selection criterion here.

This is the third measurement-integrity error found by checking rather than
assuming (after §25's two), and the pattern holds: each was invisible in
summary statistics and obvious in four frames side by side.

---

## 31. The One Free Win: `consecutive_required`, and the Mismatch That Hid It

Four algorithmic interventions have now been tested and rejected (§27, §29),
each losing to a 0.05 threshold change. This section reports the opposite
outcome — a change that removes **65% of false alarms at no measured cost in
recall** — and the measurement error that kept it invisible.

### 31.1 Every false-alarm figure in this report was taken at the wrong setting

`eval_false_alarms_corpus.py` defaults to `--consecutive 2`. `main.py:495`
constructs `SceneViolenceDetector(device=TARGET_DEVICE)` with **no** consecutive
argument, so the live system used `config.json`'s `scene_consecutive_required`,
which was **1**.

The two never matched. Replaying the deployed model at threshold 0.50:

| `consecutive` | alarms/hr | flyover | market | lyns |
|---|---|---|---|---|
| **1 — what deployment ran** | **49.0** | 38 | 2 | 9 |
| 2 — what every measurement used | 32.0 | 28 | 1 | 3 |

So the published rates — corpus_neg 32.0/hr, ucf_neg_motion 41.0/hr, and the
84/3/9 per-camera split — **understated live behaviour by roughly 53%**. The
*comparisons* between checkpoints survive, because both sides were measured the
same way; the absolute numbers do not.

This is the third measurement-integrity error found by checking rather than
assuming (§25 has two more, §30 a fourth). It shares their signature: nothing in
any output looked wrong, because both halves of the system were individually
behaving exactly as written.

### 31.2 Why the setting had never been tuned

`consecutive_required` has been untunable for a reason that is arithmetic, not
empirical. Of 3,413 violent clips available to this project the longest is 137
seconds and **the second longest is 11**; most are under 7. At the detector's
inference stride a 5-second clip yields only two or three decision points, so
`need >= 3` fails on it regardless of how confident the model becomes. Earlier
attempts to tune this measured that limitation and mistook it for a result.

A continuously-running camera has no such ceiling. The measurement had to move
to continuous footage or not be made at all.

### 31.3 Measuring it on continuous footage

`build_spliced_recall_set.py` takes 40 violent clips from the **test** split,
each at least 4 seconds, and splices them into real Davao night street footage
with 25 seconds of ordinary activity on either side — enough for the EMA to
settle into "quiet street" before each event. Exact event windows go to a
sidecar JSON; `score_spliced_recall.py` then replays the shipped
`_smooth_and_confirm()` and asks whether an alarm rose inside each window.

At threshold 0.50, against held-out camera alarms from §31.1:

| `consecutive` | events detected | recall | alarms/hr |
|---|---|---|---|
| 1 | 38/40 | 95.0% | 49.0 |
| 2 | 38/40 | 95.0% | 32.0 |
| **3** | **38/40** | **95.0%** | **17.0** |
| 4 | 36/40 | 90.0% | 9.0 |
| 5 | 35/40 | 87.5% | — |

**3 is the last free step; 4 is the first that costs.** Going from the deployed
1 to 3 loses none of 40 events and takes 49.0 alarms/hour to 17.0.

Deployed in `config.json` with a full rollback note. Reverting is one edit.

### 31.4 What this number is not

- **40 events gives about ±2.5 points of resolution.** "Zero cost" means "lost
  none of 40", not "provably lossless". A 2–3% true cost would be invisible here.
- **Spliced recall is an upper bound.** The splice is a hard cut between two
  cameras, resolutions and lighting conditions, and a large visual change is
  the kind of thing this model already responds to. A real assault on the
  flyover does not arrive that way.
- **The violent clips are not street CCTV.** Inspecting the splice points
  (`check_splices.py`) showed most are indoor, several carry YouTube
  compilation watermarks — `INSTANT KARMA OFFICIAL 2015`, `FLIPAGRAM` — one has
  a visible YouTube player UI, and several are portrait phone video letterboxed
  into frame. **The 91.4% benchmark recall this project quotes is measured on
  that footage.** This does not invalidate §31.3, which is a question about the
  state machine's temporal behaviour rather than about domain, but it should
  temper every recall figure in this report.

### 31.5 The pattern, updated

Five interventions have now been measured. The scoreboard is consistent and
worth stating plainly, because it is the most transferable thing here:

| intervention | outcome |
|---|---|
| pose / wrist-velocity / motion-localisation gates | lost to threshold |
| tiled inference | lost at every scale, at 8× compute |
| three flyover artefact hypotheses | all depleted among alarms |
| **more and more diverse negatives** | **54 → 26 → ~0 → 32 alarms/hr** |
| **`consecutive_required` 1 → 3** | **49 → 17 alarms/hr, free** |

Everything clever lost. What worked was more representative data, and correctly
configuring a temporal parameter that had been mismeasured for the whole
project.

---

## 32. Robbery and Vandalism: Building Two Classes That Had No Data

Both classes shipped as rule-based placeholders transmitting invented
confidence constants (`0.895` and `0.84`) to the dashboard. This section covers
building real models for them, and it is mostly a record of what the data
would not support.

### 32.1 The project was using 3% of the footage it owned

`extract_ucf_crime.py` takes only the 68 UCF-Crime videos carrying
frame-accurate temporal annotations and skips ~1,600 that have video-level
labels only. Surveying the archive against that policy:

| class | videos in archive | used | median size | under 15 MB |
|---|---|---|---|---|
| **Vandalism** | 50 | **5** | 12.0 MB | 30 |
| **Robbery** | 150 | **5** | 13.8 MB | 84 |
| Burglary | 100 | 13 | 17.5 MB | 45 |
| Stealing | 100 | 5 | 18.1 MB | 42 |

After filtering to outdoor and dropping arson, vandalism came to **two usable
source videos**. Two scenes cannot be split into train, validation and test.

### 32.2 Two ways to unlock the rest, both rejected by measurement

The skip policy's justification is a ratio — "a ten-minute Fighting video is
labelled violent though the fight is twenty seconds of it" — and a ratio is
empirical. Most of the unused videos are short. Two hypotheses followed, and
the 68 annotated videos are ground truth for both.

**Hypothesis 1: short videos are mostly anomaly, so video-level labels are
nearly segment-accurate.** Measured (`where_is_the_anomaly.py`): median anomaly
coverage on sub-15 MB videos is **0.32**, and that is an *upper* bound because
a video runs past its last annotated frame by an unknown amount. Cutting blind
would mislabel roughly two positives in three. **The original policy was right
and the hypothesis was wrong.**

**Hypothesis 2: motion localises the act, so rank windows by motion.** Measured
by comparing annotated crime clips against same-camera normal clips from the
same 21 sources: crime is the busier of the two in **11 sources and the quieter
in 10**. A coin flip. On `Stealing058` the normal footage is nine times busier
than the theft; on `Vandalism007`, more than twice.

Both were cheap to test and would have been expensive to assume.

### 32.3 What worked: reading them

All 86 Vandalism and Arson videos were extracted and rendered as 24-frame
timestamped filmstrips (`build_vandalism_filmstrips.py`). Spans were then read
off by eye and written in UCF's own annotation format, so the existing
extractors consume them with no changes. **11 videos newly annotated**, taking
vandalism from 2 usable outdoor sources to 18.

Four were deliberately excluded with the reason recorded, because a wrong
positive is worse than a missing one:

| video | why excluded |
|---|---|
| `Vandalism040` | Indoor. The overview sheet suggested outdoor railings; the full-resolution filmstrip showed a workshop interior. |
| `Vandalism038` | 94 s of a child riding a BMX. A positive here teaches that cycling is vandalism. |
| `Vandalism004` | A *vehicle* striking a road barrier, no person acting. Real property damage, but it would fire whenever a car brushes street furniture. |
| `Vandalism039` | Streetlight glare washes out the frame; no act locatable, so no honest span. |

### 32.4 The negative design that makes indoor footage safe

Indoor robbery was originally excluded because it teaches "indoor = crime" to a
camera that only sees outdoors — the shortcut that got UCF's violence half
dropped (§14.7). **That objection dies once negatives come from the same
videos.** Negatives here are the non-crime spans of the same source videos
(`extract_ucf_same_camera_normals.py`), so a shop interior appears on both
sides of the label and carries no information about it. The model cannot take
the shortcut because the shortcut does not separate the classes.

That unlocked every robbery source: **15 outdoor-only became 43**.

### 32.5 The datasets

| | clips | source videos | train / val / test **scenes** | leakage |
|---|---|---|---|---|
| robbery | 795 | 43 | 26 / 9 / 8 | 0 |
| vandalism | 215 | 18 | 11 / 4 / 3 | 0 |

Splits are by source video, never by clip. **Any accuracy from these is a
measurement over 8 and 3 scenes respectively**, and the manifest builder prints
that line so it cannot be quoted without it.

### 32.5a Results: robbery works, vandalism does not

Both models initialised from the deployed violence checkpoint, `unfreeze_blocks
= 2`, evaluated on their held-out **scenes**.

**Robbery — 139 clips from 8 unseen scenes:**

| threshold | accuracy | recall | precision | FPR |
|---|---|---|---|---|
| 0.5 | 76.3% | 79.6% | 62.9% | 25.6% |
| **0.7** | **84.2%** | **65.3%** | **86.5%** | **5.6%** |
| 0.8 | 84.2% | 57.1% | 96.6% | 1.1% |

This is a working model. At 0.7 it identifies two thirds of robbery clips in
scenes it has never seen, while firing on 5.6% of normal clips from those same
cameras. It is defensible as an MVP with the threshold stated.

**Vandalism — 37 clips from 3 unseen scenes: not deployable.**

| threshold | accuracy | recall | precision | FPR |
|---|---|---|---|---|
| 0.5 | 70.3% | 86.2% | 78.1% | **87.5%** |
| 0.9 | 73.0% | 75.9% | 88.0% | 37.5% |

The FPR column is the finding: at the operating threshold the model fires on
**7 of the 8 normal clips in the test set**. Its 70.3% accuracy is *below* the
78.4% obtainable by labelling every clip vandalism, so it has learned something
worse than the base rate. Validation told the same story during training —
accuracy fell from 42.4% to 33.3% while training accuracy climbed to 86.2%,
which is a model memorising 11 training scenes, not learning vandalism.

Two things are true at once and both belong in the write-up: the test set is 3
scenes and 8 negatives, so it is **too small to measure anything reliably** —
and what little it measures is bad. Neither justifies shipping it.

**The conclusion is about scene count, not about effort.** Annotating 11 videos
by hand took vandalism from 2 usable sources to 18 and the model still does not
generalise. Robbery, at 26 training scenes, does. The gap between those two
numbers is where this class becomes learnable, and the cheapest route across it
is filming: every new location is a new scene, which is exactly the resource
that is scarce.

### 32.6 Four errors caught, three of them mine

- **A dead source.** `Burglary076` has positive median motion 0.015 against
  0.2–7.6 for every other source — a still corridor where the annotated
  burglary produces no measurable change. 30 clips that could teach a motion
  model nothing.
- **Police title cards as training data.** Several UCF sources are
  police-released appeal videos with editorial slides cut in — a Greater
  Manchester Police card, a "Can you identify these men?" graphic, a
  `#CrimeMustFall` overlay. Faithfully collected as negatives, and meaningless.
- **A frozen backbone.** The first robbery run trained 4,098 of 2,978,772
  parameters because `--unfreeze-blocks` defaults to 0 while the violence model
  used 2. It scored 55.2% accuracy and 11.9% recall on held-out scenes. Void.
- **A duplicate `source_of`.** The manifest builder carried its own copy that
  did not know the `vand_` prefix, so all 11 hand-annotated vandalism sources
  parsed as source `vand_VandalismNNN`, failed the category regex, and were
  filed as **robbery**. No error anywhere; the vandalism manifest simply came
  out with 7 sources instead of 18. Fixed by importing the one definition.

### 32.7 Why these are separate models and not one softmax

Recorded here because a panel will ask why the system does not use a single
network for all incident types:

- **Weapon detection cannot join.** A weapon is visible in one frame — object
  detection, which YOLO already does. Violence is only visible across frames.
- **A shared softmax would make violence detection worse.** A robbery involving
  assault is genuinely both classes, and softmax forces probability to split
  between them on exactly the clips that matter most.
- **The classes need independent operating points.** §31 showed the threshold ×
  `consecutive` pairing is what determines usability. One softmax is one
  decision surface.
- **Independent failure.** A weak class can be disabled in configuration rather
  than retrained out of shared weights.

Both models are initialised from the deployed violence checkpoint rather than
from Kinetics: on 26 and 11 training scenes they cannot learn motion features
from scratch, and the violence model has already learned them.

---

## 33. §28.1 Wired: Per-Camera Threshold Calibration Is No Longer Just a Measurement

§28.1 measured the win (46 alarms/hr -> 20/hr network-wide, paid only by the
camera that needed it) but stopped there -- no code path existed to apply it.
As of 2026-08-28 it does.

**Shape, deliberately copied from the existing per-camera model on/off
feature (`camera_model_config`).** A new table, `camera_threshold_config`
(`camera_id`, `model_key` in `violence|robbery|vandalism`, `threshold`,
`consecutive_required`), no row means "use `config.json`'s global value" --
the same absent-means-default rule as every other per-camera override in this
project, so a camera nobody has calibrated behaves exactly as it did before
this existed. `main.py` fetches it in the same startup call that already
resolves camera name and per-camera model on/off (`/api/camera_name/{id}`),
and applies it by setting `.threshold`/`.consecutive` directly on the
already-constructed `SceneViolenceDetector` instance -- the class already
accepted these as constructor overrides for the robbery/vandalism case, this
just reaches them post-construction for the camera-specific case.

**Deliberately NOT a dashboard number input.** Both existing AI Models panels
(barangay-scoped and DevTeam) carry standing comments that threshold editing
was removed from the UI on 2026-08-23 specifically because a number typed
into a box invalidates the measured accuracy shown next to it with no
warning. A per-camera override is still a threshold, so it inherits that
rule: the only way to set one is `tools/calibrate_camera_quiet.py`, which
runs the real deployed detector against a camera's own quiet footage and
recommends the p99.5 raw confidence as its threshold -- a measured number,
not a typed one. The dashboard (DevTeam AI Models tab) gained one read-only
line, "N cameras calibrated," so it's visible that calibration happened
without offering a way to fake it from the UI.

**What this has NOT been validated against yet.** No real camera has been
calibrated with it -- §28.1's numbers came from a research script against
recorded footage, not this code path. The `p99.5`-of-quiet-footage
methodology, the `[0.05, 0.95]` sanity clamp, and the ownership/permission
checks are new surface that wants its own smoke test on a real deployment
before being relied on, the same way every other capability in this report
was checked against real footage rather than assumed correct because the
reasoning sounded right.

---

*This report was compiled from the session's logged experimental results (`eval_history.csv`, `train_log.csv`) and the measurement scripts referenced throughout, all retained in the project repository for reproducibility.*
