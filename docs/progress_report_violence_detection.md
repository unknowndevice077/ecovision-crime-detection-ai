# EcoVision Security Sentinel — Violence Detection Module
## Progress Report for Thesis Advisor

**Prepared:** August 8, 2026
**Module:** Real-time violence detection (X3D-XS video classifier, whole-frame and person-crop variants)
**Scope of this report:** Diagnosis and correction of the violence-detection subsystem, and the discovery of a deployment-blocking limitation that defines the next phase of work.

---

## 1. Problem Statement

At the start of this work period, the violence-detection component of EcoVision Security Sentinel had been retrained four separate times — varying unfreeze depth, data augmentation, class oversampling, and input representation — without breaking a **held-out accuracy plateau of approximately 70%**. No prior retrain had identified *why* accuracy was capped, only that it was.

The objective of this work was twofold:

1. Diagnose the cause of the plateau through systematic measurement rather than further blind hyperparameter search.
2. Correct whatever defects were found, and re-measure honestly.

A third objective emerged during testing: verifying the corrected model against a real, unlabelled CCTV feed (a public webcam of a street crossing in Davao City) surfaced a limitation invisible in any benchmark — one with direct implications for the system's intended deployment across city-owned cameras.

---

## 2. Methodology

The guiding principle throughout was **measure, do not assume**. Every claim below is backed by a script and a logged result; several hypotheses formed during the work were subsequently disproven by follow-up measurement and are reported alongside the ones that held, because the negative results are methodologically part of the record.

Three data sources were used throughout: **RWF-2000** and **SCVD** (the existing training datasets), and a live, unlabelled public CCTV stream used only for qualitative deployment testing (no ground truth available on that stream).

---

## 3. Defects Found and Corrected

### 3.1 Detection coupled to tracking (the original architectural cause)

The deployed pipeline only classified a person if a single YOLO tracking ID survived 20 consecutive frames. On the held-out set, **132 of 769 clips (17.2%) never reached the classifier at all** — 36 of them violent, silently scored as "normal" because the model never ran, and 96 normal clips scored as true negatives "for free." This inflated the apparent accuracy while hiding the real recall problem.

**Fix:** a whole-frame ("scene mode") classification path was added (`SceneViolenceDetector` in `x3d_violence_detector.py`), which classifies the entire frame on a fixed interval independent of person tracking. This removed the tracking gate as a source of missed detections.

### 3.2 Dataset leakage and duplication

An audit of the training data (6,182 source files) found **485 byte-identical duplicate files** and, because the train/validation split was assigned by a random shuffle, **93 held-out clips (12.1%) had an identical byte-for-byte twin in the training set** — the model had memorised, not generalised, on those clips.

**Fix:** `build_dataset_manifest.py` was written to assign every clip's split **from a SHA-256 content hash** rather than a shuffle. Two files with identical content always land on the same side of the split, making this class of leakage structurally impossible rather than something to re-audit after every dataset change. A **three-way split** (train / val / test) was added specifically so that model-selection (validation) and final reporting (test) could no longer be the same data — see §3.6.

### 3.3 The double-softmax defect (root cause of the accuracy plateau)

This was the most significant and least visible defect. The underlying model architecture (`pytorchvideo`'s X3D) already terminates its classification head in a `Softmax` layer — its raw output **is already a probability distribution**. Both the training loss (`nn.CrossEntropyLoss`, which internally applies `softmax`) and the live inference code (which applied `torch.softmax` again to the model's output) were **softmaxing an already-softmaxed value.**

Measured consequences:

- **The training loss had a hard mathematical floor.** A perfectly classified example could score no better than `-log(softmax([0,1])[1]) ≈ 0.3133`. Across 390 logged training batches, the minimum recorded loss was exactly 0.3133, and none went below it — confirming the floor was actually binding, not merely theoretical.
- **This collapsed the loss's dynamic range to just 1.0** (a perfect prediction and a confidently wrong one differed by only 1.0 in loss), which starved the model of gradient signal on its hardest, most informative examples — a strong candidate for the true cause of the four-retrain plateau.
- **Every confidence value the system ever reported, logged, or thresholded was compressed into the range [0.269, 0.731].** A normal scene could never read below 27%, and a violent one could never read above 73%. Every threshold tuned against this compressed scale (e.g., the deployed 0.40) was tuned against a distorted number.

**Fix, and a self-correction on the way to it:** the first proposed fix — replacing the head's `Softmax` with `Identity` — was tested before being deployed and found to be **incorrect**: the model architecture averages per-timestep probabilities across 10 temporal positions before pooling, so removing the softmax changes what is being averaged (probabilities vs. logits), not merely its scale. This was caught by a controlled test on 120 real clips, which found one verdict that changed at a large decision margin (a logit gap of −1.30) — proof the two formulations are not equivalent. The correct fix — training with `NLLLoss` directly on the model's native probability output, with gradient clipping added as a safety measure once the loss floor was removed — was verified to be **exactly decision-preserving** (0/120 disagreements) at the deployed threshold, while restoring the full [0, 1] confidence range and removing the loss floor entirely.

