# EcoVision Manuscript — Drafted Replacement Sections

Ready-to-paste text for the gaps and rewrites identified in
`paper_revision_notes.md`. Section headings match the manuscript so each block
can be dropped in place.

Every figure quoted is measured. Where a number is uncertain or rests on a
single run, the text says so — a capstone is stronger for stating the limits of
its evidence than for rounding them away, and a panel will find the weak points
anyway.

---

## ABSTRACT — replacement

This study covers the design, development, and evaluation of EcoVision, an
energy-efficient street lighting system with sensor and camera-based localized
crime detection, intended for barangay-level deployment. The system integrates
a solar-powered smart pole with adaptive LED lighting, a passive infrared
sensor, a panic button, a siren, and a camera running a locally-executed
machine learning model for violence detection.

Violence detection uses X3D-XS, a three-dimensional convolutional neural
network operating on 13-frame clips, fine-tuned from Kinetics-400 pretrained
weights on 7,630 clips drawn from four public benchmark datasets and 2,103
clips captured from Philippine live street cameras. A YOLO11 network performs
person and pose detection as a separate stage.

On 932 clips withheld from training, the deployed model achieves 91.4% recall
at a 12.9% false-positive rate. On three continuously-recorded Philippine
street cameras never used in training, it produces 32 alarms per hour, reduced
from 41 by the addition of locally-captured negative footage.

The study additionally reports two findings that constrain how such systems
should be evaluated. First, clip-level false-positive rate on benchmark data
was found not to predict false alarms on a continuously-running camera: three
model checkpoints scored statistically indistinguishable benchmark rates while
their real-camera alarm rates differed by a factor of 13.5. Second, four
pose-derived filtering strategies, together with tiled multi-region inference,
were each measured and found to perform worse than a single threshold
adjustment, indicating that for this class of model the operating point and the
training data — not additional inference-time machinery — govern practical
accuracy.

---

## CHAPTER 2 — Related Theories, replacement subsection

Replace *"Computer Vision & Machine Learning Theory (YOLO Algorithm)"* with the
two subsections below.

### Computer Vision Theory: Spatial Object Detection (YOLO)

The YOLO (You Only Look Once) family of detectors, introduced by Redmon et al.
(2016), provides the theoretical basis for the system's **person and pose
detection** stage. YOLO performs object localization in a single forward pass
over one image, applying two-dimensional convolutional filters that respond to
spatial patterns — edges, contours, and the arrangement of body parts. This
makes it well suited to answering *where* people and objects are in a frame,
and it is used in EcoVision for exactly that purpose: locating persons,
estimating pose keypoints, and distinguishing pedestrians from vehicles.

A two-dimensional convolution operates within a single frame and therefore has
no access to how a scene changes over time. This is a structural property, not
a limitation of training: a network given one frame cannot in principle
distinguish a raised arm from a thrown punch, because both are consistent with
the same pixels. Detecting an action requires a representation that spans time.

### Spatiotemporal Learning Theory: 3D Convolutional Networks (X3D)

Violence detection in EcoVision is grounded in spatiotemporal convolution,
specifically the X3D architecture of Feichtenhofer (2020), which extends
two-dimensional convolution by adding a temporal axis. Where a 2D filter spans
height and width, a 3D filter also spans a number of consecutive frames, so the
network learns patterns of *motion* rather than patterns of appearance.

X3D-XS, the variant used here, does not apply a single three-dimensional
kernel. It factorizes the operation into two consecutive convolutions, which
can be read directly from the network's first block:

    conv_t    kernel (1 × 3 × 3)    spatial only — one frame at a time
    conv_xy   kernel (5 × 1 × 1)    temporal only — one position, five frames

The spatial component is the same operation YOLO performs. The temporal
component examines a single spatial position across five consecutive frames.
The architecture is therefore not a departure from two-dimensional computer
vision but an extension of it, and this relationship is what allows a single
system to answer both *where are the people* and *what is happening*.

X3D was selected over larger video architectures because the expansion approach
it introduces allows a model to be scaled down along several axes — frame
count, resolution, width, and depth — while preserving accuracy. This makes it
appropriate for the constrained hardware of a solar-powered pole, where the
system must run continuously on harvested energy.

### Related Studies — correction to the existing entry

