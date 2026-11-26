# Audit report: multi_delegatecall.sol

- Audited 2026-11-26T14:14:04 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba_med
- LLM tokens: 1,808; analyser time 4.4s (wall 5.1s)

## Vulnerability categories found

- **access_control**

## Findings by analyser

### llm_ba_med — access_control (1,808 tokens, 4.4s)

- access_control `function multiDelegatecall` (high): multiDelegatecall allows any caller to supply arbitrary calldata that is delegatecalled on the contract itself. This gives the caller unrestricted ability to execute any function of the contract (including state‑changing functions) in the context of the contract, effectively bypassing any intended access restrictions.
