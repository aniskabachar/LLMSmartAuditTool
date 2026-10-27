"""
verify/improved_evaluator.py

Upgraded evaluation pipeline targeting higher accuracy through:
  1. Detailed system prompt with explicit Solidity vulnerability patterns
  2. Chain-of-thought (CoT) reasoning pass before final JSON output
  3. Category-aware second pass for any missed/ambiguous cases
  4. Larger context window (less truncation)
  5. Few-shot examples embedded in prompt

The prompt (system prompt, few-shot examples, checklist) is identical for
every tier: tiers are derived from ground-truth categories, so tier-specific
hints or examples would leak the label.

Run:
    py verify/improved_evaluator.py --difficulty easy
    py verify/improved_evaluator.py --all
"""

from __future__ import annotations
import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()
from auditor.models import Action, DetectedVulnerability, VulnerabilityType, Severity
from verify.eval_common import (
    SAMPLE_SEED, TIERS, load_contracts, select_contracts, contract_record,
    summarize, sha256, run_metadata,
)
from verify.real_evaluator import get_client

MODEL       = "openai/gpt-oss-120b"
TEMPERATURE = 0.05

# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert Solidity smart contract security auditor with 10 years of experience.
You have deep knowledge of all Ethereum vulnerability classes.

## VULNERABILITY DETECTION RULES (follow precisely)

### REENTRANCY
Trigger: external call (.call, .send, .transfer, delegatecall) happens BEFORE state variable update.
Classic pattern: balance updated AFTER transfer. Check-Effects-Interactions violation.
NOT reentrancy if: state updated before the call, or nonReentrant modifier present.

### INTEGER OVERFLOW / UNDERFLOW
Trigger: arithmetic (+, -, *) on uint/int WITHOUT SafeMath AND Solidity pragma < 0.8.0.
Solidity 0.8.0+ has built-in overflow protection — do NOT flag as overflow.
Look for: pragma solidity ^0.4.x, ^0.5.x, ^0.6.x, ^0.7.x without SafeMath.

### ACCESS CONTROL
Trigger: critical function (withdraw, selfdestruct, transfer ownership) has no modifier.
OR: wrong constructor name (pre-0.5: function with same name as contract = constructor).
OR: tx.origin used for authentication instead of msg.sender.
OR: delegatecall to user-controlled address.

### TX ORIGIN
Trigger: require(tx.origin == owner) or similar tx.origin authentication.
tx.origin is the original EOA, not the immediate caller — phishing vulnerability.

### TIMESTAMP DEPENDENCE / BAD RANDOMNESS
Trigger: block.timestamp, block.number, block.blockhash, now used to:
  - generate random numbers (e.g., winner = block.timestamp % players.length)
  - control game outcomes
  - determine winners in lotteries/gambling contracts
Miners can manipulate block.timestamp by ~15 seconds.
blockhash only available for last 256 blocks (old blocks return 0).
ANY gambling/lottery/random contract using block properties is vulnerable.

### UNCHECKED LOW-LEVEL CALLS
Trigger: .call(), .send(), .delegatecall() return value NOT checked.
If result is ignored (no require(success, ...) or if(!success)), it's a bug.

### SELFDESTRUCT / UNSAFE SUICIDE
Trigger: selfdestruct(addr) or suicide(addr) callable by unauthorized address.
Or: contract can be destroyed in a way that breaks dependent contracts.

### DENIAL OF SERVICE
Trigger: loop over unbounded array, or send to all users in one tx (gas bomb).
OR: external call that can revert blocking state progress.

## RESPONSE FORMAT

First, reason through the contract step by step.
Then output ONLY valid JSON at the end (no text after the JSON block):

```json
{
  "vulnerabilities": [
    {
      "type": "reentrancy|integer_overflow|access_control|tx_origin|timestamp_dependence|selfdestruct|uninitialized_storage|unchecked_low_level_calls|bad_randomness|front_running|denial_of_service|other",
      "location": "function name or line description",
      "severity": "low|medium|high|critical",
      "explanation": "specific reason with code evidence"
    }
  ]
}
```

If no vulnerability found, output: {"vulnerabilities": []}"""

# Few-shot examples embedded in user message (same for every contract)
FEW_SHOT_EXAMPLES = {
    "reentrancy_overflow": """Example 1 — REENTRANCY (the ONLY vuln in this contract):