The existing citation of Gao (2023) on YOLO-based violence detection should be
retained but reframed. That work uses frame-by-frame analysis, and the present
study provides direct evidence for the limits of that approach: four
pose-derived features computed from YOLO output — person count, inter-person
proximity, wrist velocity normalized by torso length, and localization of
motion energy within person bounding boxes — were each evaluated as filters on
the violence classifier's output, and none improved on the classifier's own
confidence threshold. This is reported in full in Chapter 4 as a negative
result.

---

## CHAPTER 3 — new section: Dataset Development and Model Training

Insert after *Software Development Specifications*.

### A. Dataset Composition

The violence classifier was trained on clips assembled from four public
datasets and from footage captured by the researchers from live Philippine
street cameras.

| Source | Type | Role |
|---|---|---|
| RWF-2000 | Benchmark surveillance violence | Positives and negatives |
| SCVD | Smart-city violence, includes weapons | Positives and negatives |
| CCTV-Fights (CCTV portion) | Real fixed-camera CCTV fights | Positives and negatives |
| CCTV-Fights (non-CCTV portion) | Handheld and vehicle footage | Positives and negatives |
| UCF-Crime | Real continuous surveillance | **Normals only** |
| Philippine live captures | Davao street CCTV, day and night | Negatives only |

Final composition after filtering:

| Split | Clips | Violent | Normal |
|---|---|---|---|
| Train | 7,630 | 2,805 | 4,825 |
| Validation | 1,164 | 582 | 582 |
| Test | 1,180 | 590 | 590 |

**UCF-Crime's violent classes were deliberately excluded.** Manual review of
the extracted clips found that approximately 96% of its violence categories are
filmed indoors — shops, garages, metro stations, corridors — while its normal
category is roughly 57% outdoor street. Training on both would offer the model
a shortcut it would be expected to take: *indoor implies violent*. For a system
whose deployment target is an outdoor streetlight, that association would make
outdoor violence harder to separate, not easier. Of one class inspected, only 9
clips were outdoor and in-scope, which did not justify the bias attached to
them. The normal clips were retained because 900 clips of real continuous
outdoor CCTV containing no incident are directly useful.

### B. Data Integrity Procedures

Three verification procedures were applied because errors of each kind were
found in practice during development:

1. **Decode validation.** Clips were confirmed to contain readable frames after
   extraction. The `ffmpeg` utility returns a success code when a requested
   segment begins past the end of a source video, producing a valid file
   containing zero frames; three such files were found and quarantined.
2. **Motion filtering.** 378 clips labelled violent were found to contain
   almost no inter-frame motion — empty car parks and static scenes. These were
   excluded, since a motionless clip labelled violent teaches the model that
   stillness is an indicator of violence.
3. **Visual review.** Every extracted dataset was rendered as a contact sheet
   and inspected before training. Label directories were not trusted. This
   procedure is what identified the UCF-Crime indoor bias described above.

### C. Split Methodology and Leakage Prevention

Splits are assigned by content hash rather than random shuffle, so that
byte-identical duplicate clips cannot land on opposite sides of the train/test
boundary.

Content hashing alone is insufficient once clips are cut from longer source
videos: two clips from the same source video have different bytes but show the
same camera, scene, and people. Clips are therefore **grouped by source video
and by physical camera**, and an entire group is assigned to a single split.
Without this, a test set can contain a camera the model trained on and appear
independent while measuring memorisation.

Three cameras — Agdao Market, Agdao Bus Stop and Flyover, and the camera
outside Lyn's Restaurant — were designated validation-only and excluded from
every training manifest by construction. All false-alarm figures in Chapter 4
are measured on these cameras.

### D. Model Configuration

| Parameter | Value |
|---|---|
| Architecture | X3D-XS |
| Initialization | Kinetics-400 pretrained |
| Input | 13 frames at 160 × 160, whole frame |
| Trainable parameters | 1,920,794 of 2,978,772 (64.5%) |
| Loss | Negative log-likelihood on log-softmax outputs |
| Augmentation | Zoom-out applied to 45% of clips, scale 0.18–0.9 |
| Training hardware | NVIDIA GTX 1660 SUPER (6 GB) |

The zoom-out augmentation exists because a wide street camera places a person
at a much smaller fraction of frame height than benchmark footage does. Its
effect is measured in Chapter 4.

