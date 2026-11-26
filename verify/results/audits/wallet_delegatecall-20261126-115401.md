# Audit report: wallet_delegatecall.sol

- Audited 2026-11-26T11:54:01 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba
- LLM tokens: 2,610; analyser time 18.0s (wall 18.0s)

## Vulnerability categories found

- **access_control**
- **other**
- **unchecked_low_level_calls**

## Findings by analyser

### llm_ba — access_control, other, unchecked_low_level_calls (2,610 tokens, 18.0s)

- unchecked_low_level_calls `fallback function and hasConfirmed/isOwner functions` (high): The contract uses delegatecall on arbitrary msg.data without checking the return value or restricting the called functions. An attacker can trigger any function in the library (including ones protected only by owner checks) and the contract will ignore failures, potentially leading to unauthorized state changes.
- access_control `delegatecall to library in fallback function` (medium): Because the fallback forwards all calldata to the library via delegatecall, functions that should be restricted (e.g., changeOwner, addOwner) can be invoked without the required multi‑owner confirmations if the library's modifiers are bypassed due to mismatched storage layout.
- other `mismatch of library address constants between Wallet and WalletLibrary` (medium): The Wallet contract defines a different constant _walletLibrary address than the one used inside WalletLibrary. If the address points to a malicious contract, delegatecalls will execute attacker‑controlled code in the context of the wallet, leading to full control over its storage.
