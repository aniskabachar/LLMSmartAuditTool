# Audit report: MultiTokenNetwork.sol

- Audited 2026-11-26T11:50:54 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba_dasp
- LLM tokens: 2,219; analyser time 17.4s (wall 17.3s)

## Vulnerability categories found

- **denial_of_service**

## Findings by analyser

### llm_ba_dasp — denial_of_service (2,219 tokens, 17.4s)

- denial_of_service `function allWalletBalances(address wallet)` (medium): The function iterates over the entire _multitokens array without a gas limit, so if the array grows large the call can run out of gas and block the function, causing a denial of service.
