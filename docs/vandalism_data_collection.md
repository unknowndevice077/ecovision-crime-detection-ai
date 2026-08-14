# Collecting Vandalism Footage That Will Actually Train

## Why this exists

Vandalism is the one incident class with no viable data path from public
datasets, and this is not for lack of trying:

| attempt | result |
|---|---|
| UCF-Crime, outdoor + non-arson | **2 source videos** |
| Hand-annotating 11 more UCF videos off filmstrips | 18 sources — model still fails |
| Trained model on those 18 | **87.5% FPR** — fires on 7 of 8 normal clips, accuracy *below* the base rate |
| DCSASS (Kaggle, 16,853 clips) | re-segments the **same** UCF source videos: no new scenes |
| Nexdata 874-video vandalism set | commercial |

Robbery, at **26 training scenes**, produces a working model (84.2% accuracy on
8 unseen scenes). Vandalism, at 11, does not. The gap between 11 and 26 scenes
is the whole problem, and **scenes are what filming produces cheaply** — every
new location is a new scene, which is the exact resource that is scarce.

Roughly 20 locations is the target. That is an afternoon.

---

## What counts as one "scene"

A scene is **one camera position at one place**. Ten clips of the same wall from
the same angle is one scene, and the group-aware split treats it as one sample
no matter how many clips are cut from it. This is the single most important
thing to get right:

> **Move the camera to a new place far more often than you record more at one place.**

Two minutes at twenty locations beats forty minutes at two.

---

## The shot list

At **each** location, record all four:

| # | shot | seconds | why |
|---|---|---|---|
| 1 | **The act** — kick a gate, hit a wall/post, tag it with a capped marker, tip a bin, shake a fence, pull at a sign | 20–30 | the positive |
| 2 | **The same view, nothing happening** | 30–45 | the same-camera negative — this is what stops the model learning your locations instead of the act |
| 3 | **Someone walking past normally**, same view | 20 | the hard negative: person present, no damage |
| 4 | **Someone doing something energetic but harmless** — carrying a load, waving, jogging, adjusting a bike | 20 | the hardest negative, and the one that decides the false-alarm rate |

Shots 2–4 matter more than shot 1. The failed model's problem was not missing
vandalism (recall was 86%) — it was **firing on everything else**.

---

## Camera setup

- **Mount it high and leave it still.** Chest height on a tripod, a wall ledge, a
  window sill, a stair rail. Roughly 3–4 m up, looking down at 20–40°. That is a
  streetlight's view; handheld phone footage at eye level is not, and the model
  will learn the difference rather than the act.
- **Do not zoom, pan, or follow the action.** A fixed frame is the point.
- **Let the person be small.** They should occupy about **10–25% of frame
  height** — a person filling the frame is not what a pole sees. Stand back
  further than feels natural.
- 1080p at 30 fps is plenty. Landscape, never portrait.
- **Include night.** At least a third of locations after dark, under street
  lighting. Night is where the deployed model is weakest.

## Location variety — the part that actually matters

Aim for 20 distinct spots and keep them different from each other:

walls and shutters · gates and fences · lamp posts and bollards · bus stops and
shelters · rubbish bins · parked vehicles · signboards · stair rails · covered
walkways · playground/court equipment

Vary the surface, the lighting, the background clutter, and the time of day.
Same street, ten metres apart, pointing the same way = **one** scene, not two.

---

## Safety and permission

Only stage this on property you own or have permission to use, and cause **no
actual damage** — kicking a gate that does not break, marking with a capped or
dry marker, tipping an empty bin and setting it back up. The model learns the
*motion*, not the damage. Tell anyone nearby what you are filming, and do not
record identifiable bystanders who have not agreed.

This is also worth a sentence in the methodology: staged data collection with
consent, on permitted property, no damage caused.

---

## Handing it over

Drop everything in one folder, one **subfolder per location**, named for the
place:

```
vandalism_filmed/
  01_agdao_gate/        act_01.mp4  quiet_01.mp4  passerby_01.mp4  busy_01.mp4
  02_soliman_wall/      ...
```

The subfolder name becomes the group key, so the split is correct
automatically. I will handle extraction, the same-camera negative pairing, the
contact-sheet review, and the manifest.

**Rough yield:** 20 locations × ~90 s usable ≈ 30 minutes → roughly 350–400
clips across **20 scenes**, which lands vandalism between robbery's 26 and the
11 that failed. Combined with the 18 UCF sources already annotated, that is
**~38 scenes** — comfortably past where robbery started working.

---

## What to do meanwhile

Vandalism should be **disabled**, not left as a heuristic. The rule
(`score_vandalism`) requires a YOLO-detected `Sign` box *and* no other person
nearby; on Agdao market or Bankerohan the second condition is almost never
true, and it has never been evaluated against labelled footage until now. A
class that cannot fire is more honest switched off than left in with an
invented 0.84 confidence attached to it.
