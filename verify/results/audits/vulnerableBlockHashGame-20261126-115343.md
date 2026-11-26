# Audit report: vulnerableBlockHashGame.sol

- Audited 2026-11-26T11:53:43 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba_med
- LLM tokens: 1,421; analyser time 11.2s (wall 11.2s)

## Vulnerability categories found

- **bad_randomness**
- **other**

## Findings by analyser

### llm_ba_med — bad_randomness, other (1,421 tokens, 11.2s)

- bad_randomness `checkWinner() function` (high): The contract uses blockhash(blockNumber[msg.sender]) to decide the winner. blockhash only works for the last 256 blocks; after that it returns 0, making the outcome deterministic (always even) and allowing an attacker to wait >256 blocks to guarantee a win. Additionally, miners can influence the blockhash of recent blocks, making the randomness manipulable.
- other `checkWinner() function` (high): The contract transfers a fixed `balance` (2 ether) to the winner but never updates or reduces this stored balance after a payout. This allows the same amount to be withdrawn repeatedly by multiple winners until the contract's ether is drained.
