# Docs folder index

Fifteen files live here and they are not equal. Four are the research core,
four are defense material, three are engineering records, two are stale
exports that would misrepresent the work if cited, and two are drafts.

---

## Start here

**Showing this to the panel? Read `START_HERE_PANEL.md` first** — it names the
four documents to actually put in front of them (the HTML and PDF ones), in
order, and says which files not to hand over. Everything below is the full
index, most of it working material rather than presentation material.

| File | Content |
|---|---|
| `START_HERE_PANEL.md` | **The reading order for the defense.** Which four documents to show, what to say about each, the three sections to read if you only have five minutes, and what to keep back. |
| `ecovision_full_report.md` | **Everything, in one document, written as a research report.** Abstract, related work, architecture, data, training, results, negative results, threats to validity, future work. Consolidates every other file here into one narrative — read this for the whole picture, then follow its §13 document map into the specialised files for detail. The separate documents remain authoritative for their own subjects. |

---

## Research core

| File | Content |
|---|---|
| `detection_performance_report.md` | Every measured number for all three detectors, each with the caveat that limits it. The authoritative source for any figure quoted elsewhere — nothing here is estimated. |
| `progress_report_violence_detection.md` | 1,715 lines. The full development narrative for the violence detector: what was tried, what failed, why, with derivations. §§27–32 cover the daynight checkpoint and its adoption. |
| `model_training_and_data.md` | **The methodology/RRL reference.** One section per model — what it does, how it is implemented, which datasets built it *with links and licences*, the exact training configuration, what it measures, and what would improve it. Also records the design decisions that were made rather than defaulted (why separate models instead of one softmax; why `phone` is a class; why `Sign` was removed) and an ordered improvement list. |
| `data_splits_and_leakage.md` | **Read before quoting any accuracy in this project.** How every split is grouped, the measured leakage on each (all zero as of 21 Aug), and the limitations that survive a clean audit. Records the 99.4% / 14.6% / 12.0% overlap that invalidated the previous weapon detector's metrics, and why the audit deliberately shares no code with the scripts that build the splits. |
| `related_work_notes.md` | Literature notes in two parts. Part I: Sultani MIL, RTFM, MGFN, CUE-Net, object-centric WSAD. Part II (21 Aug): the vandalism-dataset survey — every public alternative to UCF-Crime checked class-by-class — plus scene bias, spatial cropping, and the project's own non-literature findings. Verification status is marked per citation. |

## Defense material

| File | Content |
|---|---|
| `model_behavior_defense.html` | 13 sections, confusion-matrix charts for all three detectors, 3 animations, a glossary, and a section anticipating likely questions. Self-contained HTML, opens in a browser. |
| `EcoVision_Defense_Reference.pdf` | The same document, print-styled. |
| `convolution_explainer.md` | What YOLO does vs what X3D does, with animations. |
| `figures/` | 7 figures, PNG at 300 dpi and vector PDF. |

## Draft material

| File | Status |
|---|---|
| `paper_revised_sections.md` | Rewritten paper sections. Numbers should be checked against `detection_performance_report.md` before submission — this file is edited independently and can drift. |
| `paper_revision_notes.md` | Notes on what changed in the revision and why. |

## Engineering records

Document what was built rather than what was found.

| File | Content |
|---|---|
| `final_checks.md` | Dated snapshot (14 Aug) of bugs found and fixed, with how each was found. Describes that date's build state, not necessarily the current one. |
| `USER_HIERARCHY_PLAN.md` | Role and permission design. |
| `vandalism_data_collection.md` | The filming protocol that would make the vandalism-in-progress detector viable. Not yet executed. |

---

## Stale exports — do not cite

| File | Reason |
|---|---|
| `archive/stale-exports-2026-08/` | The 12 Aug PDF and HTML exports of the progress report, **moved out of `docs/` on 22 Aug** so they cannot be picked up by mistake. See that folder's `WHY_ARCHIVED.md`. |
| `archive/model_behavior_defense - Copy.pdf` | A 45 KB fragment from before the animations, glossary and training sections existed. The current file is several hundred KB. |

Regenerate rather than trust an export. For the defense document:

```
node_modules\electron\dist\electron.exe tools\html_to_pdf.js ^
    docs\model_behavior_defense.html docs\EcoVision_Defense_Reference.pdf
```

The progress report has no automated export; the `.md` is the only
authoritative copy.

---

## Headline figures, as of 19 Aug

All measured on data the model never trained on. Full detail, including raw
TP/FP/TN/FN counts, is in `detection_performance_report.md`.

| Class | Headline | What it does not capture |
|---|---|---|
| Violence (daynight checkpoint, deployed 18 Aug) | 95.0% of events detected on continuous spliced footage; 4.50 false alarms/hr aggregate across three cameras | Not a uniform improvement over the previous checkpoint: the flyover camera falls 45 → 6/hr, but the camera outside Lyn's Restaurant gets *worse*, 6 → 12/hr. The aggregate hides that spread. |
| Robbery | 84.2% accuracy, 5.6% false-positive rate | Recall is 65.3% — misses roughly one robbery in three. Measured on 8 source scenes, not 139 independent samples; clips from the same video are correlated. |
| Vandalism | Disabled by default | Both the rule-based and trained-model routes were built and measured, not just assumed to fail. The trained model's 70.3% accuracy is below the 78.4% baseline of always guessing "vandalism". |

One caveat applies to all three: recall on the actual deployment cameras is
unmeasured, because no labelled incident has ever been recorded on them. That
follows from the problem — violence cannot be labelled on footage where none
has happened — rather than from a gap in the measurement work.

**Note on the 70.3% above:** that figure belongs to the trained *vandalism*
model, not to any weapon figure. It has been misattributed once in working
notes; the weapon detector's own baseline is 79.7% recall (TP 1255 / FP 5 /
TN 578 / FN 319) on the final epoch-98 checkpoint. The epoch-68 checkpoint
measured 74.3%; both were reproduced independently by two scripts on 21 Aug.

---

## Status as of 21 Aug — headline figures above not yet updated

All three retrains are now complete. The headline table above still shows the
19 Aug snapshot; these are the current measurements.

| Class | Change | Result |
|---|---|---|
| Graffiti marks | Retrained on 7,943 images (was 1,177) | **Beats deployed on all four metrics** on the same untouched 165-image benchmark: mAP50 0.734 vs 0.718, recall 0.646 vs 0.602. Not yet deployed. |
| Weapons | Rebuilt leakage-free, `Sign` class removed, crashed run resumed to epoch 100 | Final epoch-98 checkpoint: **79.7% recall / 85.0% accuracy** at deployed thresholds, up from 74.3% / 81.0%. Re-swept thresholds give **89.0% recall at 3.1% FPR** (gun 0.30 / knife 0.23). Not yet deployed. |
| Vandalism | Scene count 11 → 26 sources | **82.4% accuracy vs a 62.7% baseline** — the class now carries positive information, where the previous model was 8.1 points *below* its baseline. **Still disabled:** FPR 36.8% against deployed robbery's 5.6%. |

Also measured and recorded rather than assumed: the rule-based vandalism route
was instrumented per condition and scored 8.3% recall (0% before the graffiti
detector unblocked its gate), and a change-detection prototype reached 3 of 4
events but false-alarmed on all four held-out cameras. Both remain disabled.
See `data_splits_and_leakage.md` for why any figure here is quotable at all.
