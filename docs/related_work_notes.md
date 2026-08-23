# Related-work notes: how other groups attack these exact failures

Compiled 2026-08-12. Purpose is not a literature review for its own sake — it is
to check whether the three problems this project measured are known problems
with known fixes, and whether any of those fixes are reachable on a single
GTX 1660 SUPER before a defense.

**Reading caveat, stated up front:** the CoMT-VD summary below comes from search
result text and the paper's abstract. The publisher (MDPI) returned HTTP 403 to
an automated fetch, so the full method section has not been read. The described
mechanism is standard enough that the summary is unlikely to be wrong in
outline, but no specific number from it should be quoted in the report until
someone opens the PDF.

---

## 1. The measurement gap may not be a gap — UCF-Crime

**This corrects something stated twice in this project's own report.** The claim
was: *"recall on continuous real footage containing real violence cannot be
measured, because we have no such footage."* That is true of *our* cameras. It
is not true in general.

[UCF-Crime](https://www.crcv.ucf.edu/projects/real-world/) is 1,900 **untrimmed,
continuous** real CCTV videos, ~128 hours, covering 13 anomaly classes including
**Fighting** and **Assault**, plus a large normal-video set. 1,610 training
videos are weakly labelled (video level); **290 test videos are annotated at
frame level.**

Why that matters here specifically, more than "another dataset" would:

- Every violence clip this project trains and tests on is a **5-second trimmed
  excerpt**. That is exactly why the operating-point sweep hit a wall: clips are
  too short to satisfy a multi-second persistence requirement, so recall at high
  `consecutive_required` is partly a clip-length artefact rather than a
  measurement. Untrimmed video removes that ceiling entirely.
- It permits the one measurement this project has never made: **false alarms per
  hour and recall, on the same continuous real-CCTV footage, at the same
  operating point.** Right now those two numbers come from different sources and
  are stitched together with an assumption.
- It is real CCTV, not staged benchmark footage, so it attacks the domain gap
  (§12.4) at the same time.

It does not solve everything: it is still not *our* cameras, so per-camera
recall stays unverifiable. But "unverifiable on our specific cameras" is a much
narrower and more defensible limitation than "unverifiable at all."

Cost: the full dataset is large (order 100GB+; needs checking against the ~185GB
free). A subset — the Fighting/Assault classes plus a matched sample of Normal
— would likely be enough for the operating-point work and far cheaper.

## 2. Domain shift is the named, studied problem — and unlabeled target data is the lever

[Overcoming Domain Shift in Violence Detection with Contrastive Consistency
Learning](https://doi.org/10.3390/bdcc9110286) (CoMT-VD) frames the exact failure
measured here: models degrade badly across real-world scenarios because of
distributional gaps between training data and the target environment, which is
precisely the 91.9% benchmark / 67.0% real-CCTV split in §14.8.

Its approach is a **Mean Teacher** setup with consistency regularisation between
a student and a teacher network, using **unlabeled target-domain data** to learn
domain-invariant features.

Why this is worth attention rather than filing away: this project has an
effectively unlimited supply of exactly that input — unlabeled Philippine street
CCTV, capturable at will from public streams. The current approach (capture
footage, assume it contains no violence, train it as negatives) is a crude
manual version of the same idea, and it carries a real risk the principled
version does not: **if a real incident ever occurs in footage assumed to be
negative, the model is explicitly trained to ignore it.** Consistency
regularisation uses the unlabeled data without asserting a label on it, which
removes that failure mode.

## 3. Where the field actually is, for calibrating claims

Weakly-supervised video anomaly detection on real CCTV benchmarks currently sits
around **91.6% AUC on UCF-Crime**
([GS-MoE](https://arxiv.org/abs/2508.06318), Aug 2025), with
[Holmes-VAD](https://arxiv.org/pdf/2406.12235) at 89.5% AUC / 90.7% AP on
XD-Violence.

This is a useful reality check on expectations. Published SOTA on *real,
untrimmed CCTV* is ~90% AUC — not the 95–97% this project reports on trimmed
benchmark clips. Those numbers are not comparable, and the gap between them is
roughly the gap this project independently measured between its benchmark and
real-CCTV splits. **The 67.0% real-CCTV accuracy is bad, but it is bad relative
to well-resourced research labs on a harder task, not relative to a solved
problem that everyone else has cracked.** Worth saying plainly at a defense.

Also relevant: these methods are trained on **video-level labels only** (weak
supervision), which is what UCF-Crime provides and what passive capture could
provide cheaply.

## 4. Lightweight/edge deployment is an active area, which supports the scaling section

Recent work targets exactly this project's constraint — real-time violence
detection on resource-limited hardware, using a light CNN backbone for spatial
features plus a GRU for temporal modelling
([embedded framework, Sci Rep](https://www.nature.com/articles/s41598-026-44939-x);
[smart-city lightweight models](https://www.americaspg.com/journal/28/article/3858)).
X3D-XS on a 1660 SUPER is a defensible choice in that company, and the CNN+GRU
alternative is a concrete comparison point if the panel asks "why X3D and not
something cheaper."

---

## What I would actually do with this

In priority order, cheapest and highest-certainty first:

1. **Pull a UCF-Crime subset (Fighting/Assault + Normal).** Directly fixes the
   clip-length artefact blocking the operating-point decision, and turns "we
   cannot measure recall on continuous footage" into "we measured it on real
   CCTV, though not on our own cameras." Needs a disk-space decision.
2. **Re-run the operating-point sweep on untrimmed video.** The current
   recommendation rests on 5–10 second clips and a stated lower-bound argument.
   This replaces the argument with a measurement.
3. **Mean Teacher / consistency regularisation on unlabeled PH capture** — the
   principled replacement for assuming captured footage is violence-free. Bigger
   change; only worth starting if the simpler fixes stall.
4. **Cite the ~90% AUC SOTA figures in the report** to frame the real-CCTV
   numbers honestly against the field rather than against trimmed benchmarks.

---
---

# Part II — references compiled 2026-08-21

Added while working the vandalism, weapon and graffiti problems. Same purpose as
Part I: check whether what this project measured is a known problem with a known
fix.

**Verification status is marked per item.** Anything labelled VERIFIED had its
source page or abstract fetched during this session; anything labelled UNVERIFIED
is from recall and must not be quoted with a number until someone opens the PDF.
Part I's CoMT-VD caveat still stands.

---

## 5. Vandalism video data: the field's entire supply, checked class-by-class

The question asked was whether UCF-Crime is the only source of vandalism video.
It very nearly is. Each dataset below had its published class list read directly,
not inferred from its title or abstract.

| dataset | vandalism class? | notes |
|---|---|---|
| [UCF-Crime](https://www.crcv.ucf.edu/projects/real-world/) (CVPR 2018) | **yes** — 50 Vandalism + 53 Arson videos | what this project uses |
| [MSAD](https://msad-dataset.github.io/) (NeurIPS 2024) | **yes** — "vandalizing glass, doors, and other structures" | property destruction, not marking |
| [NWPU Campus](https://campusvaa.github.io/) (CVPR 2023) | **no** — none of 28 classes | nearest are "kicking trash can", "littering" |
| [UBnormal](https://arxiv.org/abs/2111.08644) (CVPR 2022) | **no** — none of 22 classes | synthetic (Cinema4D) regardless |
| [XD-Violence](https://arxiv.org/abs/2007.04687) (ECCV 2020) | **no** — 6 classes | abuse, accident, explosion, fighting, riot, shooting |

VERIFIED: the NWPU 28-class table and the UBnormal 22-class list were both
fetched and read in full. Neither contains any property-damage or marking class.

**The finding worth stating in the write-up: there is no public video dataset of
the graffiti ACT.** Not a small one — none. UCF-Crime is the only source of
vandalism video of any kind, and MSAD is the only meaningful addition to it,
whose vandalism is glass and doors rather than tagging. The four graffiti scenes
this project hand-annotated out of UCF's 50 Vandalism videos are therefore close
to the world's available supply, not evidence of incomplete data collection.

That reframes the vandalism limitation from "we did not gather enough data" to
"this class is data-starved at the field level," which is a defensible position
rather than an admission.

**MSAD is worth requesting even though it cannot arrive before the defense.** Its
vandalism subtypes map onto this project's SECOND vandalism class (property
destruction), for which 15 UCF scenes are already annotated; combined they would
approach the ~26-scene point at which the robbery class began working. Videos
require an approval form and email turnaround. Note that MSAD's extracted I3D and
Video-Swin features download with no request at all — useless for training X3D on
raw frames, but enough to benchmark against a published dataset.

## 6. Weakly-supervised VAD: why MIL was implemented, measured, and then dropped

- **[Sultani et al., CVPR 2018](https://openaccess.thecvf.com/content_cvpr_2018/papers/Sultani_Real-World_Anomaly_Detection_CVPR_2018_paper.pdf)** — the origin of both UCF-Crime and the
  MIL formulation: videos are bags, segments are instances, trained with a
  ranking hinge loss plus sparsity and temporal-smoothness terms. VERIFIED for
  method and dataset. Its headline UCF-Crime AUC (75.41%) is from the paper's own
  results table and was NOT independently confirmed in this pass — read the PDF
  before quoting the figure.

- **[RTFM, Tian et al., ICCV 2021](https://arxiv.org/abs/2101.10030)** — replaces MIL's single-max
  selection with a top-k feature-magnitude criterion, which directly addresses
  MIL's central weakness: only the argmax segment receives gradient, so one
  mislabelled peak dominates learning. Reports **84.30% AUC on UCF-Crime**
  (VERIFIED, and 97.21% on ShanghaiTech). This is the method to reach for if MIL
  is revisited.

- **MIST (CVPR 2021)** — generates pseudo clip-level labels and then fine-tunes
  the encoder. UNVERIFIED in this pass; mentioned for completeness only, no
  number attached.

Relevance to this project: a feature-cached MIL head was built
(`train_robbery_mil_v2.py`), the leakage in its first version was found and
fixed, and it was then rejected on its measured merits rather than abandoned.
The RTFM result above is the reason that rejection should be recorded as "MIL as
originally formulated underperformed here" and not as "MIL does not work" — the
known fix for its failure mode was not attempted.

## 7. Scene bias — the named reason a classifier learns the background

**[Choi et al., "Why Can't I Dance in the Mall? Learning to Mitigate Scene Bias in
Action Recognition", NeurIPS 2019](https://proceedings.neurips.cc/paper_files/paper/2019/file/ab817c9349cf9c4f6877e1894a1faa00-Paper.pdf)** (VERIFIED). Action models latch onto scene
context — basketball is predicted from the court, not the movement — so the
representation fails to transfer. The fix is an adversarial scene-type loss plus
a human-mask confusion loss that penalises confident predictions when the actors
are masked out. Code: [vt-vl-lab/SDN](https://github.com/vt-vl-lab/SDN).

This is the published name for the failure this project kept hitting: it is why
same-camera negatives are extracted from the non-crime spans of the *same* video
(so camera identity carries no label information), and it is the mechanism behind
the benchmark-vs-real-CCTV accuracy gap.

## 8. Spatial cropping is how SOTA handles distant subjects

**[CUE-Net, Senadeera et al., CVPR 2024 Workshops (ABAW)](https://arxiv.org/abs/2404.18952)** (VERIFIED). SOTA on
RWF-2000 and RLVS. It performs violence detection by **spatially cropping around
detected people** before classification, explicitly to handle "distant or
partially obscured subjects." Code: [damith92/CUENet](https://github.com/damith92/CUENet).

Directly relevant: `x3d_violence_detector.py:318` `_crop_person()` already
implements the same idea, including a bystander merge. The state of the art
solves the small-person problem by cropping to people rather than by classifying
whole wide frames — which is the evidence behind the two-stage person-crop plan,
and the reason the earlier per-track-vs-scene comparison (71.0% vs 78.8%)
deserves a clean rerun now that its three confounds are known.

## 9. Findings from this project that are NOT from the literature

Recorded here because they are the kind of thing that gets rediscovered
expensively, and because a panel may ask where they came from. All are measured
in this repository, not cited.

- **Roboflow augmented-duplicate leakage.** Exported datasets ship roughly 2.5
  augmented copies per source image, and a naive split divides at *file* level.
  The augmented siblings of a training image then land in val and test. Measured
  overlap in the original weapon corpus: 99.4% / 14.6% / 12.0%. Splitting by base
  image instead brought all three to 0%. Any published metric from a
  file-level-split Roboflow export is inflated.

- **Training/runtime resolution mismatch.** A detector trained at `imgsz=640` and
  run at 416 in production is not the model that was benchmarked. This is why
  `optimize_weights.py` now reads the runtime constants out of `main.py` rather
  than the checkpoint, and why the graffiti retrain was deliberately run at 416.

- **Choosing an operating point on the split you then report.** Thresholds must
  be selected on validation and reported on test; doing both on test manufactures
  an improvement that does not exist. Applied in `sweep_weapon_thresholds.py`.

- **A rule can be structurally impassable rather than badly tuned.** The
  vandalism rule scored 0% recall because its condition-1 gate depended on a
  detector class that fired 0 times in 3,600 frames. Per-condition
  instrumentation distinguishes "needs tuning" from "cannot fire", and the two
  have nothing in common as problems.