### E. Evaluation Metrics

Four metrics are reported. The first three are standard; the fourth is the one
that determines whether the system is usable in the field.

- **Recall** — proportion of violent clips detected. A missed assault is the
  costlier error for a public safety system.
- **False-positive rate** — proportion of normal clips wrongly flagged.
- **Precision** — proportion of alarms that were correct.
- **False alarms per hour** — alarms raised per hour of continuous footage
  containing no incident. Clip-level FPR structurally understates a camera that
  runs continuously, and Chapter 4 shows the two do not track each other.

---

## CHAPTER 4 — FINDINGS AND CONCLUSIONS

### 4.1 Detection Accuracy on Held-Out Data

Evaluated on 932 clips present in the test split of both the previous and
current dataset manifests and in the training split of neither — the only set
on which two model versions can be compared without one having seen it.

| Metric | Previous model | Deployed model |
|---|---|---|
| Recall | 87.1% | **91.4%** |
| False-positive rate | 13.5% | **12.9%** |
| Precision | 91.8% | **92.5%** |
| Accuracy | 86.9% | **89.8%** |

The improvement holds across the operating range rather than at a single
threshold. At matched recall of 84.6%, the previous model requires an 11.4%
false-positive rate while the deployed model achieves the same recall at 8.2%.

The only difference between the two models is training negatives: 3,925 clips
drawn from 7 daytime cameras, against 4,825 drawn from 26. Architecture,
initialization, and positive examples are identical.

*Limitation:* this is one training run compared against one training run,
without repetition across random seeds. The direction is supported by two
independent measurements (benchmark clips and continuous footage), but the
4.3-point recall improvement should be read as a direction rather than a
precise quantity.

### 4.2 False Alarms on Real Philippine Street Cameras

Measured on three cameras excluded from all training data, 20 minutes each, at
identical settings.

| Camera | Previous model | Deployed model |
|---|---|---|
| Agdao Bus Stop / Flyover | 93/hr | 84/hr |
| Agdao Market | 15/hr | **3/hr** |
| Outside Lyn's Restaurant | 15/hr | 9/hr |
| **Total** | **41/hr** | **32/hr** |

The reduction is uneven and the unevenness is the finding. The market camera
improved five-fold; the flyover barely moved. This is examined in 4.5.

### 4.3 Effect of Scale Augmentation on Wide-Camera Detection

The project's initial architecture study identified a failure mode described as
deployment-blocking: replaying clips the model detected with full confidence
while progressively shrinking the people in them produced 40 of 40 detections
at 37% person height and **0 of 40 at 9%** — the range typical of a wide street
camera. The model was not less confident at small scale; it was blind.

Re-measured on the deployed model, which was trained with zoom-out
augmentation:

| Person height | ~37% | ~13% | ~9% |
|---|---|---|---|
| Earlier checkpoint | 40/40 | — | **0/40** |
| Deployed model | 85.4% | 80.9% | **71.9%** |

Degradation is now gradual rather than a cliff. The failure was closed in the
training data rather than the architecture, which is the more economical
solution: the alternative under consideration, tiled multi-region inference,
costs approximately eight times the computation per frame.

### 4.4 Negative Results

Both results below are reported because they constrain the design space, and
because each was measured on the same footage and scored on both accuracy axes
simultaneously.

#### 4.4.1 Pose-derived filtering does not improve on the confidence threshold

Four filters were implemented from YOLO pose output and evaluated on 60 minutes
of held-out camera footage together with 89 violent clips. Each is compared
against the alarm rate a plain threshold adjustment achieves at the same
recall:

| Filter | Alarms/hr | Recall | Threshold alone, same recall |
|---|---|---|---|
| None | 63.0 | 85.4% | — |
| At least 1 person present | 57.0 | 68.5% | 20.0 |
| At least 2 people in proximity | 54.0 | 55.1% | 8.0 |
| Wrist velocity above threshold | 8.0 | 37.1% | 0.0 |
| Motion localised within person boxes | 15.0 | 34.8% | 0.0 |

No filter performs better than the threshold. The wrist-velocity filter reduces
false alarms by 87%, which appears successful until recall is read alongside
it: 85.4% falls to 37.1%. Raising the threshold to 0.90 achieves 1 alarm per
hour at 50.6% recall — superior on both axes, with no pose model and no
additional per-frame computation.

