---
name: opensim-vicon-cmc
description: Run and review prescribed-pelvis single-leg OpenSim 4.5 CMC from verified gait IK, a designated target-foot force plate and prepared original-knee CGM24 single-leg SO inputs. Screen Fast and Slow baselines, run bounded candidate searches, and report tracking and reserve RMS/MAX on common valid times.
---

# OpenSim single-leg CMC

Version **0.3.0**. Use the prepared prescribed-pelvis single-leg SO handoff. The matched IK prescribes all six pelvis coordinates. CMC controls the six active target-leg coordinates with the 46 retained muscle units and six leg reserves. Load only the force plate explicitly assigned to the target foot. Interpret the outputs conditional on the measured pelvis motion. Read [execution and review](references/execution.md) for the input schema and report contract.

## Confirm and preflight

- Identify the requested trial/stance/side, explicit plate, model and muscle-strength profile, filtering history, exclusions and new output directory. Reuse applicable prior choices. Check that the original model, full-context IK and processed external loads match the prepared SO handoff. Preserve the SO profile for a matched comparison unless a separate sensitivity run is requested.
- Use the audited 20 N target-foot event and one continuous interval with verified target-foot contact evidence. Confirm original frame numbers, source checksums, rotation, units, wrench representation and force timing. A plate crossing alone does not establish full target-foot loading. Wait for disposition of ambiguous trials. Do not bridge unknown gaps or invent forces.
- Use 30 ms of verified preceding input for initialization when available. Otherwise initialize inside the verified window and report the lost head. The baseline prediction window is 10 ms. End the supplied wrench at the last verified sample and report any last sample used for prediction rather than output. Keep phase percentages relative to the original 20 N event and use the baseline comparison grid across candidates.

## Run and review

Run `scripts/run_single_leg_batch.py <config.json> --check-only` before a normal run. Select the installed Gait2392 example templates and the verified OpenSim 4.5 CLI and Python API. Run into a new output folder. Set `outer_search` to `true` for baseline-first Fast and Slow search. Screen each mode's 10 ms baseline before running its bounded candidate library. Keep muscle properties, inputs and physical reserve torque limits fixed across attempts. Read [Fast/Slow search and RMS/MAX](references/outer-search.md) for candidate settings, acceptance and aggregation.

Verify that native integration reached the expected end, the prescribed pelvis was followed and muscle/actuator outputs are finite. Keep every attempted configuration, raw output, comparison and log, including nonqualifying results and partial files from failures. Compare muscle force and activation with single-leg SO only at common valid timestamps. Keep failed SO samples missing. Report numerical failures separately from completed but nonqualifying cases, and give mean RMS and mean absolute MAX by coordinate and unit in chat. Separate qualified and diagnostic selections and link the per-stance details.

Each Fast/Slow batch also writes `CMC_quality_dashboard.xlsx`. Link it alongside the numerical summaries. It shows both modes, the primary selection, and diagnostic or missing results. Check the spreadsheet runtime during preflight and confirm the workbook status after the batch.

The validated scope is target-leg gait estimation under prescribed pelvis motion. The model supplies no estimate of contralateral force or whole-body residual balance. A low target-joint reserve alone does not validate muscle forces. The bundle uses an audited SO handoff and does not prepare a trial directly from raw C3D.
