# RL coordinator vs the base paper's modes, on the base paper's benchmark

- Contracts: evaluation/benchmark/Labeled/proc_* (102 vulnerable, 10 types; 10 safe) and evaluation/benchmark/cve (13). Built by rl_tools/build_paper_dataset.py.
- Coordinator: deployed setup (13 analysers, PPO, λ = 0.02), trained on 216 contracts: the 269 training contracts minus the 53 that overlap this benchmark. Seeds [0, 1, 2].
- Every method is scored on the same collected replies (identical requests). 'always llm_ba' is the base paper's BA mode (one broad call).
- Score = 0.6·recall + 0.4·precision (category level), 95% bootstrap CI; last column: coordinator (mean of seeds) minus method, paired bootstrap, one-sided p.

## Paper labelled dataset (102 vulnerable)

| Method | Score [95% CI] | Precision | Recall | LLM tokens / contract | Coordinator minus method |
|---|---|---|---|---|---|
| RL coordinator (mean of seeds) | 0.633 [0.563, 0.700] | 0.451 | 0.755 | 1,890 |  |
| RL coordinator s0 | 0.649 [0.578, 0.716] | 0.447 | 0.784 | 1,990 |  |
| RL coordinator s1 | 0.625 [0.550, 0.699] | 0.461 | 0.735 | 2,002 |  |
| RL coordinator s2 | 0.625 [0.551, 0.697] | 0.446 | 0.745 | 1,680 |  |
| base TA mode (n=3) | 0.591 [0.526, 0.656] | 0.332 | 0.765 | 15,086 | +0.042 [-0.004, +0.089], p=0.036 |
| base TA mode, round 1 | 0.588 [0.527, 0.647] | 0.280 | 0.794 | 11,553 | +0.045 [+0.001, +0.087], p=0.022 |
| always llm_ba | 0.650 [0.575, 0.723] | 0.492 | 0.755 | 1,251 | -0.016 [-0.061, +0.029], p=0.760 |
| always spec_0 | 0.539 [0.455, 0.621] | 0.406 | 0.627 | 2,387 | +0.094 [+0.032, +0.161], p=0.001 |
| always spec_1 | 0.456 [0.367, 0.543] | 0.374 | 0.510 | 2,262 | +0.178 [+0.104, +0.255], p=0.000 |
| always spec_2 | 0.241 [0.163, 0.324] | 0.235 | 0.245 | 1,877 | +0.392 [+0.300, +0.483], p=0.000 |
| always spec_3 | 0.337 [0.249, 0.427] | 0.314 | 0.353 | 1,802 | +0.296 [+0.207, +0.386], p=0.000 |
| always spec_4 | 0.384 [0.298, 0.470] | 0.312 | 0.431 | 1,980 | +0.250 [+0.178, +0.327], p=0.000 |
| always spec_5 | 0.045 [0.010, 0.086] | 0.039 | 0.049 | 1,245 | +0.588 [+0.512, +0.663], p=0.000 |
| always llm_ba_med | 0.629 [0.556, 0.700] | 0.439 | 0.755 | 1,846 | +0.005 [-0.034, +0.043], p=0.395 |
| always llm_ba_dasp | 0.619 [0.544, 0.693] | 0.446 | 0.735 | 1,542 | +0.014 [-0.027, +0.059], p=0.263 |
| always llm_qwen_ba | 0.576 [0.493, 0.655] | 0.425 | 0.676 | 1,255 | +0.057 [-0.011, +0.129], p=0.051 |
| always llm_qwen_think | 0.576 [0.493, 0.655] | 0.424 | 0.676 | 1,252 | +0.058 [-0.013, +0.130], p=0.056 |
| always llm_qwen_dasp | 0.634 [0.559, 0.706] | 0.453 | 0.755 | 1,591 | -0.001 [-0.060, +0.060], p=0.502 |
| always slither | 0.375 [0.294, 0.457] | 0.261 | 0.451 | 0 | +0.258 [+0.175, +0.343], p=0.000 |
| all 13 analysers | 0.622 [0.573, 0.666] | 0.232 | 0.882 | 20,290 | +0.011 [-0.038, +0.057], p=0.312 |

## Paper CVE dataset (13)

