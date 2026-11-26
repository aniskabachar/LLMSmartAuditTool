# Audit report: reentrancy_bonus.sol

- Audited 2026-11-26T14:14:15 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba_dasp
- LLM tokens: 1,117; analyser time 7.0s (wall 7.8s)

## Vulnerability categories found

- **integer_overflow**
- **reentrancy**

## Findings by analyser

### llm_ba_dasp — integer_overflow, reentrancy (1,117 tokens, 7.0s)

- reentrancy `getFirstWithdrawalBonus` (high): claimedBonus is set to true after the external call to withdrawReward. An attacker can re‑enter getFirstWithdrawalBonus via the call and pass the require(!claimedBonus) check again, receiving the bonus multiple times.
- integer_overflow `getFirstWithdrawalBonus (oncerewardsForA[recipient] += 100)` (medium): The contract uses Solidity ^0.4.0 which lacks built‑in overflow checks. Adding 100 to rewardsForA can overflow the uint and wrap around, allowing manipulation of the reward amount.
