# Real evaluation harness

Empirical evaluation on labelled SmartBugs contracts (`tasks/easy|medium|hard`),
replacing the simulation-based accuracy in `test_system_simple.py`. Every number
comes from real LLM output graded against ground truth, on a held-out test split.

## Setup

```bash
pip install -r verify/requirements.txt          # grader, evaluators, API
pip install -r verify/requirements-rl.txt       # only for --evaluator rl and training
```

Put `GROQ_API_KEY=...` in `.env`. Nothing below calls the API except
`run_real_evaluation.py` and `GET /evaluate/real`.

## Data

- `tasks/<tier>/<contract>.sol` + `.json`: each contract has one **curated** label
  (`vulnerabilities`). Tool-reported types are kept in `tool_findings` and only
  scored with `--labels all`. See `label_changes.md`.
- `splits/<tier>_split.json`: fixed 80/20 train/test split (seed 42). Test split:
  12 easy, 19 medium, 9 hard contracts.

## Evaluate

```bash
py verify/run_real_evaluation.py --evaluator basic     # single-shot JSON prompt
py verify/run_real_evaluation.py --evaluator cot       # chain-of-thought prompt
py verify/run_real_evaluation.py --evaluator ba        # Broad Analysis, n=3 consensus rounds
py verify/run_real_evaluation.py --evaluator ta        # Targeted Analysis (41 TA detector scenarios)
py verify/run_real_evaluation.py --evaluator hybrid    # BA + TA
py verify/run_real_evaluation.py --evaluator rl        # trained PPO mode selector + DQN stopping
py verify/compare_results.py --md verify/results/comparison.md
```

Options: `--tier easy`, `--n 5` (seeded subsample), `--labels all`, `--out FILE`.
Prompts are identical across tiers (tiers derive from labels, so tier hints would
leak them). The audit modes record measured token usage per contract, so cost and
efficiency (score per 1k tokens) are real.

`--evaluator rl` needs trained policies at `models/mode_selector/mode_selector.zip`
and `models/stopping_policy/stopping_policy.zip` (or `RL_MODE_MODEL` /
`RL_STOP_MODEL`). Train with `py training/train_policies.py` (CPU, ~25 min, no API key;
settings and model hashes go to `models/training_info.json`). RL mode never falls back
to a heuristic.

## Reproduce

Each results file stores the evaluator config (model, prompt hash, seeds, git
commit, policy hashes), every contract's findings and raw LLM output.

```bash
py verify/regrade.py verify/results/real_evaluation_ba.json                 # recompute + check
py verify/regrade.py verify/results/real_evaluation_ba.json --labels all --out verify/results/real_evaluation_ba_all.json
py verify/random_baseline_real.py                                            # random floor
```

`regrade.py` fails if any number differs or a graded contract is outside the test
split. Older files without stored findings (`real_evaluation_final.json`,
`improved_evaluation.json`) cannot be re-graded and should not be cited.

## Environment and API

`auditor.AuditEnvironment` serves one contract per episode (Gymnasium-style
`reset` / `step` / `state`); reward is the grader score.

```bash
py api/server.py
```

- `POST /reset` `{"difficulty": "hard", "split": "test", "seed": 1}` → contract source
- `POST /step` `{"vulnerabilities": [{"type": "reentrancy"}]}` → reward, grading details
- `GET /state`
- `GET /evaluate/random/{tier}`, `GET /evaluate/real/{tier}` (`?n=&seed=&labels=`)

## Files

| File | Purpose |
|---|---|
| `run_real_evaluation.py` | Runs random baseline + chosen evaluator on the test split, writes a results file |
| `real_evaluator.py`, `improved_evaluator.py` | `basic` and `cot` single-pass evaluators |
| `mode_evaluator.py` | `ba` / `ta` / `hybrid` / `rl` audit modes with consensus rounds and token accounting |
| `random_baseline_real.py` | Seeded random agent (score floor) |
| `regrade.py` | Recomputes a results file from stored findings, no API calls |
| `compare_results.py` | Table across results files |
| `eval_common.py` | Split loading, seeded sampling, per-contract records, metadata |
| `separate_tool_labels.py`, `build_ground_truth_from_existing.py` | Label migration and builder |
| `split_dataset.py` | Creates the train/test splits |
