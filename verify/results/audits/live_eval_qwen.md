# Held-out live evaluation of the RL coordinator

- 96 real-bug contracts never seen in training (real_tools.json minus the 269 training contracts); audits run live (Slither locally, LLM agents on Groq).
- Fixed-analyser rows use replies collected earlier for the same contracts.
- Score = 0.6·recall + 0.4·precision (category level); 95% bootstrap CIs.

| Method | Score [95% CI] | Precision | Recall | LLM tokens / contract | Coordinator minus method |
|---|---|---|---|---|---|
| RL coordinator (live) | 0.626 [0.560, 0.690] | 0.363 | 0.802 | 1,812 |  |
| always slither | 0.567 [0.492, 0.637] | 0.339 | 0.719 | 0 | +0.059 [-0.020, +0.138], p=0.069 |
| always llm_ba | 0.653 [0.582, 0.720] | 0.430 | 0.802 | 1,190 | -0.027 [-0.079, +0.025], p=0.848 |
| always llm_ba_med | 0.653 [0.589, 0.712] | 0.382 | 0.833 | 1,790 | -0.026 [-0.077, +0.023], p=0.851 |
| always llm_ba_dasp | 0.630 [0.560, 0.695] | 0.386 | 0.792 | 1,474 | -0.003 [-0.062, +0.057], p=0.556 |
| always llm_qwen_ba | 0.576 [0.503, 0.648] | 0.347 | 0.729 | 1,170 | +0.050 [-0.025, +0.126], p=0.103 |
| always llm_qwen_think | 0.604 [0.536, 0.672] | 0.354 | 0.771 | 1,177 | +0.022 [-0.046, +0.089], p=0.267 |
| always llm_qwen_dasp | 0.679 [0.619, 0.735] | 0.415 | 0.854 | 1,483 | -0.052 [-0.111, +0.003], p=0.968 |
| all 4 analysers | 0.647 [0.598, 0.691] | 0.273 | 0.896 | 4,455 | -0.020 [-0.076, +0.030], p=0.771 |

Analysers the coordinator ran (% of contracts): llm_ba_med 72%, llm_ba_dasp 23%, llm_ba 9%, llm_qwen_dasp 2%
Calls that failed (counted as 'found nothing'): 0