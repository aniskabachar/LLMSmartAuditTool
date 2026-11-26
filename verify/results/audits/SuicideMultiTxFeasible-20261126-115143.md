# Audit report: SuicideMultiTxFeasible.sol

- Audited 2026-11-26T11:51:43 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba_dasp
- LLM tokens: 880; analyser time 11.0s (wall 11.0s)

## Vulnerability categories found

- **access_control**

## Findings by analyser

### llm_ba_dasp — access_control (880 tokens, 11.0s)

- access_control `run(uint256 input)` (high): The run function is public and can be called by anyone after init() has been called, allowing any attacker to trigger selfdestruct and drain the contract's balance without any authorization check.