| Method | Score [95% CI] | Precision | Recall | LLM tokens / contract | Coordinator minus method |
|---|---|---|---|---|---|
| RL coordinator (mean of seeds) | 0.489 [0.289, 0.680] | 0.299 | 0.615 | 2,494 |  |
| RL coordinator s0 | 0.538 [0.349, 0.723] | 0.308 | 0.692 | 2,555 |  |
| RL coordinator s1 | 0.436 [0.215, 0.656] | 0.282 | 0.538 | 2,579 |  |
| RL coordinator s2 | 0.492 [0.287, 0.697] | 0.308 | 0.615 | 2,347 |  |
| base TA mode (n=3) | 0.588 [0.395, 0.754] | 0.317 | 0.769 | 19,109 | -0.099 [-0.318, +0.102], p=0.822 |
| base TA mode, round 1 | 0.616 [0.448, 0.737] | 0.271 | 0.846 | 14,525 | -0.127 [-0.358, +0.090], p=0.863 |
| always llm_ba | 0.518 [0.292, 0.738] | 0.372 | 0.615 | 1,756 | -0.029 [-0.096, +0.036], p=0.811 |
| always spec_0 | 0.277 [0.062, 0.508] | 0.231 | 0.308 | 2,863 | +0.212 [+0.032, +0.410], p=0.009 |
| always spec_1 | 0.277 [0.062, 0.508] | 0.231 | 0.308 | 2,702 | +0.212 [-0.188, +0.573], p=0.142 |
| always spec_2 | 0.431 [0.185, 0.677] | 0.385 | 0.462 | 2,452 | +0.058 [-0.243, +0.340], p=0.341 |
| always spec_3 | 0.349 [0.118, 0.600] | 0.295 | 0.385 | 2,297 | +0.140 [-0.183, +0.438], p=0.190 |
| always spec_4 | 0.492 [0.231, 0.738] | 0.423 | 0.538 | 2,540 | -0.003 [-0.410, +0.376], p=0.498 |
| always spec_5 | 0.000 [0.000, 0.000] | 0.000 | 0.000 | 1,671 | +0.489 [+0.289, +0.680], p=0.000 |
| always llm_ba_med | 0.538 [0.349, 0.723] | 0.308 | 0.692 | 2,602 | -0.050 [-0.150, +0.021], p=0.846 |
| always llm_ba_dasp | 0.567 [0.354, 0.764] | 0.378 | 0.692 | 2,113 | -0.078 [-0.207, +0.024], p=0.910 |
| always llm_qwen_ba | 0.675 [0.552, 0.750] | 0.304 | 0.923 | 2,395 | -0.186 [-0.411, +0.019], p=0.961 |
| always llm_qwen_think | 0.690 [0.556, 0.785] | 0.340 | 0.923 | 2,098 | -0.201 [-0.409, -0.009], p=0.980 |
| always llm_qwen_dasp | 0.549 [0.336, 0.744] | 0.333 | 0.692 | 2,460 | -0.060 [-0.268, +0.151], p=0.717 |
| always slither | 0.062 [0.000, 0.185] | 0.038 | 0.077 | 0 | +0.427 [+0.138, +0.667], p=0.002 |
| all 13 analysers | 0.690 [0.670, 0.714] | 0.224 | 1.000 | 27,948 | -0.201 [-0.407, -0.004], p=0.977 |

## Labelled + CVE (115)

