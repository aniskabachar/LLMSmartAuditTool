# Audit report: ProofOfExistence.sol

- Audited 2026-11-26T14:12:50 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba
- LLM tokens: 2,327; analyser time 3.0s (wall 4.2s)

## Vulnerability categories found

- **access_control**
- **denial_of_service**
- **timestamp_dependence**

## Findings by analyser

### llm_ba — access_control, denial_of_service, timestamp_dependence (2,327 tokens, 3.0s)

- access_control `claimReward_TOD36 / claimReward_TOD40` (high): claimReward functions can be called by any address without proper authorization, allowing anyone to withdraw the reward if it has not been claimed yet.
- denial_of_service `getReward_TOD35, getReward_TOD33, getReward_TOD27, getReward_TOD31, getReward_TOD13` (medium): These functions transfer msg.value to a winner variable that is only set after a successful guess; if no guess was made, the winner remains the zero address, causing the transferred ether to be locked forever.
- timestamp_dependence `SetFileExistenceProof (uses now)` (low): The contract uses the block timestamp (now) to record the proof date, which can be manipulated by miners within a reasonable range.
