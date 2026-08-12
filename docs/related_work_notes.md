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