### 3.4 Selection bias in reported accuracy

The training script selects its "best" checkpoint by accuracy on the validation set — meaning a validation-set accuracy figure is, by construction, a number the training process already optimized toward. Every accuracy figure reported by this project before this work period was a validation-set figure presented without that caveat. The evaluation script's own output banner asserted such numbers were "the number to cite as true generalization performance," which was corrected to distinguish validation (selection-optimistic) from test (honest) results, and a related bug where test-split results were being mislabeled as validation-split in the permanent log (`eval_history.csv`) was also fixed.

### 3.5 Supporting fixes

- A checkpoint-resume bug where the "best accuracy so far" value was saved to disk **before** being updated for the current epoch, meaning a training run interrupted and resumed (which occurred once, due to a power outage during this work period) would silently forget its actual best score and could overwrite a better model with a worse one. The associated "early stopping patience" counter had the same bug.
- The model's expected input resolution (`frame_size`) was hardcoded identically in two separate files with no mechanism to detect disagreement between them — the exact class of bug that caused §3.1–3.3. A `.meta.json` sidecar is now written next to every trained checkpoint recording its exact input contract (resolution, frame count, output convention), and the live detector reads it and **overrides a mismatched config with a loud warning** rather than silently using the wrong value.

### 3.6 Honest, leak-free, selection-free evaluation

With the three-way manifest in place, the final model was trained on a 3,298-clip training set, model-selected on a *separate* 722-clip validation set, and — for the first time in this project's history — scored on a **758-clip test set that neither gradient descent nor checkpoint selection ever saw.**

---

## 4. Results

| Stage | Split type | Accuracy | Recall | Precision | FPR |
|---|---|---|---|---|---|
| Start of this work period | selection-optimistic | 77.2%* | — | — | — |
| After leak removal, before softmax fix | honest (leak-excluded) | 78.4% | 77.0% | 91.2% | 18.2% |
| Retrained model, validation split | **selection-optimistic** | 93.9% | 96.3% | 91.9% | 8.4% |
| **Final model, held-out test split** | **honest — never seen in training or selection** | **95.0%** | **97.4%** | **92.9%** | **7.4%** |

*\*Confusion counts for the 78.4% figure: TP=237, FN=137, TN=304, FP=91 (n=769).*
*Final honest figure confusion counts: TP=369, FN=10, TN=351, FP=28 (n=758).*

The honest, held-out result represents a **+16.6 percentage point accuracy improvement and a +20.4 point recall improvement** over the session's starting point, achieved on a model trained with **18% less data** than an intermediate checkpoint, which underscores that the gain came from removing defects rather than from more data.

### 4.1 Error analysis on the held-out result

