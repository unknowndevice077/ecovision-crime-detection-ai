# EcoVision Detection Performance — Full Report

As of 2026-08-14. Every number is measured and traceable to a script in the
repository. Nothing is estimated, and where a figure rests on something thin it
says so on the same line.

---

## 1. The one-page answer

| class | status | detection rate | false alarms |
|---|---|---|---|
| **Physical injury (violence)** | deployed | **95.0%** on continuous footage (38/40 events) | **17 / hour** across 3 cameras |
| **Robbery** | deployed | **65.3%** of clips in unseen scenes | **5.6%** of normal clips |
| **Vandalism** | **disabled** | 0% — the shipped rule has never fired | n/a |

Read the rest before quoting any of these. In particular, **recall on the
actual deployment cameras is unmeasured for every class**, because no labelled
incident has ever been recorded on them.

---

## 2. Violence

### 2.1 What it is trained on

`dataset_manifest_3way_daynight.json`:

| split | clips | violent | normal |
|---|---|---|---|
| train | 9,003 | 3,057 | 5,946 |
| val | 1,286 | 643 | 643 |
| test | 1,310 | 655 | 655 |

Sources: RWF-2000, SCVD, CCTV-Fights (CCTV and non-CCTV), UCF-Crime **normals
only**, and 3,880 clips captured from Philippine live street cameras — 2,560
daytime and 1,320 night, across 26 and 22 cameras respectively.

UCF-Crime's violence half is deliberately excluded: ~96% of it is indoor, and
on an outdoor pole it teaches "indoor = violent".

### 2.2 Accuracy on benchmark clips

932 clips held out from both the old and new manifests, so the comparison is
like-for-like:

| | recall | FPR | precision |
|---|---|---|---|
| previous checkpoint | 87.1% | 13.5% | 91.8% |
| **deployed** | **91.4%** | **12.9%** | **92.5%** |

### 2.3 Detection rate on continuous footage

Benchmark clips cannot measure the confirmation logic — of 3,413 violent clips
the longest is 137 s and **the second longest is 11 s**, so most yield only two
or three inference points. 40 held-out violent clips were therefore spliced
into real Davao night street footage with 25 s of ordinary activity either side
and replayed through the shipped state machine:

| `consecutive` | events detected | detection rate | alarms/hr |
|---|---|---|---|
| 1 *(what deployment used to run)* | 38/40 | 95.0% | 49.0 |
| 2 *(what every past measurement assumed)* | 38/40 | 95.0% | 32.0 |
| **3 — deployed** | **38/40** | **95.0%** | **17.0** |
| 4 | 36/40 | 90.0% | 9.0 |

**3 is the last free step; 4 is the first that costs.** Moving from 1 to 3
removed 65% of false alarms and lost none of 40 events.

### 2.4 False alarms per camera — the number that decides usability

At threshold 0.50, `consecutive` 3, 20 minutes per camera:

| camera | alarms in 20 min | per hour | notes |
|---|---|---|---|
| agdao_market | **0** | 0 | venue also appears in training (§2.6) |
| outside Lyn's | 2 | 6 | venue also appears in training |
| agdao_flyover | 15 | 45 | PTZ; no training camera at its location |
| **total** | **17** | **17.0** | |

A single system-wide rate hides a **>45×** spread between cameras. On twelve
other cameras measured earlier the rate was zero.

### 2.5 What is not measured

- **Recall on the deployment cameras.** No labelled violence exists on them.
  The 95.0% above is on spliced benchmark footage and is an **upper bound**: a
  hard cut between two cameras is itself a large visual change.
- **The benchmark clips are not street CCTV.** Inspecting them showed most are
  indoor, several carry YouTube compilation watermarks (`INSTANT KARMA OFFICIAL
  2015`, `FLIPAGRAM`), and one has a visible player UI.
- **±2.5 points of resolution** on the 40-event set. "Zero cost" means "lost
  none of 40", not "provably lossless".

### 2.6 A caveat on the false-alarm figures

Two of the three holdout cameras have a *different* training camera at the same
venue. No camera is in both roles — verified frame by frame, the market pair
even use different DVR overlay formats — but the ranking is uncomfortable:

| camera | venue in training? | alarms before → after adding negatives |
|---|---|---|
| agdao_flyover | no | 93 → 84 (−10%) |
| outside Lyn's | yes, 2 angles | 15 → 9 (−40%) |
| agdao_market | yes, 60 clips | 15 → 3 (−80%) |

Venue-level leakage and "we added market negatives and market cameras improved"
predict the same ordering and **cannot be separated with this data**. The
flyover is therefore the most trustworthy figure here, and the least
flattering.

---

## 3. Robbery

### 3.1 Data

795 clips from **43 UCF-Crime source videos** (Burglary, Robbery, Stealing,
Shoplifting), split by source video: **26 train / 9 val / 8 test scenes**, zero
group-level leakage.

Negatives are the **non-crime spans of the same videos**, so a shop interior
appears on both sides of the label and the model cannot separate the classes on
the DVR overlay instead of on the act. That design is what makes indoor footage
safe to include, which took the class from 15 usable sources to 43.

### 3.2 Full threshold sweep — 139 clips from 8 unseen scenes

| threshold | accuracy | recall | precision | FPR |
|---|---|---|---|---|
| 0.3 | 71.2% | 91.8% | 55.6% | 40.0% |
| 0.5 | 76.3% | 79.6% | 62.9% | 25.6% |
| 0.6 | 80.6% | 71.4% | 72.9% | 14.4% |
| **0.7 — deployed** | **84.2%** | **65.3%** | **86.5%** | **5.6%** |
| 0.8 | 84.2% | 57.1% | 96.6% | 1.1% |
| 0.9 | 80.6% | 44.9% | 100.0% | 0.0% |

