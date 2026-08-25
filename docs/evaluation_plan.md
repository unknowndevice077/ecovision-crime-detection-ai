# Evaluation plan — plain-language version

Written 2026-08-26. This explains what "evaluating" the detection system
actually means, in order, before laying out the protocol. If the only thing
you take from this document is the difference between §2 and §3, that's the
part that matters most for the defense.

---

## 1. Why "95% accuracy" is not the whole answer

A single accuracy number answers one question: *out of every clip we tested,
how many did the model label correctly?* That number already exists —
95.0% on 758 held-out clips (`docs/detection_metrics.html`). But a real
camera doesn't hand the system 758 pre-cut clips, one violent-or-not decision
each. It hands the system **continuous video, 24 hours a day, where violence
is rare** — maybe minutes out of a full day. A model can be 95% "accurate" on
a balanced test set and still be *useless* on a real camera in two distinct,
opposite ways:

- **It misses real incidents** (false negative) — the actual failure mode
  that matters most for a public-safety system: an assault happens, nobody
  gets alerted.
- **It cries wolf** (false positive) — fires on cars passing, shadows moving,
  crowds walking normally. Even a small per-clip false-positive rate becomes
  dozens of false alarms a day once you multiply by "checked every few
  seconds, all day, every day." An operator who gets 40 false alerts a day
  stops trusting the system and starts ignoring it — which is functionally
  the same as it not working.

**This is why evaluation has to be two separate numbers, not one:**
**recall** (did it catch what actually happened) and **false alarms per hour
of real, continuous footage** (did it also stay quiet the rest of the time).
Optimizing either alone is easy and useless — a model that alerts on
everything gets 100% recall and is worthless; a model that never alerts gets
zero false alarms and is worthless the same way.

---

## 2. What's already been measured (and is a real precedent for this plan)

This isn't theoretical — it's already been done once, for the deployed
violence model, and the results are in
`weights/x3d_xs_violence_scene_daynight.pt.meta.json`. Worth reading that
file directly; the short version:

- **40 violent clips spliced into real Davao night footage**, replayed
  through the actual running system: 38/40 caught (95% recall) at the
  shipped threshold.
- **4 real cameras, 20 minutes each, continuously**: every alert the
  operator would have seen, counted. Total 4.5 false alarms/hour — but
  broken down **per camera**, because the average hides that one camera
  (a flyover PTZ) drove almost all of it while two others produced zero.
- A tempting threshold change (0.45 instead of 0.5) was **measured and
  rejected**: it caught one more clip out of 40 but nearly doubled false
  alarms (4.5 → 8.25/hr). That's the kind of tradeoff this whole exercise
  exists to catch before it ships, not after.

That's the model. This document generalizes it into a repeatable protocol so
every detector (weapon, robbery, vandalism, and whatever violence
architecture Phase 2 of the scaling/accuracy plan lands on) gets evaluated
the same honest way, and so a panel question about methodology has a
document to point to instead of an ad hoc answer.

---

## 3. The protocol

### Step 1 — Precision/recall curve on the held-out test split

Not a single number. Sweep the decision threshold (e.g. 0.3, 0.4, 0.5, 0.6,
0.7) and record recall + false-positive rate at each, on the clip-level test
split that neither training nor checkpoint selection touched. This produces
a curve, not a point — the operating threshold gets picked *from* the curve
in Step 3, not assumed in advance.

Already scripted: `python test_x3d_true_heldout.py --scene --manifest-path 3way --split test`.

### Step 2 — False alarms per hour, on real continuous footage

Per-clip false-positive rate structurally understates a camera that runs 24/7
— a clip-level FPR of 5% sounds small until you realize a continuously
running camera produces a "clip" worth of frames every few seconds, all day.
The only honest measurement is: **point a real camera at a real scene for a
sustained block of time, and count what actually alerts.**

- Minimum 20–30 minutes per camera, matching what's already been done.
- Cover more than one camera and more than one time of day if possible — the
  flyover-vs-market gap above shows the false-alarm rate is not uniform
  across cameras, so one camera's number does not represent the system.
- A human reviews every alert produced and marks it real/false. This is the
  ground truth — there's no automated way to know if an alert was "correct"
  on unlabelled live footage.

### Step 3 — Pick the operating point, weighing both errors as real costs

With the curve from Step 1 and the real false-alarm rate from Step 2 in hand,
choose the threshold + `consecutive_required` (how many consecutive positive
frames before an alert fires) that:

- keeps recall above whatever floor is set as acceptable for a public-safety
  deployment (this is a policy decision, not a purely technical one — worth
  stating explicitly rather than picking silently),
- and keeps false alarms/hour low enough that an operator will not tune the
  system out.

Do not pick the threshold that maximizes accuracy on the test set — the
rejected-0.45 example above is exactly why: it looked better on one number
and was worse on the number that actually matters operationally.

### Step 4 — Re-verify after any model or config change

Any time a checkpoint, threshold, or `consecutive_required` changes, re-run
Steps 1–2 before shipping it as the new default. The project has already hit
this exact trap once — a config layering bug meant `config.json` (with every
model path and threshold) was silently never read, so edits appeared to do
nothing (`docs/final_checks.md` §2). The fix for that bug does not fix "we
assumed a change worked without re-measuring it" — only re-running this
protocol does.

---

## 4. What counts as a false alarm

Define this before running Step 2, not after looking at results (defining it
after invites unconsciously excusing inconvenient alerts):

- The system posts an alert (`/api/ai_trigger`) that a human reviewer,
  watching the same clip, would not classify as the target event.
- An alert that's *technically* triggered by the right kind of motion (e.g.
  two people play-fighting, mistaken for assault) still counts as false for
  operational purposes — the operator still got interrupted for nothing —
  even though it's an interesting edge case worth separately noting.

## 5. Where the results live

Log every run's raw numbers as a dated block, same pattern as the model
`.meta.json` sidecars already in `weights/` — each entry says what was
measured, on how much footage, at what threshold, and what was concluded.
That format is deliberately reused here because it's already proven itself:
it's what caught the 0.45-threshold regression before it shipped.
