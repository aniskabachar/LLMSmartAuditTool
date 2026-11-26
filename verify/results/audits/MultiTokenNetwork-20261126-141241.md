# Audit report: MultiTokenNetwork.sol

- Audited 2026-11-26T14:12:41 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba_dasp, llm_ba_med
- LLM tokens: 5,112; analyser time 5.5s (wall 66.4s)

## Vulnerability categories found

- **unchecked_low_level_calls**

## Findings by analyser

### llm_ba_dasp — nothing (2,260 tokens, 2.3s)


### llm_ba_med — unchecked_low_level_calls (2,852 tokens, 3.2s)

- unchecked_low_level_calls `AbstractDeployer.deploy` (high): Uses address(this).call(data) without validating returned data size. The assembly reads 32 bytes of returndata regardless of length, which can lead to incorrect address being returned or allow re‑entrancy attacks if the called function modifies contract state before the call returns.
- unchecked_low_level_calls `MultiTokenNetwork.makeCall` (high): Allows the owner to execute an arbitrary external call with value using target.call.value(value)(data). The low‑level call is unchecked beyond the boolean success check, enabling re‑entrancy into MultiTokenNetwork (or other contracts) and potential denial‑of‑service or state manipulation.