0.7 is where precision becomes usable without collapsing recall. At 0.5 a
quarter of normal clips misfire, which is the alert-fatigue failure this
project spent most of its effort on for violence.

### 3.3 Caveats

- **8 test scenes, not 139 independent samples.**
- Largely foreign footage: driveways, car parks and shop interiors, not
  Philippine streets.
- `consecutive_required = 3` is **carried over from violence, not tuned** —
  there is no continuous robbery footage to tune it on.
- Replaces `RULE_ROBBERY_PLACEHOLDER_CONF = 0.895`, a constant with no
  derivation that was rendered on the dashboard as if it were a model output.

---

## 4. Vandalism — disabled, and why

### 4.1 The shipped rule has never fired

First evaluation it has ever had, against the first labelled vandalism footage
this project has ever had:

| set | clips | fired | rate |
|---|---|---|---|
| vandalism | 40 | **0** | 0.0% |
| normal | 24 | 0 | 0.0% |

**Zero `Sign` detections across 4,800 sampled frames.** `score_vandalism()`
requires a wrist near a YOLO-detected sign box; that gate never opens. Person
tracking worked normally in 1,260 of those frames, so the pipeline is fine —
the condition is not satisfiable. A second condition ("no other person nearby")
compounds it on market and food-stall cameras.

This is not a tuning problem. No threshold makes a detector that never fires work.

### 4.2 The trained model also fails

215 clips from 18 sources (11 hand-annotated by reading filmstrips, taking the
class from 2 usable outdoor sources to 18), split 11/4/3 scenes:

| threshold | accuracy | recall | precision | FPR |
|---|---|---|---|---|
| 0.5 | 70.3% | 86.2% | 78.1% | **87.5%** |
| 0.9 | 73.0% | 75.9% | 88.0% | 37.5% |

It fires on **7 of the 8 normal clips**. Its 70.3% accuracy is *below* the
78.4% obtainable by labelling everything vandalism. Validation confirmed it
during training: accuracy fell 42% → 33% while training accuracy climbed to
86% — memorisation of 11 scenes.

The test set is 3 scenes and 8 negatives, so it is simultaneously **too small
to measure anything reliably** and bad at what little it measures. Neither
justifies shipping.

### 4.3 The blocker is scene count

Robbery works at 26 training scenes. Vandalism fails at 11. The route across
that gap is filming, not filtering — every new location is a new scene. See
`docs/vandalism_data_collection.md`.

---

## 5. What the data actually is, end to end

| pool | clips | distinct scenes | role |
|---|---|---|---|
| benchmark violent (RWF/SCVD/CCTV-Fights) | 3,413 | hundreds | violence positives |
| UCF-Crime normals | 900 | 150 videos | violence negatives |
| Davao live capture, day | 2,560 | 26 cameras | violence negatives |
| Davao live capture, night | 1,320 | 22 cameras | violence negatives |
| UCF robbery + same-camera normals | 795 | 43 videos | robbery |
| UCF vandalism + same-camera normals | 215 | 18 videos | vandalism (unused) |

---

## 6. Things measured and rejected

Kept because negative results are the most transferable part of this work.

| intervention | outcome |
|---|---|
| pose ≥2-people gate | lost to a 0.05 threshold change |
| wrist-velocity gate | lost |
| motion-localisation gate | lost |
| tiled inference (4×4, 25% overlap) | lost at every scale, at 8× compute |
| flyover artefact hypotheses (scale, global motion, blur, pan/zoom) | all four *depleted* among alarms |
| "short UCF videos are mostly anomaly" | median coverage 0.32, an upper bound |
| "motion localises the anomaly" | crime is busier in 11 of 21 sources, quieter in 10 |
| **more and more diverse negatives** | **54 → 26 → 17 alarms/hr** |
| **`consecutive_required` 1 → 3** | **49 → 17 alarms/hr, free** |

Every algorithmic intervention lost. What worked was more representative data,
and correctly configuring a temporal parameter that had been mismeasured for
the whole project.

---

## 7. Object scale on the deployment cameras

Measured on 248 person detections across the four holdout cameras
(`measure_object_scale.py`):

| camera | frame height | median person | % of frame |
|---|---|---|---|
| agdao_market | 720 px | 201 px | 27.9% |
| outside Lyn's | 720 px | 178 px | 24.7% |
| agdao_flyover | 720 px | 206 px | 28.6% |
| iloilo_guiez | 360 px | 78 px | 21.7% |

**This corrects a long-standing project assumption.** The plan states that a
wide street camera puts a person at 6–12% of frame height; the actual
deployment cameras put them at **22–29%**, close to the training distribution's
24–60%. The scale problem is far less severe than believed.

Derived object sizes, against a ~24 px floor for reliable small-object detection:

| object | at median person | at 10th-percentile person | verdict |
|---|---|---|---|
| backpack / handbag | 38 px | 25 px | **detectable** |
| carried box or bag | 29 px | 19 px | borderline |
| phone / wallet | 10 px | 6 px | **below detection** |

**Caveat that matters:** these are heights of *detected* people. YOLO misses
small distant people by definition, so the true distribution contains more
small people than this and the medians are biased upward.

---

*Scripts: `sweep_operating_point.py`, `score_spliced_recall.py`,
`build_spliced_recall_set.py`, `eval_test_split.py`, `measure_vandalism_rule.py`,
`measure_object_scale.py`, `check_holdout_overlap.py`,
`build_property_crime_manifest.py`. Narrative and derivations in
`progress_report_violence_detection.md` §§27–32.*