```solidity
pragma solidity ^0.5.0;   // <-- 0.5+ means NO integer overflow concern
contract EtherStore {
    mapping(address => uint256) public balances;
    function withdraw(uint256 _amount) public {
        require(balances[msg.sender] >= _amount);
        (bool sent,) = msg.sender.call{value: _amount}("");  // external call FIRST
        require(sent, "Failed");
        balances[msg.sender] -= _amount;  // state update AFTER = reentrancy
    }
}
```
Correct output: {"vulnerabilities": [{"type": "reentrancy", "location": "withdraw()", "severity": "critical", "explanation": "External call before state update violates CEI pattern"}]}
NOTE: Do NOT add integer_overflow here — Solidity 0.5+ is safe and there is no arithmetic.

Example 2 — INTEGER OVERFLOW ONLY (no reentrancy):
```solidity
pragma solidity ^0.4.18;   // <-- 0.4.x needs SafeMath
contract Token {
    mapping(address => uint256) balances;
    function transfer(uint256 amount) public {
        balances[msg.sender] -= amount;  // underflow possible without SafeMath
        balances[to] += amount;
    }
}
```
Correct output: {"vulnerabilities": [{"type": "integer_overflow", "location": "transfer()", "severity": "high", "explanation": "Solidity 0.4.x has no overflow protection, subtraction can underflow"}]}
NOTE: Do NOT add reentrancy here — there is no external call.

IMPORTANT RULE: Only report a vulnerability if you see CLEAR CODE EVIDENCE for it.
Do NOT add integer_overflow to reentrancy contracts unless arithmetic is also vulnerable.
Do NOT add reentrancy to overflow contracts unless there is also an external call.""",

    "calls_access_control": """Example — UNCHECKED LOW-LEVEL CALL:
```solidity
function withdraw() public {
    msg.sender.call{value: balance[msg.sender]}("");  // return value ignored!
    balance[msg.sender] = 0;
}
```
Correct output: {"vulnerabilities": [{"type": "unchecked_low_level_calls", "location": "withdraw()", "severity": "medium", "explanation": "Return value of .call() is not checked - failed transfer goes undetected"}]}

Example — ACCESS CONTROL (wrong constructor):
```solidity
pragma solidity ^0.4.22;
contract Owned {
    function Owned() public { owner = msg.sender; }  // NOT a constructor in 0.4.22+
    function kill() public { selfdestruct(owner); }  // anyone can call
}
```
Correct output: {"vulnerabilities": [{"type": "access_control", "location": "Owned() / kill()", "severity": "critical", "explanation": "Function name matches contract name but is not a constructor in 0.4.22+ - anyone can call it and become owner"}]}""",

    "randomness_front_running": """Example — BAD RANDOMNESS:
```solidity
function pickWinner() public {
    uint winner = uint(keccak256(block.timestamp, block.difficulty)) % players.length;
    players[winner].transfer(address(this).balance);
}
```
Correct output: {"vulnerabilities": [{"type": "bad_randomness", "location": "pickWinner()", "severity": "high", "explanation": "block.timestamp and block.difficulty are miner-controllable - winner can be predicted/manipulated"}]}

