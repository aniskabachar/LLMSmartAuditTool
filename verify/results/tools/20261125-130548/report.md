# Tool-selection RL on real static-analysis results

- Data: `datasets\tool_results\real_tools.json` — real outputs of 4 tools on 165 labelled contracts (paper_labeled: 78, smartbugs_curated: 87); sources: SmartBugs curated contracts in tasks/ (labels); Slither run locally; LLM via Groq.
- PPO sees 4 compact contract features + tools run so far + categories they found.
- Utility = score − λ·cost, cost = min per contract (`--cost minutes`); λ = 0.02 at the main setting.
- 5-fold stratified cross-validation × 3 repeats; every contract is scored by policies that never saw it. 95% bootstrap CIs over contracts.

## Main result (λ = 0.02)

| Method | Utility [95% CI] | Score [95% CI] | Precision | Recall | Cost (min) | LLM tokens | Tools run |
|---|---|---|---|---|---|---|---|
| PPO | 0.675 [0.626, 0.719] | 0.676 [0.628, 0.721] | 0.454 | 0.824 | 0.09 | 1,619 | 1.46 |
| PPO-static | 0.667 [0.622, 0.710] | 0.670 [0.624, 0.712] | 0.420 | 0.836 | 0.11 | 2,654 | 1.95 |
| best-fixed | 0.695 [0.653, 0.734] | 0.698 [0.656, 0.737] | 0.411 | 0.889 | 0.15 | 3,978 | 2.93 |
| greedy-cheapest | 0.695 [0.653, 0.734] | 0.698 [0.656, 0.737] | 0.411 | 0.889 | 0.15 | 3,978 | 2.93 |
| all tools | 0.680 [0.641, 0.717] | 0.686 [0.646, 0.722] | 0.359 | 0.903 | 0.27 | 4,075 | 4.00 |
| random | 0.663 [0.623, 0.701] | 0.667 [0.626, 0.704] | 0.397 | 0.846 | 0.16 | 2,494 | 2.41 |
| slither | 0.479 [0.417, 0.538] | 0.481 [0.420, 0.540] | 0.294 | 0.606 | 0.11 | 0 | 1.00 |
| llm_ba | 0.661 [0.603, 0.715] | 0.663 [0.604, 0.716] | 0.484 | 0.782 | 0.06 | 1,095 | 1.00 |
| llm_ba_med | 0.669 [0.615, 0.719] | 0.670 [0.617, 0.720] | 0.458 | 0.812 | 0.06 | 1,608 | 1.00 |
| llm_ba_dasp | 0.646 [0.588, 0.701] | 0.647 [0.589, 0.701] | 0.453 | 0.776 | 0.04 | 1,373 | 1.00 |
| oracle | 0.787 [0.742, 0.829] | 0.788 [0.743, 0.829] | 0.616 | 0.903 | 0.05 | 933 | 0.90 |

## By source (λ = 0.02): score [95% CI], cost (min)

| Method | paper_labeled (n=78) | smartbugs_curated (n=87) |
|---|---|---|
| PPO | 0.628 [0.549, 0.705], 0.07 | 0.719 [0.666, 0.770], 0.10 |
| PPO-static | 0.631 [0.553, 0.707], 0.08 | 0.705 [0.656, 0.750], 0.14 |
| best-fixed | 0.668 [0.597, 0.734], 0.11 | 0.725 [0.679, 0.768], 0.20 |
| greedy-cheapest | 0.668 [0.597, 0.734], 0.11 | 0.725 [0.679, 0.768], 0.20 |
| all tools | 0.655 [0.587, 0.719], 0.22 | 0.713 [0.673, 0.751], 0.32 |
| random | 0.642 [0.571, 0.708], 0.13 | 0.689 [0.645, 0.730], 0.18 |
| slither | 0.367 [0.277, 0.460], 0.11 | 0.584 [0.510, 0.656], 0.12 |
| llm_ba | 0.643 [0.552, 0.731], 0.03 | 0.680 [0.611, 0.747], 0.08 |
| llm_ba_med | 0.631 [0.545, 0.716], 0.05 | 0.705 [0.643, 0.764], 0.07 |
| llm_ba_dasp | 0.604 [0.512, 0.693], 0.03 | 0.685 [0.616, 0.749], 0.05 |
| oracle | 0.752 [0.674, 0.825], 0.03 | 0.821 [0.774, 0.863], 0.06 |

PPO minus best-fixed (score) per source: paper_labeled -0.039 [-0.079, -0.005]; smartbugs_curated -0.006 [-0.042, +0.026]

## Paired bootstrap: PPO minus method

| Method | ΔUtility [95% CI] | p | ΔScore [95% CI] | p |
|---|---|---|---|---|
| PPO-static | +0.007 [-0.016, +0.028] | 0.248 | +0.007 [-0.016, +0.027] | 0.260 |
| best-fixed | -0.020 [-0.047, +0.004] | 0.946 | -0.022 [-0.048, +0.003] | 0.958 |
| greedy-cheapest | -0.020 [-0.047, +0.004] | 0.946 | -0.022 [-0.048, +0.003] | 0.958 |
| all tools | -0.006 [-0.033, +0.020] | 0.663 | -0.009 [-0.037, +0.016] | 0.759 |
| random | +0.011 [-0.014, +0.035] | 0.190 | +0.010 [-0.015, +0.034] | 0.220 |
| slither | +0.195 [+0.134, +0.261] | 0.000 | +0.195 [+0.134, +0.260] | 0.000 |
| llm_ba | +0.013 [-0.015, +0.043] | 0.191 | +0.014 [-0.014, +0.044] | 0.180 |
| llm_ba_med | +0.005 [-0.028, +0.040] | 0.375 | +0.006 [-0.027, +0.040] | 0.363 |
| llm_ba_dasp | +0.029 [-0.011, +0.069] | 0.074 | +0.030 [-0.010, +0.070] | 0.068 |
| oracle | -0.113 [-0.141, -0.087] | 1.000 | -0.112 [-0.141, -0.086] | 1.000 |

p = one-sided bootstrap probability that PPO is not better (small = PPO better).

## Tools PPO runs (% of contracts)

| slither | llm_ba | llm_ba_med | llm_ba_dasp |
|---|---|---|---|
| 19.4 | 69.3 | 27.3 | 30.3 |

## λ sweep

| λ | PPO utility | PPO score | PPO cost | best-fixed utility | best-fixed score | best-fixed cost | oracle utility |
|---|---|---|---|---|---|---|---|
| 0 | 0.689 | 0.689 | 0.09 | 0.704 | 0.704 | 0.16 | 0.788 |
| 0.01 | 0.691 | 0.691 | 0.07 | 0.702 | 0.704 | 0.16 | 0.788 |
| 0.02 | 0.675 | 0.676 | 0.09 | 0.695 | 0.698 | 0.15 | 0.787 |
| 0.05 | 0.677 | 0.681 | 0.09 | 0.696 | 0.704 | 0.16 | 0.786 |
