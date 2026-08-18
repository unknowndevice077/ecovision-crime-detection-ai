# Docs folder index

Fourteen files live here and they are not equal. Three are the research core,
four are defense material, three are engineering records, two are stale
exports that would misrepresent the work if cited, and two are drafts.

---

## Research core

| File | Content |
|---|---|
| `detection_performance_report.md` | Every measured number for all three detectors, each with the caveat that limits it. The authoritative source for any figure quoted elsewhere — nothing here is estimated. |
| `progress_report_violence_detection.md` | 1,715 lines. The full development narrative for the violence detector: what was tried, what failed, why, with derivations. §§27–32 cover the daynight checkpoint and its adoption. |
| `related_work_notes.md` | Literature notes: Sultani MIL, RTFM, MGFN, CUE-Net, object-centric WSAD. |

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
| `progress_report_violence_detection.pdf` | Exported 12 Aug. The `.md` gained §30–32 on 14 Aug (venue-overlap analysis, the confirmation-requirement result, the property-crime build) — this PDF predates all three and shows an earlier, worse picture of the work. |
| `progress_report_violence_detection.html` | Same export, same date, same gap. |
| `model_behavior_defense - Copy.pdf` | A 45 KB fragment from before the animations, glossary and training sections existed. The current file is several hundred KB. |

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
