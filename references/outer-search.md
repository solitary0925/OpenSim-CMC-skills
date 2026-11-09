# Baseline-first Fast and Slow search

Set `outer_search: true` and check inputs with `run_single_leg_batch.py --check-only`. Use one manifest row per audited target-leg stance. The default `muscle_strength_profile` is `prepared_so`. To repeat the S02 sensitivity profile, set `rajagopal2016_fmax` and provide `rajagopal_model` pointing to the official Rajagopal2016.osim. The runner uses the existing SO strength helper from the sibling `opensim-vicon-so` skill or the `so_strength_helper` config path. The transfer changes only matched maximum isometric forces in the CMC working copy. Report the SO and CMC strength difference where applicable.

Both modes use the Gait2392 example 10 ms baseline. After a mode qualifies, retain that baseline and skip its candidates. Otherwise execute the bounded library below. Treat these settings as starting points tested in S02, and retain the settings for each run.

| Mode | Candidate 1 | Candidate 2 |
|---|---|---|
| Fast | 5 ms prediction and target ankle Kp/Kv 144/24 | 2.5 ms prediction and original gains |
| Slow | 6.25 ms prediction, equal distal tracking weights, three distal reserve penalties ×16 | 2.5 ms prediction with the same penalties and weights |

For the reserve cost change, divide the ankle, knee and subtalar `optimal_force` by four and multiply both actuator and XML control bounds by four. This changes the cost while holding physical limits fixed. Use the same model, contact window, calibrated loads, IK reference, physical reserve limits and baseline comparison grid across both modes. Retain the native integration window for every candidate.

## Acceptance

- Verify native completion, finite outputs, pelvis prescription and common samples. The absolute study screen is ankle and subtalar RMS ≤1°, other active coordinates ≤2°, and absolute peak reserve below 10% of the same-window absolute peak ID moment at each evaluable coordinate. A peak ID moment ≤1 N·m makes its ratio unavailable. All three distal ratios must be available to qualify.
- A candidate replacing a completed baseline must pass the absolute screen, strictly lower ankle RMS and avoid an increase in all three distal reserve peaks. A candidate meeting the absolute screen after a baseline solver failure is a qualified recovery and has no relative-improvement claim.
- Among eligible candidates, minimize the worst ratio of ankle RMS to 1° and the three distal reserve/ID percentages to 10%. Break ties by lower ankle RMS. When no candidate qualifies, rank joint improvements first and then the same worst ratio. A completed fallback is diagnostic only. Prefer a qualifying baseline over a candidate when choosing one primary result per stance, with Fast first among qualifying baselines. Keep the mode-specific selections as well.

## Reporting

Keep all attempted input XML, models, native outputs, logs and per-case status, including incomplete outputs when the solver fails. `attempt_index.csv` inventories them. `per_stance_RMS_MAX.csv` records all six tracking coordinates in degrees and all six reserves in N·m on the fixed common valid SO/IK grid. RMS is the root mean square, and MAX is the largest absolute sample. `batch_RMS_MAX.csv` and its JSON counterpart give arithmetic means of the per-stance RMS and MAX and the overall absolute MAX, with equal weight per included result. Label the group and sample count. Use qualified and diagnostic primary selections separately as the main chat summary, with Fast and Slow baseline and selected groups available for comparison. Do not merge degrees with N·m or invent metrics for a failed solve. The prescribed pelvis checks are in `comparison.json`; whole-body residual force is outside the single-leg model's output.

### Excel quality dashboard

The batch automatically writes `CMC_quality_dashboard.xlsx` in the batch folder and records the export outcome as `quality_workbook` in `batch_status.json`. The preflight checks the bundled Node and `@oai/artifact-tool` spreadsheet runtime. If it is installed elsewhere, set `CMC_DASHBOARD_NODE` and `CMC_DASHBOARD_NODE_MODULES` to the workspace runtime executable and its `node_modules` directory. To export again from saved Fast/Slow results, run `python scripts/export_quality_dashboard.py <batch-folder>`. This step reads the saved selections without running OpenSim. If export fails, report it separately from the solver results and retry the export once the runtime or destination is available.

Fast and Slow each have one row per L/R stance. The trial label identifies the primary mode and the displayed mode's qualification status. Retain diagnostic, blocked and failed rows. Explicitly excluded stances are counted in the sheet header and tracked in `batch_status.json`.

The ten columns are MAX then RMS for residual force, residual moment, translational position error, rotational position error and reserve torque. For each rotational error or reserve column, use the largest corresponding per-coordinate statistic across the six active coordinates on the common grid. The single-leg prescribed-pelvis route reports N/A for residual force, residual moment and translational tracking. Missing numerical results also use gray N/A.

Color by the reference dashboard limits. Green is at or below the GOOD limit, yellow is above GOOD and at or below OKAY, and red is above OKAY. The workbook color is separate from the batch acceptance criteria above.

| Column pair | MAX GOOD / OKAY upper limits | RMS GOOD / OKAY upper limits |
|---|---:|---:|
| Residual force (N) | 10 / 25 | 10 / 25 |
| Residual moment (N·m) | 50 / 75 | 30 / 50 |
| Translational position error (cm) | 1 / 2 | 1 / 2 |
| Rotational position error (°) | 2 / 5 | 2 / 5 |
| Reserve torque (N·m) | 25 / 50 | 10 / 25 |

[OpenSim CMC best practices](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53087686/Simulation%2Bwith%2BOpenSim%2B-%2BBest%2BPractices.) · [OpenSim reserves and residuals](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53114407/_reserves%2Band%2Bresiduals)
