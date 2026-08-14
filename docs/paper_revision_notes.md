# EcoVision Manuscript — What Needs Updating

Review of `EcoVision - Manuscript.docx` against the system as actually built and
measured, as of 2026-08-13.

Companion file: `paper_revised_sections.md` contains drafted replacement text
for everything marked **REWRITE** or **MISSING** below.

Every number quoted here is measured and traceable to a script in the
repository. Nothing is estimated.

---

## Priority 1 — Would cause problems in the defense

### 1.1 Chapter 2 attributes violence detection to YOLO. It is X3D. **REWRITE**

**Where:** Related Theories ("Computer Vision & Machine Learning Theory (YOLO
Algorithm)"), Related Studies (Gao 2023), Table 2.1.

**The problem:** the paper's entire detection theory rests on YOLO. The system
does not detect violence with YOLO — it uses **X3D-XS**, a 3D convolutional
network. YOLO performs person and pose detection only.

This is not a naming quibble. YOLO's convolution spans one frame, so it can
locate *shapes*. Violence is defined by *change over time*, which a 2D
convolution structurally cannot represent regardless of training. A panelist
who understands the distinction will ask about it.

**It also contradicts your own results.** Four detection gates built on YOLO
pose output were each measured against 60 minutes of held-out Davao CCTV plus
89 violent clips, and every one lost to simply raising the X3D confidence
threshold by 0.05:

| gate | alarms/hr | recall | threshold alone, same recall |
|---|---|---|---|
| none | 63.0 | 85.4% | — |
| ≥2 people | 54.0 | 55.1% | 8.0 |
| wrist velocity > 12 b/s | 8.0 | 37.1% | 0.0 |
| motion localisation | 15.0 | 34.8% | 0.0 |

Chapter 2 as written predicts these should have worked.

**Fix:** add a 3D CNN / X3D theory subsection, keep YOLO but scope it to person
detection. Draft supplied.

### 1.2 Three ML incident classes are claimed. One exists. **REWRITE**

