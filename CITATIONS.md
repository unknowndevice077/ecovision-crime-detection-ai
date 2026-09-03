# Datasets & Attribution

The model weights shipped with EcoVision Sentinel (`weights/*.pt`, bundled by the
installer — see `package.json`'s `extraResources`) were trained on the datasets
below. None of the raw datasets themselves are redistributed by this project —
only the trained checkpoints are — but several carry attribution requirements
(most are Roboflow Universe exports under CC BY 4.0) and academic norms expect
the rest cited regardless. This file exists so that obligation is met in one
place instead of scattered across training scripts nobody outside the project
ever reads.

Confidence varies per entry: Roboflow exports carry an exact workspace/project/
license block that was read directly off the downloaded `data.yaml`, so those
are verified. The academic video benchmarks (RWF-2000, UCF-Crime, CamNuvem,
CCTV-Fights) are cited from what the training scripts and dataset READMEs on
disk record; if you're citing this work formally, verify the exact paper
venue/year against the dataset's own page before quoting it.

## Violence detection (`x3d_xs_violence_*.pt`)

- **RWF-2000** — real surveillance-camera fight/non-fight clips; the primary
  source for both the per-track and scene violence checkpoints. Ming Cheng,
  Kunjing Cai, Ming Li, *"RWF-2000: An Open Large Scale Video Database for
  Violence Detection,"* ICPR 2020. Dataset: <https://github.com/mchengny/RWF2000-Video-Database-for-Violence-Detection>
- **SCVD** (Surveillance Camera Violence Dataset) — used alongside RWF-2000 for
  the same checkpoints (`build_explainer_doc.py` cites both together). Sourced
  as a standalone academic clip set; verify the exact paper before citing
  formally, it was not re-confirmed against a live source this session.
- **UCF-Crime** — Waqas Sultani, Chen Chen, Mubarak Shah, *"Real-world Anomaly
  Detection in Surveillance Videos,"* CVPR 2018. Project page:
  <https://www.crcv.ucf.edu/projects/real-world/>. Only clips with
  frame-accurate temporal labels were used (`extract_ucf_crime.py`) — the
  ~1,600 folder-labelled-only crime videos were deliberately excluded, since a
  ten-minute video labelled "violent" for a twenty-second act would teach the
  model that the CCTV look itself means the crime.
- **CCTV-Fights** — official dataset from NTU ROSE Lab (registration-gated);
  this project used an unofficial Kaggle mirror
  (`shreyj1729/cctv-fights-dataset`) while the official registration was
  pending. Official dataset: NTU ROSE Lab, CCTV-Fights.

## Robbery detection (`x3d_xs_robbery_scene.pt`)

- **CamNuvem** — Brazilian retail/street CCTV robbery footage, the only source
  for the deployed robbery checkpoint (and for `docs/media/robbery_detection.gif`
  / `robbery_alert.jpg` in this README, both real held-out clips this
  checkpoint's training/checkpoint-selection never saw). Source:
  <https://github.com/daviduarte/camnuvem-dataset>. Only the 16 of 49
  frame-labelled videos that read as outdoor street/sidewalk/parking-lot scenes
  were used (`extract_camnuvem_robbery.py`) — CamNuvem is mostly shop/pharmacy
  interiors, and training an outdoor streetlight model on indoor footage
  reproduces the same indoor-contamination problem the UCF-Crime outdoor
  allowlist exists to prevent.
- **UCF-Crime** (property-crime subset) — see above; the same source, filtered
  to outdoor robbery-relevant clips via `filter_ucf_property_crime.py`'s
  `ROBBERY_OUTDOOR` allowlist.

## Weapon detection (`weapons_v2.pt`)

Seven Roboflow Universe exports, merged into one gun/knife/phone dataset
(`build_weapon_dataset.py`, `merge_weapons.py`) and split by base image with
0/0/0 train/val/test overlap (`weapon_ds_v2/`, verified by
`verify_all_leakage.py`):

| Dataset | Workspace / Project | License |
|---|---|---|
| Weapon-Detection (gun, knife, person_with_mask) | `weapon-rcjrw/weapon-detection-pgqnr` v8 | CC BY 4.0 |
| Robbery Activity (source of the `phone` class + metal-detector/thermal-gun confusables) | `dsstrc/robbery-activity-wt3am` v7 | CC BY 4.0 |
| gun and knife detection | `mahad-ahmed/gun-and-knife-detection` v1 | CC BY 4.0 |
| gun detection | `workspace-1qko2/gun-detection-ghlzd` v4 | Public Domain |
| Gun-cctv-detection | `dietest/gun-cctv-detection` v1 | CC BY 4.0 |
| knife-dataset | `workspace-zqssx/knife-dataset-4kytl` v2 | CC BY 4.0 |
| CCTV Knife Detection Dataset | `simuletic/cctv-knife-detection-dataset-zkkaf` v1 | CC BY 4.0 |

Each is browsable at `https://universe.roboflow.com/<workspace>/<project>`.
The `phone` class exists specifically because the deployed model has no word
for "phone," "scissors," or "razor" otherwise — anything gun/knife-shaped had
to be emitted as one of those two classes with no alternative, which is what
produced the false-alarm rate that motivated retraining v2 in the first place
(see `README.md`'s Weapon detection entry for the measured numbers).

## Vandalism detection (`vandalism_marks_v2.pt`)

- **17k-graffiti** — Roboflow Universe mirror (`detr-2bavb/17k-graffiti` v1,
  CC BY 4.0, <https://universe.roboflow.com/detr-2bavb/17k-graffiti>) of the
  full **17K-Graffiti** corpus (Zenodo record 5899631, 73.1 GB), which is
  itself restricted to academic use behind an access request naming an advisor
  and university. This project used the smaller, directly-downloadable
  Universe subset (7,641 images / 14,703 graffiti boxes), not the restricted
  full corpus.
- An earlier, smaller internal graffiti annotation set (1,177 images, the
  baseline `vandalism_marks.pt` before the 17k-graffiti expansion) — its exact
  upstream source was not preserved in a locally-recoverable form; if you can
  trace it, please open an issue so this file can be corrected.

## Pose tracking (`yolo11s-pose.pt`)

Ultralytics YOLO11-pose, pretrained checkpoint, used as shipped (not
fine-tuned on any of the datasets above). See
[Ultralytics YOLO11](https://github.com/ultralytics/ultralytics) — AGPL-3.0
licensed, which is why this project is AGPL-3.0 too; see the License section
of `README.md`.