Example — FRONT RUNNING / TOD:
```solidity
uint public reward = 100;
function solve(bytes32 answer) public {
    require(keccak256(answer) == puzzle);
    msg.sender.transfer(reward);  // anyone who sees tx in mempool can front-run
}
```
Correct output: {"vulnerabilities": [{"type": "front_running", "location": "solve()", "severity": "medium", "explanation": "Solution visible in mempool before mining - attackers can copy and submit with higher gas"}]}"""
}

FEW_SHOT = "\n\n".join(FEW_SHOT_EXAMPLES.values())

COT_CHECKLIST = """Analyse this Solidity contract step by step:
1. Check the Solidity version (pragma) — important for overflow and constructor rules
2. Look for external calls and state update order (reentrancy)
3. Look for block.timestamp / blockhash used for randomness (bad_randomness)
4. Look for unchecked .call() / .send() return values
5. Look for missing access control on critical functions
6. Look for wrong constructor names (pre-0.5.0)
7. Look for selfdestruct reachable by anyone, and delegatecall to untrusted targets
8. Summarise all vulnerabilities found"""

FOLLOWUP_PROMPT = "Based on your analysis above, provide ONLY the JSON result now. No additional text."


def config() -> dict:
    """Evaluator settings recorded alongside results."""
    return {
        "evaluator":   "cot",
        "model":       MODEL,
        "temperature": TEMPERATURE,
        "max_tokens":  {"cot": 1000, "followup": 400},
        "prompt_hash": sha256(SYSTEM_PROMPT, FEW_SHOT, COT_CHECKLIST, FOLLOWUP_PROMPT),
    }

VALID_TYPES = {t.value for t in VulnerabilityType}

# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_response(raw: str) -> dict:
    """Multi-strategy JSON extraction with keyword fallback."""
    import re
    raw = raw.strip()

    # Strategy 1: JSON in markdown fence
    fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except Exception:
            pass

    # Strategy 2: last { ... } block (after CoT reasoning)
    blocks = list(re.finditer(r'\{[^{}]*"vulnerabilities"[^{}]*\[.*?\]\s*\}', raw, re.DOTALL))
    for block in reversed(blocks):
        try:
            return json.loads(block.group())
        except Exception:
            pass

    # Strategy 3: any { } block
    all_blocks = list(re.finditer(r'\{.*?\}', raw, re.DOTALL))
    for block in reversed(all_blocks):
        try:
            return json.loads(block.group())
        except Exception:
            pass

    # Strategy 4: keyword extraction from prose (covers bad_randomness etc.)
    patterns = [
        (r'\breentrancy\b',               "reentrancy"),
        (r'\binteger[\s_]overflow\b|\bunderflow\b|\boverflow\b', "integer_overflow"),
        (r'\baccess[\s_]control\b|\bauthorization\b|\bwrong[\s_]constructor\b', "access_control"),
        (r'\btx\.origin\b',               "tx_origin"),
        (r'\btimestamp[\s_]dependence\b|\bblock\.timestamp\b|\bbad[\s_]randomness\b|\blockhash\b|\bblock\.number.*random\b', "bad_randomness"),
        (r'\bselfdestruct\b|\bsuicide\b', "selfdestruct"),
        (r'\bunchecked[\s_](?:low[\s_]level[\s_])?call\b|\b\.call\(\).*not checked\b', "unchecked_low_level_calls"),
        (r'\bfront[\s_-]run\b|\btransaction[\s_]order\b|\bTOD\b', "front_running"),
        (r'\bdenial[\s_]of[\s_]service\b|\bDoS\b', "denial_of_service"),
        (r'\bdelegatecall\b', "access_control"),
    ]
    raw_lower = raw.lower()
    found = []
    for pattern, vtype in patterns:
        if re.search(pattern, raw_lower) and vtype not in found:
            found.append(vtype)

    if found:
        return {"vulnerabilities": [
            {"type": t, "location": "inferred from analysis",
             "severity": "high" if t in {"reentrancy","bad_randomness","selfdestruct"} else "medium",
             "explanation": "Identified from chain-of-thought reasoning"}
            for t in found
        ]}

    return {"vulnerabilities": []}


def _normalise(parsed: dict) -> Action:
    vulns = []
    for v in parsed.get("vulnerabilities", []):
        t = v.get("type", "other").lower().strip().replace(" ", "_").replace("-", "_")
        if t not in VALID_TYPES:
            t = "other"
        s = v.get("severity", "medium").lower().strip()
        if s not in {"low", "medium", "high", "critical"}:
            s = "medium"
        vulns.append(DetectedVulnerability(
            type=VulnerabilityType(t), location=str(v.get("location",""))[:200],
            severity=Severity(s), explanation=str(v.get("explanation",""))[:500],
        ))
    return Action(vulnerabilities=vulns)


# ── Core auditor ──────────────────────────────────────────────────────────────

def _call_llm(messages: list, max_tokens: int = 900) -> tuple[str, str | None]:
    """Single LLM call with rate-limit retry. Returns (text, error or None)."""
    for attempt in range(5):
        try:
            r = get_client().chat.completions.create(
                model=MODEL, messages=messages,
                temperature=TEMPERATURE, max_tokens=max_tokens,
            )
            time.sleep(9)   # stay within 8000 TPM free tier
            return r.choices[0].message.content or "", None
        except RuntimeError:
            raise   # missing API key: abort the run instead of scoring zeros
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err:
                import re
                m = re.search(r'try again in (\d+\.?\d*)s', err)
                wait = float(m.group(1)) + 3.0 if m else 20.0 * (attempt + 1)
                print(f"    [rate limit] waiting {wait:.0f}s ...", end=" ", flush=True)
                time.sleep(wait)
            else:
                print(f"    [LLM error] {err[:120]}")
                return "", f"llm_error: {err[:300]}"
    return "", "max_retries_exceeded"


def audit_with_cot(code: str) -> tuple[Action, list[str], str | None]:
    """
    Two-stage audit:
      Stage 1 — CoT reasoning: ask model to reason through the contract
      Stage 2 — JSON extraction: force structured output from the reasoning
    Returns (Action, raw responses, error message or None).
    """
    # Truncate intelligently: keep pragma + first N chars + last N chars
    if len(code) > 7000:
        code = code[:3500] + "\n\n// ...truncated...\n\n" + code[-3500:]

    # ── Stage 1: Chain-of-thought analysis ───────────────────────────────────
    cot_prompt = f"""{FEW_SHOT}

