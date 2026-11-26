# Base TA mode vs PPO tool selector (same contracts)

- Contracts: 269 from `datasets\tool_results\combined_tools.json`; PPO run: `verify\results\tools\20261121-172401` (out-of-fold picks, λ = 0.02).
- TA mode: base project prompts (6 targeted scenario batches + consensus reviews), gpt-oss-120b, comments stripped and benchmark bug names neutralised.

| Method | Score [95% CI] | Precision | Recall | LLM tokens / contract | Minutes / contract |
|---|---|---|---|---|---|
| TA@1 (base TA mode, round 1) | 0.681 [0.659, 0.700] | 0.279 | 0.948 | 15,686 | 0.21 |
| TA@3 (base TA mode, n=3) | 0.698 [0.674, 0.721] | 0.340 | 0.937 | 21,528 | 0.31 |
| BA@1 (base BA mode, 1 call) | 0.726 [0.688, 0.763] | 0.527 | 0.859 | 2,021 | 0.04 |
| PPO tool selector (this project) | 0.787 [0.759, 0.814] | 0.580 | 0.926 | 1,927 | 0.08 |

## PPO minus each base mode (paired bootstrap over contracts)

| Base mode | ΔScore [95% CI] | p (PPO not better) | ΔScore smartbugs | ΔScore solidifi |
|---|---|---|---|---|
| TA@1 (base TA mode, round 1) | +0.107 [+0.078, +0.136] | 0.000 | +0.017 [-0.068, +0.101] | +0.138 [+0.115, +0.162] |
| TA@3 (base TA mode, n=3) | +0.089 [+0.061, +0.116] | 0.000 | +0.043 [-0.043, +0.127] | +0.105 [+0.084, +0.126] |
| BA@1 (base BA mode, 1 call) | +0.062 [+0.028, +0.096] | 0.000 | +0.027 [-0.050, +0.107] | +0.074 [+0.039, +0.111] |

## Score by source

| Method | smartbugs | solidifi |
|---|---|---|
| TA@1 (base TA mode, round 1) | 0.675 | 0.683 |
| TA@3 (base TA mode, n=3) | 0.648 | 0.715 |
| BA@1 (base BA mode, 1 call) | 0.665 | 0.747 |
| PPO tool selector (this project) | 0.692 | 0.821 |
