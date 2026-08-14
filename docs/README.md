# Which of these is research material?

Fourteen files live here and they are not equal. Three are for the paper, three
are for the defense, three are engineering records, and **two are stale exports
that would misrepresent the work if cited.** This page says which is which.

---

## Cite these — the research core

| File | What it is | Use it for |
|---|---|---|
| **`detection_performance_report.md`** | Every measured number, each with the caveat that limits it. Nothing estimated. | **The single source for any figure you quote.** If a number is not in here, do not put it in the paper. |
| **`progress_report_violence_detection.md`** | 1,715 lines. The full narrative: what was tried, what failed, why, with derivations. §§27–32 cover the final phase. | Methodology, and the negative results — the most transferable part of the work. |
| **`related_work_notes.md`** | Literature notes: Sultani MIL, RTFM, MGFN, CUE-Net, object-centric WSAD. | Related Work. |

## Present these — the defense

| File | What it is |
|---|---|
| **`model_behavior_defense.html`** | 11 sections, 8 charts, 3 animations, a 27-term glossary, and "Answers to the questions you will be asked". Open in a browser. |
| **`EcoVision_Defense_Reference.pdf`** | The same document, print-styled. Hand to a panel. |
| **`convolution_explainer.md`** | What YOLO does vs what X3D does, with animations. For the "explain your method" question. |
| **`figures/`** | 7 figures, PNG at 300 dpi for slides and vector PDF for the thesis. |

## Draft material

| File | Status |
|---|---|
| `paper_revised_sections.md` | Rewritten paper sections. Draft — check every number against `detection_performance_report.md` before submitting. |
| `paper_revision_notes.md` | Notes on what needed changing and why. |

## Engineering records — not research

Useful, but they answer "what did we build" rather than "what did we find".

| File | What it is |
|---|---|
| `final_checks.md` | Every bug found and fixed, with how it was found. Verification results. |
| `USER_HIERARCHY_PLAN.md` | Role and permission design. |
| `vandalism_data_collection.md` | The filming protocol that would make vandalism viable. Future work. |

---

## Do NOT cite these

| File | Why |
|---|---|
| `progress_report_violence_detection.pdf` | **Exported 12 Aug. The `.md` gained §30, §31 and §32 on 14 Aug** — the venue-overlap analysis, the confirmation-requirement result, and the property-crime build. This PDF is missing all three and shows an earlier, worse picture of the work. |
| `progress_report_violence_detection.html` | Same export, same date, same problem. |
| `model_behavior_defense - Copy.pdf` | A 45 KB fragment from an earlier render, before the animations, glossary and training sections. The current file is 436 KB. |

**Regenerate rather than trust an export.** For the defense document:

```
node_modules\electron\dist\electron.exe tools\html_to_pdf.js ^
    docs\model_behavior_defense.html docs\EcoVision_Defense_Reference.pdf
```

The progress report has no automated export; treat the `.md` as authoritative.

---

## The three numbers to have ready

Every one is measured on data the model never trained on.

| Class | Headline | The caveat you should state before being asked |
|---|---|---|
| Violence | 95.0% of events on continuous footage, 17 false alarms/hr | The rate is 0 / 6 / 45 per hour across three cameras. A single average hides a >45× spread. |
| Robbery | 84.2% accuracy, 5.6% FPR | Recall is **65.3%** — it misses about one robbery in three. 8 test scenes, not 139 independent samples. |
| Vandalism | Disabled | Trained *and* measured; 70.3% accuracy is **below** the 78.4% always-guess baseline. Both routes failed. |

**And the one that applies to all three:** recall on the actual deployment
cameras is unmeasured, because no labelled incident has ever been recorded on
them. That is a property of the problem, not an oversight — say it first.
