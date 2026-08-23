# For the panel — read these, in this order

Everything else in `docs/` is working material: engineering records, literature
notes, measurement scripts and their output. It is all real and all auditable,
but it is not what you should sit down and read.

**These four are.**

---

## 1. `model_behavior_defense.html` — start here

**Open in a browser. 5.8 MB, self-contained, no internet needed.**

Thirteen sections covering what each detector does, confusion-matrix charts for
all of them, three animations, a glossary, and a section that anticipates the
questions a panel actually asks. This is the primary document.

> Print-styled version: **`EcoVision_Defense_Reference.pdf`** — the same content,
> laid out for paper. Use it if a printed copy is wanted; otherwise prefer the
> HTML, which has the animations.

## 2. `how_ecovision_sees.html` — the "how does it actually work" document

**Open in a browser. 5.9 MB.**

What a convolution is, what X3D does with thirteen frames that a single image
cannot answer, why the system uses two model families instead of one, and worked
examples with real frames. This is the one to open when someone asks *"but how
does it know?"*

## 3. `detection_metrics.html` — every measured number

**Open in a browser. 1.2 MB.**

The metrics ledger. Each figure with the split it was measured on and the caveat
that limits it. Use it to answer "where does that number come from".

## 4. `ecovision_full_report.md` — the written report

The complete technical report: abstract, related work, architecture, data,
training, results, **negative results**, threats to validity, future work.

Markdown rather than a rendered document, so it reads best in an editor or on
GitHub. Its §13 maps every other file in this folder.

---

## If you only have five minutes before walking in

Read these three sections of `ecovision_full_report.md`:

| section | why |
|---|---|
| **§1.3** The measurement problem | Recall on the deployment cameras is *unmeasurable*, not merely unmeasured. Say this before anyone asks. |
| **§8** Negative results | Four routes to vandalism detection, all measured, all failed, each for a different documented reason. This is the strongest material in the project. |
| **§9.2** The scale blind spot | The system detects 40/40 at 37% person height and **0/40** at 9%. Better said by you than found by them. |

---

## Do not hand over

| file | why |
|---|---|
| `progress_report_violence_detection.pdf` / `.html` | Exported 12 Aug; the `.md` gained three sections on 14 Aug. These show an earlier, worse picture of the work. |
| `archive/` | Superseded manuscript backups and a pre-animation fragment of the defense doc. |
| everything else in `docs/` | Working records. Correct, but written for whoever maintains this, not for a reader. |

---

## The three things worth having ready to say

1. **"We withdrew three published numbers after measuring how they were
   produced."** A 99.4%-leaked validation split, a detector class that had never
   fired, and a rule scored against footage containing none of what it detects.
   Each was found by instrumenting a claim rather than accepting it.

2. **"Vandalism is disabled, and here is exactly why."** Not *"it didn't work"* —
   its gate fired 0 times in 3,600 frames; the inverted formulation caught 3 of 4
   events but alarmed on every camera; the trained model reached 82.4% accuracy
   against a 62.7% baseline yet still produced 125 false alarms an hour, cut to
   21.75 by adding real street negatives. And **no public video dataset of the
   graffiti act exists** — that is a field-level limitation, not a gap in our
   collection.

3. **"The models we ship are the models we measured."** Config layering meant the
   detector silently loaded different weights than the config named, and the
   robbery detector was not running at all. Found by running the system and
   reading which files it loaded, not by trusting a green check.
