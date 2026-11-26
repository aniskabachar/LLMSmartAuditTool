# Audit report: ProofOfExistence.sol

- Audited 2026-11-26T11:51:23 by the RL coordinator (MlpPolicy 64x64, trained on 269 labelled contracts, λ = 0.02).
- Analysers run, in order: llm_ba
- LLM tokens: 2,215; analyser time 19.7s (wall 19.7s)

## Vulnerability categories found

- **access_control**
- **denial_of_service**
- **timestamp_dependence**

## Findings by analyser

### llm_ba — access_control, denial_of_service, timestamp_dependence (2,215 tokens, 19.7s)

- timestamp_dependence `SetFileExistenceProof (uses now for date)` (low): The contract uses the block timestamp (now) to record the proof date, which can be manipulated by miners within a reasonable range.
- access_control `SetFileExistenceProof` (medium): Anyone can call SetFileExistenceProof and specify an arbitrary dappBoxOrigin address, allowing malicious users to insert falsified existence proofs for other users.
- denial_of_service `GetFileExistenceProof, getQRCode, searchExistenceProoUsngQRf` (medium): These view functions iterate over an unbounded array of proofs. As the array grows, the gas required may exceed the block gas limit, making the functions unusable and potentially blocking legitimate reads.