Diagnosis of one filter is worth recording. Motion localisation was found to
correlate with the *number of people in frame* rather than with the source of
the motion:

| People in frame | 0 | 1 | 2–3 | 4–7 | 8+ |
|---|---|---|---|---|---|
| Median localisation | 0.000 | 0.029 | 0.104 | 0.222 | 0.644 |

The measure therefore reports low values on wide cameras where people are
small — precisely the cameras it was intended to assist.

#### 4.4.2 Tiled inference performs worse at every operating point

Tiled inference divides the frame into overlapping regions, each classified
independently, and raises an alarm if any region does. It was expected to help
wide cameras by restoring apparent person size.

| Configuration | Alarms/hr | Recall |
|---|---|---|
| Whole frame, threshold 0.55 | **42** | 85.4% |
| Tiled, threshold 0.75 | 211 | 85.4% |

At equal recall, tiled inference produces five times the false alarms for
roughly eight times the computation. With ten regions evaluated per check, the
system has ten independent opportunities to be wrong, and the false-alarm cost
outweighs the recall benefit at every setting tested. Requiring agreement
between regions reduces alarms but never brings the trade below the whole-frame
result.

The earlier evidence favouring tiling had measured recall only. This is a
general caution: a change that increases detections must be evaluated on false
alarms, and a change that reduces false alarms must be evaluated on recall.
Measuring one alone guarantees a favourable answer.

### 4.5 Benchmark Accuracy Does Not Predict Field Performance

Three model checkpoints were compared on both a benchmark test split and real
continuous footage:

| Checkpoint | Benchmark FPR | Real camera alarms/hr |
|---|---|---|
| Baseline | 11.2% | 54.0 |
| + real-CCTV negatives | 11.4% | 26.0 |
| + motion filtering | 10.7% | **4.0** |

**Benchmark false-positive rates are indistinguishable while real alarm rates
differ by a factor of 13.5.** Had this work relied on the test split alone —
the conventional approach — the largest false-alarm improvement of the project
would have registered as no change at all.

The cause is domain rather than statistics. Benchmark negatives are curated,
framed, and largely indoor or close-range; Philippine street CCTV at night is
none of these. A model can be excellent at rejecting the first and poor at the
second, and a clip-level rate computed on curated data cannot distinguish the
two cases.

**This is offered as a methodological finding: benchmark false-positive rate
should not be used as a proxy for false alarms per hour on a deployed camera.**

### 4.6 False Alarms Are Camera-Specific

Running the deployed detector across 253 minutes of newly captured daytime
footage from 14 street cameras produced almost no false alarms: 12 of the 14
cameras produced **zero** in 20 minutes each, and the remaining two produced
one and two respectively.

On the same day, at the same settings, the flyover camera produced 84 per hour.

The false-alarm problem is therefore concentrated in specific camera
installations rather than distributed across the system or attributable to time
of day. Four candidate explanations for the flyover were tested and rejected:

| Hypothesis | Result |
|---|---|
| Small person scale | Rejected — 71.9% recall at that scale |
| High global scene motion | Rejected — 0.6× enrichment among alarms |
| Motion blur | Rejected — 0.4× enrichment among alarms |
| Camera pan and zoom | Rejected — 0.6× enrichment among alarms |

All four are *depleted* among alarms rather than enriched, indicating that
alarms occur on clear, sharp, stationary-camera frames. The remaining
explanation is that the model finds ordinary night bus-stop and roadside
traffic activity genuinely similar to violence — a training data coverage
problem rather than an image quality one.

This camera is retained as a documented limitation. It is a validation-only
camera, so it cannot be trained on without destroying the only honest measure
of generalization available.

### 4.7 Operating Point Selection

Because both error types carry real cost, the operating point was selected from
a measured curve rather than assumed.

| Threshold | Alarms/hr | Recall |
|---|---|---|
| 0.50 | 63.0 | 85.4% |
| 0.55 | **42.0** | **85.4%** |
| 0.60 | 35.0 | 77.5% |
| 0.70 | 20.0 | 69.7% |

Raising the threshold from 0.50 to 0.55 removes a third of all false alarms at
no cost in recall — the same 76 of 89 clips are detected at both settings.