| Method | Score [95% CI] | Precision | Recall | LLM tokens / contract | Coordinator minus method |
|---|---|---|---|---|---|
| RL coordinator (mean of seeds) | 0.617 [0.552, 0.683] | 0.434 | 0.739 | 1,959 |  |
| RL coordinator s0 | 0.637 [0.571, 0.702] | 0.431 | 0.774 | 2,054 |  |
| RL coordinator s1 | 0.604 [0.532, 0.676] | 0.440 | 0.713 | 2,067 |  |
| RL coordinator s2 | 0.610 [0.540, 0.680] | 0.430 | 0.730 | 1,755 |  |
| base TA mode (n=3) | 0.591 [0.528, 0.652] | 0.330 | 0.765 | 15,541 | +0.026 [-0.021, +0.075], p=0.135 |
| base TA mode, round 1 | 0.592 [0.535, 0.645] | 0.279 | 0.800 | 11,889 | +0.025 [-0.021, +0.072], p=0.141 |
| always llm_ba | 0.635 [0.563, 0.706] | 0.478 | 0.739 | 1,308 | -0.018 [-0.058, +0.024], p=0.804 |
| always spec_0 | 0.509 [0.430, 0.588] | 0.387 | 0.591 | 2,440 | +0.108 [+0.049, +0.173], p=0.000 |
| always spec_1 | 0.435 [0.353, 0.519] | 0.358 | 0.487 | 2,312 | +0.182 [+0.103, +0.261], p=0.000 |
| always spec_2 | 0.263 [0.184, 0.343] | 0.252 | 0.270 | 1,942 | +0.354 [+0.264, +0.443], p=0.000 |
| always spec_3 | 0.339 [0.256, 0.424] | 0.312 | 0.357 | 1,858 | +0.278 [+0.191, +0.364], p=0.000 |
| always spec_4 | 0.396 [0.315, 0.478] | 0.325 | 0.443 | 2,044 | +0.221 [+0.141, +0.301], p=0.000 |
| always spec_5 | 0.040 [0.009, 0.077] | 0.035 | 0.043 | 1,293 | +0.577 [+0.506, +0.648], p=0.000 |
| always llm_ba_med | 0.618 [0.551, 0.686] | 0.424 | 0.748 | 1,931 | -0.001 [-0.037, +0.034], p=0.523 |
| always llm_ba_dasp | 0.614 [0.541, 0.683] | 0.438 | 0.730 | 1,607 | +0.003 [-0.037, +0.046], p=0.439 |
| always llm_qwen_ba | 0.587 [0.515, 0.659] | 0.412 | 0.704 | 1,384 | +0.030 [-0.036, +0.098], p=0.194 |
| always llm_qwen_think | 0.589 [0.515, 0.660] | 0.415 | 0.704 | 1,348 | +0.028 [-0.040, +0.099], p=0.212 |
| always llm_qwen_dasp | 0.624 [0.554, 0.692] | 0.439 | 0.748 | 1,689 | -0.007 [-0.064, +0.052], p=0.593 |
| always slither | 0.340 [0.264, 0.418] | 0.236 | 0.409 | 0 | +0.277 [+0.197, +0.360], p=0.000 |
| all 13 analysers | 0.630 [0.587, 0.669] | 0.231 | 0.896 | 21,156 | -0.013 [-0.064, +0.036], p=0.682 |

## Detection rate per bug type (labelled dataset: share of contracts whose bug category was reported)

| Paper type | n | RL coordinator (mean of seeds) | base TA mode (n=3) | always llm_ba | always llm_qwen_dasp | all 13 analysers |
|---|---|---|---|---|---|---|
| TOD | 10 | 0% | 0% | 0% | 10% | 20% |
| arithmetic | 10 | 93% | 90% | 100% | 90% | 100% |
| bad_randomness | 10 | 93% | 80% | 90% | 90% | 100% |
| gasless_send | 11 | 48% | 55% | 27% | 64% | 82% |
| reentrancy | 10 | 93% | 100% | 100% | 100% | 100% |
| time_manipulation | 10 | 83% | 90% | 90% | 40% | 100% |
| tx_origin | 10 | 100% | 100% | 100% | 100% | 100% |
| unchecked_send | 10 | 100% | 100% | 100% | 100% | 100% |
| unsafe_delegatecall | 10 | 70% | 80% | 70% | 80% | 80% |
| unsafe_suicide | 11 | 76% | 73% | 82% | 82% | 100% |

## Safe contracts (10, no known bug): false alarms

| Method | Contracts flagged | Categories reported / contract | LLM tokens / contract |
|---|---|---|---|
| RL coordinator (mean of seeds) | 97% | 1.43 | 2,078 |
| RL coordinator s0 | 90% | 1.60 | 2,414 |
| RL coordinator s1 | 100% | 1.40 | 2,004 |
| RL coordinator s2 | 100% | 1.30 | 1,815 |
| base TA mode (n=3) | 100% | 2.40 | 14,574 |
| base TA mode, round 1 | 90% | 2.40 | 11,376 |
| always llm_ba | 80% | 0.90 | 1,191 |
| always spec_0 | 70% | 0.70 | 2,300 |
| always spec_1 | 50% | 0.70 | 2,200 |
| always spec_2 | 40% | 0.40 | 1,866 |
| always spec_3 | 30% | 0.40 | 1,779 |
| always spec_4 | 80% | 0.90 | 1,996 |
| always spec_5 | 30% | 0.30 | 1,235 |
| always llm_ba_med | 90% | 1.40 | 1,829 |
| always llm_ba_dasp | 70% | 0.90 | 1,450 |
| always llm_qwen_ba | 100% | 2.00 | 1,637 |
| always llm_qwen_think | 100% | 1.70 | 1,265 |
| always llm_qwen_dasp | 80% | 2.20 | 2,051 |
| always slither | 60% | 0.70 | 0 |
| all 13 analysers | 100% | 5.20 | 20,799 |

Analysers the coordinators ran (% of contracts, all seeds): llm_ba_med 51%, llm_ba_dasp 34%, llm_ba 12%, spec_0 11%, llm_qwen_think 3%, llm_qwen_dasp 2%, slither 1%