# Prepared single-leg CMC execution

Use OpenSim 4.5 CLI and Python API, NumPy and pandas. Point `gait2392_templates` to the installed `Resources/Models/Gait2392_Simbody` folder. The script uses the installed example settings, tasks and control constraints, restricted to the active target leg. Use `single_leg_prepared.json`, `input_audit.json`, and the referenced prescribed model, motion and target load files. The SO adapter's `--prepare-only` can build the model and inputs without running SO. Supply the contact provenance audit separately. The legacy `so_folder` config key aliases `prepared_folder`.

Copy [the example configuration](../assets/prepared-batch.example.json). Fill `kinematics_source` after the user selects IK or RRA. Legacy unlabeled handoffs apply only to explicitly selected IK. Read [RRA handoff preparation](rra-input.md) for RRA. Supply one entry per L/R stance with a prepared folder, audited event JSON and complete calibrated wrench file. The audit needs `phases`, original 20 N bounds, side, plate, original frame/time basis, and target-foot `contact_witnesses.complete_intervals`. Bind the contact audit and calibrated wrench to the prepared inputs by SHA-256. Supply optional `id_reference` for reserve ratios. Incomplete ID coverage leaves those ratios unavailable and does not shorten CMC. Use `--check-only` to verify input readiness, then run into a new output directory.

```text
python scripts/run_single_leg_batch.py batch.json --check-only
python scripts/run_single_leg_batch.py batch.json
python scripts/summarize_batch.py /path/to/new_cmc_results
```

Preserve `preflight.json`, `batch_status.json`, and each case's audit, model, logs and native `results/`. `cmc_curves.csv` preserves original frames, phase and CMC force/activation on all available evaluation frames. `muscle_summary.csv`, `tracking_errors.csv`, `reserve_comparison.csv` and `comparison.json` report independent CMC metrics. Report input, native and evaluated ranges separately. Missing reserve ratios are an evaluation limitation. Compare saved results only when requested and save that comparison separately.

The commands above describe the original single-pass layout. With `outer_search: true`, the runner writes the per-attempt and batch RMS/MAX summaries itself. See [the search reference](outer-search.md) for its layout and quality screens.

Apply the study's confirmed exclusions when constructing the manifest. For an explicit case-insensitive prefix exclusion, set `exclude_trial_prefixes`, for example `["run10"]`.

[OpenSim CMC configuration and outputs](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53089712/Getting%2BStarted%2Bwith%2BCMC) · [Muscle initialization and prediction](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53089706/How%2BCMC%2BWorks) · [OpenSim best practices](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53087686/Simulation%2Bwith%2BOpenSim%2B-%2BBest%2BPractices.)
