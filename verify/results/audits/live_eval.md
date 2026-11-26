# Held-out live evaluation of the RL coordinator

- 96 real-bug contracts never seen in training (real_tools.json minus the 269 training contracts); audits run live (Slither locally, LLM agents on Groq).
- Fixed-analyser rows use replies collected earlier for the same contracts.
- Score = 0.6·recall + 0.4·precision (category level); 95% bootstrap CIs.

| Method | Score [95% CI] | Precision | Recall | LLM tokens / contract | Coordinator minus method |
|---|---|---|---|---|---|
| RL coordinator (live) | 0.625 [0.556, 0.692] | 0.376 | 0.792 | 1,719 |  |
| always slither | 0.567 [0.492, 0.637] | 0.339 | 0.719 | 0 | +0.058 [-0.013, +0.131], p=0.052 |
| always llm_ba | 0.653 [0.582, 0.720] | 0.430 | 0.802 | 1,190 | -0.028 [-0.075, +0.019], p=0.878 |
| always llm_ba_med | 0.653 [0.589, 0.712] | 0.382 | 0.833 | 1,790 | -0.027 [-0.089, +0.035], p=0.802 |
| always llm_ba_dasp | 0.630 [0.560, 0.695] | 0.386 | 0.792 | 1,474 | -0.004 [-0.055, +0.047], p=0.565 |
| all 4 analysers | 0.647 [0.598, 0.691] | 0.273 | 0.896 | 4,455 | -0.021 [-0.075, +0.030], p=0.783 |

Analysers the coordinator ran (% of contracts): llm_ba_med 68%, llm_ba_dasp 23%, llm_ba 8%, spec_0 1%
Calls that failed (counted as 'found nothing'): 0