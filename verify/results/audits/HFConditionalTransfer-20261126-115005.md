# Audit report: HFConditionalTransfer.sol

- Audited 2026-11-26T11:50:05 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba_med
- LLM tokens: 1,334; analyser time 7.4s (wall 7.4s)

## Vulnerability categories found

- **front_running**
- **unchecked_low_level_calls**

## Findings by analyser

### llm_ba_med — front_running, unchecked_low_level_calls (1,334 tokens, 7.4s)

- unchecked_low_level_calls `transferIfHF / transferIfNoHF` (medium): The functions use .send to forward msg.value but ignore the returned boolean. If the send fails (e.g., because the recipient is a contract with a fallback that uses more than 2300 gas), the ether remains locked in the contract, leading to loss of funds.
- front_running `transferIfHF / transferIfNoHF balance check` (medium): The decision of where to send funds depends on the balance of a hard‑coded external address. An attacker can manipulate that address's balance (by sending ether to it) just before the transaction is mined, changing the outcome of the conditional transfer and potentially diverting funds.