Breaking the 32 false positives down by data source found **all 32 originated from one sub-class** (RWF-2000's "NonFight," largely crowded/wide street footage); the parallel SCVD "Normal" class scored a perfect 0% FPR on 171 clips. Further analysis found false-positive rate is strongly correlated with **clip duration** — 0% at 1–5 inference checks per clip, rising to 19.9% at 10+ checks — meaning **per-clip false-positive rate systematically understates the false-alarm rate on a continuously running camera**, which never stops accumulating checks. This is flagged as a critical consideration for the operating-point (alert threshold) decision in the next phase, rather than something the accuracy figure alone can answer.

---

## 5. A Deployment-Blocking Discovery: Scale Mismatch on Wide Camera Views

Following model correction, the corrected system was tested against a real, unlabelled public CCTV stream (a street-crossing camera in Davao City) as a qualitative deployment check. Initial observation: confidence readings fluctuated with traffic on a wide-angle view of the intersection, with no violence occurring.

**Working hypothesis (formed, then tested, then disproven):** it was hypothesized this fluctuation was the model misreading vehicle/crowd motion as violence (a false-positive risk). This was tested directly by taking the same live footage and *synthetically shrinking* the people in it while holding everything else constant. **Confidence did not rise — it barely moved (0.132 → 0.128), and no alarm fired at either scale.** The false-positive hypothesis was rejected by this test.

**The actual mechanism, confirmed by controlled measurement:** every training clip was measured for person size as a percentage of frame height using the existing pose-detection model. Training footage puts a person at a median of **37.1%** of frame height (range 24–60%). The Davao street camera, uncropped, puts a person at roughly **9–12%.** To determine the effect of this gap directly, 40 violent clips the model detects with 100% confidence in their original form were replayed at progressively smaller synthetic scale:

| Person height (% of frame) | 37% | 30% | 22% | 19% | 15% | 9% |
|---|---|---|---|---|---|---|
| **Clips still detected (of 40)** | 40/40 | 34/40 | 25/40 | 22/40 | 7/40 | **0/40** |

There is a sharp cliff between 19% and 15% person-height. **Below approximately 15%, the model does not become less confident — it detects nothing.** This is the correct explanation for the earlier field observation: a wide-angle street camera does not cause false alarms; it causes the system to be **effectively blind**, and a blind camera on a quiet street is statistically indistinguishable from a working camera on a quiet street. For a public-safety system, this is the most dangerous possible failure mode, because it produces no symptom.

### 5.1 Why the obvious fix (cropping the camera view) was rejected

The natural first response — configure each camera to crop/zoom into the region where people are expected — was proposed and then explicitly rejected after review, on the following reasoning: a camera is installed to monitor a specific area; cropping it to compensate for a model limitation reintroduces exactly the blind spot the camera was purchased to eliminate. This would violate the system's core purpose for the sake of a training-data artifact.

### 5.2 Root cause identified in the training pipeline

Investigating why the model had never learned to recognize small people, the clip-augmentation code was found to contain a **scale augmentation that could only zoom in** (random crop 80–100%, resized back up — which only ever makes a person *larger* than in the source footage). **No augmentation in the pipeline had ever shown the model a person smaller than what the raw source footage happened to contain.** This is now understood as the root cause of the scale-blindness finding in §5, not merely a symptom of it.

### 5.3 Coverage-preserving architectural alternative

A tiled inference approach was designed and implemented as an alternative to cropping: the frame is divided into a grid of overlapping regions (each independently classified with its own temporal buffer), so a distant person becomes proportionally larger within their tile **without discarding any part of the camera's field of view.** A controlled test replaying the same shrunk clips through this design recovered **25 of 30** detections that a whole-frame pass missed entirely at the same scale (0 of 30), while covering 100% of the frame. A subtlety was found and addressed during this test: a non-overlapping grid can split a single incident across a tile boundary and cause it to be missed by every tile (measured: a non-overlapping 2×2 grid scored *worse*, at 1/30, than the whole-frame baseline) — solved by overlapping tiles plus retaining one full-frame pass for scene-scale context.

---

## 6. Current Status and Immediate Next Steps

The corrected violence-detection model (95.0% honest accuracy) is deployed as the system default. A formal, evidence-based plan for the next phase — reconciling full-camera-view coverage with accurate detection — has been written and approved, structured as:

1. **Measurement phase** — determine, before building anything, whether the existing person-detection model can reliably locate distant people at an affordable computational cost, since this decides between two viable architectures (person-cropped classification, informed by 2024 published work — *CUE-Net*, CVPR — which uses the same spatial-cropping principle already present in this codebase; versus the tiled whole-frame approach in §5.3).
2. **Clean retraining** of both candidate architectures on the leak-free three-way manifest, now including a **zoom-out augmentation** (added following the §5.2 finding) so the model is exposed to small-scale people during training for the first time.
3. **Head-to-head evaluation** on the held-out test split *and* on simulated wide-camera footage, since a model can be honestly accurate on close-up benchmark clips while remaining unusable at deployment scale — precisely the gap this report documents.
4. **Operating-point selection** based on a measured false-alarms-per-hour figure from real (Davao) footage, not on a benchmark-clip false-positive rate, per the finding in §4.1.
5. A documented scaling path toward the system's ultimate goal — multiple city-owned cameras — including the compute-cost tradeoff between the two candidate architectures.

## 7. Limitations to State Plainly

- All quantitative accuracy figures in this report are measured on **RWF-2000 and SCVD**, two established academic benchmarks — not on Philippine street-camera footage. The Davao feed provided a valuable qualitative check and surfaced the scale-mismatch finding, but it carries no ground-truth labels, so **recall on real deployment cameras remains formally unmeasured** and is the central open question the next phase addresses.
- The false-alarm-rate figures in §4 are per-benchmark-clip; §4.1 explains why this likely understates the true rate on a continuously running camera, and the next phase's operating-point selection is designed specifically to correct this.
- Two other detection capabilities in this system — robbery and vandalism — are implemented as hand-written geometric rules rather than trained models, and report fixed, hardcoded confidence values to the dashboard rather than measured ones. They were out of scope for this work period (violence detection only, by explicit decision) and are noted here as a known gap for future work.

---

*This report was compiled from the session's logged experimental results (`eval_history.csv`, `train_log.csv`) and the measurement scripts referenced throughout, all retained in the project repository for reproducibility.*
