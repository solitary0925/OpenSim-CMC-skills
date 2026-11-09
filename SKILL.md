---
name: opensim-vicon-cmc
description: Run and review prescribed-pelvis single-leg OpenSim 4.5 CMC using a user-confirmed IK or RRA trajectory, a designated target-foot force plate and a prepared original-knee CGM24 model. Preserve the verified support window and report Fast and Slow tracking and reserve RMS/MAX independently of SO.
---

# OpenSim single-leg CMC

Version **0.6.0**. Use a prepared prescribed-pelvis single-leg model, motion and loads. The confirmed trajectory prescribes all six pelvis coordinates and supplies the target-leg tracking reference. CMC controls six active target-leg coordinates with 46 muscle units and six leg reserves. Load only the force plate assigned to the target foot. Read [execution and review](references/execution.md) for the input and report contract.

## Confirm and preflight

- Before a new batch ask whether to add fourth-order Butterworth 6 Hz forward–backward zero-phase low-pass filtering. Offer no additional filtering and, if selected, confirm kinematics, force/torque, or both. Check and record upstream processing using [input filtering](references/input-filtering.md). Prescribe the pelvis from the same processed trajectory used for target-leg tracking.

- Before running each new batch, ask the user which trajectory to use unless they already explicitly chose for that batch. Offer **IK** (verified measured kinematics) and **RRA** (verified, force-qualified original-frame replay with its adjusted model). Wait for the selection and record it as `kinematics_source`. A prior batch choice does not select a new batch. If the chosen route is blocked, report the missing evidence and wait for repair or an explicit new choice.
- Identify the requested trial/stance/side, explicit plate, model and muscle-strength profile, filtering history, exclusions and new output directory. Check that the selected trajectory, model and processed loads match the prepared input audit. For RRA, use the [RRA handoff contract](references/rra-input.md).
- Use the audited 20 N target-foot event and one continuous interval with verified target-foot contact evidence. Confirm original frame numbers, source checksums, rotation, units, wrench representation and force timing. A plate crossing alone does not establish full target-foot loading. Wait for disposition of ambiguous trials. Do not bridge unknown gaps or invent forces.
- Solve the full original 20 N support whenever the chosen trajectory, loads and verified contact allow it. Otherwise use the longest verified continuous part and report the omitted head or tail. Use 30 ms of verified preceding input for initialization when available. Use verified trailing prediction context when available and report frames lost to OpenSim initialization or prediction. Preserve each attempt's actual output, including frames outside the fixed CMC candidate-ranking grid.

## Run and review

Run `scripts/run_single_leg_batch.py <config.json> --check-only` before a normal run. Select the installed Gait2392 example templates and the verified OpenSim 4.5 CLI and Python API. Run into a new output folder. Set `outer_search` to `true` for baseline-first Fast and Slow search. Screen each mode's 10 ms baseline before running its bounded candidate library. Keep muscle properties, inputs and physical reserve torque limits fixed across attempts. Read [Fast/Slow search and RMS/MAX](references/outer-search.md) for candidate settings, acceptance and aggregation.

Verify that native integration reached the expected end, the prescribed pelvis was followed and muscle/actuator outputs are finite. Keep every attempted configuration, raw output, per-route review and log, including nonqualifying results and partial files from failures. Calculate native-route metrics on all available CMC evaluation frames. SO outputs and failures do not affect CMC sampling. When requested, compare saved results at common valid timestamps in separate postprocessing. Report numerical failures separately from completed but nonqualifying cases, and give mean RMS and mean absolute MAX by coordinate and unit in chat.

Each Fast/Slow batch also writes `CMC_quality_dashboard.xlsx`. Link it alongside the numerical summaries. It shows both modes, the primary selection, and diagnostic or missing results. Check the spreadsheet runtime during preflight and confirm the workbook status after the batch.

The validated scope is target-leg gait estimation under prescribed pelvis motion. The model supplies no estimate of contralateral force or whole-body residual balance. A low target-joint reserve alone does not validate muscle forces. The bundle uses audited prepared single-leg inputs.
