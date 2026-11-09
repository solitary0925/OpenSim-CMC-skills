# Prepared single-leg CMC execution

Use OpenSim 4.5 CLI and Python API, NumPy and pandas. Point `gait2392_templates` to the installed `Resources/Models/Gait2392_Simbody` folder. The script uses the installed example settings, tasks and control constraints, restricted to the active target leg. It uses the existing six leg reserves in the model. Prepared comparison needs the selected SO attempt, including `single_leg_prepared.json`, `input_audit.json`, `quality.json`, `validated_muscle_curves.csv`, original SO activation/force STO, and the referenced SO model, motion and target load files. For an SO initialization retry, provide the same-window single-leg ID reference explicitly, even if it is in the parent attempt.

Copy [the example configuration](../assets/prepared-batch.example.json). Resolve relative paths against the configuration file. Supply one entry per L/R stance with the prior SO folder, audited event JSON, complete calibrated wrench file and same-window single-leg ID STO. The audit needs `phases`, original 20 N bounds, plate and side, source frame/time basis, and target-foot `contact_witnesses.complete_intervals`. The SO handoff must bind the contact audit and calibrated wrench hashes to its source files. `--check-only` prints input readiness without starting CMC. Check-only does not initialize all copied models or prove numerical feasibility. The runner uses a new output directory and per-case work folders. Set worker and timeout limits for the local machine. Resolve reported blocks before running their affected cases.

```text
python scripts/run_single_leg_batch.py batch.json --check-only
python scripts/run_single_leg_batch.py batch.json
python scripts/summarize_batch.py /path/to/new_cmc_results
```

Preserve `preflight.json`, `batch_status.json`, and each case's `input_audit.json`, native console log, status, copied input model and `results/`. Native `*_controls.*` contains excitation, `*_states.sto` contains activation and fiber states, and `*_Actuation_force.sto` contains actuator forces. `same_window_curves.csv` preserves original frames and phase percentages for matched times, `muscle_comparison.csv` contains the 46 force and activation comparisons, and `tracking_errors.csv`, `reserve_comparison.csv` and `comparison.json` give numerical review. In batch reporting, use review flags for reserves ≥10% of the corresponding ID moment peak and explain an unusably small reference denominator. Use tracking screens above 1° RMS for ankle/subtalar and 2° for the other tracked coordinates. The latter thresholds are study screens. The activation screen is ≥99.99%. Report the flags and numerical completion separately. Save high-rate native data even if a sample is absent from the matched SO grid.

The commands above describe the original single-pass layout. With `outer_search: true`, the runner writes the per-attempt and batch RMS/MAX summaries itself. See [the search reference](outer-search.md) for its layout and quality screens.

Apply the study's confirmed exclusions when constructing the manifest. For an explicit case-insensitive prefix exclusion, set `exclude_trial_prefixes`, for example `["run10"]`.

[OpenSim CMC configuration and outputs](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53089712/Getting%2BStarted%2Bwith%2BCMC) · [Muscle initialization and prediction](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53089706/How%2BCMC%2BWorks) · [OpenSim best practices](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53087686/Simulation%2Bwith%2BOpenSim%2B-%2BBest%2BPractices.)