{COT_CHECKLIST}

CONTRACT:
```solidity
{code}
```

Reason through each check, then end with the JSON result."""

    raw_cot, error = _call_llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": cot_prompt},
    ], max_tokens=1000)

    if not raw_cot:
        return Action(vulnerabilities=[]), [], error or "empty_response"

    raws = [raw_cot]

    # ── Stage 2: If CoT didn't produce clean JSON, force extraction ──────────
    action = _normalise(_parse_response(raw_cot))

    # If we found nothing but the reasoning mentions vulnerability keywords,
    # do a targeted follow-up pass to get structured output
    if not action.vulnerabilities and len(raw_cot) > 100:
        followup, error = _call_llm([
            {"role": "system",    "content": SYSTEM_PROMPT},
            {"role": "user",      "content": cot_prompt},
            {"role": "assistant", "content": raw_cot},
            {"role": "user",      "content": FOLLOWUP_PROMPT},
        ], max_tokens=400)

        if followup:
            raws.append(followup)
            action = _normalise(_parse_response(followup))
        else:
            return action, raws, error

    return action, raws, None


# ── Evaluation runner ─────────────────────────────────────────────────────────

def evaluate_improved(difficulty: str, n: int | None = None,
                      seed: int = SAMPLE_SEED, use_test_split: bool = True,
                      labels: str = "curated") -> dict:
    """Evaluate the CoT agent on the test split (all of it when n is None)."""
    sample = select_contracts(load_contracts(difficulty, use_test_split), n, seed)
    print(f"  Evaluating {len(sample)} contracts "
          f"({'test split' if use_test_split else 'all'})")

    details = []
    for i, sol_path in enumerate(sample):
        code = sol_path.read_text(encoding="utf-8", errors="replace")

        print(f"  [{i+1}/{len(sample)}] {sol_path.name} ... ", end="", flush=True)

        action, raws, error = audit_with_cot(code)
        record = contract_record(sol_path, action, raws, error, labels=labels)
        details.append(record)

        found = ",".join(record["found_types"]) or "(none)"
        expct = ",".join(record["expected_types"]) or "(none)"
        print(f"score={record['score']:.3f}  "
              f"found=[{found}]  expected=[{expct}]"
              + (f"  [{error}]" if error else ""))

    return summarize(difficulty, details)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--difficulty", default=None, choices=list(TIERS))
    parser.add_argument("--all",  action="store_true")
    parser.add_argument("--n",    type=int, default=None,
                        help="Contracts per tier (default: whole test split)")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = parser.parse_args()

    tiers = (list(TIERS) if args.all
             else ([args.difficulty] if args.difficulty else ["easy"]))

    all_results = {}
    for tier in tiers:
        print(f"\n{'='*60}")
        print(f"  IMPROVED EVALUATION: {tier.upper()}")
        print(f"{'='*60}")
        all_results[tier] = evaluate_improved(tier, n=args.n, seed=args.seed)

    print(f"\n\n{'='*60}")
    for tier, r in all_results.items():
        print(f"  {tier:<8}  score={r['avg_score']:.3f}  P={r['avg_precision']:.3f}  "
              f"R={r['avg_recall']:.3f}  F1={r['avg_f1']:.3f}  n={r['n_contracts']}")

    # Save
    out = Path("verify/results/improved_evaluation.json")
    out.write_text(json.dumps({
        "metadata": run_metadata(**config(), n=args.n, sample_seed=args.seed),
        "results":  all_results,
    }, indent=2))
    print(f"\n  Saved => {out}")