Per-camera calibration was also evaluated, with each camera's threshold set
from a percentile of its own quiet footage and validated on a later segment it
had not seen. This reduced the three-camera total from 46 to 20 alarms per
hour, and its advantage grows with network size: quiet cameras remain at the
minimum threshold and retain full recall while only busy cameras pay. For a
pole-mounted deployment this is a practical commissioning step — record a few
quiet minutes at the camera's final angle, compute one value, record it.

### 4.8 Conclusions

1. A 3D convolutional network detects violence in Philippine street CCTV at
   91.4% recall and 12.9% false-positive rate on held-out data, running on a
   single consumer GPU.
2. Detection accuracy is governed principally by the representativeness of the
   training negatives. Every inference-time intervention tested — four pose
   filters, tiled inference, persistence requirements — was outperformed by
   adjusting the confidence threshold, while both interventions that improved
   the system materially were additions of locally-captured negative footage.
3. Clip-level benchmark metrics do not predict false alarms on a
   continuously-running camera and should not be reported alone for systems of
   this type.
4. False-alarm behaviour is a property of the individual camera installation.
   Per-camera calibration is more effective than a single system-wide
   threshold, and is practical as an installation step.
5. Scale augmentation during training removes the wide-camera blindness that
   would otherwise require substantially more expensive tiled inference.

### 4.9 Recommendations for Future Work

1. **Obtain labelled violent incidents from the deployment cameras.** This is
   the most significant remaining gap. Recall is currently validated entirely
   on foreign benchmark footage, so no recall figure specific to Philippine
   street conditions can be stated. Even a few dozen verified incidents —
   whether barangay-reported, retrieved from archived footage, or staged under
   controlled conditions — would change what can be claimed.
2. **Train robbery and vandalism classifiers**, or formally remove them from
   scope. They are presently rule-based heuristics.
3. **Augment training with synthetic camera motion** so the model becomes
   invariant to pan and zoom, rather than filtering such frames at inference.
4. **Evaluate person-crop inference for multi-camera scaling.** Running
   inference only where people are detected would allow an idle scene to cost
   nothing, which is the principal barrier to serving multiple cameras from one
   processing unit.

---

## SCOPE AND LIMITATIONS — replacement

### Scope

1. **Violence Detection.** The system detects physical violence between persons
   using a three-dimensional convolutional neural network trained on
   surveillance footage, executed locally on the pole.
2. **Person and Object Detection.** A YOLO11 network locates persons and
   distinguishes pedestrians from vehicles, supporting both lighting control
   and incident context.
3. **Wide-Area Coverage.** The system analyses the full camera field of view.
   Detection has been validated with subjects occupying as little as 9% of
   frame height, corresponding to a wide street view.
4. **Tiered Activation.** Motion sensing and the panic button gate the
   higher-power processing stage, conserving stored energy at night.
5. **Hybrid Power and Active Response.** Solar primary with grid backup;
   detected incidents trigger local siren and strobe and transmit a priority
   alert to a central dashboard.

### Limitations

1. **Recall on the deployment cameras is unmeasured.** No labelled violent
   incident exists in the researchers' own camera footage. All recall figures
   derive from public benchmark datasets recorded in other countries. False
   alarms are measured directly on Philippine footage; detection performance is
   not.
2. **False-alarm rate varies by camera installation.** Measured on the same day
   at identical settings, individual cameras produced between 0 and 84 alarms
   per hour. A single system-wide figure is not meaningful without per-camera
   reporting.
3. **Pan-tilt-zoom cameras require per-position calibration.** A camera in the
   test set was in motion during 18.6% of samples, with scale changes of −7% to
   +8%. A threshold calibrated at one framing is not valid at another.
4. **Robbery and vandalism detection are rule-based**, not machine-learned, and
   are consequently limited to explicit heuristic conditions.
5. **Detection footage is drawn from Davao City street cameras** while the
   deployment locale is Barangay Cogon, Ormoc City. Both are Philippine urban
   street environments, but no footage from the deployment site itself was
   available during development.
6. **Privacy by design.** No facial recognition or biometric identification is
   performed. The system classifies actions and locates objects; it does not
   identify individuals.
7. **Environmental sensitivity.** Heavy rain, fog, and severe backlighting
   reduce accuracy. Night performance is measured; extreme weather is not.
