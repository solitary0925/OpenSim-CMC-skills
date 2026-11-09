# RRA kinematics for single-leg CMC

Select `"kinematics_source": "RRA"` only after the user chooses it for the batch. Use the selected force-qualified replay and its adjusted model exported together in the original frame. Match the RRA job to the exact trial, stance, foot and designated plate. Read the replay review and resolve any warnings with the user before preparing the handoff. A minimum-J diagnostic export is ineligible.

Prepare the target-leg CMC inputs with the adjusted RRA model, its motion, and the audited target plate. Use the full verified support available from this replay, contact and loads. Preserve original event times and frame numbers. If a regular frame grid is needed, interpolate replay kinematics linearly on the original time basis within the native range. The CMC preflight checks all six pelvis coordinates and the six tracked target-leg coordinates against this replay.

Use a muscle-and-reserve working copy of the adjusted RRA model for the single-leg preparation. Use the named reserves, one per active target-leg coordinate. Keep the retained bodies' mass, center of mass and inertia from the adjusted RRA model. Prescribe the pelvis from the prepared RRA trajectory with the preparation's GCV splines. The SO adapter can prepare these files without solving SO. If supplying ID for reserve ratios, use the same model, trajectory and loads.

In the prepared input folder, extend `input_audit.json` with the following fields. Paths in `rra_source` can be relative to that folder. Include the four referenced files and the optional matching single-leg ID file by resolved path and SHA-256 in `source_hashes`.

```json
{
  "kinematics_source": "RRA",
  "rra_source": {
    "trial": "walk_01",
    "stance": "stance_01_R",
    "side": "R",
    "plate": 2,
    "model": "path/to/final_original_frame/RRA_model.osim",
    "motion": "path/to/final_original_frame/RRA_motion.mot",
    "final_review": "path/to/final_review.json",
    "frame_verification": "path/to/final_original_frame/frame_verification.json",
    "warning_disposition": ""
  }
}
```

Set `warning_disposition` to the user's actual decision if the review status is `selected_with_review_warnings`. The replay review must identify a selected iteration and a positive count of force-qualified candidates. Frame verification must confirm original-frame export, force-rule selection and the native time bounds. Preserve those records with the handoff. Tracking errors use the selected RRA trajectory as reference. The CMC batch and its Excel dashboard label the selected kinematics source.
