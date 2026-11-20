# Offline RL on real audit outcomes (PPO mode selection + DQN stopping)

This package trains and evaluates the two RL components — the **PPO mode selector**
(BA / TA / HYBRID) and the **DQN stopping policy** (when to stop consensus rounds) — on
**real LLM audit outcomes** instead of the synthetic simulation used before
(`training/train_policies.py`, `test_system_simple.py`).

## Idea

An audit in mode *M* stopped after round *k* is a deterministic function of the LLM calls
made up to round *k*. So we call the LLM **once** for every mode and every round
(up to `MAX_ROUNDS = 4`) on every contract and cache the replies. Any policy can then be
replayed exactly — real findings, real token counts — with no further API calls:

* RL training uses the **train pool** cache (tasks/ train split + `datasets/real/`).
* All methods are compared on the **held-out test split** cache (the same 40 contracts as
  `verify/`), against fixed modes, simple and learned baselines, and a per-contract oracle.
* `verify/run_real_evaluation.py --evaluator rl2` runs the trained policies **live** on the
  test split to confirm the replayed numbers.

Replay correctness is tested: feeding the cached replies into `verify/mode_evaluator.audit`
and into the live `rl2` loop reproduces the replayed findings, tokens and call counts exactly.

Utility of a stopped audit: **U = score − λ · tokens / 1000** (score = 0.6·recall + 0.4·precision
from `auditor/grader.py`). λ is the price of 1k tokens in score points; training one policy per λ
traces a cost–accuracy frontier.

| Component | Environment | Observation | Action | Reward |
|---|---|---|---|---|
| DQN stopping | `StopEnv` | 28 contract features, mode, round, tokens so far, #findings, confidence, **convergence** (Jaccard of finding types vs previous round, added, removed, Δconfidence) | continue / stop (stopping after round 1 allowed) | Δscore − λ·Δtokens/1000 per extra round |
| PPO mode selection | `ModeEnv` | 28 contract features | BA / TA / HYBRID | U of that mode, stopped by the trained DQN |

## Data (`scripts/fetch_real_datasets.py`)

Training-only pool in `datasets/real/` — **285 extra real contracts**:

* **SolidiFI benchmark** (Ghaleb & Pattabiraman, ISSTA 2020): real ERC-20 contracts with bugs
  injected at known lines — reentrancy, overflow, tx.origin, TOD, timestamp, unchecked send,
  unhandled exceptions. 281 kept.
* **SmartBugs Curated** contracts not already in `tasks/`. 4 kept (the rest were duplicates).

Leakage guard: a candidate is dropped if it is an exact copy of any `tasks/` contract, ≥ 0.5
token-shingle Jaccard similar to any **test** contract, or ≥ 0.9 similar to a same-label train
contract. Contracts whose labelled bug lines would be cut by the 6,000-character truncation are
dropped too. Everything dropped, and why, is listed in `datasets/real/manifest.json`.
The test split is unchanged. Note: the extra data is easy/medium tier only (no hard-tier classes
in SolidiFI).

## Commands (Windows, from the project folder)

```bash
pip install -r verify/requirements.txt -r verify/requirements-rl.txt

# 1. Collect the cache (needs GROQ_API_KEY in .env; resumable — rerun the same command)
py rl_offline/collect.py --status                      # progress + token/$ estimate
py rl_offline/collect.py --part test                   # the 40 test contracts first
py rl_offline/collect.py --part train --limit 200      # then (part of) the train pool
#    set --tpm/--rpm to your Groq limits (console.groq.com -> Settings -> Limits)

# 2. Train (CPU, about 25 min for the default 6 λ × 5 seeds + ablations)
py rl_offline/train.py

# 3. Evaluate on the test split (writes verify/results/offline/<time>_<tag>/)
py rl_offline/evaluate.py --tag <tag printed by train.py>

# 4. Optional live confirmation on fresh API calls
set RL2_POLICY_DIR=models\offline\<tag>\lam_0.005\seed_0
py verify/run_real_evaluation.py --evaluator rl2
```

Pipeline test without an API key (MOCK data — never report these numbers):

```bash
py rl_offline/collect.py --backend mock --cache-root datasets/cache_mock --limit 250
py rl_offline/train.py --cache-root datasets/cache_mock --allow-mock --tag mock --lams 0.005 --seeds 0
py rl_offline/evaluate.py --cache-root datasets/cache_mock --tag mock
```

## Budget

One contract = 1 BA call + 6 TA calls + 3 review rounds × 3 modes ≈ **16 calls, ~30–40k tokens**
(measured per contract by `--status` once collection starts). All 477 contracts ≈ 15–18M tokens
≈ **$4** at $0.15 / $0.75 per M tokens. The binding constraint is the account's **rate limits**:
at Groq's base limits for `openai/gpt-oss-120b` (8K tokens/min, 200K tokens/day) only ~5
contracts fit in a day. The collector waits for the per-minute limit, stops cleanly at the
daily limit, and resumes from the cache. A higher-limit Groq plan makes the full collection
a matter of hours.

## Outputs of `evaluate.py`

* `report.md` — headline (vs best fixed config chosen **on train**, vs HYBRID@3, oracle-gap
  closed), tables with 95% bootstrap CIs, paired bootstrap tests of PPO+DQN against every
  method, DQN-vs-fixed-rounds per mode, λ sweep, ablations.
* `pareto.png` — score vs tokens: fixed modes at n = 1..4, PPO+DQN for each λ, oracle.
* `results.json` — everything above plus per-contract outcomes for every method.

Ablations: PPO@3 (no DQN), random mode + DQN (no PPO), PPO+DQN without convergence features,
PPO+DQN without the 6 v2 security features. Learned baselines that answer "why RL?":
logistic-regression classifier + DQN, LinUCB contextual bandit + DQN.

## Reporting honestly

* Cite only numbers from `verify/results/offline/...` produced from a `groq` cache
  (`MOCK_DATA: false` in `results.json`), or from live `--evaluator rl2` runs.
* The test split has 40 contracts: report the 95% CIs and the paired-test p-values, not just means.
* Mode selection is a one-step decision (a contextual bandit). Compare PPO with the classifier
  and LinUCB baselines, and say so if they match it; the DQN stopping policy is the genuinely
  sequential part.
* The older simulated figures (84.8 % accuracy, 40.4 % cost reduction, the ablation table in
  `ablation/results/`) come from `test_system_simple.py`/simulation and should be replaced by these.
