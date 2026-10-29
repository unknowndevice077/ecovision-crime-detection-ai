# Docs folder index

The files here are not equal. Five are the research core, four are defense
material, five are operations plans for a real pilot, a few are engineering
records, two are drafts, and the archive holds stale exports that would
misrepresent the work if cited. Updated 22 Oct 2026.

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
| `detection_performance_report.md` | Every measured number for all four detectors, each with the caveat that limits it. The authoritative source for any figure quoted elsewhere — nothing here is estimated. |
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
| `figures/` | Figures 1-7 (August, the models of that time) and **figures 8-15 (October)**: violence progression, the double-check's effect, its operating points, weapons, robbery design, training data, missed fights, and confidence intervals. PNG at 300 dpi and vector PDF. Regenerate the October ones with `tools/make_october_figures.py`. |
| `statistics_summary.md` | **The October statistics**: 95% confidence intervals for every headline recall and false-alarm rate, the tests behind "does the double-check really help", and how to say it honestly (the robbery gain is clear; the fight gain is marginal; the false-alarm drops are not individually significant on 5 hours). |
| `how_ecovision_sees.html`, `detection_metrics.html` | Visual explainer and a metrics ledger, self-contained HTML. |
| `EcoVision - Manuscript (REVISED).docx` | The paper itself. Edited by hand; check its numbers against `detection_performance_report.md`. |
| `EcoVision - Manuscript (REVISED, Oct 2026).docx` | The paper with the October results added as section 4.14 (8 figures, 3 tables), an abstract addendum, two new conclusions, and corrected statements about robbery and vandalism. Built from the August file, which is unchanged. Update the table of contents and the lists of figures and tables in Word. |

## Draft material

| File | Status |
|---|---|
| `paper_revised_sections.md` | Rewritten paper sections. Numbers should be checked against `detection_performance_report.md` before submission — this file is edited independently and can drift. |
| `paper_revision_notes.md` | Notes on what changed in the revision and why. |

## Operations plans (written 26 Aug for a real pilot)

| File | Content |
|---|---|
| `evaluation_plan.md` | What evaluating the system means, in order, and the protocol. |
| `incident_response_plan.md` | From "the model fired" to "an officer is on the way": confirmation, notification, chain of custody. Telegram and SMS notifications from §2 now exist. |
| `privacy_compliance_plan.md` | RA 10173 (Data Privacy Act) requirements and where the system still falls short. |
| `recovery_plan.md` | What to do when a piece breaks, written for a non-developer. |
| `scaling_plan.md` | How many cameras one PC can run, measured on the pilot hardware. |

## Engineering records

Document what was built rather than what was found.

| File | Content |
|---|---|
| `final_checks.md` | Dated snapshot (14 Aug) of bugs found and fixed, with how each was found. Describes that date's build state, not necessarily the current one. |
| `USER_HIERARCHY_PLAN.md` | Original role and permission design (August). The September additions — diced permissions, custom roles, identity verification, report requests, audit log — are summarised in the root `README.md` under Organization & Roles. |
| `ADMIN_AND_DEVTEAM_GUIDE.md` | **Every function of the admin and DevTeam panels**: roles, scoping, permissions, approvals, stations, audit log, AI Models tab, personnel, notifications, report requests, and a who-can-do-what table. |
| `INSTALLER_NOTES.md` | Why the installer is built the way it is, why it is now an `.exe` + `.bin` in a zip (Oct, ~7.5 GB), and how `.env` is kept out of it. |
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

## Headline figures, as of 22 Oct 2026

All measured on data the models never trained on: four held-out street cameras
(2 h, nothing criminal in them) for false alarms per hour, and held-out crime
clips spliced into continuous street footage for how many events raise an
alarm, both through the shipped smoothing and confirmation rules. Full detail
is in `detection_performance_report.md` and in each model's `config.json` note.

| Class | Headline | What it does not capture |
|---|---|---|
| Violence (two-model ensemble, person-crop confirmed, 0.45 × 2) | 88.0% of 200 fights at 2.0 false alarms/hr; 93.1% accuracy / 92.8% recall / 93.4% precision on 1,310 test clips | Two hours of camera footage is a small sample — one alarm moves the rate by 0.5/hr. Most misses are ground fights, distant crowds and close-up or occluded views. |
| Weapons (`weapons_v3`, 960 px) | 89.6% recall on large guns, 39.4% on street-camera-size guns, 83.8% on street-size knives; ~8× fewer false weapons than v2 on street footage | Guns under ~5% of the frame are still mostly missed (~6%). No real armed incident has ever been recorded on the deployment cameras. |
| Vandalism (experimental, person-crop confirmed, 0.60 × 3) | 87.1% of 31 acts at 1.0 false alarm/hr | 31 acts from few source videos — approximate. |
| Robbery (experimental, 0.85 × 3) | 0.5 false alarms/hr | Catches ~40% of test clips and 8.3% of events in continuous footage — tuned to stay quiet rather than cry wolf. Every retrain since has been rejected on measurement. |

One caveat applies to all four: recall on the actual deployment cameras is
unmeasured, because no labelled incident has ever been recorded on them. That
follows from the problem — a crime cannot be labelled on footage where none
has happened — rather than from a gap in the measurement work.

### Alarm double-check (in the app, experimental)

A second-stage check by a small vision-language model (Qwen3-VL-2B): violence
88.0% → 94.0% of fights at 1.4 → 0.4 false alarms/hr (re-checking long candidates every 7 s); robbery 8.3% → 51.1% of
events at 2.0/hr, on 5 hours of held-out footage. In the app since 7 Oct
(`maincode/alarm_verifier.py`, config `detection.verifier`), on the GPU (~7 s
per alarm, ~4.2 GB VRAM) or the CPU (llama.cpp 4-bit, ~15 s, ~2.1 GB RAM, same
accuracy). Robbery now comes from the fight detector's whole-frame
candidates, checked by two questions (is it criminal; is it more robbery than
fight): 59.4% of robbery events alerted as robbery at 0.6/hr, with 20.5% of
fights also labelled robbery (robbery model + one question: 51.1% at 2.0/hr,
69% of fights). The robbery model is only the fallback. A robbery confirmed
within 60 s of an ASSAULT alert upgrades that incident instead of opening a
second one (12 Oct). Weapons (14 Oct): every ARMED THREAT alert is asked "is
this person holding a gun or a knife?" on a crop around that person — 2.8 →
1.4 false alerts/hr on 5 h of cameras, 97.9% of real weapons kept; **GPU
backend only** so far. On a 4 GB card (e.g. a laptop RTX 3050) the CPU backend
is used: fights 91.0% at 0.2/hr, weapon alerts unverified. Details:
detection_performance_report.md §1.6.
