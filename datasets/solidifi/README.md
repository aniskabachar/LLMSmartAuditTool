# SolidiFI contracts (copies)

200 buggy contracts from the [SolidiFI benchmark](https://github.com/smartbugs/SolidiFI-benchmark)
(Ghaleb & Pattabiraman, ISSTA 2020), 50 per bug type: Overflow-Underflow, Re-entrancy,
Timestamp-Dependency and Unhandled-Exceptions. Copied by `rl_tools/build_dataset.py --solidifi`
for the tool-selection RL dataset (`datasets/tool_results/combined_tools.json`).

Licence: the SolidiFI repository is MIT-licensed; per its LICENSE, the original contracts were
obtained from Etherscan, are publicly available there, and retain their original licences.

Note: SolidiFI names injected code with bug-revealing suffixes (`_re_ent`, `_intou`, `_tmstmp`,
`_unchk`). Code sent to an LLM is passed through `verify/real_evaluator.truncate()`, which strips
comments and neutralises those names.
