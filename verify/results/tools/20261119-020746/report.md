# Tool-selection RL on real static-analysis results

- Data: real outputs and run times of 8 tools on the 69 SmartBugs curated contracts (https://github.com/smartbugs/smartbugs-results (Apache-2.0), Durieux et al. ICSE 2020).
- PPO sees 4 compact contract features + tools run so far + categories they found.
- Utility = score − λ·minutes of tool run time; λ = 0.02 at the main setting.
- 5-fold stratified cross-validation × 3 repeats; every contract is scored by policies that never saw it. 95% bootstrap CIs over contracts.

## Main result (λ = 0.02)

| Method | Utility [95% CI] | Score [95% CI] | Precision | Recall | Minutes | Tools run |
|---|---|---|---|---|---|---|
| PPO | 0.479 [0.378, 0.578] | 0.498 [0.399, 0.593] | 0.405 | 0.560 | 0.97 | 2.29 |
| PPO-static | 0.497 [0.394, 0.593] | 0.515 [0.414, 0.612] | 0.375 | 0.609 | 0.93 | 2.33 |
| best-fixed | 0.513 [0.408, 0.609] | 0.549 [0.452, 0.640] | 0.373 | 0.667 | 1.80 | 3.66 |
| greedy-cheapest | 0.513 [0.407, 0.610] | 0.568 [0.474, 0.658] | 0.377 | 0.696 | 2.74 | 4.94 |
| all tools | 0.432 [0.312, 0.542] | 0.581 [0.489, 0.667] | 0.364 | 0.725 | 7.41 | 8.00 |
| random | 0.359 [0.263, 0.450] | 0.441 [0.359, 0.521] | 0.306 | 0.531 | 4.11 | 4.60 |
| honeybadger | 0.014 [-0.019, 0.060] | 0.029 [0.000, 0.072] | 0.029 | 0.029 | 0.77 | 1.00 |
| maian | 0.042 [-0.030, 0.124] | 0.101 [0.043, 0.174] | 0.101 | 0.101 | 2.95 | 1.00 |
| mythril | 0.417 [0.308, 0.525] | 0.441 [0.336, 0.545] | 0.343 | 0.507 | 1.21 | 1.00 |
| osiris | 0.174 [0.092, 0.264] | 0.188 [0.108, 0.278] | 0.145 | 0.217 | 0.73 | 1.00 |
| oyente | 0.249 [0.153, 0.348] | 0.261 [0.167, 0.359] | 0.217 | 0.290 | 0.60 | 1.00 |
| securify | 0.104 [0.037, 0.182] | 0.124 [0.058, 0.201] | 0.093 | 0.145 | 1.00 | 1.00 |
| slither | 0.239 [0.150, 0.333] | 0.240 [0.151, 0.334] | 0.187 | 0.275 | 0.05 | 1.00 |
| smartcheck | 0.186 [0.103, 0.276] | 0.187 [0.105, 0.278] | 0.164 | 0.203 | 0.10 | 1.00 |
| oracle | 0.665 [0.563, 0.762] | 0.672 [0.569, 0.769] | 0.594 | 0.725 | 0.34 | 0.72 |

## Paired bootstrap: PPO minus method

| Method | ΔUtility [95% CI] | p | ΔScore [95% CI] | p |
|---|---|---|---|---|
| PPO-static | -0.018 [-0.055, +0.018] | 0.822 | -0.017 [-0.055, +0.019] | 0.809 |
| best-fixed | -0.034 [-0.081, +0.011] | 0.931 | -0.051 [-0.098, -0.009] | 0.992 |
| greedy-cheapest | -0.034 [-0.088, +0.017] | 0.905 | -0.070 [-0.121, -0.024] | 0.999 |
| all tools | +0.047 [-0.019, +0.114] | 0.083 | -0.082 [-0.140, -0.030] | 1.000 |
| random | +0.120 [+0.051, +0.192] | 0.000 | +0.057 [-0.010, +0.126] | 0.048 |
| honeybadger | +0.465 [+0.360, +0.568] | 0.000 | +0.469 [+0.365, +0.571] | 0.000 |
| maian | +0.437 [+0.321, +0.548] | 0.000 | +0.397 [+0.282, +0.508] | 0.000 |
| mythril | +0.062 [-0.007, +0.135] | 0.038 | +0.057 [-0.012, +0.128] | 0.052 |
| osiris | +0.305 [+0.204, +0.405] | 0.000 | +0.310 [+0.211, +0.408] | 0.000 |
| oyente | +0.230 [+0.135, +0.326] | 0.000 | +0.237 [+0.142, +0.333] | 0.000 |
| securify | +0.375 [+0.266, +0.484] | 0.000 | +0.374 [+0.268, +0.480] | 0.000 |
| slither | +0.240 [+0.135, +0.347] | 0.000 | +0.258 [+0.155, +0.364] | 0.000 |
| smartcheck | +0.294 [+0.180, +0.408] | 0.000 | +0.311 [+0.199, +0.423] | 0.000 |
| oracle | -0.186 [-0.248, -0.130] | 1.000 | -0.174 [-0.237, -0.116] | 1.000 |

p = one-sided bootstrap probability that PPO is not better (small = PPO better).

## Tools PPO runs (% of contracts)

| honeybadger | maian | mythril | osiris | oyente | securify | slither | smartcheck |
|---|---|---|---|---|---|---|---|
| 12.1 | 10.1 | 66.7 | 6.3 | 20.3 | 2.9 | 73.9 | 36.7 |

## λ sweep

| λ | PPO utility | PPO score | PPO minutes | best-fixed utility | best-fixed score | best-fixed minutes | oracle utility |
|---|---|---|---|---|---|---|---|
| 0 | 0.540 | 0.540 | 2.80 | 0.568 | 0.568 | 4.79 | 0.673 |
| 0.01 | 0.491 | 0.509 | 1.87 | 0.530 | 0.548 | 1.83 | 0.669 |
| 0.02 | 0.479 | 0.498 | 0.97 | 0.513 | 0.549 | 1.80 | 0.665 |
| 0.05 | 0.514 | 0.546 | 0.65 | 0.490 | 0.558 | 1.36 | 0.655 |
