# Audit report: multi_delegatecall.sol

- Audited 2026-11-26T11:52:27 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba_med
- LLM tokens: 1,749; analyser time 8.0s (wall 8.0s)

## Vulnerability categories found

- **access_control**
- **denial_of_service**

## Findings by analyser

### llm_ba_med — access_control, denial_of_service (1,749 tokens, 8.0s)

- access_control `function multiDelegatecall` (medium): multiDelegatecall is public and allows any caller to execute arbitrary functions of the contract via delegatecall, including functions that may be privileged or modify critical state, without any access restrictions.
- denial_of_service `function multiDelegatecall (for loop over data array)` (low): The function iterates over an unbounded array supplied by the caller; a malicious caller can provide a very large array causing the transaction to run out of gas and revert, effectively DoS-ing the function.