**Where:** Objectives (#2), Scope 1(a)(b)(c), Definition of Terms,
Significance #1.

Physical Injury (violence) has a trained model. **Robbery and Vandalism are
rule-based placeholders**, and they currently transmit invented confidence
values — `RULE_ROBBERY_PLACEHOLDER_CONF = 0.895` and
`RULE_VANDALISM_PLACEHOLDER_CONF = 0.84` — to the dashboard, where they render
identically to a real model output.

There is no derivation for 0.895. If asked, there is no answer.

**Fix, choose one:**
- (a) Paper describes robbery/vandalism honestly as rule-based heuristics, and
  the dashboard displays "RULE-BASED" instead of a percentage; or
- (b) Both are removed from scope and moved to Future Work; or
- (c) A trained property-crime model, reported with its scene count. See below.

Either is defensible. Presenting a constant as a model confidence is not.

#### 1.2a What UCF-Crime can and cannot supply for these two classes

Checked 2026-08-14 by contact sheet rather than by folder name, because the
folder names are accurate and the *contents* are not what the labels imply.

| class | clips | source videos | what it actually is |
|---|---|---|---|
| robbery | 541 | 44 | Burglary 263, Shoplifting 117, Stealing 98, Robbery 63. **Every** Shoplifting video is a retail interior. |
| vandalism | 163 | 14 | Arson 131, Vandalism 32. One video (`Arson007`, an indoor Christmas-tree fire) is 57 clips — 35% of the class. |

Two findings decide the matter:

1. **Arson is not vandalism** under this paper's own definition ("defacement,
   tampering, or damage"). Excluding it leaves **32 clips from 5 source
   videos** — not a class.
2. **The unit of measurement is the source video, not the clip.** Ten clips
   from one burglary are ten views of one driveway. A group-aware split over 22
   usable source videos leaves a **test set of four scenes**.

After filtering to outdoor footage and capping each source at 15 clips so no
single scene dominates: **182 positives from 22 source videos**. Even these are
mostly vehicle theft in private driveways and car parks, not incidents on a
public street under a pole.

**A trap worth recording in the methodology.** The obvious negatives to pair
these with are the project's own Davao street captures — which would let the
model separate the classes on the burnt-in DVR timestamp rather than on the
crime, the same shortcut that got UCF's violence half excluded. The negatives
used instead are the **non-crime spans of the same 22 videos**, so camera
identity carries no information about the label.

**Recommended wording if (c) is chosen:** report it as a model trained on 22
scenes and evaluated on 4, alongside the violence model's hundreds. Do not put
its accuracy in the same table as the violence figures without that context.

#### 1.2b Why this is a separate model and not a fourth class

Worth stating explicitly, because a panelist will ask why the system does not
use one network for all incident types:

- **Weapon detection cannot join at all.** A weapon is visible in a single
  frame — an object-detection problem YOLO already solves. Violence is only
  visible across frames. Different input, different architecture.
- **A shared softmax would make violence detection worse.** A robbery
  involving assault is genuinely both classes; softmax forces probability to
  split between them on exactly the clips that matter most, pushing both below
  threshold.
- **The classes need independent operating points.** This project's own results
  show the threshold and `consecutive_required` pairing is what determines
  usability. One softmax is one decision surface.
- **Independent failure.** A weak third class can be disabled in configuration
  rather than retrained out of shared weights.

The property-crime model is initialised from the violence checkpoint rather
than from Kinetics: with ~15 training scenes it cannot learn motion features
from scratch, and the violence model has already learned them.

### 1.3 Chapter 4 is empty **MISSING**

"CHAPTER 4: FINDINGS AND CONCLUSIONS" appears in the table of contents with no
page number and no body. All results exist and are drafted in the companion
file.

### 1.4 No dataset section anywhere **MISSING**

The manuscript never states what the model was trained on. For a capstone whose
core contribution is a machine learning model, this is the first thing a panel
will ask.

Actual training composition (`dataset_manifest_3way_corpus.json`):

| split | clips | violent | normal |
|---|---|---|---|
| train | 7,630 | 2,805 | 4,825 |
| val | 1,164 | 582 | 582 |
| test | 1,180 | 590 | 590 |

Sources: RWF-2000, SCVD, CCTV-Fights (CCTV and non-CCTV portions), UCF-Crime
(normals only), and 2,393 clips captured from Philippine live street cameras.
Draft supplied, including why UCF-Crime's violence half was deliberately
excluded.

---

## Priority 2 — Substantive but not fatal

### 2.1 "Near-field, 5–7 metres" contradicts the built system **REWRITE**

**Where:** Scope 2(a), Objective #4, Definition of Terms.

The system is being developed and validated on wide-view street cameras where a
person occupies 6–12% of frame height — far beyond 7 metres. The project plan
explicitly *rejected* cropping to a near field because it creates blind spots
in a camera installed for wide coverage.

The current framing also undersells a real result. Recall against person size:

| person height | ~37% (close) | ~13% | ~9% (wide street) |
|---|---|---|---|
| earlier checkpoint | 40/40 | — | **0/40** |
| deployed model | 85.4% | 80.9% | **71.9%** |

Scale augmentation closed a failure that the project plan called
deployment-blocking. That is a stronger contribution than a 7-metre limit.

### 2.2 Research locale is Ormoc; all data is Davao **STATE EXPLICITLY**

Research Locale says Barangay Cogon, Ormoc City. Every camera used is in
Davao — Agdao Market, Bankerohan, Soliman Street, Leon Garcia Street.

This is defensible: public Davao streams stand in for cameras not yet installed
in Cogon, and they are genuine Philippine street CCTV. But it must be stated in
Methodology and Limitations rather than left for a panelist to notice.

### 2.3 Limitations are generic; the measured ones are missing **REWRITE**

Current delimitations list plausible-sounding constraints (weather, 7 m range,
2–5 s latency). Replace with what was actually measured:

- **No labelled violence exists on the target cameras.** Recall is validated
  entirely on foreign benchmark footage. This is the single largest limitation
  and no amount of further data collection on quiet streets addresses it.
- **False-alarm rate is camera-specific, not uniform.** On the same day at the
  same settings: 84/hr on one camera, 3/hr on another, and zero on twelve
  others. A single system-wide accuracy figure conceals this.
- **PTZ cameras pan and zoom.** Measured on the flyover: the camera is in
  motion during 18.6% of samples, with scale changes of −7% to +8%. A threshold
  calibrated at one framing is not valid at another.
- **Benchmark FPR does not predict real false alarms.** Measured directly:
  three checkpoints scored 11.2% / 11.4% / 10.7% clip-level FPR —
  indistinguishable — while their real-camera alarm rates differed by 13.5×.

### 2.4 No evaluation metrics defined **MISSING**

The Abstract promises "detection accuracy, response time and energy
consumption" but no metric is ever defined. Required: recall, false-positive
rate, precision, and **false alarms per hour on continuous footage** — the last
being the metric that actually determines whether an officer keeps using the
system.

---

## Priority 3 — Corrections and housekeeping

| item | location | issue |
|---|---|---|
| "BACHELOR OF SCIECE" | Approval Sheet | typo in the degree name |
| Figure 3.7 Prototype Design | p. 28 | caption present, figure absent |
| Table 2.1 first cell | p. 15 | "Provides evidence that EcoVision." — sentence cut off |
| Software list | Ch. 3 | omits PyTorch, PyTorchVideo, Ultralytics, OpenCV — the actual ML stack. Roboflow is listed but was not used for the violence model |
| "Solat Street Light" | TOC, p. ix | → "Solar" |
| "urvan infrastucture" | Table 2.1 | → "urban infrastructure" |
| "low0light" | Scope 2(c) | → "low-light" |
| "foe continous" | Table 2.1 | → "for continuous" |
| "Mangusing" / "Mangunsong" | Table 2.1 vs text | same author spelled two ways |
| LDR page number | TOC | listed as 27, actually 37 |
| "This The study focuses" | Abstract | duplicated word |
| Real-Time Alert System | Definition of Terms | listed twice, first entry empty |
| Chapter 3 header | p. 18 | "Chapter III METHODOLOGY" vs TOC "CAPSTONE PROJECT METHODOLOGY" |

---

## What is good and should not be changed

- The legal basis section is genuinely strong — RA 10173 in particular is
  well-applied, and the "object recognition not facial recognition" position is
  both accurate to the implementation and defensible.
- Hardware chapter is thorough and matches the build.
- Related Studies on edge computing and solar power management are
  appropriate and correctly cited.
- The Broken Windows / lighting-and-crime rationale is well sourced.
- Functional Decomposition and Use Case diagrams are clear.

---

## Suggested order of work

1. Decide the robbery/vandalism question (1.2) — it changes Objectives, Scope,
   Definition of Terms, Significance, and the dashboard code.
2. Paste in Chapter 4 and the dataset section (1.3, 1.4).
3. Rewrite the Chapter 2 theory subsection (1.1).
4. Rewrite Scope and Limitations (2.1, 2.3).
5. Sweep the Priority 3 list.

Items 2–4 are drafted in `paper_revised_sections.md`.
